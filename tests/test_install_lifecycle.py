from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

CORE_2320_SCHEMA_SHA256 = "3ca25788439917b4d4c0617230a762f9797756b5b54f45c8c4149f975b90f904"


@pytest.fixture(autouse=True)
def _supported_host_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle.sys, "platform", "win32")
    monkeypatch.setattr(
        lifecycle,
        "_windows_product_info",
        lambda _path: {"product_name": "Maxon ZBrush", "version": "2026.1.0.0"},
    )
    monkeypatch.setattr(
        lifecycle,
        "_windows_authenticode_info",
        lambda _path: {"status": "Valid", "subject": "CN=Maxon Computer GmbH"},
    )


def _write_fake_pe(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ" + b"\0" * 126)


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


def test_public_install_plan_matches_exact_core_2320_draft_schema(tmp_path: Path) -> None:
    from dcc_mcp_zbrush.install_contract import load_install_sop_schema
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    schema_path = (
        Path(__file__).parents[1] / "src" / "dcc_mcp_zbrush" / "schemas" / "adapter-install-sop-v1.schema.json"
    )
    schema_bytes = schema_path.read_bytes()
    assert hashlib.sha256(schema_bytes).hexdigest() == CORE_2320_SCHEMA_SHA256
    schema = load_install_sop_schema()
    Draft202012Validator.check_schema(schema)

    result = run_lifecycle(replace(_request(tmp_path), yes=False, dry_run=True))

    Draft202012Validator(schema).validate(result)


def test_published_core_floor_is_projected_to_all_public_surfaces() -> None:
    from dcc_mcp_zbrush.install_lifecycle import MINIMUM_CORE

    repository = Path(__file__).parents[1]
    floor = ".".join(map(str, MINIMUM_CORE))

    assert MINIMUM_CORE == (0, 20, 14)
    assert f"dcc-mcp-core>={floor},<1.0.0" in (repository / "pyproject.toml").read_text(encoding="utf-8")
    assert f"dcc-mcp-core >= {floor}" in (repository / "README.md").read_text(encoding="utf-8")
    assert f"dcc-mcp-core >= {floor}" in (repository / "install.md").read_text(encoding="utf-8")

    skill_paths = sorted((repository / "src" / "dcc_mcp_zbrush" / "skills").glob("*/SKILL.md"))
    assert skill_paths
    for skill_path in skill_paths:
        assert f"dcc-mcp-core {floor}+" in skill_path.read_text(encoding="utf-8"), skill_path


def test_sidecar_round_trip_preserves_shared_init_and_restores_receipt_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dcc_mcp_zbrush.install_lifecycle import LifecycleRequest, run_lifecycle

    asset_dir = tmp_path / "ZBrushData2026" / "ZStartup"
    python_dir = asset_dir / "Python"
    python_dir.mkdir(parents=True)
    shared_init = python_dir / "init.py"
    original_init = b"# studio-owned bootstrap\nprint('keep me')\n"
    shared_init.write_bytes(original_init)

    dcc_path = tmp_path / "ZBrush 2026.1" / "ZBrush.exe"
    _write_fake_pe(dcc_path)
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

    assert installed["exit_code"] == 50
    installed_init = shared_init.read_bytes()
    assert original_init in installed_init
    assert b"dcc-mcp-zbrush managed bootstrap" in installed_init
    assert (python_dir / "dcc_mcp_zbrush_socket_bridge.py").is_file()

    receipt_path = asset_dir / ".dcc-mcp" / "receipts" / "zbrush.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    backup_path = asset_dir / receipt["shared_init"]["backup"]
    assert backup_path.read_bytes() == original_init
    _, session = _pending_sidecar(request, archive, expected_sha256)
    _bind_sidecar(monkeypatch, request, session)
    assert run_lifecycle(replace(request, operation="verify"))["exit_code"] == 0

    uninstalled = run_lifecycle(replace(request, operation="uninstall"))

    assert uninstalled["exit_code"] == 0
    assert shared_init.read_bytes() == original_init
    assert not (python_dir / "dcc_mcp_zbrush_socket_bridge.py").exists()
    assert not receipt_path.exists()


def _request(tmp_path: Path, *, operation: str = "install", version: str = "0.2.24"):
    from dcc_mcp_zbrush.install_lifecycle import LifecycleRequest

    dcc_path = tmp_path / "ZBrush 2026.1" / "ZBrush.exe"
    _write_fake_pe(dcc_path)
    return LifecycleRequest(
        operation=operation,
        mode="sidecar",
        version=version,
        dcc_path=dcc_path,
        python_path=Path(sys.executable),
        asset_dir=tmp_path / "ZBrushData2026" / "ZStartup",
        yes=True,
    )


def _pending_sidecar(request, archive: Path, digest: str) -> tuple[dict, dict]:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    result = run_lifecycle(request, plugin_archive=archive, expected_sha256=digest)
    assert result["exit_code"] == 50
    receipt_path = request.asset_dir / ".dcc-mcp" / "receipts" / "zbrush.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    bridge_path = request.asset_dir / "Python" / "dcc_mcp_zbrush_socket_bridge.py"
    zbrush_origin = Path(receipt["dcc_root"]) / "Python" / "zbrush" / "commands.pyd"
    zbrush_origin.parent.mkdir(parents=True, exist_ok=True)
    zbrush_origin.write_bytes(b"native-zbrush-sdk")
    session = {
        "adapter_version": receipt["version"],
        "bridge_instance_id": "11111111-1111-1111-1111-111111111111",
        "bridge_module_path": str(bridge_path),
        "bridge_module_sha256": hashlib.sha256(bridge_path.read_bytes()).hexdigest(),
        "install_id": receipt["install_id"],
        "pid": 4242,
        "selected_dcc_path": receipt["dcc_path"],
        "selected_dcc_sha256": receipt["dcc_sha256"],
        "socket_endpoint": receipt["socket_endpoint"],
        "zbrush_commands_origin": str(zbrush_origin),
        "zbrush_version": "2026.1",
    }
    return receipt, session


def _bind_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    request,
    session: dict,
    *,
    starts: tuple[str, ...] = ("start-4242",),
) -> None:
    import dcc_mcp_zbrush.bridge as bridge_module
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    class BoundBridge:
        def __init__(self, *_args, **_kwargs):
            pass

        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def get_session_info(self) -> dict:
            return dict(session)

    identities = iter(starts)
    monkeypatch.setattr(bridge_module, "SocketBridge", BoundBridge)
    monkeypatch.setattr(lifecycle, "_process_executable", lambda _pid: Path(request.dcc_path))
    monkeypatch.setattr(lifecycle, "_process_start_identity", lambda _pid: next(identities))


