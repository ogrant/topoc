"""Completion: what may be typed at a position in a `.topo` source.

Text being typed seldom parses, so completion reads it in two halves. What is
declared (roles, groups, blocks, context fields, ...) comes from the last
syntax tree of the document that parsed. What fits at the cursor comes from a
lenient scan of the text before it: the block it is in (top level, a topic, a
`topology { }`, an instance, a `for`, a `sow`, a `delivery`, ...) and the
keyword just before it (`mesh`, `use`, `roles [`, `in`, `->`, `durability`,
...), or the `{{...}}` or `@path` it is in.
"""

import re
from dataclasses import dataclass, field
from enum import Enum

from topoc.dsl import syntax
from topoc.lsp.text import Position, Span


class Kind(Enum):
    """What a candidate is, for the editor's icon."""

    KEYWORD = "keyword"
    VALUE = "value"
    ROLE = "role"
    ATTRIBUTE = "attribute"
    INSTANCE = "instance"
    GROUP = "group"
    TOPIC = "topic"
    QUEUE = "queue"
    LIST = "list"
    TOPOLOGY = "topology block"
    CONTEXT_FIELD = "context field"
    FIELD = "field"
    VARIABLE = "loop variable"
    PARAMETER = "parameter"


@dataclass(frozen=True, slots=True)
class Candidate:
    label: str
    kind: Kind
    # What is inserted, when not the label (`owned` inserts `owned by `).
    insert: str | None = None


@dataclass(frozen=True, slots=True)
class Completion:
    candidates: list[Candidate]
    # The partial word the candidates replace.
    replace: Span


def complete(source: str, position: Position, file: syntax.SourceFile | None) -> Completion:
    """The candidates at `position` in `source`; `file` is the document's last
    syntax tree that parsed, for its declarations (`None` if none has)."""
    lines = source.split("\n")
    line = lines[position.line] if position.line < len(lines) else ""
    before = "\n".join([*lines[: position.line], line[: position.column]])
    declared = _Declared.of(file)
    scan = _scan(before)
    if scan.in_string and scan.placeholder is None:
        return Completion([], Span(position, position))
    word = scan.placeholder if scan.placeholder is not None else _word(line[: position.column])
    # The candidates replace what follows the last `.` or `@` of the word.
    cut = max(word.rfind("."), word.rfind("@")) + 1
    replace = Span(Position(position.line, position.column - len(word) + cut), position)
    scope = _scope(scan.frames, declared)
    if scan.placeholder is not None or word.startswith("@") or "." in word:
        candidates = _path(word, scope, declared, in_placeholder=scan.placeholder is not None)
    else:
        candidates = _after(scan, scope, declared)
    return Completion(candidates, replace)


# ---- what is declared --------------------------------------------------------


@dataclass(slots=True)
class _Declared:
    roles: dict[str, syntax.RoleDef] = field(default_factory=dict)
    instances: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    queues: list[str] = field(default_factory=list)
    lists: list[str] = field(default_factory=list)
    blocks: dict[str, syntax.TopologyDef] = field(default_factory=dict)
    fields: dict[str, syntax.FieldDecl] = field(default_factory=dict)
    types: dict[str, syntax.TypeDef] = field(default_factory=dict)

    @classmethod
    def of(cls, file: syntax.SourceFile | None) -> "_Declared":
        declared = cls()
        for definition in [] if file is None else file.definitions:
            declared._add(definition)
        return declared

    def _add(self, definition: syntax.Definition | syntax.ForItem) -> None:
        def once(names: list[str], name: str) -> None:
            if "{{" not in name and name not in names:
                names.append(name)

        match definition:
            case syntax.RoleDef(name=name):
                self.roles[name.text] = definition
            case syntax.InstanceDef(name=name, group=group):
                once(self.instances, name.text)
                if group is not None:
                    once(self.groups, group.text)
            case syntax.TopicDef(name=name):
                once(self.topics, name.text)
            case syntax.QueueDef(name=name):
                once(self.queues, name.text)
            case syntax.ListDef(name=name):
                once(self.lists, name.text)
            case syntax.TopologyDef(name=name):
                self.blocks[name.text] = definition
            case syntax.ContextDef(types=types, fields=fields):
                self.types.update((t.name.text, t) for t in types)
                self.fields.update((f.name.text, f) for f in fields)
            case syntax.ForDef(body=body):
                for item in body:
                    self._add(item)

    def candidates(self, *kinds: Kind) -> list[Candidate]:
        tables = {
            Kind.ROLE: list(self.roles),
            Kind.GROUP: self.groups,
            Kind.INSTANCE: self.instances,
            Kind.TOPIC: self.topics,
            Kind.QUEUE: self.queues,
            Kind.LIST: self.lists,
            Kind.TOPOLOGY: list(self.blocks),
        }
        return [Candidate(name, kind) for kind in kinds for name in tables[kind]]


