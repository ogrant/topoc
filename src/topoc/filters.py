"""The AMPS filter subset used in `replicate ... where "<filter>"`, and its
rewrites from field names to wire names and back.

Supported: field paths (`/a/b`), the routed envelope's `$route`, comparisons
(`= <> != < <= > >=`), `AND` / `OR` / `NOT`, parentheses, `LIKE` / `NOT LIKE`,
numbers, `true` / `false`, and single-quoted strings with `''` escaping.
Path-like text inside a string literal is a literal, never a field.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Final, NoReturn

from topoc.errors import TopocError

# Maps an absolute field-name path (`/book/venue`) to its wire path (`/b/v`), or
# `None` if no such field exists.
type PathResolver = Callable[[str], str | None]


# Wire name of a routed envelope's message field: message paths live under `/msg`.
MSG_FIELD: Final = "msg"

# Wire name of a routed envelope's route field, which `$route` refers to.
ROUTE_FIELD: Final = "route"


class FilterErrorKind(Enum):
    SYNTAX = "syntax"  # the filter does not parse
    UNKNOWN_FIELD = "unknown_field"  # a field path does not resolve
    ROUTE_ON_UNROUTED_TOPIC = "route_on_unrouted_topic"  # `$route` without an envelope


class FilterError(TopocError):
    def __init__(self, kind: FilterErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class FieldRef:
    """A message field path such as `/book/venue`, with its character range in
    the filter."""

    segments: list[str]
    offset: int
    length: int

    @property
    def path(self) -> str:
        """The absolute path, e.g. `/book/venue`."""
        return "/" + "/".join(self.segments)


@dataclass(frozen=True, slots=True)
class RouteRef:
    """A `$route` reference: the route of a routed topic's envelope, which is not
    a message field. Its character range in the filter."""

    offset: int
    length: int


@dataclass(frozen=True, slots=True)
class References:
    """The paths a filter refers to, each kind in source order."""

    fields: list[FieldRef]
    routes: list[RouteRef]


def references(filter_text: str) -> References:
    """Validate `filter_text` and return what it refers to. Raises `FilterError`
    (syntax) if it does not parse."""
    return _Parser(filter_text).parse()


def rewrite(filter_text: str, resolve: PathResolver, *, routed: bool) -> str:
    """Rewrite every field path through `resolve`, prefixing `/msg` when `routed`
    (the envelope location of message fields), and `$route` to the envelope's
    `/route`. Everything else — operators, literals, spacing — is preserved
    verbatim. Raises `FilterError` on a syntax error, an unresolvable field, or
    `$route` on an unrouted topic."""
    refs = references(filter_text)
    edits: list[tuple[int, int, str]] = []
    for field in reversed(refs.fields):
        wire = resolve(field.path)
        if wire is None:
            raise FilterError(
                FilterErrorKind.UNKNOWN_FIELD, f"filter references unknown field '{field.path}'"
            )
        edits.append((field.offset, field.length, f"/{MSG_FIELD}{wire}" if routed else wire))
    if refs.routes and not routed:
        raise FilterError(
            FilterErrorKind.ROUTE_ON_UNROUTED_TOPIC, "'$route' is only valid on a routed topic"
        )
    edits += [(route.offset, route.length, f"/{ROUTE_FIELD}") for route in refs.routes]

    out = filter_text
    # Splice right to left so earlier offsets stay valid.
    for offset, length, replacement in sorted(edits, reverse=True):
        out = out[:offset] + replacement + out[offset + length :]
    return out


def rewrite_to_names(filter_text: str, resolve: PathResolver, *, routed: bool) -> str:
    """The inverse of `rewrite`: a filter as AMPS runs it, with wire paths, back
    to field names. Every wire path goes through `resolve` (wire path to field
    path); on a routed topic, message paths lose their `/msg` prefix and the
    envelope's `/route` becomes `$route`. Raises `FilterError` on a syntax error
    or a path that does not resolve."""
    refs = references(filter_text)
    edits: list[tuple[int, int, str]] = []
    for field in refs.fields:
        segments = field.segments
        if routed and segments == [ROUTE_FIELD]:
            edits.append((field.offset, field.length, "$" + ROUTE_FIELD))
            continue
        if routed:
            if segments[:1] != [MSG_FIELD] or len(segments) < 2:
                raise FilterError(
                    FilterErrorKind.UNKNOWN_FIELD,
                    f"filter path '{field.path}' is neither under '/{MSG_FIELD}' nor "
                    f"'/{ROUTE_FIELD}' on a routed topic",
                )
            segments = segments[1:]
        wire = "/" + "/".join(segments)
        name = resolve(wire)
        if name is None:
            raise FilterError(FilterErrorKind.UNKNOWN_FIELD, f"filter path '{wire}' names no field")
        edits.append((field.offset, field.length, name))
    out = filter_text
    for offset, length, replacement in sorted(edits, reverse=True):
        out = out[:offset] + replacement + out[offset + length :]
    return out


_WHITESPACE = frozenset(" \t\n\r\v\f")
_DIGITS = frozenset("0123456789")
# Two-character operators first, so `<=` is not read as `<`.
_COMPARE_OPS = ("<=", ">=", "<>", "!=", "=", "<", ">")


def _is_ident_start(c: str | None) -> bool:
    return c is not None and c.isascii() and (c.isalpha() or c == "_")


def _is_ident_continue(c: str | None) -> bool:
    return c is not None and c.isascii() and (c.isalnum() or c == "_")


class _Parser:
    """Recursive-descent recogniser for the filter grammar, collecting the field
    and route references (the only parts a rewrite touches):

        or      := and { "OR" and }
        and     := unary { "AND" unary }
        unary   := { "NOT" } atom
        atom    := "(" or ")" | path [ "NOT" ] "LIKE" string | operand op operand
        path    := field | "$route"
        operand := path | string | "true" | "false" | number
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self._pos = 0
        self._fields: list[FieldRef] = []
        self._routes: list[RouteRef] = []

    def parse(self) -> References:
        self._parse_or()
        self._skip_whitespace()
        if not self._at_end():
            self._fail("end of filter")
        return References(self._fields, self._routes)

    def _parse_or(self) -> None:
        self._parse_and()
        while self._eat_keyword("OR"):
            self._parse_and()

    def _parse_and(self) -> None:
        self._parse_unary()
        while self._eat_keyword("AND"):
            self._parse_unary()

    def _parse_unary(self) -> None:
        while self._eat_keyword("NOT"):
            pass
        self._parse_atom()

    def _parse_atom(self) -> None:
        self._skip_whitespace()
        if self._eat("("):
            self._parse_or()
            self._skip_whitespace()
            if not self._eat(")"):
                self._fail("')'")
            return
        if self._starts_path():
            self._parse_path()
            after_path = self._pos
            self._eat_keyword("NOT")
            if self._eat_keyword("LIKE"):
                self._skip_whitespace()
                self._parse_string()
                return
            self._pos = after_path
            self._parse_compare_op()
            self._parse_operand()
            return
        self._parse_operand()
        self._parse_compare_op()
        self._parse_operand()

    def _parse_operand(self) -> None:
        self._skip_whitespace()
        c = self._peek()
        if self._starts_path():
            self._parse_path()
        elif c == "'":
            self._parse_string()
        elif self._eat_keyword("true") or self._eat_keyword("false"):
            pass
        elif c == "-" or (c is not None and c in _DIGITS):
            self._parse_number()
        else:
            self._fail("a field, string, number or boolean")

    def _parse_compare_op(self) -> None:
        self._skip_whitespace()
        for op in _COMPARE_OPS:
            if self._text.startswith(op, self._pos):
                self._pos += len(op)
                return
        self._fail("a comparison operator")

    def _starts_path(self) -> bool:
        return self._peek() in ("/", "$")

    def _parse_path(self) -> None:
        """A field or `$route`, recorded for the rewrite."""
        if self._peek() == "$":
            self._routes.append(self._parse_route())
        else:
            self._fields.append(self._parse_field())

    def _parse_route(self) -> RouteRef:
        """`$route`: only that name may follow the `$`."""
        offset = self._pos
        self._pos += 1  # `$`
        end = self._pos
        while end < len(self._text) and _is_ident_continue(self._text[end]):
            end += 1
        if self._text[self._pos : end] != ROUTE_FIELD:
            self._fail("'route' after '$'")
        self._pos = end
        return RouteRef(offset, self._pos - offset)

    def _parse_field(self) -> FieldRef:
        """`/segment{/segment}`"""
        offset = self._pos
        segments: list[str] = []
        while self._eat("/"):
            start = self._pos
            if not _is_ident_start(self._peek()):
                self._fail("a field name")
            while _is_ident_continue(self._peek()):
                self._pos += 1
            segments.append(self._text[start : self._pos])
        return FieldRef(segments, offset, self._pos - offset)

    def _parse_string(self) -> None:
        """`'...'` with `''` standing for a literal quote."""
        if not self._eat("'"):
            self._fail("a quoted string")
        while True:
            if self._at_end():
                self._fail("a closing quote")
            if not self._eat("'"):
                self._pos += 1
            elif not self._eat("'"):
                return  # a lone quote closes the string; `''` is an escaped quote

    def _parse_number(self) -> None:
        """`[-]int[.int]`, where an int is `0` or a digit run without leading zero."""
        self._eat("-")
        self._parse_integer()
        if self._eat("."):
            self._parse_integer()

    def _parse_integer(self) -> None:
        c = self._peek()
        if c is None or c not in _DIGITS:
            self._fail("a digit")
        if c == "0":
            self._pos += 1
            return
        while (c := self._peek()) is not None and c in _DIGITS:
            self._pos += 1

    def _eat_keyword(self, keyword: str) -> bool:
        self._skip_whitespace()
        end = self._pos
        while end < len(self._text) and _is_ident_continue(self._text[end]):
            end += 1
        if self._text[self._pos : end] != keyword:
            return False
        self._pos = end
        return True

    def _eat(self, c: str) -> bool:
        if self._peek() == c:
            self._pos += 1
            return True
        return False

    def _skip_whitespace(self) -> None:
        while (c := self._peek()) is not None and c in _WHITESPACE:
            self._pos += 1

    def _peek(self) -> str | None:
        return None if self._at_end() else self._text[self._pos]

    def _at_end(self) -> bool:
        return self._pos >= len(self._text)

    def _fail(self, expected: str) -> NoReturn:
        found = "end of filter" if self._at_end() else f"'{self._text[self._pos]}'"
        raise FilterError(
            FilterErrorKind.SYNTAX,
            f"invalid filter: expected {expected}, found {found} at offset {self._pos}",
        )
