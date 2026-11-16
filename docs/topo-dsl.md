# `.topo` Language Reference

The `.topo` DSL declares an AMPS cluster at the *logical* level: reusable
configuration bundles (**roles**), replication units (**groups**), concrete
**instances**, and **topics** whose replication topology is written in terms of
named sets of instances. Deployment facts the topology depends on — which
locations exist, which order interfaces run where — come from a typed
**context** the caller supplies, and `for` templates turn them into instances
and topics.

This is the maintained reference for the language `topoc` implements — lexical
structure, grammar, resolution semantics, and the validation checks. It lives
beside the parser and resolver so it stays true to them.

## Concepts

### Role — configuration mixin

A role is a named bundle of configuration shared across instances. Roles declare
**attributes** (typed, optionally defaulted) that member instances must provide.
An instance may carry **any number of roles**.

- Roles never participate in replication semantics directly; in a topology
  statement a role name is only a *selector* for "every instance carrying this
  role."
- When roles later grow overlapping config fragments (transports, admin,
  logging), the merge rule is **last override wins**, in the order listed in the
  instance's `roles [...]` clause. Attributes are exempt from merging: an
  attribute's identity is `(role, name)`, so two roles declaring the same
  attribute name are distinct attributes, always referenced in qualified form.

### Group — replication unit

A group is AMPS's unit of replication: its members are intended to be identical
replicas (same topics, same filters, same durability). Every instance belongs to
**exactly one** group:

- explicitly, via `group <name>` in the instance body; or
- implicitly, as a **singleton group named after the instance** when no `group`
  clause is present.

Groups are not declared at top level; they come into existence by being named in
`instance` blocks. (A top-level `group <name> { ... }` block form is reserved for
the future, if groups ever need their own properties.)

```topo
// Two replicas forming the Infra group (an HA pair).
instance Infra1 { roles [Basic] group Infra }
instance Infra2 { roles [Basic] group Infra }

// No `group` clause: this instance is alone in a group named `Pricing1`.
instance Pricing1 { roles [Basic, Pricing] }

topic refdata {
  definition market::topics::REFDATA
  topology {
    mesh Infra                    // Infra1 and Infra2 replicate to each other
    replicate Pricing1 -> Infra   // one destination, failing over between the members
    sow Infra { durability persistent { file "${AMPS_DIR}/sow/%n.sow" } }
  }
}
```

A group name is a selector like any other in topology statements, but only a
group can be meshed (`mesh` connects its members to each other). Replicating to
a group gives the sender **one** destination listing every member, which AMPS
fails over between, rather than one destination per member as a role would.
Because members are replicas, everything placed on one member must be placed
on all of them, with the same filters and durability (V9). In a `for`, a group
name is quoted to interpolate (`group "G.{{loc}}"`).

### Instance

A concrete AMPS instance. Declares its roles (ordered, for merge purposes), its
group (optional — singleton by default), and values for the attributes its roles
require.

```topo
role Basic   { attr admin_port: int = 9090 }
role Quoting { attr key: string  attr max_orders: int }

instance Quoting.LN {
  roles [Basic, Quoting]
  group Quoters                  // optional: without it, a group of its own
  attr Quoting.key "LN"          // required: `key` has no default
  attr Quoting.max_orders 500
  attr Basic.admin_port 9091     // overrides the default 9090
}
```

Attributes are always assigned in qualified form, `<Role>.<attr>`, and only for
roles the instance carries (V5, V7); every attribute without a default must be
assigned (V6), with a value of its declared type (V10). An instance name may be
dotted (`Quoting.LN`) or, in a `for`, quoted to interpolate
(`instance "Quoting.{{loc}}"`); either way it must spell an identifier (V25).

### Topic

