"""渐进式纪要：版本递增、草稿不覆盖终稿、API 返回最新一版。"""

from __future__ import annotations

import asyncio

from sqlmodel import Session, select


def _seed(client) -> int:
    from imeeting.db import engine
    from imeeting.models import Meeting, Segment, User

    with Session(engine) as db:
        user = db.exec(select(User)).first()
        meeting = Meeting(title="渐进纪要测试", owner_id=user.id, status="recording")
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        for i, text in enumerate(
            [
                "我们先讨论设备故障诊断的数据获取问题。",
                "真实故障数据很难拿，仿真和实验台是替代方案。",
                "接下来需要确定跨设备泛化的评估指标。",
            ]
        ):
            db.add(
                Segment(
                    meeting_id=meeting.id,
                    segment_id=f"s-prog-{i}",
                    session_id="prog",
                    text=text,
                    is_final=True,
                    start_ms=i * 3000,
                    end_ms=i * 3000 + 2400,
                    seq=i,
                )
            )
        db.commit()
        return meeting.id


def test_versions_increment_and_keep_history(client):
    from imeeting.llm.progressive import save_generated_minutes

    mid = _seed(client)
    result = {
        "summary": "阶段摘要",
        "topics": [{"title": "数据", "detail": "d", "conclusion": "c"}],
        "todos": [],
        "raw_markdown": "",
        "model": "test",
    }
    draft = save_generated_minutes(mid, result, is_draft=True)
    final = save_generated_minutes(mid, {**result, "summary": "终稿摘要"}, is_draft=False)

    assert draft.version == 1
    assert final.version == 2
    assert draft.is_draft is True
    assert final.is_draft is False

    from imeeting.db import engine
    from imeeting.models import Minutes

    with Session(engine) as db:
        rows = db.exec(
            select(Minutes).where(Minutes.meeting_id == mid).order_by(Minutes.version)
        ).all()
    assert [r.version for r in rows] == [1, 2]
    assert rows[0].summary == "阶段摘要"


def test_generate_progressive_draft(client, monkeypatch):
    from imeeting.llm import progressive

    mid = _seed(client)

    async def fake_generate(lines, *, model_label="", draft=False, meeting_id=None):
        assert draft is True
        assert lines, "应读取到已落库片段"
        return {
            "summary": "会中草稿",
            "topics": [],
            "todos": [],
            "raw_markdown": "",
            "model": "fake",
        }

    monkeypatch.setattr(progressive, "generate_minutes", fake_generate)
    payload = asyncio.run(
        progressive.generate_progressive_draft(mid, min_chars=10)
    )
    assert payload is not None
    assert payload["is_draft"] is True
    assert payload["version"] == 1
    assert payload["summary"] == "会中草稿"
    assert payload["topics"] == []


def test_progressive_draft_skips_when_too_little_content(client):
    from imeeting.llm.progressive import generate_progressive_draft

    mid = _seed(client)
    assert asyncio.run(generate_progressive_draft(mid, min_chars=10_000)) is None


def test_rolling_draft_uses_only_new_segments(client, monkeypatch):
    """第二版起只把新增转写交给 LLM，避免全量重跑 map-reduce。"""
    from imeeting.llm import progressive

    mid = _seed(client)
    progressive.save_generated_minutes(
        mid,
        {
            "summary": "上一版摘要。" * 20,
            "topics": [{"title": "已有议题", "detail": "", "conclusion": ""}],
            "todos": [],
            "raw_markdown": "",
            "model": "fake",
        },
        is_draft=True,
        transcript_end_ms=3000,
    )
    seen: dict = {}

    async def fake_roll(previous, new_lines, knowledge_context=""):
        seen["texts"] = [line.text for line in new_lines]
        return {
            "summary": "更新后的摘要。" * 30,
            "topics": [{"title": "已有议题", "detail": "补充", "conclusion": ""}],
            "todos": [],
            "raw_markdown": "",
            "model": "fake",
        }

    monkeypatch.setattr(progressive, "_rolling_update", fake_roll)
    payload = asyncio.run(
        progressive.generate_progressive_draft(mid, min_chars=10)
    )
    assert payload is not None
    assert payload["version"] == 2
    # 只应包含 start_ms >= 3000 的两段（第一段不应重复喂给 LLM）
    assert all(not text.startswith("我们先讨论") for text in seen["texts"])
    assert len(seen["texts"]) == 2


def test_generate_final_minutes_is_idempotent(client, monkeypatch):
    from imeeting.llm import progressive

    mid = _seed(client)

    async def fake_generate(lines, *, model_label="", draft=False, meeting_id=None):
        assert draft is False
        return {
            "summary": "终稿摘要",
            "topics": [],
            "todos": [],
            "raw_markdown": "",
            "model": "fake",
        }

    monkeypatch.setattr(progressive, "generate_minutes", fake_generate)
    first = asyncio.run(progressive.generate_final_minutes(mid, min_chars=10))
    assert first is not None
    assert first["is_draft"] is False
    second = asyncio.run(progressive.generate_final_minutes(mid, min_chars=10))
    assert second is None, "已有终稿时不应重复生成"


def test_api_returns_latest_final_after_draft(client, monkeypatch):
    from imeeting.api.http.routes import meetings as meetings_router
    from imeeting.llm.progressive import save_generated_minutes

    mid = _seed(client)
    save_generated_minutes(
        mid,
        {
            "summary": "草稿版本",
            "topics": [],
            "todos": [],
            "raw_markdown": "",
            "model": "fake",
        },
        is_draft=True,
    )

    async def fake_final(
        lines, *, model_label="", draft=False, template=None, meeting_id=None
    ):
        return {
            "summary": "终稿版本",
            "topics": [],
            "todos": [],
            "raw_markdown": "",
            "model": "fake",
        }

    monkeypatch.setattr(meetings_router, "generate_minutes", fake_final)

    resp = client.post(f"/api/v1/meetings/{mid}/minutes", json={"force": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"] == "终稿版本"
    assert body["is_draft"] is False
    assert body["version"] == 2

    latest = client.get(f"/api/v1/meetings/{mid}/minutes").json()
    assert latest["summary"] == "终稿版本"
    assert latest["version"] == 2
