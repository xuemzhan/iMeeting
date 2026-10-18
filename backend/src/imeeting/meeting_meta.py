"""口语录入的会议元信息提取（前 N 分钟）。"""

from __future__ import annotations

import logging
import re
from collections import Counter

from sqlmodel import select

from .db import session_scope
from .llm.client import LLMClient, LLMError, extract_json
from .models import Meeting, Segment
from .settings import get_config

logger = logging.getLogger(__name__)


DEFAULT_TITLE_RE = re.compile(r"^\d{1,2}月\d{1,2}日 .+ 会议$")
_LECTURE_HINTS = ("前沿", "研究", "报告", "讲座", "培训", "分享", "专题")


def _guess_meeting_type(
    title: str, agenda: str, participants: list[str]
) -> str:
    """单人 + 前沿/研究/报告类主题，多半是讲座而非“其他”。"""
    if len(participants) > 1:
        return ""
    text = f"{title or ''} {agenda or ''}"
    return "讲座" if any(hint in text for hint in _LECTURE_HINTS) else ""


def backfill_participants(meeting_id: int) -> list[str]:
    """会后用已识别的真实姓名补齐参会人。

    单人报告里 LLM 已经能识别出「陈建峰」，但 `participants` 仍可能为空；
    这会让历史列表、检索和纪要元信息不一致。这里只回填非「说话人 N」
    的名称，并保留用户已经手填的内容。
    """
    with session_scope() as db:
        meeting = db.get(Meeting, meeting_id)
        if meeting is None:
            return []
        existing = [
            item.strip()
            for item in (meeting.participants or "").replace("，", ",").split(",")
            if item.strip()
        ]
        if existing:
            return existing
        labels = db.exec(
            select(Segment.speaker).where(Segment.meeting_id == meeting_id)
        ).all()
        counter = Counter(
            str(label).strip()
            for label in labels
            if label
            and not str(label).startswith(("说话人", "疑似", "未知"))
            and str(label).strip()
            and not str(label).strip().isdigit()
        )
        names = [name for name, _count in counter.most_common()]
        if names:
            meeting.participants = ", ".join(names)[:1000]
            db.add(meeting)
            db.commit()
        return names


async def capture_meeting_metadata(meeting_id: int) -> dict | None:
    """读取开场转写，提取主题/参会人/议程；失败返回 None。"""
    minutes = int(get_config("meeting.metadata_capture_minutes") or 0)
    if minutes <= 0:
        return None
    window_ms = minutes * 60 * 1000
    min_chars = int(get_config("meeting.metadata_min_chars") or 0)

    with session_scope() as db:
        meeting = db.get(Meeting, meeting_id)
        if meeting is None or meeting.metadata_captured:
            return None
        segments = db.exec(
            select(Segment)
            .where(Segment.meeting_id == meeting_id, Segment.start_ms <= window_ms)
            .order_by(Segment.start_ms, Segment.seq, Segment.id)
        ).all()
        text = "\n".join(s.text for s in segments if (s.text or "").strip())
        speakers = sorted({s.speaker for s in segments if s.speaker})
        if len(text) < min_chars:
            return None

    result: dict | None = None
    client = LLMClient()
    if client.configured:
        prompt = (
            "这是会议开场几分钟的转写。请提取会议元信息，只依据原文，不要编造。\n"
            '输出 JSON：{"title":"会议主题","participants":["姓名"],'
            '"agenda":"议程摘要","meeting_type":"周会/评审/访谈/培训/讲座/汇报/其他"}\n\n'
            f"转写：\n---\n{text[:3000]}\n---"
        )
        try:
            raw = await client.chat(
                [{"role": "user", "content": prompt}],
                json_mode=True,
                meeting_id=meeting_id,
                purpose="meeting_meta",
            )
            parsed = extract_json(raw)
            if parsed:
                result = {
                    "title": str(parsed.get("title") or "").strip()[:200],
                    "participants": [
                        str(p).strip()[:32]
                        for p in (parsed.get("participants") or [])
                        if str(p).strip()
                    ],
                    "agenda": str(parsed.get("agenda") or "").strip()[:2000],
                    "meeting_type": str(parsed.get("meeting_type") or "").strip()[:64],
                }
        except LLMError:
            logger.warning("会议元信息提取失败，使用启发式结果")

    if result is None:
        first = text.strip().splitlines()[0] if text.strip() else ""
        result = {
            "title": first.strip()[:24],
            "participants": speakers,
            "agenda": first.strip()[:120],
            "meeting_type": "",
        }

    if result["meeting_type"] in ("", "其他"):
        guessed = _guess_meeting_type(
            result["title"],
            result["agenda"],
            result["participants"] or speakers,
        )
        if guessed:
            result["meeting_type"] = guessed

    with session_scope() as db:
        meeting = db.get(Meeting, meeting_id)
        if meeting is None or meeting.metadata_captured:
            return None
        if result["title"] and DEFAULT_TITLE_RE.match(meeting.title):
            meeting.title = result["title"]
        if result["participants"]:
            meeting.participants = ", ".join(result["participants"])[:1000]
        if result["agenda"]:
            meeting.agenda = result["agenda"]
        if result["meeting_type"]:
            meeting.meeting_type = result["meeting_type"]
        meeting.metadata_captured = True
        db.add(meeting)
        db.commit()
        return {
            "meeting_id": meeting_id,
            "title": meeting.title,
            "participants": result["participants"],
            "agenda": meeting.agenda,
            "meeting_type": meeting.meeting_type,
        }