def _commit_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    request,
    archive: Path,
    digest: str,
) -> tuple[dict, dict]:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    receipt, session = _pending_sidecar(request, archive, digest)
    _bind_sidecar(monkeypatch, request, session)
    verified = run_lifecycle(replace(request, operation="verify"))
    assert verified["exit_code"] == 0
    return receipt, session


def _embedded_readiness(request, receipt: dict) -> dict:
    adapter_path = request.asset_dir / "dcc_mcp_zbrush" / "__init__.py"
    zbrush_origin = Path(receipt["dcc_root"]) / "Python" / "zbrush" / "commands.pyd"
    zbrush_origin.parent.mkdir(parents=True, exist_ok=True)
    zbrush_origin.write_bytes(b"native-zbrush-sdk")
    instance_id = "22222222-2222-2222-2222-222222222222"
    mcp_url = "http://127.0.0.1:45678/mcp"
    session = {
        "adapter_module_path": str(adapter_path),
        "adapter_module_sha256": hashlib.sha256(adapter_path.read_bytes()).hexdigest(),
        "adapter_version": receipt["version"],
        "install_id": receipt["install_id"],
        "instance_id": instance_id,
        "mcp_url": mcp_url,
        "pid": 5252,
        "process_executable": receipt["dcc_path"],
        "selected_dcc_path": receipt["dcc_path"],
        "selected_dcc_sha256": receipt["dcc_sha256"],
        "zbrush_commands_origin": str(zbrush_origin),
        "zbrush_version": "2026.1",
    }
    return {
        "success": True,
        "entry": {"dcc_pid": 5252, "instance_id": instance_id, "mcp_url": mcp_url},
        "probe": {"result": {"structuredContent": session}},
    }


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


@pytest.mark.parametrize(
    "version",
    [
        "0.2.24rc1",
        "garbage0.2.24",
        "0.2.24suffix",
        " 0.2.24",
        "00.2.24",
        "0.2",
        "0.2.24.1",
        "1." + "9" * 5000 + ".0",
    ],
)
def test_public_lifecycle_rejects_noncanonical_or_unbounded_adapter_versions(tmp_path: Path, version: str) -> None:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    result = run_lifecycle(replace(_request(tmp_path, version=version), yes=False, dry_run=True))

    assert result["exit_code"] == 10
    assert result["stage"] == "version"
    assert result["status"] == "failed"


@pytest.mark.parametrize(
    "core_version",
    ["0.20.13", "0.20.14rc1", "garbage0.20.14", "0.20", "1." + "9" * 5000 + ".0"],
)
def test_public_lifecycle_rejects_noncanonical_or_unbounded_core_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, core_version: str
) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "package_version", lambda _name: core_version)

    result = lifecycle.run_lifecycle(replace(_request(tmp_path), yes=False, dry_run=True))

    assert result["exit_code"] == 10
    assert result["stage"] == "core"


def test_preflight_rejects_zero_byte_or_name_only_zbrush_executable(tmp_path: Path) -> None:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    request = _request(tmp_path)
    assert request.dcc_path is not None
    request.dcc_path.write_bytes(b"")

    result = run_lifecycle(replace(request, yes=False, dry_run=True))

    assert result["exit_code"] == 10
    assert result["stage"] == "host"


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
    assert run_lifecycle(request, plugin_archive=archive, expected_sha256=digest)["exit_code"] == 50

    result = run_lifecycle(replace(request, operation="verify", socket_port=1))

    assert result["exit_code"] == 40
    assert result["state"] == "host_unavailable"
    assert not (request.asset_dir / ".dcc-mcp" / "receipts" / "zbrush.json").exists()


