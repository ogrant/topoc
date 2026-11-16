# topoc

`topoc` compiles a `.topo` topology — a logical description of an AMPS cluster
(roles, replication groups, instances, and topics with their replication
topology) — into one AMPS XML configuration file per instance. Its companion,
**topoview** (`topoc view`), draws any AMPS cluster from its XML configuration
files as a standalone, interactive HTML page.

The language is documented in [docs/topo-dsl.md](docs/topo-dsl.md).

It is written in Python 3.12+. Its only dependencies, pygls and PyYAML, serve
the language server (`topoc lsp`).

## Usage

```sh
topoc check    --topology system.topo [--context context.json]
topoc generate --topology system.topo [--context context.json] \
               --catalog catalog.json --output-dir out/
topoc view     --cluster <name> [--catalog catalog.json] --output-dir pages/ \
               configs/*.xml
topoc lsp      # the language server, for editors (see editors/README.md)
```

`generate` writes `out/<Instance>.xml` for every instance. Every option is
required, except `--context`, which is required exactly when the topology
declares a `context` block, and `view`'s `--catalog`. Exit status: `0` success, `1` invalid input or I/O
failure, `2` usage error.

The **context** supplies the deployment facts the topology's templates expand
over, such as which locations exist (see
[Context](docs/topo-dsl.md#context--deployment-facts)). Errors in it are
reported against the context file.

The **catalog** supplies each topic's codec, SOW keys and field names (see
[Topic catalog](docs/topo-dsl.md#topic-catalog)).

Generated files refer to hosts and ports only through environment
placeholders that AMPS expands at startup, so the deployment must define, per
instance (name upper-cased, non-alphanumerics mapped to `_`, e.g. `Quoting.LN`
-> `QUOTING_LN`):

- `<NAME>_ADMIN_INETADDR`
- `<NAME>_CLIENT_INETADDR`
- `<NAME>_WEBSOCKET_INETADDR`
- `<NAME>_REPLICATION_INETADDR`

plus `AMPS_DIR`, used by the journal directory (and by any SOW file path that
references it).

## Viewing a cluster: topoview

```sh
topoc view --cluster LN-prod --output-dir pages/ configs/*.xml
```

reads the cluster's AMPS XML configurations — one file per instance, the files
`ampServer` runs, whether topoc generated them or not — and writes
`pages/LN-prod.html`. The page is self-contained: the cluster is embedded in
it, and it opens offline in any browser, so it can be shared as a file. The
cluster name titles the page and names its file.

A configuration writes SOW keys, replication filters and queue filters, priorities and barriers with wire names (`/s`),
the names AMPS matches on. Given the topic catalog the configurations were
generated from, `--catalog catalog.json` shows them with field names instead
(`/symbol`), as they are written in code and in `.topo` files, with the wire
form beneath where it differs. Each topic is matched to its catalog entry by
the entry's name pattern; a topic matching no entry or several, and a path the
entry's fields do not describe, keep their wire names and add a warning.

On the page:

- each instance is a box, and the members of a replication group with several
  members sit in a frame;
- each replication destination is an arrow: solid when synchronous, dashed when
  asynchronous, two-headed between instances replicating to each other. An
  arrow to a frame is a failover list, delivered to whichever member is up; an
  arrow to an *external group* targets a group none of the files declares;
- selecting a topic highlights the instances holding it (badged with their SOW
  or journal) and the arrows carrying it, labelled with their filters;
- selecting a queue (`Queue`, `GroupLocalQueue` or `LocalQueue`) highlights
  the instances holding it and the links replicating it, and shows the topics it
  reads (a pattern in `UnderlyingTopic` is matched against the instance's
  topics), its owner and its settings. A queue's own topic is the queue, so it is
  not listed among the topics;
- selecting an instance highlights it and its replication peers, and lists its
  topics and queues with where each replicates to and from;
- clicking an arrow lists its destinations and their topics.

The selection is kept in the page's URL (`#topic=orders%2FLN`,
`#instance=Infra1`), so a link can point at it. Oddities that do not stop the
cluster being drawn — such as a destination reaching no other instance — are
listed as warnings.

**What a configuration cannot show:** AMPS configurations name the topics an
instance keeps in a SOW, journals or replicates. A plain pub/sub topic leaves
no trace in them, so it cannot appear on the page.

Replication is resolved the way AMPS addresses it, by each destination's
`<Group>`: replication addresses are not compared, since in hand-written
configurations they may be literal and unresolvable. The design is recorded in
[docs/topoview.md](docs/topoview.md).

## Editor support

Syntax highlighting for `.topo` files in VS Code and Vim/Neovim, and the
clients for topoc's language server (`topoc lsp`: errors as you type, an
outline, go to definition, references, hover, semantic colouring and
completion), live in [`editors/`](editors/README.md), with installation
instructions.

## Development

Development uses [uv](https://docs.astral.sh/uv/):

```sh
uv sync                                   # create .venv with the dev tools
uv run topoc check --topology testdata/reference.topo \
    --context testdata/reference.context.json
uv run pytest                             # tests
uv run mypy                               # strict type checking
uv run ruff check && uv run ruff format --check
```

## Layout

| Module | Responsibility |
|---|---|
| `errors` | Error types (`CompileError`, `ContextError`, `CatalogError`, `ClusterError`, `GenerateError`) |
| `dsl.tokens`, `dsl.lexer`, `dsl.syntax`, `dsl.parser` | Tokens, lexer, syntax tree, parser — grammar only |
| `context` | Generation context data, read from JSON in document order |
| `context.schema` | The `context` declaration as a schema, and the data's validation (V20, V21) |
| `resolve.resolver` | Name binding, topology fan-out, validation V1–V11, V16 and V25 |
| `resolve.data_expander` | Expansion of loops over data (lists and the context), validation V13–V15, V17, V19, V22–V24 |
| `resolve.expander` | Expansion of loops over instances, validation V13–V15 and V17–V19 |
| `resolve.interpolation`, `resolve.names`, `resolve.origin` | `{{...}}` substitution, the flat namespace and iteration notes, shared by the three above |
| `model` | The resolved cluster model handed to generators |
| `catalog`, `catalog_json` | Topic catalog and its JSON loader |
| `filters` | AMPS filter parser and field-name→wire-name rewrite |
| `amps.config` | The configuration document model, every decision made |
| `amps.builder` | Makes those decisions (coalescing, sync type, exclusions, journaling) |
| `amps.layout` | Lays the model out as XML elements, deciding nothing |
| `amps.policy` | The fixed AMPS settings, in one place |
| `amps.naming` | Endpoint environment-variable names |
| `amps.topic_name` | Checks each topic name against its definition's name pattern |
| `amps.generator` | One file per instance |
| `xmldoc` | Document serialisation on top of `xml.etree.ElementTree` (declaration, banner, indentation) |
| `view.cluster` | topoview's cluster model, read from AMPS XML configurations: instances, groups, revealed topics, replication edges |
| `view.model_json` | The cluster model's JSON form, embedded in a topoview page |
| `view.page` | A topoview page: the front end's committed template with a cluster embedded |
| `lsp.diagnostics`, `lsp.symbols`, `lsp.text` | The language server's diagnostics and outline, as functions of the source |
| `lsp.index` | What every name refers to: declarations, occurrences and hovers |
| `lsp.semantic` | Semantic tokens: each name's type, for editors to colour it by |
| `lsp.completion` | Completion: what may be typed at a position, from the text before it |
| `lsp.context_discovery` | Finds the context data for a `.topo` open in an editor |
| `lsp.server` | The language server: the protocol, over stdio |
| `cli` | Command-line front end: argument parsing and file I/O only |

Each stage is a pure function of its inputs; only `cli` and the language
server's `lsp.context_discovery` and `lsp.server` touch the file system. `topoview/` holds topoview's front end (see below). `testdata/` holds the reference topology, context, catalog and expected
output the tests run against.

## Updating the golden files

`testdata/expected/` holds the configuration generated for
`testdata/reference.topo` and its context. When output changes on purpose,
regenerate and review the diff:

```sh
uv run topoc generate --topology testdata/reference.topo \
    --context testdata/reference.context.json \
    --catalog testdata/reference.catalog.json --output-dir testdata/expected
git diff testdata/expected
```

## Front-end development (topoview)

The page is a React application using [xyflow](https://reactflow.dev) and
[ELK](https://eclipse.dev/elk/), in [`topoview/`](topoview). Vite builds it
into one self-contained HTML file, which is committed as the package's
template (`src/topoc/view/template.html`) so that using topoc needs only
Python. Node is needed only to change the front end:

```sh
cd topoview
npm ci                 # install the pinned dependencies
npm run dev            # serve the page with the reference cluster
npm test               # unit tests of the display logic
npm run build          # type-check, then rebuild the template
```

The template carries a hash of the sources it was built from, and
`tests/test_view_template.py` fails when the sources no longer match it: after
changing anything in `topoview/`, run `npm run build` and commit the template
with the change.

`npm run dev` and the unit tests use `topoview/fixtures/reference.cluster.json`,
the reference cluster read from `testdata/expected/`. Regenerate it when the
model or the goldens change:

```sh
uv run python topoview/fixtures/generate.py
```
