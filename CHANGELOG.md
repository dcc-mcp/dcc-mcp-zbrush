# Changelog

## [0.2.10](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.9...v0.2.10) (2026-06-22)


### Features

* implement zbrush-import-to-scene skill (PIP-1895) ([#29](https://github.com/dcc-mcp/dcc-mcp-zbrush/issues/29)) ([dec6508](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/dec65089d2c61ea33ecada036b12f42999d344a3))

## [0.2.10](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.9...v0.2.10) (2026-06-08)

### Dependencies

* bump dcc-mcp-core floor to >=0.18.14 for DccServerBase 4 seam controllers (PIP-688)

## [0.2.9](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.8...v0.2.9) (2026-06-08)


### Features

* add agent docs single-source-of-truth sync script ([c87d7f0](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/c87d7f09ad76d8ff5fb6789c7f1d7296bec84616))


### Bug Fixes

* remove extraneous f-string prefix in sync script ([110aff0](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/110aff0cb9285ce9c3110e8c502935355943464d))
* use artifact name from YAML instead of hardcoded package name ([82ef1a9](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/82ef1a99d34d7adba6b9a80352f1f4e3e501d7ed))

## [0.2.8](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.7...v0.2.8) (2026-06-07)


### Features

* add bootstrap auto-install script + CI smoke test ([aa86410](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/aa8641055e47e61e5243cd7ade93e7c48b8df202))


### Bug Fixes

* guard windows-only path test with sys.platform check and fix ruff format ([3e37ae1](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/3e37ae1e735b55a91f181cfa0749262e16986645))
* sort imports and apply ruff format to test_agent_instruction_files.py ([e5dfcf4](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/e5dfcf4a157a53ac634c863488534334896837ca))

## [0.2.7](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.6...v0.2.7) (2026-06-07)


### Documentation

* clarify mode defaults and gateway-port semantics ([#14](https://github.com/dcc-mcp/dcc-mcp-zbrush/issues/14)) ([8b4449b](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/8b4449baf93dfb83438ab514c3751f9e7d61941b))

## [0.2.6](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.5...v0.2.6) (2026-06-06)


### Documentation

* align core version references with package floor ([dc75409](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/dc7540973e3ca2a56d844f5b6c09e3276a208857))

## [0.2.5](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.4...v0.2.5) (2026-06-06)


### Bug Fixes

* **ci:** isolate workflow_dispatch from push concurrency in release workflow ([#15](https://github.com/dcc-mcp/dcc-mcp-zbrush/issues/15)) ([28076a2](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/28076a2028bda88332e64e485366aa3ce7e3adc0))

## [0.2.4](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.3...v0.2.4) (2026-06-06)


### Bug Fixes

* **ci:** set cancel-in-progress to true in release workflow concurrency ([88d915e](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/88d915ecf75a48089e1bc13585a7c48a0dfadbd5))

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