def test_verify_rejects_foreign_socket_session_without_exact_install_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dcc_mcp_zbrush.bridge as bridge_module
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    request = _request(tmp_path)
    archive = tmp_path / "plugin.zip"
    digest = _build_sidecar_archive(archive)
    assert lifecycle.run_lifecycle(request, plugin_archive=archive, expected_sha256=digest)["exit_code"] == 50

    class ForeignBridge:
        def __init__(self, *_args, **_kwargs):
            pass

        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def get_session_info(self) -> dict:
            return {
                "install_id": "foreign",
                "instance_id": "11111111-1111-1111-1111-111111111111",
                "pid": 4242,
                "socket_endpoint": "127.0.0.1:9876",
                "zbrush_version": "2026.1.0.0",
            }

    monkeypatch.setattr(bridge_module, "SocketBridge", ForeignBridge)

    result = lifecycle.run_lifecycle(replace(request, operation="verify"))

    assert result["exit_code"] == 40
    assert result["stage"] == "host_identity"
    assert result["directly_usable"] is False


def _installed_sidecar_session(tmp_path: Path) -> tuple[object, dict, dict]:
    request = _request(tmp_path)
    archive = tmp_path / "plugin.zip"
    digest = _build_sidecar_archive(archive)
    receipt, session = _pending_sidecar(request, archive, digest)
    return request, receipt, session


def test_verify_persists_exact_runtime_identity_and_rejects_pid_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    request, _receipt, session = _installed_sidecar_session(tmp_path)

    _bind_sidecar(monkeypatch, request, session, starts=("start-4242", "replacement-start-4242"))

    first = lifecycle.run_lifecycle(replace(request, operation="verify"))
    second = lifecycle.run_lifecycle(replace(request, operation="verify"))

    assert first["exit_code"] == 0
    stored = json.loads((request.asset_dir / ".dcc-mcp" / "receipts" / "zbrush.json").read_text(encoding="utf-8"))
    assert stored["runtime_identity"]["process_start_identity"] == "start-4242"
    assert second["exit_code"] == 40
    assert second["stage"] == "host_identity"


def test_process_identity_timeout_is_stable_verify_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    request, _receipt, session = _installed_sidecar_session(tmp_path)
    _bind_sidecar(monkeypatch, request, session)

    def timeout(_pid: int) -> str:
        raise subprocess.TimeoutExpired(["ps"], 5)

    monkeypatch.setattr(lifecycle, "_process_start_identity", timeout)
    result = lifecycle.run_lifecycle(replace(request, operation="verify"))

    assert result["exit_code"] == 40
    assert result["stage"] == "host_identity"
    assert result["status"] == "failed"


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


def test_uninstall_removes_only_the_managed_block_after_user_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dcc_mcp_zbrush.install_lifecycle import MANAGED_BLOCK, run_lifecycle

    request = _request(tmp_path)
    shared_init = request.asset_dir / "Python" / "init.py"
    shared_init.parent.mkdir(parents=True)
    original = b"# studio startup"
    shared_init.write_bytes(original)
    archive = tmp_path / "plugin.zip"
    digest = _build_sidecar_archive(archive)
    _commit_sidecar(monkeypatch, request, archive, digest)
    shared_init.write_bytes(shared_init.read_bytes() + b"# user edit after install\n")

    result = run_lifecycle(replace(request, operation="uninstall"))

    assert result["exit_code"] == 0
    assert shared_init.read_bytes() == original + b"\n# user edit after install\n"
    assert MANAGED_BLOCK not in shared_init.read_bytes()


def test_upgrade_keeps_original_backup_and_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    request = _request(tmp_path)
    shared_init = request.asset_dir / "Python" / "init.py"
    shared_init.parent.mkdir(parents=True)
    original = b"# studio startup\n"
    shared_init.write_bytes(original)
    first = tmp_path / "first.zip"
    first_digest = _build_sidecar_archive(first)
    _commit_sidecar(monkeypatch, request, first, first_digest)
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

    assert upgraded["exit_code"] == 50
    receipt, session = _pending_sidecar(replace(request, operation="upgrade", version="0.2.25"), second, second_digest)
    _bind_sidecar(monkeypatch, replace(request, version="0.2.25"), session)
    assert run_lifecycle(replace(request, operation="verify", version="0.2.25"))["exit_code"] == 0
    assert "upgraded" in (request.asset_dir / "Python" / "dcc_mcp_zbrush_socket_bridge.py").read_text()
    assert run_lifecycle(replace(request, operation="uninstall"))["exit_code"] == 0
    assert shared_init.read_bytes() == original


def test_upgrade_does_not_absorb_post_install_shared_init_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    request = _request(tmp_path)
    shared_init = request.asset_dir / "Python" / "init.py"
    shared_init.parent.mkdir(parents=True)
    original = b"# studio startup\n"
    shared_init.write_bytes(original)
    first = tmp_path / "first.zip"
    first_digest = _build_sidecar_archive(first)
    _commit_sidecar(monkeypatch, request, first, first_digest)
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
    _, session = _pending_sidecar(replace(request, operation="upgrade", version="0.2.25"), second, second_digest)
    _bind_sidecar(monkeypatch, replace(request, version="0.2.25"), session)
    assert run_lifecycle(replace(request, operation="verify", version="0.2.25"))["exit_code"] == 0
    uninstalled = run_lifecycle(replace(request, operation="uninstall"))

    assert upgraded["exit_code"] == 50
    assert uninstalled["exit_code"] == 0
    assert shared_init.read_bytes() == original + user_edit


