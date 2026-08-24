"""Canonical Install SOP v1 schema access with an audited byte-for-byte copy."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from typing import Any

CORE_2320_SCHEMA_SHA256 = "3ca25788439917b4d4c0617230a762f9797756b5b54f45c8c4149f975b90f904"


def load_install_sop_schema() -> dict[str, Any]:
    from dcc_mcp_core.deployment import load_install_sop_schema as load_shared_schema

    resource = files("dcc_mcp_zbrush").joinpath("schemas").joinpath("adapter-install-sop-v1.schema.json")
    schema_bytes = resource.read_bytes()
    if hashlib.sha256(schema_bytes).hexdigest() != CORE_2320_SCHEMA_SHA256:
        raise RuntimeError("Bundled Install SOP schema bytes do not match the reviewed Core #2320 resource")
    bundled = json.loads(schema_bytes)
    shared = load_shared_schema()
    if shared != bundled:
        raise RuntimeError("Installed Core Install SOP schema differs from the reviewed canonical resource")
    return shared
