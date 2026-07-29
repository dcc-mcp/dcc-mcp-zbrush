---
name: zbrush-viewport
description: >-
  Domain skill — capture deterministic ZBrush turntable frames by rotating the
  active tool and exporting the document. Use for visual review evidence and
  rotation showcases. Not for clicking or inspecting ZBrush UI controls — use
  the shared ui-control skill for that.
license: MIT
compatibility: "dcc-mcp-zbrush 0.2+, ZBrush 2026.1+, dcc-mcp-core 0.19.45+"
allowed-tools: Bash Read Write
metadata:
  dcc-mcp:
    dcc: zbrush
    layer: domain
    stage: presentation
    version: "1.0.0"
    tags: [zbrush, viewport, capture, render, turntable]
    search-hint: "capture viewport, render turntable, rotate model, export document frames, visual evidence"
    tools: tools.yaml
---

# zbrush-viewport

Captures PSD frames through ZBrush's native `set_transform`, optional BPR
render, and `Document:Export` controls. The tool restores the exact starting
transform after success or failure and suppresses scripted action feedback.

