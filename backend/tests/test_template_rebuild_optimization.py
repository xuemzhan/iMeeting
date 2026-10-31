"""模板重建与匹配路径优化（R6）：取样缓存、细粒度进度、场景粗筛与迁移。

对应修复报告“仍待完成”里的两项结构能力：

- ``嵌入缓存``：同一片段在重跑重建时不再重复解码 + 跑模型；
- ``细粒度进度``：按说话人推进并落库，长会议可在处理过程中查询。

- ``先筛后算``：场景桶粗筛改成整型向量化，语义与 ``Scenario.match_score`` 一致。
同时锁住两条容易回归的边界：音频变化必须让缓存失效；会议物理删除时必须先
清理这两张新表，否则会重演 GAP-15 的外键错误。
"""

from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlmodel import Session, create_engine, select

from imeeting.db import engine
from imeeting.engine import speaker, speaker_reconcile, voice_match, voice_template
from imeeting.engine.voice_template import Scenario
from imeeting.meeting_cleanup import purge_expired_meetings
from imeeting.models import (
    Meeting,
    Segment,
    TemplateRebuildState,
    TemplateSampleEmbedding,
    Voiceprint,
    VoiceprintTemplateSource,
    utcnow,
)

from .test_doc_code_gap_regressions import header, setup_pair, unit

KEY = "spk_opt"


def _audio(tmp_path: Path) -> Path:
    path = tmp_path / "m.wav"
    path.write_bytes(b"RIFF")
    return path


