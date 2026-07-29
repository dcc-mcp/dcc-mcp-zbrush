"""ZBrush socket bridge plugin for sidecar MCP mode.

Copy this file into ZStartup/ZPlugs64 or expose it via ZBRUSH_PLUGIN_PATH.
It listens on TCP port 9876 (override with DCC_MCP_ZBRUSH_SOCKET_PORT) and
executes JSON-RPC requests against ``zbrush.commands``.

Registers a top-level DCC MCP palette with Copy Instance ID, Server Info, and
About DCC MCP actions through the official ZBrush Python SDK.
"""

from __future__ import annotations

import importlib
import json
import os
import queue
import socket
import threading
import time
import traceback
from typing import Any, Dict, Iterator, Optional

_ZBRUSH_REQUEST_LOCK = threading.Lock()
_CLIENT_READ_SECONDS = 1.0
_UI_POLL_SECONDS = 0.02
_REQUEST_TIMEOUT_SECONDS = 120.0
_REQUEST_QUEUE: "queue.Queue[Dict[str, Any]]" = queue.Queue()
_BRIDGE_THREAD: Optional[threading.Thread] = None


def _mark(message: str) -> None:
    """Append optional startup diagnostics without depending on host logging."""
    log_path = os.environ.get("DCC_MCP_ZBRUSH_BRIDGE_LOG", "").strip()
    if not log_path:
        return
    with open(log_path, "a", encoding="utf-8") as stream:
        stream.write(message + "\n")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _handle_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    method = payload.get("method")
    params = payload.get("params") or {}
    req_id = payload.get("id", 0)

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"ok": True}}

    with _ZBRUSH_REQUEST_LOCK:
        return _handle_zbrush_request(method, params, req_id)


def _handle_zbrush_request(method: Any, params: Dict[str, Any], req_id: Any) -> Dict[str, Any]:
    try:
        if method == "get_session_info":
            result = _get_session_info()
        elif method == "get_scene_info":
            result = _get_scene_info()
        elif method == "list_subtools":
            result = _list_subtools()
        elif method == "execute_python":
            result = _execute_python(params.get("code", ""), params.get("context") or {})
        elif method == "select_subtool":
            result = _select_subtool(int(params.get("index", -1)))
        elif method == "get_subtool_status":
            index = params.get("index")
            result = _get_subtool_status(None if index is None else int(index))
        elif method == "refine_active_subtool":
            result = _refine_active_subtool(
                int(params.get("subdivision_levels", 1)),
                float(params.get("polish", 0)),
                float(params.get("inflate", 0)),
            )
        elif method == "export_active_subtool_obj":
            result = _export_active_subtool_obj(str(params.get("output_path", "")))
        elif method == "import_to_scene":
            result = _import_to_scene(str(params.get("file_path", "")))
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            }
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except Exception as exc:  # noqa: BLE001
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32000, "message": str(exc), "data": traceback.format_exc()},
        }


def _import_zbc():
    import zbrush.commands as zbc  # noqa: PLC0415

    return zbc


def _subtool_status_flags(status: int) -> Dict[str, bool]:
    return {"visible": bool(status & 0x01), "locked": bool(status & 0x02)}


def _get_session_info() -> Dict[str, Any]:
    zbc = _import_zbc()
    return {
        "zbrush_version": f"{int(zbc.zbrush_info(0))}.{int(zbc.zbrush_info(1))}",
        "active_tool_path": str(zbc.get_active_tool_path() or ""),
        "subtool_count": int(zbc.get_subtool_count()),
        "embedded_python": True,
    }


def _get_scene_info() -> Dict[str, Any]:
    zbc = _import_zbc()
    try:
        active_index = int(zbc.get_active_subtool_index())
    except Exception:
        active_index = None
    return {
        "active_tool_path": str(zbc.get_active_tool_path() or ""),
        "subtool_count": int(zbc.get_subtool_count()),
        "active_subtool_index": active_index,
    }


def _list_subtools() -> Dict[str, Any]:
    zbc = _import_zbc()
    count = int(zbc.get_subtool_count())
    subtools = []
    for index in range(count):
        raw_status = int(zbc.get_subtool_status(index))
        subtools.append(
            {
                "index": index,
                "raw_status": raw_status,
                "flags": _subtool_status_flags(raw_status),
            }
        )
    return {"count": count, "subtools": subtools}


