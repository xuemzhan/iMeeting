"""会后停用「证据不足」的自动声纹（审查第 5 节第 4 条）。

实测库里 person 5/6 只由一句 1.8 秒的话生成，却成了可以被匹配到的身份。
规则：只动本场自动生成的「待命名」声纹；片段数与累计语音**同时**不达标才停用；
停用可恢复（不删除），人工命名过的身份一律不动。
"""

from __future__ import annotations

import numpy as np
from sqlmodel import Session, select

from imeeting.db import engine
from imeeting.engine import voice_template
from imeeting.models import Meeting, Segment, User, Voiceprint


def _unit(index: int = 0) -> np.ndarray:
    vector = np.zeros(192, dtype=np.float32)
    vector[index] = 1.0
    return vector


def _seed():
    """一场会议 + 四条片段 + 四个身份，分别命中不同判定分支。"""
    with Session(engine) as db:
        admin = db.exec(select(User).where(User.username == "admin")).one()
        meeting = Meeting(title="噪声身份清理", owner_id=admin.id, audio_path="", duration_ms=60000)
        other = Meeting(title="别的会议", owner_id=admin.id, audio_path="", duration_ms=60000)
        db.add(meeting)
        db.add(other)
        db.commit()
        db.refresh(meeting)
        db.refresh(other)

        def voiceprint(name: str, key: str | None, *, provisional: bool, meeting_id: int | None):
            row = Voiceprint(
                owner_id=admin.id,
                name=name,
                embedding=_unit(0).tobytes(),
                sample_ms=0,
                sample_count=1,
                provisional=provisional,
                meeting_id=meeting_id,
                speaker_key=key,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id

        def segment(meeting_id: int, key: str, label: str, start: int, end: int):
            db.add(
                Segment(
                    meeting_id=meeting_id,
                    segment_id=f"seg-{key}-{start}",
                    speaker=label,
                    speaker_key=key,
                    text="嗯。",
                    start_ms=start,
                    end_ms=end,
                    is_final=True,
                )
            )

        noisy = voiceprint("说话人 1", "spk_noise", provisional=True, meeting_id=meeting.id)
        segment(meeting.id, "spk_noise", "说话人 1", 0, 1800)

        many = voiceprint("说话人 2", "spk_many", provisional=True, meeting_id=meeting.id)
        for index in range(3):
            segment(meeting.id, "spk_many", "说话人 2", index * 1000, index * 1000 + 900)

        long = voiceprint("说话人 3", "spk_long", provisional=True, meeting_id=meeting.id)
        segment(meeting.id, "spk_long", "说话人 3", 0, 5000)

        named = voiceprint("郭睿", "spk_named", provisional=False, meeting_id=meeting.id)
        segment(meeting.id, "spk_named", "郭睿", 0, 1200)

        foreign = voiceprint("说话人 9", "spk_foreign", provisional=True, meeting_id=other.id)
        segment(other.id, "spk_foreign", "说话人 9", 0, 1500)

        db.commit()
        return (
            meeting.id,
            {"noisy": noisy, "many": many, "long": long, "named": named, "foreign": foreign},
        )


def _disabled(ids: dict[str, int]) -> dict[str, bool]:
    with Session(engine) as db:
        return {name: bool(db.get(Voiceprint, value).disabled) for name, value in ids.items()}


def test_only_short_and_thin_idle_identities_are_disabled():
    meeting_id, ids = _seed()

    with Session(engine) as db:
        disabled = voice_template.disable_noise_identities(db, meeting_id)
        db.commit()

    assert disabled == ["说话人 1"]
    state = _disabled(ids)
    assert state["noisy"] is True, "1 段 1.8 秒应当被停用"
    assert state["many"] is False, "3 段即使每段不足 3 秒也不停用"
    assert state["long"] is False, "单段 5 秒已满足时长门槛"
    assert state["named"] is False, "人工命名过的身份一律不动"
    assert state["foreign"] is False, "别的会议的声纹不属于本场清理范围"


def test_cleanup_is_idempotent_and_keeps_the_row():
    meeting_id, ids = _seed()

    with Session(engine) as db:
        assert voice_template.disable_noise_identities(db, meeting_id) == ["说话人 1"]
        db.commit()
    with Session(engine) as db:
        assert voice_template.disable_noise_identities(db, meeting_id) == []
        # 停用不是删除：行还在，可以人工恢复
        assert db.get(Voiceprint, ids["noisy"]) is not None
