"""Data expansion: the first stage of resolution.

It checks the supplied context against the `.topo`'s `context` declaration
(V20, V21), then expands every `for` over data — a `list`, an inline list or a
context path (`@...`) — into the topics, instances and loops over instances it
generates, substituting loop variables and context paths as it goes (V13, V15,
V17, V19, V22-V24).

It runs before instances are resolved: a loop over data does not depend on
instances, so it may declare them. Loops over instances (a role, group or
instance) are left to the expander, with the data they use already substituted.
"""

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import NoReturn

from topoc.context import Context
from topoc.context.schema import Schema, Ty, TyKind, Value, ValueKind
from topoc.dsl import syntax
from topoc.errors import CompileError, ContextError, SourceLocation
from topoc.resolve.interpolation import substitute
from topoc.resolve.names import NameEntry, NameKind
from topoc.resolve.origin import Origin, annotated


@dataclass(frozen=True, slots=True)
class Item:
    """A definition after data expansion."""

    # A role, list, context, topic or instance, or a `for` over instances whose
    # body holds only topics.
    definition: syntax.Definition
    # The iterations that generated it; for a loop over instances, the
    # enclosing iterations it was generated in.
    origin: Origin | None
    # For a topic, its name as written; for a loop over instances, the names as
    # written of its topics, in body order.
    name_templates: list[str]


def expand_data(file: syntax.SourceFile, context: Context | None) -> list[Item]:
    """Expand `file`'s loops over data against `context`, which must be supplied
    exactly when the file declares a `context` block. Raises `CompileError`, or
    `ContextError` for data that does not fit."""
    declaration = next(
        (d for d in file.definitions if isinstance(d, syntax.ContextDef)),
        None,
    )
    if declaration is not None and context is None:
        _fail(
            "this topology declares a 'context' block, but no context was supplied",
            declaration.location,
        )
    if declaration is None and context is not None:
        raise ContextError("a context was supplied, but the topology declares no 'context' block")
    data: _Data | None = None
    if declaration is not None and context is not None:
        schema = Schema(declaration)
        data = _Data(schema, schema.validate(context))
    expander = _DataExpander(file, data)
    items: list[Item] = []
    for definition in file.definitions:
        expander.expand_definition(definition, items)
    return items


def _fail(message: str, at: SourceLocation) -> NoReturn:
    raise CompileError.at(message, at)


@dataclass(frozen=True, slots=True)
class _Data:
    """The validated context."""

    schema: Schema
    # The top-level record.
    value: Value


class _BoundKind(Enum):
    LIST_ITEM = "list item"  # an item of a list (always a scalar)
    INDEX = "index"  # a count of a loop over an integer `n`: 0 to n - 1, an int
    MAP_KEY = "map key"  # the key of a map whose values are not records (`for k, v`)
    MAP_VALUE = "map value"  # the value of a map whose values are not records
    RECORD_KEY = "record key"  # the key of a map of records, which also reaches its fields

    @property
    def domain(self) -> str:
        """What a variable of this kind iterates over, for error messages."""
        match self:
            case _BoundKind.LIST_ITEM:
                return "a list of values"
            case _BoundKind.INDEX:
                return "integers"
            case _BoundKind.MAP_VALUE:
                return "a map's values"
            case _:
                return "a map's keys"


@dataclass(frozen=True, slots=True)
class _Bound:
    """What a loop variable is bound to in one iteration."""

    kind: _BoundKind
    # The item or key.
    text: str = ""
    # The value, for `MAP_VALUE` and `RECORD_KEY`.
    value: Value | None = None
    ty: Ty | None = None

    def describe(self) -> str:
        """The value as an iteration note shows it."""
        if self.kind is _BoundKind.MAP_VALUE and self.value is not None:
            text = self.value.scalar_text()
            return self.value.describe() if text is None else text
        return self.text


@dataclass(frozen=True, slots=True)
class _Var:
    """A loop variable in scope."""

    name: str
    bound: _Bound


