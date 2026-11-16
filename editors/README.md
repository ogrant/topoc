# Editor support for the `.topo` DSL

Syntax highlighting for the topology DSL in VS Code and Vim/Neovim, and clients
for topoc's language server. The language itself is documented in
[`docs/topo-dsl.md`](../docs/topo-dsl.md).

The commands below assume the repository is checked out at `~/dev/topoc`;
adjust the paths if yours lives elsewhere.

## Language server (`topoc lsp`)

`topoc lsp` serves the Language Server Protocol over stdio. For each open
`.topo` it:

- reports what `topoc check` would, as you type. The compiler stops at its
  first error, so there is at most one error at a time;
- lists its definitions as an outline;
- goes to a name's definition, finds its references and highlights its
  occurrences: roles, attributes (`R.key`, `{{Quoting.key}}`), instances,
  groups, topics, queues, lists, topology blocks (`use ha`), context fields and
  types (`@sites`, `loc.port_base`), and loop variables;
- shows on hover what a name is: a role's attributes and the instances carrying
  it, an instance's roles, group and attributes, a group's members, a block's
  statements, a loop variable's domain and, when known, its values;
- colours each name by what it is (semantic tokens), where the grammars can
  only guess from its spelling: a loop variable named `instance` is coloured as
  a loop variable, and a group in `mesh Infra` as a group. Names inside strings
  and `{{...}}` keep the grammars' colours. The token types are topo's own
  (`role`, `group`, `loopVariable`, `contextField`, ...); VS Code maps each to
  the TextMate scope the grammar gives the same thing, and Neovim links
  `@lsp.type.<type>.topo` to the syntax file's groups, so colours stay as the
  grammars have them. The VS Code extension turns semantic highlighting on for
  `.topo` files (`"[topo]": { "editor.semanticHighlighting.enabled": true }`),
  since many themes leave it off. Recolour them with
  `editor.semanticTokenColorCustomizations` in VS Code, or by setting the
  `@lsp.type.*.topo` groups in Neovim.

- completes as you type: the keywords a block takes, then names of the kind
  the keyword before the cursor expects (groups after `mesh`, topology blocks
  after `use`, roles in `roles [...]`, topics after `from`, `Role.attr` after
  `attr`, a `for`'s domains after `in`, a block's parameters in its statements,
  and in `use b(...)` the parameters not yet given, then names of each one's
  kind), values (`durability`, `delivery`,
  `fairness`, ...), context fields after `@`, fields and attributes after `.`,
  and inside `{{...}}` the loop variables in scope and their paths. It
  triggers on `.` and `@`; elsewhere ask for it (Ctrl-Space in VS Code,
  Ctrl-X Ctrl-O in Neovim). Declarations come from the last version of the
  file that parsed, so names stay on offer while the line being typed does not
  parse.

Names are linked without compiling, from the file alone, so a name a loop
generates (`"Quoting.{{loc}}"`) links through its placeholders only. These
features need the file to parse; while it does not, the last colours stand.

Install `topoc` on your PATH so editors can start it; `--editable` makes it
follow this checkout:

```sh
uv tool install --editable ~/dev/topoc
```

A topology that declares a `context` block needs context data to be checked.
The server uses the first of:

1. the file configured for the topology (`contextFiles` below);
2. `<name>.context.json` beside `<name>.topo`;
3. the `context:` section of an `env.yaml` beside it.

Without any, the file is still parsed and its `context` declaration checked,
and a note on the `context` block says its loops are not.

## VS Code (`vscode/topo-syntax`)