# ---- the text before the cursor ----------------------------------------------

# A token, after comments are dropped: a string (possibly unterminated), a
# placeholder (possibly unterminated), a word or `@path`, or punctuation.
_TOKEN = re.compile(
    r'(?P<string>"(?:[^"\\\n]|\\.)*"?)'
    r"|(?P<placeholder>\{\{[^\n]*?(?:\}\}|$))"
    r"|(?P<word>@?[\w.]+(?:::[\w.]+)*@?)"
    r"|(?P<punct><->|->|[{}\[\](),:=])",
)
_COMMENT = re.compile(r"//[^\n]*")
_COMPLETE_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')


@dataclass(slots=True)
class _Frame:
    """An open `{`: the tokens of its header (since the previous `{`, `}` or
    statement boundary), and the tokens inside it so far."""

    header: list[str]
    body: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        """What the block is: the last block keyword of its header outside
        parentheses (a parameter's kind is no block), refined."""
        for index in _outside_parentheses(self.header):
            word = self.header[index]
            following = self.header[index + 1 : index + 2]
            match word:
                case "topology":
                    return "topology"
                case "owned" if following == ["by"]:
                    return "owned"
                case "durability" if following in (["persistent"], ["transient"]):
                    return following[0]
                case "delivery" if following in (["at_least_once"], ["at_most_once"]):
                    return following[0]
                case "role" | "instance" | "topic" | "queue" | "for" | "context" | "sow" | "type":
                    return word
        return "unknown"


@dataclass(slots=True)
class _Scan:
    # The open blocks, outermost first; the first is the file itself.
    frames: list[_Frame]
    # The text of the `{{...}}` the cursor is in, after the braces.
    placeholder: str | None
    in_string: bool


def _scan(before: str) -> _Scan:
    frames = [_Frame(["<file>"])]
    placeholder = None
    in_string = False
    text_before = _COMMENT.sub("", before)
    for match in _TOKEN.finditer(text_before):
        text = match.group()
        at_end = match.end() == len(text_before)
        if match.lastgroup == "string":
            if at_end and _COMPLETE_STRING.fullmatch(text) is None:
                in_string = True
                opening = text.rfind("{{")
                if opening >= 0 and "}}" not in text[opening:]:
                    placeholder = text[opening + 2 :].lstrip()
            frames[-1].body.append('""')
            continue
        if match.lastgroup == "placeholder":
            if at_end and not text.endswith("}}"):
                placeholder = text[2:].lstrip()
            frames[-1].body.append("{{}}")
            continue
        if text == "{":
            frame = frames[-1]
            header = _header(frame.body)
            frames.append(_Frame(header))
            continue
        if text == "}":
            if len(frames) > 1:
                frames.pop()
            frames[-1].body.append("}")
            continue
        frames[-1].body.append(text)
    return _Scan(frames, placeholder, in_string)


# Keywords that start something within a block: a header runs from the last
# one before a `{`.
_STARTS = {
    "role", "instance", "topic", "queue", "list", "for", "context", "topology", "type",
    "mesh", "replicate", "local", "sow", "owned", "use", "durability", "delivery",
    "definition", "roles", "group", "attr", "from", "publish_to", "where", "priority",
    "barrier", "recovery_point", "target_depth", "file_backed_metadata",
    "deferred_ack_expiration",
}  # fmt: skip


def _header(body: list[str]) -> list[str]:
    """The header of a block opening after `body`: from its last `}` and the
    first block keyword after it that the `{` belongs to."""
    tail = body[len(body) - 1 - body[::-1].index("}") + 1 :] if "}" in body else body
    for index in _outside_parentheses(tail):
        if tail[index] in ("role", "instance", "topic", "queue", "for", "context", "type"):
            return tail[index:]
        if tail[index] in ("topology", "sow", "owned", "durability", "delivery"):
            return tail[index:]
    return tail


