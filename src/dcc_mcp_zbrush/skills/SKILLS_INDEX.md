# ZBrush Bundled Skills Index

Progressive loading stages for `dcc-mcp-zbrush`. Minimal mode loads **bootstrap + scene** at startup.

| Stage | Skills | Default loaded |
|-------|--------|----------------|
| `bootstrap` | `zbrush-scripting` | yes |
| `scene` | `zbrush-scene` | yes |
| `authoring` | `zbrush-subtool` | no |
| `interchange` | `zbrush-interchange` | no |
| `pipeline` | _(planned: publish, validation, render)_ | no |

## Common chains

| Task | Chain |
|------|-------|
| Verify MCP session | `zbrush_scripting__get_session_info` |
| Inspect active tool | `zbrush_scene__get_scene_info` → `zbrush_scene__list_subtools` |
| Switch subtool | `load_skill("zbrush-subtool")` → `zbrush_subtool__select_subtool` |
| Export to OBJ | `zbrush_subtool__select_subtool` → `load_skill("zbrush-interchange")` → `zbrush_interchange__export_active_subtool_obj` |
| Escape hatch | `load_skill("zbrush-scripting")` → `zbrush_scripting__execute_python` (last resort) |

## Skill inventory

| Skill | Tools | Layer |
|-------|-------|-------|
| `zbrush-scripting` | `execute_python`, `get_session_info` | thin-harness |
| `zbrush-scene` | `get_scene_info`, `list_subtools` | domain |
| `zbrush-subtool` | `select_subtool`, `get_subtool_status` | domain |
| `zbrush-interchange` | `export_active_subtool_obj` | domain |
