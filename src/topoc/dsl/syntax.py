"""Syntax tree for the `.topo` DSL, produced by `topoc.dsl.parser.parse`.

The tree is purely syntactic: optional topic properties stay optional, names
are unresolved, and `{{...}}` placeholders (inside strings, or as a bare
attribute value) are kept verbatim. The resolver owns requiredness, name
binding and validation.
"""

from dataclasses import dataclass
from enum import Enum

from topoc.errors import SourceLocation
from topoc.queue import Delivery, Duration, QueueRecoveryPoint

type Literal = str | int | bool


@dataclass(frozen=True, slots=True)
class Ident:
    """A (possibly dotted) name. The dot is just a name character."""

    text: str
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class StringLit:
    """A quoted payload string (path, filter, attribute value), passed through
    verbatim — including `${...}` and `%n` tokens."""

    value: str
    location: SourceLocation


class AttrType(Enum):
    """Attribute type, valued by its DSL spelling."""

    STRING = "string"
    INT = "int"
    BOOL = "bool"


@dataclass(frozen=True, slots=True)
class AttrDecl:
    """`attr <name>: <type> [= <default>]`"""

    name: Ident
    attr_type: AttrType
    default: Literal | None


@dataclass(frozen=True, slots=True)
class RoleDef:
    """`role <name> { attr ... }`"""

    name: Ident
    attrs: list[AttrDecl]


class RecoveryPoint(Enum):
    NOW = "now"  # no recovery
    EPOCH = "epoch"  # rebuild the SOW from the whole transaction log


@dataclass(frozen=True, slots=True)
class PersistentDurability:
    file: StringLit


@dataclass(frozen=True, slots=True)
class TransientDurability:
    recovery_point: RecoveryPoint


type Durability = PersistentDurability | TransientDurability


@dataclass(frozen=True, slots=True)
class MeshStmt:
    """`mesh <group>`"""

    group: Ident


@dataclass(frozen=True, slots=True)
class ReplicateStmt:
    """`replicate <source> -> <target> [where "<filter>"]`, or
    `replicate <a> <-> <b> [where "<filter>"]` when `two_way`: one edge each
    way, both with the filter."""

    source: Ident
    target: Ident
    filter: StringLit | None
    two_way: bool


@dataclass(frozen=True, slots=True)
class LocalStmt:
    """`local <target>`"""

    target: Ident


@dataclass(frozen=True, slots=True)
class SowStmt:
    """`sow <target> { durability ... }`"""

    target: Ident
    durability: Durability


@dataclass(frozen=True, slots=True)
class OwnedStmt:
    """`owned by <instance> { mesh ... replicate ... <-> ... }`, in a queue's
    topology only: the statements place one `GroupLocalQueue` domain, whose
    messages all start owned by `owner`. Only `mesh` and two-way `replicate` may
    appear inside (the parser enforces it)."""

    owner: Ident
    statements: list["TopologyStmt"]


type TopologyStmt = MeshStmt | ReplicateStmt | LocalStmt | SowStmt | OwnedStmt


@dataclass(frozen=True, slots=True)
class TopicDef:
    """`topic <name | "name"> { definition <path> topology { ... } }`"""

    # An identifier or a quoted string (topic names may contain `/`). Inside a
    # `for` block it is a template referencing the loop variable.
    name: Ident
    # The `::`-joined definition path naming the topic's catalog entry.
    definition: Ident | None
    topology: list[TopologyStmt] | None


@dataclass(frozen=True, slots=True)
class Placeholder:
    """A bare `{{path}}` attribute value: the expression between the braces,
    trimmed. It takes the type of the context value it names."""

    expr: str


@dataclass(frozen=True, slots=True)
class AttrAssign:
    """`attr <Role.attr> <value>`, already split at the last dot."""

    role: Ident
    attr: Ident
    value: Literal | Placeholder
    value_location: SourceLocation


@dataclass(frozen=True, slots=True)
class InstanceDef:
    """`instance <name> { roles [...] group <name> attr ... }`. The instance and
    group names are identifiers or quoted strings; in a `for` block they may
    interpolate the loop variables."""

    name: Ident
    roles: list[Ident]
    group: Ident | None
    attrs: list[AttrAssign]


@dataclass(frozen=True, slots=True)
class ListDef:
    """`list <name> [ <item>, ... ]`: named values a `for` block can iterate over.
    Items are identifiers or quoted strings, kept as their text."""

    name: Ident
    items: list[StringLit]


@dataclass(frozen=True, slots=True)
class ContextPath:
    """`@path`: a path from a context field, as a `for` domain; `path` has no
    `@`."""

    path: str
    location: SourceLocation  # of the `@`

    def __str__(self) -> str:
        return f"@{self.path}"


# What a `for` block iterates over: a declared list, role, group or instance,
# or a path from a loop variable (by name); a context path; or an inline list
# of values.
type ForDomain = Ident | ContextPath | list[StringLit]


@dataclass(frozen=True, slots=True)
class ForDef:
    """`for <var>[, <value>] in <domain> { ... }`: templates instantiated once
    per domain item."""

    location: SourceLocation  # of the `for` keyword
    # The item, or the key when iterating a map.
    variable: Ident
    # The value, in the two-variable form over a map.
    value: Ident | None
    domain: ForDomain
    # Non-empty, in source order.
    body: list["ForItem"]


# A template in a `for` body.
type ForItem = TopicDef | "QueueDef" | InstanceDef | ForDef


