---
name: zbrush-subtool
description: >-
  Domain skill — select, inspect, and apply bounded refinement to subtools on
  the active ZBrush tool. Use when you need to switch the active subtool, read
  visibility/lock flags, inspect mesh metrics, create a lower-density ZRemesher
  copy, subdivide, polish, inflate, or bake normal/displacement maps. Not for
  mesh export or arbitrary Python — use zbrush-interchange or zbrush-scripting.
license: MIT
compatibility: "dcc-mcp-zbrush 0.2+, ZBrush 2026.1+, dcc-mcp-core 0.20.14+"
allowed-tools: Bash Read
metadata:
  dcc-mcp:
    dcc: zbrush
    layer: domain
    stage: authoring
    version: "1.2.0"
    tags: [zbrush, subtool, selection, visibility, sculpt, polygon, zremesh, uv, baking]
    search-hint: "select subtool, active mesh face count, zremesh, reduce polygon count, wireframe, uv bounds, subdivision, normal map, displacement map"
    tools: tools.yaml
---

# zbrush-subtool

Typed subtool selection, mesh metrics, bounded ZRemesher/refinement, and TIFF
map baking via `zbrush.commands`. ZRemesher and baking execute as one
main-thread SDK operation; the socket bridge rejects parallel work while it is
busy rather than queueing another scene mutation.
