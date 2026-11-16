# topoview — Plan

**Status:** implemented (phases 1–5 below).

`topoview` renders an AMPS cluster as a standalone, interactive HTML page, built
from the cluster's AMPS XML configuration files — the files `ampServer` itself
runs. It ships with the Python `topoc` package as `topoc view`.

## Decisions

| # | Decision |
|---|---|
| D1 | The input is the cluster's AMPS XML configurations, one per instance — not a `.topo` and not topoc's resolved model — so the view works on any existing cluster and shows what `ampServer` will run. |
| D2 | topoc becomes Python only: the C++ implementation is removed, the Python package moves to the repo root, and topoview is bundled with it. The Rust implementation in skynet.rs is unaffected. |
| D3 | The page is fully standalone: the cluster model is embedded as JSON in one HTML file, which works offline. The user names the cluster; the name titles the page and the file (`<name>.html`). |
| D4 | The front end (React, TypeScript, `@xyflow/react`, ELK) lives in `topoview/` and Vite builds it into one self-contained HTML template, committed as package data. Using topoc needs only Python; Node is needed only to change the front end. The template carries a hash of the front-end sources, and a Python test fails if they no longer match. |
| D5 | Explicit groups (more than one member) are drawn as frames around their members. |
| D6 | The look follows `~/dev/cartographer`: its design tokens, light and dark themes, toolbar, panels and highlight ring. |

## Reading the cluster from XML

Per file (`<AMPSConfig>`):

- **Instance:** `<Name>` and `<Group>`.
- **Topics on the instance:** the union of `<SOW>` topics (message type, key,
  durability, file or recovery point), `<TransactionLog>` topics (message
  type) and the topics of `<Replication>` destinations.
- **Replication:** each `<Destination>` targets the group named by its
  `<Group>` — how AMPS itself addresses replication. Its targets are that
  group's members other than the source:
  - one member left: an edge to that instance (a mesh from `Infra1` to group
    `Infra` lands on `Infra2`);
  - several: an edge to the group's frame — a failover list, delivered to
    whichever member is up;
  - a group none of the files declares: an edge to an *external* node.

  Addresses are not compared: in hand-written configurations they may be
  literal `host:port` values that cannot be resolved. Each destination keeps
  its `SyncType` (sync edges are solid, async dashed), and each of its topics
  its filter and excluded validations.

**Limitation:** AMPS configurations do not declare plain pub/sub topics. A
topic with no SOW, no journal and no replication leaves no trace in the XML and
cannot be shown. The page's help and the README say so.

## The page

- **Toolbar:** cluster name, theme toggle, fit-to-view.
- **Left sidebar:** a searchable list of topics and one of instances.
- **Canvas:** one node per instance (name; group frames per D5), laid out
  automatically by ELK, which also routes the links around the nodes. Nodes can
  be dragged for the session; a link touching a dragged node runs straight
  between node borders instead of along its route. One link per source →
  target, two-headed for instances replicating to each other; mesh links
  between members of one frame are left out of the layout, so members stack.
  Links are labelled only when highlighted, to keep the resting canvas
  readable.
- **Selecting a topic:** highlights the instances hosting it (a badge marks a
  SOW, persistent or transient) and the edges carrying it, labelled with their
  filters; the rest is dimmed. The right-hand panel details the topic.
- **Selecting an instance:** highlights it and its replication peers; the panel
  lists its group and topics (SOW, journal, where each replicates to and from).
  A topic in the list selects that topic.
- **Selecting a link** (click it): the panel lists its destinations and their
  topics.
- The selection lives in the URL fragment (`#topic=tvgrids`,
  `#instance=Infra1`), so a link can point at it; Escape clears it.

## Command

```sh
topoc view --cluster <name> [--catalog <catalog.json>] --output-dir <dir> <config.xml>...
```

writes `<dir>/<name>.html`. Every option is required except `--catalog`, which
gives SOW keys and filters their field names (`/symbol` rather than the wire
`/s`): each topic is matched to its entry by name pattern, and its wire paths
are mapped back through the entry's field tree, dropping a routed topic's
`/msg` envelope prefix and showing `/route` as `$route`.

## Phases

1. **Restructure:** remove the C++ implementation; move the Python package to
   the repo root; update READMEs.
2. **Cluster model:** the XML reader and the cluster model (Python), with its
   JSON form; tests against `testdata/expected/`, which are real AMPS
   configurations.
3. **Front end:** `topoview/` — the React app, reading the embedded model;
   unit tests for the selection logic; the single-file build and source hash.
4. **`topoc view`:** template embedding, the command, the staleness test.
5. **Docs.**
