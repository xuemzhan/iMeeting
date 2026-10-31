"""会中参会人介绍抽取（REQ-001）。

流程：候选片段 → LLM 结构化抽取（或规则兜底）→ **证据强校验** → 写入人员本体
→ 回写兼容字段 → 审计。

两条硬约束（都是为了「宁缺勿错」）：

1. 姓名必须出现在 evidence 的 ``quote`` 原文里；
2. ``quote`` 必须能在本场片段文本里找到（去标点后子串匹配，或 ≥80% 字符重合）。

不满足就整条丢弃，并在审计里记 ``declined_reason``，绝不把编造的姓名写进本体。
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Iterable

from sqlmodel import Session, select

from . import persons
from .models import AuditLog, Meeting, Segment
from .settings import get_config

logger = logging.getLogger(__name__)

#: 介绍句式。命中即认为这段值得送进 LLM（宁可多送一次，也别漏掉中途介绍）。
INTRO_KEYWORDS = (
    "我是",
    "我叫",
    "我来自",
    "这位是",
    "这是",
    "给大家介绍",
    "介绍一下",
    "参会的有",
    "在座",
    "负责",
)

#: 规则兜底用的姓名正则（LLM 不可用时）。三条并行：
#:  1) 「我是/我叫 + 姓名」
#:  2) 「这位是/这是/介绍一下 + ……的 + 姓名」
#:  3) 「我是/我叫 + ……的 + 姓名」
_RULE_PATTERNS = (
    re.compile(r"(?:我是|我叫)[，,：:\s]*([\u4e00-\u9fa5]{2,4})"),
    re.compile(r"(?:这位是|这是|给大家介绍一下|介绍一下)[^，。！？]{0,12}?的([\u4e00-\u9fa5]{2,4})"),
    re.compile(r"(?:我是|我叫)[^，。！？]{0,10}?的([\u4e00-\u9fa5]{2,4})"),
)

#: 明显是机构而不是姓名的后缀，规则兜底时排除
_ORG_SUFFIXES = (
    "所",
    "院",
    "部",
    "处",
    "科",
    "司",
    "局",
    "会",
    "室",
    "组",
    "队",
    "公司",
    "集团",
    "中心",
)

#: 明显不是姓名的词，规则兜底时排除
_STOPWORDS = {
    "我们",
    "大家",
    "一下",
    "本次",
    "今天",
    "会议",
    "一个",
    "这个",
    "那个",
    "项目",
    "工作",
    "简单",
}

_PUNCT = re.compile(r"[\s，。！？、；：“”‘’（）()【】\[\]…—\-·.,!?;:\"'']")

#: 单次送入 LLM 的最大字符数
MAX_PROMPT_CHARS = 3000

_PROMPT = (
    "这是一场会议开头的转写（可能夹带中途自我介绍）。请只依据原文，提取被介绍的"
    "参会人员，不要编造。\n"
    "输出 JSON：\n"
    '{"people":[{"name":"姓名","aliases":["称呼"],'
    '"attributes":[{"category":"org|role|domain|contact|identity",'
    '"key":"unit|department|team|title|meeting_role|expertise|project","value":"值"}],'
    '"meeting_role":"主持|主讲|评审|记录|旁听|","introduced_by_speaker":"介绍人",'
    '"evidence":[{"segment_id":"片段id","quote":"原文片段"}],"confidence":0.8}]}\n'
    "规则：\n"
    "1. name 必须是原文出现的真实姓名（不要用“张主任”代替姓名本身，可放进 aliases）；\n"
    "2. evidence.quote 必须逐字来自原文，segment_id 必须来自下方标注；\n"
    "3. 分不清介绍人与被介绍人时，introduced_by_speaker 留空；\n"
    "4. 没有人员信息就返回空数组。\n\n"
    "转写（每行格式 [segment_id] 说话人: 文本）：\n---\n{body}\n---"
)


def _plain(text: str) -> str:
    return _PUNCT.sub("", text or "")


def quote_matches(quote: str, source: str) -> bool:
    """quote 是否来自 source：去标点后子串匹配，或 ≥80% 字符重合。"""
    q = _plain(quote)
    s = _plain(source)
    if not q or not s:
        return False
    if q in s:
        return True
    if len(q) < 6:
        return False
    # 用最长公共子序列长度近似「重合字符数」
    from difflib import SequenceMatcher

    matched = sum(block.size for block in SequenceMatcher(None, q, s).get_matching_blocks())
    return matched / len(q) >= 0.8


def intro_segment_ids(segments: Iterable[Segment]) -> set[str]:
    """命中介绍句式的片段 id。"""
    hits: set[str] = set()
    for seg in segments:
        text = seg.text or ""
        if any(keyword in text for keyword in INTRO_KEYWORDS):
            hits.add(seg.segment_id)
    return hits


def _context_segments(
    segments: list[Segment], *, opening_ms: int, hits: set[str], window: int = 2
) -> list[Segment]:
    """开场窗口内的片段 + 命中句式前后各 ``window`` 句。"""
    chosen: dict[str, Segment] = {}
    for index, seg in enumerate(segments):
        if seg.start_ms <= opening_ms or seg.segment_id in hits:
            chosen[seg.segment_id] = seg
        if seg.segment_id in hits:
            for offset in range(1, window + 1):
                for neighbor in (index - offset, index + offset):
                    if 0 <= neighbor < len(segments):
                        chosen[segments[neighbor].segment_id] = segments[neighbor]
    return sorted(chosen.values(), key=lambda item: (item.start_ms, item.seq))


def _build_body(segments: list[Segment]) -> str:
    lines = [
        f"[{seg.segment_id}] {seg.speaker or '未知'}: {(seg.text or '').strip()}"
        for seg in segments
        if (seg.text or "").strip()
    ]
    body = "\n".join(lines)
    return body[-MAX_PROMPT_CHARS:]


def _auto_extract_count(db: Session, meeting_id: int) -> int:
    rows = db.exec(
        select(AuditLog).where(
            AuditLog.action == "person_extract",
            AuditLog.target == f"meeting:{meeting_id}",
        )
    ).all()
    return len(rows)


def _rule_extract(segments: list[Segment]) -> list[dict]:
    """LLM 不可用时的兜底：正则抽姓名，置信度低，等人工确认。

    机构名（「十四所」「总体院」）不能当姓名：先按机构后缀过滤，再去重。
    """
    people: list[dict] = []
    seen: set[str] = set()
    for seg in segments:
        text = seg.text or ""
        for pattern in _RULE_PATTERNS:
            for match in pattern.finditer(text):
                name = match.group(1).strip()
                if name in _STOPWORDS or len(name) < 2:
                    continue
                if name.endswith(_ORG_SUFFIXES):
                    continue
                if name in seen:
                    continue
                seen.add(name)
                people.append(
                    {
                        "name": name,
                        "aliases": [],
                        "attributes": [],
                        "meeting_role": "",
                        "introduced_by_speaker": seg.speaker or "",
                        "evidence": [
                            {"segment_id": seg.segment_id, "quote": text[:200]}
                        ],
                        "confidence": 0.3,
                    }
                )
    return people


def validate_people(
    raw_people: list[dict], segments_by_id: dict[str, Segment]
) -> tuple[list[dict], list[dict]]:
    """证据校验：返回 (accepted, declined)。

    任何一条不满足「姓名在引文里 + 引文来自本场片段」都丢弃，并给出原因，
    方便在审计里看清是模型编造还是分段对不上。
    """
    accepted: list[dict] = []
    declined: list[dict] = []
    for item in raw_people or []:
        if not isinstance(item, dict):
            declined.append({"reason": "not_object", "raw": str(item)[:120]})
            continue
        name = persons.normalize_name(str(item.get("name") or ""))
        if not (2 <= len(name) <= 32):
            declined.append({"reason": "name_length", "raw": str(item.get("name"))[:32]})
            continue
        evidence = item.get("evidence") or []
        if not isinstance(evidence, list):
            declined.append({"reason": "invalid_evidence", "raw": name})
            continue
        good_evidence: list[dict] = []
        for ev in evidence:
            if not isinstance(ev, dict):
                continue
            seg_id = str(ev.get("segment_id") or "")
            quote = str(ev.get("quote") or "")
            seg = segments_by_id.get(seg_id)
            if seg is None or not quote:
                continue
            if name not in _plain(quote) or name not in _plain(seg.text or ""):
                continue
            if not quote_matches(quote, seg.text or ""):
                continue
            good_evidence.append({"segment_id": seg_id, "quote": quote[:300]})
        if not good_evidence:
            declined.append(
                {
                    "reason": "evidence_mismatch",
                    "raw": name,
                    "evidence": str(evidence)[:200],
                }
            )
            continue
        raw_confidence = item.get("confidence", 0.5)
        if not isinstance(raw_confidence, (int, float)) or isinstance(raw_confidence, bool):
            declined.append({"reason": "invalid_confidence", "raw": name})
            continue
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            declined.append({"reason": "invalid_confidence", "raw": name})
            continue
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            declined.append({"reason": "invalid_confidence", "raw": name})
            continue
        # 无证据的置信度打对折（这里已经要求有证据，保留规则以备扩展）
        confidence = min(1.0, max(0.0, confidence))
        accepted.append(
            {
                "name": name,
                "aliases": [str(alias)[:64] for alias in item.get("aliases", []) if isinstance(alias, str)][:5] if isinstance(item.get("aliases", []), list) else [],
                "attributes": persons.clean_attributes(item.get("attributes") or []) if isinstance(item.get("attributes", []), list) else [],
                "meeting_role": str(item.get("meeting_role") or "")[:16],
                "evidence": good_evidence,
                "confidence": confidence,
            }
        )
    return accepted, declined


async def _call_llm(meeting_id: int, body: str) -> tuple[list[dict], bool]:
    """返回 (people, degraded)。LLM 不可用/失败时走规则兜底标记 degraded。"""
    from .llm.client import LLMClient, LLMError, extract_json

    client = LLMClient()
    if not client.configured:
        return [], True
    try:
        raw = await client.chat(
            [{"role": "user", "content": _PROMPT.replace("{body}", body)}],
            json_mode=True,
            meeting_id=meeting_id,
            purpose="person_extract",
        )
    except LLMError:
        logger.warning("参会人抽取 LLM 失败 meeting=%s", meeting_id)
        return [], True
    parsed = extract_json(raw)
    if not parsed:
        logger.warning("参会人抽取返回非法 JSON meeting=%s", meeting_id)
        return [], True
    people = parsed.get("people") if isinstance(parsed, dict) else None
    if not isinstance(people, list):
        return [], True
    return people, False


async def extract_people(meeting_id: int, *, manual: bool = False) -> dict:
    """Reserve a persisted quota, call LLM without a DB transaction, commit atomically."""
    from .application.person_extraction import reserve
    from .db import engine, session_scope
    from .models import PersonExtractionState

    with Session(engine) as db:
        meeting = db.get(Meeting, meeting_id)
        if meeting is None or meeting.deleted_at is not None:
            raise LookupError(f"会议不存在：{meeting_id}")
        owner_id = meeting.owner_id
        state = db.get(PersonExtractionState, meeting_id)
        query = select(Segment).where(Segment.meeting_id == meeting_id, Segment.is_final.is_(True))
        if not manual and state:
            query = query.where(Segment.id > state.cursor_id)
        segments = list(db.exec(query.order_by(Segment.start_ms, Segment.seq, Segment.id)).all())
        opening_ms = int(get_config("meeting.metadata_capture_minutes")) * 60_000
        hits = intro_segment_ids(segments)
        context = _context_segments(segments, opening_ms=opening_ms, hits=hits)
        if not context:
            context = segments[-20:]
        min_chars = 1 if hits and state and state.auto_count else max(1, int(get_config("meeting.metadata_min_chars")))
        if sum(len(seg.text or "") for seg in context) < min_chars:
            return {"meeting_id": meeting_id, "skipped": "not_enough_text"}
        cursor = max((seg.id or 0 for seg in segments), default=0)
        body = _build_body(context)
        by_id = {seg.segment_id: seg for seg in context}
    token, failures = reserve(meeting_id, manual=manual, cursor_id=cursor)
    if token is None:
        return {"meeting_id": meeting_id, "skipped": failures}
    completed = False
    try:
        if failures >= 3:
            raw_people, degraded = _rule_extract(context), True
        else:
            raw_people, degraded = await _call_llm(meeting_id, body)
            if degraded:
                raw_people = _rule_extract(context)
        accepted, declined = validate_people(raw_people, by_id)
        with session_scope() as db:
            state = db.get(PersonExtractionState, meeting_id)
            meeting = db.get(Meeting, meeting_id)
            if state is None or state.lease_token != token or meeting is None or meeting.deleted_at is not None:
                return {"meeting_id": meeting_id, "skipped": "stale_request"}
            extracted = []
            for item in accepted:
                person, created, ambiguous = persons.upsert_person(db, owner_id=owner_id,
                    name=item["name"], aliases=item["aliases"], attributes=item["attributes"],
                    meeting_id=meeting_id, confidence=item["confidence"],
                    source="rule" if degraded else "llm",
                    evidence=[ev["segment_id"] for ev in item["evidence"]], role=item["meeting_role"])
                extracted.append({"person_id": person.id, "name": person.display_name,
                    "created": created, "ambiguous": ambiguous, "confidence": item["confidence"]})
            persons.sync_participants_string(db, meeting_id=meeting_id)
            persons.audit(db, action="person_extract", target=f"meeting:{meeting_id}", payload={
                "manual": manual, "degraded": degraded, "accepted": len(accepted),
                "declined": declined, "intro_hits": len(hits), "cursor_id": cursor})
            state.cursor_id = max(state.cursor_id, cursor)
            state.failures = state.failures + 1 if degraded else 0
            state.lease_token = None
            state.lease_until = None
            db.add(state)
            db.flush()
            candidates = persons.list_participants(db, meeting_id)
            result = {"meeting_id": meeting_id, "degraded": degraded, "extracted": extracted,
                "declined": declined, "created": sum(item["created"] for item in extracted),
                "updated": sum(not item["created"] for item in extracted), "candidates": candidates}
        completed = True
        return result
    finally:
        if not completed:
            with session_scope() as db:
                state = db.get(PersonExtractionState, meeting_id)
                if state and state.lease_token == token:
                    state.lease_token = None
                    state.lease_until = None
                    state.failures += 1
                    db.add(state)



def should_extract_on_final(meeting_id: int, text: str) -> bool:
    """会中每来一条定稿片段时的轻量判断：是否值得触发一次增量抽取。"""
    if not bool(get_config("person.extract_enabled")):
        return False
    if not text or not any(keyword in text for keyword in INTRO_KEYWORDS):
        return False
    # interval=0 disables only periodic scans; introductions still trigger.
    return True


def summarize_attributes(attributes: Iterable[dict]) -> str:
    """把属性拼成横幅上的一行摘要（单位·职务）。"""
    unit = ""
    title = ""
    for item in attributes or []:
        if item.get("category") == "org" and item.get("key") == "unit":
            unit = str(item.get("value") or "")
        if item.get("category") == "role" and item.get("key") == "title":
            title = str(item.get("value") or "")
    parts = [part for part in (unit, title) if part]
    return " · ".join(parts)


def dump_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