A message stream, identified by a `definition` — a `::`-separated path naming
the entry in the **topic catalog** that owns its contract (message type, codec,
SOW key, routing). The `.topo` only names that entry; the *facts* behind it
(codec, wire keys, field names) come from the catalog at generation time, not
from this DSL (see [Topic catalog](#topic-catalog)). A topic also carries a
topology block written against *names* that denote sets of instances.

A topic name is an identifier or a quoted string; strings allow the characters
AMPS topic names commonly use, such as `/` (`topic "orders/LN" { ... }`).

The name must fit its definition's **name pattern** from the catalog (see
[Topic names and definitions](#topic-names-and-definitions)). A definition with
a fixed name (`quotes`) admits exactly that name. Several topics sharing one
contract — such as the topics of a template — reference a definition whose
pattern has a `{}` hole per varying segment (`orders/{}`).

### Queue

An AMPS message queue: it delivers each message to one subscriber at a time.
A queue is its own named topic, which consumers subscribe to, so it shares the
flat namespace with topics. It reads its messages from one of two places:

- `from t1, t2, ...` — other topics (its *sources*), which must share one
  `definition` and be placed on every instance holding the queue; with
  several, `publish_to` names the one a publish to the queue's own name lands
  on;
- `definition <path>` — the topic of the queue's own name, whose contract is
  that catalog entry (the queue's name must fit its name pattern). Publishers
  publish to the queue's name directly.

Its name may not contain regular-expression characters (`. ^ $ * + ? ( ) [ ] {
} | \`), which AMPS rejects in a queue's name; `/` is fine (`work/orders`).

Its own topology places it, and decides which AMPS queue element it is:

| Topology | AMPS element | One queue per |
|---|---|---|
| `local R` | `LocalQueue` | instance: never replicated |
| `mesh G`, `replicate A <-> B` | `Queue` | the whole topology: each message is owned by the instance it was published to |
| `owned by I { mesh ... replicate ... <-> ... }` | `GroupLocalQueue` | block (a *domain*): every message starts owned by `I` |

An `owned by` block places one queue shared by the instances its statements
name, whose messages all start owned by `I`, an instance it places. Several
blocks are independent queues over the same messages: acknowledging a message
in one leaves it queued in the others, which suits a queue per region over
globally replicated data. Two-way statements outside the blocks link domains:
they replicate only the queue's sources. Each domain is named after its owner
in the configuration (`GroupLocalQueueDomain`).

AMPS requires a replicated queue to be replicated back, so a queue never takes
a one-way `replicate ... -> ...`, and an `owned by` block takes only `mesh` and
`replicate ... <-> ...`. It refuses to start with a replicated `LocalQueue`, so
`local` cannot be combined with replication or blocks. Once a queue has blocks,
every `mesh` belongs in one (a queue cannot be partly owned), and an instance
is in at most one domain. A queue never takes `sow`.

```topo
queue "work/orders" {
  from "orders/LN", "orders/NY"
  publish_to "orders/LN"
  where "/isin LIKE '^GB'"
  delivery at_least_once { lease 60s max_deliveries 5 per_subscription_backlog 10 }
  priority "/urgency"
  topology { mesh Infra }
}

queue "work/regional" {
  from "orders/LN", "orders/NY"
  publish_to "orders/LN"
  delivery at_least_once { lease 60s }
  topology {
    owned by Infra1 {                 // the EU queue, dispatched by Infra1
      mesh Infra
      replicate Infra <-> Backup
    }
    owned by NY1 {                    // an independent US queue
      mesh NY
    }
    replicate Infra <-> NY            // shares the orders between them
  }
}
```

Exactly one of `from` and `definition` is required, as are `delivery` and
`topology`; `at_least_once` delivery requires a `lease`. Every other setting maps to one AMPS queue
element and is emitted only when written, so AMPS's own default applies
otherwise:

| Setting | AMPS element |
|---|---|
| `publish_to` | `DefaultPublishTarget`: required with several sources, and one of them |
| `where` | `Filter`: only matching messages enter the queue |
| `lease`, `leasing` | `LeasePeriod`, `Leasing` (at-least-once only) |
| `max_deliveries`, `max_cancels` | `MaxDeliveries`, `MaxCancels` (at-least-once only) |
| `backlog`, `per_subscription_backlog` | `MaxBacklog`, `MaxPerSubscriptionBacklog` |
| `expiration`, `expiration_model` | `Expiration`, `ExpirationModel` |
| `fairness` | `FairnessModel`; at-most-once supports only `round_robin` |
| `priority` / `barrier` | `Priority` (a single field path) / `BarrierExpression`; not both |
| `recovery_point` | `RecoveryPoint` |
| `target_depth` | `TargetQueueDepth`; not with `priority` or `barrier` |
| `file_backed_metadata` | `FileBackedMetadata` |
| `deferred_ack_expiration` | `DeferredAckExpiration` |

The queue's `definition`, its own or its sources', gives it its message type
and field names. Queues are declared in `for` blocks like topics: one queue per
iteration, its name referencing every enclosing loop variable (V15), and each
value interpolated into the name one whole segment (V19), as a queue reading its
own name must fit its definition's name pattern:

```topo
for q in Quoting {
  queue "work/{{q.Quoting.key}}" {
    from "orders/{{q.Quoting.key}}"
    delivery at_least_once { lease 60s }
    topology { owned by q { replicate q <-> Infra mesh Infra } }
  }
}
```

### Topology blocks — reusable statements

Topics and queues often share their placement and replication. A **topology
block** names a set of topology statements once, and `use` puts them in a
topic's or queue's `topology`:

```topo
topology infra_ha {
  mesh Infra
  sow Infra { durability persistent { file "${AMPS_DIR}/sow/%n.sow" } }
}

topic refdata {
  definition market::topics::REFDATA
  topology {
    use infra_ha
    replicate Infra -> Quoting
  }
}
```

`use` stands for the block's statements, in its place: the topic above is
exactly the topic with `mesh Infra` and the `sow` written where `use infra_ha`
is, and every check applies to the result. A `use` may name several blocks
(`use infra_ha, fanout`), and a block may be declared before or after its uses.

A block holds any topology statements, including `owned by` for a queue, and
may interpolate `{{Role.attr}}` (expanded per target, as anywhere) and context
paths (`{{@region}}`). It cannot:

- **see loop variables**: a block used inside a `for` may not refer to that
  loop's variable, as a bare name or in a placeholder, so it means the same
  wherever it is used. What varies per iteration stays in the topic, around
  the `use`;
- **use another block**: its statements are written out.

Block names join the [flat namespace](#name-resolution--one-flat-namespace).
Errors in a block's statements point at the block.

**Parameters.** A block may take the names its statements place on, declared
`name: kind` and passed by name at each use:

```topo
topology fanout(from: group, to: role) {
  replicate from -> to where "/owner LIKE '{{to.key}}/.*'"
}

topic tvgrids {
  definition market::topics::TVGRIDS
  topology { use infra_ha  use fanout(from = Infra, to = Quoting) }
}
```

A parameter's kind is what its argument must name:

| Kind | The argument names |
|---|---|
| `group` | a group |
| `role` | a role |
| `instance` | an instance |
| `selector` | any of them: a role, a group or an instance |

The kind matters most for `replicate`, which takes any of them but means
something different for each (one failover destination for a group, one
destination per instance for a role). It is checked where the block is used,
for every iteration of the loops around it (V32), and inside the block, where a
parameter must fit its statement: `mesh` takes a `group` (or `selector`)
parameter and `owned by` an `instance` (or `selector`) one.

Each parameter stands, in the block, for its argument:

- as a name in a statement (`replicate from -> to`), where the argument may be
  a bare name, a quoted name interpolating the use's loop variables
  (`"Quoting.{{loc}}"`), or an instance-loop variable (`use b(source = q)`
  inside `for q in Quoting`);
- in a placeholder: `{{p}}` is the argument's name, and `{{p.attr}}` reads an
  attribute of a `role` parameter's argument, like `{{Role.attr}}`.

Arguments are passed by name, in any order, every parameter exactly once
(`use fanout(to = Quoting, from = Infra)`). A block without parameters is
declared and used without parentheses. A parameter may not reuse a declared
name, and a block's own parameters are not loop variables: a block may name a
parameter like a loop variable at its use.

### Context — deployment facts

A `.topo` may declare the shape of a **generation context**: data the
environment supplies when the topology is compiled, so deployment facts have a
single source of truth instead of being repeated in the `.topo`.

```topo
context {
  type location_info {
    order_interfaces: string[]
    port_base: int
  }
  locations: location_info[string]
  region: string
}
```

The block is the top-level record: each `name: type` line is a field the data
must provide. `type` declares a named record type. Types are written postfix
and compose left to right:

| Type | Meaning | Data |
|---|---|---|
| `string`, `int`, `bool` | a scalar | `"LN"`, `3`, `true` |
| `T[]` | a list of scalars | `["A", "B"]` |
| `V[string]` | a map from string keys to `V` | `{"LN": ...}` |
| `name` | a record of the declared type `name` | `{"order_interfaces": [...], ...}` |

Lists hold scalars only — an iteration is named by its item, and a record has no
name; a map keyed by name expresses the same data. A record type may not
contain itself.

The data is checked strictly against the declaration before anything is
expanded: every field present, no undeclared field, every value of its type.
Errors give the path to the offending value
(`context: locations.NY: 'location_info' has no field 'order_interface'`). Map
entries keep the order the data lists them in, which fixes the order of what
they generate.

The caller supplies the data as a JSON file, `topoc generate --context
<context.json>` (and `topoc check --context <context.json>`), exactly when the
topology declares a `context` block:

```json
{
  "locations": {
    "LN": { "order_interfaces": ["A", "B", "C"], "port_base": 9100 },
    "NY": { "order_interfaces": ["A", "B"], "port_base": 9200 }
  },
  "region": "emea"
}
```

An object may not repeat a key, and integers are signed 64-bit. Errors in the
data are reported against the context file.

Context fields are referenced with an `@`, by **path**: `@` and a field,
followed by `.field` to reach a record's fields (`@region`,
`@site.port_base`). A loop variable bound from the context is referenced
without one (`loc.port_base`): the `@` marks only where a path enters the
context. A map's entries are reached by iterating it, not by key.

The `@` keeps context fields out of the [flat namespace](#name-resolution--one-flat-namespace):
a field may share its name with a role, a list or a loop variable. A bare name
that only the context declares is an error that asks for the `@` (`'region' is a
context field; write '{{@region}}'`).

### List and `for` — templates

When several instances or topics follow the same pattern, a `for` block declares
them once:

```topo
list Strategies [momentum, arbitrage]

for s in Strategies {
  topic "signals/{{s}}" { definition market::topics::SIGNALS topology { mesh Infra } }
}
```

expands to the topics `signals/momentum` and `signals/arbitrage`. A `for`
iterates over one of:

- **a list of values**: a named `list` (declared at top level), an inline one
  (`for s in [a, b]`), or a context list (`for oi in @interfaces`,
  `for oi in loc.order_interfaces`). The variable is the item, as text.
- **a context map**: `for loc in @locations` over a map of records binds `loc`
  to each key, and `loc.field` reaches the record's fields; any other map takes
  two variables, `for name, port in @ports`, the key and the value.
- **a context integer**: `for i in @shards` (or `for i in loc.shards`) counts
  from `0` up to the integer, excluded, so `shards: 3` generates `0`, `1` and
  `2`; `0` generates nothing, and a negative integer is an error. The variable
  is an `int`: it interpolates as `3`, and a bare `{{i}}` fits an `int`
  attribute. It takes one variable and has no fields:

  ```topo
  for i in @shards {
    topic "signals/{{i}}" { definition market::topics::SIGNALS topology { mesh Infra } }
  }
  ```
- **the instances of a role, group or instance**, in declaration order. The
  variable denotes each instance: `{{q.Role.attr}}` reads its attributes, and a
  bare `q` names it wherever a topology statement takes a name:

  ```topo
  for q in Quoting {
    topic "orders/{{q.Quoting.key}}" {
      definition market::topics::ORDERS
      topology { mesh Infra  replicate q -> Infra }
    }
  }
  ```

A `for` body holds topics, nested `for` blocks and — in loops over data, at any
depth — instances. A loop over instances cannot declare instances (it would
change its own domain) and holds no other loop over instances. Names are quoted
to interpolate the variables, wherever an instance or group is named:

```topo
for loc in @locations {
  instance "Quoting.{{loc}}" {
    roles [Basic, Quoting]
    attr Quoting.key "{{loc}}"
    attr Quoting.port_base {{loc.port_base}}
  }
  for oi in loc.order_interfaces {
    topic "orders/{{loc}}/{{oi}}" {
      definition market::topics::ORDERS
      topology { replicate Infra -> "Quoting.{{loc}}" }
    }
  }
}
```

Expansion produces ordinary instances and topics, which then go through every
check a hand-written one does (see [Template expansion](#template-expansion)).

### Name resolution — one flat namespace

Roles, groups, instances, topics, queues, lists and topology blocks share a
**single flat namespace**; declaring
two entities with the same name is an error, and an explicit group name may not
collide with any other name. This is what makes bare names in topology statements
unambiguous. Context fields stand apart: they are always written `@field`.

The one exception is that several topic declarations, or several queue
declarations, may share a name, each placing its own copy on its own instances,
like a `local` topic on a group, where each instance has an independent copy:

```topo
for site in @sites {
  for i in site.workers {
    queue "work/{{i}}" {
      definition market::topics::WORK
      delivery at_least_once { lease 60s }
      topology { local "Quoting.{{site}}" }
    }
  }
}
```

Every site's instance gets its own `work/0`, `work/1`, and so on. The rules are
checked on the outcome, per instance, whichever statement placed a name (V30):

- an instance hosts one topic or queue of each name, so a second declaration
  placing it on the same instance (`local`, `mesh`, `replicate`, `owned by`) is
  an error naming both declarations and the iterations that generated them;
- replication links copies of one declaration only: a `replicate` cannot target
  an instance hosting another declaration's copy;
- every declaration of a name has the same `definition` (message type and
  field names), while its other settings may differ per declaration;
- a name is either a topic or a queue throughout;
- members of a group are replicas, so they host copies of the same
  declarations (V9).

A queue's sources are looked up on the queue's own instance.

In a topology statement, a name denotes a **set of instances**:

| Name kind | Denotes | As replication target |
|---|---|---|
| group | the group's members | **one** destination per source instance, members listed as failover addresses (they are replicas) |
| role | every instance carrying the role | **one destination per target instance**, filters interpolated per target (they are distinct individuals) |
| instance | the instance itself | one direct destination (equivalent to its singleton group) |

## Lexical Structure

- **Comments**: `//` to end of line.
- **Whitespace** (including newlines) is insignificant. Every statement begins
  with a keyword, so no statement terminator is needed.
- **Identifiers**: one or more segments joined by `.`, where a segment is
  `[A-Za-z_][A-Za-z0-9_]*`. No leading/trailing dot, no consecutive dots. The dot
  is *just a name character* — `Quoting` and `Quoting.LN` are unrelated names.
  Exception: **attribute names are single segments (no dots)**, so a qualified
  attribute reference always splits unambiguously at its **last** dot
  (`Quoting.Fast.key` → role `Quoting.Fast`, attribute `key`).
- **Keywords are contextual**: `role`, `topic`, `instance`, `attr`,
  `definition`, `durability`, `persistent`, `transient`, `file`,
  `recovery_point`, `now`, `epoch`, `topology`, `mesh`, `replicate`, `local`,
  `sow`, `use`, `selector`, `where`, `roles`, `group`, `string`, `int`, `bool`, `list`, `for`,
  `in`, `context`, `type`. They are recognized only in the positions the grammar expects them;
  elsewhere the same spellings are ordinary identifiers (so a role may declare
  `attr key` even though `key` is a keyword elsewhere).
- **Strings**: double-quoted, with `\"` and `\\` escapes. All *payload* data —
  file paths, AMPS filters, SOW key fields, attribute string values — is quoted.
  Bare tokens are reserved for DSL names and keywords, with two exceptions: a
  list item, and a name — of a topic, an instance, a group, or in a topology
  statement — may be a quoted string, so it can hold `/` or interpolate loop
  variables. An instance or group name must still spell an identifier once
  interpolated (V25).
- **Literals**: strings, integers, `true`/`false`. An attribute value may
  instead be a bare placeholder, `{{path}}`, which takes the type of the value
  it names.
- **Context paths**: `@` directly followed by a dotted identifier, `@region`,
  `@site.workers`: a `for` domain, or the start of a `{{...}}` path.
- **Punctuation**: `{` `}` `[` `]` `,` `:` `=` `->`.

### Interpolation

Exactly one substitution syntax belongs to the DSL, `{{...}}`, used in three
phases:

- **Data**: `{{path}}`, where the path starts at a loop variable over data or,
  with `@`, at a context field (`{{loc}}`, `{{loc.port_base}}`, `{{@region}}`) — substituted
  before instances are resolved, in topic, instance and group names, names in
  topology statements, `where` filters, `file` paths and attribute values. Only
  scalars interpolate; ints and bools render as `3`, `true`.
- **Instances**: `{{q}}` and `{{q.Role.attr}}`, where `q` iterates over
  instances — substituted when that loop expands, in topic names, `where`
  filters and `file` paths. `{{q}}` is the instance name.
- **Fan-out**: `{{Role.attr}}` — expanded by the compiler, per target instance,
  during topology fan-out. Valid only inside strings on statements whose target
  set guarantees the role (see V8). Templates leave these untouched, so they
  work inside `for` blocks exactly as elsewhere.

A path starting with `@` is a context path; otherwise its first segment is a
loop variable or is looked up in the flat namespace, so `{{@X.y}}` reads the
context and `{{X.y}}` an attribute of role `X`, even when both are named `X`.

Everything else inside strings is **passed through verbatim** for later layers to
resolve: `${...}` (environment expansion at deploy time) and `%n`-style tokens
(AMPS's own instance-name substitution).

## Grammar

```ebnf
file           ::= { definition } ;
definition     ::= role_def | topic_def | queue_def | instance_def | list_def
                 | for_def | context_def | topology_block ;

role_def       ::= "role" name "{" { attr_decl } "}" ;
attr_decl      ::= "attr" attr_name ":" type [ "=" literal ] ;
type           ::= "string" | "int" | "bool" ;

topic_def      ::= "topic" name_or_string "{" { topic_prop } "}" ;
topic_prop     ::= "definition" definition_path (* topic catalog key *)
                 | topology_def ;

topology_def   ::= "topology" "{" { topology_stmt } "}" ;
topology_block ::= "topology" name [ "(" param { "," param } ")" ]
                   "{" topology_stmt { topology_stmt } "}" ;
param          ::= field_name ":" ( "group" | "role" | "instance" | "selector" ) ;
                                                 (* no "use" inside *)
topology_stmt  ::= "use" block_use { "," block_use }   (* a topology block's statements *)
                 | "mesh" names
                 | "replicate" names "->" names [ "where" string ]
                 | "replicate" names "<->" names [ "where" string ]
                 | "owned" "by" name_or_string "{" owned_stmt { owned_stmt } "}"
                                                 (* in a queue's topology only *)
                 | "local" names
                 | "sow" names "{" durability_def "}" ;
owned_stmt     ::= "mesh" names | "replicate" names "<->" names [ "where" string ] ;
names          ::= name_or_string { "," name_or_string } ;   (* distinct *)
block_use      ::= name [ "(" argument { "," argument } ")" ] ;
argument       ::= field_name "=" name_or_string ;  (* every parameter once, by name *)

durability_def ::= "durability" "persistent" "{" { "file" string } "}"
                 | "durability" "transient"  "{" { "recovery_point" ( "now" | "epoch" ) } "}" ;

queue_def      ::= "queue" name_or_string "{" { queue_prop } "}" ;
                   (* exactly one of "from" and "definition";
                      "delivery" and "topology" are required *)
queue_prop     ::= "from" names
                 | "publish_to" name_or_string
                 | "definition" definition_path
                 | "where" string
                 | "delivery" "at_least_once" "{" "lease" DURATION { delivery_opt
                                                  | "leasing" ( "strict" | "sublet" )
                                                  | "max_deliveries" INTEGER
                                                  | "max_cancels" INTEGER } "}"
                 | "delivery" "at_most_once" "{" { delivery_opt } "}"
                 | "priority" string          (* a single field path *)
                 | "barrier" string           (* a filter *)
                 | "recovery_point" ( "epoch" | "now" | "creation" )
                 | "target_depth" INTEGER
                 | "file_backed_metadata"
                 | "deferred_ack_expiration" DURATION
                 | topology_def ;
delivery_opt   ::= "backlog" INTEGER | "per_subscription_backlog" INTEGER
                 | "expiration" DURATION
                 | "expiration_model" ( "queue" | "latest" | "earliest" | "default" )
                 | "fairness" ( "fast" | "round_robin" | "proportional" ) ;
DURATION       ::= digits ( "ms" | "s" | "m" | "h" | "d" ) ;   (* 60s, 500ms, 1d *)

list_def       ::= "list" name items ;
for_def        ::= "for" var [ "," var ] "in" ( name | items ) "{" for_item { for_item } "}" ;
for_item       ::= topic_def | queue_def | instance_def | for_def ;
items          ::= "[" item { "," item } "]" ;
item           ::= IDENT | STRING ;
var            ::= IDENT ;                (* single segment, no dots *)

instance_def   ::= "instance" name_or_string "{" { instance_prop } "}" ;
instance_prop  ::= "roles" "[" name { "," name } "]"
                 | "group" name_or_string
                 | "attr" qualified_name ( literal | PLACEHOLDER ) ;

context_def    ::= "context" "{" { type_decl | field } "}" ;
type_decl      ::= "type" field_name "{" { field } "}" ;
field          ::= field_name ":" type_expr ;
type_expr      ::= ( "string" | "int" | "bool" | field_name ) { "[" [ "string" ] "]" } ;
                                          (* "[]" a list, "[string]" a map *)

name           ::= IDENT ;                (* dotted identifier *)
name_or_string ::= IDENT | STRING ;       (* non-empty; strings may interpolate *)
definition_path ::= IDENT { "::" IDENT } ;  (* e.g. market::topics::REFDATA *)
attr_name      ::= IDENT ;                (* single segment, no dots *)
field_name     ::= IDENT ;                (* single segment, no dots *)
qualified_name ::= IDENT ;                (* split at last '.' during resolution *)
literal        ::= STRING | INTEGER | "true" | "false" ;
PLACEHOLDER    ::= "{{" path "}}" ;       (* outside a string, on one line *)
path           ::= [ "@" ] IDENT ;        (* "@": from a context field *)
CONTEXT_PATH   ::= "@" IDENT ;            (* no space after "@" *)
```

A `for` domain is a list, a role, group or instance, a loop variable's field
(`loc.order_interfaces`), or a context path (`@locations`). At most one `context` block appears in a
file; `type` followed by `:` is a field named `type`.

Every attribute declaration carries an explicit type — there is no implicit
default to `string`. An attribute with no default value must be assigned by every
instance carrying the role.

## Resolution Semantics

Parsing inlines every `use`, so resolution sees only the statements of the
topology blocks it names (see [Topology blocks](#topology-blocks--reusable-statements)).
Resolution runs in this order:

1. the context is checked against its declaration (V20, V21);
2. **data expansion**: every `for` over data is expanded, generating instances,
   topics and loops over instances, and data placeholders are substituted;
3. names are declared (V1);
4. instances are resolved (roles, groups, attributes);
5. loops over instances are expanded;
6. topics are resolved and fanned out;
7. group consistency (V9) is checked.

Data expansion comes first because loops over data may declare instances; loops
over instances come after instances are resolved because they need their
attributes and memberships.

### Attribute resolution

For each `attr <qualified> <value>` in an instance (a bare `{{path}}` value
having been replaced by the literal it names):

1. Split the qualified name at the last dot into `(role, attr)`.
2. `role` must appear in the instance's `roles [...]` list (V7).
3. `attr` must be declared by that role (V5), and the literal must match the
   declared type (V10).

After assignment, every `(role, attr)` pair required by the instance's roles must
have a value, from assignment or declaration default (V6).

### Topology fan-out

Every topology statement contributes **placements** (instance hosts topic) and
**edges** (replication destinations):

- `mesh G` — `G` must be a group (V3). Every member hosts the topic; every member
  gets a destination to every other member (full mesh).
- `replicate S -> T [where F]` — every instance in set(`S`) hosts the topic and
  gets destination(s) toward `T`; every instance in set(`T`) hosts the topic.
  The *kind* of `T` decides the shape (see table above): group → one destination
  with member failover list; role → one destination per target instance with `F`
  interpolated against that instance's attributes; instance → one direct
  destination. set(`S`) ∩ set(`T`) must be empty (V11).
- `replicate A <-> B [where F]` — two-way: exactly `replicate A -> B` plus
  `replicate B -> A`, each shaped by its target's kind and both filtered by `F`.
  Each end replicates the topic back to the other, but they are not failover
  partners. AMPS requires both directions to use the same filter (its
  `replicate_filter` check), so `F` must render identically for every instance
  on either side: a `{{Role.attr}}` whose value differs between them is an
  error.
- `local R` — every instance in set(`R`) hosts the topic; no edges.
- `sow T { durability … }` — every instance in set(`T`) *materialises* the topic
  with the given durability (a SOW); a topic with no `sow` statement is pure
  pub/sub.

A statement naming several sets is shorthand for one statement per name, in
the order written: `sow A, B { … }` is `sow A { … }` then `sow B { … }`, and
`replicate A, B -> C, D where F` is the four edges `A -> C`, `A -> D`, `B -> C`,
`B -> D`, each with `F`. Each target keeps the shape its kind decides, and every
check applies to the single statements (V11 to each pair, so no source may be a
target). A name listed twice in one list is an error.

Statements accumulate: an instance may acquire the same topic from several
statements, which is legal precisely because the topic's contract is identical
wherever the topic lands.

The kind of edge also sets the default replication mode: `mesh` members are HA
failover partners (sync, fully validated), while `replicate` targets are one-way
distribution downstreams (async, with the failover-partner validation checks
excluded). A two-way `replicate <->` edge is async and keeps AMPS's `replicate`
check (it does replicate back), excluding only `cascade` and `sow`.

**Relaying (passthrough).** AMPS forwards a message that arrived over
replication only through a destination that passes the group it arrived from.
Generation adds that `PassThrough` wherever an instance must relay for the
topology to hold: when it reaches a target member that a sender has no
single-member destination to. The typical case is a group target: `replicate
Q -> G` reaches one member of `G` (a failover list), so `mesh G` relays `Q` to
the other members; without it they would never receive `Q`'s messages. Chains
(`replicate A -> B`, `replicate B -> C`) relay `A` through `B` the same way.
Distribution from every member of a group (`mesh G`, `replicate G -> R`) needs
no relay, since each member reaches each target directly.

### Template expansion

Each `for` block is replaced, where it stands, by what it generates: for each
domain item in order, every item of the body in order, nested loops expanding
in place.

A loop over data is expanded first (see [Resolution Semantics](#resolution-semantics)).
Its variables, and every context path (`@...`), are substituted wherever data
interpolates (see [Interpolation](#interpolation)). A data variable is text: it
cannot stand alone as a name in a topology statement (V17); quote a name that
interpolates it instead (`"Quoting.{{loc}}"`). A loop over instances nested in
it keeps its own variable for the next stage, with the data already
substituted.

A loop over instances substitutes its variable `q`:

- in the topic or queue name, `where` filters, `file` paths, and a queue's
  sources, publish target, priority and barrier: `{{q}}` and `{{q.Role.attr}}`;
- in name positions of topology statements (`mesh`, `replicate`, `local`, `sow`,
  `owned by`): a bare `q`, which becomes the instance's name.

Any other `{{...}}` is left for fan-out. Every instance name a loop declares must
reference the variable of every enclosing loop (for `for k, v`, the key), or
two iterations would declare the same instance (V15). A topic or queue name
need not: iterations sharing a name each place their own copy, which is valid
on separate instances (V30). Generated instances, topics
and queues join the flat namespace and are resolved exactly like hand-written ones,
so every check (including V9) applies. In particular, members of a group are
replicas: a template over a group must place each generated topic on the whole
group, not on a single member.

An error found in a generated instance or topic keeps its location in the
template and names every iteration that produced it, the innermost `for`
locating the note:

```text
unknown name 'Nowhere' at line 5, column 85 (in topic 'orders/LN/A', from the
'for' at line 4, column 18 with loc = 'LN', oi = 'A')
```

### Validation

| # | Check | Failure message sketch |
|---|---|---|
| V1 | Flat-namespace uniqueness across roles, groups, instances, topics, queues, lists and topology blocks (several topic, or queue, declarations may share a name: V30); context fields, written `@field`, are not in it | "name `X` already defined as \<kind\> at \<loc\>" |
| V2 | Every referenced name exists (roles lists, topology statements) | "unknown name `X`" |
| V3 | `mesh` target is a group | "`mesh` requires a group; `X` is a \<kind\>" |
| V4 | Topology names resolve to a non-empty instance set | "role `X` has no instances" |
| V5 | Assigned attribute is declared by the named role | "role `R` declares no attribute `a`" |
| V6 | Every required attribute has a value (assignment or default) | "instance `I` missing value for `R.a`" |
| V7 | Attribute qualification names a role the instance carries | "instance `I` does not carry role `R`" |
| V8 | `{{R.a}}` used only where every target instance carries role `R` | "target of this edge is group `G`; `{{R.a}}` requires targets selected by role `R`" |
| V9 | **Group consistency**: after fan-out, all members of a group host copies of the same declarations, with identical filters and durability | "members of group `G` would diverge on topic `T`" |
| V10 | Attribute literal matches declared type | "`R.a` is `int`, got string" |
| V11 | `replicate` source and target sets are disjoint | "edge from `S` to `T` includes instance `I` on both sides" |
| V12 | Attribute names contain no dots | "attribute name `a.b` may not contain `.`" |
| V13 | A loop variable does not reuse a declared name or an enclosing loop's variable | "loop variable `s` conflicts with instance defined at \<loc\>" |
| V14 | A `for` domain is a list, a context list, map or integer, or a non-empty role, group or instance | "`t` is a topic; a `for` iterates over a list, role, group or instance" |
| V15 | Every instance name in a `for` references the variable of every enclosing loop (the key, for `for k, v`) | "instance name `I` does not reference loop variable `s`" |
| V16 | List items are distinct; expanded names follow V1 and V30 after expansion | "duplicate item `a` in list `S`" |
| V17 | A data variable is only text: no `{{v.Role.attr}}`, not a bare name in statements; instance attributes referenced exist | "`s` iterates over a list of values; it cannot be used as an instance name" |
| V18 | Topic names keep no unresolved `{{...}}` | "topic name `x/{{y}}` contains an unresolved `{{...}}`" |
| V19 | A value interpolated into a topic name is one segment: non-empty, without `/` | "`{{q.Q.key}}` in a topic name expands to `LN/A`, which contains `/`" |
| V20 | The `context` declaration is well formed: types declared, lists of scalars, no type containing itself | "type `node` contains itself (node -> node)" |
| V21 | The context data conforms to the declaration, strictly; it is supplied exactly when declared | "context: `locations.LN.port_base`: expected `int`, got 1.5" |
| V22 | Paths reach declared fields; a context field is written `@field`; only scalars interpolate; a `for` iterates lists, maps and non-negative integers | "`{{loc.names}}` is a list; only strings, ints and bools can be interpolated"; "`region` is a context field; write `{{@region}}`" |
| V23 | Instances are declared only in loops over data; a loop over instances contains none over instances | "instances may only be declared in loops over data" |
| V24 | `for k, v` exactly for maps whose values are not records (a list or an integer takes one variable) | "`@locations` maps to records: iterate it with one variable" |
| V25 | Instance and group names, quoted or not, spell an identifier once interpolated | "`I.a b` is not a valid instance name" |
| V26 | A queue's sources are declared topics of one `definition`; `publish_to` is one of them, and required for several | "queue `q` reads from topics of different definitions" |
| V27 | A queue's topology is `local` only, or replication only; replication is two-way; with `owned by` blocks, every `mesh` is in one and two-way statements outside them link different domains; never `sow` | "a replicated queue must be replicated both ways (AMPS requires it)" |
| V28 | Every instance holding a queue hosts its sources; a block's owner is an instance it places, owning one block; an instance is in at most one domain | "instance `A2` is in two domains of queue `q`" |
| V29 | A queue's name has no regular-expression characters (`. ^ $ * + ? ( ) [ ] { } \| \`), which AMPS rejects | "queue name `orders.work` contains `.`" |
| V30 | An instance hosts one topic or queue of each name, whichever statements placed it; every declaration of a name has the same `definition` | "topic `t` is already on instance `A`, placed by the topic declared at line 3, column 7" |
| V31 | A `use` names a topology block and passes each of its parameters exactly once, by name; a block places something, uses no other block, uses each parameter where its kind fits, and refers to no variable of a `for` around its use (its parameters aside) | "topology block `to_q` refers to `q` (line 1, column 36), the variable of the enclosing `for` at line 2, column 16" |
| V32 | Each topology block argument names its parameter's kind (a `selector` takes a role, group or instance), checked where the block is used, per iteration; a parameter does not reuse a declared name | "argument `Quoting` for `from` of topology block `fanout` is a role; `from` takes a group" |

V9 is the safety net that lets bare names stay ergonomic: any expansion that would
make replicas diverge (e.g. per-instance interpolated filters landing on members
of a shared group) is a compile error, not a silently broken cluster.

### Topic names and definitions

Generation checks every topic name against the **name pattern** of its
`definition`, taken from the catalog: a fixed name, or a pattern where each `{}`
is a hole standing for one whole, non-empty `/`-separated segment. Name and
pattern are compared segment by segment:

- a literal segment of the pattern must appear verbatim in the name, and must
  be written literally in the `.topo` — interpolation is only allowed where the
  pattern has a hole;
- a hole accepts any non-empty segment, written literally or interpolated.

| `.topo` topic | Definition's pattern | Result |
|---|---|---|
| `quotes` | `quotes` | matches |
| `"orders/LN"` | `orders/{}` | matches: a family member written by hand |
| `"orders/{{q.Quoting.key}}"` in a `for` | `orders/{}` | matches |
| `"orders/{{q.Quoting.key}}/A"` in a `for` | `orders/{}/{}` | matches: a hole filled literally |
| `"orders/{{q}}"` in a `for` | `orders` | error: a fixed name admits one topic |
| `"orders/{{q}}/{{q.R.x}}"` in a `for` | `orders/{}/A` | error: interpolation where the pattern expects `A` |

Because interpolated values never contain `/` (V19), whether they come from
attributes, lists or the context, a template's segments line up with those of
every name it expands to, so the check does not depend on the values. A producer building member names at runtime should apply the
same rule: one non-empty part per hole, without `/`.

## Resolved Model (generator input)

Resolution fans the group- and role-level source out to concrete per-instance
placements and edges, with `{{Role.attr}}` interpolations applied. Generators
consume this model and must not re-derive anything it already holds:

```text
ResolvedSystem
├── topics:    [ResolvedTopic]        // name, definition (topic catalog key)
├── queues:    [ResolvedQueue]        // name, definition, source (topics or its
│                                     // own name), kind, delivery and settings
├── groups:    [ResolvedGroup]        // name, members, explicit?
└── instances: [ResolvedInstance]
      ├── name, group
      ├── roles:        [String]                 // declaration order (merge order)
      ├── attrs:        {(role, name) → AttrValue}  // fully resolved, defaults applied
      ├── topics:       [String]                 // topics hosted here, sorted
      ├── destinations: [Destination]            // outgoing edges: topic, kind,
      │                                          // target group, member failover
      │                                          // list, interpolated filter,
      │                                          // groups to pass through
      ├── incoming:     [Incoming]               // inbound edges (the inversion of
      │                                          // destinations, spared to generators)
      ├── sows:         [ResolvedSow]            // SOW materialisations: topic + durability
      ├── queues:       [String]                 // queues held here, sorted
      └── queue_destinations, queue_incoming     // a queue's edges, kept apart from
                                                 // the topics'; passthrough per
                                                 // AMPS's queue rule
```

Note what is **not** here: a topic's message type, codec, and wire SOW keys are
*not* resolved by this DSL. They belong to the catalog entry named by the topic's
`definition`, and are supplied to generators separately.

## Topic catalog

The catalog is a JSON array with one entry per topic definition:

```json
[
  {
    "definition": "market::topics::REFDATA",
    "name_pattern": "refdata",
    "codec": "msgpack",
    "routed": false,
    "wire_keys": ["/s"],
    "fields": [
      { "name": "symbol", "wire_name": "s" },
      { "name": "book", "wire_name": "b",
        "children": [{ "name": "venue", "wire_name": "v" }] }
    ]
  }
]
```

| Member | Required | Meaning |
|---|---|---|
| `definition` | yes | The value a topic's `definition` refers to. Must be unique. |
| `name_pattern` | yes | The definition's topic name, or a pattern where each `{}` is a hole for one whole `/`-separated segment (`orders/{}`). Braces may appear only as such holes. |
| `codec` | yes | AMPS `<MessageType>` (`msgpack`, `json`, …). |
| `routed` | no (`false`) | Publishes are envelope-wrapped: message fields live under `/msg`. |
| `wire_keys` | no (`[]`) | Final AMPS `<Key>` paths of the topic's SOW. |
| `fields` | no (`[]`) | Field tree: `name` is what filters and keys are written against, `wire_name` the serialized name AMPS matches on. |

Unknown members are ignored. Every topic a generated configuration references
must have an entry; a miss is a generation error.

`replicate ... where` filters are written against field names (`name`). At generation time each `/path` is resolved through `fields` to
its wire form (and `/msg`-prefixed for a routed topic); a path naming no field
is an error. Text inside a quoted filter literal is never rewritten.

On a routed topic, `$route` names the envelope's route, which is not a message
field. It lets replication distribute messages by route:

```topo
replicate Infra -> Quoting where "$route LIKE '^{{Quoting.key}}/'"
```

sends each quoting instance only the messages routed under its key (`LN/A`,
`LN/B`, ... to `Quoting.LN`), which its own subscribers can then select by exact
route. `$route` becomes `/route` in the generated filter; on an unrouted topic it
is an error. AMPS `LIKE` is a regular expression and is not anchored, so anchor
prefixes with `^`.

## Example

The reference example is the exact `.topo` the tests run
([`testdata/reference.topo`](../testdata/reference.topo)), with its context in
[`testdata/reference.context.json`](../testdata/reference.context.json), its
catalog in [`testdata/reference.catalog.json`](../testdata/reference.catalog.json)
and the
generated configuration in [`testdata/expected/`](../testdata/expected).
Highlights:

- `Infra1`/`Infra2` share `group Infra` — a true replica pair.
- `Quoting.LN`/`Quoting.NY` come from the context: `for loc in
  @quoting_locations` declares one per location, keyed by it. They have **no**
  `group` clause — each is its own singleton group, because their
  differently-filtered `tvgrids` content means they were never legitimately one
  replication group.
- `replicate Infra -> Quoting` targets the *role* `Quoting`: one destination per
  quoting instance, with `{{Quoting.key}}` interpolated per target on `tvgrids`.
- Filters are AMPS syntax (`LIKE`, not `=`), written in field names.
- `for q in Quoting` creates one `orders/<key>` topic per quoting instance, named
  from its `Quoting.key` attribute; each quoter replicates its own topic to the
  Infra pair, which meshes it.
- `for s in Strategies` creates one `signals/<strategy>` topic per value of a
  `list`, for processes the topology does not otherwise model.
