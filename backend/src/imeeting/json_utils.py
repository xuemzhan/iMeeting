"""Small JSON helpers shared by API and export layers."""

from __future__ import annotations

import json


def safe_json_list(raw: str | None) -> list:
    try:
        parsed = json.loads(raw or "[]")
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []

