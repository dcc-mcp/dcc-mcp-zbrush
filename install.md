# Install and lifecycle operations

## Requirements

This is the canonical install SOP for `dcc-mcp-zbrush`. ZBrush 2026.1 or
newer is required. The external sidecar requires Python 3.10 or newer and
`dcc-mcp-core >= 0.20.14`.

## Supported versions

Supported host platforms are Windows and macOS. Linux can build the wheel and
run contract tests, but it cannot authenticate, install, or verify a ZBrush
host.

## Agent quick path

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

## Manual path

The default `sidecar` mode installs a dedicated bridge module and appends one
bounded managed import block to the shared `<AssetDir>/Python/init.py`. It
never replaces that shared file. Existing bytes are backed up before the
staged atomic replacement and recorded in
`<AssetDir>/.dcc-mcp/receipts/zbrush.json`.

Preflight accepts only a native Maxon-signed ZBrush product with canonical
2026.1+ version metadata; a path or filename alone is never treated as a host.
The installer resolves only the fixed `v<version>` release asset, requires its
GitHub SHA-256 provenance, verifies the bytes before they enter the cache, and
rejects unsafe ZIP members. Invalid cached payloads are removed; obsolete
version cache directories are pruned after a successful install.

An applied install or upgrade returns exit `50` while the candidate is staged.
The receipt retains immutable recovery snapshots until `verify` binds the
exact ZBrush executable, PID/start identity, bridge instance, endpoint, loaded
module origins, and version. Verification commits the candidate only after all
bindings succeed; otherwise it restores the exact prior installation.

## Verify

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
wheel into ZBrush's embedded VM. Use `--mode embedded`; verification selects
one registry instance and rejects a probe unless its PID/start identity,
endpoint, product bytes, adapter module, and `zbrush.commands` origin all match
the receipt.

## Upgrade

Review an upgrade plan before applying it:

```bash
dcc-mcp-zbrush upgrade --version <new-version> --dcc-path "<ZBrush path>" --python python --dry-run --json
dcc-mcp-zbrush upgrade --version <new-version> --dcc-path "<ZBrush path>" --python python --yes --json
```

## Uninstall

Uninstall is receipt-driven and transactional. It restores the exact
pre-install `init.py` when unchanged; if another tool edited that file after
installation, it removes only the unambiguous managed block and preserves the
later edits. Embedded trees record typed file/directory/link ownership, so
operator-created files remain in place. Any failed removal restores the
receipt, payload, shared startup state, and backups before returning.

```bash
dcc-mcp-zbrush uninstall --version <version> --dcc-path "<ZBrush path>" --python python --dry-run --json
dcc-mcp-zbrush uninstall --version <version> --dcc-path "<ZBrush path>" --python python --yes --json
```

## JSON and exit codes

All lifecycle verbs accept `--json`, `--yes`, `--dry-run`, `--dcc-path`, and
`--python`. JSON responses use Install SOP schema version `1`; every next step has `id`,
`description`, `why`, and exactly one of `command` or `file_edit`.

| Exit | Meaning |
|---:|---|
| `0` | Complete, planned, or already in the requested state |
| `10` | Preflight, configuration, receipt, or partial-install failure |
| `20` | Payload acquisition, provenance, or checksum failure |
| `30` | Install, upgrade, uninstall, or filesystem failure |
| `40` | Verification, host-readiness, or captured bootstrap failure |
| `50` | A candidate needs exact host verification, or loaded files require restart |

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
<https://raw.githubusercontent.com/dcc-mcp/dcc-mcp-zbrush/main/install.md>
