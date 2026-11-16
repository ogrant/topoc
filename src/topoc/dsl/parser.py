"""Recursive-descent parser for the `.topo` DSL.

Keywords are contextual: they are identifiers recognised only where the
grammar expects them, so `topic` or `key` remain valid names elsewhere.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import NoReturn

from topoc.dsl.lexer import tokenize
from topoc.dsl.syntax import (
    Argument,
    AttrAssign,
    AttrDecl,
    AttrType,
    Barrier,
    BlockUse,
    ContextDef,
    ContextPath,
    Definition,
    Durability,
    FieldDecl,
    ForDef,
    ForDomain,
    ForItem,
    Ident,
    InstanceDef,
    ListDef,
    Literal,
    LocalStmt,
    MeshStmt,
    OwnedStmt,
    Param,
    ParamKind,
    PersistentDurability,
    Placeholder,
    Priority,
    QueueDef,
    QueueOrdering,
    QueueOwnName,
    QueueSource,
    QueueTopics,
    RecoveryPoint,
    ReplicateStmt,
    RoleDef,
    SourceFile,
    SowStmt,
    StringLit,
    TopicDef,
    TopologyDef,
    TopologyStmt,
    TransientDurability,
    TypeDef,
    TypeExpr,
    TypeKind,
)
from topoc.dsl.tokens import (
    ContextPathToken,
    DurationToken,
    IdentToken,
    IntToken,
    PlaceholderToken,
    Punct,
    StringToken,
    Token,
    describe,
)
from topoc.errors import CompileError, SourceLocation
from topoc.queue import (
    AtLeastOnce,
    AtMostOnce,
    Delivery,
    DeliveryLimits,
    Duration,
    ExpirationModel,
    Fairness,
    Leasing,
    QueueRecoveryPoint,
)

_DEFINITION_KEYWORDS = (
    "'role', 'topic', 'queue', 'instance', 'list', 'for', 'context' or 'topology'"
)

# The properties of a `queue` block, for error messages.
_QUEUE_PROPERTIES = (
    "'from', 'publish_to', 'definition', 'where', 'delivery', 'priority', 'barrier', "
    "'recovery_point', 'target_depth', 'file_backed_metadata', 'deferred_ack_expiration' "
    "or 'topology'"
)

# The settings each `delivery` mode takes, for error messages.
_AT_LEAST_ONCE_SETTINGS = (
    "'lease', 'leasing', 'max_deliveries', 'max_cancels', 'backlog', "
    "'per_subscription_backlog', 'expiration', 'expiration_model', 'fairness' or '}'"
)
_AT_MOST_ONCE_SETTINGS = (
    "'backlog', 'per_subscription_backlog', 'expiration', 'expiration_model', 'fairness' or '}'"
)

# The kinds a topology block's parameter may take, for error messages.
_PARAM_KINDS = "'group', 'role', 'instance' or 'selector'"

# The definitions a `for` body may hold, for error messages.
_FOR_ITEM_KEYWORDS = "'topic', 'queue', 'instance' or 'for'"


def parse(source: str) -> SourceFile:
    """Parse `.topo` source. Raises `CompileError` on a lexical or syntax error."""
    tokens = tokenize(source)
    # Topology blocks first, so a `use` may precede the block it names.
    blocks = _Parser(tokens, {}).collect_blocks()
    return _Parser(tokens, blocks).parse_file()


def _invalid(message: str, at: SourceLocation) -> NoReturn:
    raise CompileError.at(message, at)


def _unexpected(expected: str, found: Token | None) -> NoReturn:
    if found is None:
        raise CompileError(f"unexpected end of input, expected {expected}", None)
    raise CompileError.at(f"expected {expected}, found {describe(found.value)}", found.location)


def _ensure_new(already_set: bool, what: str, owner: Ident, at: Ident) -> None:
    if already_set:
        _invalid(f"duplicate '{what}' in '{owner.text}'", at.location)


def _each_text(statements: list[TopologyStmt]) -> list[tuple[str, SourceLocation]]:
    """Each name and string in `statements`, with its location: the names a
    statement takes (bare, or quoted with placeholders), its filter and its
    file path."""
    texts: list[tuple[str, SourceLocation]] = []
    for statement in statements:
        match statement:
            case MeshStmt(group=name) | LocalStmt(target=name):
                texts.append((name.text, name.location))
            case ReplicateStmt(source, target, where, _):
                texts += [(source.text, source.location), (target.text, target.location)]
                if where is not None:
                    texts.append((where.value, where.location))
            case SowStmt(target, durability):
                texts.append((target.text, target.location))
                if isinstance(durability, PersistentDurability):
                    texts.append((durability.file.value, durability.file.location))
            case OwnedStmt(owner, inner):
                texts.append((owner.text, owner.location))
                texts += _each_text(inner)
    return texts


def _placeholders(text: str) -> list[str]:
    """The expressions of the `{{...}}` placeholders in `text`, trimmed."""
    return [rest.split("}}", 1)[0].strip() for rest in text.split("{{")[1:]]


def _find_reference(statements: list[TopologyStmt], variable: str) -> SourceLocation | None:
    """Where `statements` refer to `variable`: as a bare name, or as the head of
    a `{{...}}` path in a name or string."""
    for text, location in _each_text(statements):
        if text == variable or any(
            expr.split(".", 1)[0] == variable for expr in _placeholders(text)
        ):
            return location
    return None


def _check_parameter_uses(params: list[Param], statements: list[TopologyStmt]) -> None:
    """A block's statements use each parameter where its kind fits: `mesh`
    takes a group, `owned by` an instance (a selector may be either), and only
    a role has attributes, as `{{role.attr}}`."""
    kinds = {param.name.text: param.kind for param in params}
    for statement in statements:
        match statement:
            case MeshStmt(group=name):
                wanted, needs = "mesh", "a group"
                allowed = (ParamKind.GROUP, ParamKind.SELECTOR)
            case OwnedStmt(owner=name, statements=inner):
                _check_parameter_uses(params, inner)
                wanted, needs = "owned by", "an instance"
                allowed = (ParamKind.INSTANCE, ParamKind.SELECTOR)
            case _:
                continue
        kind = kinds.get(name.text)
        if kind is not None and kind not in allowed:
            _invalid(
                f"'{wanted} {name.text}' needs {needs}; '{name.text}' is a {kind.value} parameter",
                name.location,
            )
    for text, location in _each_text(statements):
        for expr in _placeholders(text):
            head, *rest = expr.split(".")
            kind = kinds.get(head)
            if kind is None or not rest:
                continue
            if kind is not ParamKind.ROLE:
                _invalid(
                    f"'{{{{{expr}}}}}': only a role parameter has attributes ('{head}' is a "
                    f"{kind.value} parameter)",
                    location,
                )
            if len(rest) > 1:
                _invalid(
                    f"'{{{{{expr}}}}}': a role parameter reads one attribute, as "
                    f"'{{{{{head}.attr}}}}'",
                    location,
                )


@dataclass(frozen=True, slots=True)
class _Substitution:
    """The replacement of a block's parameters by a use's arguments."""

    arguments: list[Argument]
    # The loop variables in scope at the use: an argument naming one stands
    # for its value inside a placeholder.
    loop_variables: list[str]

    def _argument(self, name: str) -> Argument | None:
        return next((a for a in self.arguments if a.parameter.text == name), None)

    def name(self, name: Ident) -> Ident:
        """A parameter becomes its argument, keeping the argument's location;
        a quoted name has its placeholders replaced."""
        argument = self._argument(name.text)
        return (
            argument.value if argument is not None else Ident(self.text(name.text), name.location)
        )

    def string(self, string: StringLit) -> StringLit:
        return StringLit(self.text(string.value), string.location)

    def text(self, text: str) -> str:
        """`text` with `{{param}}` replaced by the argument's name (or, for a
        loop variable, by `{{variable}}`) and `{{param.attr}}` by
        `{{argument.attr}}`."""
        out = []
        rest = text
        while "{{" in rest:
            before, after = rest.split("{{", 1)
            out.append(before)
            if "}}" not in after:
                out.append("{{")
                rest = after
                continue
            raw, rest = after.split("}}", 1)
            head, dot, tail = raw.strip().partition(".")
            argument = self._argument(head)
            if argument is None:
                out.append("{{" + raw + "}}")
            elif dot:
                out.append("{{" + f"{argument.value.text}.{tail}" + "}}")
            elif argument.value.text in self.loop_variables:
                out.append("{{" + argument.value.text + "}}")
            else:
                out.append(argument.value.text)
        out.append(rest)
        return "".join(out)

    def statement(self, statement: TopologyStmt) -> TopologyStmt:
        match statement:
            case MeshStmt(group):
                return MeshStmt(self.name(group))
            case LocalStmt(target):
                return LocalStmt(self.name(target))
            case ReplicateStmt(source, target, where, two_way):
                return ReplicateStmt(
                    self.name(source),
                    self.name(target),
                    None if where is None else self.string(where),
                    two_way,
                )
            case SowStmt(target, durability):
                if isinstance(durability, PersistentDurability):
                    durability = PersistentDurability(self.string(durability.file))
                return SowStmt(self.name(target), durability)
            case OwnedStmt(owner, inner):
                return OwnedStmt(self.name(owner), [self.statement(s) for s in inner])


def _match_arguments(
    block: Ident, definition: TopologyDef, given: list[tuple[Ident, Ident]] | None
) -> list[Argument]:
    """The arguments `given` to `definition`, by name: every parameter exactly
    once."""
    params = definition.params
    if not params:
        if given is not None:
            _invalid(
                f"topology block '{block.text}' takes no arguments: write 'use {block.text}'",
                block.location,
            )
        return []
    if given is None:
        expected = ", ".join(f"{param.name.text} = ..." for param in params)
        _invalid(
            f"topology block '{block.text}' takes arguments: write 'use {block.text}({expected})'",
            block.location,
        )
    arguments: list[Argument] = []
    for parameter, value in given:
        param = next((p for p in params if p.name.text == parameter.text), None)
        if param is None:
            names = ", ".join(p.name.text for p in params)
            _invalid(
                f"topology block '{block.text}' has no parameter '{parameter.text}' (its "
                f"parameters are {names})",
                parameter.location,
            )
        if any(a.parameter.text == parameter.text for a in arguments):
            _invalid(f"'{parameter.text}' is given twice", parameter.location)
        arguments.append(Argument(parameter, param.kind, value))
    for param in params:
        if not any(a.parameter.text == param.name.text for a in arguments):
            _invalid(
                f"use of topology block '{block.text}' is missing '{param.name.text}'",
                block.location,
            )
    return arguments


class _Parser:
    def __init__(self, tokens: list[Token], blocks: dict[str, TopologyDef]) -> None:
        self._tokens = tokens
        self._pos = 0
        # Every topology block in the file, by name, which `use` inlines.
        self._blocks = blocks
        # The variables of the `for` blocks enclosing the current position, with
        # the location of their `for`, which a used block may not refer to.
        self._loop_variables: list[tuple[Ident, SourceLocation]] = []
        # Whether a topology block is being parsed (it cannot use another).
        self._in_block = False
        # Every `use` of a block so far, in source order.
        self._block_uses: list[BlockUse] = []

    def collect_blocks(self) -> dict[str, TopologyDef]:
        """The file's topology blocks, by name: each `topology <name>` starting a
        top-level definition (at the start, or after a definition's closing `}`
        or `]`). Of two blocks sharing a name, the first is kept; the resolver
        rejects the second (V1)."""
        blocks: dict[str, TopologyDef] = {}
        depth = 0
        at_definition = True
        while (token := self._peek()) is not None:
            starts_block = depth == 0 and at_definition and token.value == IdentToken("topology")
            at_definition = depth == 0 and token.value in (Punct.RBRACE, Punct.RBRACKET)
            if token.value == Punct.LBRACE:
                depth += 1
            elif token.value == Punct.RBRACE:
                depth = max(depth - 1, 0)
                at_definition = depth == 0
            self._pos += 1
            if starts_block:
                block = self._parse_topology_block()
                blocks.setdefault(block.name.text, block)
                at_definition = True
        return blocks

    def _parse_topology_block(self) -> TopologyDef:
        """`topology <name>[(<param>: <kind>, ...)] { ... }` after the keyword."""
        name = self._expect_ident("topology block name")
        params = self._parse_params(name) if self._eat_punct(Punct.LPAREN) else []
        self._in_block = True
        try:
            statements = self._parse_topology()
        finally:
            self._in_block = False
        if not statements:
            _invalid(f"topology block '{name.text}' places nothing", name.location)
        _check_parameter_uses(params, statements)
        return TopologyDef(name, params, statements)

    def _parse_params(self, block: Ident) -> list[Param]:
        """`<param>: <kind>, ... )` after a block's `(`."""
        if self._eat_punct(Punct.RPAREN):
            _invalid(
                f"topology block '{block.text}' declares no parameters: write it without '()'",
                block.location,
            )
        params: list[Param] = []
        while True:
            name = self._expect_ident("parameter name")
            if "." in name.text:
                _invalid(f"parameter name '{name.text}' may not contain '.'", name.location)
            if any(param.name.text == name.text for param in params):
                _invalid(f"parameter '{name.text}' is declared twice", name.location)
            self._expect_punct(Punct.COLON, "':' and the parameter's kind")
            kind = self._expect_ident(_PARAM_KINDS)
            known = next((k for k in ParamKind if k.value == kind.text), None)
            if known is None:
                _invalid(
                    f"unknown parameter kind '{kind.text}' (expected {_PARAM_KINDS})",
                    kind.location,
                )
            params.append(Param(name, known))
            if self._eat_punct(Punct.RPAREN):
                return params
            self._expect_punct(Punct.COMMA, "',' or ')'")

    def _parse_arguments(self, block: Ident) -> list[tuple[Ident, Ident]]:
        """`<param> = <argument>, ... )` after a `use`'s `(`."""
        if self._eat_punct(Punct.RPAREN):
            _invalid(
                f"'use {block.text}()' passes nothing: write 'use {block.text}'", block.location
            )
        arguments = []
        while True:
            parameter = self._expect_ident("parameter name")
            self._expect_punct(Punct.EQ, "'=' and the argument")
            arguments.append((parameter, self._parse_name("argument")))
            if self._eat_punct(Punct.RPAREN):
                return arguments
            self._expect_punct(Punct.COMMA, "',' or ')'")

    def _inline_block(
        self, block: Ident, given: list[tuple[Ident, Ident]] | None
    ) -> list[TopologyStmt]:
        """The statements `use <block>(<arguments>)` stands for, each parameter
        replaced by its argument. The block may not refer to a loop variable in
        scope here, so it means the same wherever it is used."""
        if self._in_block:
            _invalid(
                f"a topology block cannot use another ('use {block.text}'): write its "
                "statements here",
                block.location,
            )
        definition = self._blocks.get(block.text)
        if definition is None:
            _invalid(f"unknown topology block '{block.text}'", block.location)
        arguments = _match_arguments(block, definition, given)
        params = {param.name.text for param in definition.params}
        for variable, loop in self._loop_variables:
            if variable.text in params:
                continue
            reference = _find_reference(definition.statements, variable.text)
            if reference is not None:
                _invalid(
                    f"topology block '{block.text}' refers to '{variable.text}' (line "
                    f"{reference.line}, column {reference.column}), the variable of the "
                    f"enclosing 'for' at line {loop.line}, column {loop.column}: a block cannot "
                    "see loop variables, so it means the same wherever it is used",
                    block.location,
                )
        substitution = _Substitution(arguments, [v.text for v, _ in self._loop_variables])
        self._block_uses.append(BlockUse(block, arguments))
        return [substitution.statement(statement) for statement in definition.statements]

    def parse_file(self) -> SourceFile:
        definitions: list[Definition] = []
        context: SourceLocation | None = None
        while self._peek() is not None:
            keyword = self._expect_ident(_DEFINITION_KEYWORDS)
            match keyword.text:
                case "role":
                    definitions.append(self._parse_role())
                case "topic":
                    definitions.append(self._parse_topic())
                case "queue":
                    definitions.append(self._parse_queue(keyword.location))
                case "instance":
                    definitions.append(self._parse_instance())
                case "list":
                    definitions.append(self._parse_list())
                case "for":
                    definitions.append(self._parse_for(keyword.location))
                case "topology":
                    definitions.append(self._parse_topology_block())
                case "context":
                    if context is not None:
                        _invalid(
                            "duplicate 'context' block "
                            f"(the first is at line {context.line}, column {context.column})",
                            keyword.location,
                        )
                    context = keyword.location
                    definitions.append(self._parse_context(keyword.location))
                case other:
                    _invalid(f"expected {_DEFINITION_KEYWORDS}, found '{other}'", keyword.location)
        return SourceFile(definitions, self._block_uses)

    # ---- token-level helpers ------------------------------------------------

    def _peek(self) -> Token | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _advance(self) -> Token | None:
        token = self._peek()
        if token is not None:
            self._pos += 1
        return token

    def _expect_ident(self, expected: str) -> Ident:
        token = self._advance()
        if token is not None and isinstance(token.value, IdentToken):
            return Ident(token.value.text, token.location)
        _unexpected(expected, token)

    def _expect_string(self, expected: str) -> StringLit:
        token = self._advance()
        if token is not None and isinstance(token.value, StringToken):
            return StringLit(token.value.value, token.location)
        _unexpected(expected, token)

    def _expect_punct(self, punct: Punct, expected: str) -> None:
        token = self._advance()
        if token is None or token.value != punct:
            _unexpected(expected, token)

    def _second_is(self, punct: Punct) -> bool:
        """Whether the token after the next one is `punct`."""
        return self._pos + 1 < len(self._tokens) and self._tokens[self._pos + 1].value == punct

    def _next_is(self, punct: Punct) -> bool:
        token = self._peek()
        return token is not None and token.value == punct

    def _eat_punct(self, punct: Punct) -> bool:
        token = self._peek()
        if token is not None and token.value == punct:
            self._pos += 1
            return True
        return False

    def _eat_keyword(self, keyword: str) -> bool:
        token = self._peek()
        if token is not None and token.value == IdentToken(keyword):
            self._pos += 1
            return True
        return False

    def _expect_definition_path(self, expected: str) -> Ident:
        """`IDENT { "::" IDENT }`, joined back into one `::`-separated name."""
        first = self._expect_ident(expected)
        text = first.text
        while self._eat_punct(Punct.COLON_COLON):
            text += "::" + self._expect_ident("a path segment after '::'").text
        return Ident(text, first.location)

    def _parse_literal(self) -> tuple[Literal, SourceLocation]:
        token = self._advance()
        if token is not None:
            match token.value:
                case StringToken(value):
                    return value, token.location
                case IntToken(value):
                    return value, token.location
                case IdentToken("true"):
                    return True, token.location
                case IdentToken("false"):
                    return False, token.location
        _unexpected("a literal (string, integer, 'true' or 'false')", token)

    # ---- roles --------------------------------------------------------------

    def _parse_role(self) -> RoleDef:
        name = self._expect_ident("role name")
        self._expect_punct(Punct.LBRACE, "'{'")
        attrs: list[AttrDecl] = []
        while not self._eat_punct(Punct.RBRACE):
            keyword = self._expect_ident("'attr' or '}'")
            if keyword.text != "attr":
                _invalid(f"expected 'attr' or '}}', found '{keyword.text}'", keyword.location)
            attr_name = self._expect_ident("attribute name")
            if "." in attr_name.text:
                _invalid(
                    f"attribute name '{attr_name.text}' may not contain '.'", attr_name.location
                )
            if any(attr.name.text == attr_name.text for attr in attrs):
                _invalid(
                    f"duplicate attribute '{attr_name.text}' in role '{name.text}'",
                    attr_name.location,
                )
            self._expect_punct(
                Punct.COLON,
                "':' (attribute declarations require an explicit type, e.g. 'attr key: string')",
            )
            attr_type = self._parse_attr_type()
            default = self._parse_literal()[0] if self._eat_punct(Punct.EQ) else None
            attrs.append(AttrDecl(attr_name, attr_type, default))
        return RoleDef(name, attrs)

    def _parse_attr_type(self) -> AttrType:
        spelling = self._expect_ident("attribute type ('string', 'int' or 'bool')")
        try:
            return AttrType(spelling.text)
        except ValueError:
            _invalid(
                f"unknown attribute type '{spelling.text}' (expected 'string', 'int' or 'bool')",
                spelling.location,
            )

    # ---- topics -------------------------------------------------------------

    def _parse_name(self, what: str) -> Ident:
        """An identifier or a non-empty quoted string; strings allow characters
        such as `/` and, in a `for` block, `{{...}}` placeholders. `what` names
        the expected name in error messages."""
        token = self._advance()
        if token is not None:
            match token.value:
                case IdentToken(text):
                    return Ident(text, token.location)
                case StringToken(""):
                    _invalid(f"{what} may not be empty", token.location)
                case StringToken(value):
                    return Ident(value, token.location)
        _unexpected(f"{what} (a name or a quoted string)", token)

    def _parse_names(self, what: str) -> list[Ident]:
        """`<name> { "," <name> }`: one or more distinct names, for a topology
        statement that applies to each of them."""
        names = [self._parse_name(what)]
        while self._eat_punct(Punct.COMMA):
            name = self._parse_name(what)
            if any(seen.text == name.text for seen in names):
                _invalid(f"'{name.text}' is listed twice", name.location)
            names.append(name)
        return names

    def _parse_queue(self, keyword: SourceLocation) -> QueueDef:
        """`queue <name> { <property>... }`. `keyword` locates the block for
        the errors about what it lacks."""
        name = self._parse_name("queue name")
        self._expect_punct(Punct.LBRACE, "'{'")
        draft = _QueueDraft()
        while not self._eat_punct(Punct.RBRACE):
            prop = self._expect_ident(f"a queue property ({_QUEUE_PROPERTIES}) or '}}'")
            self._parse_queue_property(name, prop, draft)
        return draft.finish(name, keyword)

    def _parse_queue_property(self, queue: Ident, prop: Ident, draft: "_QueueDraft") -> None:
        """One property of queue `queue`, into `draft`."""

        def fresh(is_set: bool) -> None:
            _ensure_new(is_set, prop.text, queue, prop)

        match prop.text:
            case "from":
                fresh(draft.sources is not None)
                draft.sources = self._parse_names("source topic name")
            case "publish_to":
                fresh(draft.publish_to is not None)
                draft.publish_to = self._parse_name("topic name")
            case "definition":
                fresh(draft.definition is not None)
                draft.definition = self._expect_definition_path("queue definition path")
            case "where":
                fresh(draft.filter is not None)
                draft.filter = self._expect_string("a filter string")
            case "delivery":
                fresh(draft.delivery is not None)
                draft.delivery = self._parse_delivery()
            case "priority" | "barrier":
                if draft.ordering is not None:
                    _invalid(
                        f"queue '{queue.text}' has both '{draft.ordering[1].text}' and "
                        f"'{prop.text}': AMPS orders a queue by priority or by barriers, not both",
                        prop.location,
                    )
                ordering: QueueOrdering
                if prop.text == "priority":
                    path = self._expect_string("a field path string")
                    _check_field_path(path)
                    ordering = Priority(path)
                else:
                    ordering = Barrier(self._expect_string("a filter string"))
                draft.ordering = (ordering, prop)
            case "recovery_point":
                fresh(draft.recovery_point is not None)
                draft.recovery_point = self._parse_choice(
                    "'epoch', 'now' or 'creation'", list(QueueRecoveryPoint)
                )
            case "target_depth":
                fresh(draft.target_depth is not None)
                draft.target_depth = (self._expect_count("a target depth"), prop)
            case "file_backed_metadata":
                fresh(draft.file_backed_metadata)
                draft.file_backed_metadata = True
            case "deferred_ack_expiration":
                fresh(draft.deferred_ack_expiration is not None)
                draft.deferred_ack_expiration = self._expect_duration()
            case "topology":
                fresh(draft.topology is not None)
                draft.topology = self._parse_topology()
            case other:
                _invalid(
                    f"unknown queue property '{other}' (expected {_QUEUE_PROPERTIES})",
                    prop.location,
                )

    def _parse_delivery(self) -> Delivery:
        """`at_least_once { lease <duration> ... }` or `at_most_once { ... }`."""
        mode = self._expect_ident("'at_least_once' or 'at_most_once'")
        if mode.text not in ("at_least_once", "at_most_once"):
            _invalid(
                f"unknown delivery '{mode.text}' (expected 'at_least_once' or 'at_most_once')",
                mode.location,
            )
        at_least_once = mode.text == "at_least_once"
        expected = _AT_LEAST_ONCE_SETTINGS if at_least_once else _AT_MOST_ONCE_SETTINGS
        self._expect_punct(Punct.LBRACE, "'{'")
        draft = _DeliveryDraft()
        while not self._eat_punct(Punct.RBRACE):
            setting = self._expect_ident(expected)
            self._parse_delivery_setting(mode, at_least_once, setting, draft)
        limits = DeliveryLimits(
            draft.backlog,
            draft.per_subscription_backlog,
            draft.expiration,
            draft.expiration_model,
            draft.fairness,
        )
        if not at_least_once:
            return AtMostOnce(limits)
        if draft.lease is None:
            _invalid(
                "'at_least_once' delivery requires a 'lease' (how long a subscriber may hold a "
                "message)",
                mode.location,
            )
        return AtLeastOnce(
            draft.lease, draft.leasing, draft.max_deliveries, draft.max_cancels, limits
        )

    def _parse_delivery_setting(
        self, mode: Ident, at_least_once: bool, setting: Ident, draft: "_DeliveryDraft"
    ) -> None:
        """One setting of a `delivery` block in `mode`, into `draft`."""

        def fresh(is_set: bool) -> None:
            if is_set:
                _invalid(f"duplicate '{setting.text}' in '{mode.text}'", setting.location)

        match (setting.text, at_least_once):
            case ("lease", True):
                fresh(draft.lease is not None)
                draft.lease = self._expect_duration()
            case ("leasing", True):
                fresh(draft.leasing is not None)
                draft.leasing = self._parse_choice("'strict' or 'sublet'", list(Leasing))
            case ("max_deliveries", True):
                fresh(draft.max_deliveries is not None)
                draft.max_deliveries = self._expect_count("a delivery count")
            case ("max_cancels", True):
                fresh(draft.max_cancels is not None)
                draft.max_cancels = self._expect_count("a cancel count")
            case ("backlog", _):
                fresh(draft.backlog is not None)
                draft.backlog = self._expect_count("a backlog")
            case ("per_subscription_backlog", _):
                fresh(draft.per_subscription_backlog is not None)
                draft.per_subscription_backlog = self._expect_count("a backlog")
            case ("expiration", _):
                fresh(draft.expiration is not None)
                draft.expiration = self._expect_duration()
            case ("expiration_model", _):
                fresh(draft.expiration_model is not None)
                draft.expiration_model = self._parse_choice(
                    "'queue', 'latest', 'earliest' or 'default'", list(ExpirationModel)
                )
            case ("fairness", _):
                fresh(draft.fairness is not None)
                fairness = self._parse_choice(
                    "'fast', 'round_robin' or 'proportional'", list(Fairness)
                )
                if not at_least_once and fairness is not Fairness.ROUND_ROBIN:
                    _invalid(
                        "'at_most_once' delivery only supports 'round_robin' fairness",
                        setting.location,
                    )
                draft.fairness = fairness
            case ("lease" | "leasing" | "max_deliveries" | "max_cancels", False):
                _invalid(
                    f"'{setting.text}' applies only to 'at_least_once' delivery (at most once "
                    "removes a message when it is sent)",
                    setting.location,
                )
            case (other, _):
                expected = _AT_LEAST_ONCE_SETTINGS if at_least_once else _AT_MOST_ONCE_SETTINGS
                _invalid(
                    f"unknown delivery setting '{other}' (expected {expected})", setting.location
                )

    def _parse_choice[E: Enum](self, expected: str, choices: list[E]) -> E:
        """One of `choices`, written as its value."""
        word = self._expect_ident(expected)
        for choice in choices:
            if choice.value == word.text:
                return choice
        _invalid(f"unknown value '{word.text}' (expected {expected})", word.location)

    def _expect_count(self, what: str) -> int:
        """A positive integer: a count AMPS treats `0` as "unset" for, so `0` is
        rejected rather than silently meaning something else."""
        token = self._advance()
        if token is not None and isinstance(token.value, IntToken):
            if token.value.value <= 0:
                _invalid(
                    f"{what} must be a positive integer, got {token.value.value}", token.location
                )
            return token.value.value
        _unexpected(f"{what} (a positive integer)", token)

    def _expect_duration(self) -> Duration:
        token = self._advance()
        if token is not None and isinstance(token.value, DurationToken):
            return token.value.value
        _unexpected("a duration (such as '60s' or '1d')", token)

    def _parse_topic(self) -> TopicDef:
        name = self._parse_name("topic name")
        self._expect_punct(Punct.LBRACE, "'{'")
        definition: Ident | None = None
        topology: list[TopologyStmt] | None = None
        while not self._eat_punct(Punct.RBRACE):
            keyword = self._expect_ident("a topic property ('definition' or 'topology') or '}'")
            match keyword.text:
                case "definition":
                    _ensure_new(definition is not None, "definition", name, keyword)
                    definition = self._expect_definition_path("topic definition path")
                case "topology":
                    _ensure_new(topology is not None, "topology", name, keyword)
                    topology = self._parse_topology()
                case other:
                    _invalid(
                        f"unknown topic property '{other}' (expected 'definition' or 'topology')",
                        keyword.location,
                    )
        return TopicDef(name, definition, topology)

    def _parse_topology(self) -> list[TopologyStmt]:
        """A statement naming several sets (`sow A, B { ... }`,
        `replicate A, B -> C, D`) is read as one statement per name — per source
        and target pair for `replicate` — in the order written."""
        self._expect_punct(Punct.LBRACE, "'{'")
        statements: list[TopologyStmt] = []
        while not self._eat_punct(Punct.RBRACE):
            keyword = self._expect_ident(
                "'mesh', 'replicate', 'local', 'sow', 'owned', 'use' or '}'"
            )
            match keyword.text:
                case "owned":
                    statements.append(self._parse_owned(keyword))
                case "use":
                    while True:
                        block = self._expect_ident("topology block name")
                        given = (
                            self._parse_arguments(block) if self._eat_punct(Punct.LPAREN) else None
                        )
                        statements += self._inline_block(block, given)
                        if not self._eat_punct(Punct.COMMA):
                            break
                case _:
                    statements += self._parse_topology_statement(keyword)
        return statements

    def _parse_topology_statement(self, keyword: Ident) -> list[TopologyStmt]:
        """One topology statement after its `keyword` (several when it names
        lists)."""
        match keyword.text:
            case "mesh":
                return [MeshStmt(g) for g in self._parse_names("group name")]
            case "local":
                targets = self._parse_names("role, group or instance name")
                return [LocalStmt(target) for target in targets]
            case "replicate":
                return list(self._parse_replicate())
            case "sow":
                return list(self._parse_sow(keyword))
            case other:
                _invalid(
                    f"unknown topology statement '{other}' "
                    "(expected 'mesh', 'replicate', 'local', 'sow', 'owned' or 'use')",
                    keyword.location,
                )

    def _parse_owned(self, keyword: Ident) -> OwnedStmt:
        """`owned by <instance> { mesh ... replicate ... <-> ... }`: only the
        statements that share a queue may appear inside."""
        if not self._eat_keyword("by"):
            _invalid("expected 'owned by <instance> { ... }'", keyword.location)
        owner = self._parse_name("owner instance name")
        self._expect_punct(Punct.LBRACE, "'{'")
        statements: list[TopologyStmt] = []
        while not self._eat_punct(Punct.RBRACE):
            inner = self._expect_ident("'mesh', 'replicate' or '}'")
            if inner.text not in ("mesh", "replicate"):
                _invalid(
                    "an 'owned by' block takes only 'mesh' and two-way 'replicate ... <-> ...', "
                    f"which share the owned queue; found '{inner.text}'",
                    inner.location,
                )
            parsed = self._parse_topology_statement(inner)
            if any(isinstance(st, ReplicateStmt) and not st.two_way for st in parsed):
                _invalid(
                    "an 'owned by' block shares one queue, which AMPS requires to be "
                    "replicated both ways: write 'replicate ... <-> ...'",
                    inner.location,
                )
            statements += parsed
        if not statements:
            _invalid(f"'owned by {owner.text}' block places nothing", owner.location)
        return OwnedStmt(owner, statements)

    def _parse_replicate(self) -> list[ReplicateStmt]:
        """One statement per source and target pair, sharing the filter."""
        sources = self._parse_names("source name")
        two_way = self._eat_punct(Punct.TWO_WAY_ARROW)
        if not two_way:
            self._expect_punct(Punct.ARROW, "'->', '<->' or ','")
        targets = self._parse_names("target name")
        where: StringLit | None = None
        if self._eat_keyword("where"):
            where = self._expect_string("a filter string")
        return [
            ReplicateStmt(source, target, where, two_way)
            for source in sources
            for target in targets
        ]

    def _parse_sow(self, keyword: Ident) -> list[SowStmt]:
        """`sow <role|group|instance>, ... { durability <persistent|transient> {...} }`:
        one statement per target, sharing the durability. `keyword` locates the
        statement."""
        targets = self._parse_names("role, group or instance name")
        self._expect_punct(Punct.LBRACE, "'{'")
        durability: Durability | None = None
        while not self._eat_punct(Punct.RBRACE):
            keyword = self._expect_ident("a sow property ('durability') or '}'")
            if keyword.text != "durability":
                _invalid(
                    f"unknown sow property '{keyword.text}' (expected 'durability')",
                    keyword.location,
                )
            if durability is not None:
                _invalid("duplicate 'durability' in 'sow' block", keyword.location)
            durability = self._parse_durability()
        if durability is None:
            names = ", ".join(target.text for target in targets)
            _invalid(f"'sow {names}' requires a 'durability'", keyword.location)
        return [SowStmt(target, durability) for target in targets]

    def _parse_durability(self) -> Durability:
        kind = self._expect_ident("'persistent' or 'transient'")
        self._expect_punct(Punct.LBRACE, "'{'")
        match kind.text:
            case "persistent":
                return self._parse_persistent(kind)
            case "transient":
                return self._parse_transient(kind)
            case other:
                _invalid(
                    f"unknown durability kind '{other}' (expected 'persistent' or 'transient')",
                    kind.location,
                )

    def _parse_persistent(self, kind: Ident) -> PersistentDurability:
        file: StringLit | None = None
        while not self._eat_punct(Punct.RBRACE):
            keyword = self._expect_ident("'file' or '}'")
            if keyword.text != "file":
                _invalid(
                    f"unknown durability property '{keyword.text}' ('persistent' expects 'file')",
                    keyword.location,
                )
            if file is not None:
                _invalid("duplicate 'file'", keyword.location)
            file = self._expect_string("a file path string")
        if file is None:
            _invalid("'durability persistent' requires a 'file' property", kind.location)
        return PersistentDurability(file)

    def _parse_transient(self, kind: Ident) -> TransientDurability:
        recovery_point: RecoveryPoint | None = None
        while not self._eat_punct(Punct.RBRACE):
            keyword = self._expect_ident("'recovery_point' or '}'")
            if keyword.text != "recovery_point":
                _invalid(
                    f"unknown durability property '{keyword.text}' "
                    "('transient' expects 'recovery_point')",
                    keyword.location,
                )
            if recovery_point is not None:
                _invalid("duplicate 'recovery_point'", keyword.location)
            if self._eat_keyword("now"):
                recovery_point = RecoveryPoint.NOW
            elif self._eat_keyword("epoch"):
                recovery_point = RecoveryPoint.EPOCH
            else:
                _unexpected("'now' or 'epoch'", self._advance())
        if recovery_point is None:
            _invalid("'durability transient' requires a 'recovery_point' property", kind.location)
        return TransientDurability(recovery_point)

    # ---- lists and iteration ------------------------------------------------

    def _parse_list(self) -> ListDef:
        """`list <name> [ <item>, ... ]`"""
        name = self._expect_ident("list name")
        return ListDef(name, self._parse_items(f"list '{name.text}'"))

    def _parse_items(self, owner: str) -> list[StringLit]:
        """`[ <item>, ... ]`: one or more distinct identifiers or quoted strings.
        `owner` names the list in error messages."""
        self._expect_punct(Punct.LBRACKET, "'['")
        items = [self._expect_item()]
        while self._eat_punct(Punct.COMMA):
            item = self._expect_item()
            if any(seen.value == item.value for seen in items):
                _invalid(f"duplicate item '{item.value}' in {owner}", item.location)
            items.append(item)
        self._expect_punct(Punct.RBRACKET, "']' or ','")
        return items

    def _expect_item(self) -> StringLit:
        token = self._advance()
        if token is not None:
            match token.value:
                case IdentToken(text):
                    return StringLit(text, token.location)
                case StringToken(value):
                    return StringLit(value, token.location)
        _unexpected("a list item (a name or a quoted string)", token)

    def _parse_loop_variable(self) -> Ident:
        """A loop variable: a single segment, so `{{v.field}}` splits unambiguously."""
        variable = self._expect_ident("loop variable name")
        if "." in variable.text:
            _invalid(f"loop variable '{variable.text}' may not contain '.'", variable.location)
        return variable

    def _parse_for(self, at: SourceLocation) -> ForDef:
        """`for <var>[, <value>] in <domain> { ... }`: the body holds topic,
        instance and nested `for` templates."""
        variable = self._parse_loop_variable()
        value: Ident | None = None
        if self._eat_punct(Punct.COMMA):
            value = self._parse_loop_variable()
            if value.text == variable.text:
                _invalid(f"loop variables must differ, both are '{value.text}'", value.location)
        if not self._eat_keyword("in"):
            _unexpected("'in'", self._advance())
        domain: ForDomain
        if self._next_is(Punct.LBRACKET):
            domain = self._parse_items("inline list")
        else:
            token = self._advance()
            match token:
                case Token(value=IdentToken(text)):
                    domain = Ident(text, token.location)
                case Token(value=ContextPathToken(path)):
                    domain = ContextPath(path, token.location)
                case _:
                    _unexpected(
                        "a list, role, group or instance name, a loop variable's field, a "
                        "context path '@...', or an inline list '[...]'",
                        token,
                    )
        self._expect_punct(Punct.LBRACE, "'{'")
        scoped = len(self._loop_variables)
        self._loop_variables.append((variable, at))
        if value is not None:
            self._loop_variables.append((value, at))
        try:
            body = self._parse_for_body()
        finally:
            del self._loop_variables[scoped:]
        if not body:
            _invalid("'for' block defines nothing", at)
        return ForDef(at, variable, value, domain, body)

    def _parse_for_body(self) -> list[ForItem]:
        """A `for` block's body, after its `{`, through its `}`."""
        body: list[ForItem] = []
        while not self._eat_punct(Punct.RBRACE):
            keyword = self._expect_ident(f"{_FOR_ITEM_KEYWORDS} or '}}'")
            match keyword.text:
                case "topic":
                    body.append(self._parse_topic())
                case "instance":
                    body.append(self._parse_instance())
                case "for":
                    body.append(self._parse_for(keyword.location))
                case "queue":
                    body.append(self._parse_queue(keyword.location))
                case other:
                    _invalid(
                        f"only {_FOR_ITEM_KEYWORDS} definitions may appear in a 'for' block, "
                        f"found '{other}'",
                        keyword.location,
                    )
        return body

    # ---- context declarations -----------------------------------------------

    def _parse_context(self, at: SourceLocation) -> ContextDef:
        """`context { type <name> { <fields> } <fields> }`"""
        self._expect_punct(Punct.LBRACE, "'{'")
        types: list[TypeDef] = []
        fields: list[FieldDecl] = []
        while not self._eat_punct(Punct.RBRACE):
            # `type` followed by ':' is a field named `type`.
            if not self._second_is(Punct.COLON) and self._eat_keyword("type"):
                name = self._expect_ident("type name")
                if "." in name.text:
                    _invalid(f"type name '{name.text}' may not contain '.'", name.location)
                for first in types:
                    if first.name.text == name.text:
                        _invalid(
                            f"duplicate type '{name.text}' (the first is at line "
                            f"{first.name.location.line}, column {first.name.location.column})",
                            name.location,
                        )
                self._expect_punct(Punct.LBRACE, "'{'")
                type_fields: list[FieldDecl] = []
                while not self._eat_punct(Punct.RBRACE):
                    self._parse_field(type_fields, f"type '{name.text}'")
                types.append(TypeDef(name, type_fields))
            else:
                self._parse_field(fields, "context")
        return ContextDef(at, types, fields)

    def _parse_field(self, fields: list[FieldDecl], owner: str) -> None:
        """`<name>: <type>`, appended to `fields`; `owner` names the record in
        error messages."""
        name = self._expect_ident("field name or '}'")
        if "." in name.text:
            _invalid(f"field name '{name.text}' may not contain '.'", name.location)
        if any(field.name.text == name.text for field in fields):
            _invalid(f"duplicate field '{name.text}' in {owner}", name.location)
        self._expect_punct(Punct.COLON, "':' and the field's type")
        token = self._peek()
        type_location = token.location if token is not None else name.location
        fields.append(FieldDecl(name, self._parse_type(), type_location))

    def _parse_type(self) -> TypeExpr:
        """`string`, `int`, `bool` or a type name, followed by any number of `[]`
        (list) or `[string]` (map) suffixes."""
        base = self._expect_ident("a type ('string', 'int', 'bool' or a type name)")
        type_expr = (
            TypeExpr(TypeKind(base.text), None, None)
            if base.text in ("string", "int", "bool")
            else TypeExpr(TypeKind.RECORD, base, None)
        )
        while self._eat_punct(Punct.LBRACKET):
            if self._eat_punct(Punct.RBRACKET):
                type_expr = TypeExpr(TypeKind.LIST, None, type_expr)
                continue
            key = self._expect_ident("']' (a list) or 'string' (a map's key type)")
            if key.text != "string":
                _invalid(f"map keys must be 'string', found '{key.text}'", key.location)
            self._expect_punct(Punct.RBRACKET, "']'")
            type_expr = TypeExpr(TypeKind.MAP, None, type_expr)
        return type_expr

    # ---- instances ----------------------------------------------------------

    def _parse_instance(self) -> InstanceDef:
        name = self._parse_name("instance name")
        self._expect_punct(Punct.LBRACE, "'{'")
        roles: list[Ident] | None = None
        group: Ident | None = None
        attrs: list[AttrAssign] = []
        while not self._eat_punct(Punct.RBRACE):
            keyword = self._expect_ident("an instance property ('roles', 'group' or 'attr') or '}'")
            match keyword.text:
                case "roles":
                    _ensure_new(roles is not None, "roles", name, keyword)
                    roles = self._parse_role_list()
                case "group":
                    _ensure_new(group is not None, "group", name, keyword)
                    group = self._parse_name("group name")
                case "attr":
                    attrs.append(self._parse_attr_assign(name, attrs))
                case other:
                    _invalid(
                        f"unknown instance property '{other}' "
                        "(expected 'roles', 'group' or 'attr')",
                        keyword.location,
                    )
        return InstanceDef(name, roles or [], group, attrs)

    def _parse_role_list(self) -> list[Ident]:
        self._expect_punct(Punct.LBRACKET, "'['")
        roles = [self._expect_ident("role name")]
        while self._eat_punct(Punct.COMMA):
            roles.append(self._expect_ident("role name"))
        self._expect_punct(Punct.RBRACKET, "']' or ','")
        return roles

    def _parse_attr_assign(self, instance: Ident, existing: list[AttrAssign]) -> AttrAssign:
        """`attr <Role.attr> <value>`: attribute names never contain dots, so
        the qualified name splits unambiguously at its last dot."""
        qualified = self._expect_ident("qualified attribute name 'Role.attr'")
        role_text, dot, attr_text = qualified.text.rpartition(".")
        if not dot:
            _invalid(
                "attribute assignments require a qualified name 'Role.attr', "
                f"got '{qualified.text}'",
                qualified.location,
            )
        if any(a.role.text == role_text and a.attr.text == attr_text for a in existing):
            _invalid(
                f"duplicate attribute assignment '{qualified.text}' in instance '{instance.text}'",
                qualified.location,
            )
        value, value_location = self._parse_attr_value()
        return AttrAssign(
            Ident(role_text, qualified.location),
            Ident(attr_text, qualified.location),
            value,
            value_location,
        )

    def _parse_attr_value(self) -> tuple[Literal | Placeholder, SourceLocation]:
        """An attribute assignment's value: a literal or a bare `{{path}}`."""
        token = self._peek()
        if token is not None and isinstance(token.value, PlaceholderToken):
            self._pos += 1
            return Placeholder(token.value.expr), token.location
        return self._parse_literal()


