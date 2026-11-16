"""The `context` declaration as a schema (V20), and the strict check of supplied
data against it (V21)."""

import re
from dataclasses import dataclass, field
from enum import Enum

from topoc.context import Context, Data, ObjectData, describe
from topoc.dsl import syntax
from topoc.errors import CompileError, ContextError, SourceLocation

# A map key that renders as `path.key`; any other renders as `path["key"]`.
_PLAIN_KEY = re.compile(r"[A-Za-z0-9_-]+")


class TyKind(Enum):
    STRING = "string"
    INT = "int"
    BOOL = "bool"
    LIST = "list"
    MAP = "map"
    RECORD = "record"


@dataclass(frozen=True, slots=True)
class Ty:
    """A declared type, with record types referenced by name."""

    kind: TyKind
    # The record type's name, for `RECORD`.
    record: str = ""
    # The item or value type, for `LIST` and `MAP`.
    inner: "Ty | None" = None

    def is_scalar(self) -> bool:
        return self.kind in (TyKind.STRING, TyKind.INT, TyKind.BOOL)

    def __str__(self) -> str:
        """The type as it is written in a `.topo`: `string[]`, `info[string]`."""
        match self.kind:
            case TyKind.LIST:
                return f"{self.inner}[]"
            case TyKind.MAP:
                return f"{self.inner}[string]"
            case TyKind.RECORD:
                return self.record
            case _:
                return str(self.kind.value)


@dataclass(frozen=True, slots=True)
class Field:
    """A field of a record: the context itself or a declared `type`."""

    name: str
    ty: Ty


class ValueKind(Enum):
    STRING = "a string"
    INT = "an int"
    BOOL = "a bool"
    LIST = "a list"
    MAP = "a map"
    RECORD = "a record"


@dataclass(frozen=True, slots=True)
class Value:
    """A validated context value, shaped by its declared type."""

    kind: ValueKind
    scalar: str | int | bool | None = None
    items: list["Value"] = field(default_factory=list)
    # Map entries in document order, or every declared field of a record in
    # declaration order.
    entries: list[tuple[str, "Value"]] = field(default_factory=list)

    def scalar_text(self) -> str | None:
        """A scalar's text as it interpolates (`3`, `true`), or `None`."""
        match self.scalar:
            case bool():
                return "true" if self.scalar else "false"
            case None:
                return None
            case _:
                return str(self.scalar)

    def describe(self) -> str:
        """The value's kind as error messages name it: `a string`, `a map`."""
        return self.kind.value