def _meeting(owner_id: int, audio: Path, *, speaker_key: str = KEY) -> int:
    with Session(engine) as db:
        meeting = Meeting(
            title="模板重建优化",
            owner_id=owner_id,
            audio_path=str(audio),
            duration_ms=24_000,
            status="finished",
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        return meeting.id


def _segments(meeting_id: int, *, speaker_key: str = KEY, count: int = 6) -> None:
    with Session(engine) as db:
        for index in range(count):
            db.add(
                Segment(
                    meeting_id=meeting_id,
                    segment_id=f"{speaker_key}-{index}",
                    text="这是一句用于模板取样的发言。",
                    is_final=True,
                    start_ms=index * 4000,
                    end_ms=index * 4000 + 3000,
                    speech_ms=3000,
                    snr_db=7.0,
                    audio_rms=0.03,
                    speaker_key=speaker_key,
                )
            )
        db.commit()


def _voiceprint(owner_id: int, *, speaker_key: str = KEY, name: str = "优化说话人") -> int:
    with Session(engine) as db:
        row = Voiceprint(
            owner_id=owner_id,
            name=name,
            embedding=unit().tobytes(),
            speaker_key=speaker_key,
            sample_ms=8000,
            sample_count=8,
            provisional=False,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


@pytest.fixture(autouse=True)
def _reset_caches():
    voice_match.invalidate_cache()
    speaker.invalidate_cache()
    yield
    voice_match.invalidate_cache()
    speaker.invalidate_cache()


def _counting_audio(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"decode": 0, "extract": 0}

    def decode(*_args, **_kwargs):
        calls["decode"] += 1
        return np.zeros(16000, dtype=np.float32)

    def extract(_pcm):
        calls["extract"] += 1
        return voice_template._vec(unit().tobytes())

    monkeypatch.setattr(speaker_reconcile, "_read_audio", decode)
    monkeypatch.setattr(speaker, "extract_embedding", extract)
    return calls


def test_second_rebuild_reuses_cached_vectors(tmp_path, monkeypatch, admin_user):
    audio = _audio(tmp_path)
    meeting_id = _meeting(admin_user.id, audio)
    _segments(meeting_id)
    _voiceprint(admin_user.id)
    calls = _counting_audio(monkeypatch)

    first = voice_template.rebuild_meeting_templates(meeting_id)
    assert first["cache_hits"] == 0
    assert first["cache_misses"] == 6
    decoded = calls["decode"]
    assert decoded == 6

    second = voice_template.rebuild_meeting_templates(meeting_id)
    assert second["cache_hits"] == 6, "二次重建应全部命中取样缓存"
    assert second["cache_misses"] == 0
    assert calls["decode"] == decoded, "命中缓存时不应再解码音频"
    assert calls["extract"] == decoded, "命中缓存时不应再跑声纹模型"

    with Session(engine) as db:
        rows = db.exec(select(TemplateSampleEmbedding)).all()
        assert len(rows) == 6, "同一片段只应保留一行缓存"
        sources = db.exec(select(VoiceprintTemplateSource)).all()
    assert len({row.source_key for row in sources}) == len(sources), "重跑不应重复累计同一来源"


def test_cache_invalidates_after_audio_or_boundary_change(tmp_path, monkeypatch, admin_user):
    audio = _audio(tmp_path)
    meeting_id = _meeting(admin_user.id, audio)
    _segments(meeting_id)
    _voiceprint(admin_user.id)
    calls = _counting_audio(monkeypatch)
    voice_template.rebuild_meeting_templates(meeting_id)
    assert calls["decode"] == 6

    audio.write_bytes(b"RIFF-changed-audio-bytes")
    changed = voice_template.rebuild_meeting_templates(meeting_id)
    assert changed["cache_hits"] == 0, "音频文件变化后缓存必须失效"
    assert changed["cache_misses"] == 6
    assert calls["decode"] == 12

    with Session(engine) as db:
        segment = db.exec(
            select(Segment).where(Segment.meeting_id == meeting_id).order_by(Segment.start_ms)
        ).first()
        segment.end_ms += 500
        db.add(segment)
        db.commit()
    moved = voice_template.rebuild_meeting_templates(meeting_id)
    assert moved["cache_hits"] == 5, "只有边界变化的片段需要重算"
    assert moved["cache_misses"] == 1
    assert calls["decode"] == 13


def test_progress_reaches_done_and_exposes_cache_ratio(tmp_path, monkeypatch, admin_user):
    audio = _audio(tmp_path)
    meeting_id = _meeting(admin_user.id, audio)
    _segments(meeting_id)
    _voiceprint(admin_user.id)
    _counting_audio(monkeypatch)

    assert voice_template.rebuild_state(meeting_id)["stage"] == "idle"
    voice_template.rebuild_meeting_templates(meeting_id)

    state = voice_template.rebuild_state(meeting_id)
    assert state["stage"] == "done"
    assert state["speakers_total"] == 1
    assert state["speakers_done"] == 1
    assert state["samples_done"] == 6
    assert state["samples_cached"] + state["samples_extracted"] == state["samples_done"]
    assert state["templates_created"] + state["templates_updated"] >= 1
    assert state["cache_hit_rate"] == 0.0
    assert state["finished_at"] is not None
    assert state["last_error"] == ""


def test_failure_keeps_partial_progress_and_records_error(tmp_path, monkeypatch, admin_user):
    audio = _audio(tmp_path)
    meeting_id = _meeting(admin_user.id, audio)
    _segments(meeting_id, speaker_key="spk_a", count=3)
    _segments(meeting_id, speaker_key="spk_b", count=3)
    _voiceprint(admin_user.id, speaker_key="spk_a", name="甲")
    _voiceprint(admin_user.id, speaker_key="spk_b", name="乙")
    _counting_audio(monkeypatch)

    original = voice_template._sample_vectors

    def flaky(db, meeting_id_arg, speaker_key, *, stats=None):
        if speaker_key == "spk_b":
            raise RuntimeError("模型内存不足")
        return original(db, meeting_id_arg, speaker_key, stats=stats)

    monkeypatch.setattr(voice_template, "_sample_vectors", flaky)

    with pytest.raises(RuntimeError):
        voice_template.rebuild_meeting_templates(meeting_id)

    state = voice_template.rebuild_state(meeting_id)
    assert state["stage"] == "failed"
    assert "模型内存不足" in state["last_error"]
    assert state["speakers_total"] == 2
    assert state["speakers_done"] == 1, "第一位说话人的进度必须已经提交"


def test_stale_cache_is_pruned_and_removed_with_meeting(tmp_path, monkeypatch, admin_user):
    audio = _audio(tmp_path)
    meeting_id = _meeting(admin_user.id, audio)
    _segments(meeting_id)
    with Session(engine) as db:
        db.add(
            TemplateSampleEmbedding(
                meeting_id=meeting_id,
                segment_id="stale",
                audio_signature="old",
                embedding=unit().tobytes(),
                updated_at=utcnow() - timedelta(days=90),
            )
        )
        db.commit()
        assert voice_template.prune_sample_cache(db, 30) == 1
        db.commit()
    with Session(engine) as db:
        assert not db.exec(
            select(TemplateSampleEmbedding).where(
                TemplateSampleEmbedding.segment_id == "stale"
            )
        ).all()

    _voiceprint(admin_user.id)
    _counting_audio(monkeypatch)
    voice_template.rebuild_meeting_templates(meeting_id)
    with Session(engine) as db:
        assert db.exec(select(TemplateSampleEmbedding)).all()
        assert db.get(TemplateRebuildState, meeting_id) is not None
        meeting = db.get(Meeting, meeting_id)
        meeting.deleted_at = utcnow() - timedelta(days=2)
        db.add(meeting)
        db.commit()

    assert purge_expired_meetings(1) == 1
    with Session(engine) as db:
        assert db.get(Meeting, meeting_id) is None
        assert not db.exec(
            select(TemplateSampleEmbedding).where(
                TemplateSampleEmbedding.meeting_id == meeting_id
            )
        ).all()
        assert db.get(TemplateRebuildState, meeting_id) is None


def test_rebuild_status_endpoint_requires_meeting_access(client, monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    owner, other = setup_pair()
    with Session(engine) as db:
        meeting = Meeting(title="接口进度", owner_id=owner, audio_path=None)
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        meeting_id = meeting.id

    ok = client.get(
        f"/api/v1/voiceprints/templates/rebuild-status?meeting_id={meeting_id}",
        headers=header(owner),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["stage"] == "idle"
    assert ok.json()["speakers_total"] == 0

    denied = client.get(
        f"/api/v1/voiceprints/templates/rebuild-status?meeting_id={meeting_id}",
        headers=header(other),
    )
    assert denied.status_code == 403, denied.text


def test_migration_adds_and_removes_optimization_tables(tmp_path):
    migration_root = Path(__file__).resolve().parents[1] / "migrations/versions"
    isolated = create_engine(f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    modules = []
    with isolated.begin() as conn:
        conn.execute(sa.text("CREATE TABLE voiceprint (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE meeting (id INTEGER PRIMARY KEY)"))
        for filename in (
            "20261031_03_voiceprint_templates.py",
            "20261031_04_identity_integrity.py",
            "20261031_05_template_optimization.py",
        ):
            spec = importlib.util.spec_from_file_location(filename[:-3], migration_root / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules.append(module)
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        inspector = sa.inspect(conn)
        assert inspector.has_table("template_sample_embedding")
        assert inspector.has_table("template_rebuild_state")
        conn.execute(
            sa.text(
                "INSERT INTO template_sample_embedding"
                "(meeting_id,segment_id,audio_signature,embedding) VALUES(1,'s1','sig',X'00')"
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO template_sample_embedding"
                    "(meeting_id,segment_id,audio_signature,embedding) VALUES(1,'s1','sig2',X'00')"
                )
            )
        with Operations.context(MigrationContext.configure(conn)):
            modules[-1].downgrade()
        assert not sa.inspect(conn).has_table("template_rebuild_state")
        assert not sa.inspect(conn).has_table("template_sample_embedding")
        # 再次升级仍然可用（降级后再升级的兼容性）
        with Operations.context(MigrationContext.configure(conn)):
            modules[-1].upgrade()
        assert sa.inspect(conn).has_table("template_rebuild_state")
    isolated.dispose()


# ---------------------------------------------------------------------------
# 场景粗筛：整型向量化必须与逐行 match_score 完全等价
# ---------------------------------------------------------------------------


def test_scenario_mask_matches_scenario_score_semantics():
    rng = np.random.default_rng(20261031)
    values = ["", "local_mic", "usb_mic", "phone"]
    rows = [
        {
            "scenario": Scenario(
                device=str(rng.choice(values)),
                distance=str(rng.choice(["", "near", "mid", "far"])),
                noise=str(rng.choice(["", "quiet", "reverb", "noisy"])),
                voice=str(rng.choice(["", "normal", "fast", "low"])),
            )
        }
        for _ in range(300)
    ]
    vocab, codes = voice_match._scenario_tables(rows)
    for _ in range(40):
        query = Scenario(
            device=str(rng.choice(values)),
            distance=str(rng.choice(["", "near", "mid", "far"])),
            noise=str(rng.choice(["", "quiet", "reverb", "noisy"])),
            voice=str(rng.choice(["", "normal", "fast", "low"])),
        )
        mask = voice_match.scenario_mask(rows, codes, vocab, query)
        expected = np.array([row["scenario"].match_score(query) >= 0.6 for row in rows])
        assert np.array_equal(mask, expected), query


def test_prefilter_does_not_change_the_matched_identity(monkeypatch):
    owner, _ = setup_pair()
    with Session(engine) as db:
        target = Voiceprint(
            owner_id=owner,
            name="粗筛目标",
            embedding=unit(0).tobytes(),
            sample_ms=4000,
            sample_count=4,
            provisional=False,
        )
        db.add(target)
        db.flush()
        voice_template.store_template(
            db,
            voiceprint_id=target.id,
            embedding=unit(0),
            scenario=Scenario(device="local_mic", distance="near", noise="quiet", voice="normal"),
            quality=1.0,
            confirmed=True,
        )
        # 再塞入 60 条不同场景/不同人的模板，保证进入粗筛分支
        for index in range(60):
            other = Voiceprint(
                owner_id=owner,
                name=f"他人{index}",
                embedding=unit(1 + index).tobytes(),
                sample_ms=2000,
                sample_count=2,
                provisional=False,
            )
            db.add(other)
            db.flush()
            voice_template.store_template(
                db,
                voiceprint_id=other.id,
                embedding=unit(1 + index),
                scenario=Scenario(device="usb_mic", distance="far", noise="noisy", voice="fast"),
                quality=0.6,
                confirmed=True,
            )
        db.commit()

    scenario = Scenario(device="local_mic", distance="near", noise="quiet", voice="normal")
    voice_match.invalidate_cache()
    prefiltered = voice_match.match(unit(0), meeting_id=None, scenario=scenario, speech_ms=3000)
    monkeypatch.setattr(
        voice_match,
        "_int",
        lambda key, default: 10**9 if key == "spk.stage1_min_templates" else default,
    )
    voice_match.invalidate_cache()
    full = voice_match.match(unit(0), meeting_id=None, scenario=scenario, speech_ms=3000)
    assert prefiltered is not None and prefiltered["name"] == "粗筛目标"
    assert full is not None and full["name"] == "粗筛目标"
    assert prefiltered["voiceprint_id"] == full["voiceprint_id"]
    assert prefiltered["score"] == pytest.approx(full["score"], abs=1e-6)


def test_cache_invalidates_when_embedding_model_changes(tmp_path, monkeypatch, admin_user):
    from imeeting.settings import config_store

    audio = _audio(tmp_path)
    meeting_id = _meeting(admin_user.id, audio)
    _segments(meeting_id)
    _voiceprint(admin_user.id)
    calls = _counting_audio(monkeypatch)
    voice_template.rebuild_meeting_templates(meeting_id)
    assert calls["decode"] == 6

    config_store.set_overrides({"spk.embedding_model": "iic/another-embedding-model"})
    changed = voice_template.rebuild_meeting_templates(meeting_id)
    assert changed["cache_hits"] == 0, "换声纹模型后旧向量不能继续复用"
    assert changed["cache_misses"] == 6
    assert calls["decode"] == 12
