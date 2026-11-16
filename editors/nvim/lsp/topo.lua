-- The .topo language server, `topoc lsp`: diagnostics as you type, an outline,
-- go to definition, references, hover, semantic colouring and completion
-- (`omnifunc`, Ctrl-X Ctrl-O). With editors/nvim on the runtime path, enable
-- it with
--
--   vim.lsp.enable('topo')
--
-- `topoc` must be on PATH (`uv tool install --editable <topoc checkout>`).
-- Context data is found beside each .topo (`<name>.context.json`, or an
-- `env.yaml` with a `context:` section); map other topologies to their context
-- files, relative to the workspace root, in `init_options.contextFiles`:
--
--   vim.lsp.config('topo', {
--     init_options = { contextFiles = { ['envs/a/system.topo'] = 'ctx/a.json' } },
--   })
return {
  cmd = { 'topoc', 'lsp' },
  filetypes = { 'topo' },
  root_markers = { '.git' },
  init_options = { contextFiles = vim.empty_dict() },
}
