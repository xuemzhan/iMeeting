"""会议内说话人的重要程度（纪要加权）。

约定
----
- 重要程度：**1 = 关键参会人（最高）**、2 = 次要参会人、3 = 一般参会人（默认）。
- 键优先用 ``Segment.speaker_key``：改名只改 label、键不变，因此改名不会丢
  重要程度；历史数据没有 key 时退回 ``label:<显示名>``。
- 存储粒度是「会议 × 说话人」：同一个人在不同会议里可以承担不同角色。
- 纪要以关键参会人为主干、次要参会人作补充、其他人作背景（见
  ``importance_prompt_section``）。
"""

from __future__ import annotations

from sqlalchemy import or_
from sqlmodel import Session, select

from .models import Segment, SpeakerRole, utcnow

#: 关键参会人（最高）
IMPORTANCE_KEY = 1
#: 次要参会人
IMPORTANCE_SECONDARY = 2
#: 一般参会人（默认）
IMPORTANCE_NORMAL = 3
IMPORTANCE_VALUES = (IMPORTANCE_KEY, IMPORTANCE_SECONDARY, IMPORTANCE_NORMAL)

IMPORTANCE_LABELS = {
    IMPORTANCE_KEY: "关键参会人",
    IMPORTANCE_SECONDARY: "次要参会人",
    IMPORTANCE_NORMAL: "一般参会人",
}

#: 参会人处理状态：面板里逐行「确认 / 删除」，两者都从面板消失
STATE_VISIBLE = 0  # 未处理：正常显示在参会人面板
STATE_CONFIRMED = 1  # 已确认：确认为参会人后不再显示
STATE_DELETED = 2  # 已删除：判定不是参会人，不再显示
STATE_VALUES = (STATE_VISIBLE, STATE_CONFIRMED, STATE_DELETED)
STATE_LABELS = {
    STATE_VISIBLE: "待处理",
    STATE_CONFIRMED: "已确认",
    STATE_DELETED: "已删除",
}
IMPORTANCE_SHORT = {
    IMPORTANCE_KEY: "关键",
    IMPORTANCE_SECONDARY: "次要",
    IMPORTANCE_NORMAL: "一般",
}
IMPORTANCE_DESCRIPTIONS = {
    IMPORTANCE_KEY: "1（最高）",
    IMPORTANCE_SECONDARY: "2",
    IMPORTANCE_NORMAL: "3",
}

_LABEL_PREFIX = "label:"


def normalize_importance(value: object) -> int:
    """把任意输入收敛到 1/2/3；非法值一律按「一般」处理，绝不抛异常。"""
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return IMPORTANCE_NORMAL
    return number if number in IMPORTANCE_VALUES else IMPORTANCE_NORMAL


def normalize_state(value: object) -> int:
    """把任意输入收敛到 0/1/2；非法值按「未处理」处理，绝不抛异常。"""
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return STATE_VISIBLE
    return number if number in STATE_VALUES else STATE_VISIBLE


def speaker_key_for(speaker_key: str | None, speaker: str | None) -> str:
    """片段 → 重要程度表的键。"""
    if speaker_key:
        return speaker_key
    label = (speaker or "").strip()
    return f"{_LABEL_PREFIX}{label}" if label else ""


def resolve_speaker_key(
    db: Session, meeting_id: int, speaker: str, speaker_key: str | None = None
) -> str | None:
    """定位「要改哪一行」的稳定键。

    优先用调用方给的 ``speaker_key``：同一场会议里可能出现两个说话人共用同一个
    显示名（声纹把不同声音认成同一个人），只按姓名找会落到最近说话的那个人
    身上——用户点了 A 行的「删除」，消失的却是 B 行。给了键就必须精确命中，
    命中不了返回 ``None``（由调用方 404），不做静默降级。

    没给键时退回旧口径：先按显示名找最近一条片段（改名后仍能定位），再兼容
    「传进来的是 key」。
    """
    explicit = (speaker_key or "").strip()
    if explicit:
        if explicit.startswith(_LABEL_PREFIX):
            label = explicit[len(_LABEL_PREFIX) :].strip()
            if not label:
                return None
            row = db.exec(
                select(Segment.id).where(
                    Segment.meeting_id == meeting_id,
                    Segment.speaker == label,
                    or_(Segment.speaker_key.is_(None), Segment.speaker_key == ""),
                )
            ).first()
            return explicit if row is not None else None
        row = db.exec(
            select(Segment.id).where(
                Segment.meeting_id == meeting_id,
                Segment.speaker_key == explicit,
            )
        ).first()
        return explicit if row is not None else None

    name = (speaker or "").strip()
    if not name:
        return None
    row = db.exec(
        select(Segment)
        .where(Segment.meeting_id == meeting_id, Segment.speaker == name)
        .order_by(Segment.start_ms.desc(), Segment.id.desc())
    ).first()
    if row is None:
        row = db.exec(
            select(Segment)
            .where(
                Segment.meeting_id == meeting_id,
                Segment.speaker_key == name,
            )
            .order_by(Segment.start_ms.desc(), Segment.id.desc())
        ).first()
    if row is None:
        return None
    return speaker_key_for(row.speaker_key, row.speaker or name)