def _check_field_path(text: StringLit) -> None:
    """A `priority` is a single absolute field path (`/urgency`, `/book/rank`)."""
    segments = text.value[1:].split("/") if text.value.startswith("/") else []
    if not segments or not all(_FIELD_SEGMENT.fullmatch(segment) for segment in segments):
        _invalid(
            f"a queue 'priority' is a single field path such as '/urgency', got '{text.value}'",
            text.location,
        )


_FIELD_SEGMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(slots=True)
class _QueueDraft:
    """A `queue` block's properties as read so far."""

    sources: list[Ident] | None = None
    publish_to: Ident | None = None
    definition: Ident | None = None
    filter: StringLit | None = None
    delivery: Delivery | None = None
    # With the keyword that set it, for errors.
    ordering: tuple[QueueOrdering, Ident] | None = None
    recovery_point: QueueRecoveryPoint | None = None
    # With the keyword that set it, for errors.
    target_depth: tuple[int, Ident] | None = None
    file_backed_metadata: bool = False
    deferred_ack_expiration: Duration | None = None
    topology: list[TopologyStmt] | None = None

    def finish(self, name: Ident, keyword: SourceLocation) -> QueueDef:
        """The queue, once every required property is present and no two
        properties conflict. `keyword` locates errors about the block."""
        if self.target_depth is not None and self.ordering is not None:
            _invalid(
                f"queue '{name.text}' has both '{self.target_depth[1].text}' and "
                f"'{self.ordering[1].text}': AMPS cannot keep a target depth for a queue "
                "ordered by priority or barriers",
                self.target_depth[1].location,
            )

        def missing(what: str) -> NoReturn:
            _invalid(f"queue '{name.text}' requires '{what}'", keyword)

        source: QueueSource
        if self.sources is not None and self.definition is not None:
            _invalid(
                f"queue '{name.text}' has both 'from' and 'definition': a queue reads either "
                "other topics ('from') or the topic of its own name ('definition')",
                self.definition.location,
            )
        if self.sources is not None:
            source = QueueTopics(self.sources, self.publish_to)
        elif self.definition is not None:
            if self.publish_to is not None:
                _invalid(
                    f"queue '{name.text}' reads the topic of its own name, so it takes no "
                    "'publish_to' (only a queue reading 'from' other topics does)",
                    self.publish_to.location,
                )
            source = QueueOwnName(self.definition)
        else:
            _invalid(
                f"queue '{name.text}' requires 'from' (the topics it reads) or 'definition' (the "
                "contract of the topic of its own name, which it then reads)",
                keyword,
            )
        if self.delivery is None:
            missing("delivery")
        if self.topology is None:
            missing("topology")
        return QueueDef(
            name,
            source,
            self.filter,
            self.delivery,
            None if self.ordering is None else self.ordering[0],
            self.recovery_point,
            None if self.target_depth is None else self.target_depth[0],
            self.file_backed_metadata,
            self.deferred_ack_expiration,
            self.topology,
        )


@dataclass(slots=True)
class _DeliveryDraft:
    """A `delivery` block's settings as read so far."""

    lease: Duration | None = None
    leasing: Leasing | None = None
    max_deliveries: int | None = None
    max_cancels: int | None = None
    backlog: int | None = None
    per_subscription_backlog: int | None = None
    expiration: Duration | None = None
    expiration_model: ExpirationModel | None = None
    fairness: Fairness | None = None
