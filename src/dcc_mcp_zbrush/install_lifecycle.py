"""Secure, receipt-driven ZBrush adapter installation lifecycle.

The ZBrush Asset Directory is a shared host location.  This module therefore
owns the host-specific transaction while delegating native lock inspection to
``dcc-mcp-core``.  Core's tree helper currently removes a destination before
copying, which is not suitable for this shared location, so replacement here
is staged beside the destination and committed with a rename.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, distribution
from importlib.metadata import version as package_version
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, url2pathname, urlopen

from dcc_mcp_zbrush.__version__ import __version__

ADAPTER = "zbrush"
SCHEMA_VERSION = 1
MINIMUM_ZBRUSH = (2026, 1)
MINIMUM_CORE = (0, 20, 14)
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
IDENTITY_RELATIVE = Path("Python/dcc_mcp_zbrush_install_identity.json")
EMBEDDED_IDENTITY_RELATIVE = Path("dcc_mcp_zbrush_install_identity.json")
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
TRANSACTION_TARGETS = {
    RECEIPT_RELATIVE.as_posix(),
    SHARED_INIT_RELATIVE.as_posix(),
    SIDECAR_RELATIVE.as_posix(),
    IDENTITY_RELATIVE.as_posix(),
    EMBEDDED_IDENTITY_RELATIVE.as_posix(),
    "dcc_mcp_zbrush",
    "dcc_mcp_zbrush_plugin.py",
}


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
    def __init__(self, exit_code: int, stage: str, reason: str, *, next_steps: Optional[list[dict[str, Any]]] = None):
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


def _next_step(
    step_id: str,
    description: str,
    why: str,
    *,
    command: Optional[list[str]] = None,
    file_edit: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    result = {"id": step_id, "description": description, "why": why}
    if command:
        result["command"] = list(command)
    elif file_edit:
        result["file_edit"] = dict(file_edit)
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
    next_steps: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    canonical_status = {
        "planned": "planned",
        "partial": "partial",
        "requires_restart": "requires_restart",
    }.get(status, "failed" if exit_code != EXIT_OK else "ok")
    is_failure = canonical_status in {"failed", "partial"}
    core_version = str((detected or {}).get("core_version") or "unknown")
    adapter_version = request.version or __version__
    receipt_value = str(receipt) if receipt else None
    return {
        "schema_version": SCHEMA_VERSION,
        "dcc_type": ADAPTER,
        "adapter_version": adapter_version,
        "core_version": core_version,
        "steps": [{"id": stage or request.operation, "status": canonical_status, "message": reason}],
        "verify": {
            "directly_usable": directly_usable,
            "failure_stage": stage if is_failure else None,
            "failure_reason": reason if is_failure else None,
        },
        "adapter": ADAPTER,
        "operation": request.operation,
        "status": canonical_status,
        "state": status,
        "exit_code": exit_code,
        "stage": stage,
        "reason": reason,
        "changed": changed,
        "directly_usable": directly_usable,
        "mode": request.mode,
        "version": request.version,
        "receipt": receipt_value,
        "receipt_path": receipt_value,
        "detected": dict(detected or {}),
        "next_steps": list(next_steps or []),
    }


def _parse_version(value: str, *, stage: str, components: int = 3) -> tuple[int, ...]:
    """Parse one bounded canonical final release version."""

    if not isinstance(value, str) or len(value) > 64:
        raise LifecycleFailure(EXIT_PREFLIGHT, stage, f"{stage} version is not a bounded canonical release")
    component = r"(?:0|[1-9]\d{0,5})"
    pattern = rf"{component}(?:\.{component}){{{components - 1}}}"
    if re.fullmatch(pattern, value) is None:
        raise LifecycleFailure(EXIT_PREFLIGHT, stage, f"{stage} version is not a canonical final release: {value}")
    values = tuple(int(part) for part in value.split("."))
    if any(part > 999_999 for part in values):
        raise LifecycleFailure(EXIT_PREFLIGHT, stage, f"{stage} version component is out of range")
    return values


def _parse_host_version(value: str, *, stage: str = "host") -> tuple[int, ...]:
    if not isinstance(value, str) or len(value) > 64:
        raise LifecycleFailure(EXIT_PREFLIGHT, stage, "ZBrush product version is not bounded")
    component = r"(?:0|[1-9]\d{0,5})"
    if re.fullmatch(rf"{component}(?:\.{component}){{1,3}}", value) is None:
        raise LifecycleFailure(EXIT_PREFLIGHT, stage, f"ZBrush product version is not canonical: {value}")
    parts = tuple(int(part) for part in value.split("."))
    if any(part > 999_999 for part in parts):
        raise LifecycleFailure(EXIT_PREFLIGHT, stage, "ZBrush product version component is out of range")
    return parts


def _windows_product_info(path: Path) -> dict[str, str]:
    """Read Windows version-resource strings without trusting the path name."""

    import ctypes
    from ctypes import wintypes

    handle = wintypes.DWORD(0)
    size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), ctypes.byref(handle))
    if not size:
        raise LifecycleFailure(EXIT_PREFLIGHT, "host", "ZBrush executable has no Windows version resource")
    buffer = ctypes.create_string_buffer(size)
    if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buffer):
        raise LifecycleFailure(EXIT_PREFLIGHT, "host", "Could not read the ZBrush Windows version resource")

    pointer = ctypes.c_void_p()
    length = wintypes.UINT(0)
    language, codepage = 0x0409, 0x04B0
    if (
        ctypes.windll.version.VerQueryValueW(
            buffer, "\\VarFileInfo\\Translation", ctypes.byref(pointer), ctypes.byref(length)
        )
        and length.value >= 4
    ):
        translation = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ushort))
        language, codepage = int(translation[0]), int(translation[1])

    def query(name: str) -> str:
        block = f"\\StringFileInfo\\{language:04x}{codepage:04x}\\{name}"
        value_pointer = ctypes.c_void_p()
        value_length = wintypes.UINT(0)
        if not ctypes.windll.version.VerQueryValueW(
            buffer, block, ctypes.byref(value_pointer), ctypes.byref(value_length)
        ):
            return ""
        return ctypes.wstring_at(value_pointer, max(0, value_length.value - 1)).strip()

    return {
        "product_name": query("ProductName"),
        "version": query("ProductVersion") or query("FileVersion"),
    }


def _windows_authenticode_info(path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["DCC_MCP_ZBRUSH_SIGNATURE_TARGET"] = str(path)
    script = (
        "$s=Get-AuthenticodeSignature -LiteralPath $env:DCC_MCP_ZBRUSH_SIGNATURE_TARGET;"
        "@{status=[string]$s.Status;subject=[string]$s.SignerCertificate.Subject}|ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LifecycleFailure(EXIT_PREFLIGHT, "host", "Could not verify ZBrush Authenticode identity") from exc
    if completed.returncode != 0 or len(completed.stdout) > 4096:
        raise LifecycleFailure(EXIT_PREFLIGHT, "host", "ZBrush Authenticode probe failed")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise LifecycleFailure(EXIT_PREFLIGHT, "host", "ZBrush Authenticode probe returned invalid data") from exc
    return {"status": str(result.get("status") or ""), "subject": str(result.get("subject") or "")}


def _macos_signing_authority(path: Path) -> str:
    try:
        verified = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        details = subprocess.run(
            ["/usr/bin/codesign", "-dv", "--verbose=4", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LifecycleFailure(EXIT_PREFLIGHT, "host", "Could not verify ZBrush code-signing identity") from exc
    output = (details.stdout + "\n" + details.stderr)[: 64 * 1024]
    authority = next(
        (line.partition("=")[2].strip() for line in output.splitlines() if line.startswith("Authority=")),
        "",
    )
    if verified.returncode != 0 or details.returncode != 0:
        raise LifecycleFailure(EXIT_PREFLIGHT, "host", "ZBrush application signature is invalid")
    return authority


def _probe_zbrush_host(path: Path) -> dict[str, Any]:
    candidate = path.expanduser().resolve()
    if sys.platform == "win32":
        if not candidate.is_file() or candidate.name.casefold() != "zbrush.exe":
            raise LifecycleFailure(EXIT_PREFLIGHT, "host", "--dcc-path must be the ZBrush.exe executable")
        with candidate.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise LifecycleFailure(EXIT_PREFLIGHT, "host", "ZBrush.exe is not a Windows PE executable")
        product = _windows_product_info(candidate)
        product_name = str(product.get("product_name") or "").strip()
        if re.fullmatch(r"(?i)(?:maxon\s+)?zbrush", product_name) is None:
            raise LifecycleFailure(EXIT_PREFLIGHT, "host", "Executable product metadata is not Maxon ZBrush")
        version_text = str(product.get("version") or "").strip()
        signature = _windows_authenticode_info(candidate)
        signer = signature["subject"]
        if signature["status"] != "Valid" or "maxon" not in signer.casefold():
            raise LifecycleFailure(EXIT_PREFLIGHT, "host", "ZBrush.exe is not validly signed by Maxon")
        root = candidate.parent
    elif sys.platform == "darwin":
        bundle = (
            candidate
            if candidate.suffix.casefold() == ".app"
            else next((parent for parent in candidate.parents if parent.suffix.casefold() == ".app"), None)
        )
        if bundle is None or not bundle.is_dir():
            raise LifecycleFailure(EXIT_PREFLIGHT, "host", "--dcc-path must identify the ZBrush application bundle")
        info_path = bundle / "Contents" / "Info.plist"
        try:
            with info_path.open("rb") as stream:
                info = plistlib.load(stream)
        except (OSError, plistlib.InvalidFileException) as exc:
            raise LifecycleFailure(EXIT_PREFLIGHT, "host", "ZBrush application metadata is unreadable") from exc
        product_name = str(info.get("CFBundleName") or info.get("CFBundleDisplayName") or "").strip()
        if re.fullmatch(r"(?i)(?:maxon\s+)?zbrush", product_name) is None:
            raise LifecycleFailure(EXIT_PREFLIGHT, "host", "Application bundle metadata is not Maxon ZBrush")
        executable_name = str(info.get("CFBundleExecutable") or "").strip()
        candidate = (bundle / "Contents" / "MacOS" / executable_name).resolve()
        if not executable_name or not candidate.is_file():
            raise LifecycleFailure(EXIT_PREFLIGHT, "host", "ZBrush application executable is missing")
        with candidate.open("rb") as stream:
            if stream.read(4) not in {
                b"\xcf\xfa\xed\xfe",
                b"\xfe\xed\xfa\xcf",
                b"\xca\xfe\xba\xbe",
                b"\xbe\xba\xfe\xca",
            }:
                raise LifecycleFailure(EXIT_PREFLIGHT, "host", "ZBrush application is not a Mach-O executable")
        version_text = str(info.get("CFBundleShortVersionString") or "").strip()
        signer = _macos_signing_authority(bundle)
        if "maxon" not in signer.casefold():
            raise LifecycleFailure(EXIT_PREFLIGHT, "host", "ZBrush application is not signed by Maxon")
        root = bundle
    else:
        raise LifecycleFailure(EXIT_PREFLIGHT, "platform", "ZBrush is supported only on Windows and macOS")

    version = _parse_host_version(version_text)
    if version[:2] < MINIMUM_ZBRUSH:
        raise LifecycleFailure(EXIT_PREFLIGHT, "host", f"ZBrush 2026.1+ is required; detected {version_text}")
    return {
        "dcc_path": str(candidate),
        "dcc_root": str(root),
        "dcc_sha256": _sha256_file(candidate),
        "product_name": product_name,
        "product_signer": signer,
        "zbrush_version": version_text,
    }


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
    if _parse_version(detected, stage="python") < (3, 10, 0):
        raise LifecycleFailure(EXIT_PREFLIGHT, "python", f"Python 3.10+ is required; detected {detected}")
    return detected


def _distribution_module_provenance(distribution_name: str, module_name: str) -> dict[str, str]:
    """Bind an imported package to one RECORD or validated editable direct_url root."""

    try:
        installed = distribution(distribution_name)
        module = importlib.import_module(module_name)
    except (ImportError, PackageNotFoundError) as exc:
        raise LifecycleFailure(EXIT_PREFLIGHT, "python", f"Could not load {distribution_name} provenance") from exc
    origin_text = str(getattr(module, "__file__", "") or "")
    origin = Path(origin_text).resolve() if origin_text else None
    if origin is None or not origin.is_file():
        raise LifecycleFailure(EXIT_PREFLIGHT, "python", f"{module_name} has no concrete module origin")
    record_owned = any(
        Path(installed.locate_file(item)).resolve() == origin
        for item in (installed.files or [])
        if not str(item).endswith((".pyc", ".pyo"))
    )
    editable_root: Optional[Path] = None
    direct_url_text = installed.read_text("direct_url.json")
    if direct_url_text:
        try:
            direct_url = json.loads(direct_url_text)
            parsed = urlparse(str(direct_url.get("url") or ""))
            if direct_url.get("dir_info", {}).get("editable") is True and parsed.scheme == "file":
                editable_root = Path(url2pathname(unquote(parsed.path))).resolve()
        except (OSError, ValueError, TypeError):
            editable_root = None
    editable_candidates: set[Path] = set()
    if editable_root is not None:
        package_parts = module_name.split(".")
        for source_root in (editable_root, editable_root / "src", editable_root / "python"):
            editable_candidates.add(source_root.joinpath(*package_parts, "__init__.py").resolve())
    editable_owned = origin in editable_candidates
    if not record_owned and not editable_owned:
        raise LifecycleFailure(EXIT_PREFLIGHT, "python", f"{module_name} is not owned by its installed distribution")
    version = str(installed.version)
    _parse_version(version, stage="python")
    try:
        module_digest = _sha256_file(origin)
    except OSError as exc:
        raise LifecycleFailure(EXIT_PREFLIGHT, "python", f"Could not hash {module_name} provenance") from exc
    return {
        "distribution": distribution_name,
        "module": module_name,
        "module_path": str(origin),
        "module_sha256": module_digest,
        "version": version,
    }


def _resolve_python(path: Path) -> Path:
    candidate = path.expanduser()
    if candidate.is_file():
        return candidate.resolve()
    discovered = shutil.which(str(path))
    if discovered:
        return Path(discovered).resolve()
    raise LifecycleFailure(EXIT_PREFLIGHT, "python", f"Python interpreter does not exist: {path}")


def _process_executable(pid: int) -> Path:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "ZBrush readiness PID is invalid")
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Could not bind the reported ZBrush PID")
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not ctypes.windll.kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Could not resolve the reported ZBrush process")
            return Path(buffer.value).resolve()
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    try:
        completed = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "comm="],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Could not resolve the reported ZBrush process") from exc
    if completed.returncode != 0 or not completed.stdout.strip():
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Could not resolve the reported ZBrush process")
    return Path(completed.stdout.strip()).resolve()


def _process_start_identity(pid: int) -> str:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Could not bind the ZBrush process start time")
        try:
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetProcessTimes(
                process, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
            ):
                raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Could not read the ZBrush process start time")
            value = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
            return f"windows-filetime:{value}"
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    try:
        completed = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "lstart="],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Could not read the ZBrush process start time") from exc
    value = " ".join(completed.stdout.split())
    if completed.returncode != 0 or not value:
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Could not read the ZBrush process start time")
    return f"ps-lstart:{value}"


def _same_path(left: Path | str, right: Path | str) -> bool:
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


def _zbrush_module_provenance(receipt: Mapping[str, Any], origin_text: str) -> tuple[Path, str]:
    dcc_root = Path(str(receipt.get("dcc_root") or "")).resolve()
    origin = Path(origin_text).resolve()
    if (
        not origin.is_file()
        or not origin.is_relative_to(dcc_root)
        or origin.parent.name.casefold() != "zbrush"
        or origin.name.casefold() not in {"commands.pyd", "commands.so", "commands.dylib"}
    ):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "zbrush.commands is not a native selected-product module")
    try:
        digest = _sha256_file(origin)
    except OSError as exc:
        raise LifecycleFailure(
            EXIT_VERIFY, "host_identity", "Could not hash the selected zbrush.commands module"
        ) from exc
    return origin, digest


def _validate_sidecar_session(
    request: LifecycleRequest,
    asset_dir: Path,
    receipt: Mapping[str, Any],
    session: Mapping[str, Any],
) -> dict[str, Any]:
    if session.get("install_id") != receipt.get("install_id"):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Socket endpoint belongs to another install transaction")
    try:
        instance_id = str(uuid.UUID(str(session.get("bridge_instance_id") or "")))
    except (ValueError, AttributeError) as exc:
        raise LifecycleFailure(
            EXIT_VERIFY, "host_identity", "ZBrush bridge instance UUID is missing or invalid"
        ) from exc
    pid = session.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "ZBrush readiness PID is missing or invalid")
    endpoint = f"{request.socket_host}:{request.socket_port}"
    if session.get("socket_endpoint") != endpoint or receipt.get("socket_endpoint") != endpoint:
        raise LifecycleFailure(
            EXIT_VERIFY, "host_identity", "ZBrush socket endpoint does not match the selected endpoint"
        )
    if session.get("adapter_version") != receipt.get("version"):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Loaded ZBrush adapter version does not match the receipt")
    if session.get("selected_dcc_sha256") != receipt.get("dcc_sha256"):
        raise LifecycleFailure(
            EXIT_VERIFY, "host_identity", "Loaded ZBrush executable digest does not match the receipt"
        )
    if not _same_path(str(session.get("selected_dcc_path") or ""), str(receipt.get("dcc_path") or "")):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Loaded ZBrush executable path does not match the receipt")
    runtime_version = _parse_host_version(str(session.get("zbrush_version") or ""))
    receipt_version = _parse_host_version(str(receipt.get("host_version") or ""))
    if runtime_version[:2] != receipt_version[:2]:
        raise LifecycleFailure(
            EXIT_VERIFY, "host_identity", "Running ZBrush version does not match the selected product"
        )

    bridge_path = asset_dir / SIDECAR_RELATIVE
    if not _same_path(str(session.get("bridge_module_path") or ""), bridge_path):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Loaded bridge module path is not receipt-owned")
    bridge_record = next(
        (item for item in receipt.get("managed_files", []) if item.get("path") == SIDECAR_RELATIVE.as_posix()),
        None,
    )
    if not isinstance(bridge_record, dict) or session.get("bridge_module_sha256") != bridge_record.get(
        "installed_sha256"
    ):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Loaded bridge module bytes are not receipt-owned")
    zbrush_origin, zbrush_digest = _zbrush_module_provenance(receipt, str(session.get("zbrush_commands_origin") or ""))

    try:
        process_path = _process_executable(pid)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Could not bind the reported ZBrush process") from exc
    if not _same_path(process_path, str(receipt.get("dcc_path") or "")):
        raise LifecycleFailure(
            EXIT_VERIFY, "host_identity", "Reported PID does not belong to the selected ZBrush executable"
        )
    try:
        process_digest = _sha256_file(process_path)
    except OSError as exc:
        raise LifecycleFailure(
            EXIT_VERIFY, "host_identity", "Could not verify running ZBrush executable bytes"
        ) from exc
    if process_digest != receipt.get("dcc_sha256"):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Running ZBrush executable bytes changed after preflight")
    try:
        start_identity = _process_start_identity(pid)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Could not bind the ZBrush process start time") from exc
    identity = {
        "adapter_version": receipt.get("version"),
        "bridge_module_path": str(bridge_path.resolve()),
        "endpoint": endpoint,
        "instance_id": instance_id,
        "pid": pid,
        "process_start_identity": start_identity,
        "zbrush_commands_origin": str(zbrush_origin),
        "zbrush_commands_sha256": zbrush_digest,
        "zbrush_version": str(session.get("zbrush_version")),
    }
    prior_identity = receipt.get("runtime_identity")
    if prior_identity is not None:
        if not isinstance(prior_identity, dict) or any(
            prior_identity.get(key) != value for key, value in identity.items()
        ):
            raise LifecycleFailure(EXIT_VERIFY, "host_identity", "ZBrush runtime identity changed after verification")
    return identity


def _extract_probe_session(readiness: Mapping[str, Any]) -> Mapping[str, Any]:
    queue: list[tuple[Any, int]] = [(readiness.get("probe", {}).get("result"), 0)]
    seen = 0
    while queue:
        value, depth = queue.pop(0)
        seen += 1
        if seen > 64 or depth > 6:
            break
        if isinstance(value, dict):
            if {"install_id", "pid", "instance_id"}.issubset(value):
                return value
            for key in ("structuredContent", "result", "data", "context"):
                if key in value:
                    queue.append((value[key], depth + 1))
            content = value.get("content")
            if isinstance(content, list):
                queue.extend((item, depth + 1) for item in content[:8])
            text = value.get("text")
            if isinstance(text, str) and len(text) <= 64 * 1024:
                try:
                    queue.append((json.loads(text), depth + 1))
                except json.JSONDecodeError:
                    pass
        elif isinstance(value, list):
            queue.extend((item, depth + 1) for item in value[:8])
    raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Embedded probe returned no typed session identity")


def _validate_embedded_readiness(
    asset_dir: Path,
    receipt: Mapping[str, Any],
    readiness: Mapping[str, Any],
) -> dict[str, Any]:
    entry = readiness.get("entry")
    if not isinstance(entry, dict):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Embedded readiness has no selected registry entry")
    session = _extract_probe_session(readiness)
    try:
        instance_id = str(uuid.UUID(str(session.get("instance_id") or "")))
    except (ValueError, AttributeError) as exc:
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Embedded instance UUID is invalid") from exc
    if str(entry.get("instance_id") or "") != instance_id:
        raise LifecycleFailure(
            EXIT_VERIFY, "host_identity", "Probe instance does not match the selected registry entry"
        )
    pid = session.get("pid")
    entry_pid = entry.get("dcc_pid", entry.get("pid"))
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or entry_pid != pid:
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Probe PID does not match the selected registry entry")
    if session.get("install_id") != receipt.get("install_id"):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Embedded process belongs to another installation")
    if session.get("adapter_version") != receipt.get("version"):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Embedded adapter version does not match the receipt")
    if session.get("selected_dcc_sha256") != receipt.get("dcc_sha256") or not _same_path(
        str(session.get("selected_dcc_path") or ""), str(receipt.get("dcc_path") or "")
    ):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Embedded process is not bound to the selected product")
    runtime_version = _parse_host_version(str(session.get("zbrush_version") or ""))
    selected_version = _parse_host_version(str(receipt.get("host_version") or ""))
    if runtime_version[:2] != selected_version[:2]:
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Embedded ZBrush version does not match the product")

    mcp_url = str(entry.get("mcp_url") or "")
    if not mcp_url or session.get("mcp_url") != mcp_url:
        raise LifecycleFailure(
            EXIT_VERIFY, "host_identity", "Embedded endpoint does not match the selected registry entry"
        )
    adapter_path = asset_dir / "dcc_mcp_zbrush" / "__init__.py"
    tree = next(
        (item for item in receipt.get("managed_trees", []) if item.get("path") == "dcc_mcp_zbrush"),
        None,
    )
    adapter_entry = next(
        (
            item
            for item in (tree or {}).get("installed_manifest", [])
            if item.get("kind") == "file" and item.get("path") == "__init__.py"
        ),
        None,
    )
    if not _same_path(str(session.get("adapter_module_path") or ""), adapter_path):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Embedded adapter module is not receipt-owned")
    if not isinstance(adapter_entry, dict) or session.get("adapter_module_sha256") != adapter_entry.get("sha256"):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Embedded adapter module bytes are not receipt-owned")

    zbrush_origin, zbrush_digest = _zbrush_module_provenance(receipt, str(session.get("zbrush_commands_origin") or ""))
    try:
        process_path = _process_executable(pid)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Could not bind the reported ZBrush process") from exc
    if not _same_path(process_path, str(receipt.get("dcc_path") or "")):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Registry PID is not the selected ZBrush executable")
    if not _same_path(str(session.get("process_executable") or ""), process_path):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Embedded Python reports a different host executable")
    try:
        process_digest = _sha256_file(process_path)
    except OSError as exc:
        raise LifecycleFailure(
            EXIT_VERIFY, "host_identity", "Could not verify running ZBrush executable bytes"
        ) from exc
    if process_digest != receipt.get("dcc_sha256"):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Running ZBrush executable bytes changed")
    try:
        start_identity = _process_start_identity(pid)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Could not bind the ZBrush process start time") from exc
    identity = {
        "adapter_module_path": str(adapter_path.resolve()),
        "endpoint": mcp_url,
        "instance_id": instance_id,
        "pid": pid,
        "process_start_identity": start_identity,
        "zbrush_commands_origin": str(zbrush_origin),
        "zbrush_commands_sha256": zbrush_digest,
        "zbrush_version": str(session.get("zbrush_version")),
    }
    prior = receipt.get("runtime_identity")
    if prior is not None and (
        not isinstance(prior, dict) or any(prior.get(key) != value for key, value in identity.items())
    ):
        raise LifecycleFailure(EXIT_VERIFY, "host_identity", "Embedded runtime identity changed after verification")
    return identity


def _preflight(request: LifecycleRequest, *, require_paths: bool = True) -> dict[str, Any]:
    if request.mode not in {"embedded", "sidecar"}:
        raise LifecycleFailure(EXIT_PREFLIGHT, "mode", f"Unsupported install mode: {request.mode}")
    if not request.version or request.version.lower() == "latest":
        raise LifecycleFailure(
            EXIT_PREFLIGHT, "version", "A fixed adapter version is required; 'latest' is not accepted"
        )
    _parse_version(request.version, stage="version")
    if (
        request.socket_host not in {"127.0.0.1", "localhost", "::1"}
        or not isinstance(request.socket_port, int)
        or isinstance(request.socket_port, bool)
        or not (1 <= request.socket_port <= 65535)
    ):
        raise LifecycleFailure(EXIT_PREFLIGHT, "endpoint", "ZBrush socket endpoint must be a bounded loopback port")
    try:
        core = package_version("dcc-mcp-core")
    except PackageNotFoundError as exc:
        raise LifecycleFailure(EXIT_PREFLIGHT, "core", "dcc-mcp-core is not installed") from exc
    if _parse_version(core, stage="core") < MINIMUM_CORE:
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
        )
    host = _probe_zbrush_host(request.dcc_path)
    if request.asset_dir is None:
        raise LifecycleFailure(
            EXIT_PREFLIGHT,
            "asset_dir",
            "ZBrush Asset Directory was not provided",
        )
    asset_dir = _validate_root(request.asset_dir, "Asset Directory")
    python_path = request.python_path or Path(sys.executable)
    resolved_python = _resolve_python(python_path)
    if resolved_python != Path(sys.executable).resolve():
        raise LifecycleFailure(
            EXIT_PREFLIGHT,
            "python",
            "Run the lifecycle command with the exact selected Python interpreter",
            next_steps=[
                _next_step(
                    "rerun-selected-python",
                    "Run this lifecycle operation with the selected interpreter.",
                    "The adapter and Core module provenance must belong to the process performing the transaction.",
                    command=_lifecycle_command(request, request.operation, yes=request.yes),
                )
            ],
        )
    adapter_runtime = _distribution_module_provenance("dcc-mcp-zbrush", "dcc_mcp_zbrush")
    core_runtime = _distribution_module_provenance("dcc-mcp-core", "dcc_mcp_core")
    detected.update(
        {
            **host,
            "adapter_runtime": adapter_runtime,
            "asset_dir": str(asset_dir),
            "core_runtime": core_runtime,
            "python": str(resolved_python),
            "python_sha256": _sha256_file(resolved_python),
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


def _remove_path(path: Path) -> None:
    is_junction = getattr(path, "is_junction", lambda: False)
    if is_junction():
        os.rmdir(path)
    elif path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _prune_empty_managed_parents(asset_dir: Path) -> None:
    for relative in (
        Path(".dcc-mcp/transactions"),
        Path(".dcc-mcp/staging"),
        Path(".dcc-mcp/backups"),
        Path(".dcc-mcp/receipts"),
    ):
        path = asset_dir / relative
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def _capture_transaction(
    asset_dir: Path,
    transaction_id: str,
    backup_root: Path,
    *,
    extra_targets: Iterable[Path] = (),
    remove_new_backup_on_restore: bool = True,
    operation: str = "install",
) -> dict[str, Any]:
    recovery_root = asset_dir / ".dcc-mcp" / "transactions" / transaction_id
    recovery_root.mkdir(parents=True, exist_ok=False)
    snapshots: list[dict[str, Any]] = []
    target_texts = set(TRANSACTION_TARGETS)
    target_texts.update(path.as_posix() for path in extra_targets)
    for index, relative_text in enumerate(sorted(target_texts)):
        relative = Path(PurePosixPath(relative_text))
        path = asset_dir / relative
        is_junction = getattr(path, "is_junction", lambda: False)
        snapshot: dict[str, Any] = {"path": relative.as_posix()}
        if path.is_symlink() or is_junction():
            snapshot.update(
                {
                    "kind": "link",
                    "target": os.readlink(path),
                    "target_is_directory": path.is_dir(),
                }
            )
        elif path.is_file():
            backup = recovery_root / f"{index}.file"
            _atomic_write(backup, path.read_bytes())
            snapshot.update({"kind": "file", "backup": backup.relative_to(asset_dir).as_posix()})
        elif path.is_dir():
            backup = recovery_root / f"{index}.tree"
            shutil.copytree(path, backup, symlinks=True)
            snapshot.update({"kind": "directory", "backup": backup.relative_to(asset_dir).as_posix()})
        else:
            snapshot["kind"] = "missing"
        snapshots.append(snapshot)
    return {
        "id": transaction_id,
        "operation": operation,
        "recovery_root": recovery_root.relative_to(asset_dir).as_posix(),
        "new_backup_root": backup_root.relative_to(asset_dir).as_posix(),
        "extra_targets": sorted(path.as_posix() for path in extra_targets),
        "remove_new_backup_on_restore": remove_new_backup_on_restore,
        "snapshots": snapshots,
    }


def _restore_transaction(asset_dir: Path, transaction: Mapping[str, Any]) -> None:
    recovery_root = asset_dir / str(transaction["recovery_root"])
    if not recovery_root.is_dir():
        raise LifecycleFailure(EXIT_INSTALL, "rollback", "Transaction recovery storage is missing")
    snapshots = sorted(
        transaction["snapshots"],
        key=lambda item: str(item.get("path")) == RECEIPT_RELATIVE.as_posix(),
    )
    for snapshot in snapshots:
        destination = asset_dir / str(snapshot["path"])
        _remove_path(destination)
        kind = snapshot["kind"]
        if kind == "file":
            backup = asset_dir / str(snapshot["backup"])
            _atomic_write(destination, backup.read_bytes())
        elif kind == "directory":
            backup = asset_dir / str(snapshot["backup"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(backup, destination, symlinks=True)
        elif kind == "link":
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(
                str(snapshot["target"]),
                destination,
                target_is_directory=bool(snapshot.get("target_is_directory")),
            )
    if transaction.get("remove_new_backup_on_restore", True):
        _remove_path(asset_dir / str(transaction["new_backup_root"]))
    _remove_path(recovery_root)
    _prune_empty_managed_parents(asset_dir)


def _rollback_pending_transaction(asset_dir: Path, receipt: Mapping[str, Any]) -> None:
    transaction = receipt.get("transaction")
    if isinstance(transaction, dict):
        _restore_transaction(asset_dir, transaction)


def _commit_pending_transaction(
    asset_dir: Path, receipt: Mapping[str, Any], runtime_identity: Mapping[str, Any]
) -> dict[str, Any]:
    transaction = receipt.get("transaction")
    committed = dict(receipt)
    committed.pop("transaction", None)
    committed["runtime_identity"] = dict(runtime_identity)
    _write_json(asset_dir / RECEIPT_RELATIVE, committed)
    if isinstance(transaction, dict):
        try:
            _remove_path(asset_dir / str(transaction["recovery_root"]))
            if transaction.get("new_backup_root") != committed.get("backup_root"):
                _remove_path(asset_dir / str(transaction["new_backup_root"]))
            _prune_empty_managed_parents(asset_dir)
        except OSError:
            # Verification has committed the usable candidate. Retaining recovery
            # data is safer than reporting a false rollback or deleting it partially.
            pass
    return committed


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
    _parse_version(str(data.get("version") or ""), stage="receipt")
    _parse_host_version(str(data.get("host_version") or ""), stage="receipt")
    if re.fullmatch(r"(?i)(?:maxon\s+)?zbrush", str(data.get("product_name") or "").strip()) is None:
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt product identity is not Maxon ZBrush")
    if "maxon" not in str(data.get("product_signer") or "").casefold():
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt product signer is not Maxon")
    try:
        uuid.UUID(str(data.get("install_id") or ""))
    except (ValueError, AttributeError) as exc:
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt install identity is invalid") from exc
    dcc_path = Path(str(data.get("dcc_path") or ""))
    dcc_root = Path(str(data.get("dcc_root") or ""))
    if (
        not dcc_path.is_absolute()
        or not dcc_root.is_absolute()
        or not dcc_path.resolve().is_relative_to(dcc_root.resolve())
    ):
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt ZBrush product path binding is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(data.get("dcc_sha256") or "")):
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt ZBrush executable digest is invalid")
    python_path = Path(str(data.get("python_path") or ""))
    if not python_path.is_absolute() or not re.fullmatch(r"[0-9a-f]{64}", str(data.get("python_sha256") or "")):
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt Python interpreter binding is invalid")
    _parse_version(str(data.get("python_version") or ""), stage="receipt")
    endpoint = data.get("socket_endpoint")
    if (mode == "sidecar" and not isinstance(endpoint, str)) or (mode == "embedded" and endpoint is not None):
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt endpoint binding is invalid")
    backup_root = _receipt_relative(asset_dir, data.get("backup_root"), "backup_root")
    expected_backup_parent = (asset_dir / ".dcc-mcp" / "backups").resolve()
    if not backup_root.is_relative_to(expected_backup_parent):
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt backup_root is outside the managed backup area")
    _validate_manifest(backup_root, data.get("backup_manifest"), verify_bytes=False)
    managed_files = data.get("managed_files")
    managed_trees = data.get("managed_trees")
    if not isinstance(managed_files, list) or not isinstance(managed_trees, list):
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt managed file collections must be arrays")
    expected_files = (
        {SIDECAR_RELATIVE.as_posix(), IDENTITY_RELATIVE.as_posix()}
        if mode == "sidecar"
        else {"dcc_mcp_zbrush_plugin.py", EMBEDDED_IDENTITY_RELATIVE.as_posix()}
    )
    file_paths = {str(record.get("path") or "") for record in managed_files if isinstance(record, dict)}
    expected_trees = set() if mode == "sidecar" else {"dcc_mcp_zbrush"}
    tree_paths = {str(record.get("path") or "") for record in managed_trees if isinstance(record, dict)}
    if file_paths != expected_files or tree_paths != expected_trees:
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt ownership does not match the selected mode")
    records: list[Mapping[str, Any]] = list(managed_files) + list(managed_trees)
    seen: set[Path] = set()
    for record in records:
        if not isinstance(record, dict):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt managed entries must be objects")
        managed_path = _receipt_relative(asset_dir, record.get("path"), "managed path")
        if managed_path in seen:
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt contains duplicate managed paths")
        seen.add(managed_path)
        if record in managed_files and not isinstance(record.get("existed"), bool):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt managed file pre-install state is invalid")
        if record in managed_files and record.get("existed"):
            backup = _receipt_relative(asset_dir, record.get("backup"), "backup path")
            expected_backup = backup_root / managed_path.relative_to(asset_dir)
            if backup != expected_backup:
                raise LifecycleFailure(
                    EXIT_PREFLIGHT, "receipt", "Receipt backup is not structurally bound to its file"
                )
        installed_sha256 = record.get("installed_sha256")
        if record in managed_files and not re.fullmatch(r"[0-9a-f]{64}", str(installed_sha256 or "")):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt contains an invalid managed file digest")
        if record in managed_trees:
            if not isinstance(record.get("root_existed"), bool):
                raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Managed tree root ownership is missing")
            _validate_manifest(managed_path, record.get("installed_manifest"), verify_bytes=False)
    shared = data.get("shared_init")
    if mode == "sidecar":
        if not isinstance(shared, dict):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Sidecar receipt has no shared init.py record")
        shared_path = _receipt_relative(asset_dir, shared.get("path"), "shared init path")
        if shared_path != (asset_dir / SHARED_INIT_RELATIVE).resolve():
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Sidecar receipt targets an unexpected shared init.py")
        if not isinstance(shared.get("existed"), bool):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Shared init.py pre-install state is invalid")
        if shared.get("existed"):
            backup = _receipt_relative(asset_dir, shared.get("backup"), "shared init backup")
            if backup != backup_root / SHARED_INIT_RELATIVE:
                raise LifecycleFailure(
                    EXIT_PREFLIGHT, "receipt", "Shared init.py backup is not structurally bound to its file"
                )
        if not re.fullmatch(r"[0-9a-f]{64}", str(shared.get("installed_sha256") or "")):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Shared init.py receipt digest is invalid")
    elif shared is not None:
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Embedded receipt must not own the shared init.py")
    for field, expected_distribution, expected_module in (
        ("adapter_runtime", "dcc-mcp-zbrush", "dcc_mcp_zbrush"),
        ("core_runtime", "dcc-mcp-core", "dcc_mcp_core"),
    ):
        runtime = data.get(field)
        if not isinstance(runtime, dict):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", f"Receipt has no {field} provenance")
        if runtime.get("distribution") != expected_distribution or runtime.get("module") != expected_module:
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", f"Receipt {field} owner is invalid")
        _parse_version(str(runtime.get("version") or ""), stage="receipt")
        if not re.fullmatch(r"[0-9a-f]{64}", str(runtime.get("module_sha256") or "")):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", f"Receipt {field} digest is invalid")
        module_path = Path(str(runtime.get("module_path") or ""))
        if not module_path.is_absolute():
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", f"Receipt {field} path is not absolute")
    transaction = data.get("transaction")
    if transaction is not None:
        if not isinstance(transaction, dict):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt transaction must be an object")
        recovery_root = _receipt_relative(asset_dir, transaction.get("recovery_root"), "transaction recovery root")
        if transaction.get("operation", "install") not in {"install", "uninstall"}:
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Transaction operation is invalid")
        expected_transaction_parent = (asset_dir / ".dcc-mcp" / "transactions").resolve()
        if not recovery_root.is_relative_to(expected_transaction_parent):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Transaction recovery root is outside managed storage")
        snapshots = transaction.get("snapshots")
        if not isinstance(snapshots, list) or not snapshots:
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt transaction has no recovery snapshots")
        seen_snapshot_paths: set[str] = set()
        extra_targets = transaction.get("extra_targets", [])
        if not isinstance(extra_targets, list):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Transaction extra targets must be an array")
        allowed_snapshot_paths = set(TRANSACTION_TARGETS)
        for extra_target in extra_targets:
            extra_path = _receipt_relative(asset_dir, extra_target, "transaction extra target")
            if not extra_path.is_relative_to(expected_backup_parent):
                raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Transaction extra target escapes managed backups")
            allowed_snapshot_paths.add(extra_path.relative_to(asset_dir).as_posix())
        if not isinstance(transaction.get("remove_new_backup_on_restore", True), bool):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Transaction restore policy is invalid")
        for snapshot in snapshots:
            if not isinstance(snapshot, dict) or snapshot.get("kind") not in {"missing", "file", "directory", "link"}:
                raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt transaction snapshot is malformed")
            snapshot_path = str(snapshot.get("path") or "")
            if snapshot_path not in allowed_snapshot_paths or snapshot_path in seen_snapshot_paths:
                raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Receipt transaction snapshot path is not owned")
            seen_snapshot_paths.add(snapshot_path)
            if snapshot["kind"] in {"file", "directory"}:
                backup = _receipt_relative(asset_dir, snapshot.get("backup"), "transaction backup")
                if not backup.is_relative_to(recovery_root):
                    raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Transaction backup escapes recovery storage")
            elif snapshot["kind"] == "link":
                target = snapshot.get("target")
                if (
                    not isinstance(target, str)
                    or not target
                    or len(target) > 32768
                    or not isinstance(snapshot.get("target_is_directory"), bool)
                ):
                    raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Transaction link snapshot is invalid")
        new_backup_root = _receipt_relative(asset_dir, transaction.get("new_backup_root"), "new backup root")
        if not new_backup_root.is_relative_to(expected_backup_parent):
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Transaction backup root escapes managed backups")
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
    _parse_version(version, stage="version")
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


def _lock_preflight(asset_dir: Path, request: LifecycleRequest) -> None:
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
                    command=_lifecycle_command(request, request.operation, yes=True),
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


def _safe_link_target(root: Path, link: Path, target: str) -> str:
    if not target or len(target) > 4096 or Path(target).is_absolute():
        raise LifecycleFailure(EXIT_PREFLIGHT, "ownership", "Managed link target is not bounded and relative")
    resolved = (link.parent / target).resolve(strict=False)
    if not resolved.is_relative_to(root.resolve()):
        raise LifecycleFailure(EXIT_PREFLIGHT, "ownership", "Managed link target escapes its owned tree")
    return target


def _manifest(root: Path) -> list[dict[str, Any]]:
    """Describe the exact owned file/directory/link closure without following links."""

    entries: list[dict[str, Any]] = []
    if not root.is_dir() or root.is_symlink():
        return entries
    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        traversed: list[str] = []
        for name in sorted(directory_names):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                target = _safe_link_target(root, path, os.readlink(path))
                entries.append({"kind": "link", "path": relative, "target": target, "target_is_directory": True})
            else:
                entries.append({"kind": "directory", "path": relative})
                traversed.append(name)
        directory_names[:] = traversed
        for name in sorted(file_names):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                target = _safe_link_target(root, path, os.readlink(path))
                entries.append({"kind": "link", "path": relative, "target": target, "target_is_directory": False})
            elif path.is_file():
                entries.append(
                    {
                        "kind": "file",
                        "path": relative,
                        "sha256": _sha256_file(path),
                        "size": path.stat().st_size,
                    }
                )
            else:
                raise LifecycleFailure(EXIT_PREFLIGHT, "ownership", f"Unsupported managed entry: {relative}")
    return sorted(entries, key=lambda item: (str(item["path"]), str(item["kind"])))


def _validate_manifest(root: Path, manifest: Any, *, verify_bytes: bool = True) -> None:
    if not isinstance(manifest, list):
        raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Managed tree manifest must be a typed array")
    seen: set[str] = set()
    directories: set[str] = set()
    for entry in manifest:
        if not isinstance(entry, dict) or entry.get("kind") not in {"file", "directory", "link"}:
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Managed tree manifest entry is malformed")
        relative_text = str(entry.get("path") or "")
        if not _safe_member(relative_text) or relative_text in seen:
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Managed tree manifest path is unsafe or duplicated")
        seen.add(relative_text)
        relative = PurePosixPath(relative_text)
        if len(relative.parts) > 64:
            raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Managed tree manifest path is too deep")
        destination = root.joinpath(*relative.parts)
        for parent in relative.parents:
            if str(parent) != "." and parent.as_posix() not in directories:
                raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Managed tree manifest omits a parent directory")
        kind = entry["kind"]
        if kind == "directory":
            if verify_bytes and (destination.is_symlink() or not destination.is_dir()):
                raise LifecycleFailure(EXIT_PREFLIGHT, "integrity", f"Managed directory drifted: {relative_text}")
            directories.add(relative_text)
        elif kind == "file":
            digest = str(entry.get("sha256") or "")
            size = entry.get("size")
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or not isinstance(size, int) or size < 0:
                raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Managed file ownership metadata is invalid")
            if verify_bytes and (destination.is_symlink() or not destination.is_file()):
                raise LifecycleFailure(EXIT_PREFLIGHT, "integrity", f"Managed file drifted: {relative_text}")
            if verify_bytes and (destination.stat().st_size != size or _sha256_file(destination) != digest):
                raise LifecycleFailure(EXIT_PREFLIGHT, "integrity", f"Managed file bytes drifted: {relative_text}")
        else:
            target = _safe_link_target(root, destination, str(entry.get("target") or ""))
            if not isinstance(entry.get("target_is_directory"), bool):
                raise LifecycleFailure(EXIT_PREFLIGHT, "receipt", "Managed link ownership metadata is invalid")
            if verify_bytes and not destination.is_symlink():
                raise LifecycleFailure(EXIT_PREFLIGHT, "integrity", f"Managed link drifted: {relative_text}")
            if verify_bytes and os.readlink(destination) != target:
                raise LifecycleFailure(EXIT_PREFLIGHT, "integrity", f"Managed link target drifted: {relative_text}")


def _remove_owned_entries(root: Path, manifest: list[dict[str, Any]], *, keep: set[str] | None = None) -> None:
    keep_paths = keep or set()
    leaves = [entry for entry in manifest if entry["kind"] in {"file", "link"}]
    directories = [entry for entry in manifest if entry["kind"] == "directory"]
    for entry in sorted(leaves, key=lambda item: len(PurePosixPath(str(item["path"])).parts), reverse=True):
        if str(entry["path"]) not in keep_paths:
            _remove_path(root.joinpath(*PurePosixPath(str(entry["path"])).parts))
    for entry in sorted(directories, key=lambda item: len(PurePosixPath(str(item["path"])).parts), reverse=True):
        path = root.joinpath(*PurePosixPath(str(entry["path"])).parts)
        if str(entry["path"]) not in keep_paths and path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def _install_managed_tree(
    source: Path,
    destination: Path,
    previous_manifest: Optional[list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], bool]:
    root_existed = destination.is_dir()
    if destination.is_symlink() or getattr(destination, "is_junction", lambda: False)():
        raise LifecycleFailure(EXIT_PREFLIGHT, "ownership", "Managed package root cannot be a link")
    if root_existed and previous_manifest is None and any(destination.iterdir()):
        raise LifecycleFailure(EXIT_PREFLIGHT, "partial_install", "Embedded package root exists without ownership")
    if previous_manifest is not None:
        _validate_manifest(destination, previous_manifest)
    candidate = _manifest(source)
    candidate_kinds = {str(entry["path"]): str(entry["kind"]) for entry in candidate}
    if previous_manifest is not None:
        unchanged_kinds = {
            str(entry["path"])
            for entry in previous_manifest
            if candidate_kinds.get(str(entry["path"])) == str(entry["kind"])
        }
        _remove_owned_entries(destination, previous_manifest, keep=unchanged_kinds)
    previous_paths = {str(entry["path"]) for entry in previous_manifest or []}
    destination.mkdir(parents=True, exist_ok=True)
    for entry in candidate:
        relative = PurePosixPath(str(entry["path"]))
        source_path = source.joinpath(*relative.parts)
        target = destination.joinpath(*relative.parts)
        if target.exists() or target.is_symlink():
            if str(entry["path"]) not in previous_paths and entry["kind"] != "directory":
                raise LifecycleFailure(EXIT_PREFLIGHT, "ownership", f"Refusing unowned path collision: {entry['path']}")
        if entry["kind"] == "directory":
            target.mkdir(parents=True, exist_ok=True)
        elif entry["kind"] == "file":
            _atomic_write(target, source_path.read_bytes())
        else:
            _remove_path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(
                str(entry["target"]),
                target,
                target_is_directory=bool(entry["target_is_directory"]),
            )
    _validate_manifest(destination, candidate)
    return candidate, root_existed


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
    if existing and existing.get("transaction") is not None:
        return _result(
            request,
            status="requires_restart",
            exit_code=EXIT_REQUIRES_RESTART,
            stage="verify_pending",
            reason="The candidate is installed but is not committed until exact ZBrush readiness succeeds",
            receipt=receipt_path,
            detected=detected,
            next_steps=_verification_steps(request, detected),
        )
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
            next_steps=_verification_steps(request, detected),
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
                    command=_lifecycle_command(request, "upgrade", yes=True),
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
                    command=_lifecycle_command(request, request.operation, yes=True),
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
    _lock_preflight(asset_dir, request)
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
    records: list[dict[str, Any]] = []
    tree_records: list[dict[str, Any]] = []
    staging_roots: list[Path] = []
    shared_record: dict[str, Any] = {}
    old_backup_root: Optional[Path] = None
    transaction_record: Optional[dict[str, Any]] = None
    try:
        asset_dir.mkdir(parents=True, exist_ok=True)
        transaction_record = _capture_transaction(asset_dir, transaction, backup_root)
        install_id = str(uuid.uuid4())
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
            _atomic_write(bridge_path, bridge_bytes)
            record["installed_sha256"] = _sha256_bytes(bridge_bytes)
            records.append(record)

            identity_path = asset_dir / IDENTITY_RELATIVE
            prior_identity_record = None
            if existing:
                prior_identity_record = next(
                    (
                        item
                        for item in existing.get("managed_files", [])
                        if item.get("path") == IDENTITY_RELATIVE.as_posix()
                    ),
                    None,
                )
            identity_record = (
                dict(prior_identity_record)
                if prior_identity_record
                else _backup_file(identity_path, asset_dir, backup_root, IDENTITY_RELATIVE)
            )
            identity_bytes = (
                json.dumps(
                    {
                        "adapter_version": request.version,
                        "dcc_path": detected.get("dcc_path"),
                        "dcc_sha256": detected.get("dcc_sha256"),
                        "install_id": install_id,
                        "socket_endpoint": f"{request.socket_host}:{request.socket_port}",
                        "zbrush_version": detected.get("zbrush_version"),
                    },
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            _atomic_write(identity_path, identity_bytes)
            identity_record["installed_sha256"] = _sha256_bytes(identity_bytes)
            records.append(identity_record)

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
            previous_manifest = prior_tree.get("installed_manifest") if isinstance(prior_tree, dict) else None
            manifest, root_existed = _install_managed_tree(package_source, destination, previous_manifest)
            tree_record = {
                "path": "dcc_mcp_zbrush",
                "root_existed": bool(prior_tree.get("root_existed")) if prior_tree else root_existed,
                "installed_manifest": manifest,
            }
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
            _atomic_write(plugin_path, plugin_bytes)
            record["installed_sha256"] = _sha256_bytes(plugin_bytes)
            records.append(record)

            embedded_identity_path = asset_dir / EMBEDDED_IDENTITY_RELATIVE
            prior_identity_record = None
            if existing:
                prior_identity_record = next(
                    (
                        item
                        for item in existing.get("managed_files", [])
                        if item.get("path") == EMBEDDED_IDENTITY_RELATIVE.as_posix()
                    ),
                    None,
                )
            identity_record = (
                dict(prior_identity_record)
                if prior_identity_record
                else _backup_file(
                    embedded_identity_path,
                    asset_dir,
                    backup_root,
                    EMBEDDED_IDENTITY_RELATIVE,
                )
            )
            identity_bytes = (
                json.dumps(
                    {
                        "adapter_version": request.version,
                        "dcc_path": detected.get("dcc_path"),
                        "dcc_sha256": detected.get("dcc_sha256"),
                        "install_id": install_id,
                        "mode": "embedded",
                        "zbrush_version": detected.get("zbrush_version"),
                    },
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            _atomic_write(embedded_identity_path, identity_bytes)
            identity_record["installed_sha256"] = _sha256_bytes(identity_bytes)
            records.append(identity_record)

        error_path = asset_dir / BOOTSTRAP_ERRORS_RELATIVE
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "adapter": ADAPTER,
            "version": request.version,
            "mode": request.mode,
            "installed_at": _utc_now(),
            "payload_sha256": digest,
            "backup_root": (old_backup_root or backup_root).relative_to(asset_dir).as_posix(),
            "backup_manifest": _manifest(old_backup_root or backup_root),
            "shared_init": shared_record or None,
            "managed_files": records,
            "managed_trees": tree_records,
            "bootstrap_error_offset": error_path.stat().st_size if error_path.exists() else 0,
            "adapter_runtime": detected.get("adapter_runtime"),
            "core_runtime": detected.get("core_runtime"),
            "host_version": detected.get("zbrush_version"),
            "product_name": detected.get("product_name"),
            "product_signer": detected.get("product_signer"),
            "dcc_path": detected.get("dcc_path"),
            "dcc_root": detected.get("dcc_root"),
            "dcc_sha256": detected.get("dcc_sha256"),
            "install_id": install_id,
            "socket_endpoint": f"{request.socket_host}:{request.socket_port}" if request.mode == "sidecar" else None,
            "python_path": detected.get("python"),
            "python_sha256": detected.get("python_sha256"),
            "python_version": detected.get("python_version"),
            "transaction": transaction_record,
        }
        _write_json(receipt_path, receipt)
        for staging_root in staging_roots:
            shutil.rmtree(staging_root, ignore_errors=True)
        if plugin_archive is None:
            _prune_cache((cache_root or _default_cache_root()), request.version)
        return _result(
            request,
            status="requires_restart",
            exit_code=EXIT_REQUIRES_RESTART,
            stage="verify_pending",
            reason="Candidate files are staged with recovery data and require exact ZBrush readiness before commit",
            changed=True,
            receipt=receipt_path,
            detected=detected,
            next_steps=_verification_steps(request, detected),
        )
    except BaseException:
        if transaction_record is not None:
            _restore_transaction(asset_dir, transaction_record)
        for staging_root in staging_roots:
            shutil.rmtree(staging_root, ignore_errors=True)
        raise
    finally:
        archive.close()


def _command_interpreter(request: LifecycleRequest) -> str:
    candidate = request.python_path or Path(sys.executable)
    if candidate.expanduser().is_file():
        return str(candidate.expanduser().resolve())
    discovered = shutil.which(str(candidate))
    return str(Path(discovered).resolve()) if discovered else str(candidate)


def _lifecycle_command(request: LifecycleRequest, operation: str, *, yes: bool = False) -> list[str]:
    interpreter = _command_interpreter(request)
    command = [
        interpreter,
        "-m",
        "dcc_mcp_zbrush.cli",
        operation,
        "--mode",
        request.mode,
        "--version",
        request.version,
    ]
    if request.dcc_path is not None:
        command.extend(["--dcc-path", str(request.dcc_path)])
    if request.python_path is not None:
        command.extend(["--python", str(request.python_path)])
    if request.asset_dir is not None:
        command.extend(["--asset-dir", str(request.asset_dir)])
    command.extend(["--socket-host", request.socket_host, "--socket-port", str(request.socket_port)])
    if yes:
        command.append("--yes")
    command.append("--json")
    return command


def _verification_steps(
    request: LifecycleRequest, detected: Optional[Mapping[str, Any]] = None
) -> list[dict[str, Any]]:
    start_path = str((detected or {}).get("dcc_path") or request.dcc_path or "")
    if request.mode == "sidecar":
        steps = [
            _next_step(
                "restart-zbrush",
                "Start the exact selected ZBrush executable.",
                "ZBrush loads Python/init.py during host startup.",
                command=[start_path] if start_path else _lifecycle_command(request, "status"),
            ),
            _next_step(
                "verify-zbrush",
                "Verify the exact installed bridge and running ZBrush identity.",
                "Verification commits the pending transaction only after exact readiness succeeds.",
                command=_lifecycle_command(request, "verify"),
            ),
        ]
        return steps
    return [
        _next_step(
            "restart-zbrush",
            "Start the exact selected ZBrush executable.",
            "Embedded startup is completed on ZBrush's main thread.",
            command=[start_path] if start_path else _lifecycle_command(request, "status"),
        ),
        _next_step(
            "verify-zbrush",
            "Verify the exact embedded registration identity.",
            "Verification commits the pending transaction only after exact readiness succeeds.",
            command=_lifecycle_command(request, "verify"),
        ),
    ]


def _installation_state(asset_dir: Path, receipt: Optional[Mapping[str, Any]]) -> tuple[str, str]:
    marker = (asset_dir / SHARED_INIT_RELATIVE).read_bytes() if (asset_dir / SHARED_INIT_RELATIVE).is_file() else b""
    managed_candidates = [
        asset_dir / SIDECAR_RELATIVE,
        asset_dir / IDENTITY_RELATIVE,
        asset_dir / EMBEDDED_IDENTITY_RELATIVE,
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
        try:
            _validate_manifest(path, record.get("installed_manifest"))
        except LifecycleFailure:
            return "partial", f"Managed directory drifted: {record.get('path')}"
    backup_root = asset_dir / str(receipt.get("backup_root", ""))
    if _manifest(backup_root) != receipt.get("backup_manifest"):
        return "partial", "Managed recovery backup contains missing, changed, or unexpected entries"
    return "installed", "Receipt and managed payload hashes are valid"


def _status(request: LifecycleRequest, detected: Mapping[str, Any]) -> dict[str, Any]:
    if request.asset_dir is None:
        raise LifecycleFailure(EXIT_PREFLIGHT, "asset_dir", "ZBrush Asset Directory was not provided")
    asset_dir = _validate_root(request.asset_dir, "Asset Directory")
    receipt = _read_receipt(asset_dir)
    state, reason = _installation_state(asset_dir, receipt)
    transaction = receipt.get("transaction") if receipt else None
    if state == "installed" and isinstance(transaction, dict):
        if transaction.get("operation", "install") == "install":
            state = "requires_restart"
            reason = "Candidate files are staged but not committed until exact ZBrush readiness succeeds"
        else:
            state = "partial"
            reason = "An interrupted uninstall has immutable recovery data and must be resumed"
    code = {
        "installed": EXIT_OK,
        "not_installed": EXIT_OK,
        "requires_restart": EXIT_REQUIRES_RESTART,
    }.get(state, EXIT_PREFLIGHT)
    return _result(
        request,
        status=state,
        exit_code=code,
        stage="status",
        reason=reason,
        receipt=asset_dir / RECEIPT_RELATIVE if receipt else None,
        detected=detected,
        next_steps=_verification_steps(request, detected) if state in {"installed", "requires_restart"} else [],
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


def _verification_failure(
    request: LifecycleRequest,
    asset_dir: Path,
    receipt: Mapping[str, Any],
    detected: Mapping[str, Any],
    *,
    status: str,
    stage: str,
    reason: str,
    extra_detected: Optional[Mapping[str, Any]] = None,
    next_steps: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Fail verification and restore the exact pre-install state when pending."""

    rolled_back = False
    if isinstance(receipt.get("transaction"), dict):
        try:
            _rollback_pending_transaction(asset_dir, receipt)
            rolled_back = True
        except (OSError, LifecycleFailure) as exc:
            return _result(
                request,
                status="failed",
                exit_code=EXIT_INSTALL,
                stage="rollback",
                reason=f"Verification failed and exact recovery could not be restored: {exc}",
                receipt=asset_dir / RECEIPT_RELATIVE,
                detected={**detected, **dict(extra_detected or {})},
                next_steps=[
                    _next_step(
                        "retry-rollback",
                        "Retry verification to complete the recorded recovery procedure.",
                        "The immutable transaction snapshots remain available until exact recovery succeeds.",
                        command=_lifecycle_command(request, "verify"),
                    )
                ],
            )
    receipt_path = asset_dir / RECEIPT_RELATIVE
    suffix = "; the candidate was rolled back to the exact prior state" if rolled_back else ""
    return _result(
        request,
        status=status,
        exit_code=EXIT_VERIFY,
        stage=stage,
        reason=reason + suffix,
        receipt=receipt_path if receipt_path.is_file() else None,
        detected={**detected, **dict(extra_detected or {})},
        next_steps=[] if rolled_back else list(next_steps or _verification_steps(request, detected)),
    )