def load_roles(db: Session, meeting_id: int) -> dict[str, int]:
    """speaker_key → importance（缺省视为一般）。"""
    rows = db.exec(
        select(SpeakerRole).where(SpeakerRole.meeting_id == meeting_id)
    ).all()
    return {
        row.speaker_key: normalize_importance(row.importance) for row in rows
    }


def load_states(db: Session, meeting_id: int) -> dict[str, int]:
    """speaker_key → 处理状态（缺省视为未处理）。"""
    rows = db.exec(
        select(SpeakerRole).where(SpeakerRole.meeting_id == meeting_id)
    ).all()
    return {row.speaker_key: normalize_state(row.state) for row in rows}


def latest_labels(db: Session, meeting_id: int) -> dict[str, str]:
    """speaker_key → 当前显示名（以时间轴最后一条带该 key 的片段为准）。"""
    rows = db.exec(
        select(Segment.speaker_key, Segment.speaker)
        .where(Segment.meeting_id == meeting_id)
        .order_by(Segment.start_ms, Segment.id)
    ).all()
    labels: dict[str, str] = {}
    for key, label in rows:
        if key and label:
            labels[key] = label
    return labels


def list_speakers(db: Session, meeting_id: int) -> list[dict]:
    """会议内说话人清单：显示名、重要程度、处理状态、片段数与发言时长。

    排序：重要程度升序（1 关键在前）→ 发言时长降序 → 姓名。``state`` 供前端
    决定是否显示：0 未处理正常显示，1 已确认 / 2 已删除 都不再显示。
    """
    segments = db.exec(
        select(Segment)
        .where(Segment.meeting_id == meeting_id)
        .order_by(Segment.start_ms, Segment.id)
    ).all()
    roles = load_roles(db, meeting_id)
    states = load_states(db, meeting_id)
    items: dict[str, dict] = {}
    for seg in segments:
        if not seg.speaker and not seg.speaker_key:
            continue
        key = speaker_key_for(seg.speaker_key, seg.speaker)
        if not key:
            continue
        item = items.setdefault(
            key,
            {
                "speaker_key": key,
                "speaker": seg.speaker or "",
                "segments": 0,
                "speech_ms": 0,
            },
        )
        item["segments"] += 1
        item["speech_ms"] += max(0, (seg.end_ms or 0) - (seg.start_ms or 0))
        if seg.speaker:
            # 按时间顺序遍历，最后一条即最新显示名（改名后自动跟上）
            item["speaker"] = seg.speaker
    out = [
        {
            **item,
            "importance": roles.get(key, IMPORTANCE_NORMAL),
            "state": states.get(key, STATE_VISIBLE),
        }
        for key, item in items.items()
    ]
    out.sort(key=lambda row: (row["importance"], -row["speech_ms"], row["speaker"]))
    return out


def set_importance(
    db: Session,
    meeting_id: int,
    speaker: str,
    importance: int,
    speaker_key: str | None = None,
) -> list[dict]:
    """写入某说话人的重要程度（不存在则创建），返回刷新后的说话人清单。"""
    key = resolve_speaker_key(db, meeting_id, speaker, speaker_key)
    if key is None:
        raise LookupError(f"会议 {meeting_id} 中没有说话人：{speaker_key or speaker}")
    value = normalize_importance(importance)
    row = db.exec(
        select(SpeakerRole).where(
            SpeakerRole.meeting_id == meeting_id,
            SpeakerRole.speaker_key == key,
        )
    ).first()
    if row is None:
        db.add(
            SpeakerRole(
                meeting_id=meeting_id,
                speaker_key=key,
                label=(speaker or "").strip()[:64],
                importance=value,
            )
        )
    else:
        row.importance = value
        row.label = (speaker or "").strip()[:64] or row.label
        row.updated_at = utcnow()
        db.add(row)
    db.commit()
    return list_speakers(db, meeting_id)


def set_state(
    db: Session,
    meeting_id: int,
    speaker: str,
    state: int,
    speaker_key: str | None = None,
) -> list[dict]:
    """写入某说话人的处理状态（不存在则创建），返回刷新后的说话人清单。

    「确认」与「删除」都只是标记：不删片段、不改重要程度，仅让该说话人从
    参会人面板消失（前端按 ``state`` 过滤）。重复提交同一状态是幂等的。
    """
    key = resolve_speaker_key(db, meeting_id, speaker, speaker_key)
    if key is None:
        raise LookupError(f"会议 {meeting_id} 中没有说话人：{speaker_key or speaker}")
    value = normalize_state(state)
    row = db.exec(
        select(SpeakerRole).where(
            SpeakerRole.meeting_id == meeting_id,
            SpeakerRole.speaker_key == key,
        )
    ).first()
    if row is None:
        db.add(
            SpeakerRole(
                meeting_id=meeting_id,
                speaker_key=key,
                label=(speaker or "").strip()[:64],
                state=value,
            )
        )
    else:
        row.state = value
        row.label = (speaker or "").strip()[:64] or row.label
        row.updated_at = utcnow()
        db.add(row)
    db.commit()
    return list_speakers(db, meeting_id)


def migrate_label_key(
    db: Session, meeting_id: int, old_label: str, new_label: str
) -> int:
    """说话人改名时迁移「按显示名兜底」的重要程度记录。

    有 speaker_key 的片段不受影响（键不变）；这里只处理历史数据里以
    ``label:<旧名>`` 为键的记录。返回迁移条数。
    """
    old_key = f"{_LABEL_PREFIX}{(old_label or '').strip()}"
    new_key = f"{_LABEL_PREFIX}{(new_label or '').strip()}"
    if old_key == new_key:
        return 0
    moved = 0
    rows = db.exec(
        select(SpeakerRole).where(
            SpeakerRole.meeting_id == meeting_id,
            SpeakerRole.speaker_key == old_key,
        )
    ).all()
    for row in rows:
        existing = db.exec(
            select(SpeakerRole).where(
                SpeakerRole.meeting_id == meeting_id,
                SpeakerRole.speaker_key == new_key,
            )
        ).first()
        if existing is not None:
            # 目标已存在：保留更重要的那个，避免改名把关键参会人降级
            if row.importance < existing.importance:
                existing.importance = row.importance
                db.add(existing)
            db.delete(row)
        else:
            row.speaker_key = new_key
            row.label = (new_label or "").strip()[:64]
            row.updated_at = utcnow()
            db.add(row)
        moved += 1
    if moved:
        db.commit()
    return moved


def importance_prompt_section(lines: list) -> str:
    """给 LLM 的「参会人重要程度」约束块；没有显式角色时返回空串。

    ``lines`` 是 ``TranscriptLine`` 列表，需要 ``speaker`` 与
    ``speaker_importance`` 两个字段。默认（一般）不写进清单，避免把
    「没标记」误说成「一般参会人」——只有显式设置过 1/2 才生成约束。
    """
    groups: dict[int, list[str]] = {1: [], 2: []}
    seen: set[tuple[str, int]] = set()
    for line in lines:
        name = (getattr(line, "speaker", None) or "").strip()
        raw = getattr(line, "speaker_importance", None)
        if not name or raw is None:
            continue
        importance = normalize_importance(raw)
        if importance == IMPORTANCE_NORMAL:
            continue
        marker = (name, importance)
        if marker in seen:
            continue
        seen.add(marker)
        groups[importance].append(name)
    if not groups[1] and not groups[2]:
        return ""

    def names(level: int) -> str:
        return "、".join(groups[level]) if groups[level] else "（无）"

    return (
        "参会人重要程度（数字越小越重要）：\n"
        f"- 关键参会人（1，最高）：{names(1)}\n"
        f"- 次要参会人（2）：{names(2)}\n"
        "- 其余发言人均为一般参会人（3）。\n"
        "纪要要求：\n"
        "1. 整体摘要、议题与结论以**关键参会人**的发言为主干，只写他们确认、"
        "主导或拍板的内容；没有关键参会人时按一般规则处理。\n"
        "2. **次要参会人**的观点作为补充：可写进议题细节，但不得推翻或改写"
        "关键参会人的结论；有多个次要参会人时按发言分量合并，冲突处写明分歧。\n"
        "3. 一般参会人的内容只作背景与旁证；只有被关键/次要参会人采纳时，"
        "才进入摘要主干。\n"
        "4. 待办责任人优先取关键/次要参会人；无法判断发言人的内容按一般处理。\n"
        "5. 正文用姓名指代发言人即可，不要输出「关键/次要/一般参会人」这类"
        "标签本身，也不要为体现权重而编造原文没有的内容。\n\n"
    )
