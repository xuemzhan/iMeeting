"""模板合并（REQ-002 FR-5「合并两条模板」）。

口径：同一人的两条活跃模板合成 target 一条；source 的来源贡献改挂后重算质心，
source 退役但保留可追溯；任一方是主模板时 target 继承主模板。
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlmodel import Session, select

from imeeting.db import engine
from imeeting.engine import speaker, voice_match, voice_template
from imeeting.engine.voice_template import Scenario
from imeeting.models import (
    AuditLog,
    Voiceprint,
    VoiceprintTemplate,
    VoiceprintTemplateSource,
)

from .test_doc_code_gap_regressions import header, setup_pair, unit


def _voiceprint(owner: int, *, name: str = "模板合并") -> int:
    with Session(engine) as db:
        row = Voiceprint(
            owner_id=owner,
            name=name,
            embedding=unit(0).tobytes(),
            sample_ms=4000,
            sample_count=4,
            provisional=False,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _template(
    voiceprint_id: int,
    vector: np.ndarray,
    *,
    quality: float = 0.6,
    sample_ms: int = 2000,
    segment: str = "seg",
    meeting_id: int | None = None,
) -> int:
    with Session(engine) as db:
        row = voice_template.store_template(
            db,
            voiceprint_id=voiceprint_id,
            embedding=vector,
            scenario=Scenario(),
            quality=quality,
            sample_ms=sample_ms,
            source_meeting_id=meeting_id,
            source_segment_id=segment,
            confirmed=True,
        )
        db.commit()
        return row.id


def _row(template_id: int) -> VoiceprintTemplate:
    with Session(engine) as db:
        return db.get(VoiceprintTemplate, template_id)


def _source_count(template_id: int) -> int:
    with Session(engine) as db:
        return len(
            db.exec(
                select(VoiceprintTemplateSource).where(
                    VoiceprintTemplateSource.template_id == template_id
                )
            ).all()
        )


@pytest.fixture(autouse=True)
def _reset_caches():
    speaker.invalidate_cache()
    voice_match.invalidate_cache()
    yield
    speaker.invalidate_cache()
    voice_match.invalidate_cache()


def test_merge_moves_sources_and_recomputes_centroid():
    owner, _ = setup_pair()
    voice_id = _voiceprint(owner)
    source_id = _template(voice_id, unit(0), sample_ms=2000, segment="a", meeting_id=1)
    target_id = _template(voice_id, unit(1), sample_ms=4000, segment="b", meeting_id=1)
    assert _source_count(source_id) == 1 and _source_count(target_id) == 1

    with Session(engine) as db:
        merged = voice_template.merge_templates(
            db, voiceprint_id=voice_id, source_id=source_id, target_id=target_id
        )
        db.commit()
        merged_id, merged_embedding, merged_sample_ms = (
            merged.id,
            merged.embedding,
            merged.sample_ms,
        )

    assert merged_id == target_id
    assert _source_count(target_id) == 2, "两条模板的来源贡献都应挂到 target"
    assert merged_sample_ms == 6000, "样本时长应累计"
    expected = voice_template._weighted_centroid(
        [voice_template._vec(unit(0).tobytes()), voice_template._vec(unit(1).tobytes())],
        [2.0, 4.0],
    )
    assert voice_template.cosine(
        voice_template._vec(merged_embedding), expected
    ) == pytest.approx(1.0, abs=1e-5)

    retired = _row(source_id)
    assert retired.slot < 0 and retired.superseded_by == target_id, "source 应退役但保留可追溯"
    with Session(engine) as db:
        active = voice_template.active_templates(db, voice_id)
    assert [row.id for row in active] == [target_id]


def test_merge_is_deterministic_for_the_same_sources():
    owner, _ = setup_pair()
    voice_id = _voiceprint(owner)
    first = _template(voice_id, unit(0), segment="a", meeting_id=1)
    second = _template(voice_id, unit(1), segment="b", meeting_id=1)
    with Session(engine) as db:
        merged = voice_template.merge_templates(
            db, voiceprint_id=voice_id, source_id=first, target_id=second
        )
        db.commit()
        once = merged.embedding
    with Session(engine) as db:
        sources = db.exec(
            select(VoiceprintTemplateSource)
            .where(VoiceprintTemplateSource.template_id == second)
            .order_by(VoiceprintTemplateSource.source_key)
        ).all()
        pairs = [(voice_template._vec(item.embedding), item.weight) for item in sources]
        again = voice_template._weighted_centroid(
            [vector for vector, _ in pairs], [weight for _, weight in pairs]
        ).astype(np.float32).tobytes()
    assert once == again, "同一组来源必须得到同一质心（可重放）"


def test_merge_rejects_cross_voiceprint_and_inactive_templates():
    owner, _ = setup_pair()
    voice_id = _voiceprint(owner)
    other_voice = _voiceprint(owner, name="另一个人")
    first = _template(voice_id, unit(0), segment="a", meeting_id=1)
    foreign = _template(other_voice, unit(1), segment="b", meeting_id=1)
    with Session(engine) as db, pytest.raises(ValueError, match="同一声纹"):
        voice_template.merge_templates(
            db, voiceprint_id=voice_id, source_id=foreign, target_id=first
        )
    with Session(engine) as db, pytest.raises(ValueError, match="自己"):
        voice_template.merge_templates(
            db, voiceprint_id=voice_id, source_id=first, target_id=first
        )

    second = _template(voice_id, unit(2), segment="c", meeting_id=1)
    with Session(engine) as db:
        voice_match.set_template_flags(db, template_id=second, disabled=True)
    with Session(engine) as db, pytest.raises(ValueError, match="活跃模板"):
        voice_template.merge_templates(
            db, voiceprint_id=voice_id, source_id=first, target_id=second
        )


def test_merge_transfers_the_primary_flag():
    owner, _ = setup_pair()
    voice_id = _voiceprint(owner)
    source_id = _template(voice_id, unit(0), segment="a", meeting_id=1)
    target_id = _template(voice_id, unit(1), segment="b", meeting_id=1)
    with Session(engine) as db:
        voice_match.set_template_flags(db, template_id=source_id, pinned=True)
    with Session(engine) as db:
        voice_template.merge_templates(
            db, voiceprint_id=voice_id, source_id=source_id, target_id=target_id
        )
        db.commit()
    with Session(engine) as db:
        pinned = db.exec(
            select(VoiceprintTemplate).where(
                VoiceprintTemplate.voiceprint_id == voice_id,
                VoiceprintTemplate.pinned.is_(True),
            )
        ).all()
    assert [row.id for row in pinned] == [target_id], "主模板标记应转移到合并后的模板"

    # 目标本身已有主模板时保持目标
    third = _template(voice_id, unit(2), segment="c", meeting_id=1)
    with Session(engine) as db:
        voice_match.set_template_flags(db, template_id=third, pinned=True)
        voice_template.merge_templates(
            db, voiceprint_id=voice_id, source_id=target_id, target_id=third
        )
        db.commit()
    with Session(engine) as db:
        pinned = db.exec(
            select(VoiceprintTemplate).where(
                VoiceprintTemplate.voiceprint_id == voice_id,
                VoiceprintTemplate.pinned.is_(True),
            )
        ).all()
    assert [row.id for row in pinned] == [third]


def test_merge_endpoint_is_audited_and_replay_is_rejected(client, auth_headers, monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    owner, other = setup_pair()
    voice_id = _voiceprint(owner)
    source_id = _template(voice_id, unit(0), segment="a", meeting_id=1)
    target_id = _template(voice_id, unit(1), segment="b", meeting_id=1)

    path = f"/api/v1/voiceprints/{voice_id}/templates/merge"
    denied = client.post(
        path,
        headers=header(other),
        json={"source_template_id": source_id, "target_template_id": target_id},
    )
    assert denied.status_code in (403, 404), denied.text

    from imeeting.security import create_access_token

    owner_headers = {"Authorization": f"Bearer {create_access_token(str(owner), 'user')}"}
    ok = client.post(
        path,
        headers=owner_headers,
        json={"source_template_id": source_id, "target_template_id": target_id},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["template_id"] == target_id

    with Session(engine) as db:
        actions = [row.action for row in db.exec(select(AuditLog)).all()]
    assert "template_merge" in actions

    replay = client.post(
        path,
        headers=owner_headers,
        json={"source_template_id": source_id, "target_template_id": target_id},
    )
    assert replay.status_code == 409, "已退役的模板不能被重复合并"