def _outside_parentheses(tokens: list[str]) -> list[int]:
    """The indexes of `tokens` outside `( ... )`, last first."""
    indexes = []
    depth = 0
    for index in range(len(tokens) - 1, -1, -1):
        if tokens[index] == ")":
            depth += 1
        elif tokens[index] == "(":
            depth = max(depth - 1, 0)
        elif depth == 0:
            indexes.append(index)
    return indexes


def _word(text: str) -> str:
    """The partial word ending `text`: a name, a dotted path or an `@path`."""
    match = re.search(r"@?[\w.]*$", text)
    return "" if match is None else match.group()


# ---- loop variables in scope -------------------------------------------------


class _VarKind(Enum):
    VALUE = "value"
    INSTANCE = "instance"
    RECORD = "record"  # a map key whose value is a record of `record`
    MAP_VALUE = "map value"


@dataclass(frozen=True, slots=True)
class _Var:
    name: str
    kind: _VarKind
    type_expr: syntax.TypeExpr | None


def _scope(frames: list[_Frame], declared: _Declared) -> list[_Var]:
    """The variables of the `for` blocks enclosing the cursor, outermost
    first, typed from their domains."""
    scope: list[_Var] = []
    for frame in frames:
        header = frame.header
        if frame.kind != "for" or "in" not in header:
            continue
        index = header.index("in")
        names = [word for word in header[1:index] if word != ","]
        domain = header[index + 1] if index + 1 < len(header) else ""
        type_expr = _type_of(domain, scope, declared)
        names_kind = _VarKind.VALUE
        if domain in declared.roles or domain in declared.groups or domain in declared.instances:
            names_kind = _VarKind.INSTANCE
        elif type_expr is not None and type_expr.kind is syntax.TypeKind.MAP:
            inner = type_expr.inner
            if len(names) == 2 and inner is not None:
                scope.append(_Var(names[0], _VarKind.VALUE, None))
                scope.append(_Var(names[1], _VarKind.MAP_VALUE, inner))
                continue
            if inner is not None and inner.kind is syntax.TypeKind.RECORD:
                scope.append(_Var(names[0], _VarKind.RECORD, inner))
                continue
        scope.extend(_Var(name, names_kind, None) for name in names)
    return scope


def _type_of(path: str, scope: list[_Var], declared: _Declared) -> syntax.TypeExpr | None:
    """The context type `path` (`@a.b`, `var.b`) reaches, if any."""
    head, *rest = path.split(".")
    current: syntax.TypeExpr | None
    if head.startswith("@"):
        decl = declared.fields.get(head[1:])
        current = None if decl is None else decl.type_expr
    else:
        var = next((v for v in reversed(scope) if v.name == head), None)
        current = None if var is None else var.type_expr
    for segment in rest:
        record = _record(current, declared)
        decl = (
            None
            if record is None
            else next((f for f in record.fields if f.name.text == segment), None)
        )
        current = None if decl is None else decl.type_expr
    return current


def _record(type_expr: syntax.TypeExpr | None, declared: _Declared) -> syntax.TypeDef | None:
    if type_expr is None or type_expr.kind is not syntax.TypeKind.RECORD:
        return None
    assert type_expr.record is not None
    return declared.types.get(type_expr.record.text)


# ---- candidates --------------------------------------------------------------


def _path(
    word: str, scope: list[_Var], declared: _Declared, *, in_placeholder: bool
) -> list[Candidate]:
    """Candidates for a path being typed: after `@`, after a `.`, or inside
    `{{...}}`."""
    if "." not in word:
        if word.startswith("@"):
            return [Candidate(name, Kind.CONTEXT_FIELD) for name in declared.fields]
        # The start of a placeholder: a variable, `@`, or a role (`Role.attr`).
        return [Candidate(var.name, Kind.VARIABLE) for var in reversed(scope)] + [
            Candidate(name, Kind.ROLE) for name in declared.roles
        ]
    stem = word[: word.rfind(".")]
    head = stem.split(".", 1)[0]
    var = next((v for v in reversed(scope) if v.name == head), None)
    if var is not None and var.kind is _VarKind.INSTANCE and in_placeholder:
        # `{{q.Role.attr}}`: after `q.` a role, after `q.Role.` its attributes.
        role = stem[len(head) + 1 :]
        if not role:
            return [Candidate(name, Kind.ROLE) for name in declared.roles]
        return _attributes(role, declared)
    if head.startswith("@") or var is not None:
        record = _record(_type_of(stem, scope, declared), declared)
        if record is None:
            return []
        return [Candidate(f.name.text, Kind.FIELD) for f in record.fields]
    return _attributes(stem, declared)


