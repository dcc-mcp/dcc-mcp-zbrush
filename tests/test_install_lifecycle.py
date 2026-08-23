from __future__ import annotations

import hashlib
import json
import os
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _supported_host_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle.sys, "platform", "win32")


def _build_sidecar_archive(path: Path) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "sidecar/mcp_socket_bridge.py",
            "BRIDGE_SENTINEL = 'dcc-mcp-zbrush'\n",
        )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_exit_codes_match_install_sop_v1() -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    assert (
        lifecycle.EXIT_OK,
        lifecycle.EXIT_PREFLIGHT,
        lifecycle.EXIT_ACQUIRE,
        lifecycle.EXIT_INSTALL,
        lifecycle.EXIT_VERIFY,
        lifecycle.EXIT_REQUIRES_RESTART,
    ) == (0, 10, 20, 30, 40, 50)


def test_sidecar_round_trip_preserves_shared_init_and_restores_receipt_backup(
    tmp_path: Path,
) -> None:
    from dcc_mcp_zbrush.install_lifecycle import LifecycleRequest, run_lifecycle

    asset_dir = tmp_path / "ZBrushData2026" / "ZStartup"
    python_dir = asset_dir / "Python"
    python_dir.mkdir(parents=True)
    shared_init = python_dir / "init.py"
    original_init = b"# studio-owned bootstrap\nprint('keep me')\n"
    shared_init.write_bytes(original_init)

    dcc_path = tmp_path / "ZBrush 2026.1" / "ZBrush.exe"
    dcc_path.parent.mkdir()
    dcc_path.touch()
    archive = tmp_path / "dcc-mcp-zbrush-plugin-v0.2.24.zip"
    expected_sha256 = _build_sidecar_archive(archive)

    request = LifecycleRequest(
        operation="install",
        mode="sidecar",
        version="0.2.24",
        dcc_path=dcc_path,
        python_path=Path(sys.executable),
        asset_dir=asset_dir,
        yes=True,
    )
    installed = run_lifecycle(
        request,
        plugin_archive=archive,
        expected_sha256=expected_sha256,
    )

    assert installed["exit_code"] == 0
    installed_init = shared_init.read_bytes()
    assert original_init in installed_init
    assert b"dcc-mcp-zbrush managed bootstrap" in installed_init
    assert (python_dir / "dcc_mcp_zbrush_socket_bridge.py").is_file()

    receipt_path = asset_dir / ".dcc-mcp" / "receipts" / "zbrush.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    backup_path = asset_dir / receipt["shared_init"]["backup"]
    assert backup_path.read_bytes() == original_init

    uninstalled = run_lifecycle(replace(request, operation="uninstall"))

    assert uninstalled["exit_code"] == 0
    assert shared_init.read_bytes() == original_init
    assert not (python_dir / "dcc_mcp_zbrush_socket_bridge.py").exists()
    assert not receipt_path.exists()


def _request(tmp_path: Path, *, operation: str = "install", version: str = "0.2.24"):
    from dcc_mcp_zbrush.install_lifecycle import LifecycleRequest

    dcc_path = tmp_path / "ZBrush 2026.1" / "ZBrush.exe"
    dcc_path.parent.mkdir(parents=True, exist_ok=True)
    dcc_path.touch(exist_ok=True)
    return LifecycleRequest(
        operation=operation,
        mode="sidecar",
        version=version,
        dcc_path=dcc_path,
        python_path=Path(sys.executable),
        asset_dir=tmp_path / "ZBrushData2026" / "ZStartup",
        yes=True,
    )


@pytest.mark.parametrize("version", ["", "latest"])
def test_unfixed_versions_fail_before_the_install_cache_is_created(tmp_path: Path, version: str) -> None:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    request = _request(tmp_path, version=version)
    cache = tmp_path / "cache"

    result = run_lifecycle(request, cache_root=cache)

    assert result["exit_code"] == 10
    assert result["stage"] == "version"
    assert not cache.exists()
    assert not request.asset_dir.exists()


def test_checksum_mismatch_fails_closed_before_shared_startup_changes(tmp_path: Path) -> None:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    request = _request(tmp_path)
    request.asset_dir.mkdir(parents=True)
    shared_init = request.asset_dir / "Python" / "init.py"
    shared_init.parent.mkdir()
    original = b"# studio startup\n"
    shared_init.write_bytes(original)
    archive = tmp_path / "plugin.zip"
    _build_sidecar_archive(archive)

    result = run_lifecycle(request, plugin_archive=archive, expected_sha256="0" * 64)

    assert result["exit_code"] == 20
    assert result["stage"] == "integrity"
    assert shared_init.read_bytes() == original
    assert not (request.asset_dir / ".dcc-mcp" / "receipts" / "zbrush.json").exists()