class TypeKind(Enum):
    STRING = "string"
    INT = "int"
    BOOL = "bool"
    RECORD = "record"
    LIST = "list"
    MAP = "map"


@dataclass(frozen=True, slots=True)
class TypeExpr:
    """A type, written postfix: `string`, `T[]` (list of `T`), `V[string]` (map
    from string keys to `V`), or the name of a record type."""

    kind: TypeKind
    # The record type's name, for `RECORD`.
    record: Ident | None
    # The item or value type, for `LIST` and `MAP`.
    inner: "TypeExpr | None"


@dataclass(frozen=True, slots=True)
class FieldDecl:
    """`<name>: <type>`, a field of a record."""

    name: Ident
    type_expr: TypeExpr
    type_location: SourceLocation


@dataclass(frozen=True, slots=True)
class TypeDef:
    """`type <name> { <field>: <type> ... }`: a named record type."""

    name: Ident
    fields: list[FieldDecl]


@dataclass(frozen=True, slots=True)
class ContextDef:
    """`context { type ... <field>: <type> ... }`: the shape of the generation
    context the caller supplies. The block itself is the top-level record."""

    location: SourceLocation  # of the `context` keyword
    # Record types, in declaration order.
    types: list[TypeDef]
    fields: list[FieldDecl]


@dataclass(frozen=True, slots=True)
class QueueTopics:
    """`from <topic>, ... [publish_to <topic>]`: the topics whose messages enter
    the queue (`UnderlyingTopic`), and the one a publish to the queue's own
    name lands on (`DefaultPublishTarget`)."""

    sources: list[Ident]
    publish_to: Ident | None


@dataclass(frozen=True, slots=True)
class QueueOwnName:
    """`definition <path>`: the queue reads the topic of its own name, whose
    contract is that catalog entry."""

    definition: Ident


# Where a queue's messages come from.
type QueueSource = QueueTopics | QueueOwnName


@dataclass(frozen=True, slots=True)
class Priority:
    """`priority "<field>"`: a field path; higher values are delivered first."""

    path: StringLit


@dataclass(frozen=True, slots=True)
class Barrier:
    """`barrier "<filter>"`: a filter marking barrier messages."""

    filter: StringLit


# `priority` or `barrier`, which AMPS cannot combine.
type QueueOrdering = Priority | Barrier


@dataclass(frozen=True, slots=True)
class QueueDef:
    """`queue <name> { from ... delivery ... topology { ... } }`: an AMPS
    message queue, placed and replicated by its own topology, which also decides
    which AMPS queue element it is: `local` a `LocalQueue`, `mesh` and
    `replicate ... <-> ...` a `Queue`, `owned by` blocks a `GroupLocalQueue`.
    Exactly one of `from` and `definition`, and `delivery` and `topology`, are
    required, which the parser enforces."""

    name: Ident
    source: QueueSource
    # `where`: only messages matching it enter the queue (`Filter`).
    filter: StringLit | None
    delivery: Delivery
    ordering: QueueOrdering | None
    recovery_point: QueueRecoveryPoint | None
    # `target_depth`: messages kept active (`TargetQueueDepth`).
    target_depth: int | None
    # `file_backed_metadata`: page queue metadata to the journal directory.
    file_backed_metadata: bool
    # `deferred_ack_expiration`: how long acks for absent messages are kept.
    deferred_ack_expiration: Duration | None
    topology: list[TopologyStmt]


class ParamKind(Enum):
    """What a topology block's parameter names, valued by its keyword."""

    GROUP = "group"
    ROLE = "role"
    INSTANCE = "instance"
    SELECTOR = "selector"  # any name selecting instances: a role, a group or an instance


@dataclass(frozen=True, slots=True)
class Param:
    """`<name>: <kind>`, a topology block's parameter."""

    name: Ident
    kind: ParamKind


@dataclass(frozen=True, slots=True)
class TopologyDef:
    """`topology <name>[(<param>: <kind>, ...)] { <statements> }`: a named
    block of topology statements. `use <name>[(<param> = <argument>, ...)]` in
    a topic's or queue's `topology` stands for its statements, each parameter
    replaced by its argument; the parser inlines them there, so no `use`
    survives parsing."""

    name: Ident
    # In declaration order; empty for a block declared without `(...)`.
    params: list[Param]
    # Non-empty, in source order; no `use` (a block cannot use another).
    statements: list[TopologyStmt]


type Definition = (
    RoleDef | TopicDef | QueueDef | InstanceDef | ListDef | ForDef | ContextDef | TopologyDef
)


@dataclass(frozen=True, slots=True)
class Argument:
    """`<parameter> = <argument>` in a `use`."""

    # The parameter's name, as written at the use.
    parameter: Ident
    # The parameter's kind, from the block's declaration.
    kind: ParamKind
    # A name, or a quoted name that may interpolate the use's loop variables;
    # it stands wherever the parameter does in the block.
    value: Ident


@dataclass(frozen=True, slots=True)
class BlockUse:
    """`use <block>[(<parameter> = <argument>, ...)]`."""

    block: Ident
    # In the order written.
    arguments: list[Argument]


@dataclass(frozen=True, slots=True)
class SourceFile:
    definitions: list[Definition]
    # Every `use` of a topology block, in source order. The parser inlines the
    # blocks' statements, so this keeps where each was named and what it was
    # passed, for the resolver to check each argument's kind, and for editors.
    block_uses: list[BlockUse]
