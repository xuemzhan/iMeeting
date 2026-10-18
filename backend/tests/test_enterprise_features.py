"""企业级能力：纪要模板、思维导图、模板注入生成。"""

from __future__ import annotations

import json

from sqlmodel import Session, select


def _seed_meeting_with_minutes() -> int:
    from imeeting.db import engine
    from imeeting.models import Meeting, Minutes, Segment, User

    with Session(engine) as db:
        user = db.exec(select(User)).first()
        meeting = Meeting(title="企业级验收", owner_id=user.id, status="finished")
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        db.add(
            Segment(
                meeting_id=meeting.id,
                segment_id="s-ent-0",
                text="我们决定采用方案 B，下周完成迁移。",
                is_final=True,
                start_ms=0,
                end_ms=2000,
                speaker="王工",
                seq=0,
            )
        )
        db.add(
            Minutes(
                meeting_id=meeting.id,
                summary="决定采用方案 B",
                topics=json.dumps(
                    [
                        {
                            "title": "方案选择",
                            "detail": "对比 A/B 两套方案",
                            "conclusion": "采用方案 B",
                        }
                    ],
                    ensure_ascii=False,
                ),
                todos=json.dumps(
                    [{"task": "完成迁移", "owner": "王工", "due": "下周五"}],
                    ensure_ascii=False,
                ),
                status="ready",
                version=1,
                is_draft=False,
            )
        )
        db.commit()
        return meeting.id


def test_builtin_templates_seeded(client):
    rows = client.get("/api/v1/minutes-templates").json()
    names = {row["name"] for row in rows}
    assert {"标准会议纪要", "项目周会", "决策评审", "客户访谈"} <= names
    assert all(row["is_builtin"] for row in rows)


def test_import_markdown_template(client):
    resp = client.post(
        "/api/v1/minutes-templates/import",
        files={"file": ("周报模板.md", "# 周报\n{transcript}".encode(), "text/markdown")},
        data={"name": "周报模板", "description": "导入测试"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "周报模板"
    assert "{transcript}" in body["content"]
    assert body["is_builtin"] is False

    # 同名重复导入应 409
    again = client.post(
        "/api/v1/minutes-templates/import",
        files={"file": ("周报模板.md", b"x", "text/markdown")},
        data={"name": "周报模板"},
    )
    assert again.status_code == 409


def test_builtin_template_cannot_be_deleted(client):
    rows = client.get("/api/v1/minutes-templates").json()
    builtin = next(row for row in rows if row["is_builtin"])
    assert client.delete(f"/api/v1/minutes-templates/{builtin['id']}").status_code == 400


def test_generate_minutes_uses_selected_template(client, monkeypatch):
    from imeeting.api.http.routes import meetings as meetings_router

    mid = _seed_meeting_with_minutes()
    created = client.post(
        "/api/v1/minutes-templates",
        json={"name": "自定义模板", "description": "", "content": "只输出要点：{transcript}"},
    )
    assert created.status_code == 200, created.text
    template_id = created.json()["id"]

    captured: dict = {}

    async def fake_generate(
        lines, *, model_label="", draft=False, template=None, meeting_id=None
    ):
        captured["template"] = template
        return {
            "summary": "按模板生成",
            "topics": [],
            "todos": [],
            "raw_markdown": "",
            "model": "fake",
        }

    monkeypatch.setattr(meetings_router, "generate_minutes", fake_generate)
    resp = client.post(
        f"/api/v1/meetings/{mid}/minutes",
        json={"force": True, "template_id": template_id},
    )
    assert resp.status_code == 200, resp.text
    assert captured["template"] == "只输出要点：{transcript}"
    assert resp.json()["summary"] == "按模板生成"


def test_mindmap_tree_from_latest_minutes(client):
    mid = _seed_meeting_with_minutes()
    resp = client.get(f"/api/v1/meetings/{mid}/mindmap")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["root"]["label"] == "企业级验收"
    labels = [child["label"] for child in body["root"]["children"]]
    assert "方案选择" in labels
    assert "待办事项" in labels
    topic = next(c for c in body["root"]["children"] if c["label"] == "方案选择")
    assert any("采用方案 B" in child["label"] for child in topic["children"])
    todo = next(c for c in body["root"]["children"] if c["label"] == "待办事项")
    assert todo["children"][0]["meta"] == "责任人：王工 · 截止：下周五"


def test_mindmap_requires_minutes(client):
    from imeeting.db import engine
    from imeeting.models import Meeting, User

    with Session(engine) as db:
        user = db.exec(select(User)).first()
        meeting = Meeting(title="无纪要", owner_id=user.id, status="finished")
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        mid = meeting.id
    assert client.get(f"/api/v1/meetings/{mid}/mindmap").status_code == 404
