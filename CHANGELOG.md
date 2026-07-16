# Changelog

## [0.2.18](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.17...v0.2.18) (2026-07-16)


### Bug Fixes

* use OS-assigned MCP instance ports ([#47](https://github.com/dcc-mcp/dcc-mcp-zbrush/issues/47)) ([113232c](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/113232cd88517f5336152cee21d083017511a013))

## [0.2.17](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.16...v0.2.17) (2026-07-14)


### Bug Fixes

* stabilize ZBrush sidecar bridge ([#45](https://github.com/dcc-mcp/dcc-mcp-zbrush/issues/45)) ([590b413](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/590b4135406f48a6958b08bcde5700b244457ffb))

## [0.2.16](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.15...v0.2.16) (2026-07-13)


### Bug Fixes

* keep ZBrush UI responsive ([#43](https://github.com/dcc-mcp/dcc-mcp-zbrush/issues/43)) ([1c49dd8](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/1c49dd846f7ff66efc135c3619a78fe400dc6cd9))

## [0.2.15](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.14...v0.2.15) (2026-07-13)


### Bug Fixes

* serialize zbrush bridge requests ([#41](https://github.com/dcc-mcp/dcc-mcp-zbrush/issues/41)) ([e5230bb](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/e5230bb9dd01065e8b8f81a0ecfb3f56cb8359c2))

## [0.2.14](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.13...v0.2.14) (2026-07-13)


### Bug Fixes

* publish ZBrush import schema ([#39](https://github.com/dcc-mcp/dcc-mcp-zbrush/issues/39)) ([2a35b3e](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/2a35b3e9e56a669194754a29cd77e0766ad6f951))

## [0.2.13](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.12...v0.2.13) (2026-07-13)


### Bug Fixes

* restore ZBrush plugin execution ([#37](https://github.com/dcc-mcp/dcc-mcp-zbrush/issues/37)) ([8397a8b](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/8397a8b74bd9b6107ccc9bf3d3392fb574217e03))

## [0.2.12](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.11...v0.2.12) (2026-07-05)


### Features

* embedded ZBrush adapter, skills, CI, and installable plugin package ([5d196a0](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/5d196a08fed5f96aa95a289247162526e77f6468))
* initial placeholder for dcc-mcp-zbrush ([8e89623](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/8e89623f4ac73bcc1ce2c7edb54d40f41c7ed1ed))


### Bug Fixes

* **bootstrap:** handle GitHub API rate limit in dry-run mode ([04e4ac9](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/04e4ac9bb31baaa959f44ea73648253acb5aeab0))
* recover main from v0.2.11 ([f071449](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/f0714496bd840ac56e838f0803f41b10d9db726e))
* **test:** update GITHUB_REPO assertion to match canonical org repo ([cde75d6](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/cde75d6698b1ac9dc21b1f7c105590221b311d97))

## [0.2.11](https://github.com/dcc-mcp/dcc-mcp-zbrush/compare/v0.2.10...v0.2.11) (2026-06-23)


### Features

* add agent docs single-source-of-truth sync script ([6bc3746](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/6bc37468e0bf968d1d4869d2770ba512ccadd18e))
* add bootstrap auto-install script + CI smoke test ([f133a1f](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/f133a1f66489ba092c84a266b54099b3b759ae0d))
* embedded ZBrush adapter, skills, CI, and installable plugin package ([5d196a0](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/5d196a08fed5f96aa95a289247162526e77f6468))
* implement zbrush-import-to-scene skill (PIP-1895) ([#29](https://github.com/dcc-mcp/dcc-mcp-zbrush/issues/29)) ([6467bf5](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/6467bf576e2e5cd41e3053a09e55545f6e98c096))
* initial placeholder for dcc-mcp-zbrush ([8e89623](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/8e89623f4ac73bcc1ce2c7edb54d40f41c7ed1ed))


### Bug Fixes

* **ci:** isolate workflow_dispatch from push concurrency in release workflow ([#15](https://github.com/dcc-mcp/dcc-mcp-zbrush/issues/15)) ([643108c](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/643108c2591efbde3919659efc56a19b6ba1ee45))
* **ci:** set cancel-in-progress to true in release workflow concurrency ([001b61b](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/001b61b18ff2c8ac851dc4757526062914b7d05b))
* guard windows-only path test with sys.platform check and fix ruff format ([7f6f051](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/7f6f0516cb122ecbba4510e73cd2b397416d5945))
* read __version__ from package metadata instead of hardcoding ([997afb4](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/997afb4288b63b8949d70aa12cd59f87924d2bb2))
* remove extraneous f-string prefix in sync script ([263bed2](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/263bed2696b0707e14d0d075cc713e46031410dd))
* sort imports and apply ruff format to test_agent_instruction_files.py ([5ec258f](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/5ec258fba20c95665b1db2f23c2c4ad4721d68de))
* use artifact name from YAML instead of hardcoded package name ([887bf37](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/887bf3785a288a99c824bcf23a47a27b243612d7))


### Documentation

* add .claude/CLAUDE.md and llms.txt agent entry points ([7fbcd97](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/7fbcd97a74e76095814cc52af7cd0c0a6e44de49))
* align core version references with package floor ([7d16276](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/7d16276e36ff98a5c199419f80885b33a1467dfa))
* clarify mode defaults and gateway-port semantics ([#14](https://github.com/dcc-mcp/dcc-mcp-zbrush/issues/14)) ([2f9786d](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/2f9786d0688c81ec907138b14ded0c919a994465))
* fix distribution source and restart semantics across docs ([e78944c](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/e78944c265aec44d339c4309d2cad4a404116b18))
* rewrite PRD.md to match current embedded+sidecar architecture ([96867b8](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/96867b85f0731b01cecc51e7fa21815c1a1f8055))
* rewrite PRD.md with current embedded+sidecar architecture facts ([5aa8952](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/5aa89520058e681f6de3715182b8bb02b011d92b))
* rewrite README to release-first install flow and add development guide ([3e7be21](https://github.com/dcc-mcp/dcc-mcp-zbrush/commit/3e7be21354dfae2811839864829cc3c697aa4a68))

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