def test_verify_reports_captured_bootstrap_error_without_claiming_usable(tmp_path: Path) -> None:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    request = _request(tmp_path)
    archive = tmp_path / "plugin.zip"
    digest = _build_sidecar_archive(archive)
    assert run_lifecycle(request, plugin_archive=archive, expected_sha256=digest)["exit_code"] == 50
    error_path = request.asset_dir / ".dcc-mcp" / "bootstrap-errors.jsonl"
    error_path.write_text('{"stage":"sidecar_bootstrap","reason":"SDK unavailable"}\n', encoding="utf-8")

    result = run_lifecycle(replace(request, operation="verify"))

    assert result["exit_code"] == 40
    assert result["state"] == "bootstrap_failed"
    assert result["directly_usable"] is False
    assert result["reason"].startswith("SDK unavailable")


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
    assert payload["schema_version"] == 1
    assert payload["operation"] == "status"
    assert payload["status"] == "ok"
    assert payload["state"] == "not_installed"
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

    assert installed["exit_code"] == 50
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
    _commit_sidecar(monkeypatch, request, first, first_digest)
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
                "schema_version": 1,
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


def test_every_public_verb_emits_draft_valid_success_and_failure_envelopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle
    from dcc_mcp_zbrush.install_contract import load_install_sop_schema

    validator = Draft202012Validator(load_install_sop_schema())
    planned = []
    for operation in ("install", "status", "uninstall", "upgrade"):
        request = replace(_request(tmp_path / operation, operation=operation), yes=False, dry_run=True)
        planned.append(lifecycle.run_lifecycle(request))
    request = _request(tmp_path / "verify")
    archive = tmp_path / "verify.zip"
    digest = _build_sidecar_archive(archive)
    _, session = _pending_sidecar(request, archive, digest)
    _bind_sidecar(monkeypatch, request, session)
    planned.append(lifecycle.run_lifecycle(replace(request, operation="verify")))
    for operation in ("install", "status", "verify", "uninstall", "upgrade"):
        failed = lifecycle.run_lifecycle(
            replace(_request(tmp_path / f"failed-{operation}", operation=operation), version="0.2.24rc1")
        )
        validator.validate(failed)
        assert failed["status"] == "failed"
    for payload in planned:
        validator.validate(payload)
    assert {payload["operation"] for payload in planned} == {"install", "status", "verify", "uninstall", "upgrade"}
    assert all(payload["status"] != "failed" for payload in planned)


def test_upgrade_readiness_failure_restores_exact_committed_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dcc_mcp_zbrush.bridge as bridge_module
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    request = _request(tmp_path)
    first = tmp_path / "first.zip"
    first_digest = _build_sidecar_archive(first)
    _commit_sidecar(monkeypatch, request, first, first_digest)
    receipt_path = request.asset_dir / ".dcc-mcp" / "receipts" / "zbrush.json"
    bridge_path = request.asset_dir / "Python" / "dcc_mcp_zbrush_socket_bridge.py"
    identity_path = request.asset_dir / "Python" / "dcc_mcp_zbrush_install_identity.json"
    exact_before = (receipt_path.read_bytes(), bridge_path.read_bytes(), identity_path.read_bytes())
    second = tmp_path / "second.zip"
    with zipfile.ZipFile(second, "w") as payload:
        payload.writestr("sidecar/mcp_socket_bridge.py", "BRIDGE_SENTINEL = 'new'\n")
    second_digest = hashlib.sha256(second.read_bytes()).hexdigest()
    upgraded = run_lifecycle(
        replace(request, operation="upgrade", version="0.2.25"),
        plugin_archive=second,
        expected_sha256=second_digest,
    )
    assert upgraded["exit_code"] == 50

    class UnavailableBridge:
        def __init__(self, *_args, **_kwargs):
            pass

        def connect(self) -> None:
            raise OSError("no matching ZBrush")

    monkeypatch.setattr(bridge_module, "SocketBridge", UnavailableBridge)
    failed = run_lifecycle(replace(request, operation="verify", version="0.2.25"))

    assert failed["exit_code"] == 40
    assert (receipt_path.read_bytes(), bridge_path.read_bytes(), identity_path.read_bytes()) == exact_before
    assert not (request.asset_dir / ".dcc-mcp" / "transactions").exists()


def test_tampered_transaction_recovery_is_rejected_before_destructive_restore(tmp_path: Path) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    asset_dir = tmp_path / "ZStartup"
    target = asset_dir / lifecycle.SIDECAR_RELATIVE
    target.parent.mkdir(parents=True)
    target.write_bytes(b"prior managed bytes")
    transaction = lifecycle._capture_transaction(
        asset_dir,
        "tamper-test",
        asset_dir / ".dcc-mcp" / "backups" / "new",
    )
    snapshot = next(item for item in transaction["snapshots"] if item["path"] == lifecycle.SIDECAR_RELATIVE.as_posix())
    backup = asset_dir / snapshot["backup"]
    target.write_bytes(b"candidate must survive failed recovery validation")
    backup.write_bytes(b"tampered recovery")

    with pytest.raises(lifecycle.LifecycleFailure, match="Recovery"):
        lifecycle._restore_transaction(asset_dir, transaction)

    assert target.read_bytes() == b"candidate must survive failed recovery validation"
    assert (asset_dir / transaction["recovery_root"]).is_dir()


