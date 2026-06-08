"""Synchronise AGENTS.md, .claude/CLAUDE.md, and llms.txt from docs/agent-docs.yaml.

Usage:
    python tools/sync_agent_docs.py          # write all three files
    python tools/sync_agent_docs.py --check   # exit 1 if any file differs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "docs" / "agent-docs.yaml"
OUTPUTS = {
    "AGENTS.md": REPO_ROOT / "AGENTS.md",
    ".claude/CLAUDE.md": REPO_ROOT / ".claude" / "CLAUDE.md",
    "llms.txt": REPO_ROOT / "llms.txt",
}

# ── helpers ──────────────────────────────────────────────────────────────


def _load() -> dict:
    with open(SOURCE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _fmt_table(headers: list[str], rows: list[list[str]]) -> str:
    """Return a GFM table string."""
    sep = "|" + "|".join("---" for _ in headers) + "|"
    hdr = "|" + "|".join(headers) + "|"
    body = "\n".join("|" + "|".join(row) + "|" for row in rows)
    return hdr + "\n" + sep + "\n" + body


# ── generators ───────────────────────────────────────────────────────────


def _generate_agents_md(data: dict) -> str:
    p = data["project"]
    t = data["target_host"]
    lines = [
        f"# AGENTS.md — {p['name']}",
        "",
        "> Navigation map for AI agents. Detailed API lives in `README.md`.",
        "",
        "## Quick facts",
        "",
    ]
    lines.append(f"- **Target host:** {t['software']} **{t['version']}** with embedded Python SDK (`{t['sdk']}`)")
    lines.append(f"- **CLI default:** sidecar (`{p['name']} --mode sidecar`) via `bridge/plugin/mcp_socket_bridge.py`")
    lines.append(f"- **Plugin default:** embedded Python inside {t['software']} (`DCC_MCP_ZBRUSH_MODE=embedded`)")
    lines.append(
        f"- **Library auto-detect:** `resolve_mode()` detects {t['software']} availability; falls back to sidecar"
    )
    for note in data["do_not_assume"]:
        lines.append(f"- **Do not assume:** {note}")

    lines.extend(
        [
            "",
            "## Skills-first workflow",
            "",
            "```",
        ]
    )
    for step in data["skills_workflow"]["steps"]:
        lines.append(f"{step}")
    lines.append("```")
    lines.append("")
    lines.append(f"Default minimal mode loads {', '.join(data['skills_workflow']['default_minimal'])}.")
    lines.append("")

    lines.extend(
        [
            "## Key files",
            "",
            _fmt_table(
                ["Path", "Role"],
                [[kf["path"], kf["role"]] for kf in data["key_files"]],
            ),
            "",
        ]
    )

    lines.extend(["## ZBrush Python VM constraints", ""])
    for c in data["zbrush_vm_constraints"]:
        lines.append(f"- {c}")

    lines.extend(
        [
            "",
            "## Agent entry points",
            "",
            _fmt_table(
                ["File", "Role"],
                [[ep["file"], ep["role"]] for ep in data["agent_entry_points"]],
            ),
            "",
        ]
    )

    lines.extend(["## External docs", ""])
    for doc in data["external_docs"]:
        lines.append(f"- {doc['label']}: {doc['url']}")

    return "\n".join(lines) + "\n"


def _generate_claude_md(data: dict) -> str:
    p = data["project"]
    t = data["target_host"]
    lines = [
        f"# CLAUDE.md — {p['name']} (Claude Code entry)",
        "",
        "> Claude-specific entry point. Full navigation map: `AGENTS.md`.",
        "",
        "## Quick facts",
        "",
    ]
    lines.append(f"- **Target host:** {t['software']} **{t['version']}** with embedded Python SDK (`{t['sdk']}`)")
    lines.append(f"- **Primary mode:** embedded Python inside {t['software']} (`DCC_MCP_ZBRUSH_MODE=embedded`)")
    lines.append("- **Fallback mode:** sidecar MCP process + `bridge/plugin/mcp_socket_bridge.py`")
    for note in data["do_not_assume"]:
        lines.append(f"- **Do not assume:** {note}")

    lines.extend(
        [
            "",
            "## Cursor / Claude Desktop MCP config",
            "",
            "```json",
            "{",
            '  "mcpServers": {',
            f'    "{p["name"]}": {{',
            f'      "url": "{data["mcp_config"]["direct"]["url"]}"',
            "    }",
            "  }",
            "}",
            "```",
            "",
            "With gateway (multi-DCC):",
            "",
            "```json",
            "{",
            '  "mcpServers": {',
            f'    "{p["name"]}": {{',
            f'      "url": "{data["mcp_config"]["gateway"]["url"]}"',
            "    }",
            "  }",
            "}",
            "```",
        ]
    )

    lines.extend(
        [
            "",
            "## Skills-first workflow",
            "",
            "```",
        ]
    )
    for step in data["skills_workflow"]["steps"]:
        lines.append(f"{step}")
    lines.append("```")
    lines.append("")
    lines.append(f"Default minimal loads {', '.join(data['skills_workflow']['default_minimal'])}.")
    lines.append("")

    lines.extend(
        [
            "## Key files",
            "",
            _fmt_table(
                ["Path", "Role"],
                [[kf["path"], kf["role"]] for kf in data["key_files"]],
            ),
            "",
        ]
    )

    lines.extend(["## ZBrush Python VM constraints", ""])
    for c in data["zbrush_vm_constraints"]:
        lines.append(f"- {c}")

    lines.extend(["", "## External docs", ""])
    for doc in data["external_docs"]:
        lines.append(f"- {doc['label']}: {doc['url']}")

    return "\n".join(lines) + "\n"


def _generate_llms_txt(data: dict) -> str:
    p = data["project"]
    t = data["target_host"]
    lines = [
        f"# {p['name']}",
        "",
        "> Agent entry point. Read `AGENTS.md` for full navigation map.",
        "",
        "## Supported modes",
        "",
        _fmt_table(
            ["Mode", "When to use", "Stack"],
            [
                [
                    "**Embedded (recommended)**",
                    f"{t['software']} {t['version']} with Python SDK",
                    data["modes"]["embedded"]["stack"],
                ],
                [
                    "**Sidecar + socket plugin**",
                    "External MCP process / restricted installs",
                    data["modes"]["sidecar"]["stack"],
                ],
            ],
        ),
        "",
        "## Distribution artifacts",
        "",
        "There are three independent distribution channels. Each covers a different scope:",
        "",
        _fmt_table(
            ["Artifact", "Scope", "Install command"],
            [
                [
                    f"**PyPI wheel** (`{p['name']}`)",
                    a["scope"],
                    f"`{a['install']}`",
                ]
                for a in data["distribution"]["artifacts"]
            ],
        ),
        "",
        data["distribution"]["plugin_zip_note"],
        "",
        "## Install steps (embedded)",
        "",
    ]
    for group in data["install_steps_embedded"]:
        lines.append(f"### {group['title']}")
        lines.append("")
        for i, step in enumerate(group["steps"], 1):
            lines.append(f"{i}. {step}")
        lines.append("")

    lines.append(f"MCP endpoint: `{data['mcp_endpoint']}`")
    lines.append("")

    # MCP config
    mcp_label_direct = data["mcp_config"]["direct"]["label"]
    mcp_url_direct = data["mcp_config"]["direct"]["url"]
    mcp_url_gateway = data["mcp_config"]["gateway"]["url"]
    lines.extend(
        [
            "## MCP config snippets",
            "",
            f"### {mcp_label_direct}",
            "",
            "```json",
            "{",
            '  "mcpServers": {',
            f'    "{p["name"]}": {{',
            f'      "url": "{mcp_url_direct}"',
            "    }",
            "  }",
            "}",
            "```",
            "",
            "### With gateway (multi-DCC)",
            "",
            "```json",
            "{",
            '  "mcpServers": {',
            f'    "{p["name"]}": {{',
            f'      "url": "{mcp_url_gateway}"',
            "    }",
            "  }",
            "}",
            "```",
            "",
        ]
    )

    # env vars table
    lines.extend(
        [
            "## Environment variables",
            "",
            _fmt_table(
                ["Variable", "Default", "Purpose"],
                [[ev["name"], f"`{ev['default']}`", ev["purpose"]] for ev in data["env_vars"]],
            ),
            "",
            "## Bundled skills",
            "",
            _fmt_table(
                ["Skill", "Tools"],
                [[s["name"], ", ".join(s["tools"]) if s["tools"] else "—"] for s in data["bundled_skills"]],
            ),
            "",
            "## Health check",
            "",
        ]
    )
    for hc in data["health_checks"]:
        lines.extend(
            [
                "```bash",
                hc,
                "```",
                "",
            ]
        )

    lines.extend(
        [
            "## Common failure modes",
            "",
            _fmt_table(
                ["Problem", "Likely cause", "Fix"],
                [[fm["problem"], fm["cause"], fm["fix"]] for fm in data["failure_modes"]],
            ),
            "",
        ]
    )

    return "\n".join(lines)


def _check_drift(data: dict, key: str, path: Path) -> bool:
    """Return True if generated content matches file on disk."""
    gen = GENERATORS[key](data)
    if not path.exists():
        return False
    existing = path.read_text(encoding="utf-8")
    return gen == existing


GENERATORS: dict[str, callable] = {
    "AGENTS.md": _generate_agents_md,
    ".claude/CLAUDE.md": _generate_claude_md,
    "llms.txt": _generate_llms_txt,
}


def _write_all(data: dict) -> None:
    for key, path in OUTPUTS.items():
        gen = GENERATORS[key](data)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(gen, encoding="utf-8")
        print(f"  ✓  {key}")


def _check_all(data: dict) -> bool:
    ok = True
    for key, path in OUTPUTS.items():
        if _check_drift(data, key, path):
            print(f"  ✓  {key}")
        else:
            print(f"  ✗  {key} — drift detected")
            ok = False
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronise agent docs from docs/agent-docs.yaml")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check for drift without writing (exit 1 if drift found)",
    )
    opts = parser.parse_args()

    if not SOURCE.exists():
        print(f"Error: source file not found: {SOURCE}", file=sys.stderr)
        sys.exit(1)

    data = _load()

    if opts.check:
        ok = _check_all(data)
        if not ok:
            print("\nDrift detected — run `python tools/sync_agent_docs.py` to regenerate.", file=sys.stderr)
            sys.exit(1)
    else:
        print("Writing agent docs …")
        _write_all(data)


if __name__ == "__main__":
    main()