def _select_subtool(index: int) -> Dict[str, Any]:
    zbc = _import_zbc()
    count = int(zbc.get_subtool_count())
    if index < 0 or index >= count:
        return {
            "success": False,
            "message": f"Subtool index {index} out of range",
            "error": "INVALID_SUBTOOL_INDEX",
            "count": count,
            "index": index,
        }
    zbc.select_subtool(index)
    path = str(zbc.get_active_tool_path() or "")
    return {
        "index": index,
        "active_tool_path": path,
        "subtool_name": path.rsplit("/", 1)[-1] if path else "",
    }


def _get_subtool_status(index: Optional[int]) -> Dict[str, Any]:
    zbc = _import_zbc()
    resolved_index = int(zbc.get_active_subtool_index()) if index is None else index
    raw_status = int(zbc.get_subtool_status(resolved_index))
    path = str(zbc.get_active_tool_path() or "")
    return {
        "index": resolved_index,
        "raw_status": raw_status,
        "flags": _subtool_status_flags(raw_status),
        "active_tool_path": path,
    }


def _refine_active_subtool(
    subdivision_levels: int,
    polish: float,
    inflate: float,
) -> Dict[str, Any]:
    zbc = _import_zbc()
    for _ in range(subdivision_levels):
        zbc.press("Tool:Geometry:Divide")
    if polish:
        zbc.set("Tool:Deformation:Polish", polish)
    if inflate:
        zbc.set("Tool:Deformation:Inflate", inflate)
    path = str(zbc.get_active_tool_path() or "")
    return {
        "active_tool_path": path,
        "subtool_name": path.rsplit("/", 1)[-1] if path else "",
        "subdivision_levels": subdivision_levels,
        "polish": polish,
        "inflate": inflate,
    }


def _export_active_subtool_obj(output_path: str) -> Dict[str, Any]:
    zbc = _import_zbc()
    directory = os.path.dirname(os.path.abspath(output_path))
    if directory and not os.path.isdir(directory):
        return {
            "success": False,
            "message": f"Output directory does not exist: {directory}",
            "error": "OUTPUT_DIR_MISSING",
            "output_path": output_path,
        }
    abs_path = os.path.abspath(output_path)
    zbc.set_next_filename(abs_path)
    zbc.press("Tool:Export")
    path = str(zbc.get_active_tool_path() or "")
    return {
        "output_path": abs_path,
        "active_tool_path": path,
        "subtool_name": path.rsplit("/", 1)[-1] if path else "",
    }


def _import_to_scene(file_path: str) -> Dict[str, Any]:
    if not file_path:
        return {
            "success": False,
            "message": "file_path must not be empty",
            "error": "FILE_PATH_MISSING",
            "imported_nodes": [],
        }
    abs_path = os.path.abspath(file_path)
    if os.path.splitext(abs_path)[1].lower() != ".obj":
        return {
            "success": False,
            "message": "Only OBJ files can be imported without opening an interactive ZBrush dialog.",
            "error": "UNSUPPORTED_FORMAT",
            "imported_nodes": [],
        }
    if not os.path.isfile(abs_path):
        return {
            "success": False,
            "message": f"File does not exist: {abs_path}",
            "error": "FILE_NOT_FOUND",
            "imported_nodes": [],
        }
    zbc = _import_zbc()
    subtool_count_before = int(zbc.get_subtool_count())
    zbc.set_next_filename(abs_path)
    zbc.press("Tool:Import")
    subtool_count_after = int(zbc.get_subtool_count())
    active_path = str(zbc.get_active_tool_path() or "")
    subtool_name = active_path.rsplit("/", 1)[-1] if active_path else ""
    imported_nodes = [subtool_name] if subtool_name else []
    return {
        "success": True,
        "file_path": abs_path,
        "imported_nodes": imported_nodes,
        "subtool_count_before": subtool_count_before,
        "subtool_count_after": subtool_count_after,
        "active_tool_path": active_path,
    }