def test_partial_transaction_restore_retains_complete_validated_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    asset_dir = tmp_path / "ZStartup"
    first = asset_dir / lifecycle.IDENTITY_RELATIVE
    second = asset_dir / lifecycle.SIDECAR_RELATIVE
    first.parent.mkdir(parents=True)
    first.write_bytes(b"prior identity")
    second.write_bytes(b"prior bridge")
    transaction = lifecycle._capture_transaction(
        asset_dir,
        "partial-test",
        asset_dir / ".dcc-mcp" / "backups" / "new",
    )
    first.write_bytes(b"candidate identity")
    second.write_bytes(b"candidate bridge")
    original_atomic_write = lifecycle._atomic_write
    restored = 0

    def fail_second_restore(path: Path, data: bytes) -> None:
        nonlocal restored
        if path in {first, second}:
            restored += 1
            if restored == 2:
                raise PermissionError("injected partial restore")
        original_atomic_write(path, data)

    monkeypatch.setattr(lifecycle, "_atomic_write", fail_second_restore)

    with pytest.raises(PermissionError, match="partial restore"):
        lifecycle._restore_transaction(asset_dir, transaction)

    recovery_root = asset_dir / transaction["recovery_root"]
    assert recovery_root.is_dir()
    lifecycle._validate_transaction_recovery(asset_dir, transaction)
    restored_values = [path.read_bytes() if path.is_file() else None for path in (first, second)]
    assert sum(value in {b"prior identity", b"prior bridge"} for value in restored_values) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode and relative-link recovery contract")
def test_transaction_recovery_restores_directory_file_link_and_posix_modes(tmp_path: Path) -> None:
    import stat

    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    asset_dir = tmp_path / "ZStartup"
    package = asset_dir / "dcc_mcp_zbrush"
    nested = package / "nested"
    nested.mkdir(parents=True)
    module = nested / "module.py"
    module.write_bytes(b"prior module")
    alias = package / "alias.py"
    os.symlink("nested/module.py", alias)
    package.chmod(0o711)
    nested.chmod(0o750)
    module.chmod(0o640)
    transaction = lifecycle._capture_transaction(
        asset_dir,
        "mode-test",
        asset_dir / ".dcc-mcp" / "backups" / "new",
    )
    package_snapshot = next(item for item in transaction["snapshots"] if item["path"] == "dcc_mcp_zbrush")
    link_entry = next(item for item in package_snapshot["manifest"] if item["path"] == "alias.py")
    assert link_entry["kind"] == "link"
    assert link_entry["target"] == "nested/module.py"
    assert isinstance(link_entry["mode"], int)
    lifecycle._remove_path(package)
    package.mkdir()
    (package / "candidate.py").write_bytes(b"candidate")

    lifecycle._restore_transaction(asset_dir, transaction)

    assert stat.S_IMODE(package.stat().st_mode) == 0o711
    assert stat.S_IMODE(nested.stat().st_mode) == 0o750
    assert stat.S_IMODE(module.stat().st_mode) == 0o640
    assert alias.is_symlink() and os.readlink(alias) == "nested/module.py"


def test_upgrade_rejects_unowned_empty_directory_inside_managed_tree(tmp_path: Path) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    source = tmp_path / "candidate"
    destination = tmp_path / "installed"
    source.mkdir()
    destination.mkdir()
    (source / "owned.py").write_bytes(b"next")
    (destination / "owned.py").write_bytes(b"prior")
    previous = lifecycle._manifest(destination)
    operator_directory = destination / "operator-empty"
    operator_directory.mkdir()

    with pytest.raises(lifecycle.LifecycleFailure, match="unowned path"):
        lifecycle._install_managed_tree(source, destination, previous)

    assert operator_directory.is_dir()
    assert (destination / "owned.py").read_bytes() == b"prior"


