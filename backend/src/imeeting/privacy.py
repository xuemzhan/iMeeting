"""会议文本脱敏。音频本身不在此处处理。"""

from __future__ import annotations

import json
import re

PATTERNS = [
    ("邮箱", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("身份证号", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("银行卡号", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("IP地址", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
]


def redact_text(text: str) -> tuple[str, int]:
    changed = 0
    for label, pattern in PATTERNS:
        text, count = pattern.subn(f"[{label}]", text)
        changed += count
    return text, changed


def redact_json(raw: str) -> tuple[str, int]:
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return raw, 0
    total = 0

    def walk(value):
        nonlocal total
        if isinstance(value, str):
            result, count = redact_text(value)
            total += count
            return result
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        return value

    return json.dumps(walk(data), ensure_ascii=False), total


def apply_meeting_redaction(meeting_id: int) -> int:
    from sqlmodel import select

    from .db import session_scope
    from .models import Meeting, Minutes, Segment, utcnow

    changed = 0
    with session_scope() as db:
        meeting = db.get(Meeting, meeting_id)
        if meeting is None:
            return 0
        for segment in db.exec(
            select(Segment).where(Segment.meeting_id == meeting_id)
        ).all():
            text, count = redact_text(segment.text or "")
            if count:
                if not segment.original_text:
                    segment.original_text = segment.text
                segment.text = text
                segment.edited_at = utcnow()
                db.add(segment)
            changed += count
        for row in db.exec(
            select(Minutes).where(Minutes.meeting_id == meeting_id)
        ).all():
            summary, c1 = redact_text(row.summary or "")
            topics, c2 = redact_json(row.topics or "[]")
            todos, c3 = redact_json(row.todos or "[]")
            raw, c4 = redact_text(row.raw_markdown or "")
            count = c1 + c2 + c3 + c4
            if count:
                row.summary = summary
                row.topics = topics
                row.todos = todos
                row.raw_markdown = raw
                db.add(row)
            changed += count
        if changed:
            meeting.redacted_at = utcnow()
            db.add(meeting)
    return changed
