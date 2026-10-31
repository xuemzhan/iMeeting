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
    persons,
    system,
    voiceprint_templates,
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
    # 静态路径（/voiceprints/conflicts、/voiceprints/{id}/templates）先于
    # 录入/改名那组注册，避免被 ``/{voiceprint_id}`` 之类的动态段抢先匹配
    voiceprint_templates,
    voiceprints,
    # 人员本体：/meetings/{id}/participants 与 /persons
    persons,
):
    api_router.include_router(module.router)

__all__ = ["api_router"]