def test_upgrade_rejects_unowned_link_inside_managed_tree(tmp_path: Path) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    source = tmp_path / "candidate"
    destination = tmp_path / "installed"
    source.mkdir()
    destination.mkdir()
    (source / "owned.py").write_bytes(b"next")
    (destination / "owned.py").write_bytes(b"prior")
    previous = lifecycle._manifest(destination)
    operator_link = destination / "operator-link.py"
    try:
        os.symlink("owned.py", operator_link)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(lifecycle.LifecycleFailure, match="unowned path"):
        lifecycle._install_managed_tree(source, destination, previous)

    assert operator_link.is_symlink()
    assert os.readlink(operator_link) == "owned.py"
    assert (destination / "owned.py").read_bytes() == b"prior"


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction contract")
def test_native_windows_junction_is_rejected_without_traversal_or_recovery_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stat

    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    outside = tmp_path / "operator-data"
    outside.mkdir()
    secret = outside / "secret.bin"
    secret.write_bytes(b"operator bytes must remain untouched")
    asset_dir = tmp_path / "ZStartup"
    package = asset_dir / "dcc_mcp_zbrush"
    asset_dir.mkdir()
    completed = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "mklink", "/J", str(package), str(outside)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.skip(f"native junction creation is unavailable: {completed.stderr.strip()}")

    metadata = os.lstat(package)
    assert metadata.st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT
    assert Path(os.path.realpath(package)) == Path(os.path.realpath(outside))
    monkeypatch.delattr(Path, "is_junction", raising=False)
    hashed: list[Path] = []
    copied: list[Path] = []
    original_sha256_file = lifecycle._sha256_file
    original_copyfile = lifecycle.shutil.copyfile

    def track_hash(path: Path) -> str:
        hashed.append(Path(path))
        return original_sha256_file(path)

    def track_copy(
        source: str | os.PathLike[str], destination: str | os.PathLike[str], *args: object, **kwargs: object
    ):
        source_path = Path(source)
        if source_path.is_relative_to(outside):
            copied.append(source_path)
        return original_copyfile(source, destination, *args, **kwargs)

    monkeypatch.setattr(lifecycle, "_sha256_file", track_hash)
    monkeypatch.setattr(lifecycle.shutil, "copyfile", track_copy)

    with pytest.raises(lifecycle.LifecycleFailure, match="reparse point"):
        lifecycle._manifest(asset_dir)
    with pytest.raises(lifecycle.LifecycleFailure, match="reparse point"):
        lifecycle._capture_transaction(
            asset_dir,
            "junction-test",
            asset_dir / ".dcc-mcp" / "backups" / "new",
        )

    recovery_parent = asset_dir / ".dcc-mcp" / "transactions"
    recovery_parent.mkdir(parents=True, exist_ok=True)
    recovery_root = recovery_parent / "junction-recovery"
    completed = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "mklink", "/J", str(recovery_root), str(outside)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.skip(f"native recovery junction creation is unavailable: {completed.stderr.strip()}")
    empty: list[dict[str, object]] = []
    with pytest.raises(lifecycle.LifecycleFailure, match="linked path|reparse point"):
        lifecycle._validate_transaction_recovery(
            asset_dir,
            {
                "recovery_root": recovery_root.relative_to(asset_dir).as_posix(),
                "snapshots": empty,
                "snapshots_sha256": lifecycle._typed_manifest_sha256(empty),
                "recovery_manifest": empty,
                "recovery_manifest_sha256": lifecycle._typed_manifest_sha256(empty),
            },
        )

    assert hashed == []
    assert copied == []
    assert secret.read_bytes() == b"operator bytes must remain untouched"
    assert os.lstat(package).st_reparse_tag == metadata.st_reparse_tag
    assert Path(os.path.realpath(package)) == Path(os.path.realpath(outside))
    assert not (asset_dir / ".dcc-mcp" / "transactions" / "junction-test").exists()

    lifecycle._remove_path(recovery_root)
    lifecycle._remove_path(package)
    assert not os.path.lexists(package)
    assert outside.is_dir()
    assert secret.read_bytes() == b"operator bytes must remain untouched"


def test_public_embedded_upgrade_rejects_unowned_paths_before_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dcc_mcp_core.install_lifecycle as core_lifecycle

    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    request = replace(_request(tmp_path), mode="embedded")
    first = tmp_path / "first-embedded.zip"
    with zipfile.ZipFile(first, "w") as payload:
        payload.writestr("embedded/dcc_mcp_zbrush/__init__.py", "PLUGIN = 'prior'\n")
        payload.writestr("embedded/dcc_mcp_zbrush_plugin.py", "ENTRY = 'prior'\n")
    first_digest = hashlib.sha256(first.read_bytes()).hexdigest()
    installed = lifecycle.run_lifecycle(request, plugin_archive=first, expected_sha256=first_digest)
    assert installed["exit_code"] == 50
    receipt_path = request.asset_dir / ".dcc-mcp" / "receipts" / "zbrush.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    readiness = _embedded_readiness(request, receipt)
    monkeypatch.setattr(core_lifecycle, "wait_for_sidecar_ready", lambda **_kwargs: readiness)
    monkeypatch.setattr(lifecycle, "_process_executable", lambda _pid: Path(request.dcc_path))
    monkeypatch.setattr(lifecycle, "_process_start_identity", lambda _pid: "start-5252")
    assert lifecycle.run_lifecycle(replace(request, operation="verify"))["exit_code"] == 0
    package = request.asset_dir / "dcc_mcp_zbrush"
    operator_directory = package / "operator-empty"
    operator_directory.mkdir()
    second = tmp_path / "second-embedded.zip"
    with zipfile.ZipFile(second, "w") as payload:
        payload.writestr("embedded/dcc_mcp_zbrush/__init__.py", "PLUGIN = 'next'\n")
        payload.writestr("embedded/dcc_mcp_zbrush_plugin.py", "ENTRY = 'next'\n")
    second_digest = hashlib.sha256(second.read_bytes()).hexdigest()

    upgraded = lifecycle.run_lifecycle(
        replace(request, operation="upgrade", version="0.2.25"),
        plugin_archive=second,
        expected_sha256=second_digest,
    )

    assert upgraded["exit_code"] == 10
    assert upgraded["stage"] == "ownership"
    assert operator_directory.is_dir()
    assert (package / "__init__.py").read_text(encoding="utf-8") == "PLUGIN = 'prior'\n"
    assert not (request.asset_dir / ".dcc-mcp" / "transactions").exists()


