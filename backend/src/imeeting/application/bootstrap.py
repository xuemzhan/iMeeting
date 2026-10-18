"""Application database bootstrap and startup maintenance."""

from __future__ import annotations

import logging

from ..db import init_db
from ..meeting_cleanup import purge_expired_meetings, purge_retention_expired
from .seeds import ensure_builtin_glossary_terms, ensure_builtin_minutes_templates

logger = logging.getLogger(__name__)


def initialize_app_database() -> None:
    """Prepare schema, seed application data and run startup cleanup."""
    init_db()
    ensure_builtin_minutes_templates()
    ensure_builtin_glossary_terms()
    try:
        purge_expired_meetings()
        purge_retention_expired()
    except Exception:
        logger.exception("回收站过期清理失败")

