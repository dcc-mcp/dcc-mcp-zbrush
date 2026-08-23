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
import math
import ntpath
import os
import queue
import socket
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, Optional

_ZBRUSH_REQUEST_LOCK = threading.Lock()
_CLIENT_READ_SECONDS = 1.0
_UI_POLL_SECONDS = 0.02
_REQUEST_TIMEOUT_SECONDS = 600.0
_REQUEST_QUEUE: "queue.Queue[Dict[str, Any]]" = queue.Queue()
_BRIDGE_THREAD: Optional[threading.Thread] = None
_REQUEST_STATE_LOCK = threading.Lock()
_ACTIVE_REQUEST: Optional[Dict[str, Any]] = None
_PUMP_TIMER_ID = 0
_PUMP_TIMER_CALLBACK: Any = None


def _mark(message: str) -> None:
    """Append optional startup diagnostics without depending on host logging."""
    log_path = os.environ.get("DCC_MCP_ZBRUSH_BRIDGE_LOG", "").strip()
    if not log_path:
        return
    with open(log_path, "a", encoding="utf-8") as stream:
        stream.write(message + "\n")


def _capture_bootstrap_error(stage: str, error: BaseException) -> None:
    """Persist a bounded JSONL startup error for ``dcc-mcp-zbrush verify``."""
    configured = os.environ.get("DCC_MCP_ZBRUSH_BOOTSTRAP_ERRORS", "").strip()
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = configured or os.path.join(default_root, ".dcc-mcp", "bootstrap-errors.jsonl")
    payload = {
        "timestamp": time.time(),
        "stage": stage,
        "reason": str(error),
        "exception_type": type(error).__name__,
    }
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")
    except BaseException:
        # Startup error reporting must not hide the original bootstrap failure.
        pass


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
        with _REQUEST_STATE_LOCK:
            active = _ACTIVE_REQUEST
            active_method = active["payload"].get("method") if active else None
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"ok": True, "busy": active is not None, "active_method": active_method},
        }

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
        elif method == "remesh_active_subtool":
            result = _remesh_active_subtool(
                int(params.get("target_face_count", 100_000)),
                bool(params.get("duplicate", True)),
            )
        elif method == "inspect_active_mesh":
            result = _inspect_active_mesh()
        elif method == "bake_active_subtool_map":
            result = _bake_active_subtool_map(
                str(params.get("map_type", "")),
                str(params.get("output_path", "")),
                int(params.get("width", 2048)),
                int(params.get("height", 2048)),
                bool(params.get("smooth", True)),
                int(params.get("border", 8)),
            )
        elif method == "create_wrinkle_brush":
            result = _create_wrinkle_brush(str(params.get("output_path", "")))
        elif method == "load_wrinkle_brush":
            result = _load_wrinkle_brush(str(params.get("brush_path", "")))
        elif method == "export_active_subtool_obj":
            result = _export_active_subtool_obj(str(params.get("output_path", "")))
        elif method == "capture_turntable":
            result = _capture_turntable(
                str(params.get("output_dir", "")),
                params.get("angles"),
                str(params.get("prefix", "zbrush-turntable")),
                bool(params.get("bpr_render", True)),
                bool(params.get("polyframe", False)),
            )
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


@contextmanager
def _quiet_ui_actions(zbc: Any) -> Iterator[None]:
    zbc.show_actions(0)
    try:
        yield
    finally:
        zbc.show_actions(1)


def _run_quiet_ui(zbc: Any, action: Callable[[], None]) -> None:
    with _quiet_ui_actions(zbc):
        action()


def _subtool_name(path: str) -> str:
    return ntpath.basename(path)


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
        "subtool_name": _subtool_name(path),
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
        "subtool_name": _subtool_name(path),
        "subdivision_levels": subdivision_levels,
        "polish": polish,
        "inflate": inflate,
    }


