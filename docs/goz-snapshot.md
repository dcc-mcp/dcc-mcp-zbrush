# Read-only GoZ snapshot candidate (schema v1)

This offline PoC reads an explicitly supplied `.GoZ` file and compares stored
values. It does not call ZBrush, register an MCP tool, install anything, inspect
global GoZ configuration, follow texture paths, or write a GoZ file. Existing
tools and dependencies are unchanged. The direct script uses only Python's
standard library; importing through the adapter package still imports its normal
runtime dependencies.

```sh
python -I -S src/dcc_mcp_zbrush/goz_snapshot.py synthetic.GoZ > snapshot.json
```

The output contains mesh arrays: keep real snapshots local and do not attach
user models or their snapshots to PRs, CI artifacts, telemetry, or remote tools.
File paths, mesh names, header text, texture references and unknown block payloads
are not emitted as text. Their SHA-256 digests are comparison evidence, **not
anonymization**. File read failures emit a fixed error code without the input
path. All input bytes remain unchanged.

## Public protocol evidence

- [Official GoZ SDK PDF](https://developers.maxon.net/docs/zbrush/goz_sdk.pdf),
  pp. 7–9: mesh exchange, optional per-face UV/group data, per-vertex mask/MRGB,
  maps, 32-byte file header and 16-byte block headers. The PDF directs readers
  to `GoZ_Binary.h` for tag layouts.
- [Official download page](https://developers.maxon.net/downloads/), listing
  **ZBrush GoZ SDK 2022.0**, and its
  [public SDK archive](https://developers.maxon.net/downloads/goz_sdk.zip),
  inspected 2026-10-13. Archive SHA-256:
  `50ceb446c2170e7226eaf261cbd6db2e96132f6c08476331281723ea483fc770`.
- Archive `Sources/GoZ_Binary.h` SHA-256:
  `cc2e6e3f7f334ea23dabd363bfcab923d5fa6c4906f4a15b272412507053b942`.
  `Sources/GoZ_Utils.cpp` SHA-256:
  `a2d40c4b125520475ae5fdbc2bc5c9235b274c662740d8a88987524d7decd762`.
  These define the format facts used here. SDK sources/assets are not vendored,
  compiled, or executed by this PoC.

The PDF describes EOF as a 16-byte block with size 16. The official SDK writer
sets the EOF size field to zero while still writing its full 16-byte header.
Both forms are accepted explicitly; other EOF layouts and trailing bytes fail.
This discrepancy is covered by two separate synthetic fixtures.

## Versioned snapshot contract

The machine-readable contract is
[`goz-snapshot-v1.schema.json`](goz-snapshot-v1.schema.json).
`parse_goz(bytes, identity=None, texture_inventory=None)` returns JSON-compatible
data. `read_goz(path, ...)` reads at most 16 MiB + 1 byte from that one file and
rejects oversize inputs. No partial snapshot is returned on invalid input.

| Field | v1 meaning |
| --- | --- |
| `schema_version` | Integer `1`; compare rejects other versions. |
| `validation_scope` | `stored_values_only`, never a host/roundtrip claim. |
| `source` | File SHA-256, byte length, and 32-byte header SHA-256. |
| `identity` | `null` for an unbound file, otherwise caller-supplied session/build/tool/index/host_id. |
| `name` | String-byte SHA-256, length, strict UTF-8 validity; no name text. |
| `flags` | Raw uint32 or `null`; no transforms applied. |
| `positions` | Ordered XYZ float32 values widened exactly to JSON numbers. |
| `faces` | Four ordered zero-based indices; fourth `-1` denotes triangle. |
| `uv4` | Eight floats per face, including unused triangle UV slot; or `null`. |
| `mask` | `bits` (8 or 16) and raw values; zero fully masked, maximum unmasked. |
| `polypaint_mrgb_u32` | Raw packed uint32 MRGB words, including material byte; or `null`. |
| `polygroups_u16` | Raw uint16 group ID per face, including 0/65535; or `null`. |
| `maps` | Color/normal/displacement reference digests, modifier and inventory status. |
| `blocks` | Ordered tag/offset/size/count, raw modifier hex, whole-block SHA-256 and `decoded` boolean. |

Absent optional arrays are `null`, distinct from a present empty array. The
parser accepts empty geometry with all required mesh/name/face blocks present.
Supported tags are mesh name `1`, flags `5001`, positions `10001`, faces `20001`,
UV4 `25001`, mask `30001`/`30002`, MRGB `35001`, groups `40001`, map references
`45001`/`50001`/`55001`, and EOF `0`.

Values are little-endian and in file order. No axis, winding, UV, mask inversion,
color-space conversion or material interpretation occurs. Packed MRGB words
avoid inventing an RGBA byte ordering. The schema preserves signed zero through
JSON serialization; non-finite geometry/UV/map modifiers are rejected. Unknown
modifiers remain raw hexadecimal bits, so a future tag cannot inject JSON NaN.

Unknown blocks, including subdivision/crease/material or private tags, are
digest-only evidence, **not decoded or recoverable data**. A snapshot cannot
reconstruct a complete GoZ file. Known unsupported geometry alternatives
(`10002`, `20002`, `20003`, `25002`) fail explicitly to avoid reporting an
incomplete geometry interpretation as success.

Limits are 16 MiB input, 100,000 records per decoded array, and 256 blocks
including EOF. These are deliberately small PoC budgets, not production sculpt
capacity. Validate size/count before unpacking; validate face indices and
attribute cardinality after collecting blocks. Duplicate semantic blocks,
ambiguous mask variants, truncation, missing mandatory blocks and malformed
strings fail. SDK-style strings require a terminator and zero padding to four
bytes. UTF-8 is tested strictly but is not assumed to be mandated by the SDK;
non-UTF-8 strings retain digests without lossy decoding.

### Texture inventory boundary

`texture_inventory` optionally accepts a caller-attested set of relative names
(for example `['maps/color.tif']`). The parser does no filesystem or network
lookup. Matching normalizes slash direction only and is case/Unicode-exact.
Status is `not_checked`, `inventory_present`, `inventory_missing`, or
`unresolved_reference` for absolute/traversing/invalid-UTF-8 references. Missing
textures do not erase a valid mesh. An inventory match does not validate image
contents or certify that a real exported texture exists. Actual texture-file
validation is deferred to an explicitly scoped local export check.

### Identity and comparisons

Identity is never derived from a GoZ filename or invented from a SubToolID.
`session`, `build`, and `tool` are bounded opaque tokens, not paths; `index` and
`host_id` are nonnegative integers. A binding is valid only for that capture
context. Reordering, deleting, duplicating or reloading tools requires fresh
binding. No field is a permanent universal object ID.

`identity_conflicts(snapshots)` reports repeated host IDs within
session/build/tool even when indices differ, plus repeated index locations. It
does not merge records. Unbound files remain explicitly unbound.

`compare_snapshots(before, after)` reports exact source digest equality and
changed fields, including block digests (covering unknown data and references).
The comparison is conservative: reordered blocks, padding, and modified bytes
can differ even when decoded geometry matches. Identity and inventory status
are context, not exported mesh data. A header-only edit changes the source
digest even if `changed_fields` is empty. This is evidence for a future fidelity
check, not a certificate of visual/scene equivalence.

## Validation and remaining host boundary

`tests/test_goz_snapshot.py` generates all binary data locally from independent
literal tag/layout constants. The fixtures include triangle/quad geometry,
asymmetric MRGB words, UVs, mask endpoints, group endpoints, displacement scale,
unknown tags, Unicode and invalid UTF-8, missing inventory references, duplicates,
limits and every prefix truncation of the complete fixture. The direct CLI test
uses Python `-I -S` to demonstrate no adapter/core/ZBrush dependency is needed.

These tests are **synthetic protocol tests**. No ZBrush was launched, no real
local ZBrush export was inspected, and no ZBrush import/export roundtrip was
performed. Real validation still requires a separately scheduled GUI window,
a controlled artist-owned mesh exported through official GoZ, a local inventory
and checks against the known source values. No model upload is needed. Sculpt
layers, undo/history, full subdivision state, textures' pixel data, and complete
scene fidelity are outside this candidate's claims.

## Separate status-flag follow-up

At base `931190e4bfe2cc9d5ad933494a0a5c86001dda40`, `_skill_host.py` and the socket
bridge label bit `0x02` as `locked`. The official
[Modeling API status table](https://developers.maxon.net/docs/zbrush/py/2026_1_0/api/zbr_cmds_modeling.html#zbrush.commands.get_subtool_status)
defines that bit as the folder eye; its generic bit-mask teaching example is
not the API's semantic mapping.

On 2026-10-13, active draft [#90](https://github.com/dcc-mcp/dcc-mcp-zbrush/pull/90)
(owner `loonghao`, head `d75420dbf01c61fb84ed7dea977c5c867ec8e5b7`) modifies
`bridge/plugin/mcp_socket_bridge.py`. This batch leaves that file and the status
API unchanged. Coordinate the follow-up with #90's owner before editing it;
that batch needs raw-bit regression and embedded/sidecar equivalence coverage.
Unmerged [#91](https://github.com/dcc-mcp/dcc-mcp-zbrush/pull/91) is not a dependency.
