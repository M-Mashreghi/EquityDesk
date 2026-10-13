"""Architecture guardrails: enforce dependency direction between layers via static import scan."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "equitydesk"

UI_AND_IO = {"fastapi", "starlette", "uvicorn", "streamlit", "httpx", "requests", "sqlite3"}
LLM_SDKS = {"anthropic", "openai"}

# layer -> forbidden top-level modules / equitydesk sub-packages
RULES: dict[str, set[str]] = {
    "domain": UI_AND_IO
    | LLM_SDKS
    | {
        "equitydesk.application",
        "equitydesk.infrastructure",
        "equitydesk.analytics",
        "equitydesk.reporting",
        "equitydesk.api",
        "equitydesk.dashboard",
        "equitydesk.config",
        "equitydesk.bootstrap",
    },
    "analytics": UI_AND_IO
    | LLM_SDKS
    | {
        "equitydesk.application",
        "equitydesk.infrastructure",
        "equitydesk.reporting",
        "equitydesk.api",
        "equitydesk.dashboard",
        "equitydesk.bootstrap",
    },
    "application": UI_AND_IO
    | LLM_SDKS
    | {
        "equitydesk.infrastructure",
        "equitydesk.api",
        "equitydesk.dashboard",
        "equitydesk.bootstrap",
    },
    "reporting": UI_AND_IO
    | LLM_SDKS
    | {
        "equitydesk.infrastructure",
        "equitydesk.api",
        "equitydesk.dashboard",
        "equitydesk.bootstrap",
    },
    "infrastructure": {"fastapi", "streamlit", "equitydesk.api", "equitydesk.dashboard"},
    "api": {"streamlit", "sqlite3", "equitydesk.dashboard", "equitydesk.analytics"},
    # The dashboard is a thin client: it talks to the API, never to analytics or storage.
    "dashboard": {
        "sqlite3",
        "fastapi",
        "equitydesk.infrastructure",
        "equitydesk.analytics",
        "equitydesk.application",
        "equitydesk.reporting",
        "equitydesk.bootstrap",
    }
    | LLM_SDKS,
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _violations(layer: str) -> list[str]:
    forbidden = RULES[layer]
    found = []
    for file in sorted((PACKAGE_ROOT / layer).rglob("*.py")):
        for name in _imports(file):
            for bad in forbidden:
                if name == bad or name.startswith(bad + "."):
                    found.append(f"{file.relative_to(PACKAGE_ROOT)} imports {name}")
    return found


@pytest.mark.parametrize("layer", sorted(RULES))
def test_layer_respects_dependency_rules(layer: str) -> None:
    assert (PACKAGE_ROOT / layer).is_dir(), f"missing layer package {layer}"
    assert _violations(layer) == []


def test_scanner_detects_violation(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("import fastapi\nfrom equitydesk.infrastructure.db import x\n", encoding="utf-8")
    assert _imports(bad) == {"fastapi", "equitydesk.infrastructure.db"}