def _remesh_active_subtool(target_face_count: int, duplicate: bool) -> Dict[str, Any]:
    if not 1_000 <= target_face_count <= 100_000:
        return {
            "success": False,
            "message": "target_face_count must be between 1000 and 100000",
            "error": "INVALID_TARGET_FACE_COUNT",
        }

    zbc = _import_zbc()
    target_control = "Tool:Geometry:ZRemesher:Target Polygons Count"
    remesh_control = "Tool:Geometry:ZRemesher"
    duplicate_control = "Tool:SubTool:Duplicate"
    required = [target_control, remesh_control]
    if duplicate:
        required.append(duplicate_control)
    _require_controls(zbc, *required)

    face_count_before = int(zbc.query_mesh3d(1)[0])
    started = time.perf_counter()
    with _quiet_ui_actions(zbc):
        if duplicate:
            zbc.press(duplicate_control)
        zbc.set(target_control, target_face_count / 1000.0)
        zbc.press(remesh_control)
    path = str(zbc.get_active_tool_path() or "")
    return {
        "active_tool_path": path,
        "subtool_name": _subtool_name(path),
        "target_face_count": target_face_count,
        "face_count_before": face_count_before,
        "face_count_after": int(zbc.query_mesh3d(1)[0]),
        "duplicate": duplicate,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def _inspect_active_mesh() -> Dict[str, Any]:
    zbc = _import_zbc()
    uv_bounds = [float(value) for value in zbc.query_mesh3d(3)]
    has_uvs = len(uv_bounds) == 4 and uv_bounds[2] > uv_bounds[0] and uv_bounds[3] > uv_bounds[1]
    path = str(zbc.get_active_tool_path() or "")
    return {
        "active_tool_path": path,
        "subtool_name": _subtool_name(path),
        "point_count": int(zbc.query_mesh3d(0)[0]),
        "face_count": int(zbc.query_mesh3d(1)[0]),
        "bounds": [float(value) for value in zbc.query_mesh3d(2)],
        "uv_bounds": uv_bounds,
        "has_uvs": has_uvs,
        "mesh_area": float(zbc.query_mesh3d(8)[0]),
        "solid": bool(zbc.is_polymesh3d_solid()),
    }


def _bake_active_subtool_map(
    map_type: str,
    output_path: str,
    width: int,
    height: int,
    smooth: bool,
    border: int,
) -> Dict[str, Any]:
    normalized_type = map_type.strip().lower()
    if normalized_type not in {"normal", "displacement"}:
        return {
            "success": False,
            "message": "map_type must be normal or displacement",
            "error": "UNSUPPORTED_MAP_TYPE",
            "map_type": normalized_type,
            "output_path": output_path,
        }
    if not output_path:
        return {"success": False, "message": "output_path must not be empty", "error": "OUTPUT_PATH_MISSING"}
    abs_path = os.path.abspath(output_path)
    if os.path.splitext(abs_path)[1].lower() not in {".tif", ".tiff"}:
        return {
            "success": False,
            "message": "Baked maps must use a .tif or .tiff output path",
            "error": "UNSUPPORTED_FORMAT",
            "output_path": abs_path,
        }
    directory = os.path.dirname(abs_path)
    if directory and not os.path.isdir(directory):
        return {
            "success": False,
            "message": f"Output directory does not exist: {directory}",
            "error": "OUTPUT_DIR_MISSING",
            "output_path": abs_path,
        }
    if width != height:
        return {
            "success": False,
            "message": "ZBrush native map baking requires equal width and height",
            "error": "NON_SQUARE_MAP",
            "output_path": abs_path,
        }
    if not (256 <= width <= 8192) or width & (width - 1):
        return {
            "success": False,
            "message": "width and height must be the same power of two between 256 and 8192",
            "error": "INVALID_MAP_SIZE",
            "output_path": abs_path,
        }
    if not 0 <= border <= 16:
        return {
            "success": False,
            "message": "border must be between 0 and 16",
            "error": "INVALID_BORDER",
            "output_path": abs_path,
        }

    zbc = _import_zbc()
    uv_bounds = [float(value) for value in zbc.query_mesh3d(3)]
    if len(uv_bounds) != 4 or uv_bounds[2] <= uv_bounds[0] or uv_bounds[3] <= uv_bounds[1]:
        return {
            "success": False,
            "message": "The active subtool has no usable UVs",
            "error": "UVS_MISSING",
            "map_type": normalized_type,
            "output_path": abs_path,
            "uv_bounds": uv_bounds,
        }

    map_size_control = "Tool:UV Map:UV Map Size"
    map_border_control = "Tool:UV Map:UV Map Border"
    controls = (
        {
            "create": "Tool:Normal Map:Create NormalMap",
            "clone": "Tool:Normal Map:Clone NM",
            "export": "Texture:Export",
            "smooth": "Tool:Normal Map:SmoothUV",
            "tangent": "Tool:Normal Map:Tangent",
        }
        if normalized_type == "normal"
        else {
            "create": "Tool:Displacement Map:Create DispMap",
            "clone": "Tool:Displacement Map:Clone Disp",
            "export": "Alpha:Export",
            "smooth": "Tool:Displacement Map:SmoothUV",
        }
    )
    setting_controls = [map_size_control, map_border_control, controls["smooth"]]
    tangent_control = controls.get("tangent")
    if tangent_control:
        setting_controls.append(tangent_control)
    _require_controls(zbc, controls["create"], controls["clone"], controls["export"], *setting_controls)
    saved_settings = {path: zbc.get(path) for path in setting_controls}
    stem, extension = os.path.splitext(os.path.basename(abs_path))
    stage_path = os.path.join(directory, f".{stem}-{os.getpid()}-{time.time_ns()}{extension}")
    map_created = False

    def bake_and_export() -> None:
        nonlocal map_created
        lowered = 0
        try:
            zbc.set(map_size_control, width)
            zbc.set(map_border_control, border)
            zbc.set(controls["smooth"], int(smooth))
            if tangent_control:
                zbc.set(tangent_control, 1)
            while zbc.exists("Tool:Geometry:Lower Res") and zbc.is_enabled("Tool:Geometry:Lower Res"):
                zbc.press("Tool:Geometry:Lower Res")
                lowered += 1
            zbc.press(controls["create"])
            if not zbc.is_enabled(controls["clone"]):
                return
            zbc.press(controls["clone"])
            zbc.set_next_filename(stage_path)
            zbc.press(controls["export"])
            map_created = True
        finally:
            try:
                for _ in range(lowered):
                    if not zbc.exists("Tool:Geometry:Higher Res") or not zbc.is_enabled("Tool:Geometry:Higher Res"):
                        raise RuntimeError("ZBrush could not restore the original subdivision level")
                    zbc.press("Tool:Geometry:Higher Res")
            finally:
                for item_path, value in saved_settings.items():
                    zbc.set(item_path, value)

    try:
        _run_quiet_ui(zbc, bake_and_export)
        if not map_created:
            return {
                "success": False,
                "message": "ZBrush did not create a baked map from the active subdivision stack",
                "error": "MAP_NOT_CREATED",
                "map_type": normalized_type,
                "output_path": abs_path,
                "uv_bounds": uv_bounds,
            }
        if not os.path.isfile(stage_path) or os.path.getsize(stage_path) == 0:
            raise RuntimeError("ZBrush did not export a non-empty baked map")
        os.replace(stage_path, abs_path)
        return {
            "map_type": normalized_type,
            "output_path": abs_path,
            "bytes": os.path.getsize(abs_path),
            "width": width,
            "height": height,
            "smooth": smooth,
            "border": border,
            "uv_bounds": uv_bounds,
        }
    finally:
        if os.path.isfile(stage_path):
            os.remove(stage_path)


_WRINKLE_SETTINGS = {
    "Stroke:Lazy Mouse": 1.0,
    "Stroke:Lazy Mouse:LazyStep": 0.05,
    "Stroke:Lazy Mouse:LazySmooth": 3.0,
    "Stroke:Lazy Mouse:LazyRadius": 12.0,
    "Draw:Z Intensity": 18.0,
    "Draw:Focal Shift": -70.0,
    "Draw:Draw Size": 18.0,
    "Draw:Zsub": 1.0,
}


def _brush_error(path: str, *, must_exist: bool) -> Optional[Dict[str, Any]]:
    if not path:
        return {"success": False, "message": "Brush path must not be empty", "error": "BRUSH_PATH_MISSING"}
    if os.path.splitext(path)[1].lower() != ".zbp":
        return {
            "success": False,
            "message": "Brush path must end in .ZBP",
            "error": "UNSUPPORTED_FORMAT",
            "brush_path": path,
        }
    if must_exist and not os.path.isfile(path):
        return {
            "success": False,
            "message": f"Brush file does not exist: {path}",
            "error": "FILE_NOT_FOUND",
            "brush_path": path,
        }
    directory = os.path.dirname(path)
    if not must_exist and directory and not os.path.isdir(directory):
        return {
            "success": False,
            "message": f"Output directory does not exist: {directory}",
            "error": "OUTPUT_DIR_MISSING",
            "brush_path": path,
        }
    return None


def _require_controls(zbc: Any, *controls: str) -> None:
    missing = [control for control in controls if not zbc.exists(control)]
    if missing:
        raise RuntimeError(f"Missing required ZBrush controls: {', '.join(missing)}")


def _apply_wrinkle_settings(zbc: Any) -> None:
    _require_controls(zbc, "Stroke:FreeHand", "Alpha:Alpha 01", *_WRINKLE_SETTINGS)
    zbc.press("Stroke:FreeHand")
    zbc.press("Alpha:Alpha 01")
    for item_path, value in _WRINKLE_SETTINGS.items():
        zbc.set(item_path, value)
    if zbc.exists("Draw:Rgb"):
        zbc.set("Draw:Rgb", 0.0)


def _current_wrinkle_settings(zbc: Any) -> Dict[str, float]:
    return {
        "lazy_mouse": float(zbc.get("Stroke:Lazy Mouse")),
        "lazy_step": float(zbc.get("Stroke:Lazy Mouse:LazyStep")),
        "lazy_smooth": float(zbc.get("Stroke:Lazy Mouse:LazySmooth")),
        "lazy_radius": float(zbc.get("Stroke:Lazy Mouse:LazyRadius")),
        "z_intensity": float(zbc.get("Draw:Z Intensity")),
        "focal_shift": float(zbc.get("Draw:Focal Shift")),
        "draw_size": float(zbc.get("Draw:Draw Size")),
        "zsub": float(zbc.get("Draw:Zsub")),
    }


def _create_wrinkle_brush(output_path: str) -> Dict[str, Any]:
    error = _brush_error(output_path, must_exist=False)
    if error:
        return error
    abs_path = os.path.abspath(output_path)
    zbc = _import_zbc()
    _require_controls(zbc, "Brush:DamStandard", "Brush:Clone", "Brush:Save As")
    backup_path = ""
    if os.path.isfile(abs_path):
        backup_path = f"{abs_path}.dcc-mcp-{os.getpid()}-{time.time_ns()}.bak"
        os.replace(abs_path, backup_path)
    try:
        with _quiet_ui_actions(zbc):
            zbc.press("Brush:DamStandard")
            zbc.press("Brush:Clone")
            _apply_wrinkle_settings(zbc)
            zbc.set_next_filename(abs_path)
            zbc.press("Brush:Save As")
        if not os.path.isfile(abs_path) or os.path.getsize(abs_path) == 0:
            raise RuntimeError("ZBrush did not save a non-empty brush file")
    except Exception:
        if os.path.isfile(abs_path):
            os.remove(abs_path)
        if backup_path:
            os.replace(backup_path, abs_path)
        raise
    if backup_path:
        os.remove(backup_path)
    return {
        "brush_path": abs_path,
        "bytes": os.path.getsize(abs_path),
        "base_brush": "DamStandard",
        "settings": _current_wrinkle_settings(zbc),
    }


def _load_wrinkle_brush(brush_path: str) -> Dict[str, Any]:
    error = _brush_error(brush_path, must_exist=True)
    if error:
        return error
    abs_path = os.path.abspath(brush_path)
    zbc = _import_zbc()
    _require_controls(zbc, "Brush:Load Brush")
    with _quiet_ui_actions(zbc):
        zbc.set_next_filename(abs_path)
        zbc.press("Brush:Load Brush")
        _apply_wrinkle_settings(zbc)
    return {
        "brush_path": abs_path,
        "bytes": os.path.getsize(abs_path),
        "settings": _current_wrinkle_settings(zbc),
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

    def export_obj() -> None:
        zbc.set_next_filename(abs_path)
        zbc.press("Tool:Export")

    _run_quiet_ui(zbc, export_obj)
    path = str(zbc.get_active_tool_path() or "")
    return {
        "output_path": abs_path,
        "active_tool_path": path,
        "subtool_name": _subtool_name(path),
    }


def _capture_turntable(
    output_dir: str,
    angles: Any,
    prefix: str,
    bpr_render: bool,
    polyframe: bool,
) -> Dict[str, Any]:
    if not output_dir:
        return {"success": False, "message": "output_dir must not be empty", "error": "OUTPUT_DIR_MISSING"}
    abs_dir = os.path.abspath(output_dir)
    if not os.path.isdir(abs_dir):
        return {
            "success": False,
            "message": f"Output directory does not exist: {abs_dir}",
            "error": "OUTPUT_DIR_MISSING",
            "output_dir": abs_dir,
        }
    if not prefix or os.path.basename(prefix) != prefix:
        return {"success": False, "message": "prefix must be a file name", "error": "INVALID_PREFIX"}
    try:
        normalized_angles = [float(angle) for angle in angles]
    except (TypeError, ValueError):
        return {"success": False, "message": "angles must be a list of numbers", "error": "INVALID_ANGLES"}
    if not normalized_angles or len(normalized_angles) > 72 or any(not math.isfinite(a) for a in normalized_angles):
        return {"success": False, "message": "angles must contain 1 to 72 finite values", "error": "INVALID_ANGLES"}

    zbc = _import_zbc()
    required = ["Document:Export", "Transform:Edit"]
    if bpr_render:
        required.append("Render:BPR")
    _require_controls(zbc, *required)
    if not bool(zbc.get("Transform:Edit")):
        return {
            "success": False,
            "message": "The active tool is not drawn in 3D Edit mode",
            "error": "EDIT_MODE_REQUIRED",
        }
    base_transform = [float(value) for value in zbc.get_transform()]
    if len(base_transform) != 9:
        raise RuntimeError("ZBrush returned an invalid tool transform")

    staged: list[tuple[str, str, float]] = []
    polyframe_toggled = False
    try:
        with _quiet_ui_actions(zbc):
            try:
                if polyframe:
                    zbc.press_key("SHIFT+F", lambda: None)
                    polyframe_toggled = True
                for index, angle in enumerate(normalized_angles):
                    zbc.set_transform(
                        x_rotate=base_transform[6],
                        y_rotate=angle,
                        z_rotate=base_transform[8],
                    )
                    zbc.update(redraw_ui=True)
                    if bpr_render:
                        zbc.press("Render:BPR")
                    final_path = os.path.join(abs_dir, f"{prefix}-{index:03d}.psd")
                    stage_path = os.path.join(
                        abs_dir,
                        f".{prefix}-{index:03d}-{os.getpid()}-{time.time_ns()}.psd",
                    )
                    staged.append((stage_path, final_path, angle))
                    zbc.set_next_filename(stage_path)
                    zbc.press("Document:Export")
                    if not os.path.isfile(stage_path) or os.path.getsize(stage_path) == 0:
                        raise RuntimeError(f"ZBrush did not export a non-empty document frame: {final_path}")
            finally:
                try:
                    zbc.set_transform(*base_transform)
                finally:
                    if polyframe_toggled:
                        zbc.press_key("SHIFT+F", lambda: None)
                zbc.update(redraw_ui=True)

        frames = []
        for stage_path, final_path, angle in staged:
            os.replace(stage_path, final_path)
            frames.append({"angle": angle, "path": final_path, "bytes": os.path.getsize(final_path)})
        return {
            "output_dir": abs_dir,
            "frames": frames,
            "base_transform": base_transform,
            "bpr_render": bpr_render,
            "polyframe": polyframe,
        }
    finally:
        for stage_path, _final_path, _angle in staged:
            if os.path.isfile(stage_path):
                os.remove(stage_path)


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
    duplicate_failed = False

    def import_obj() -> None:
        nonlocal duplicate_failed
        if zbc.exists("Tool:SubTool:Duplicate") and zbc.is_enabled("Tool:SubTool:Duplicate"):
            zbc.press("Tool:SubTool:Duplicate")
            if int(zbc.get_subtool_count()) != subtool_count_before + 1:
                duplicate_failed = True
                return
            while zbc.exists("Tool:Geometry:Lower Res") and zbc.is_enabled("Tool:Geometry:Lower Res"):
                zbc.press("Tool:Geometry:Lower Res")
            if zbc.exists("Tool:Geometry:Del Higher") and zbc.is_enabled("Tool:Geometry:Del Higher"):
                zbc.press("Tool:Geometry:Del Higher")
        zbc.set_next_filename(abs_path)
        zbc.press("Tool:Import")

    _run_quiet_ui(zbc, import_obj)
    if duplicate_failed:
        return {
            "success": False,
            "message": "ZBrush did not create an import target subtool",
            "error": "SUBTOOL_CREATE_FAILED",
            "imported_nodes": [],
        }
    subtool_count_after = int(zbc.get_subtool_count())
    active_path = str(zbc.get_active_tool_path() or "")
    subtool_name = _subtool_name(active_path)
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


def _route_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Keep bridge health responsive without touching the ZBrush SDK."""
    if payload.get("method") == "ping":
        return _handle_request(payload)
    return _dispatch_request(payload)


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
        response = _route_request(payload)
        conn.sendall((json.dumps(response) + "\n").encode("utf-8"))


def _dispatch_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Queue host API work for the ZBrush main thread and await its response."""
    global _ACTIVE_REQUEST

    pending: Dict[str, Any] = {"payload": payload, "event": threading.Event()}
    with _REQUEST_STATE_LOCK:
        if _ACTIVE_REQUEST is not None:
            active_method = _ACTIVE_REQUEST["payload"].get("method")
            return {
                "jsonrpc": "2.0",
                "id": payload.get("id", 0),
                "error": {
                    "code": -32001,
                    "message": f"ZBrush bridge is busy with {active_method}",
                    "data": {"retryable": True, "active_method": active_method},
                },
            }
        _ACTIVE_REQUEST = pending
    _REQUEST_QUEUE.put(pending)
    if not pending["event"].wait(_REQUEST_TIMEOUT_SECONDS):
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id", 0),
            "error": {
                "code": -32002,
                "message": "Timed out waiting for ZBrush; the accepted request may still be running",
                "data": {"retryable": False, "still_running": True, "active_method": payload.get("method")},
            },
        }
    return pending["response"]


def _drain_request_queue() -> None:
    """Run queued ZBrush SDK calls from the host's main-thread pump."""
    global _ACTIVE_REQUEST

    while True:
        try:
            pending = _REQUEST_QUEUE.get_nowait()
        except queue.Empty:
            return
        try:
            pending["response"] = _handle_request(pending["payload"])
        finally:
            with _REQUEST_STATE_LOCK:
                if _ACTIVE_REQUEST is pending:
                    _ACTIVE_REQUEST = None
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


def _install_main_thread_pump() -> None:
    """Drain SDK work from ZBrush's Windows UI thread without blocking it."""
    global _PUMP_TIMER_CALLBACK, _PUMP_TIMER_ID

    if _PUMP_TIMER_ID:
        return
    if sys.platform != "win32":
        _run_main_thread_pump()
        return

    import ctypes  # noqa: PLC0415

    def on_timer(_window: Any, _message: int, _timer_id: int, _tick: int) -> None:
        try:
            _drain_request_queue()
        except BaseException:
            _mark("main-thread timer failed\n" + traceback.format_exc())

    callback_type = ctypes.WINFUNCTYPE(
        None,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_ulong,
    )
    callback = callback_type(on_timer)
    timer_id = ctypes.windll.user32.SetTimer(None, 0, max(1, round(_UI_POLL_SECONDS * 1000)), callback)
    if not timer_id:
        raise OSError("ZBrush main-thread timer registration failed")
    _PUMP_TIMER_CALLBACK = callback
    _PUMP_TIMER_ID = int(timer_id)


def _start_bridge(host: str, port: int) -> threading.Thread:
    """Start the listener and install the ZBrush main-thread request pump."""
    global _BRIDGE_THREAD

    if _BRIDGE_THREAD is None or not _BRIDGE_THREAD.is_alive():
        _BRIDGE_THREAD = threading.Thread(
            target=_serve_forever,
            args=(host, port),
            daemon=True,
            name="dcc-mcp-zbrush-socket-bridge",
        )
        _BRIDGE_THREAD.start()
    _install_main_thread_pump()
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
    except BaseException as exc:
        _mark("bootstrap failed\n" + traceback.format_exc())
        _capture_bootstrap_error("sidecar_bootstrap", exc)
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
