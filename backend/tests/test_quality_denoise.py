"""兜底文本门禁、兜底声纹、内置词表与轮转日志的回归测试。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from logging.config import fileConfig
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np
import pytest
from sqlmodel import Session, select


@pytest.mark.parametrize(
    "text",
    [
        "的户的资金对说说是",
        "国家力业力量的的力的的们也是网",
        "个",
        "的的的的",
        "好好好好好好",
    ],
)
def test_fallback_quality_rejects_observed_garbage(text):
    from imeeting.text_quality import fallback_quality

    usable, reason = fallback_quality(text)
    assert usable is False
    assert reason


@pytest.mark.parametrize(
    "text",
    [
        "我们先看拓扑结构，然后讨论零日漏洞",
        "APT 攻击通常先做内网渗透",
        "这个载荷会通过跳板机投递木马",
        "对，就是这样",
        "我们说的是这个意思",
    ],
)
def test_fallback_quality_keeps_normal_sentences(text):
    from imeeting.text_quality import fallback_quality

    usable, reason = fallback_quality(text)
    assert usable is True, reason


def _fallback_session(monkeypatch, resolver):
    from imeeting.engine.streaming import StreamSession

    session = StreamSession(meeting_id=1, emit=_noop_emit)
    session._translate_enabled = False

    async def fake_resolve(audio, start_ms=0):
        return resolver

    monkeypatch.setattr(session, "_resolve_speaker", fake_resolve)
    return session


async def _noop_emit(_payload: dict) -> None:
    return None


def test_rejected_fallback_never_reaches_speaker(monkeypatch):
    from imeeting.engine.streaming import SegmentState

    session = _fallback_session(monkeypatch, None)
    called = False

    async def fail_if_called(audio, start_ms=0):
        nonlocal called
        called = True
        raise AssertionError("不合格草稿不应进入声纹判定")

    monkeypatch.setattr(session, "_resolve_speaker", fail_if_called)
    seg = SegmentState(
        segment_id="bad-1",
        text="的户的资金对说说是",
        start_ms=0,
        end_ms=4000,
    )
    asyncio.run(
        session._emit_draft_fallback(
            seg, np.zeros(16000, dtype=np.float32), "offline_exception"
        )
    )
    assert called is False
    assert seg.is_final is False
    assert (seg.asr_error or "").startswith("fallback_rejected:")
    assert session.metrics.fallback_rejected == 1
    assert session.metrics.drops.get("fallback_quality") == 1


def test_accepted_fallback_runs_speaker_resolution(monkeypatch):
    from imeeting.engine.speaker import SpeakerResolution
    from imeeting.engine.streaming import SegmentState

    session = _fallback_session(
        monkeypatch,
        SpeakerResolution("陈建峰", "vp_22", 0.874, "enrolled"),
    )
    seg = SegmentState(
        segment_id="ok-1",
        text="今天讨论零日漏洞和供应链攻击路径",
        start_ms=1000,
        end_ms=6000,
        seq=0,
    )
    asyncio.run(
        session._emit_draft_fallback(
            seg, np.ones(16000, dtype=np.float32) * 0.05, "offline_exception"
        )
    )
    assert seg.is_final is True
    assert seg.fallback is True
    assert seg.speaker == "陈建峰"
    assert seg.speaker_key == "vp_22"
    assert seg.speaker_score == 0.874
    assert seg.speaker_source == "enrolled"
    assert session._speaker_context.snapshot() == [("陈建峰", "vp_22", 6000)]


def test_builtin_cyber_glossary_is_idempotent_and_applied(client):
    from imeeting.application.seeds import ensure_builtin_glossary_terms
    from imeeting.db import engine
    from imeeting.glossary import apply_terms, invalidate_cache
    from imeeting.models import GlossaryTerm

    required = {
        "拓扑结构",
        "震网病毒",
        "零日漏洞",
        "载荷",
        "情报载荷",
        "跳板",
        "木马",
        "APT",
        "PDR",
        "DAPV",
        "多模态",
        "数字孪生",
        "动态防御",
        "量子密码",
        "行为认证",
        "数字靶场",
        "防护对象",
        "基础理论",
    }
    ensure_builtin_glossary_terms()
    ensure_builtin_glossary_terms()
    invalidate_cache()
    with Session(engine) as db:
        terms = db.exec(select(GlossaryTerm)).all()
    names = [row.term for row in terms]
    assert required.issubset(set(names))
    assert names.count("拓扑结构") == 1

    assert apply_terms("拓部结构") == "拓扑结构"
    assert apply_terms("阵亡病毒") == "震网病毒"
    assert apply_terms("灵力漏洞") == "零日漏洞"
    assert apply_terms("情报载合") == "情报载荷"
    assert apply_terms("优步码") == "木马"
    assert apply_terms("条板") == "跳板"
    assert apply_terms("空击") == "攻击"
    assert apply_terms("0day") == "零日漏洞"
    assert apply_terms("播模态") == "多模态"
    assert apply_terms("数字本身") == "数字孪生"
    assert apply_terms("团论对象") == "防护对象"
    assert apply_terms("基术理论") == "基础理论"
    assert apply_terms("重险") == "风险"
    assert apply_terms("成务") == "成本"
    # 规范词本身不能被别名再次命中，产生“拓扑结构结构/智能汽车车”。
    assert apply_terms("拓扑结构") == "拓扑结构"
    assert apply_terms("智能汽车") == "智能汽车"


def test_business_log_writes_to_rotating_file(client):
    import imeeting.main  # noqa: F401  导入时安装根日志处理器

    handlers = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, RotatingFileHandler)
    ]
    assert handlers, "根 logger 未安装轮转文件处理器"
    handler = handlers[0]
    assert handler.maxBytes == 20 * 1024 * 1024
    assert handler.backupCount == 10
    path = Path(handler.baseFilename)
    assert path.parent.exists()

    token = f"log-probe-{uuid.uuid4().hex}"
    logging.getLogger("app.test.logging").warning(token)
    handler.flush()
    assert token in path.read_text(encoding="utf-8")


def test_file_logging_recovers_after_alembic_config(client):
    """Alembic fileConfig 重置根 logger 后，业务日志必须仍能落盘。"""
    import imeeting.main as main

    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    fileConfig(str(ini), disable_existing_loggers=False)
    logger = logging.getLogger("app.test.recovery")
    logger.disabled = True

    handler = main._install_file_logging()
    assert isinstance(handler, RotatingFileHandler)
    files = [
        h
        for h in logging.getLogger().handlers
        if getattr(h, "_meeting_app_file", False)
    ]
    assert len(files) == 1, "重复调用不能叠加多个业务文件 handler"
    assert logger.disabled is False
    token = f"recovery-{uuid.uuid4().hex}"
    logger.warning(token)
    handler.flush()
    assert token in Path(handler.baseFilename).read_text(encoding="utf-8")


def test_denoise_recommendation_is_actionable():
    from imeeting.denoise_eval import recommend_denoise

    assert "可" in recommend_denoise(0.80, 0.83, 5, 1)
    assert "中性" in recommend_denoise(0.7999, 0.8014, 2, 3)
    assert "不建议" in recommend_denoise(0.7999, 0.7886, 2, 4)


def test_short_speaker_uses_higher_confidence_threshold():
    from imeeting.speaker_policy import is_low_confidence

    assert is_low_confidence(0.63, 1000, "那么就是未来的一个网络系统和技术。")
    assert is_low_confidence(0.63, 4000, "这是一段足够长的正常语音内容。") is False
    assert is_low_confidence(0.63, 1000, "短句", source="manual") is False
    assert is_low_confidence(0.75, 1000, "短句") is False


def test_segment_out_marks_short_low_confidence():
    from imeeting.models import Segment
    from imeeting.schemas import SegmentOut

    out = SegmentOut.model_validate(
        Segment(
            id=1,
            meeting_id=1,
            segment_id="s-short",
            text="怎么会呢？",
            is_final=True,
            start_ms=0,
            end_ms=2400,
            speaker="陈建峰",
            speaker_score=0.625,
            speaker_source="cluster",
            speech_ms=1000,
            seq=0,
        )
    )
    assert out.speaker_low_confidence is True


def test_trim_silence_keeps_speech_and_margin():
    from imeeting.engine.streaming import StreamSession

    session = StreamSession(meeting_id=1, emit=_noop_emit)
    sr = 16000
    silence = np.zeros(sr * 2, dtype=np.float32)
    tone = (
        0.1 * np.sin(2 * np.pi * 440 * np.arange(sr) / sr)
    ).astype(np.float32)
    audio = np.concatenate([silence, tone, silence])
    trimmed = session._trim_silence_for_asr(audio)
    assert 1.2 * sr <= len(trimmed) <= 2.0 * sr
    assert len(trimmed) < len(audio) * 0.65


def test_backfill_participants_from_speaker_labels(client, admin_user):
    from imeeting.db import engine
    from imeeting.meeting_meta import backfill_participants
    from imeeting.models import Meeting, Segment

    with Session(engine) as db:
        meeting = Meeting(title="单人报告", owner_id=admin_user.id)
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        mid = meeting.id
        db.add(
            Segment(
                meeting_id=mid,
                segment_id="p-1",
                text="第一段",
                is_final=True,
                speaker="陈建峰",
                seq=0,
            )
        )
        db.add(
            Segment(
                meeting_id=mid,
                segment_id="p-2",
                text="第二段",
                is_final=True,
                speaker="说话人 1",
                seq=1,
            )
        )
        db.add(
            Segment(
                meeting_id=mid,
                segment_id="p-3",
                text="第三段",
                is_final=True,
                speaker="0",
                seq=2,
            )
        )
        db.commit()

    assert backfill_participants(mid) == ["陈建峰"]
    with Session(engine) as db:
        assert db.get(Meeting, mid).participants == "陈建峰"


def test_minutes_numeric_claim_mismatch_is_rewritten():
    from imeeting.llm.minutes import _sanitize_numeric_claims

    text = "介绍未来技术预见方法、四大技术方向（多模态、数字孪生、动态防御、行为认证、靶场、密码、认证）"
    fixed = _sanitize_numeric_claims(text)
    assert "四大" not in fixed
    assert "相关技术方向" in fixed
    assert _sanitize_numeric_claims("三大方向（甲、乙、丙）") == "三大方向（甲、乙、丙）"


def test_evidence_text_uses_latest_glossary(client):
    from imeeting.evidence import attach_evidence
    from imeeting.glossary import invalidate_cache
    from imeeting.llm.minutes import TranscriptLine

    invalidate_cache()
    line = TranscriptLine(
        start_ms=0,
        end_ms=3000,
        speaker="陈建峰",
        text="第二就是播模态的数据治理。",
    )
    result = attach_evidence(
        {
            "topics": [
                {"title": "多模态数据治理", "detail": "", "conclusion": ""}
            ],
            "todos": [],
        },
        [line],
    )
    evidence = result["topics"][0]["evidence"]
    assert evidence
    assert "多模态" in evidence[0]["text"]


def test_single_speaker_frontier_report_is_classified_as_lecture():
    from imeeting.meeting_meta import _guess_meeting_type

    assert (
        _guess_meeting_type(
            "网络安全前沿技术研究",
            "讨论前沿技术、研究方法与未来预见",
            ["陈建峰"],
        )
        == "讲座"
    )
    assert _guess_meeting_type("周会", "同步进度", ["甲", "乙"]) == ""


def test_knowledge_context_corrects_asr_terms(client):
    from imeeting.glossary import invalidate_cache
    from imeeting.llm.knowledge_context import build_knowledge_context
    from imeeting.llm.minutes import TranscriptLine

    invalidate_cache()
    lines = [
        TranscriptLine(
            start_ms=0,
            end_ms=3000,
            speaker="陈建峰",
            text="数据确认机制、韩没内衣和clall三opx都被提到了。",
        )
    ]
    context = build_knowledge_context(None, lines)
    assert "知识库校正规则" in context
    assert "数据确权" in context
    assert "哈梅内伊" in context
    assert "Claude 3 Opus" in context


def test_knowledge_context_uses_only_other_final_minutes(client, admin_user):
    from imeeting.db import engine
    from imeeting.llm.knowledge_context import build_knowledge_context
    from imeeting.llm.minutes import TranscriptLine
    from imeeting.models import Meeting, Minutes

    with Session(engine) as db:
        current = Meeting(title="当前会议", owner_id=admin_user.id)
        past = Meeting(title="历史数据安全会议", owner_id=admin_user.id)
        db.add(current)
        db.add(past)
        db.commit()
        db.refresh(current)
        db.refresh(past)
        current_id = current.id
        db.add(
            Minutes(
                meeting_id=past.id,
                summary="数据安全治理与个人信息保护的最终结论。",
                is_draft=False,
            )
        )
        db.commit()

    context = build_knowledge_context(
        current_id,
        [
            TranscriptLine(
                start_ms=0,
                end_ms=2000,
                speaker="陈建峰",
                text="讨论数据安全和个人信息保护。",
            )
        ],
    )
    assert "历史数据安全会议" in context
    assert "[当前会议]" not in context


def test_llm_corrections_are_whitelisted_and_applied(client):
    from imeeting.glossary import invalidate_cache
    from imeeting.llm.corrections import apply_to_result, validate_corrections

    invalidate_cache()
    text = "韩没内衣和clall三opx都需要纠正，科理设备也要归一。"
    corrections = validate_corrections(
        [
            {"from": "韩没内衣", "to": "哈梅内伊", "confidence": 0.95},
            {"from": "clall三opx", "to": "Claude 3 Opus", "confidence": 0.95},
            {"from": "科理设备", "to": "不存在的实体", "confidence": 0.99},
        ],
        text,
    )
    assert ("韩没内衣", "哈梅内伊") in corrections
    assert ("clall三opx", "Claude 3 Opus") in corrections
    assert all(right != "不存在的实体" for _wrong, right in corrections)

    result = apply_to_result(
        {
            "summary": "韩没内衣网络网络攻击",
            "topics": [
                {
                    "title": "clall三opx",
                    "detail": "科理设备",
                    "conclusion": "",
                    "evidence": [{"text": "科理设备"}],
                }
            ],
            "todos": [],
        },
        corrections,
    )
    assert result["summary"] == "哈梅内伊网络攻击"
    assert result["topics"][0]["title"] == "Claude 3 Opus"
    assert result["topics"][0]["detail"] == "算力设备"
    assert result["topics"][0]["evidence"][0]["text"] == "算力设备"
