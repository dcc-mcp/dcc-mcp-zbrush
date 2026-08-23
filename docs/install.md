# Install and lifecycle operations

This is the canonical install SOP for `dcc-mcp-zbrush`. ZBrush 2026.1 or
newer is required. The external sidecar requires Python 3.10 or newer and
`dcc-mcp-core >= 0.19.45`.

Supported host platforms are Windows and macOS. Linux can build the wheel,
inspect a dry-run plan, and run unit tests, but it cannot install or verify a
ZBrush host.

## Install the wheel

Use a fixed release version. The lifecycle intentionally rejects `latest`.

```bash
python -m pip install "dcc-mcp-zbrush==<version>"
```

Identify the ZBrush executable/application and Asset Directory. Set the Asset
Directory explicitly instead of guessing a shared startup location:

```powershell
$env:ZBRUSH_USER_ASSETS_DIR = "<ZBrush Asset Directory>"
dcc-mcp-zbrush install --version <version> --dcc-path "<ZBrush.exe>" --python python --dry-run --json
dcc-mcp-zbrush install --version <version> --dcc-path "<ZBrush.exe>" --python python --yes --json
```

```bash
export ZBRUSH_USER_ASSETS_DIR="<ZBrush Asset Directory>"
dcc-mcp-zbrush install --version <version> --dcc-path "<ZBrush.app>" --python python3 --dry-run --json
dcc-mcp-zbrush install --version <version> --dcc-path "<ZBrush.app>" --python python3 --yes --json
```

The default `sidecar` mode installs a dedicated bridge module and appends one
bounded managed import block to the shared `<AssetDir>/Python/init.py`. It
never replaces that shared file. Existing bytes are backed up before the
staged atomic replacement and recorded in
`<AssetDir>/.dcc-mcp/receipts/zbrush.json`.

The installer resolves only the fixed `v<version>` release asset, requires its
GitHub SHA-256 provenance, verifies the bytes before they enter the cache, and
rejects unsafe ZIP members. Invalid cached payloads are removed; obsolete
version cache directories are pruned after a successful install.

## Status and verification

```bash
dcc-mcp-zbrush status --version <version> --dcc-path "<ZBrush path>" --python python --json
```

Restart ZBrush so it loads the managed bootstrap, then run:

```bash
dcc-mcp-zbrush verify --version <version> --dcc-path "<ZBrush path>" --python python --json
```

For sidecar mode, `verify` checks receipt and payload hashes, reads captured
bootstrap errors, connects to the in-process socket bridge, and calls the safe
session-info probe. A valid install is not reported as directly usable until
that host probe succeeds. Afterward start the external process:

```bash
dcc-mcp-zbrush --mode sidecar
dcc-mcp-cli list
```

Embedded mode is advanced and Python-only. It does not load the Core native
wheel into ZBrush's embedded VM. Use `--mode embedded`; verification uses the
registered ZBrush instance and `zbrush_scripting__get_session_info`.

## Upgrade and uninstall

Review an upgrade plan before applying it:

```bash
dcc-mcp-zbrush upgrade --version <new-version> --dcc-path "<ZBrush path>" --python python --dry-run --json
dcc-mcp-zbrush upgrade --version <new-version> --dcc-path "<ZBrush path>" --python python --yes --json
```

Uninstall is receipt-driven. It restores the exact pre-install `init.py` when
unchanged; if another tool edited that file after installation, it removes
only the unambiguous managed block and preserves the later edits.

```bash
dcc-mcp-zbrush uninstall --version <version> --dcc-path "<ZBrush path>" --python python --dry-run --json
dcc-mcp-zbrush uninstall --version <version> --dcc-path "<ZBrush path>" --python python --yes --json
```

## JSON and exit codes

All lifecycle verbs accept `--json`, `--yes`, `--dry-run`, `--dcc-path`, and
`--python`. JSON responses use schema `1.0`; every next step has `id`,
`description`, `why`, and exactly one of `command` or `file_edit`.

| Exit | Meaning |
|---:|---|
| `0` | Complete, planned, or already in the requested state |
| `10` | Preflight, configuration, receipt, or partial-install failure |
| `20` | Payload acquisition, provenance, or checksum failure |
| `30` | Install, upgrade, uninstall, or filesystem failure |
| `40` | Verification, host-readiness, or captured bootstrap failure |
| `50` | Loaded files require ZBrush to restart before retrying |

## Troubleshooting

- `partial`: managed files or markers exist without a valid receipt, or a
  receipt-managed payload drifted. The command fails closed; do not delete the
  shared `init.py`. Restore from the receipt backup or inspect the reported
  path before retrying.
- `locked_files`: close ZBrush, confirm no host process is loading the managed
  files, then retry. The installer stages replacements and rolls back a failed
  commit.
- `bootstrap_failed`: inspect
  `<AssetDir>/.dcc-mcp/bootstrap-errors.jsonl`; restart ZBrush after correcting
  the reported SDK, import, or socket error.
- `host_unavailable` / exit `40`: start or restart ZBrush and rerun `verify`.
  Gateway health alone is not proof that the in-process ZBrush bridge is ready.
- Checksum or provenance errors are not bypassable. Select an official fixed
  release version and retry; never substitute a mutable CDN or an unverified
  ZIP.

Canonical raw URL:
<https://raw.githubusercontent.com/dcc-mcp/dcc-mcp-zbrush/main/docs/install.md>
