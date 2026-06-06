# Changelog

## [0.2.3](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.2...v0.2.3) (2026-06-06)


### Documentation

* add .claude/CLAUDE.md and llms.txt agent entry points ([bf680a3](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/bf680a3a6243864a7f8360ac970009ee3d8b355d))
* fix distribution source and restart semantics across docs ([4b38657](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/4b38657bc88481b7861335da7be068128fa0c728))
* rewrite PRD.md to match current embedded+sidecar architecture ([ea6182d](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/ea6182d15f25000d235d03d7ca1c7e4012875eec))
* rewrite PRD.md with current embedded+sidecar architecture facts ([eecfe4e](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/eecfe4eb64c720cf0b4f5431ea38ed1debe234a6))
* rewrite README to release-first install flow and add development guide ([adde8a9](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/adde8a91ed5d4430477a8246217422c010e04e9b))

## [0.2.2](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.1...v0.2.2) (2026-06-05)


### Bug Fixes

* align dcc-mcp-core dependency floor to 0.18.2 ([dc0592f](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/dc0592fb5ef636ee4bf30f3f455aac2f949bf215))

## [0.2.1](https://github.com/loonghao/dcc-mcp-zbrush/compare/v0.2.0...v0.2.1) (2026-05-21)


### Features

* embedded ZBrush adapter, skills, CI, and installable plugin package ([43ac601](https://github.com/loonghao/dcc-mcp-zbrush/commit/43ac601d60b8f623266a41a30b0cd745436c2965))
* initial placeholder for dcc-mcp-zbrush ([14e1024](https://github.com/loonghao/dcc-mcp-zbrush/commit/14e1024cfdea459de665fdf4fafe2426e696ac8e))

## [0.2.0](https://github.com/loonghao/dcc-mcp-zbrush/compare/v0.1.0...v0.2.0) (2026-05-21)


### Features

* embedded ZBrush 2026.1+ Python SDK adapter with DccServerBase
* bundled skills: zbrush-scripting, zbrush-scene, zbrush-subtool, zbrush-interchange
* installable ZBrush plugin zip (embedded + sidecar socket bridge)
* CI: test, lint, skill validation, plugin artifact build
