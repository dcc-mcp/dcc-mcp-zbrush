"""Import an asset file (OBJ/FBX) into ZBrush using the asset_import contract."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from dcc_mcp_core.asset_import import (
    AssetDescriptor,
    AssetFileVariant,
    AssetFormat,
    AssetImportValidationError,
    ImportToSceneResult,
    ImportWarning,
    ImportWarningCode,
    MaterialMode,
)
from dcc_mcp_core.skill import skill_entry

from dcc_mcp_zbrush.api import with_zbrush, zb_error, zb_success

_SUPPORTED_FORMATS = {AssetFormat.OBJ, AssetFormat.FBX}


def _pick_variant(variants: List[AssetFileVariant]) -> Optional[AssetFileVariant]:
    """Return the preferred variant, or the first supported one, or the first available."""
    for v in variants:
        if v.preferred and v.format in _SUPPORTED_FORMATS:
            return v
    for v in variants:
        if v.format in _SUPPORTED_FORMATS:
            return v
    return variants[0] if variants else None


def _import(zbc: Any, file_path: str) -> Dict[str, Any]:
    abs_path = os.path.abspath(file_path)
    if not os.path.isfile(abs_path):
        return {
            "success": False,
            "message": f"File does not exist: {abs_path}",
            "error": "FILE_NOT_FOUND",
            "imported_nodes": [],
        }
    zbc.set_next_filename(abs_path)
    zbc.press("Tool:Import")
    active_path = str(zbc.get_active_tool_path() or "")
    subtool_name = active_path.rsplit("/", 1)[-1] if active_path else ""
    return {
        "success": True,
        "file_path": abs_path,
        "imported_nodes": [subtool_name] if subtool_name else [],
        "active_tool_path": active_path,
    }


@skill_entry
@with_zbrush
def import_to_scene(
    asset_id: str,
    variants: List[Dict[str, Any]],
    unit_hint: str = "unitless",
    up_axis: str = "y",
    material_mode: str = MaterialMode.AS_AUTHORED,
    **kwargs: Any,
) -> dict:
    from dcc_mcp_zbrush._skill_host import run_in_zbrush  # noqa: PLC0415

    # Build and validate descriptor from incoming payload
    try:
        descriptor = AssetDescriptor.from_dict(
            {
                "asset_id": asset_id,
                "variants": variants,
                "unit_hint": unit_hint,
                "up_axis": up_axis,
            }
        )
        descriptor.validate()
    except (AssetImportValidationError, KeyError, TypeError, ValueError) as exc:
        return zb_error(
            f"Invalid AssetDescriptor: {exc}",
            "INVALID_DESCRIPTOR",
            prompt="Provide asset_id and at least one variant with a local_path.",
        )

    variant = _pick_variant(list(descriptor.variants))
    if variant is None:
        return zb_error(
            "No usable variant found in AssetDescriptor",
            "NO_VARIANT",
            prompt="Add at least one variant with a non-empty local_path.",
        )

    warnings: List[ImportWarning] = []
    if variant.format not in _SUPPORTED_FORMATS:
        warnings.append(
            ImportWarning(
                code=ImportWarningCode.UNSUPPORTED_FEATURE,
                message=(
                    f"Format '{variant.format}' is not natively supported by the ZBrush SDK import path; "
                    "attempting import anyway — results may vary."
                ),
            )
        )

    payload = run_in_zbrush(
        lambda zbc: _import(zbc, variant.local_path),
        "import_to_scene",
        file_path=variant.local_path,
    )

    if not payload.get("success", True):
        result = ImportToSceneResult(
            success=False,
            imported_nodes=[],
            warnings=warnings,
            error_message=payload.get("message", "Import failed"),
        )
        return zb_error(
            result.error_message or "Import failed",
            payload.get("error", "IMPORT_FAILED"),
            prompt="Check that the file path is correct and ZBrush is running.",
            **result.to_dict(),
        )

    imported_nodes = payload.get("imported_nodes", [])
    result = ImportToSceneResult(
        success=True,
        imported_nodes=imported_nodes,
        warnings=warnings,
        extra={
            "asset_id": asset_id,
            "file_path": payload.get("file_path", variant.local_path),
            "active_tool_path": payload.get("active_tool_path", ""),
        },
    )
    return zb_success(
        f"Imported '{asset_id}' → {imported_nodes}",
        prompt="Use zbrush_scene__list_subtools to confirm the new subtool.",
        **result.to_dict(),
    )


def main(**kwargs: Any) -> dict:
    return import_to_scene(**kwargs)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