def _execute_python(code: str, context: Dict[str, Any]) -> Dict[str, Any]:
    zbc = _import_zbc()
    namespace: Dict[str, Any] = {"zbc": zbc}
    namespace.update(context)
    try:
        exec(compile(code, "<mcp-bridge>", "exec"), namespace)  # noqa: S102
    except Exception:
        return {
            "success": False,
            "message": "Script execution failed",
            "error": traceback.format_exc(),
            "context": {},
        }
    result = namespace.get("result")
    return {
        "success": True,
        "message": "Script executed successfully",
        "context": {"result": str(result) if result is not None else None},
    }


def _serve_client(conn: socket.socket) -> None:
    with conn:
        conn.settimeout(_CLIENT_READ_SECONDS)
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(65536)
            if not chunk:
                return
            data += chunk
        line = data.split(b"\n", 1)[0]
        payload = json.loads(line.decode("utf-8"))
        response = _dispatch_request(payload)
        conn.sendall((json.dumps(response) + "\n").encode("utf-8"))


def _dispatch_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Queue host API work for the ZBrush main thread and await its response."""
    pending: Dict[str, Any] = {"payload": payload, "event": threading.Event()}
    _REQUEST_QUEUE.put(pending)
    if not pending["event"].wait(_REQUEST_TIMEOUT_SECONDS):
        raise TimeoutError("Timed out waiting for ZBrush main-thread dispatch")
    return pending["response"]


def _drain_request_queue() -> None:
    """Run queued ZBrush SDK calls from the host's main-thread pump."""
    while True:
        try:
            pending = _REQUEST_QUEUE.get_nowait()
        except queue.Empty:
            return
        try:
            pending["response"] = _handle_request(pending["payload"])
        finally:
            pending["event"].set()


def _serve_forever(host: str, port: int) -> None:
    """Accept socket clients on a background listener thread."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((host, port))
            server.listen(5)
            _mark(f"listening {host}:{port}")
            print(f"[dcc-mcp-zbrush] socket bridge listening on {host}:{port}")
            while True:
                conn, _addr = server.accept()
                threading.Thread(target=_serve_client, args=(conn,), daemon=True).start()
    except BaseException:
        _mark("listener failed\n" + traceback.format_exc())
        raise


def _run_main_thread_pump() -> None:
    """Dispatch queued SDK work while yielding to ZBrush's native UI pump."""
    zbc = _import_zbc()
    while True:
        _drain_request_queue()
        zbc.update(redraw_ui=True)
        time.sleep(_UI_POLL_SECONDS)


def _start_bridge(host: str, port: int) -> threading.Thread:
    """Start the listener and run the ZBrush-native pump on the main thread."""
    global _BRIDGE_THREAD

    if _BRIDGE_THREAD is None or not _BRIDGE_THREAD.is_alive():
        _BRIDGE_THREAD = threading.Thread(
            target=_serve_forever,
            args=(host, port),
            daemon=True,
            name="dcc-mcp-zbrush-socket-bridge",
        )
        _BRIDGE_THREAD.start()
    _run_main_thread_pump()
    return _BRIDGE_THREAD


def _running_in_zbrush() -> bool:
    try:
        import zbrush.commands  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def bootstrap_bridge() -> Optional[threading.Thread]:
    """Start the bridge when executed by ZBrush's plugin scan."""
    _mark(f"bootstrap module={__name__} in_zbrush={_running_in_zbrush()}")
    if __name__ != "__main__" and not _running_in_zbrush():
        return None
    host = os.environ.get("DCC_MCP_ZBRUSH_SOCKET_HOST", "127.0.0.1")
    port = _env_int("DCC_MCP_ZBRUSH_SOCKET_PORT", 9876)
    try:
        if not _install_menu():
            _mark("menu registration returned false")
    except BaseException:
        _mark("menu registration failed\n" + traceback.format_exc())
    try:
        bridge_thread = _start_bridge(host, port)
    except BaseException:
        _mark("bootstrap failed\n" + traceback.format_exc())
        raise
    _mark(f"bootstrap started {host}:{port}")
    return bridge_thread


# ══════════════════════════════════════════════════════════════════════════════
# Unified DCC MCP menu actions (PIP-2905)
# ──────────────────────────────────────────────────────────────────────────────
# The sidecar plugin must remain self-contained because dcc-mcp-zbrush runs in
# an external process and may not be importable in ZBrush's embedded VM.
# ══════════════════════════════════════════════════════════════════════════════


