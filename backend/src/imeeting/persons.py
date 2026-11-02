"""人员本体（REQ-001）：归一化、对齐、落库、确认与绑定。

设计要点
--------
- **身份主键是** ``person``：姓名/单位/职务挂在它上面，``voiceprint`` 只回答
  「这个声音像谁」。
- **幂等**：属性以 ``(person_id, category, key)`` 为锚点 upsert；同一场会议重复
  抽取不增长行数。
- **同名不同人**：展示名唯一，但单位/职务冲突时用「姓名（单位）」另立一条并双方
  标 ``ambiguous``，等人工裁决，绝不静默合并。
- **人工优先**：``state=confirmed`` 的记录不会被自动抽取覆盖。
- **证据**：所有自动写入都带 ``evidence``（segment_id 列表）与 ``source``，
  失败/无证据的抽取在 ``person_extract`` 里就被丢掉，不会走到这里。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlmodel import Session, select

from .models import (
    Meeting,
    MeetingParticipant,
    Person,
    PersonAlias,
    PersonAttribute,
    Segment,
    Voiceprint,
    utcnow,
)

logger = logging.getLogger(__name__)

#: 属性白名单：category -> 允许的 key
ATTRIBUTE_KEYS: dict[str, tuple[str, ...]] = {
    "identity": ("name", "alias", "pinyin"),
    "org": ("unit", "department", "team"),
    "role": ("title", "meeting_role"),
    "domain": ("expertise", "project"),
    "contact": ("phone", "email", "im"),
}

#: 视为称谓的后缀，归一化时剥掉（只用于匹配，不改展示名）
_TITLE_SUFFIXES = (
    "主任",
    "经理",
    "总监",
    "总工",
    "老师",
    "先生",
    "女士",
    "同志",
    "博士",
    "教授",
    "所长",
    "院长",
    "部长",
    "处长",
    "科长",
    "工",
    "总",
)

#: 中文姓名长度范围（含少数民族姓名放宽到 12）
_NAME_MIN = 2
_NAME_MAX = 32

STATE_PENDING = "pending"
STATE_CONFIRMED = "confirmed"
STATE_REJECTED = "rejected"
STATE_VALUES = (STATE_PENDING, STATE_CONFIRMED, STATE_REJECTED)

PERSON_ACTIVE = "active"
PERSON_MERGED = "merged"
PERSON_DELETED = "deleted"

_MAX_ALIAS_SOURCE = 16


def _now() -> datetime:
    return utcnow()


def _norm_width(text: str) -> str:
    """全角转半角 + 去首尾空白。"""
    out = []
    for ch in text or "":
        code = ord(ch)
        if code == 0x3000:
            code = 32
        elif 0xFF01 <= code <= 0xFF5E:
            code -= 0xFEE0
        out.append(chr(code))
    return "".join(out).strip()


def normalize_name(raw: str) -> str:
    """姓名归一：去空白、全角转半角、去称谓后缀（至少保留 2 个字）。"""
    text = _norm_width(raw)
    placeholder = re.fullmatch(r"\s*说话人\s*(\d+)\s*", text)
    if placeholder:
        return f"说话人 {placeholder.group(1)}"
    text = re.sub(r"\s+", "", text)
    for suffix in _TITLE_SUFFIXES:
        if text.endswith(suffix) and len(text) - len(suffix) >= _NAME_MIN:
            text = text[: -len(suffix)]
            break
    return text[:_NAME_MAX]


def pinyin_key(raw: str) -> str:
    """检索键。

    没有引入 ``pypinyin`` 依赖：装了就用真拼音，没装则退化为归一化后的字符
    键（仍可用于大小写/全角不一致的匹配）。字段名沿用 ``pinyin`` 以便后续
    平滑替换。
    """
    name = normalize_name(raw)
    if not name:
        return ""
    try:  # pragma: no cover - 环境未安装 pypinyin 时走退化分支
        from pypinyin import lazy_pinyin  # type: ignore

        return "".join(lazy_pinyin(name)).lower()
    except Exception:
        return name.lower()


def clean_attributes(items: Iterable[dict]) -> list[dict]:
    """按白名单过滤属性并裁长度；非法项丢弃，不影响整条人员。"""
    out: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip().lower()
        key = str(item.get("key") or "").strip().lower()
        value = _norm_width(str(item.get("value") or ""))[:64]
        if not value or category not in ATTRIBUTE_KEYS:
            continue
        if key not in ATTRIBUTE_KEYS[category]:
            continue
        out.append({"category": category, "key": key, "value": value})
    return out


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def attributes_of(db: Session, person_id: int) -> list[PersonAttribute]:
    return list(
        db.exec(
            select(PersonAttribute)
            .where(PersonAttribute.person_id == person_id)
            .order_by(PersonAttribute.category, PersonAttribute.key)
        ).all()
    )


def aliases_of(db: Session, person_id: int) -> list[PersonAlias]:
    return list(
        db.exec(
            select(PersonAlias)
            .where(PersonAlias.person_id == person_id)
            .order_by(PersonAlias.created_at)
        ).all()
    )


def get_person(db: Session, person_id: int) -> Person | None:
    return db.get(Person, person_id)


def _attribute_value(db: Session, person_id: int, category: str, key: str) -> str:
    row = db.exec(
        select(PersonAttribute).where(
            PersonAttribute.person_id == person_id,
            PersonAttribute.category == category,
            PersonAttribute.key == key,
        )
    ).first()
    return (row.value or "") if row is not None else ""


# ---------------------------------------------------------------------------
# 对齐
# ---------------------------------------------------------------------------


def find_person(
    db: Session,
    owner_id: int,
    name: str,
    *,
    unit: str = "",
) -> Person | None:
    """按「展示名 → 别名 → 拼音+同单位」顺序定位已有人员。

    每步命中即停止，避免过度合并（FR-3.2）。
    """
    target = normalize_name(name)
    if not target:
        return None

    exact = db.exec(
        select(Person).where(
            Person.owner_id == owner_id,
            Person.display_name == target,
            Person.status == PERSON_ACTIVE,
        )
    ).first()
    if exact is not None:
        # 同名但单位明确冲突：不合并，交给调用方另立一条
        existing_unit = _attribute_value(db, exact.id, "org", "unit")
        if unit and existing_unit and unit != existing_unit:
            pass
        else:
            return exact

    alias_rows = db.exec(
        select(PersonAlias).where(PersonAlias.alias == target)
    ).all()
    for alias in alias_rows:
        owner = db.get(Person, alias.person_id)
        if owner is not None and owner.owner_id == owner_id and owner.status == PERSON_ACTIVE:
            existing_unit = _attribute_value(db, owner.id, "org", "unit")
            if not unit or not existing_unit or existing_unit == unit:
                return owner

    key = pinyin_key(name)
    if key:
        candidates = db.exec(
            select(Person).where(
                Person.owner_id == owner_id,
                Person.pinyin == key,
                Person.status == PERSON_ACTIVE,
            )
        ).all()
        for candidate in candidates:
            if not unit:
                return candidate
            if _attribute_value(db, candidate.id, "org", "unit") == unit:
                return candidate
    return None


def _unique_display_name(db: Session, owner_id: int, name: str, unit: str) -> str:
    """展示名唯一；同名不同人时补单位区分。"""
    base = normalize_name(name) or name[:64]
    taken = db.exec(
        select(Person).where(
            Person.owner_id == owner_id,
            Person.display_name == base,
        )
    ).first()
    if taken is None:
        return base
    if unit:
        candidate = f"{base}（{unit}）"[:64]
        clash = db.exec(
            select(Person).where(
                Person.owner_id == owner_id,
                Person.display_name == candidate,
            )
        ).first()
        if clash is None:
            return candidate
    index = 2
    while True:
        candidate = f"{base}#{index}"[:64]
        clash = db.exec(
            select(Person).where(
                Person.owner_id == owner_id,
                Person.display_name == candidate,
            )
        ).first()
        if clash is None:
            return candidate
        index += 1


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------


def _upsert_attribute(
    db: Session,
    person_id: int,
    *,
    meeting_id: int | None,
    category: str,
    key: str,
    value: str,
    confidence: float,
    source: str,
    evidence: list[str],
) -> PersonAttribute:
    row = db.exec(
        select(PersonAttribute).where(
            PersonAttribute.person_id == person_id,
            PersonAttribute.category == category,
            PersonAttribute.key == key,
        )
    ).first()
    payload = json.dumps(sorted({item for item in evidence if item}), ensure_ascii=False)
    if row is None:
        row = PersonAttribute(
            person_id=person_id,
            meeting_id=meeting_id,
            category=category,
            key=key,
            value=value,
            confidence=confidence,
            source=source,
            evidence=payload,
        )
    else:
        # 人工写入优先：手动值不被自动抽取覆盖
        if row.source == "manual" and source != "manual":
            return row
        if source == "manual" or confidence >= row.confidence or row.value != value:
            row.value = value
        row.confidence = max(row.confidence, confidence) if source != "manual" else 1.0
        row.source = "manual" if source == "manual" else row.source
        if meeting_id is not None:
            row.meeting_id = meeting_id
        row.evidence = payload if payload != "[]" else row.evidence
        row.updated_at = _now()
    db.add(row)
    return row


def upsert_person(
    db: Session,
    *,
    owner_id: int,
    name: str,
    aliases: Iterable[str] = (),
    attributes: Iterable[dict] = (),
    meeting_id: int | None = None,
    confidence: float = 0.5,
    source: str = "llm",
    evidence: Iterable[str] = (),
    role: str = "",
    importance: int = 3,
    state: str = STATE_PENDING,
) -> tuple[Person, bool, bool]:
    """写入（或复用）一位人员，并更新其在本次会议中的参与关系。

    Returns:
        (person, created, ambiguous)
    """
    evidence_list = [item for item in evidence if item]
    attrs = clean_attributes(attributes)
    unit = next(
        (item["value"] for item in attrs if item["category"] == "org" and item["key"] == "unit"),
        "",
    )
    display = normalize_name(name) or _norm_width(name)[:64]
    person = find_person(db, owner_id, name, unit=unit)
    created = False
    ambiguous = False

    if person is None:
        # 同名已存在但单位冲突：双方都标 ambiguous，等人工用单位/职别区分
        same_name = db.exec(
            select(Person).where(
                Person.owner_id == owner_id,
                Person.display_name == display,
                Person.status == PERSON_ACTIVE,
            )
        ).all()
        if same_name:
            ambiguous = True
            for row in same_name:
                row.ambiguous = True
                row.updated_at = _now()
                db.add(row)
        person = Person(
            owner_id=owner_id,
            display_name=_unique_display_name(db, owner_id, display, unit),
            pinyin=pinyin_key(display),
            ambiguous=ambiguous,
        )
        db.add(person)
        db.flush()
        created = True
    else:
        if unit and not _attribute_value(db, person.id, "org", "unit"):
            person.ambiguous = False
        person.updated_at = _now()
        db.add(person)

    for alias in aliases:
        cleaned = _norm_width(str(alias))[:64]
        if not cleaned or cleaned == person.display_name:
            continue
        exists = db.exec(
            select(PersonAlias).where(
                PersonAlias.person_id == person.id,
                PersonAlias.alias == cleaned,
            )
        ).first()
        if exists is None:
            db.add(
                PersonAlias(
                    person_id=person.id,
                    alias=cleaned[:64],
                    source=source[:_MAX_ALIAS_SOURCE] or "oral",
                    confidence=confidence,
                )
            )

    for item in attrs:
        _upsert_attribute(
            db,
            person.id,
            meeting_id=meeting_id,
            category=item["category"],
            key=item["key"],
            value=item["value"],
            confidence=confidence,
            source=source,
            evidence=evidence_list,
        )

    if meeting_id is not None:
        _upsert_participant(
            db,
            meeting_id=meeting_id,
            person=person,
            confidence=confidence,
            evidence=evidence_list,
            role=role,
            importance=importance,
            state=state,
        )
    db.flush()
    return person, created, ambiguous


def _upsert_participant(
    db: Session,
    *,
    meeting_id: int,
    person: Person,
    confidence: float,
    evidence: list[str],
    role: str = "",
    importance: int = 3,
    state: str = STATE_PENDING,
) -> MeetingParticipant:
    row = db.exec(
        select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.person_id == person.id,
        )
    ).first()
    payload = json.dumps(sorted(set(evidence)), ensure_ascii=False)
    if row is None:
        row = MeetingParticipant(
            meeting_id=meeting_id,
            person_id=person.id,
            role=role or "",
            importance=importance,
            state=state,
            confidence=confidence,
            evidence=payload,
        )
    else:
        # 人工确认/删除过的记录不被自动抽取改回 pending
        if row.state == STATE_PENDING and state != STATE_PENDING:
            row.state = state
        if role and not row.role:
            row.role = role
        if importance != 3 or row.importance == 3:
            row.importance = importance
        row.confidence = max(row.confidence, confidence)
        if evidence:
            row.evidence = payload
        row.updated_at = _now()
    db.add(row)
    return row


def list_participants(db: Session, meeting_id: int) -> list[dict]:
    """会议参会人清单（含属性与绑定），按重要程度、状态、姓名排序。"""
    rows = db.exec(
        select(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting_id)
    ).all()
    out: list[dict] = []
    for row in rows:
        person = db.get(Person, row.person_id)
        if person is None or person.status != PERSON_ACTIVE:
            continue
        attrs = attributes_of(db, person.id)
        from .settings import get_config
        low_semantic_candidate = row.state == STATE_PENDING and bool(json.loads(row.evidence or "[]")) and row.confidence < float(get_config("person.confidence_floor"))
        out.append(
            {
                "participant_id": row.id,
                "person_id": person.id,
                "speaker": person.display_name,
                "speaker_key": row.speaker_key,
                "role": row.role,
                "importance": row.importance,
                "state": row.state,
                "confidence": round(row.confidence, 4),
                "hidden_by_confidence": low_semantic_candidate,
                "ambiguous": bool(person.ambiguous),
                "evidence": json.loads(row.evidence or "[]"),
                "attributes": [
                    {
                        "category": item.category,
                        "key": item.key,
                        "value": item.value,
                        "confidence": round(item.confidence, 4),
                        "source": item.source,
                    }
                    for item in attrs
                    if item.category != "identity"
                ],
            }
        )
    state_rank = {STATE_CONFIRMED: 0, STATE_PENDING: 1, STATE_REJECTED: 2}
    out.sort(
        key=lambda item: (
            state_rank.get(item["state"], 1),
            item["importance"],
            item["speaker"],
        )
    )
    return out


def set_participant(
    db: Session,
    *,
    meeting_id: int,
    person_id: int,
    state: str | None = None,
    role: str | None = None,
    importance: int | None = None,
    display_name: str | None = None,
    attributes: Iterable[dict] = (),
) -> dict:
    """人工修改参会人；姓名变更走别名逻辑。"""
    person = db.get(Person, person_id)
    if person is None:
        raise LookupError(f"人员不存在：{person_id}")
    row = db.exec(
        select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.person_id == person_id,
        )
    ).first()
    if row is None:
        raise LookupError(f"会议 {meeting_id} 中没有该参会人：{person_id}")

    if display_name:
        new_name = normalize_name(display_name)
        if new_name and new_name != person.display_name:
            conflict = db.exec(
                select(Person).where(
                    Person.owner_id == person.owner_id,
                    Person.display_name == new_name,
                    Person.id != person.id,
                )
            ).first()
            if conflict is not None:
                raise ValueError(f"已存在同名人员：{new_name}")
            old_name = person.display_name
            person.display_name = new_name[:64]
            person.pinyin = pinyin_key(new_name)
            person.updated_at = _now()
            exists = db.exec(
                select(PersonAlias).where(
                    PersonAlias.person_id == person.id,
                    PersonAlias.alias == old_name,
                )
            ).first()
            if exists is None:
                db.add(
                    PersonAlias(
                        person_id=person.id,
                        alias=old_name,
                        source="manual",
                        confidence=1.0,
                    )
                )
            db.add(person)
            db.flush()
            _propagate_name(db, person.id, old_name, person.display_name)

    if state is not None:
        if state not in STATE_VALUES:
            raise ValueError(f"非法状态：{state}")
        row.state = state
    if role is not None:
        row.role = role[:16]
    if importance is not None:
        row.importance = importance if importance in (1, 2, 3) else 3
    row.updated_at = _now()
    db.add(row)

    for item in attributes:
        if isinstance(item, dict) and item.get("value") == "" and item.get("key") in ATTRIBUTE_KEYS.get(item.get("category"), ()):
            existing = db.exec(select(PersonAttribute).where(PersonAttribute.person_id == person.id, PersonAttribute.category == item["category"], PersonAttribute.key == item["key"])).first()
            if existing is not None:
                db.delete(existing)
    for item in clean_attributes(attributes):
        _upsert_attribute(
            db,
            person.id,
            meeting_id=meeting_id,
            category=item["category"],
            key=item["key"],
            value=item["value"],
            confidence=1.0,
            source="manual",
            evidence=[],
        )
    db.flush()
    for item in list_participants(db, meeting_id):
        if item["person_id"] == person.id:
            return item
    return {}


def link_voiceprints_by_speaker(
    db: Session, *, person_id: int, meeting_id: int, speaker_key: str | None
) -> int:
    """把本场该 ``speaker_key`` 的声纹永久关联到人员（幂等，可改绑）。

    声纹是生物特征，只有 owner 一致（或旧数据 owner 为空）时才建立关联；
    跨账号一律拒绝并记日志。改绑到新的正确人员是允许的——这正是「同一段声音
    换成另一个身份」的处理方式。返回实际改动的条数。
    """
    if not speaker_key:
        return 0
    person = db.get(Person, person_id)
    if person is None or person.status != PERSON_ACTIVE:
        return 0
    changed = 0
    rows = db.exec(
        select(Voiceprint).where(
            Voiceprint.meeting_id == meeting_id,
            Voiceprint.speaker_key == speaker_key,
        )
    ).all()
    for voice in rows:
        if voice.owner_id is not None and voice.owner_id != person.owner_id:
            logger.warning(
                "拒绝跨账号关联声纹 person=%s voiceprint=%s", person_id, voice.id
            )
            continue
        if voice.person_id == person.id:
            continue
        voice.person_id = person.id
        voice.updated_at = _now()
        db.add(voice)
        changed += 1
    if changed:
        db.flush()
    return changed


def voiceprint_person(db: Session, voiceprint) -> Person | None:
    """声纹永久关联的人员；未关联、已合并或已删除时返回 ``None``。"""
    person_id = getattr(voiceprint, "person_id", None)
    if person_id is None:
        return None
    person = db.get(Person, person_id)
    return person if person is not None and person.status == PERSON_ACTIVE else None


def bind_speaker(
    db: Session, *, meeting_id: int, person_id: int, speaker_key: str | None
) -> dict:
    """把会议内的 speaker_key 绑到某位人员，并回溯本场转写标签。

    ``speaker_key=None`` 表示解绑。返回 {"previous": 旧绑定人姓名或 None}。
    """
    person = db.get(Person, person_id)
    if person is None:
        raise LookupError(f"人员不存在：{person_id}")
    row = db.exec(
        select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.person_id == person_id,
        )
    ).first()
    if row is None:
        raise LookupError(f"会议 {meeting_id} 中没有该参会人：{person_id}")

    previous_name: str | None = None
    old_key = row.speaker_key
    if old_key and old_key != speaker_key:
        for seg in db.exec(select(Segment).where(Segment.meeting_id == meeting_id, Segment.speaker_key == old_key)).all():
            # 只做标注：把原标注留在 speaker_previous，改绑/解绑不再抹掉文本证据。
            seg.speaker_previous = (seg.speaker or "")[:32]
            seg.speaker = "未确认说话人"
            seg.speaker_source = "unknown"
            seg.speaker_score = 0.0
            db.add(seg)
        db.info.setdefault("identity_events", []).append((meeting_id, person.display_name, "未确认说话人"))
    if speaker_key:
        other = db.exec(
            select(MeetingParticipant).where(
                MeetingParticipant.meeting_id == meeting_id,
                MeetingParticipant.speaker_key == speaker_key,
                MeetingParticipant.person_id != person_id,
            )
        ).first()
        if other is not None:
            previous_name = (db.get(Person, other.person_id) or person).display_name
            other.speaker_key = None
            other.updated_at = _now()
            db.add(other)
            db.flush()  # Free the unique meeting/key before assigning it.
        segments = db.exec(
            select(Segment).where(
                Segment.meeting_id == meeting_id,
                Segment.speaker_key == speaker_key,
            )
        ).all()
        old_labels = {seg.speaker for seg in segments if seg.speaker and seg.speaker != person.display_name}
        for seg in segments:
            seg.speaker = person.display_name[:32]
            seg.speaker_source = "manual"
            seg.speaker_score = 1.0
            db.add(seg)
        for old_name in old_labels:
            db.info.setdefault("identity_events", []).append((meeting_id, old_name, person.display_name))

    row.speaker_key = speaker_key
    row.state = STATE_CONFIRMED if speaker_key else row.state
    row.updated_at = _now()
    db.add(row)
    # 绑定即建立声纹→人员的永久关联（owner 不一致时内部会拒绝）。
    link_voiceprints_by_speaker(
        db, person_id=person.id, meeting_id=meeting_id, speaker_key=speaker_key
    )
    db.flush()
    return {"person_id": person.id, "speaker_key": speaker_key, "previous": previous_name}


def _propagate_name(db: Session, person_id: int, old_name: str, new_name: str) -> None:
    for part in db.exec(select(MeetingParticipant).where(MeetingParticipant.person_id == person_id)).all():
        if part.speaker_key:
            for seg in db.exec(select(Segment).where(Segment.meeting_id == part.meeting_id, Segment.speaker_key == part.speaker_key)).all():
                seg.speaker = new_name[:32]
                seg.speaker_source = "manual"
                seg.speaker_score = 1.0
                db.add(seg)
            db.info.setdefault("identity_events", []).append((part.meeting_id, old_name, new_name))
        sync_participants_string(db, meeting_id=part.meeting_id)


def publish_identity_changes(db: Session) -> None:
    """Publish only after callers have committed the identity transaction."""
    from .engine import speaker, streaming, voice_match
    speaker.invalidate_cache()
    voice_match.invalidate_cache()
    for meeting_id, old_name, new_name in db.info.pop("identity_events", []):
        streaming.rename_active_speaker(meeting_id, old_name, new_name)


def sync_transcript_identity(db: Session, *, meeting_id: int, owner_id: int, from_label: str, to_name: str, segments) -> None:
    """Project a confirmed transcript correction into the meeting ontology.

    A correction never renames a different established person globally.
    Anonymous placeholders can retain their ID when first given a real name.
    """
    keys = {item.speaker_key for item in segments if item.speaker_key}
    sources = db.exec(select(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.speaker_key.in_(keys))).all() if keys else []
    target = db.exec(select(Person).where(Person.owner_id == owner_id, Person.display_name == to_name, Person.status == PERSON_ACTIVE)).first()
    if target is None and len(sources) == 1 and from_label.startswith("说话人"):
        target = db.get(Person, sources[0].person_id)
        set_participant(db, meeting_id=meeting_id, person_id=target.id, display_name=to_name)
    if target is None:
        target, _, _ = upsert_person(db, owner_id=owner_id, name=to_name, meeting_id=meeting_id, confidence=1.0, source="manual")
    else:
        _upsert_participant(db, person=target, meeting_id=meeting_id, state=STATE_CONFIRMED, confidence=1.0, evidence=[])
    for source in sources:
        if source.person_id != target.id:
            source.speaker_key = None
            source.state = STATE_REJECTED
            db.add(source)
    db.flush()
    target_part = participant_of(db, meeting_id, target.id)
    if keys and target_part is not None and not target_part.speaker_key:
        bind_speaker(db, meeting_id=meeting_id, person_id=target.id, speaker_key=sorted(keys)[0])
    set_participant(db, meeting_id=meeting_id, person_id=target.id, state=STATE_CONFIRMED)
    sync_participants_string(db, meeting_id=meeting_id)


def merge_persons(db: Session, *, target_id: int, source_id: int) -> Person:
    """把 source 合并进 target：别名/属性/参会关系全部迁移，source 标记 merged。"""
    if target_id == source_id:
        raise ValueError("不能把人员合并到自己")
    target = db.get(Person, target_id)
    source = db.get(Person, source_id)
    if target is None or source is None:
        raise LookupError("人员不存在")
    if source.owner_id != target.owner_id:
        raise ValueError("不能跨账号合并人员")

    for alias in aliases_of(db, source.id):
        exists = db.exec(
            select(PersonAlias).where(
                PersonAlias.person_id == target.id,
                PersonAlias.alias == alias.alias,
            )
        ).first()
        if exists is None:
            alias.person_id = target.id
            db.add(alias)
        else:
            db.delete(alias)

    for attr in attributes_of(db, source.id):
        exists = db.exec(
            select(PersonAttribute).where(
                PersonAttribute.person_id == target.id,
                PersonAttribute.category == attr.category,
                PersonAttribute.key == attr.key,
            )
        ).first()
        if exists is None:
            attr.person_id = target.id
            db.add(attr)
        elif attr.confidence > exists.confidence:
            exists.value = attr.value
            exists.confidence = attr.confidence
            exists.source = attr.source
            exists.evidence = attr.evidence
            db.add(exists)
            db.delete(attr)
        else:
            db.delete(attr)

    affected_meetings = set()
    for part in db.exec(
        select(MeetingParticipant).where(MeetingParticipant.person_id == source.id)
    ).all():
        affected_meetings.add(part.meeting_id)
        if part.speaker_key:
            for segment in db.exec(select(Segment).where(Segment.meeting_id == part.meeting_id, Segment.speaker_key == part.speaker_key)).all():
                segment.speaker = target.display_name[:32]
                segment.speaker_source = "manual"
                db.add(segment)
            db.info.setdefault("identity_events", []).append((part.meeting_id, source.display_name, target.display_name))
        clash = db.exec(
            select(MeetingParticipant).where(
                MeetingParticipant.meeting_id == part.meeting_id,
                MeetingParticipant.person_id == target.id,
            )
        ).first()
        if clash is None:
            part.person_id = target.id
            db.add(part)
        else:
            if part.speaker_key and not clash.speaker_key:
                transferred_key = part.speaker_key
                part.speaker_key = None
                db.add(part)
                db.flush()
                clash.speaker_key = transferred_key
            clash.confidence = max(clash.confidence, part.confidence)
            clash.updated_at = _now()
            db.add(clash)
            db.delete(part)

    if source.display_name != target.display_name and not db.exec(select(PersonAlias).where(PersonAlias.person_id == target.id, PersonAlias.alias == source.display_name)).first():
        db.add(PersonAlias(person_id=target.id, alias=source.display_name, source="manual", confidence=1.0))
    source.status = PERSON_MERGED
    source.merged_into_id = target.id
    source.updated_at = _now()
    db.add(source)
    # 声纹跟随身份合并：source 名下的声纹改挂到 target，不留悬空引用。
    for voice in db.exec(
        select(Voiceprint).where(Voiceprint.person_id == source.id)
    ).all():
        voice.person_id = target.id
        voice.updated_at = _now()
        db.add(voice)
    db.flush()
    for meeting_id in affected_meetings:
        sync_participants_string(db, meeting_id=meeting_id)
    return target


def delete_person(db: Session, *, person_id: int) -> None:
    """软删除：状态标记 + 清空属性与别名（保留会议内引用）。"""
    person = db.get(Person, person_id)
    if person is None:
        raise LookupError("人员不存在")
    for attr in attributes_of(db, person.id):
        db.delete(attr)
    for alias in aliases_of(db, person.id):
        db.delete(alias)
    meeting_ids = [part.meeting_id for part in db.exec(select(MeetingParticipant).where(MeetingParticipant.person_id == person.id)).all()]
    person.status = PERSON_DELETED
    person.updated_at = _now()
    db.add(person)
    # 人员删除不连带删除声纹：生物特征与历史模板保留，只解除永久关联，
    # 避免「删了档案就把采集到的声音数据一起毁掉」。
    for voice in db.exec(
        select(Voiceprint).where(Voiceprint.person_id == person.id)
    ).all():
        voice.person_id = None
        voice.updated_at = _now()
        db.add(voice)
    db.flush()
    for meeting_id in meeting_ids:
        sync_participants_string(db, meeting_id=meeting_id)


def sync_participants_string(db: Session, *, meeting_id: int) -> str:
    """把本体回写成 ``Meeting.participants``（兼容老前端与导出）。"""
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        return ""
    items = [
        item
        for item in list_participants(db, meeting_id)
        if item["state"] != STATE_REJECTED and not item.get("hidden_by_confidence", False)
    ]
    text = ", ".join(item["speaker"] for item in items)[:1000]
    meeting.participants = text
    db.add(meeting)
    return text


def list_persons(db: Session, owner_id: int, *, query: str = "") -> list[dict]:
    """人员检索（P1 接口，但实现成本低，一并提供）。"""
    stmt = select(Person).where(
        Person.owner_id == owner_id, Person.status == PERSON_ACTIVE
    )
    rows = db.exec(stmt.order_by(Person.display_name)).all()
    keyword = _norm_width(query).lower()
    out: list[dict] = []
    for person in rows:
        attrs = attributes_of(db, person.id)
        haystack = " ".join(
            [person.display_name, person.pinyin]
            + [alias.alias for alias in aliases_of(db, person.id)]
            + [item.value for item in attrs]
        ).lower()
        if keyword and keyword not in haystack:
            continue
        out.append(
            {
                "person_id": person.id,
                "display_name": person.display_name,
                "pinyin": person.pinyin,
                "ambiguous": bool(person.ambiguous),
                "aliases": [alias.alias for alias in aliases_of(db, person.id)],
                "attributes": [
                    {
                        "category": item.category,
                        "key": item.key,
                        "value": item.value,
                        "source": item.source,
                    }
                    for item in attrs
                ],
            }
        )
    return out


def audit(
    db: Session,
    *,
    action: str,
    target: str,
    payload: dict[str, Any] | None = None,
    user_id: int | None = None,
    username: str = "",
) -> None:
    """写审计（与现有 AuditLog 表同构；哈希链由后台任务补齐）。"""
    from .models import AuditLog

    db.add(
        AuditLog(
            user_id=user_id,
            username=username[:64],
            action=action[:64],
            target=target[:200],
            payload=json.dumps(payload or {}, ensure_ascii=False, default=str),
        )
    )


def participant_of(
    db: Session, meeting_id: int, person_id: int
) -> Optional[MeetingParticipant]:
    return db.exec(
        select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.person_id == person_id,
        )
    ).first()


# ---------------------------------------------------------------------------
# 会中自动 / 手工加入参会人
# ---------------------------------------------------------------------------

#: 自动加入的置信度：只说明「系统听出了这个声音」，不代表身份已确认
AUTO_CONFIDENCE = 0.3

#: 视为占位标签的前缀：这类名字只在本场会议内复用，跨会议必须区分
_PLACEHOLDER_PREFIXES = ("说话人", "参会人", "未知", "陌生", "嘉宾")


def _participant_item(db: Session, meeting_id: int, person_id: int) -> dict | None:
    for item in list_participants(db, meeting_id):
        if item["person_id"] == person_id:
            return item
    return None


def _person_for_label(
    db: Session, *, owner_id: int, meeting_id: int, label: str
) -> Person:
    """给「说话人 N」这类标签找/建人员行。

    两种标签区别对待：

    - **占位标签**（``说话人 1`` / ``参会人``）：只在**本场会议**内复用。不同
      会议的「说话人 1」必须是不同的人，否则两场会的两个人会被并成同一个
      person；名字被别的会议占用时加会议后缀（``说话人 1（#8）``）。
    - **真实姓名**（已登记声纹认出来的，如「丁效」）：跨会议复用同一个人，
      这正是人员本体想要的效果。
    """
    placeholder = label.startswith(_PLACEHOLDER_PREFIXES)
    existing = db.exec(
        select(Person).where(
            Person.owner_id == owner_id,
            Person.display_name == label,
            Person.status == PERSON_ACTIVE,
        )
    ).all()
    for person in existing:
        if not placeholder:
            person.updated_at = _now()
            db.add(person)
            return person
        used_elsewhere = db.exec(
            select(MeetingParticipant).where(
                MeetingParticipant.person_id == person.id,
                MeetingParticipant.meeting_id != meeting_id,
            )
        ).first()
        if used_elsewhere is None:
            person.updated_at = _now()
            db.add(person)
            return person
    name = label if (not placeholder or not existing) else f"{label}（#{meeting_id}）"
    person = Person(owner_id=owner_id, display_name=name[:64], pinyin=pinyin_key(label))
    db.add(person)
    db.flush()
    return person


def ensure_auto_participant(
    db: Session, *, meeting_id: int, speaker_key: str, label: str = ""
) -> dict | None:
    """会中识别到新讲话人时自动加进参会人（待确认、按 speaker_key 幂等）。

    这只是「有人在说话」的记录，不代表身份已确认；人工确认或改名后才把转写
    标签换成真实姓名。
    """
    if not speaker_key:
        return None
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        return None
    row = db.exec(
        select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.speaker_key == speaker_key,
        )
    ).first()
    if row is not None:
        return _participant_item(db, meeting_id, row.person_id)
    display = _norm_width(label) or "参会人"
    if not display.startswith(_PLACEHOLDER_PREFIXES):
        # 真实姓名要归一（去称谓/空白）；占位标签原样保留，才能和转写里的
        # 「说话人 2」对上号
        display = normalize_name(display) or display
    person = _person_for_label(
        db,
        owner_id=meeting.owner_id,
        meeting_id=meeting_id,
        label=display,
    )
    # A second acoustic key can resolve to a person already in this meeting.
    # Reuse that relationship without replacing its confirmed key/state.
    if participant_of(db, meeting_id, person.id) is not None:
        return _participant_item(db, meeting_id, person.id)
    from sqlalchemy.exc import IntegrityError

    try:
        with db.begin_nested():
            db.add(MeetingParticipant(
                meeting_id=meeting_id,
                person_id=person.id,
                speaker_key=speaker_key,
                state=STATE_PENDING,
                confidence=AUTO_CONFIDENCE,
            ))
            db.flush()
    except IntegrityError:
        # Another writer may have inserted this relationship after our read.
        if participant_of(db, meeting_id, person.id) is None:
            raise
    return _participant_item(db, meeting_id, person.id)


def add_manual_participant(
    db: Session,
    *,
    meeting_id: int,
    name: str,
    speaker_key: str | None = None,
    role: str = "",
    attributes: Iterable[dict] = (),
) -> dict:
    """手工（或语音输入转文字后）加参会人：直接视为已确认。

    带了 ``speaker_key`` 就同时把本场那段声音绑到这个人身上，转写标签一并回溯。
    """
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise LookupError(f"会议不存在：{meeting_id}")
    person, _created, _ambiguous = upsert_person(
        db,
        owner_id=meeting.owner_id,
        name=name,
        attributes=attributes,
        meeting_id=meeting_id,
        confidence=1.0,
        source="manual",
        role=role,
        importance=2,
        state=STATE_CONFIRMED,
    )
    if speaker_key and participant_of(db, meeting_id, person.id) is not None:
        bind_speaker(
            db, meeting_id=meeting_id, person_id=person.id, speaker_key=speaker_key
        )
    db.flush()
    item = _participant_item(db, meeting_id, person.id)
    if item is None:
        raise ValueError("参会人写入失败")
    return item