def test_embedded_owned_manifest_preserves_unowned_operator_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dcc_mcp_core.install_lifecycle as core_lifecycle

    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    request = replace(_request(tmp_path), mode="embedded")
    archive = tmp_path / "embedded.zip"
    with zipfile.ZipFile(archive, "w") as payload:
        payload.writestr("embedded/dcc_mcp_zbrush/__init__.py", "PLUGIN = True\n")
        payload.writestr("embedded/dcc_mcp_zbrush/nested/module.py", "VALUE = 1\n")
        payload.writestr("embedded/dcc_mcp_zbrush_plugin.py", "PLUGIN_ENTRY = True\n")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert lifecycle.run_lifecycle(request, plugin_archive=archive, expected_sha256=digest)["exit_code"] == 50
    receipt_path = request.asset_dir / ".dcc-mcp" / "receipts" / "zbrush.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    readiness = _embedded_readiness(request, receipt)
    monkeypatch.setattr(core_lifecycle, "wait_for_sidecar_ready", lambda **_kwargs: readiness)
    monkeypatch.setattr(lifecycle, "_process_executable", lambda _pid: Path(request.dcc_path))
    monkeypatch.setattr(lifecycle, "_process_start_identity", lambda _pid: "start-5252")
    assert lifecycle.run_lifecycle(replace(request, operation="verify"))["exit_code"] == 0
    operator_file = request.asset_dir / "dcc_mcp_zbrush" / "operator-owned.txt"
    operator_file.write_text("preserve", encoding="utf-8")

    result = lifecycle.run_lifecycle(replace(request, operation="uninstall"))

    assert result["exit_code"] == 0
    assert operator_file.read_text(encoding="utf-8") == "preserve"
    assert not (request.asset_dir / "dcc_mcp_zbrush" / "__init__.py").exists()
    assert not (request.asset_dir / "dcc_mcp_zbrush" / "nested").exists()


def test_tampered_owned_embedded_file_blocks_uninstall_without_touching_unowned_data(
    tmp_path: Path,
) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    root = tmp_path / "owned"
    (root / "nested").mkdir(parents=True)
    owned = root / "nested" / "module.py"
    owned.write_text("managed", encoding="utf-8")
    manifest = lifecycle._manifest(root)
    operator_file = root / "operator.txt"
    operator_file.write_text("keep", encoding="utf-8")
    owned.write_text("tampered", encoding="utf-8")

    with pytest.raises(lifecycle.LifecycleFailure, match="bytes drifted"):
        lifecycle._validate_manifest(root, manifest)
    assert operator_file.read_text(encoding="utf-8") == "keep"


def test_typed_manifest_binds_relative_links_and_rejects_escape(tmp_path: Path) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    root = tmp_path / "owned"
    root.mkdir()
    target = root / "target.py"
    target.write_text("managed", encoding="utf-8")
    link = root / "alias.py"
    try:
        os.symlink("target.py", link)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    manifest = lifecycle._manifest(root)
    lifecycle._validate_manifest(root, manifest)
    link_entry = next(item for item in manifest if item["path"] == "alias.py")
    assert link_entry == {
        "kind": "link",
        "path": "alias.py",
        "target": "target.py",
        "target_is_directory": False,
    }

    link.unlink()
    os.symlink("../outside.py", link)
    with pytest.raises(lifecycle.LifecycleFailure, match="target drifted"):
        lifecycle._validate_manifest(root, manifest)
    forged = [dict(item) for item in manifest]
    next(item for item in forged if item["path"] == "alias.py")["target"] = "../outside.py"
    with pytest.raises(lifecycle.LifecycleFailure, match="escapes"):
        lifecycle._validate_manifest(root, forged, verify_bytes=False)


def test_uninstall_failure_restores_exact_receipt_payload_and_backups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    request = _request(tmp_path)
    archive = tmp_path / "plugin.zip"
    digest = _build_sidecar_archive(archive)
    _commit_sidecar(monkeypatch, request, archive, digest)
    receipt_path = request.asset_dir / ".dcc-mcp" / "receipts" / "zbrush.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    paths = [
        receipt_path,
        request.asset_dir / "Python" / "init.py",
        request.asset_dir / "Python" / "dcc_mcp_zbrush_socket_bridge.py",
        request.asset_dir / "Python" / "dcc_mcp_zbrush_install_identity.json",
    ]
    exact_before = {str(path): path.read_bytes() for path in paths}
    backup_root = request.asset_dir / receipt["backup_root"]
    backup_before = {
        str(path.relative_to(backup_root)): path.read_bytes() for path in backup_root.rglob("*") if path.is_file()
    }
    real_restore = lifecycle._restore_file
    calls = 0

    def fail_after_first_restore(asset_dir: Path, record: dict) -> None:
        nonlocal calls
        real_restore(asset_dir, record)
        calls += 1
        if calls == 1:
            raise PermissionError("simulated Windows deletion failure")

    monkeypatch.setattr(lifecycle, "_restore_file", fail_after_first_restore)
    result = lifecycle.run_lifecycle(replace(request, operation="uninstall"))

    assert result["exit_code"] == 30
    assert result["stage"] == "uninstall"
    assert {str(path): path.read_bytes() for path in paths} == exact_before
    assert {
        str(path.relative_to(backup_root)): path.read_bytes() for path in backup_root.rglob("*") if path.is_file()
    } == backup_before