def test_host_unavailable_is_a_verify_failure_exit_40(tmp_path: Path) -> None:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    request = _request(tmp_path)
    archive = tmp_path / "plugin.zip"
    digest = _build_sidecar_archive(archive)
    assert run_lifecycle(request, plugin_archive=archive, expected_sha256=digest)["exit_code"] == 0

    result = run_lifecycle(replace(request, operation="verify", socket_port=1))

    assert result["exit_code"] == 40
    assert result["status"] == "host_unavailable"


def test_loaded_native_files_use_requires_restart_exit_50(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import dcc_mcp_core.install_lifecycle as core_lifecycle

    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    request = _request(tmp_path)
    request.asset_dir.mkdir(parents=True)
    monkeypatch.setattr(core_lifecycle, "inspect_install_root", lambda _path: {"requires_restart": True})

    result = run_lifecycle(request)

    assert result["exit_code"] == 50
    assert result["stage"] == "lock_check"


def test_partial_install_without_receipt_is_reported_and_not_repaired_implicitly(tmp_path: Path) -> None:
    from dcc_mcp_zbrush.install_lifecycle import MANAGED_BLOCK, run_lifecycle

    request = replace(_request(tmp_path), operation="status")
    shared_init = request.asset_dir / "Python" / "init.py"
    shared_init.parent.mkdir(parents=True)
    shared_init.write_bytes(MANAGED_BLOCK)

    result = run_lifecycle(request)

    assert result["exit_code"] == 10
    assert result["status"] == "partial"
    assert shared_init.read_bytes() == MANAGED_BLOCK


def test_uninstall_removes_only_the_managed_block_after_user_edits(tmp_path: Path) -> None:
    from dcc_mcp_zbrush.install_lifecycle import MANAGED_BLOCK, run_lifecycle

    request = _request(tmp_path)
    shared_init = request.asset_dir / "Python" / "init.py"
    shared_init.parent.mkdir(parents=True)
    original = b"# studio startup"
    shared_init.write_bytes(original)
    archive = tmp_path / "plugin.zip"
    digest = _build_sidecar_archive(archive)
    assert run_lifecycle(request, plugin_archive=archive, expected_sha256=digest)["exit_code"] == 0
    shared_init.write_bytes(shared_init.read_bytes() + b"# user edit after install\n")

    result = run_lifecycle(replace(request, operation="uninstall"))

    assert result["exit_code"] == 0
    assert shared_init.read_bytes() == original + b"\n# user edit after install\n"
    assert MANAGED_BLOCK not in shared_init.read_bytes()


def test_upgrade_keeps_original_backup_and_is_idempotent(tmp_path: Path) -> None:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    request = _request(tmp_path)
    shared_init = request.asset_dir / "Python" / "init.py"
    shared_init.parent.mkdir(parents=True)
    original = b"# studio startup\n"
    shared_init.write_bytes(original)
    first = tmp_path / "first.zip"
    first_digest = _build_sidecar_archive(first)
    assert run_lifecycle(request, plugin_archive=first, expected_sha256=first_digest)["changed"] is True
    assert run_lifecycle(request, plugin_archive=first, expected_sha256=first_digest)["changed"] is False

    second = tmp_path / "second.zip"
    with zipfile.ZipFile(second, "w") as archive:
        archive.writestr("sidecar/mcp_socket_bridge.py", "BRIDGE_SENTINEL = 'upgraded'\n")
    second_digest = hashlib.sha256(second.read_bytes()).hexdigest()
    upgraded = run_lifecycle(
        replace(request, operation="upgrade", version="0.2.25"),
        plugin_archive=second,
        expected_sha256=second_digest,
    )

    assert upgraded["exit_code"] == 0
    assert "upgraded" in (request.asset_dir / "Python" / "dcc_mcp_zbrush_socket_bridge.py").read_text()
    assert run_lifecycle(replace(request, operation="uninstall"))["exit_code"] == 0
    assert shared_init.read_bytes() == original


def test_upgrade_does_not_absorb_post_install_shared_init_edits(tmp_path: Path) -> None:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    request = _request(tmp_path)
    shared_init = request.asset_dir / "Python" / "init.py"
    shared_init.parent.mkdir(parents=True)
    original = b"# studio startup\n"
    shared_init.write_bytes(original)
    first = tmp_path / "first.zip"
    first_digest = _build_sidecar_archive(first)
    assert run_lifecycle(request, plugin_archive=first, expected_sha256=first_digest)["exit_code"] == 0
    user_edit = b"# edit after install\n"
    shared_init.write_bytes(shared_init.read_bytes() + user_edit)
    second = tmp_path / "second.zip"
    with zipfile.ZipFile(second, "w") as payload:
        payload.writestr("sidecar/mcp_socket_bridge.py", "BRIDGE_SENTINEL = 'upgraded'\n")
    second_digest = hashlib.sha256(second.read_bytes()).hexdigest()

    upgraded = run_lifecycle(
        replace(request, operation="upgrade", version="0.2.25"),
        plugin_archive=second,
        expected_sha256=second_digest,
    )
    uninstalled = run_lifecycle(replace(request, operation="uninstall"))

    assert upgraded["exit_code"] == 0
    assert uninstalled["exit_code"] == 0
    assert shared_init.read_bytes() == original + user_edit


def test_verify_reports_captured_bootstrap_error_without_claiming_usable(tmp_path: Path) -> None:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    request = _request(tmp_path)
    archive = tmp_path / "plugin.zip"
    digest = _build_sidecar_archive(archive)
    assert run_lifecycle(request, plugin_archive=archive, expected_sha256=digest)["exit_code"] == 0
    error_path = request.asset_dir / ".dcc-mcp" / "bootstrap-errors.jsonl"
    error_path.write_text('{"stage":"sidecar_bootstrap","reason":"SDK unavailable"}\n', encoding="utf-8")

    result = run_lifecycle(replace(request, operation="verify"))

    assert result["exit_code"] == 40
    assert result["status"] == "bootstrap_failed"
    assert result["directly_usable"] is False
    assert result["reason"] == "SDK unavailable"


def test_standard_cli_emits_json_schema_and_stable_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dcc_mcp_zbrush.cli import main

    request = _request(tmp_path, operation="status")
    exit_code = main(
        [
            "status",
            "--json",
            "--dcc-path",
            str(request.dcc_path),
            "--python",
            str(request.python_path),
            "--asset-dir",
            str(request.asset_dir),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema_version"] == "1.0"
    assert payload["operation"] == "status"
    assert payload["status"] == "not_installed"
    assert payload["next_steps"] == []


def test_embedded_mode_round_trip_uses_staged_tree_and_removes_managed_files(tmp_path: Path) -> None:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    request = replace(_request(tmp_path), mode="embedded")
    existing_package = request.asset_dir / "dcc_mcp_zbrush"
    existing_entry = request.asset_dir / "dcc_mcp_zbrush_plugin.py"
    archive = tmp_path / "embedded.zip"
    with zipfile.ZipFile(archive, "w") as payload:
        payload.writestr("embedded/dcc_mcp_zbrush/__init__.py", "PLUGIN = True\n")
        payload.writestr("embedded/dcc_mcp_zbrush_plugin.py", "PLUGIN_ENTRY = True\n")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    installed = run_lifecycle(request, plugin_archive=archive, expected_sha256=digest)
    uninstalled = run_lifecycle(replace(request, operation="uninstall"))

    assert installed["exit_code"] == 0
    assert uninstalled["exit_code"] == 0
    assert not existing_package.exists()
    assert not existing_entry.exists()


def test_receipt_write_failure_rolls_back_shared_startup_and_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    request = _request(tmp_path)
    shared_init = request.asset_dir / "Python" / "init.py"
    shared_init.parent.mkdir(parents=True)
    original = b"# studio startup\n"
    shared_init.write_bytes(original)
    archive = tmp_path / "plugin.zip"
    digest = _build_sidecar_archive(archive)
    real_write = lifecycle._write_json

    def fail_receipt(path: Path, payload: dict) -> None:
        if path.name == "zbrush.json":
            raise PermissionError("simulated Windows lock")
        real_write(path, payload)

    monkeypatch.setattr(lifecycle, "_write_json", fail_receipt)
    result = lifecycle.run_lifecycle(request, plugin_archive=archive, expected_sha256=digest)

    assert result["exit_code"] == 30
    assert result["stage"] == "locked_files"
    assert shared_init.read_bytes() == original
    assert not (request.asset_dir / "Python" / "dcc_mcp_zbrush_socket_bridge.py").exists()
    assert not (request.asset_dir / ".dcc-mcp" / "receipts" / "zbrush.json").exists()


def test_failed_upgrade_restores_previous_payload_and_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    request = _request(tmp_path)
    first = tmp_path / "first.zip"
    first_digest = _build_sidecar_archive(first)
    assert lifecycle.run_lifecycle(request, plugin_archive=first, expected_sha256=first_digest)["exit_code"] == 0
    bridge_path = request.asset_dir / "Python" / "dcc_mcp_zbrush_socket_bridge.py"
    receipt_path = request.asset_dir / ".dcc-mcp" / "receipts" / "zbrush.json"
    original_bridge = bridge_path.read_bytes()
    original_receipt = receipt_path.read_bytes()
    second = tmp_path / "second.zip"
    with zipfile.ZipFile(second, "w") as payload:
        payload.writestr("sidecar/mcp_socket_bridge.py", "BRIDGE_SENTINEL = 'new'\n")
    digest = hashlib.sha256(second.read_bytes()).hexdigest()
    real_write = lifecycle._write_json

    def fail_receipt(path: Path, payload: dict) -> None:
        if path == receipt_path:
            raise PermissionError("simulated Windows lock")
        real_write(path, payload)

    monkeypatch.setattr(lifecycle, "_write_json", fail_receipt)
    result = lifecycle.run_lifecycle(
        replace(request, operation="upgrade", version="0.2.25"),
        plugin_archive=second,
        expected_sha256=digest,
    )

    assert result["exit_code"] == 30
    assert bridge_path.read_bytes() == original_bridge
    assert receipt_path.read_bytes() == original_receipt


def test_receipt_paths_cannot_escape_the_asset_directory(tmp_path: Path) -> None:
    from dcc_mcp_zbrush.install_lifecycle import MANAGED_BLOCK, run_lifecycle

    request = replace(_request(tmp_path), operation="status")
    shared_init = request.asset_dir / "Python" / "init.py"
    shared_init.parent.mkdir(parents=True)
    shared_init.write_bytes(MANAGED_BLOCK)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    escaped = Path(os.path.relpath(victim, request.asset_dir)).as_posix()
    receipt_path = request.asset_dir / ".dcc-mcp" / "receipts" / "zbrush.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "adapter": "zbrush",
                "version": "0.2.24",
                "mode": "sidecar",
                "backup_root": ".dcc-mcp/backups/test",
                "shared_init": {
                    "path": "Python/init.py",
                    "existed": False,
                    "installed_sha256": hashlib.sha256(MANAGED_BLOCK).hexdigest(),
                },
                "managed_files": [
                    {
                        "path": escaped,
                        "existed": False,
                        "installed_sha256": hashlib.sha256(b"keep").hexdigest(),
                    }
                ],
                "managed_trees": [],
            }
        ),
        encoding="utf-8",
    )

    result = run_lifecycle(request)

    assert result["exit_code"] == 10
    assert result["stage"] == "receipt"
    assert victim.read_text(encoding="utf-8") == "keep"