def dcc_mcp_copy_instance_id() -> None:
    """Copy the DCC MCP instance UUID to the system clipboard."""
    try:
        from dcc_mcp_zbrush._menu import copy_instance_id

        copy_instance_id()
    except ImportError:
        _fallback_copy_instance_id()


def dcc_mcp_show_server_info() -> None:
    """Show DCC MCP server information."""
    try:
        from dcc_mcp_zbrush._menu import show_server_info

        show_server_info()
    except ImportError:
        _fallback_server_info()


def dcc_mcp_show_about() -> None:
    """Show About DCC MCP dialog."""
    try:
        from dcc_mcp_zbrush._menu import show_about

        show_about()
    except ImportError:
        _fallback_about()


def _qt_widgets_modules() -> Iterator[Any]:
    for binding in ("PySide2", "PySide6"):
        try:
            yield importlib.import_module(f"{binding}.QtWidgets")
        except Exception:
            continue


def _show_message(title: str, message: str) -> None:
    try:
        _import_zbc().message_ok(message, title)
        return
    except Exception:
        pass
    for widgets in _qt_widgets_modules():
        try:
            widgets.QMessageBox.information(None, title, message)
            return
        except Exception:
            continue
    print(f"[dcc-mcp-zbrush] {title}\n{message}")  # noqa: T201


# ── fallback implementations when dcc-mcp-zbrush is not importable ────────


def _fallback_copy_instance_id() -> None:
    """Fail closed when the external sidecar identity is unavailable."""
    _show_message(
        "DCC MCP — Copy Instance ID",
        "Instance ID is owned by the external MCP server and is unavailable "
        "inside this standalone plugin. Run `dcc-mcp-cli list` from a terminal.",
    )


def _fallback_server_info() -> None:
    """Fallback: show server info using available tools."""
    import sys

    gateway_port = os.environ.get("DCC_MCP_GATEWAY_PORT", "9765")

    try:
        import zbrush.commands as zbc  # noqa: PLC0415

        zb_version = f"{int(zbc.zbrush_info(0))}.{int(zbc.zbrush_info(1))}"
    except Exception:
        zb_version = "unknown"

    msg = (
        "Instance UUID: unavailable in standalone plugin\n"
        f"DCC: ZBrush {zb_version}\n"
        f"ZBrush PID: {os.getpid()}\n"
        f"Gateway Port: {gateway_port}\n"
        f"Python: {sys.version.split()[0]}\n\n"
        "Run `dcc-mcp-cli list` for the external server identity and URL."
    )
    _show_message("DCC MCP — Server Info", msg)


def _fallback_about() -> None:
    """Fallback: show about dialog."""
    msg = (
        "dcc-mcp-zbrush\nDCC MCP — shared infrastructure for DCC automation.\nhttps://github.com/dcc-mcp/dcc-mcp-zbrush"
    )
    _show_message("About DCC MCP", msg)


def _on_copy_instance_id(_sender: str) -> None:
    dcc_mcp_copy_instance_id()


def _on_show_server_info(_sender: str) -> None:
    dcc_mcp_show_server_info()


def _on_show_about(_sender: str) -> None:
    dcc_mcp_show_about()


def _install_menu(zbc: Any = None) -> bool:
    """Install the top-level DCC MCP palette before entering the host pump."""
    zbc = zbc or _import_zbc()
    if not zbc.exists("DCC MCP") and not zbc.add_palette("DCC MCP", docking_bar=1):
        return False
    actions = (
        ("Copy Instance ID", "Copy the DCC MCP instance UUID to the clipboard.", _on_copy_instance_id),
        ("Server Info", "Show DCC MCP server and runtime information.", _on_show_server_info),
        ("About DCC MCP", "Show adapter and ZBrush version information.", _on_show_about),
    )
    for label, info, callback in actions:
        item_path = f"DCC MCP:{label}"
        if not zbc.exists(item_path) and not zbc.add_button(item_path, info, callback):
            return False
    return True


_BRIDGE_BOOTSTRAP = bootstrap_bridge()