def _attributes(role: str, declared: _Declared) -> list[Candidate]:
    definition = declared.roles.get(role)
    if definition is None:
        return []
    return [Candidate(attr.name.text, Kind.ATTRIBUTE) for attr in definition.attrs]


def _keywords(*words: str) -> list[Candidate]:
    return [
        Candidate(word, Kind.KEYWORD, "owned by " if word == "owned" else None) for word in words
    ]


def _values(*words: str) -> list[Candidate]:
    return [Candidate(word, Kind.VALUE) for word in words]


# The statement keywords of each kind of block.
_BLOCK_KEYWORDS = {
    "<file>": ("role", "topic", "queue", "instance", "list", "for", "context", "topology"),
    "role": ("attr",),
    "instance": ("roles", "group", "attr"),
    "topic": ("definition", "topology"),
    "queue": (
        "from", "publish_to", "definition", "where", "delivery", "priority", "barrier",
        "recovery_point", "target_depth", "file_backed_metadata", "deferred_ack_expiration",
        "topology",
    ),
    "for": ("topic", "queue", "instance", "for"),
    "context": ("type",),
    "topology": ("mesh", "replicate", "local", "sow", "owned", "use"),
    "owned": ("mesh", "replicate"),
    "sow": ("durability",),
    "persistent": ("file",),
    "transient": ("recovery_point",),
    "at_least_once": (
        "lease", "leasing", "max_deliveries", "max_cancels", "backlog",
        "per_subscription_backlog", "expiration", "expiration_model", "fairness",
    ),
    "at_most_once": (
        "backlog", "per_subscription_backlog", "expiration", "expiration_model", "fairness",
    ),
}  # fmt: skip

# Keywords whose argument is a list of names, and what those name.
_PLACES = (Kind.ROLE, Kind.GROUP, Kind.INSTANCE)


def _block_params(frames: list[_Frame]) -> list[tuple[str, syntax.ParamKind]]:
    """The parameters of the topology block whose statements the cursor is in,
    read from its header as written: `topology b(<name>: <kind>, ...)`."""
    for frame in frames:
        header = frame.header
        if frame.kind != "topology" or header[:1] != ["topology"] or "(" not in header:
            continue
        inside = header[header.index("(") + 1 :]
        if ")" in inside:
            inside = inside[: inside.index(")")]
        params = []
        for index in range(0, len(inside) - 2, 4):
            name, colon, kind = inside[index : index + 3]
            known = next((k for k in syntax.ParamKind if k.value == kind), None)
            if colon == ":" and known is not None:
                params.append((name, known))
        return params
    return []


def _params(
    params: list[tuple[str, syntax.ParamKind]], *kinds: syntax.ParamKind
) -> list[Candidate]:
    return [Candidate(name, Kind.PARAMETER) for name, kind in params if kind in kinds]


_ANY_KIND = tuple(syntax.ParamKind)

# The names an argument of each kind may be.
_ARGUMENT_KINDS = {
    syntax.ParamKind.GROUP: (Kind.GROUP,),
    syntax.ParamKind.ROLE: (Kind.ROLE,),
    syntax.ParamKind.INSTANCE: (Kind.INSTANCE,),
    syntax.ParamKind.SELECTOR: (Kind.ROLE, Kind.GROUP, Kind.INSTANCE),
}


