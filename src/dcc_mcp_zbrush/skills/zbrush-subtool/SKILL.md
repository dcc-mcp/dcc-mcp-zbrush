---
name: zbrush-subtool
description: >-
  Domain skill — select and inspect subtools on the active ZBrush tool. Use when
  you need to switch the active subtool or read visibility/lock flags. Not for
  mesh export or arbitrary Python — use zbrush-interchange or zbrush-scripting.
license: MIT
compatibility: "dcc-mcp-zbrush 0.2+, ZBrush 2026.1+, dcc-mcp-core 0.18+"
allowed-tools: Bash Read
metadata:
  dcc-mcp:
    dcc: zbrush
    layer: domain
    stage: authoring
    version: "1.0.0"
    tags: [zbrush, subtool, selection, visibility, sculpt]
    search-hint: "select subtool, active subtool, subtool status, visibility"
    tools: tools.yaml
---

# zbrush-subtool

Typed subtool selection and status reads via `zbrush.commands`.