def test_managed_import_block_captures_bridge_import_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dcc_mcp_zbrush.install_lifecycle import MANAGED_BLOCK

    error_log = tmp_path / "bootstrap-errors.jsonl"
    monkeypatch.setenv("DCC_MCP_ZBRUSH_BOOTSTRAP_ERRORS", str(error_log))
    with pytest.raises(ModuleNotFoundError):
        exec(compile(MANAGED_BLOCK, str(tmp_path / "Python" / "init.py"), "exec"), {"__file__": "init.py"})

    payload = json.loads(error_log.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["stage"] == "sidecar_import"
    assert payload["exception_type"] == "ModuleNotFoundError"


def test_embedded_file_failure_rolls_back_staged_package_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    request = replace(_request(tmp_path), mode="embedded")
    archive = tmp_path / "embedded.zip"
    with zipfile.ZipFile(archive, "w") as payload:
        payload.writestr("embedded/dcc_mcp_zbrush/__init__.py", "PLUGIN = True\n")
        payload.writestr("embedded/dcc_mcp_zbrush_plugin.py", "PLUGIN_ENTRY = True\n")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    real_write = lifecycle._atomic_write

    def fail_entry(path: Path, data: bytes) -> None:
        if path.name == "dcc_mcp_zbrush_plugin.py":
            raise PermissionError("simulated Windows lock")
        real_write(path, data)

    monkeypatch.setattr(lifecycle, "_atomic_write", fail_entry)
    result = lifecycle.run_lifecycle(request, plugin_archive=archive, expected_sha256=digest)

    assert result["exit_code"] == 30
    assert not (request.asset_dir / "dcc_mcp_zbrush").exists()
    assert not (request.asset_dir / "dcc_mcp_zbrush_plugin.py").exists()
    assert not (request.asset_dir / ".dcc-mcp" / "receipts" / "zbrush.json").exists()