@dataclass(frozen=True, slots=True)
class _Scope:
    """The loop variables in scope, outermost first, and the iterations that
    bound them."""

    variables: tuple[_Var, ...] = ()
    origin: Origin | None = None

    def get(self, name: str) -> _Var | None:
        return next((var for var in reversed(self.variables) if var.name == name), None)

    def bind(self, loop: SourceLocation, bound: list[_Var]) -> "_Scope":
        """This scope with one iteration of the `for` at `loop` added."""
        origin = self.origin
        for var in bound:
            origin = Origin.within(origin, loop, var.name, var.bound.describe())
        return _Scope((*self.variables, *bound), origin)

    def naming_variables(self) -> list[str]:
        """The variables that name their iteration (not a `for k, v` value)."""
        return [var.name for var in self.variables if var.bound.kind is not _BoundKind.MAP_VALUE]


@dataclass(frozen=True, slots=True)
class _Found:
    """What a `{{...}}` expression or a domain path denotes: text (an item or a
    key), or a value of a declared type."""

    text: str | None = None
    value: Value | None = None
    ty: Ty | None = None


@dataclass(frozen=True, slots=True)
class _Iteration:
    """The items a loop over data iterates."""

    is_list: bool
    # `0` to `count - 1`: a loop over an integer (with `is_list` false).
    count: int | None = None
    # List items, as text.
    items: list[str] = field(default_factory=list)
    # Map entries, whose values are records (`for k`) or not (`for k, v`).
    entries: list[tuple[str, Value]] = field(default_factory=list)
    value_ty: Ty | None = None

    def bindings(self, loop: syntax.ForDef) -> list[list[_Var]]:
        """The variables each iteration binds, in order."""
        variable = loop.variable.text
        if self.count is not None:
            return [[_Var(variable, _Bound(_BoundKind.INDEX, str(i)))] for i in range(self.count)]
        if self.is_list:
            return [[_Var(variable, _Bound(_BoundKind.LIST_ITEM, item))] for item in self.items]
        if loop.value is None:
            return [
                [_Var(variable, _Bound(_BoundKind.RECORD_KEY, key, value, self.value_ty))]
                for key, value in self.entries
            ]
        value_name = loop.value.text
        return [
            [
                _Var(variable, _Bound(_BoundKind.MAP_KEY, key)),
                _Var(value_name, _Bound(_BoundKind.MAP_VALUE, "", value, self.value_ty)),
            ]
            for key, value in self.entries
        ]


def _domain_text(domain: syntax.ForDomain) -> str:
    match domain:
        case syntax.Ident(text=text):
            return text
        case syntax.ContextPath():
            return str(domain)
        case _:
            return "[...]"


def _shadowing(variable: str) -> str:
    return f"loop variable '{variable}' is already the variable of an enclosing 'for'"


def _list_iteration(loop: syntax.ForDef, items: list[str]) -> _Iteration:
    """The items of a loop over a list (V24: one variable)."""
    if loop.value is not None:
        _fail(
            f"'for {loop.variable.text}, {loop.value.text}' iterates a map; "
            f"'{_domain_text(loop.domain)}' is a list",
            loop.value.location,
        )
    return _Iteration(True, items=items)


def _data_iteration(
    loop: syntax.ForDef, found: _Found, shown: str, at: SourceLocation
) -> _Iteration:
    """The items of a loop over the data `found`, written `shown` at `at`: a
    list, an integer or a map (V14, V24)."""
    if found.text is not None:
        _fail(f"'{shown}' is a single value; a 'for' iterates over a list, a map or an integer", at)
    value, ty = found.value, found.ty
    assert value is not None
    assert ty is not None
    if value.kind is ValueKind.LIST:
        return _list_iteration(
            loop,
            [
                text if (text := item.scalar_text()) is not None else item.describe()
                for item in value.items
            ],
        )
    if value.kind is ValueKind.INT:
        assert isinstance(value.scalar, int)
        if loop.value is not None:
            _fail(
                f"'for {loop.variable.text}, {loop.value.text}' iterates a map; "
                f"'{shown}' is an integer",
                loop.value.location,
            )
        if value.scalar < 0:
            _fail(
                f"'{shown}' is {value.scalar}; a 'for' over an integer counts from 0 up to it, "
                "so it cannot be negative",
                at,
            )
        return _Iteration(False, count=value.scalar)
    if value.kind is not ValueKind.MAP:
        _fail(
            f"'{shown}' is {value.describe()}; a 'for' iterates over a list, a map or an integer",
            at,
        )
    assert ty.inner is not None
    value_ty = ty.inner
    records = value_ty.kind is TyKind.RECORD
    if loop.value is not None and records:
        _fail(
            f"'{shown}' maps to records: iterate it with one variable "
            f"('for {loop.variable.text} in {shown}') and reach their fields with "
            f"'{loop.variable.text}.field'",
            loop.value.location,
        )
    if loop.value is None and not records:
        _fail(
            f"'{shown}' maps to '{value_ty}' values: iterate it with two variables "
            f"('for {loop.variable.text}, value in {shown}')",
            loop.variable.location,
        )
    return _Iteration(False, entries=value.entries, value_ty=value_ty)


