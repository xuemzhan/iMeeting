"""Compose all HTTP route modules in their externally visible order."""

from fastapi import APIRouter

from .routes import (
    admin_ops,
    auth,
    config,
    export_jobs,
    glossary,
    meeting_extras,
    meetings,
    members,
    minutes_templates,
    system,
    voiceprints,
)

api_router = APIRouter()
for module in (
    auth,
    admin_ops,
    config,
    export_jobs,
    members,
    glossary,
    meeting_extras,
    meetings,
    minutes_templates,
    system,
    voiceprints,
):
    api_router.include_router(module.router)

__all__ = ["api_router"]

