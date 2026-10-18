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
    assert importlib.import_module("imeeting.api.http.router").api_router
    assert importlib.import_module("imeeting.api.ws.stream")


def test_application_workflows_are_separated_from_transport() -> None:
    assert importlib.import_module("imeeting.application.jobs").enqueue_job
    assert importlib.import_module("imeeting.application.exports").run_export_job
    assert importlib.import_module("imeeting.application.meeting_access").owned_meeting
    assert importlib.import_module("imeeting.application.bootstrap").initialize_app_database
    assert importlib.import_module("imeeting.application.meeting_sessions").persist_segments
    assert importlib.import_module("imeeting.application.seeds").ensure_builtin_glossary_terms
    assert importlib.import_module("imeeting.export.meeting_export").render_meeting_export

