---
name: zbrush-subtool
description: >-
  Domain skill — select, inspect, and apply bounded refinement to subtools on
  the active ZBrush tool. Use when you need to switch the active subtool, read
  visibility/lock flags, subdivide, polish, or inflate. Not for mesh export or
  arbitrary Python — use zbrush-interchange or zbrush-scripting.
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
    search-hint: "select subtool, active subtool, subdivision, polish, inflate"
    tools: tools.yaml
---

# zbrush-subtool

Typed subtool selection, status reads, and bounded refinement via
`zbrush.commands`.
