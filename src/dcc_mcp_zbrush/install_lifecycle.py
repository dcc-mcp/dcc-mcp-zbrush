"""Secure, receipt-driven ZBrush adapter installation lifecycle.

The ZBrush Asset Directory is a shared host location.  This module therefore
owns the host-specific transaction while delegating native lock inspection to
``dcc-mcp-core``.  Core's tree helper currently removes a destination before
copying, which is not suitable for this shared location, so replacement here
is staged beside the destination and committed with a rename.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ADAPTER = "zbrush"
SCHEMA_VERSION = "1.0"
MINIMUM_ZBRUSH = (2026, 1)
MINIMUM_CORE = (0, 19, 45)
EXIT_OK = 0
EXIT_PREFLIGHT = 10
EXIT_ACQUIRE = 20
EXIT_INSTALL = 30
EXIT_VERIFY = 40
EXIT_REQUIRES_RESTART = 50
REPOSITORY = "dcc-mcp/dcc-mcp-zbrush"
RECEIPT_RELATIVE = Path(".dcc-mcp/receipts/zbrush.json")
BOOTSTRAP_ERRORS_RELATIVE = Path(".dcc-mcp/bootstrap-errors.jsonl")
SIDECAR_RELATIVE = Path("Python/dcc_mcp_zbrush_socket_bridge.py")
SHARED_INIT_RELATIVE = Path("Python/init.py")
MANAGED_START = b"# >>> dcc-mcp-zbrush managed bootstrap >>>\n"
MANAGED_END = b"# <<< dcc-mcp-zbrush managed bootstrap <<<\n"
MANAGED_BLOCK = (
    MANAGED_START
    + b"try:\n"
    + b"    import dcc_mcp_zbrush_socket_bridge as _dcc_mcp_zbrush_socket_bridge\n"
    + b"except Exception as _dcc_mcp_zbrush_bootstrap_error:\n"
    + b"    import json as _dcc_mcp_zbrush_json\n"
    + b"    import os as _dcc_mcp_zbrush_os\n"
    + b"    import time as _dcc_mcp_zbrush_time\n"
    + b"    try:\n"
    + b"        _dcc_mcp_zbrush_root = _dcc_mcp_zbrush_os.path.dirname(_dcc_mcp_zbrush_os.path.dirname(_dcc_mcp_zbrush_os.path.abspath(__file__)))\n"
    + b"        _dcc_mcp_zbrush_errors = _dcc_mcp_zbrush_os.environ.get('DCC_MCP_ZBRUSH_BOOTSTRAP_ERRORS') or _dcc_mcp_zbrush_os.path.join(_dcc_mcp_zbrush_root, '.dcc-mcp', 'bootstrap-errors.jsonl')\n"
    + b"        _dcc_mcp_zbrush_os.makedirs(_dcc_mcp_zbrush_os.path.dirname(_dcc_mcp_zbrush_os.path.abspath(_dcc_mcp_zbrush_errors)), exist_ok=True)\n"
    + b"        with open(_dcc_mcp_zbrush_errors, 'a', encoding='utf-8') as _dcc_mcp_zbrush_stream:\n"
    + b"            _dcc_mcp_zbrush_stream.write(_dcc_mcp_zbrush_json.dumps({'timestamp': _dcc_mcp_zbrush_time.time(), 'stage': 'sidecar_import', 'reason': str(_dcc_mcp_zbrush_bootstrap_error), 'exception_type': type(_dcc_mcp_zbrush_bootstrap_error).__name__}, sort_keys=True) + '\\n')\n"
    + b"    except Exception:\n"
    + b"        pass\n"
    + b"    raise\n"
    + MANAGED_END
)
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class LifecycleRequest:
    """Inputs shared by the public lifecycle commands."""

    operation: str
    mode: str = "sidecar"
    version: str = ""
    dcc_path: Optional[Path] = None
    python_path: Optional[Path] = None
    asset_dir: Optional[Path] = None
    yes: bool = False
    dry_run: bool = False
    socket_host: str = "127.0.0.1"
    socket_port: int = 9876


class LifecycleFailure(RuntimeError):
    def __init__(self, exit_code: int, stage: str, reason: str, *, next_steps: Optional[list[dict[str, str]]] = None):
        super().__init__(reason)
        self.exit_code = exit_code
        self.stage = stage
        self.reason = reason
        self.next_steps = next_steps or []


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_step(step_id: str, description: str, why: str, *, command: str = "", file_edit: str = "") -> dict[str, str]:
    result = {"id": step_id, "description": description, "why": why}
    if command:
        result["command"] = command
    elif file_edit:
        result["file_edit"] = file_edit
    else:
        raise ValueError("A next step requires command or file_edit")
    return result


def _result(
    request: LifecycleRequest,
    *,
    status: str,
    exit_code: int,
    stage: str,
    reason: str,
    changed: bool = False,
    directly_usable: bool = False,
    receipt: Optional[Path] = None,
    detected: Optional[Mapping[str, Any]] = None,
    next_steps: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "adapter": ADAPTER,
        "operation": request.operation,
        "status": status,
        "exit_code": exit_code,
        "stage": stage,
        "reason": reason,
        "changed": changed,
        "directly_usable": directly_usable,
        "mode": request.mode,
        "version": request.version,
        "receipt": str(receipt) if receipt else None,
        "detected": dict(detected or {}),
        "next_steps": list(next_steps or []),
    }


def _parse_version(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value)
    return tuple(int(number) for number in numbers[:4])


def _host_version(path: Path) -> Optional[tuple[int, int]]:
    match = re.search(r"(?i)zbrush(?:data|\s|[_-])*(\d{4})(?:[._ -](\d+))?", str(path))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or 0)


def _validate_root(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise LifecycleFailure(EXIT_PREFLIGHT, "preflight", f"Refusing broad {label} path: {resolved}")
    return resolved


def _python_probe(path: Path) -> str:
    resolved = _resolve_python(path)
    if resolved == Path(sys.executable).resolve():
        current = sys.version_info
        detected = f"{current.major}.{current.minor}.{current.micro}"
    else:
        try:
            completed = subprocess.run(
                [str(resolved), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LifecycleFailure(EXIT_PREFLIGHT, "python", f"Could not execute Python interpreter: {exc}") from exc
        if completed.returncode != 0:
            raise LifecycleFailure(EXIT_PREFLIGHT, "python", "Python interpreter probe failed")
        detected = completed.stdout.strip()
    if _parse_version(detected) < (3, 10):
        raise LifecycleFailure(EXIT_PREFLIGHT, "python", f"Python 3.10+ is required; detected {detected}")
    return detected


def _resolve_python(path: Path) -> Path:
    candidate = path.expanduser()
    if candidate.is_file():
        return candidate.resolve()
    discovered = shutil.which(str(path))
    if discovered:
        return Path(discovered).resolve()
    raise LifecycleFailure(EXIT_PREFLIGHT, "python", f"Python interpreter does not exist: {path}")


def _preflight(request: LifecycleRequest, *, require_paths: bool = True) -> dict[str, Any]:
    if request.mode not in {"embedded", "sidecar"}:
        raise LifecycleFailure(EXIT_PREFLIGHT, "mode", f"Unsupported install mode: {request.mode}")
    if not request.version or request.version.lower() == "latest":
        raise LifecycleFailure(
            EXIT_PREFLIGHT, "version", "A fixed adapter version is required; 'latest' is not accepted"
        )
    try:
        core = package_version("dcc-mcp-core")
    except PackageNotFoundError as exc:
        raise LifecycleFailure(EXIT_PREFLIGHT, "core", "dcc-mcp-core is not installed") from exc
    if _parse_version(core) < MINIMUM_CORE:
        raise LifecycleFailure(
            EXIT_PREFLIGHT,
            "core",
            f"dcc-mcp-core {'.'.join(map(str, MINIMUM_CORE))}+ is required; detected {core}",
        )
    detected: dict[str, Any] = {"core_version": core}
    if not require_paths:
        return detected
    if request.dcc_path is None:
        raise LifecycleFailure(
            EXIT_PREFLIGHT,
            "host",
            "ZBrush installation was not provided",
            next_steps=[
                _next_step(
                    "set-dcc-path",
                    "Provide the ZBrush executable or application path.",
                    "The installer must prove the host version before writing shared startup files.",
                    command="dcc-mcp-zbrush install --dcc-path <ZBrush-path> --dry-run --json",
                )
            ],
        )
    dcc_path = request.dcc_path.expanduser().resolve()
    if not dcc_path.exists():
        raise LifecycleFailure(EXIT_PREFLIGHT, "host", f"ZBrush path does not exist: {dcc_path}")
    host = _host_version(dcc_path)
    if host is None:
        raise LifecycleFailure(
            EXIT_PREFLIGHT,
            "host",
            "Could not prove the ZBrush version from --dcc-path; use a versioned ZBrush 2026.1+ path",
        )
    if host < MINIMUM_ZBRUSH:
        raise LifecycleFailure(EXIT_PREFLIGHT, "host", f"ZBrush 2026.1+ is required; detected {host[0]}.{host[1]}")
    if request.asset_dir is None:
        raise LifecycleFailure(
            EXIT_PREFLIGHT,
            "asset_dir",
            "ZBrush Asset Directory was not provided",
            next_steps=[
                _next_step(
                    "set-asset-dir",
                    "Set ZBRUSH_USER_ASSETS_DIR to the ZBrush Asset Directory.",
                    "The adapter never guesses a shared host startup directory.",
                    command="set ZBRUSH_USER_ASSETS_DIR=<ZBrush-Asset-Directory>",
                )
            ],
        )
    asset_dir = _validate_root(request.asset_dir, "Asset Directory")
    python_path = request.python_path or Path(sys.executable)
    resolved_python = _resolve_python(python_path)
    detected.update(
        {
            "zbrush_version": f"{host[0]}.{host[1]}",
            "dcc_path": str(dcc_path),
            "asset_dir": str(asset_dir),
            "python": str(resolved_python),
            "python_version": _python_probe(python_path),
            "platform_supported": sys.platform in {"win32", "darwin"},
        }
    )
    if sys.platform not in {"win32", "darwin"} and not request.dry_run:
        raise LifecycleFailure(
            EXIT_PREFLIGHT,
            "platform",
            "ZBrush host installation is supported on Windows and macOS; Linux is plan-only",
        )
    return detected


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".stage", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _receipt_relative(asset_dir: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not _safe_member(value):
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", f"Receipt contains an unsafe {label}")
    relative = PurePosixPath(value)
    raw_destination = asset_dir.joinpath(*relative.parts)
    is_junction = getattr(raw_destination, "is_junction", lambda: False)
    if raw_destination.is_symlink() or is_junction():
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", f"Receipt {label} points to a linked path")
    destination = raw_destination.resolve()
    if not destination.is_relative_to(asset_dir.resolve()):
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", f"Receipt {label} escapes the Asset Directory")
    return destination


def _validate_receipt(asset_dir: Path, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Installation receipt must be a JSON object")
    if data.get("adapter") != ADAPTER or data.get("schema_version") != SCHEMA_VERSION:
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Installation receipt has an unsupported schema or owner")
    mode = data.get("mode")
    if mode not in {"embedded", "sidecar"}:
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Installation receipt has an unsupported mode")
    backup_root = _receipt_relative(asset_dir, data.get("backup_root"), "backup_root")
    expected_backup_parent = (asset_dir / ".dcc-mcp" / "backups").resolve()
    if not backup_root.is_relative_to(expected_backup_parent):
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt backup_root is outside the managed backup area")
    managed_files = data.get("managed_files")
    managed_trees = data.get("managed_trees")
    if not isinstance(managed_files, list) or not isinstance(managed_trees, list):
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt managed file collections must be arrays")
    records: list[Mapping[str, Any]] = list(managed_files) + list(managed_trees)
    seen: set[Path] = set()
    for record in records:
        if not isinstance(record, dict):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt managed entries must be objects")
        managed_path = _receipt_relative(asset_dir, record.get("path"), "managed path")
        if managed_path in seen:
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt contains duplicate managed paths")
        seen.add(managed_path)
        if record.get("existed"):
            backup = _receipt_relative(asset_dir, record.get("backup"), "backup path")
            if not backup.is_relative_to(expected_backup_parent):
                raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt backup is outside the managed backup area")
        installed_sha256 = record.get("installed_sha256")
        if record in managed_files and not re.fullmatch(r"[0-9a-f]{64}", str(installed_sha256 or "")):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt contains an invalid managed file digest")
    shared = data.get("shared_init")
    if mode == "sidecar":
        if not isinstance(shared, dict):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Sidecar receipt has no shared init.py record")
        shared_path = _receipt_relative(asset_dir, shared.get("path"), "shared init path")
        if shared_path != (asset_dir / SHARED_INIT_RELATIVE).resolve():
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Sidecar receipt targets an unexpected shared init.py")
        if shared.get("existed"):
            backup = _receipt_relative(asset_dir, shared.get("backup"), "shared init backup")
            if not backup.is_relative_to(expected_backup_parent):
                raise LifecycleFailure(
                    EXIT_PREFLIGHT, "receipt", "Shared init.py backup is outside the managed backup area"
                )
        if not re.fullmatch(r"[0-9a-f]{64}", str(shared.get("installed_sha256") or "")):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Shared init.py receipt digest is invalid")
    elif shared is not None:
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Embedded receipt must not own the shared init.py")
    return data


def _read_receipt(asset_dir: Path) -> Optional[dict[str, Any]]:
    path = asset_dir / RECEIPT_RELATIVE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", f"Installation receipt is unreadable: {exc}") from exc
    return _validate_receipt(asset_dir, data)


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def _open_verified_archive(path: Path, expected_sha256: str) -> zipfile.ZipFile:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise LifecycleFailure(
            EXIT_ACQUIRE, "integrity", "A valid SHA-256 digest is required before opening the payload"
        )
    if not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise LifecycleFailure(EXIT_ACQUIRE, "integrity", "Plugin archive is missing or exceeds the size limit")
    actual = _sha256_file(path)
    if actual.lower() != expected_sha256.lower():
        raise LifecycleFailure(
            EXIT_ACQUIRE,
            "integrity",
            f"Plugin archive checksum mismatch: expected {expected_sha256.lower()}, got {actual}",
        )
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise LifecycleFailure(EXIT_ACQUIRE, "integrity", "Plugin archive is not a valid ZIP") from exc
    names: set[str] = set()
    total = 0
    for info in archive.infolist():
        if not _safe_member(info.filename) or info.filename in names:
            archive.close()
            raise LifecycleFailure(EXIT_ACQUIRE, "integrity", f"Unsafe or duplicate archive member: {info.filename}")
        if info.is_dir():
            continue
        names.add(info.filename)
        total += info.file_size
        if info.file_size > MAX_MEMBER_BYTES or total > MAX_ARCHIVE_BYTES:
            archive.close()
            raise LifecycleFailure(EXIT_ACQUIRE, "integrity", "Plugin archive exceeds extracted size limits")
    return archive


def _github_payload(version: str, cache_root: Optional[Path] = None) -> tuple[Path, str]:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[A-Za-z0-9.-]+)?", version):
        raise LifecycleFailure(EXIT_PREFLIGHT, "version", f"Invalid fixed release version: {version}")
    asset_name = f"dcc-mcp-zbrush-plugin-{version}.zip"
    api_url = f"https://api.github.com/repos/{REPOSITORY}/releases/tags/v{version}"
    request = Request(api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "dcc-mcp-zbrush"})
    try:
        with urlopen(request, timeout=20) as response:
            release = json.loads(response.read(MAX_ARCHIVE_BYTES))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LifecycleFailure(
            EXIT_ACQUIRE, "download", f"Could not resolve immutable release v{version}: {exc}"
        ) from exc
    matches = [asset for asset in release.get("assets", []) if asset.get("name") == asset_name]
    if len(matches) != 1:
        raise LifecycleFailure(
            EXIT_ACQUIRE, "download", f"Release v{version} does not contain exactly one {asset_name}"
        )
    asset = matches[0]
    digest_field = str(asset.get("digest") or "")
    if not digest_field.startswith("sha256:") or not re.fullmatch(r"[0-9a-fA-F]{64}", digest_field[7:]):
        raise LifecycleFailure(EXIT_ACQUIRE, "integrity", "Release asset has no immutable SHA-256 provenance")
    expected = digest_field[7:].lower()
    download_url = str(asset.get("browser_download_url") or "")
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/v{version}/{asset_name}"
    if download_url != expected_url:
        raise LifecycleFailure(
            EXIT_ACQUIRE, "integrity", "Release asset URL does not match the fixed official repository path"
        )
    root = _validate_root(cache_root or _default_cache_root(), "cache")
    cached = root / version / asset_name
    if cached.is_file() and _sha256_file(cached) == expected:
        return cached, expected
    if cached.exists():
        cached.unlink()
    cached.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{asset_name}.", suffix=".download", dir=cached.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        download_request = Request(download_url, headers={"User-Agent": "dcc-mcp-zbrush"})
        with urlopen(download_request, timeout=30) as response, temporary.open("wb") as stream:
            remaining = MAX_ARCHIVE_BYTES + 1
            while remaining > 0:
                chunk = response.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                stream.write(chunk)
                remaining -= len(chunk)
        if temporary.stat().st_size > MAX_ARCHIVE_BYTES or _sha256_file(temporary) != expected:
            raise LifecycleFailure(EXIT_ACQUIRE, "integrity", "Downloaded payload failed size or SHA-256 verification")
        os.replace(temporary, cached)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return cached, expected


def _default_cache_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "dcc-mcp" / "zbrush"


def _prune_cache(root: Path, keep_version: str) -> None:
    resolved = _validate_root(root, "cache")
    if not resolved.exists():
        return
    for child in resolved.iterdir():
        if child.is_dir() and child.name != keep_version:
            try:
                shutil.rmtree(child)
            except OSError:
                # Cache retirement is maintenance, not part of the host-file transaction.
                continue


def _lock_preflight(asset_dir: Path) -> None:
    if not asset_dir.exists():
        return
    try:
        from dcc_mcp_core.install_lifecycle import inspect_install_root

        inspection = inspect_install_root(asset_dir)
    except (ImportError, OSError) as exc:
        raise LifecycleFailure(EXIT_INSTALL, "lock_check", f"Could not inspect the ZBrush install root: {exc}") from exc
    if inspection.get("requires_restart"):
        raise LifecycleFailure(
            EXIT_REQUIRES_RESTART,
            "lock_check",
            "Loaded native files prevent a safe replacement; close ZBrush and retry",
            next_steps=[
                _next_step(
                    "close-zbrush",
                    "Close ZBrush and retry the lifecycle command.",
                    "Windows cannot safely replace loaded files.",
                    command="dcc-mcp-zbrush install --yes --json",
                )
            ],
        )


def _backup_file(path: Path, asset_dir: Path, backup_root: Path, relative: Path) -> dict[str, Any]:
    is_junction = getattr(path, "is_junction", lambda: False)
    if path.is_symlink() or is_junction():
        raise LifecycleFailure(EXIT_PREFLIGHT, "preflight", f"Refusing linked managed path: {relative.as_posix()}")
    record: dict[str, Any] = {"path": relative.as_posix(), "existed": path.is_file()}
    if path.is_file():
        data = path.read_bytes()
        backup = backup_root / relative
        _atomic_write(backup, data)
        record.update(
            {
                "backup": backup.relative_to(asset_dir).as_posix(),
                "original_sha256": _sha256_bytes(data),
            }
        )
    return record


def _restore_file(asset_dir: Path, record: Mapping[str, Any]) -> None:
    destination = asset_dir / str(record["path"])
    if record.get("existed"):
        backup = asset_dir / str(record["backup"])
        _atomic_write(destination, backup.read_bytes())
    else:
        destination.unlink(missing_ok=True)


def _manifest(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): _sha256_file(path) for path in sorted(root.rglob("*")) if path.is_file()}


def _stage_replace_tree(source: Path, destination: Path, backup: Optional[Path]) -> None:
    is_junction = getattr(destination, "is_junction", lambda: False)
    if destination.is_symlink() or is_junction():
        raise LifecycleFailure(EXIT_PREFLIGHT, "preflight", f"Refusing linked managed directory: {destination.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.parent / f".{destination.name}.stage-{uuid.uuid4().hex}"
    displaced = destination.parent / f".{destination.name}.old-{uuid.uuid4().hex}"
    shutil.copytree(source, stage)
    try:
        if destination.exists():
            if backup is not None:
                shutil.copytree(destination, backup)
            os.replace(destination, displaced)
        os.replace(stage, destination)
        if displaced.exists():
            shutil.rmtree(displaced)
    except BaseException:
        if not destination.exists() and displaced.exists():
            os.replace(displaced, destination)
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _remove_managed_tree(destination: Path) -> None:
    if not destination.exists():
        return
    displaced = destination.parent / f".{destination.name}.remove-{uuid.uuid4().hex}"
    os.replace(destination, displaced)
    shutil.rmtree(displaced)


def _install(
    request: LifecycleRequest,
    detected: Mapping[str, Any],
    *,
    plugin_archive: Optional[Path],
    expected_sha256: Optional[str],
    cache_root: Optional[Path],
) -> dict[str, Any]:
    assert request.asset_dir is not None
    asset_dir = _validate_root(request.asset_dir, "Asset Directory")
    receipt_path = asset_dir / RECEIPT_RELATIVE
    existing = _read_receipt(asset_dir) if receipt_path.exists() else None
    state, state_reason = _installation_state(asset_dir, existing)
    if (
        existing
        and state == "installed"
        and existing.get("version") == request.version
        and existing.get("mode") == request.mode
    ):
        return _result(
            request,
            status="installed",
            exit_code=EXIT_OK,
            stage="complete",
            reason="Requested version is already installed and receipt integrity is valid",
            receipt=receipt_path,
            detected=detected,
            next_steps=_verification_steps(request),
        )
    if existing and request.operation != "upgrade":
        raise LifecycleFailure(
            EXIT_PREFLIGHT,
            "existing_install",
            "A different managed installation exists; use upgrade",
            next_steps=[
                _next_step(
                    "upgrade",
                    "Upgrade the existing receipt-managed installation.",
                    "Upgrade preserves the original shared startup backup.",
                    command="dcc-mcp-zbrush upgrade --yes --json",
                )
            ],
        )
    if state == "partial" and not existing:
        raise LifecycleFailure(EXIT_PREFLIGHT, "partial_install", state_reason)
    if request.dry_run or not request.yes:
        next_steps = []
        if not request.yes:
            next_steps.append(
                _next_step(
                    "confirm-install",
                    "Apply the reviewed installation plan.",
                    "Mutation requires explicit confirmation.",
                    command=f"dcc-mcp-zbrush {request.operation} --yes --json",
                )
            )
        return _result(
            request,
            status="planned",
            exit_code=EXIT_OK,
            stage="plan",
            reason="Preflight passed; no files were changed",
            detected=detected,
            next_steps=next_steps,
        )
    _lock_preflight(asset_dir)
    archive_path: Path
    digest: str
    if plugin_archive is not None:
        if expected_sha256 is None:
            raise LifecycleFailure(EXIT_PREFLIGHT, "integrity", "Local plugin archives require an expected SHA-256")
        archive_path, digest = plugin_archive.resolve(), expected_sha256.lower()
    else:
        archive_path, digest = _github_payload(request.version, cache_root)
    archive = _open_verified_archive(archive_path, digest)
    transaction = uuid.uuid4().hex
    backup_root = asset_dir / ".dcc-mcp" / "backups" / transaction
    created: list[Path] = []
    records: list[dict[str, Any]] = []
    tree_records: list[dict[str, Any]] = []
    rollback_files: dict[Path, bytes] = {}
    rollback_trees: dict[Path, Path] = {}
    staging_roots: list[Path] = []
    shared_record: dict[str, Any] = {}
    old_backup_root: Optional[Path] = None
    try:
        asset_dir.mkdir(parents=True, exist_ok=True)
        if existing:
            old_mode = str(existing.get("mode"))
            if old_mode != request.mode:
                raise LifecycleFailure(
                    EXIT_PREFLIGHT,
                    "upgrade",
                    "Changing embedded/sidecar mode requires uninstall followed by install",
                )
            shared_record = dict(existing["shared_init"]) if request.mode == "sidecar" else {}
            old_backup = existing.get("backup_root")
            old_backup_root = asset_dir / str(old_backup) if old_backup else None
        elif request.mode == "sidecar":
            shared_path = asset_dir / SHARED_INIT_RELATIVE
            shared_record = _backup_file(shared_path, asset_dir, backup_root, SHARED_INIT_RELATIVE)
        else:
            shared_record = {}

        if request.mode == "sidecar":
            member = "sidecar/mcp_socket_bridge.py"
            if member not in archive.namelist():
                raise LifecycleFailure(EXIT_ACQUIRE, "integrity", f"Plugin archive is missing {member}")
            bridge_bytes = archive.read(member)
            bridge_path = asset_dir / SIDECAR_RELATIVE
            prior_record = None
            if existing:
                prior_record = next(
                    (
                        item
                        for item in existing.get("managed_files", [])
                        if item.get("path") == SIDECAR_RELATIVE.as_posix()
                    ),
                    None,
                )
            record = (
                dict(prior_record)
                if prior_record
                else _backup_file(bridge_path, asset_dir, backup_root, SIDECAR_RELATIVE)
            )
            if existing:
                rollback_files[bridge_path] = bridge_path.read_bytes()
            _atomic_write(bridge_path, bridge_bytes)
            record["installed_sha256"] = _sha256_bytes(bridge_bytes)
            records.append(record)
            created.append(bridge_path)

            shared_path = asset_dir / SHARED_INIT_RELATIVE
            current = shared_path.read_bytes() if shared_path.exists() else b""
            if existing and _sha256_bytes(current) != shared_record.get("installed_sha256"):
                shared_record["preserve_edits"] = True
            count = current.count(MANAGED_START)
            if count > 1 or (count == 1 and MANAGED_END not in current):
                raise LifecycleFailure(
                    EXIT_PREFLIGHT, "shared_init", "Shared init.py contains an ambiguous managed marker"
                )
            if count == 0:
                separator = b"" if not current or current.endswith(b"\n") else b"\n"
                current = current + separator + MANAGED_BLOCK
                shared_record["inserted_separator"] = bool(separator)
                _atomic_write(shared_path, current)
                created.append(shared_path)
            elif existing is None:
                raise LifecycleFailure(
                    EXIT_PREFLIGHT, "partial_install", "Managed init.py marker exists without a receipt"
                )
            shared_record["installed_sha256"] = _sha256_bytes(current)
            shared_record["managed_block_sha256"] = _sha256_bytes(MANAGED_BLOCK)
        else:
            required = "embedded/dcc_mcp_zbrush_plugin.py"
            embedded_members = [name for name in archive.namelist() if name.startswith("embedded/dcc_mcp_zbrush/")]
            if required not in archive.namelist() or not embedded_members:
                raise LifecycleFailure(EXIT_ACQUIRE, "integrity", "Plugin archive is missing embedded payload files")
            staging_root = asset_dir / ".dcc-mcp" / "staging" / transaction
            staging_roots.append(staging_root)
            package_source = staging_root / "dcc_mcp_zbrush"
            package_source.mkdir(parents=True, exist_ok=True)
            for name in embedded_members:
                if name.endswith("/"):
                    continue
                relative = PurePosixPath(name).relative_to("embedded/dcc_mcp_zbrush")
                target = package_source.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
            destination = asset_dir / "dcc_mcp_zbrush"
            prior_tree = existing.get("managed_trees", [None])[0] if existing else None
            tree_record = (
                dict(prior_tree)
                if prior_tree
                else {
                    "path": "dcc_mcp_zbrush",
                    "existed": destination.exists(),
                }
            )
            persistent_backup = None
            if destination.exists() and not prior_tree:
                persistent_backup = backup_root / "dcc_mcp_zbrush"
                tree_record["backup"] = persistent_backup.relative_to(asset_dir).as_posix()
            if existing:
                rollback_source = staging_root / "rollback_dcc_mcp_zbrush"
                shutil.copytree(destination, rollback_source)
                rollback_trees[destination] = rollback_source
            _stage_replace_tree(package_source, destination, persistent_backup)
            tree_record["installed_manifest"] = _manifest(destination)
            tree_records.append(tree_record)
            plugin_path = asset_dir / "dcc_mcp_zbrush_plugin.py"
            prior_record = None
            if existing:
                prior_record = next(
                    (
                        item
                        for item in existing.get("managed_files", [])
                        if item.get("path") == "dcc_mcp_zbrush_plugin.py"
                    ),
                    None,
                )
            record = (
                dict(prior_record)
                if prior_record
                else _backup_file(plugin_path, asset_dir, backup_root, Path("dcc_mcp_zbrush_plugin.py"))
            )
            plugin_bytes = archive.read(required)
            if existing:
                rollback_files[plugin_path] = plugin_path.read_bytes()
            _atomic_write(plugin_path, plugin_bytes)
            record["installed_sha256"] = _sha256_bytes(plugin_bytes)
            records.append(record)

        error_path = asset_dir / BOOTSTRAP_ERRORS_RELATIVE
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "adapter": ADAPTER,
            "version": request.version,
            "mode": request.mode,
            "installed_at": _utc_now(),
            "payload_sha256": digest,
            "backup_root": (old_backup_root or backup_root).relative_to(asset_dir).as_posix(),
            "shared_init": shared_record or None,
            "managed_files": records,
            "managed_trees": tree_records,
            "bootstrap_error_offset": error_path.stat().st_size if error_path.exists() else 0,
            "host_version": detected.get("zbrush_version"),
            "python_version": detected.get("python_version"),
        }
        _write_json(receipt_path, receipt)
        for staging_root in staging_roots:
            shutil.rmtree(staging_root, ignore_errors=True)
        if plugin_archive is None:
            _prune_cache((cache_root or _default_cache_root()), request.version)
        return _result(
            request,
            status="installed",
            exit_code=EXIT_OK,
            stage="complete",
            reason="Plugin installed with a receipt and recoverable shared startup backup",
            changed=True,
            receipt=receipt_path,
            detected=detected,
            next_steps=_verification_steps(request),
        )
    except BaseException:
        if existing:
            for path, data in rollback_files.items():
                _atomic_write(path, data)
            for destination, rollback_source in rollback_trees.items():
                if rollback_source.exists():
                    _stage_replace_tree(rollback_source, destination, None)
        else:
            for record in reversed(records):
                _restore_file(asset_dir, record)
            for record in reversed(tree_records):
                destination = asset_dir / str(record["path"])
                if record.get("existed"):
                    backup = asset_dir / str(record["backup"])
                    _stage_replace_tree(backup, destination, None)
                else:
                    _remove_managed_tree(destination)
            if shared_record:
                _restore_file(asset_dir, shared_record)
            for path in created:
                if path.is_file() and path not in {asset_dir / SHARED_INIT_RELATIVE}:
                    path.unlink(missing_ok=True)
            if backup_root.exists():
                shutil.rmtree(backup_root, ignore_errors=True)
        for staging_root in staging_roots:
            shutil.rmtree(staging_root, ignore_errors=True)
        raise
    finally:
        archive.close()


def _verification_steps(request: LifecycleRequest) -> list[dict[str, str]]:
    if request.mode == "sidecar":
        return [
            _next_step(
                "restart-zbrush",
                "Restart ZBrush, then verify the in-process socket bridge.",
                "ZBrush loads Python/init.py during host startup.",
                command="dcc-mcp-zbrush verify --json",
            ),
            _next_step(
                "start-sidecar",
                "Start the external MCP sidecar after verify succeeds.",
                "The socket bridge and MCP sidecar are separate readiness stages.",
                command="dcc-mcp-zbrush --mode sidecar",
            ),
        ]
    return [
        _next_step(
            "restart-zbrush",
            "Restart ZBrush and verify the embedded MCP registration.",
            "Embedded startup is completed on ZBrush's main thread.",
            command="dcc-mcp-zbrush verify --mode embedded --json",
        )
    ]


def _installation_state(asset_dir: Path, receipt: Optional[Mapping[str, Any]]) -> tuple[str, str]:
    marker = (asset_dir / SHARED_INIT_RELATIVE).read_bytes() if (asset_dir / SHARED_INIT_RELATIVE).is_file() else b""
    managed_candidates = [
        asset_dir / SIDECAR_RELATIVE,
        asset_dir / "dcc_mcp_zbrush_plugin.py",
        asset_dir / "dcc_mcp_zbrush",
    ]
    if receipt is None:
        if MANAGED_START in marker or any(path.exists() for path in managed_candidates):
            return "partial", "Managed plugin files exist without a receipt; automatic mutation is blocked"
        return "not_installed", "No managed installation was found"
    if receipt.get("mode") == "sidecar" and marker.count(MANAGED_START) != 1:
        return "partial", "Receipt exists but the shared init.py marker is missing or ambiguous"
    for record in receipt.get("managed_files", []):
        path = asset_dir / str(record.get("path", ""))
        if not path.is_file() or _sha256_file(path) != record.get("installed_sha256"):
            return "partial", f"Managed file drifted: {record.get('path')}"
    for record in receipt.get("managed_trees", []):
        path = asset_dir / str(record.get("path", ""))
        if not path.is_dir() or _manifest(path) != record.get("installed_manifest"):
            return "partial", f"Managed directory drifted: {record.get('path')}"
    return "installed", "Receipt and managed payload hashes are valid"


def _status(request: LifecycleRequest, detected: Mapping[str, Any]) -> dict[str, Any]:
    if request.asset_dir is None:
        raise LifecycleFailure(EXIT_PREFLIGHT, "asset_dir", "ZBrush Asset Directory was not provided")
    asset_dir = _validate_root(request.asset_dir, "Asset Directory")
    receipt = _read_receipt(asset_dir)
    state, reason = _installation_state(asset_dir, receipt)
    code = EXIT_OK if state in {"installed", "not_installed"} else EXIT_PREFLIGHT
    return _result(
        request,
        status=state,
        exit_code=code,
        stage="status",
        reason=reason,
        receipt=asset_dir / RECEIPT_RELATIVE if receipt else None,
        detected=detected,
        next_steps=_verification_steps(request) if state == "installed" else [],
    )


def _bootstrap_failures(asset_dir: Path, receipt: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = asset_dir / BOOTSTRAP_ERRORS_RELATIVE
    if not path.is_file():
        return []
    offset = int(receipt.get("bootstrap_error_offset", 0))
    with path.open("rb") as stream:
        stream.seek(min(offset, path.stat().st_size))
        lines = stream.read().decode("utf-8", errors="replace").splitlines()
    failures = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            item = {"stage": "bootstrap", "reason": "Malformed bootstrap error record"}
        failures.append(item)
    return failures


def _verify(request: LifecycleRequest, detected: Mapping[str, Any]) -> dict[str, Any]:
    if request.asset_dir is None:
        raise LifecycleFailure(EXIT_PREFLIGHT, "asset_dir", "ZBrush Asset Directory was not provided")
    asset_dir = _validate_root(request.asset_dir, "Asset Directory")
    receipt = _read_receipt(asset_dir)
    state, reason = _installation_state(asset_dir, receipt)
    if state != "installed" or receipt is None:
        return _result(
            request,
            status=state,
            exit_code=EXIT_VERIFY,
            stage="integrity",
            reason=reason,
            receipt=asset_dir / RECEIPT_RELATIVE if receipt else None,
            detected=detected,
        )
    failures = _bootstrap_failures(asset_dir, receipt)
    if failures:
        latest = failures[-1]
        return _result(
            request,
            status="bootstrap_failed",
            exit_code=EXIT_VERIFY,
            stage=str(latest.get("stage", "bootstrap")),
            reason=str(latest.get("reason", "ZBrush bootstrap failed")),
            receipt=asset_dir / RECEIPT_RELATIVE,
            detected={**detected, "bootstrap_errors": failures},
            next_steps=[
                _next_step(
                    "inspect-bootstrap-errors",
                    "Inspect the captured ZBrush bootstrap errors.",
                    "The host reported a startup failure after installation.",
                    command="dcc-mcp-zbrush status --json",
                )
            ],
        )
    if request.mode == "sidecar":
        try:
            from dcc_mcp_zbrush.bridge import SocketBridge

            bridge = SocketBridge(request.socket_host, request.socket_port, timeout=2.0)
            bridge.connect()
            session = bridge.get_session_info()
            bridge.disconnect()
        except Exception as exc:
            return _result(
                request,
                status="host_unavailable",
                exit_code=EXIT_VERIFY,
                stage="host_readiness",
                reason=f"Installed payload is valid but the ZBrush socket bridge is unavailable: {exc}",
                receipt=asset_dir / RECEIPT_RELATIVE,
                detected=detected,
                next_steps=_verification_steps(request),
            )
        return _result(
            request,
            status="usable",
            exit_code=EXIT_OK,
            stage="host_readiness",
            reason="Receipt integrity, ZBrush socket ping, and session probe succeeded",
            directly_usable=True,
            receipt=asset_dir / RECEIPT_RELATIVE,
            detected={**detected, "session": session},
            next_steps=[
                _next_step(
                    "start-sidecar",
                    "Start the external MCP sidecar.",
                    "The host socket bridge is ready for the external MCP process.",
                    command="dcc-mcp-zbrush --mode sidecar",
                )
            ],
        )
    try:
        from dcc_mcp_core.install_lifecycle import wait_for_sidecar_ready

        ready = wait_for_sidecar_ready(
            dcc_type="zbrush",
            timeout_secs=2.0,
            probe_tool="zbrush_scripting__get_session_info",
            probe_timeout_secs=2.0,
        )
    except (ImportError, OSError) as exc:
        ready = {"success": False, "message": str(exc)}
    if not ready.get("success"):
        return _result(
            request,
            status="host_unavailable",
            exit_code=EXIT_VERIFY,
            stage="host_readiness",
            reason=str(ready.get("message") or "Embedded ZBrush registration is not ready"),
            receipt=asset_dir / RECEIPT_RELATIVE,
            detected={**detected, "readiness": ready},
            next_steps=_verification_steps(request),
        )
    return _result(
        request,
        status="usable",
        exit_code=EXIT_OK,
        stage="host_readiness",
        reason="Embedded ZBrush session probe succeeded",
        directly_usable=True,
        receipt=asset_dir / RECEIPT_RELATIVE,
        detected={**detected, "readiness": ready},
    )


def _uninstall(request: LifecycleRequest, detected: Mapping[str, Any]) -> dict[str, Any]:
    if request.asset_dir is None:
        raise LifecycleFailure(EXIT_PREFLIGHT, "asset_dir", "ZBrush Asset Directory was not provided")
    asset_dir = _validate_root(request.asset_dir, "Asset Directory")
    receipt_path = asset_dir / RECEIPT_RELATIVE
    receipt = _read_receipt(asset_dir)
    state, reason = _installation_state(asset_dir, receipt)
    if receipt is None:
        if state == "partial":
            raise LifecycleFailure(EXIT_PREFLIGHT, "partial_install", reason)
        return _result(
            request,
            status="not_installed",
            exit_code=EXIT_OK,
            stage="complete",
            reason="No receipt-managed installation was found",
            detected=detected,
        )
    if request.dry_run or not request.yes:
        return _result(
            request,
            status="planned",
            exit_code=EXIT_OK,
            stage="plan",
            reason="Receipt-driven uninstall plan is valid; no files were changed",
            receipt=receipt_path,
            detected=detected,
            next_steps=[
                _next_step(
                    "confirm-uninstall",
                    "Apply the reviewed uninstall plan.",
                    "Mutation requires explicit confirmation.",
                    command="dcc-mcp-zbrush uninstall --yes --json",
                )
            ],
        )
    _lock_preflight(asset_dir)
    for record in receipt.get("managed_files", []):
        path = asset_dir / str(record["path"])
        if path.is_file() and _sha256_file(path) != record.get("installed_sha256"):
            raise LifecycleFailure(
                EXIT_INSTALL, "uninstall", f"Managed file changed after install; refusing to remove {record['path']}"
            )
    for record in receipt.get("managed_trees", []):
        path = asset_dir / str(record["path"])
        if path.is_dir() and _manifest(path) != record.get("installed_manifest"):
            raise LifecycleFailure(
                EXIT_INSTALL,
                "uninstall",
                f"Managed directory changed after install; refusing to remove {record['path']}",
            )
    shared_record = receipt.get("shared_init")
    shared_path: Optional[Path] = None
    current = b""
    if receipt.get("mode") == "sidecar":
        if not isinstance(shared_record, dict):
            raise LifecycleFailure(EXIT_INSTALL, "uninstall", "Sidecar receipt has no shared init.py backup")
        shared_path = asset_dir / str(shared_record["path"])
        current = shared_path.read_bytes() if shared_path.is_file() else b""
        if current.count(MANAGED_START) != 1 or current.count(MANAGED_END) != 1:
            raise LifecycleFailure(EXIT_INSTALL, "uninstall", "Shared init.py managed block is missing or ambiguous")
    for record in receipt.get("managed_files", []):
        _restore_file(asset_dir, record)
    for record in receipt.get("managed_trees", []):
        destination = asset_dir / str(record["path"])
        if record.get("existed"):
            backup = asset_dir / str(record["backup"])
            _stage_replace_tree(backup, destination, None)
        else:
            _remove_managed_tree(destination)
    if isinstance(shared_record, dict) and shared_path is not None:
        if _sha256_bytes(current) == shared_record.get("installed_sha256") and not shared_record.get("preserve_edits"):
            _restore_file(asset_dir, shared_record)
        else:
            updated = current.replace(MANAGED_BLOCK, b"", 1)
            _atomic_write(shared_path, updated)
    receipt_path.unlink()
    backup_root = asset_dir / str(receipt.get("backup_root", ""))
    if backup_root.exists():
        shutil.rmtree(backup_root)
    return _result(
        request,
        status="uninstalled",
        exit_code=EXIT_OK,
        stage="complete",
        reason="Receipt-managed files were removed and previous shared startup state was restored",
        changed=True,
        detected=detected,
    )


def run_lifecycle(
    request: LifecycleRequest,
    *,
    plugin_archive: Optional[Path] = None,
    expected_sha256: Optional[str] = None,
    cache_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Run one lifecycle operation and always return the stable JSON schema."""

    try:
        operation = request.operation.lower()
        if operation not in {"install", "status", "verify", "uninstall", "upgrade"}:
            raise LifecycleFailure(EXIT_PREFLIGHT, "operation", f"Unsupported lifecycle operation: {operation}")
        detected = _preflight(request, require_paths=True)
        if operation == "status":
            return _status(request, detected)
        if operation == "verify":
            return _verify(request, detected)
        if operation == "uninstall":
            return _uninstall(request, detected)
        return _install(
            request,
            detected,
            plugin_archive=plugin_archive,
            expected_sha256=expected_sha256,
            cache_root=cache_root,
        )
    except LifecycleFailure as exc:
        return _result(
            request,
            status="failed",
            exit_code=exc.exit_code,
            stage=exc.stage,
            reason=exc.reason,
            next_steps=exc.next_steps,
        )
    except PermissionError:
        return _result(
            request,
            status="failed",
            exit_code=EXIT_INSTALL,
            stage="locked_files",
            reason="A ZBrush file is locked; close ZBrush and retry",
            next_steps=[
                _next_step(
                    "close-zbrush",
                    "Close ZBrush and retry.",
                    "Windows cannot replace an open startup file safely.",
                    command=f"dcc-mcp-zbrush {request.operation} --yes --json",
                )
            ],
        )
    except (OSError, zipfile.BadZipFile) as exc:
        return _result(
            request,
            status="failed",
            exit_code=EXIT_INSTALL,
            stage="filesystem",
            reason=str(exc),
        )