def _arguments(tokens: list[str], scope: list[_Var], declared: _Declared) -> list[Candidate] | None:
    """Candidates inside `use <block>(...)`: the parameters not yet given,
    or, after `<param> =`, names of its kind; `None` outside one."""
    opening = len(tokens) - 1 - tokens[::-1].index("(") if "(" in tokens else -1
    if opening < 2 or ")" in tokens[opening:] or tokens[opening - 2] != "use":
        return None
    block = declared.blocks.get(tokens[opening - 1])
    if block is None:
        return []
    inside = tokens[opening + 1 :]
    if inside[-1:] == ["="] and len(inside) >= 2:
        param = next((p for p in block.params if p.name.text == inside[-2]), None)
        if param is None:
            return []
        instance_vars = [
            Candidate(v.name, Kind.VARIABLE)
            for v in scope
            if v.kind is _VarKind.INSTANCE
            and param.kind in (syntax.ParamKind.INSTANCE, syntax.ParamKind.SELECTOR)
        ]
        return instance_vars + declared.candidates(*_ARGUMENT_KINDS[param.kind])
    if inside and inside[-1] != ",":
        return []
    given = {inside[i - 1] for i, token in enumerate(inside) if token == "=" and i >= 1}
    return [
        Candidate(p.name.text, Kind.PARAMETER, f"{p.name.text} = ")
        for p in block.params
        if p.name.text not in given
    ]


def _after(scan: _Scan, scope: list[_Var], declared: _Declared) -> list[Candidate]:
    """Candidates for a name or keyword, from what precedes it in its block."""
    frame = scan.frames[-1]
    tokens = frame.body
    block = frame.kind if len(scan.frames) > 1 else "<file>"
    instance_vars = [Candidate(v.name, Kind.VARIABLE) for v in scope if v.kind is _VarKind.INSTANCE]
    params = _block_params(scan.frames)

    arguments = _arguments(tokens, scope, declared)
    if arguments is not None:
        return arguments

    # A `for` header being written: `for x in |`.
    statement = _header(tokens)
    if statement[:1] == ["for"] and statement[-1:] == ["in"]:
        return (
            [Candidate(f"@{name}", Kind.CONTEXT_FIELD) for name in declared.fields]
            + [Candidate(v.name, Kind.VARIABLE) for v in reversed(scope)]
            + declared.candidates(Kind.LIST, Kind.ROLE, Kind.GROUP, Kind.INSTANCE)
        )

    # The keyword whose arguments are being written: the last token, or the
    # one before a run of `name ,` pairs ending in a comma.
    index = len(tokens) - 1
    while index >= 1 and tokens[index] == "," and tokens[index - 1] not in _STARTS:
        index -= 2
    last = tokens[index] if index >= 0 else None
    opened = tokens[index - 1] if index >= 1 else None
    if last == "[" and opened == "roles":
        last = "roles"
    match last:
        case "mesh":
            group = _params(params, syntax.ParamKind.GROUP, syntax.ParamKind.SELECTOR)
            return group + declared.candidates(Kind.GROUP)
        case "local" | "sow" | "replicate" | "->" | "<->":
            return _params(params, *_ANY_KIND) + instance_vars + declared.candidates(*_PLACES)
        case "by" if opened == "owned":
            owner = _params(params, syntax.ParamKind.INSTANCE, syntax.ParamKind.SELECTOR)
            return owner + instance_vars + declared.candidates(Kind.INSTANCE)
        case "use":
            return declared.candidates(Kind.TOPOLOGY)
        case "roles":
            return declared.candidates(Kind.ROLE)
        case "group" if block == "instance":
            return declared.candidates(Kind.GROUP)
        case "attr" if block == "instance":
            roles = _listed_roles(tokens) or list(declared.roles)
            return [
                Candidate(f"{role}.{attr.name.text}", Kind.ATTRIBUTE)
                for role in roles
                if role in declared.roles
                for attr in declared.roles[role].attrs
            ]
        case "from" | "publish_to":
            return declared.candidates(Kind.TOPIC)
        case "durability":
            return _values("persistent", "transient")
        case "delivery":
            return _values("at_least_once", "at_most_once")
        case "recovery_point":
            return _values("now", "epoch", "creation")
        case "leasing":
            return _values("strict", "sublet")
        case "fairness":
            return _values("fast", "round_robin", "proportional")
        case "expiration_model":
            return _values("queue", "latest", "earliest", "default")
        case "owned":
            return _keywords("by")
    return _keywords(*_BLOCK_KEYWORDS.get(block, ()))


def _listed_roles(tokens: list[str]) -> list[str]:
    """The roles an instance body lists in `roles [...]` so far."""
    if "roles" not in tokens:
        return []
    start = tokens.index("roles") + 1
    if tokens[start : start + 1] != ["["]:
        return []
    listed = []
    for token in tokens[start + 1 :]:
        if token == "]":
            break
        if token != ",":
            listed.append(token)
    return listed