def _check_names_reference(body: list[syntax.ForItem], variables: list[str]) -> None:
    """V15: every instance name declared in a loop body references every
    enclosing loop variable that names its iteration (not a `for k, v` value),
    or two iterations would declare the same instance. Topics and queues may
    share a name, each declaration on its own instances: the resolver's
    per-instance registry rejects two copies on one instance instead."""
    for item in body:
        match item:
            case syntax.InstanceDef(name=name):
                kind = "instance"
            case _:
                continue
        for variable in variables:
            referenced = False

            def note(expr: str, variable: str = variable) -> None:
                nonlocal referenced
                referenced = referenced or expr.split(".", 1)[0] == variable

            substitute(name.text, note)
            if not referenced:
                _fail(
                    f"{kind} name '{name.text}' does not reference loop variable '{variable}', "
                    f"so every iteration would produce the same {kind}",
                    name.location,
                )


def _interpolated(expr: str, found: _Found, at: SourceLocation) -> str:
    """The text a placeholder interpolates as (V22: only scalars)."""
    if found.text is not None:
        return found.text
    assert found.value is not None
    text = found.value.scalar_text()
    if text is None:
        _fail(
            f"'{{{{{expr}}}}}' is {found.value.describe()}; only strings, ints and bools can be "
            "interpolated",
            at,
        )
    return text


def _declared_names(file: syntax.SourceFile) -> dict[str, NameEntry]:
    """Names a loop variable may not reuse (V13): everything declared in the
    source under a plain name. Context fields are not among them: they are
    written `@field`, so a loop variable cannot be mistaken for one."""
    names: dict[str, NameEntry] = {}

    def add(name: syntax.Ident, kind: NameKind) -> None:
        names.setdefault(name.text, NameEntry(kind, name.location))

    for definition in file.definitions:
        match definition:
            case syntax.RoleDef(name=name):
                add(name, NameKind.ROLE)
            case syntax.TopicDef(name=name):
                add(name, NameKind.TOPIC)
            case syntax.QueueDef(name=name):
                add(name, NameKind.QUEUE)
            case syntax.InstanceDef(name=name, group=group):
                add(name, NameKind.INSTANCE)
                if group is not None:
                    add(group, NameKind.GROUP)
            case syntax.ListDef(name=name):
                add(name, NameKind.LIST)
            case syntax.TopologyDef(name=name):
                add(name, NameKind.TOPOLOGY)
            case syntax.ContextDef() | syntax.ForDef():
                pass
    return names


