# Changelog

## 1.0.0

The first release. `topoc` compiles a `.topo` topology into one AMPS XML
configuration file per instance, and draws any AMPS cluster from its
configuration files (`topoc view`). The language is documented in
[docs/topo-dsl.md](docs/topo-dsl.md).

**The language**

- Roles (typed attributes, defaults), instances, groups (explicit, or a
  singleton per instance), topics and their topology: `mesh`, one- and two-way
  `replicate` (filtered with `where`), `local`, and `sow` with persistent or
  transient durability.
- Queues: `from` topics or a queue's own name, both delivery modes and their
  settings, priority or barrier ordering, placed `local`, replicated, or owned
  by domains (`owned by`).
- Templates: `for` over lists, roles, groups and instances, and over the
  generation context: lists, maps (one or two variables) and integers.
- A typed generation context (`context { ... }`), its fields written `@field`,
  supplied as JSON.
- Topology blocks (`topology name { ... }`, `use name`), with named, kinded
  parameters (`group`, `role`, `instance`, `selector`).
- Topic and queue names checked against the topic catalog's definitions;
  filters written in field names, rewritten to wire names.
- Validation checks V1 to V32, each reported at its location, with the
  iterations that generated a definition.

**Tools**

- `topoc check`, `topoc generate`, and `topoc view` (topoview).
- `topoc lsp`, a language server: diagnostics as you type, an outline, go to
  definition, references, hover, semantic colouring and completion. It finds
  each topology's context data beside it.
- Editor support for VS Code (grammars, including ` ```topo ` blocks in
  Markdown, and the language server client) and Vim/Neovim (syntax, and the
  language server configuration for Neovim 0.11).
