"""Guard the package layout and transport boundaries."""

from __future__ import annotations

import importlib
from pathlib import Path

from imeeting.settings import BASE_DIR


def test_backend_uses_src_layout() -> None:
    backend = Path(__file__).resolve().parents[1]
    assert (backend / "src" / "imeeting" / "main.py").is_file()
    assert not (backend / "imeeting").exists()
    assert BASE_DIR == backend


def test_transport_modules_are_grouped_under_api() -> None:
    assert importlib.import_module("imeeting.api.http.routes.auth")
    assert importlib.import_module("imeeting.api.ws.stream")