class _DataExpander:
    def __init__(self, file: syntax.SourceFile, data: _Data | None) -> None:
        self._data = data
        self._declared = _declared_names(file)
        self._lists = {d.name.text: d for d in file.definitions if isinstance(d, syntax.ListDef)}

    def expand_definition(self, definition: syntax.Definition, items: list[Item]) -> None:
        top = _Scope()
        match definition:
            case syntax.TopicDef():
                items.append(Item(self._bind_topic(definition, top), None, [definition.name.text]))
            case syntax.InstanceDef():
                items.append(Item(self._bind_instance(definition, top), None, []))
            case syntax.QueueDef():
                items.append(Item(self._bind_queue(definition, top), None, []))
            case syntax.ForDef():
                self._expand_for(definition, top, items)
            case syntax.TopologyDef():
                # A parameter may not reuse a declared name, so a name in the
                # block is never ambiguous (as V13 for loop variables).
                for param in definition.params:
                    if (entry := self._declared.get(param.name.text)) is not None:
                        _fail(
                            f"parameter '{param.name.text}' of topology block "
                            f"'{definition.name.text}' conflicts with {entry.kind.value} defined "
                            f"at line {entry.location.line}, column {entry.location.column}",
                            param.name.location,
                        )
                # Its statements are already inlined where used; binding them
                # checks the context paths of one that is not.
                for statement in definition.statements:
                    self._bind_statement(statement, top)
                items.append(Item(definition, None, []))
            case _:
                items.append(Item(definition, None, []))

    def _expand_for(self, loop: syntax.ForDef, scope: _Scope, items: list[Item]) -> None:
        """Expand a loop over data in place, or bind the data a loop over
        instances uses and keep it for the expander."""
        iteration = self._iteration(loop, scope)
        if iteration is None:
            self._keep_instance_loop(loop, scope, items)
            return
        self._check_variables(loop, scope)
        _check_names_reference(loop.body, [*scope.naming_variables(), loop.variable.text])
        for bound in iteration.bindings(loop):
            inner = scope.bind(loop.location, bound)
            for item in loop.body:
                match item:
                    case syntax.TopicDef():
                        items.append(
                            Item(self._bind_topic(item, inner), inner.origin, [item.name.text])
                        )
                    case syntax.QueueDef():
                        items.append(
                            Item(self._bind_queue(item, inner), inner.origin, [item.name.text])
                        )
                    case syntax.InstanceDef():
                        items.append(Item(self._bind_instance(item, inner), inner.origin, []))
                    case syntax.ForDef():
                        self._expand_for(item, inner, items)

    def _keep_instance_loop(self, loop: syntax.ForDef, scope: _Scope, items: list[Item]) -> None:
        """A loop over instances: its topics and queues keep the instance
        variable for the expander, with the data in scope substituted, and loops
        over data in its body expanded into more of them (V23: no instances, no
        nested loop over instances)."""
        if loop.value is not None:
            _fail(
                f"'for {loop.variable.text}, {loop.value.text}' iterates a map; "
                f"'{_domain_text(loop.domain)}' is not a list or map of the context",
                loop.value.location,
            )
        if scope.get(loop.variable.text) is not None:
            _fail(_shadowing(loop.variable.text), loop.variable.location)
        kept_items: list[syntax.ForItem] = []
        name_templates: list[str] = []
        self._collect_instance_loop_items(
            loop, loop.body, scope, scope.naming_variables(), kept_items, name_templates
        )
        kept = syntax.ForDef(loop.location, loop.variable, None, loop.domain, kept_items)
        items.append(Item(kept, scope.origin, name_templates))

    def _collect_instance_loop_items(
        self,
        instance_loop: syntax.ForDef,
        body: list[syntax.ForItem],
        scope: _Scope,
        data_names: list[str],
        kept: list[syntax.ForItem],
        name_templates: list[str],
    ) -> None:
        """The topics and queues of a loop over instances, data bound, in body
        order; `name_templates` gets each one's name as written."""
        _check_names_reference(body, data_names)
        for item in body:
            match item:
                case syntax.TopicDef():
                    kept.append(self._bind_topic(item, scope))
                    name_templates.append(item.name.text)
                case syntax.QueueDef():
                    kept.append(self._bind_queue(item, scope))
                    name_templates.append(item.name.text)
                case syntax.InstanceDef():
                    _fail(
                        "instances may only be declared in loops over data (a list or the "
                        f"context); '{instance_loop.variable.text}' iterates over instances (the "
                        f"'for' at line {instance_loop.location.line}, column "
                        f"{instance_loop.location.column})",
                        item.name.location,
                    )
                case syntax.ForDef():
                    iteration = self._iteration(item, scope)
                    if iteration is None:
                        _fail(
                            "a loop over instances may not contain another loop over instances "
                            f"('{instance_loop.variable.text}' in the 'for' at line "
                            f"{instance_loop.location.line}, column "
                            f"{instance_loop.location.column})",
                            item.location,
                        )
                    self._check_variables(item, scope)
                    if item.variable.text == instance_loop.variable.text:
                        _fail(_shadowing(item.variable.text), item.variable.location)
                    names = [*data_names, item.variable.text]
                    for bound in iteration.bindings(item):
                        self._collect_instance_loop_items(
                            instance_loop,
                            item.body,
                            scope.bind(item.location, bound),
                            names,
                            kept,
                            name_templates,
                        )

    def _check_variables(self, loop: syntax.ForDef, scope: _Scope) -> None:
        """V13: a loop variable may not reuse a declared name or a variable of an
        enclosing loop, so every reference to it is unambiguous."""
        variables = [loop.variable] if loop.value is None else [loop.variable, loop.value]
        for variable in variables:
            if (entry := self._declared.get(variable.text)) is not None:
                _fail(
                    f"loop variable '{variable.text}' conflicts with {entry.kind.value} defined "
                    f"at line {entry.location.line}, column {entry.location.column}",
                    variable.location,
                )
            if scope.get(variable.text) is not None:
                _fail(_shadowing(variable.text), variable.location)

    def _iteration(self, loop: syntax.ForDef, scope: _Scope) -> _Iteration | None:
        """The items of a loop over data (V14, V22, V24), or `None` for a loop
        over a role, group or instance."""
        domain = loop.domain
        match domain:
            case list():
                return _list_iteration(loop, [item.value for item in domain])
            case syntax.ContextPath(path=path, location=location):
                shown = str(domain)
                return _data_iteration(
                    loop, self._lookup_context(path, shown, location), shown, location
                )
        if (named := self._lists.get(domain.text)) is not None:
            return _list_iteration(loop, [item.value for item in named.items])
        head = domain.text.split(".", 1)[0]
        if scope.get(head) is None and self._misses_sigil(head):
            _fail(
                f"'{head}' is a context field; write 'for {loop.variable.text} in @{domain.text}'",
                domain.location,
            )
        found = self._lookup(domain.text, scope, domain.location)
        if found is None:
            return None
        return _data_iteration(loop, found, domain.text, domain.location)

    def _misses_sigil(self, head: str) -> bool:
        """Whether `head`, written without `@`, can only mean a context field:
        the context declares it and the source declares nothing by that name."""
        return (
            head not in self._declared
            and self._data is not None
            and any(f.name == head for f in self._data.schema.fields)
        )

    def _lookup(self, expr: str, scope: _Scope, at: SourceLocation) -> _Found | None:
        """What `expr` (`v`, `v.field...`, `@field...`) denotes: a loop variable
        (innermost first) or, with `@`, a context path; `None` for any other
        name (the placeholder is left for later stages)."""
        if expr.startswith("@"):
            return self._lookup_context(expr[1:], f"{{{{{expr}}}}}", at)
        head, *rest = expr.split(".")
        if (var := scope.get(head)) is not None:
            bound = var.bound
            match bound.kind:
                case _BoundKind.LIST_ITEM if rest:
                    _fail(
                        f"'{{{{{expr}}}}}': '{head}' iterates over a list of values, which have "
                        "no attributes",
                        at,
                    )
                case _BoundKind.MAP_KEY if rest:
                    _fail(f"'{{{{{expr}}}}}': '{head}' is a map key, which has no fields", at)
                case _BoundKind.INDEX if rest:
                    _fail(
                        f"'{{{{{expr}}}}}': '{head}' counts over an integer, which has no fields",
                        at,
                    )
                case _BoundKind.INDEX:
                    # An int, so a bare `{{i}}` attribute value is typed `int`.
                    return _Found(value=Value(ValueKind.INT, int(bound.text)), ty=Ty(TyKind.INT))
                case _BoundKind.LIST_ITEM | _BoundKind.MAP_KEY | _BoundKind.RECORD_KEY if not rest:
                    return _Found(text=bound.text)
            assert bound.value is not None
            assert bound.ty is not None
            return self._navigate(bound.value, bound.ty, head, rest, f"{{{{{expr}}}}}", at)
        if self._misses_sigil(head):
            _fail(f"'{{{{{expr}}}}}': '{head}' is a context field; write '{{{{@{expr}}}}}'", at)
        return None

    def _lookup_context(self, path: str, shown: str, at: SourceLocation) -> _Found:
        """What the context path `path` (without its `@`) denotes; `shown` is
        how error messages quote it."""
        head, *rest = path.split(".")
        if self._data is None:
            _fail(f"'{shown}': this topology declares no 'context' block", at)
        ty = next((f.ty for f in self._data.schema.fields if f.name == head), None)
        value = next((v for name, v in self._data.value.entries if name == head), None)
        if ty is None or value is None:
            _fail(f"'{shown}': the context has no field '{head}'", at)
        return self._navigate(value, ty, f"@{head}", rest, shown, at)

    def _navigate(
        self, value: Value, ty: Ty, prefix: str, path: list[str], shown: str, at: SourceLocation
    ) -> _Found:
        """Descend `path` through record fields from `value` (of type `ty`),
        reached as `prefix` (V22); errors quote the whole path as `shown`."""
        assert self._data is not None
        reached = prefix
        for segment in path:
            if ty.kind is not TyKind.RECORD or value.kind is not ValueKind.RECORD:
                _fail(
                    f"'{shown}': '{reached}' is {value.describe()}, which has no fields",
                    at,
                )
            declared = next(
                (f for f in self._data.schema.record(ty.record) if f.name == segment), None
            )
            following = next((v for name, v in value.entries if name == segment), None)
            if declared is None or following is None:
                _fail(f"'{shown}': '{ty.record}' has no field '{segment}'", at)
            ty, value = declared.ty, following
            reached = f"{reached}.{segment}"
        return _Found(value=value, ty=ty)

    def _bind_text(self, text: str, scope: _Scope, at: SourceLocation, in_topic_name: bool) -> str:
        """`text` with every placeholder naming a loop variable or context path
        replaced by its value (V22: only scalars); `in_topic_name` applies V19.
        Other placeholders are left for later stages."""

        def replace(expr: str) -> str | None:
            found = self._lookup(expr, scope, at)
            if found is None:
                return None
            value = _interpolated(expr, found, at)
            if in_topic_name and (value == "" or "/" in value):
                problem = "which is empty" if value == "" else "which contains '/'"
                _fail(f"'{{{{{expr}}}}}' in a topic name expands to '{value}', {problem}", at)
            return value

        bound = substitute(text, replace)
        return text if bound is None else bound

    def _bind_ident(self, ident: syntax.Ident, scope: _Scope, in_topic_name: bool) -> syntax.Ident:
        return syntax.Ident(
            self._bind_text(ident.text, scope, ident.location, in_topic_name), ident.location
        )

    def _bind_string(self, string: syntax.StringLit, scope: _Scope) -> syntax.StringLit:
        return syntax.StringLit(
            self._bind_text(string.value, scope, string.location, False), string.location
        )

    def _bind_name(self, name: syntax.Ident, scope: _Scope) -> syntax.Ident:
        """A name in a topology statement: a data variable cannot name instances
        (V17); placeholders in a quoted name are substituted."""
        if (var := scope.get(name.text)) is not None:
            _fail(
                f"'{name.text}' iterates over {var.bound.kind.domain}; it cannot be used as an "
                f'instance name (quote a name that interpolates it instead, e.g. "Name.{{{{'
                f'{name.text}}}}}")',
                name.location,
            )
        return self._bind_ident(name, scope, False)

    def _bind_topic(self, topic: syntax.TopicDef, scope: _Scope) -> syntax.TopicDef:
        """A topic with the scope's data substituted. Errors name the iteration."""
        try:
            name = self._bind_ident(topic.name, scope, True)
        except CompileError as error:
            raise annotated(error, scope.origin, "topic", topic.name.text) from error
        try:
            topology = None
            if topic.topology is not None:
                topology = [self._bind_statement(s, scope) for s in topic.topology]
        except CompileError as error:
            raise annotated(error, scope.origin, "topic", name.text) from error
        return syntax.TopicDef(name, topic.definition, topology)

    def _bind_queue(self, queue: syntax.QueueDef, scope: _Scope) -> syntax.QueueDef:
        """A queue with the scope's data substituted in its names and strings.
        Errors name the iteration."""
        try:
            return self._bind_queue_unannotated(queue, scope)
        except CompileError as error:
            raise annotated(error, scope.origin, "queue", queue.name.text) from error

    def _bind_queue_unannotated(self, queue: syntax.QueueDef, scope: _Scope) -> syntax.QueueDef:

        def string(text: syntax.StringLit | None) -> syntax.StringLit | None:
            return None if text is None else self._bind_string(text, scope)

        ordering: syntax.QueueOrdering | None
        match queue.ordering:
            case syntax.Priority(path):
                ordering = syntax.Priority(self._bind_string(path, scope))
            case syntax.Barrier(text):
                ordering = syntax.Barrier(self._bind_string(text, scope))
            case None:
                ordering = None
        source = queue.source
        if isinstance(source, syntax.QueueTopics):
            source = syntax.QueueTopics(
                [self._bind_name(name, scope) for name in source.sources],
                None if source.publish_to is None else self._bind_name(source.publish_to, scope),
            )
        return replace(
            queue,
            # Like a topic's (V19): a queue reading its own name fits its
            # definition's pattern segment by segment.
            name=self._bind_ident(queue.name, scope, True),
            source=source,
            filter=string(queue.filter),
            ordering=ordering,
            topology=[self._bind_statement(statement, scope) for statement in queue.topology],
        )

    def _bind_statement(self, statement: syntax.TopologyStmt, scope: _Scope) -> syntax.TopologyStmt:
        match statement:
            case syntax.MeshStmt(group):
                return syntax.MeshStmt(self._bind_name(group, scope))
            case syntax.ReplicateStmt(source, target, where, two_way):
                return syntax.ReplicateStmt(
                    self._bind_name(source, scope),
                    self._bind_name(target, scope),
                    None if where is None else self._bind_string(where, scope),
                    two_way,
                )
            case syntax.LocalStmt(target):
                return syntax.LocalStmt(self._bind_name(target, scope))
            case syntax.SowStmt(target, durability):
                if isinstance(durability, syntax.PersistentDurability):
                    durability = syntax.PersistentDurability(
                        self._bind_string(durability.file, scope)
                    )
                return syntax.SowStmt(self._bind_name(target, scope), durability)
            case syntax.OwnedStmt(owner, statements):
                return syntax.OwnedStmt(
                    self._bind_name(owner, scope),
                    [self._bind_statement(inner, scope) for inner in statements],
                )

    def _bind_instance(self, instance: syntax.InstanceDef, scope: _Scope) -> syntax.InstanceDef:
        """An instance with the scope's data substituted in its name, group and
        attribute values. A bare `{{...}}` value takes the type of the value it
        names. Errors name the iteration."""
        try:
            return syntax.InstanceDef(
                self._bind_ident(instance.name, scope, False),
                instance.roles,
                None if instance.group is None else self._bind_ident(instance.group, scope, False),
                [self._bind_attr(assign, scope) for assign in instance.attrs],
            )
        except CompileError as error:
            raise annotated(error, scope.origin, "instance", instance.name.text) from error

    def _bind_attr(self, assign: syntax.AttrAssign, scope: _Scope) -> syntax.AttrAssign:
        value = assign.value
        if isinstance(value, str):
            value = self._bind_text(value, scope, assign.value_location, False)
        elif isinstance(value, syntax.Placeholder):
            value = self._placeholder_value(value.expr, scope, assign.value_location)
        return syntax.AttrAssign(assign.role, assign.attr, value, assign.value_location)

    def _placeholder_value(self, expr: str, scope: _Scope, at: SourceLocation) -> syntax.Literal:
        found = self._lookup(expr, scope, at)
        if found is None:
            _fail(f"'{{{{{expr}}}}}' names no loop variable or context field", at)
        if found.text is not None:
            return found.text
        assert found.value is not None
        if found.value.scalar is None:
            _fail(
                f"'{{{{{expr}}}}}' is {found.value.describe()}; an attribute value is a string, "
                "an int or a bool",
                at,
            )
        return found.value.scalar