def test_uninstall_never_deletes_unowned_content_added_to_recovery_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    request = _request(tmp_path)
    archive = tmp_path / "plugin.zip"
    digest = _build_sidecar_archive(archive)
    _commit_sidecar(monkeypatch, request, archive, digest)
    receipt = json.loads((request.asset_dir / ".dcc-mcp" / "receipts" / "zbrush.json").read_text(encoding="utf-8"))
    victim = request.asset_dir / receipt["backup_root"] / "operator-owned.txt"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text("keep", encoding="utf-8")

    result = lifecycle.run_lifecycle(replace(request, operation="uninstall"))

    assert result["exit_code"] == 30
    assert result["stage"] == "uninstall"
    assert victim.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    ("product_name", "version"),
    [
        ("Unrelated.exe", "2026.1.0.0"),
        ("Maxon ZBrush", "2026.1rc1"),
        ("Maxon ZBrush", "1." + "9" * 5000),
    ],
)
def test_host_product_metadata_is_authentic_and_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    product_name: str,
    version: str,
) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    monkeypatch.setattr(
        lifecycle,
        "_windows_product_info",
        lambda _path: {"product_name": product_name, "version": version},
    )
    result = lifecycle.run_lifecycle(replace(_request(tmp_path), yes=False, dry_run=True))
    assert result["exit_code"] == 10
    assert result["stage"] == "host"


def test_unsigned_or_non_maxon_zbrush_executable_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    monkeypatch.setattr(
        lifecycle,
        "_windows_authenticode_info",
        lambda _path: {"status": "NotSigned", "subject": ""},
    )
    result = lifecycle.run_lifecycle(replace(_request(tmp_path), yes=False, dry_run=True))

    assert result["exit_code"] == 10
    assert result["stage"] == "host"
    assert "signed by Maxon" in result["reason"]


def test_pythonpath_shadow_adapter_is_not_accepted_as_distribution_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    real_distribution = lifecycle.distribution

    class ShadowDistribution:
        version = "0.2.24"
        files: list = []

        @staticmethod
        def read_text(_name: str):
            return None

    monkeypatch.setattr(
        lifecycle,
        "distribution",
        lambda name: ShadowDistribution() if name == "dcc-mcp-zbrush" else real_distribution(name),
    )
    result = lifecycle.run_lifecycle(replace(_request(tmp_path), yes=False, dry_run=True))

    assert result["exit_code"] == 10
    assert result["stage"] == "python"
    assert "not owned" in result["reason"]


def test_editable_provenance_rejects_same_repository_shadow_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dcc_mcp_zbrush.install_lifecycle as lifecycle

    shadow = tmp_path / "shadow" / "dcc_mcp_zbrush" / "__init__.py"
    shadow.parent.mkdir(parents=True)
    shadow.write_text("__version__ = '0.2.24'", encoding="utf-8")

    class EditableDistribution:
        version = "0.2.24"
        files: list = []

        @staticmethod
        def read_text(name: str):
            if name == "direct_url.json":
                return json.dumps({"url": tmp_path.as_uri(), "dir_info": {"editable": True}})
            return None

    class ShadowModule:
        __file__ = str(shadow)

    monkeypatch.setattr(lifecycle, "distribution", lambda _name: EditableDistribution())
    monkeypatch.setattr(lifecycle.importlib, "import_module", lambda _name: ShadowModule())

    with pytest.raises(lifecycle.LifecycleFailure, match="not owned"):
        lifecycle._distribution_module_provenance("dcc-mcp-zbrush", "dcc_mcp_zbrush")


def test_remediation_commands_preserve_complete_selected_context(
    tmp_path: Path,
) -> None:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    request = replace(_request(tmp_path), yes=False, dry_run=True, socket_host="localhost", socket_port=4321)
    result = run_lifecycle(request)
    command = result["next_steps"][0]["command"]

    assert isinstance(command, list)
    assert command == [
        str(Path(sys.executable).resolve()),
        "-m",
        "dcc_mcp_zbrush.cli",
        "install",
        "--mode",
        "sidecar",
        "--version",
        "0.2.24",
        "--dcc-path",
        str(request.dcc_path),
        "--python",
        str(request.python_path),
        "--asset-dir",
        str(request.asset_dir),
        "--socket-host",
        "localhost",
        "--socket-port",
        "4321",
        "--yes",
        "--json",
    ]
    assert all("<" not in token and ">" not in token for token in command)


def test_different_selected_python_fails_with_exact_executable_remediation(tmp_path: Path) -> None:
    from dcc_mcp_zbrush.install_lifecycle import run_lifecycle

    selected_python = tmp_path / "managed-python.exe"
    selected_python.write_bytes(b"MZ")
    request = replace(_request(tmp_path), python_path=selected_python, yes=False, dry_run=True)

    result = run_lifecycle(request)

    assert result["exit_code"] == 10
    assert result["stage"] == "python"
    command = result["next_steps"][0]["command"]
    assert command[:4] == [str(selected_python), "-m", "dcc_mcp_zbrush.cli", "install"]
    assert "--dcc-path" in command and str(request.dcc_path) in command
    assert "--asset-dir" in command and str(request.asset_dir) in command