def _verify(request: LifecycleRequest, detected: Mapping[str, Any]) -> dict[str, Any]:
    if request.asset_dir is None:
        raise LifecycleFailure(EXIT_PREFLIGHT, "asset_dir", "ZBrush Asset Directory was not provided")
    asset_dir = _validate_root(request.asset_dir, "Asset Directory")
    receipt = _read_receipt(asset_dir)
    state, reason = _installation_state(asset_dir, receipt)
    if receipt is not None and isinstance(receipt.get("transaction"), dict) and state != "installed":
        return _verification_failure(
            request,
            asset_dir,
            receipt,
            detected,
            status="failed",
            stage="rollback",
            reason=f"Pending transaction integrity failed: {reason}",
        )
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
    for field in ("adapter_runtime", "core_runtime"):
        if receipt.get(field) != detected.get(field):
            return _verification_failure(
                request,
                asset_dir,
                receipt,
                detected,
                status="failed",
                stage="python_identity",
                reason=f"Loaded {field} module provenance differs from the install receipt",
            )
    if receipt.get("python_path") != detected.get("python") or receipt.get("python_sha256") != detected.get(
        "python_sha256"
    ):
        return _verification_failure(
            request,
            asset_dir,
            receipt,
            detected,
            status="failed",
            stage="python_identity",
            reason="Selected Python interpreter provenance differs from the install receipt",
        )
    failures = _bootstrap_failures(asset_dir, receipt)
    if failures:
        latest = failures[-1]
        return _verification_failure(
            request,
            asset_dir,
            receipt,
            detected,
            status="bootstrap_failed",
            stage=str(latest.get("stage", "bootstrap")),
            reason=str(latest.get("reason", "ZBrush bootstrap failed")),
            extra_detected={"bootstrap_errors": failures},
            next_steps=[
                _next_step(
                    "inspect-bootstrap-errors",
                    "Inspect the captured ZBrush bootstrap errors.",
                    "The host reported a startup failure after installation.",
                    command=_lifecycle_command(request, "status"),
                )
            ],
        )
    if request.mode == "sidecar":
        try:
            from dcc_mcp_zbrush.bridge import SocketBridge

            bridge = SocketBridge(request.socket_host, request.socket_port, timeout=2.0)
            try:
                bridge.connect()
                session = bridge.get_session_info()
            finally:
                bridge.disconnect()
        except Exception as exc:
            return _verification_failure(
                request,
                asset_dir,
                receipt,
                detected,
                status="host_unavailable",
                stage="host_readiness",
                reason=f"Installed payload is valid but the ZBrush socket bridge is unavailable: {exc}",
            )
        try:
            runtime_identity = _validate_sidecar_session(request, asset_dir, receipt, session)
        except LifecycleFailure as exc:
            return _verification_failure(
                request,
                asset_dir,
                receipt,
                detected,
                status="failed",
                stage=exc.stage,
                reason=exc.reason,
                extra_detected={"session": session},
            )
        if isinstance(receipt.get("transaction"), dict):
            receipt = _commit_pending_transaction(asset_dir, receipt, runtime_identity)
        elif receipt.get("runtime_identity") is None:
            receipt = dict(receipt)
            receipt["runtime_identity"] = runtime_identity
            _write_json(asset_dir / RECEIPT_RELATIVE, receipt)
        return _result(
            request,
            status="usable",
            exit_code=EXIT_OK,
            stage="host_readiness",
            reason="Receipt integrity, ZBrush socket ping, and session probe succeeded",
            directly_usable=True,
            receipt=asset_dir / RECEIPT_RELATIVE,
            detected={**detected, "runtime_identity": runtime_identity, "session": session},
            next_steps=[
                _next_step(
                    "start-sidecar",
                    "Start the external MCP sidecar.",
                    "The host socket bridge is ready for the external MCP process.",
                    command=[
                        _command_interpreter(request),
                        "-m",
                        "dcc_mcp_zbrush.cli",
                        "--mode",
                        "sidecar",
                        "--socket-host",
                        request.socket_host,
                        "--socket-port",
                        str(request.socket_port),
                    ],
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
        return _verification_failure(
            request,
            asset_dir,
            receipt,
            detected,
            status="host_unavailable",
            stage="host_readiness",
            reason=str(ready.get("message") or "Embedded ZBrush registration is not ready"),
            extra_detected={"readiness": ready},
        )
    try:
        runtime_identity = _validate_embedded_readiness(asset_dir, receipt, ready)
    except LifecycleFailure as exc:
        return _verification_failure(
            request,
            asset_dir,
            receipt,
            detected,
            status="failed",
            stage=exc.stage,
            reason=exc.reason,
            extra_detected={"readiness": ready},
        )
    if isinstance(receipt.get("transaction"), dict):
        receipt = _commit_pending_transaction(asset_dir, receipt, runtime_identity)
    elif receipt.get("runtime_identity") is None:
        receipt = dict(receipt)
        receipt["runtime_identity"] = runtime_identity
        _write_json(asset_dir / RECEIPT_RELATIVE, receipt)
    return _result(
        request,
        status="usable",
        exit_code=EXIT_OK,
        stage="host_readiness",
        reason="Embedded ZBrush session probe succeeded",
        directly_usable=True,
        receipt=asset_dir / RECEIPT_RELATIVE,
        detected={**detected, "readiness": ready, "runtime_identity": runtime_identity},
    )


def _uninstall(request: LifecycleRequest, detected: Mapping[str, Any]) -> dict[str, Any]:
    if request.asset_dir is None:
        raise LifecycleFailure(EXIT_PREFLIGHT, "asset_dir", "ZBrush Asset Directory was not provided")
    asset_dir = _validate_root(request.asset_dir, "Asset Directory")
    receipt_path = asset_dir / RECEIPT_RELATIVE
    receipt = _read_receipt(asset_dir)
    if receipt is not None and isinstance(receipt.get("transaction"), dict):
        try:
            _rollback_pending_transaction(asset_dir, receipt)
        except (OSError, LifecycleFailure) as exc:
            raise LifecycleFailure(EXIT_INSTALL, "rollback", f"Could not restore pending transaction: {exc}") from exc
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
    if state != "installed":
        raise LifecycleFailure(EXIT_INSTALL, "uninstall", reason)
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
                    command=_lifecycle_command(request, "uninstall", yes=True),
                )
            ],
        )
    _lock_preflight(asset_dir, request)
    for record in receipt.get("managed_files", []):
        path = asset_dir / str(record["path"])
        if path.is_symlink() or not path.is_file() or _sha256_file(path) != record.get("installed_sha256"):
            raise LifecycleFailure(
                EXIT_INSTALL, "uninstall", f"Managed file changed after install; refusing to remove {record['path']}"
            )
    for record in receipt.get("managed_trees", []):
        path = asset_dir / str(record["path"])
        try:
            _validate_manifest(path, record.get("installed_manifest"))
        except LifecycleFailure as exc:
            raise LifecycleFailure(EXIT_INSTALL, "uninstall", str(exc)) from exc
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
    backup_root = asset_dir / str(receipt.get("backup_root", ""))
    transaction_id = uuid.uuid4().hex
    transaction = _capture_transaction(
        asset_dir,
        transaction_id,
        backup_root,
        extra_targets=[backup_root.relative_to(asset_dir)],
        remove_new_backup_on_restore=False,
        operation="uninstall",
    )
    pending_receipt = dict(receipt)
    pending_receipt["transaction"] = transaction
    recovery_retained: Optional[str] = None
    try:
        _write_json(receipt_path, pending_receipt)
        for record in receipt.get("managed_files", []):
            _restore_file(asset_dir, record)
        for record in receipt.get("managed_trees", []):
            destination = asset_dir / str(record["path"])
            _remove_owned_entries(destination, list(record["installed_manifest"]))
            if not record.get("root_existed") and destination.is_dir() and not any(destination.iterdir()):
                destination.rmdir()
        if isinstance(shared_record, dict) and shared_path is not None:
            if _sha256_bytes(current) == shared_record.get("installed_sha256") and not shared_record.get(
                "preserve_edits"
            ):
                _restore_file(asset_dir, shared_record)
            else:
                updated = current.replace(MANAGED_BLOCK, b"", 1)
                _atomic_write(shared_path, updated)
        _remove_path(receipt_path)
        _remove_path(backup_root)
    except BaseException as exc:
        try:
            _restore_transaction(asset_dir, transaction)
        except BaseException as rollback_exc:
            raise LifecycleFailure(
                EXIT_INSTALL,
                "rollback",
                f"Uninstall failed and exact recovery could not be restored: {rollback_exc}",
            ) from rollback_exc
        raise LifecycleFailure(EXIT_INSTALL, "uninstall", f"Uninstall failed and was rolled back: {exc}") from exc
    try:
        _remove_path(asset_dir / str(transaction["recovery_root"]))
    except OSError:
        recovery_retained = str(transaction["recovery_root"])
    _prune_empty_managed_parents(asset_dir)
    return _result(
        request,
        status="uninstalled",
        exit_code=EXIT_OK,
        stage="complete",
        reason="Receipt-managed files were removed and previous shared startup state was restored",
        changed=True,
        detected={**detected, **({"recovery_retained": recovery_retained} if recovery_retained else {})},
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
                    command=_lifecycle_command(request, request.operation, yes=True),
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
