" Vim/Neovim syntax highlighting for the .topo topology DSL.
" Language reference: docs/topo-dsl.md

if exists("b:current_syntax")
  finish
endif

syn case match
" `.` is a word character, so a dotted name or path (`instance.quoters`,
" `Quoting.key`) is one word and none of its segments matches a keyword.
syn iskeyword @,48-57,_,192-255,.

" Comments
syn match topoComment "//.*$" contains=@Spell

" Definition keywords, followed by the defined (possibly dotted) name. A topic,
" instance or group name may instead be a quoted string (highlighted as a
" string below). `type` declares a record type inside a `context` block.
syn keyword topoDefine role topic instance list type nextgroup=topoName skipwhite
" `queue` declares a queue, except as an `expiration_model` value.
syn match topoDefine "\%(expiration_model\s\+\)\@<!\<queue\>" nextgroup=topoName skipwhite
syn match topoConstant "\%(expiration_model\s\+\)\@<=\<queue\>"
syn match topoName "[A-Za-z_][A-Za-z0-9_]*\%(\.[A-Za-z_][A-Za-z0-9_]*\)*" contained

" The generation context's declaration: `context { ... }`.
syn keyword topoContext context

" Templates: `for <var>[, <value>] in <domain> { ... }`. The loop variables are
" highlighted where they are bound; `in` only right after them (it is
" contextual). A context domain is a context path, `@locations`.
syn keyword topoRepeat for nextgroup=topoLoopVar skipwhite
syn match topoLoopVar "[A-Za-z_][A-Za-z0-9_]*" contained nextgroup=topoIn,topoLoopComma skipwhite
syn match topoLoopComma "," contained nextgroup=topoLoopVar skipwhite
syn keyword topoIn in contained

" A topic's `definition` clause names its topic catalog entry by a
" ::-separated path (e.g. market::topics::REFDATA).
syn keyword topoReference definition nextgroup=topoPath skipwhite
syn match topoPath "[A-Za-z_][A-Za-z0-9_]*\%(::[A-Za-z_][A-Za-z0-9_]*\)*" contained

" Property keywords (contextual in the DSL; approximated by word match here)
syn keyword topoProperty attr durability roles group file recovery_point
" `topology <name> { ... }` declares a topology block; a bare `topology { ... }`
" is a topic's or queue's property.
syn match topoDefine "\<topology\>\ze\s\+\h" nextgroup=topoName skipwhite
syn match topoProperty "\<topology\>\ze\s*{"
" Queue properties and delivery settings
syn keyword topoProperty from publish_to delivery priority barrier target_depth
syn keyword topoProperty file_backed_metadata deferred_ack_expiration
syn keyword topoProperty lease leasing max_deliveries max_cancels backlog
syn keyword topoProperty per_subscription_backlog expiration expiration_model fairness

" Topology statements
syn keyword topoTopology mesh replicate local sow where
" `use <block>, ...`: the topology blocks whose statements stand here.
syn keyword topoTopology use nextgroup=topoBlockNames skipwhite
syn match topoBlockNames "\h[A-Za-z0-9_.]*\%(\s*,\s*\h[A-Za-z0-9_.]*\)*" contained
" A queue's owned block: `owned by <instance> { ... }`
syn match topoTopology "\<owned\s\+by\>"

" Durability kinds
syn keyword topoKind persistent transient
" Queue delivery modes
syn keyword topoKind at_least_once at_most_once

" Attribute types
" A topology block parameter's kind `selector` (a role, group or instance).
syn keyword topoType string int bool selector

" Literal constants and recovery points
syn keyword topoBoolean true false
syn keyword topoConstant now epoch
" Queue setting values
syn keyword topoConstant creation strict sublet fast round_robin proportional
syn keyword topoConstant latest earliest default

" Replication arrow
syn match topoArrow "<\?->"

" A context path: `@` and a dotted field path (`@locations`, `@site.workers`),
" as a `for` domain or at the start of a `{{...}}` path.
syn match topoContextPath "@[A-Za-z_][A-Za-z0-9_]*\%(\.[A-Za-z_][A-Za-z0-9_]*\)*"

" A bare placeholder outside a string: an attribute value taking the type of the
" value it names (`attr Quoting.port {{loc.port}}`, `attr Quoting.region {{@region}}`).
syn match topoPlaceholder "{{[^}]*}}" contains=topoContextPath

" Integer literals, and durations: an amount and a unit (`60s`, `500ms`, `1d`)
syn match topoNumber "-\?\<\d\+\>"
syn match topoDuration "\<\d\+\%(ms\|s\|m\|h\|d\)\>"

" Strings: payload data passed to AMPS. Highlight the two passthrough token
" families (${ENV}, %n) and the compiler-expanded {{...}} interpolation, with
" its context paths ({{@region}}).
syn region topoString start=+"+ skip=+\\\\\|\\"+ end=+"+ contains=topoEscape,topoInterp,topoEnv,topoAmpsSub,topoRoute
syn match topoEscape "\\[\"\\]" contained
syn match topoInterp "{{[^}]*}}" contained contains=topoContextPath
syn match topoEnv "${[^}]*}" contained
syn match topoAmpsSub "%[A-Za-z]" contained
" `$route` in a filter: the routed envelope's route, not a message field.
syn match topoRoute "\$route\>" contained

hi def link topoComment   Comment
hi def link topoDefine    Structure
hi def link topoName      Type
hi def link topoBlockNames Type
hi def link topoContext   Structure
hi def link topoRepeat    Repeat
hi def link topoLoopVar   Identifier
hi def link topoIn        Repeat
hi def link topoReference Keyword
hi def link topoPath      Type
hi def link topoProperty  Statement
hi def link topoTopology Keyword
hi def link topoKind     StorageClass
hi def link topoType     Type
hi def link topoBoolean  Boolean
hi def link topoConstant Constant
hi def link topoArrow    Operator
hi def link topoNumber   Number
hi def link topoDuration Number
hi def link topoString   String
hi def link topoEscape   SpecialChar
hi def link topoInterp   Special
hi def link topoPlaceholder Special
hi def link topoContextPath Constant
hi def link topoEnv      PreProc
hi def link topoAmpsSub  PreProc
hi def link topoRoute    Special

" Semantic tokens from the language server (`topoc lsp`), which knows what each
" name is: the same groups as the syntax above.
if has('nvim')
  for s:type in ['role', 'instance', 'group', 'topic', 'queue', 'list', 'topologyBlock', 'contextType']
    execute 'hi def link @lsp.type.' . s:type . '.topo Type'
  endfor
  hi def link @lsp.type.attribute.topo @property
  hi def link @lsp.type.contextTypeField.topo @property
  hi def link @lsp.type.loopVariable.topo Identifier
  hi def link @lsp.type.blockParameter.topo Identifier
  hi def link @lsp.type.contextField.topo Constant
endif

let b:current_syntax = "topo"
