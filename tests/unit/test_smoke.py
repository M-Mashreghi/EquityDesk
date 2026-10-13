"""Smoke tests: the package installs and every layer imports."""

from __future__ import annotations

import importlib

import pytest

import equitydesk

LAYERS = [
    "equitydesk.domain",
    "equitydesk.application",
    "equitydesk.infrastructure",
    "equitydesk.analytics",
    "equitydesk.reporting",
    "equitydesk.api",
    "equitydesk.dashboard",
]


def test_version_exposed() -> None:
    assert equitydesk.__version__ == "0.1.0"


@pytest.mark.parametrize("module", LAYERS)
def test_layer_imports(module: str) -> None:
    assert importlib.import_module(module) is not None