class Schema:
    """A well-formed `context` declaration (V20)."""

    def __init__(self, definition: syntax.ContextDef) -> None:
        """Check a `context` declaration (V20): every type referenced is
        declared, lists hold scalars, and no record type contains itself.
        Raises `CompileError`."""
        declared = {ty.name.text for ty in definition.types}
        self._records = {
            ty.name.text: _resolve_fields(ty.fields, declared) for ty in definition.types
        }
        # No record type may contain itself: its data could never be finite.
        for ty in definition.types:
            chain = [ty.name.text]
            if self._contains(ty.name.text, ty.name.text, chain):
                raise CompileError.at(
                    f"type '{ty.name.text}' contains itself ({' -> '.join(chain)})",
                    ty.name.location,
                )
        self._fields = _resolve_fields(definition.fields, declared)

    @property
    def fields(self) -> list[Field]:
        """The context's top-level fields, in declaration order."""
        return self._fields

    def record(self, name: str) -> list[Field]:
        """The fields of the record type `name`, which the schema declares."""
        return self._records.get(name, [])

    def validate(self, context: Context) -> Value:
        """Check `context` against the declaration, strictly (V21): every
        declared field present, no undeclared field, every value of its declared
        type. Raises `ContextError`."""
        return self._record_value(self._fields, "the context", context.members, "")

    def _contains(self, target: str, current: str, chain: list[str]) -> bool:
        """Whether record type `current` reaches `target`, extending `chain` with
        the path that does. A revisit of anything but `target` is cut short, so
        the search ends."""
        for fld in self._records[current]:
            name = _record_in(fld.ty)
            if name is None:
                continue
            if name == target:
                chain.append(name)
                return True
            if name in chain:
                continue
            chain.append(name)
            if self._contains(target, name, chain):
                return True
            chain.pop()
        return False

    def _value(self, ty: Ty, data: Data, path: str) -> Value:
        match ty.kind, data:
            case TyKind.STRING, str():
                return Value(ValueKind.STRING, data)
            case TyKind.INT, int() if not isinstance(data, bool):
                return Value(ValueKind.INT, data)
            case TyKind.BOOL, bool():
                return Value(ValueKind.BOOL, data)
            case TyKind.LIST, list():
                assert ty.inner is not None
                inner = ty.inner
                return Value(
                    ValueKind.LIST,
                    items=[
                        self._value(inner, item, f"{path}[{ix}]") for ix, item in enumerate(data)
                    ],
                )
            case TyKind.MAP, ObjectData():
                assert ty.inner is not None
                inner = ty.inner
                return Value(
                    ValueKind.MAP,
                    entries=[
                        (key, self._value(inner, value, _child(path, key)))
                        for key, value in data.members
                    ],
                )
            case TyKind.RECORD, ObjectData():
                return self._record_value(
                    self.record(ty.record), f"'{ty.record}'", data.members, path
                )
        raise _invalid(path, f"expected '{ty}', got {describe(data)}")

    def _record_value(
        self, fields: list[Field], owner: str, members: list[tuple[str, Data]], path: str
    ) -> Value:
        """A record's members: undeclared ones first (most often a typo of a
        missing one), then missing ones, then each value."""
        declared = {fld.name for fld in fields}
        for key, _ in members:
            if key not in declared:
                raise _invalid(path, f"{owner} has no field '{key}'")
        values = dict(members)
        entries: list[tuple[str, Value]] = []
        for fld in fields:
            if fld.name not in values:
                raise _invalid(path, f"missing field '{fld.name}' of {owner}")
            entries.append(
                (fld.name, self._value(fld.ty, values[fld.name], _child(path, fld.name)))
            )
        return Value(ValueKind.RECORD, entries=entries)


def _resolve_fields(decls: list[syntax.FieldDecl], declared: set[str]) -> list[Field]:
    return [
        Field(decl.name.text, _resolve_type(decl.type_expr, decl.type_location, declared))
        for decl in decls
    ]


def _resolve_type(type_expr: syntax.TypeExpr, at: SourceLocation, declared: set[str]) -> Ty:
    match type_expr.kind:
        case syntax.TypeKind.RECORD:
            assert type_expr.record is not None
            name = type_expr.record
            if name.text not in declared:
                raise CompileError.at(
                    f"unknown type '{name.text}' (expected 'string', 'int', 'bool' or a type "
                    "declared in the context block)",
                    name.location,
                )
            return Ty(TyKind.RECORD, name.text)
        case syntax.TypeKind.LIST:
            assert type_expr.inner is not None
            item = _resolve_type(type_expr.inner, at, declared)
            if not item.is_scalar():
                raise CompileError.at(
                    f"a list holds scalars ('string', 'int' or 'bool'), not '{item}': iterations "
                    "are named by their item (use a map keyed by name instead)",
                    at,
                )
            return Ty(TyKind.LIST, inner=item)
        case syntax.TypeKind.MAP:
            assert type_expr.inner is not None
            return Ty(TyKind.MAP, inner=_resolve_type(type_expr.inner, at, declared))
        case _:
            return Ty(TyKind(type_expr.kind.value))


def _record_in(ty: Ty) -> str | None:
    """The record type a field's type is made of, if any."""
    if ty.kind is TyKind.RECORD:
        return ty.record
    return _record_in(ty.inner) if ty.inner is not None else None


def _child(path: str, key: str) -> str:
    """`path.key`, or `path["key"]` when the key is not a plain name segment."""
    if not _PLAIN_KEY.fullmatch(key):
        return f'{path}["{key}"]'
    return f"{path}.{key}" if path else key


def _invalid(path: str, message: str) -> ContextError:
    return ContextError(f"{path}: {message}" if path else message)
