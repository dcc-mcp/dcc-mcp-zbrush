"""ZBrush socket bridge plugin for sidecar MCP mode.

Copy this file into ZStartup/ZPlugs64 or expose it via ZBRUSH_PLUGIN_PATH.
It listens on TCP port 9876 (override with DCC_MCP_ZBRUSH_SOCKET_PORT) and
executes JSON-RPC requests against ``zbrush.commands``.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import traceback
from typing import Any, Dict, Optional


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

    try:
        if method == "ping":
            result = {"ok": True}
        elif method == "get_session_info":
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
        elif method == "export_active_subtool_obj":
            result = _export_active_subtool_obj(str(params.get("output_path", "")))
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
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(65536)
            if not chunk:
                return
            data += chunk
        line = data.split(b"\n", 1)[0]
        payload = json.loads(line.decode("utf-8"))
        response = _handle_request(payload)
        conn.sendall((json.dumps(response) + "\n").encode("utf-8"))


def _serve_forever(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(5)
        print(f"[dcc-mcp-zbrush] socket bridge listening on {host}:{port}")
        while True:
            conn, _addr = server.accept()
            threading.Thread(target=_serve_client, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    host = os.environ.get("DCC_MCP_ZBRUSH_SOCKET_HOST", "127.0.0.1")
    port = _env_int("DCC_MCP_ZBRUSH_SOCKET_PORT", 9876)
    _serve_forever(host, port)
