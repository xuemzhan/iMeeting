"""会中渐进纪要：定时基于已落库转写生成阶段草稿。

设计要点
--------
- 只读已落库片段（依赖增量落库），不碰运行中的内存状态；
- 每版草稿单独一行、版本号递增，终稿另起一版，历史可追溯；
- 从第二版起走「上一版纪要 + 新增转写」滚动合并，避免每 5 分钟对全量
  转写重跑 map-reduce（真实长测里一场 65 分钟会议产生了 54 次 LLM 调用）；
- 任何失败都只影响本轮草稿，绝不打断转写。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

from sqlmodel import func, select

from ..db import session_scope
from ..models import ActionItem, Minutes, Segment, utcnow
from ..settings import get_config
from .client import LLMClient, extract_json
from .minutes import _finalize_minutes, generate_minutes
from .types import TranscriptLine

logger = logging.getLogger(__name__)


def load_transcript_lines(meeting_id: int) -> list[TranscriptLine]:
    """按全局时间轴读取当前已落库的转写。"""
    with session_scope() as db:
        rows = db.exec(
            select(Segment)
            .where(Segment.meeting_id == meeting_id)
            .order_by(Segment.start_ms, Segment.seq, Segment.id)
        ).all()
        return [
            TranscriptLine(
                start_ms=s.start_ms,
                speaker=s.speaker,
                text=s.text,
                end_ms=s.end_ms,
                segment_id=s.segment_id,
                segment_ids=[s.segment_id],
            )
            for s in rows
            if (s.text or "").strip()
        ]


def _latest(meeting_id: int, *, draft: bool | None = None) -> Minutes | None:
    with session_scope() as db:
        stmt = select(Minutes).where(Minutes.meeting_id == meeting_id)
        if draft is True:
            stmt = stmt.where(Minutes.is_draft == True)  # noqa: E712
        elif draft is False:
            stmt = stmt.where(Minutes.is_draft == False)  # noqa: E712
        row = db.exec(
            stmt.order_by(Minutes.version.desc(), Minutes.created_at.desc())
        ).first()
        if row is not None:
            db.expunge(row)
        return row


def save_generated_minutes(
    meeting_id: int,
    result: dict,
    *,
    is_draft: bool,
    transcript_end_ms: int = 0,
) -> Minutes:
    """写入一版纪要，版本号自增；不覆盖历史版本。"""
    with session_scope() as db:
        latest = db.exec(
            select(func.max(Minutes.version)).where(Minutes.meeting_id == meeting_id)
        ).one()
        version = int(latest or 0) + 1
        row = Minutes(
            meeting_id=meeting_id,
            summary=str(result.get("summary", "")),
            topics=json.dumps(result.get("topics", []), ensure_ascii=False),
            todos=json.dumps(result.get("todos", []), ensure_ascii=False),
            raw_markdown=str(result.get("raw_markdown", "")),
            model_used=str(result.get("model", "")),
            status="ready",
            version=version,
            is_draft=is_draft,
            transcript_end_ms=max(0, int(transcript_end_ms)),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        if not is_draft:
            _sync_action_items(db, meeting_id, row)
            db.commit()
            # 第二次 commit 同样会 expire 所有属性；先重新加载，再脱离
            # Session，否则调用方读取 version/summary 时会报 DetachedInstanceError。
            db.refresh(row)
        # 脱离 Session 后仍可安全读取属性（commit 会 expire 属性的坑）
        db.expunge(row)
        return row


def _sync_action_items(db, meeting_id: int, row: Minutes) -> None:
    """把终稿里的待办同步到 ActionItem，保留已有状态。"""
    try:
        todos = json.loads(row.todos or "[]")
    except json.JSONDecodeError:
        return
    if not isinstance(todos, list):
        return
    for item in todos:
        if not isinstance(item, dict):
            continue
        task = str(item.get("task") or "").strip()
        if not task:
            continue
        existing = db.exec(
            select(ActionItem).where(
                ActionItem.meeting_id == meeting_id,
                ActionItem.task == task,
                ActionItem.source == "auto",
            )
        ).first()
        if existing is None:
            db.add(
                ActionItem(
                    meeting_id=meeting_id,
                    task=task[:500],
                    owner=str(item.get("owner") or "")[:64],
                    due=str(item.get("due") or "")[:64],
                    evidence=json.dumps(
                        item.get("evidence") or [], ensure_ascii=False
                    ),
                    source="auto",
                    source_version=row.version,
                )
            )
        else:
            existing.owner = str(item.get("owner") or "")[:64]
            existing.due = str(item.get("due") or "")[:64]
            existing.evidence = json.dumps(
                item.get("evidence") or [], ensure_ascii=False
            )
            existing.source_version = row.version
            existing.updated_at = utcnow()
            db.add(existing)


def minutes_payload(row: Minutes) -> dict:
    """给 WebSocket 推送用的轻量结构（与 MinutesOut 字段对齐）。"""
    return {
        "id": row.id,
        "meeting_id": row.meeting_id,
        "summary": row.summary,
        "topics": json.loads(row.topics or "[]"),
        "todos": json.loads(row.todos or "[]"),
        "raw_markdown": row.raw_markdown or "",
        "model_used": row.model_used,
        "status": row.status,
        "error": row.error,
        "version": row.version,
        "is_draft": row.is_draft,
        "edited": row.edited,
        "created_at": row.created_at.isoformat(),
    }


def _format_lines(lines: list[TranscriptLine]) -> str:
    out = []
    for line in lines:
        total = line.start_ms // 1000
        out.append(
            f"[{total // 60:02d}:{total % 60:02d}] "
            f"{line.speaker or '发言'}：{line.text}"
        )
    return "\n".join(out)


async def _rolling_update(
    previous: Minutes,
    new_lines: list[TranscriptLine],
    knowledge_context: str = "",
) -> dict | None:
    """把上一版纪要与新增转写合并成一版新纪要（单次调用）。"""
    client = LLMClient()
    if not client.configured:
        return None
    model = str(get_config("llm.draft_model") or "") or client.model
    prev_payload = {
        "summary": previous.summary,
        "topics": json.loads(previous.topics or "[]"),
        "todos": json.loads(previous.todos or "[]"),
    }
    from ..glossary import prompt_section

    prompt_prefix = prompt_section()
    if knowledge_context:
        prompt_prefix += knowledge_context + "\n\n"
    prompt_body = (
        "你是会议记录员。这是同一场会议的最新进展。请把「上一版纪要」与"
        "「新增转写」合并成更新后的阶段性纪要。\n"
        "要求：\n"
        "1. 保留已经确认的议题与结论，不要丢掉已有待办；\n"
        "2. 把新增信息补充到对应议题，避免重复；\n"
        "3. 只基于给定内容，不要编造；\n"
        "4. 数字、日期、专有名词和数量词只能来自转写；不要自行添加"
        "“三大/四大/五项”等数量词，列举项与数量词不一致时删除数量词；\n"
        "5. 结合知识库和上下文修正上一版中明显的 ASR 同音/错字；"
        "不能确认的实体、年份和事件细节改为概括或省略；\n"
        "6. 输出同样的 JSON 结构，并额外给出 corrections 字段："
        '[{"from":"转写中的错误片段","to":"知识库规范词","confidence":0.9}]；'
        "只列有把握的项，不确定就不要列。\n\n"
        f"上一版纪要：\n{json.dumps(prev_payload, ensure_ascii=False)}\n\n"
        f"新增转写：\n---\n{_format_lines(new_lines)}\n---"
    )
    prompt = prompt_prefix + prompt_body
    raw = await client.chat(
        [{"role": "user", "content": prompt}],
        json_mode=True,
        model=model,
        meeting_id=previous.meeting_id,
        purpose="rolling_draft",
    )
    parsed = extract_json(raw)
    if parsed is None:
        logger.warning("滚动纪要输出无法解析为 JSON，跳过本版")
        return None
    from ..evidence import preserve_previous_evidence

    result = _finalize_minutes(parsed, new_lines, model)
    return preserve_previous_evidence(prev_payload, result)


async def generate_progressive_draft(
    meeting_id: int, *, min_chars: int = 300
) -> dict | None:
    """生成一版会中草稿；内容不足或 LLM 未配置时返回 None。"""
    lines = await asyncio.to_thread(load_transcript_lines, meeting_id)
    if not lines:
        return None
    # 优先使用最新版本作为滚动基线。人工在会中生成的“终稿”如果带有
    # transcript_end_ms，也应作为后续渐进更新的语义基线，避免下一版又
    # 回退到更早、更粗糙的草稿。旧数据没有 end_ms 时回退到最新草稿。
    previous = await asyncio.to_thread(_latest, meeting_id)
    if previous is not None and previous.transcript_end_ms <= 0:
        previous = await asyncio.to_thread(_latest, meeting_id, draft=True)
    prev_end = previous.transcript_end_ms if previous is not None else 0
    new_lines = (
        [line for line in lines if line.start_ms >= prev_end]
        if previous is not None and prev_end > 0
        else lines
    )
    new_chars = sum(len(line.text) for line in new_lines)
    if new_chars < min_chars:
        return None

    if previous is not None and previous.summary:
        from .knowledge_context import build_knowledge_context

        try:
            knowledge_context = await asyncio.to_thread(
                build_knowledge_context, meeting_id, lines
            )
        except Exception:
            logger.exception("构建知识库校正上下文失败 meeting=%s", meeting_id)
            knowledge_context = ""
        result = await _rolling_update(
            previous, new_lines, knowledge_context
        )
        if result is None:
            return None
        # 质量护栏：明显短于上一版时保留上一版，避免「越更越短」
        if len(result.get("summary", "")) < max(
            120, int(len(previous.summary) * 0.5)
        ):
            logger.info("滚动纪要过短，保留上一版 meeting=%s", meeting_id)
            return None
    else:
        result = await generate_minutes(
            lines, model_label="实时草稿", draft=True, meeting_id=meeting_id
        )

    end_ms = max(
        (line.end_ms or (line.start_ms + 1000)) for line in lines
    )
    row = await asyncio.to_thread(
        save_generated_minutes,
        meeting_id,
        result,
        is_draft=True,
        transcript_end_ms=end_ms,
    )
    logger.info(
        "渐进纪要已生成 meeting=%s version=%s new_chars=%s",
        meeting_id,
        row.version,
        new_chars,
    )
    return minutes_payload(row)


# ---------------------------------------------------------------------------
# 会议结束自动生成终稿
# ---------------------------------------------------------------------------

_inflight_lock = threading.Lock()
_final_inflight: set[int] = set()


def _begin_final(meeting_id: int) -> bool:
    with _inflight_lock:
        if meeting_id in _final_inflight:
            return False
        _final_inflight.add(meeting_id)
        return True


def _end_final(meeting_id: int) -> None:
    with _inflight_lock:
        _final_inflight.discard(meeting_id)


async def generate_final_minutes(
    meeting_id: int, *, min_chars: int = 200
) -> dict | None:
    """生成终稿；已有终稿或正在生成时返回 None。"""
    if not _begin_final(meeting_id):
        return None
    try:
        lines = await asyncio.to_thread(load_transcript_lines, meeting_id)
        if sum(len(line.text) for line in lines) < min_chars:
            return None
        end_ms = max(
            (line.end_ms or (line.start_ms + 1000)) for line in lines
        )
        existing = await asyncio.to_thread(_latest, meeting_id, draft=False)
        if (
            existing is not None
            and existing.status == "ready"
            and existing.transcript_end_ms >= end_ms
        ):
            return None
        result = await generate_minutes(
            lines, model_label="终稿", meeting_id=meeting_id
        )
        row = await asyncio.to_thread(
            save_generated_minutes,
            meeting_id,
            result,
            is_draft=False,
            transcript_end_ms=end_ms,
        )
        logger.info("会议终稿已生成 meeting=%s version=%s", meeting_id, row.version)
        return minutes_payload(row)
    finally:
        _end_final(meeting_id)