An extension with language registration for `*.topo`, bracket and comment
configuration, TextMate grammars (`.topo` files, and ` ```topo ` blocks in
Markdown), and a client for the language server. Its settings:

- `topo.server.command`: the command starting the server, default
  `["topoc", "lsp"]`; empty turns it off.
- `topo.contextFiles`: context files for topologies whose context is not
  beside them, e.g. `{ "envs/a/system.topo": "ctx/a.json" }`, relative to the
  workspace root.

The client is TypeScript; build it once, and after changing `src/`:

```sh
cd ~/dev/topoc/editors/vscode/topo-syntax
npm install && npm run compile
```

**Symlink** (simplest; edits apply after *Developer: Reload Window*):

```sh
ln -s ~/dev/topoc/editors/vscode/topo-syntax ~/.vscode/extensions/topoc.topo-syntax-0.3.0
```

Then reload VS Code. For VS Code Insiders or VSCodium, use
`~/.vscode-insiders/extensions` or `~/.vscode-oss/extensions` instead.

**Packaged** (a `.vsix` you can share or install on another machine):

```sh
cd ~/dev/topoc/editors/vscode/topo-syntax
npx @vscode/vsce package --allow-missing-repository
code --install-extension topo-syntax-0.3.0.vsix
```

To uninstall, remove the symlink, or run
`code --uninstall-extension topoc.topo-syntax`.

## Vim / Neovim (`nvim/`)

Classic `syntax` and `ftdetect` runtime files: no tree-sitter compilation, and
the same files work in both Vim and Neovim.

**Neovim with lazy.nvim**, as a local plugin spec:

```lua
{ dir = "~/dev/topoc/editors/nvim", name = "topo-syntax" }
```

**Neovim without a plugin manager**, in `init.lua`:

```lua
vim.opt.runtimepath:append(vim.fn.expand("~/dev/topoc/editors/nvim"))
```

**Vim**, in `~/.vimrc`:

```vim
set runtimepath+=~/dev/topoc/editors/nvim
```

or copy (or symlink) the files into your Vim directory:

```sh
mkdir -p ~/.vim/syntax ~/.vim/ftdetect
ln -s ~/dev/topoc/editors/nvim/syntax/topo.vim ~/.vim/syntax/topo.vim
ln -s ~/dev/topoc/editors/nvim/ftdetect/topo.vim ~/.vim/ftdetect/topo.vim
```

Both need `syntax on` and `filetype on`, which most configurations already
enable. Opening a `.topo` file should then report `:set filetype?` as
`filetype=topo`.

**Language server** (Neovim 0.11+): with `editors/nvim` on the runtime path,
`lsp/topo.lua` configures it; enable it in `init.lua`:

```lua
vim.lsp.enable("topo")
```

Context files for topologies whose context is not beside them go in
`init_options`:

```lua
vim.lsp.config("topo", {
  init_options = { contextFiles = { ["envs/a/system.topo"] = "ctx/a.json" } },
})
```

## Highlighting model

Both grammars follow the same scheme:

- `role` / `topic` / `queue` / `instance` / `list` are definition keywords, as are
  `context` and, inside it, `type`, and `topology` when a name follows it (a
  topology block); the name that follows is highlighted as a type. `use`, which
  names topology blocks, is a topology verb, and the names after it types. A topic or instance named by a quoted string
  (`instance "Quoting.{{loc}}"`) keeps the keyword highlight and the string's.
  `type` followed by `:` is a field named `type` in the DSL, but is still
  highlighted as the keyword. `queue` after `expiration_model` is a value, and
  is highlighted as a constant.
- `for <var> in` and `for <key>, <value> in` are highlighted as loop headers:
  `for` and `in` as loop keywords and the variables as parameters. `in` is only
  highlighted there, so it stays usable as an ordinary name elsewhere.
- A topic's `definition` clause names its topic catalog entry by a
  `::`-separated path (e.g. `market::topics::REFDATA`); the keyword is
  highlighted and the path as a type.
- Property keywords (`attr`, `durability`, `topology`, `roles`, `group`,
  `file`, `recovery_point`, and a queue's `from`, `publish_to`, `delivery`,
  `priority`, `barrier`, `target_depth`, `file_backed_metadata`,
  `deferred_ack_expiration` and delivery settings such as `lease` and
  `fairness`) and topology verbs (`mesh`, `replicate`, `local`, `sow`, `where`,
  and a queue's `owned by`) are keywords. The DSL treats keywords as contextual, so an
  attribute named after one (e.g. `group`) will still be highlighted as a
  keyword — a cosmetic approximation both editors share.
- A context path, `@` and a field (`@locations`, `{{@region}}`), is highlighted
  as a constant, on its own or inside `{{...}}`.
- A dotted name or path (`instance.quoters`, `Quoting.key`) is one name: none of
  its segments is highlighted as a keyword.
- A bare `{{path}}` attribute value outside a string is highlighted like an
  interpolation.
- Inside strings, the substitution families are distinguished: `{{...}}`
  (`{{Role.attr}}`, loop variables and context paths, expanded by topoc),
  `${...}`
  (environment, passed through) and `%n`-style AMPS tokens (passed through).
  `$route` in a filter, the routed envelope's route, is highlighted as a
  language variable.
- `persistent`/`transient` and the queue delivery modes
  `at_least_once`/`at_most_once` are storage modifiers;
  `now`/`epoch`/`true`/`false` and queue setting values (`creation`, `strict`,
  `round_robin`, ...) are constants; `string`/`int`/`bool` are types; integers
  and durations (`60s`, `500ms`) are numbers; `->` and `<->` are operators.
