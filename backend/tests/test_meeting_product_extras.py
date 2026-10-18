"""产品化能力验收：元信息、修订、分享、日历、知识库、文档导出。"""

from __future__ import annotations

import asyncio
import json

from sqlmodel import Session, select


def _seed_meeting(
    *,
    title: str = "产品化验收会议",
    with_minutes: bool = True,
) -> int:
    from imeeting.db import engine
    from imeeting.models import Meeting, Minutes, Segment, User

    with Session(engine) as db:
        user = db.exec(select(User).where(User.username == "admin")).one()
        meeting = Meeting(
            title=title,
            owner_id=user.id,
            status="finished",
            duration_ms=12_000,
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        for index, text in enumerate(
            [
                "本次会议讨论设备轴承故障诊断。",
                "我们决定采用方案 B，下周完成迁移。",
            ]
        ):
            db.add(
                Segment(
                    meeting_id=meeting.id,
                    segment_id=f"prod-{index}",
                    text=text,
                    is_final=True,
                    start_ms=index * 3000,
                    end_ms=index * 3000 + 2400,
                    speaker="王工",
                    seq=index,
                )
            )
        if with_minutes:
            db.add(
                Minutes(
                    meeting_id=meeting.id,
                    summary="讨论设备故障诊断并决定采用方案 B。",
                    topics=json.dumps(
                        [
                            {
                                "title": "方案选择",
                                "detail": "对比 A/B 方案",
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


def test_metadata_segment_edit_and_minutes_version(
    client, auth_headers
):
    from imeeting.db import engine
    from imeeting.models import ActionItem, Segment

    mid = _seed_meeting()
    updated = client.patch(
        f"/api/v1/meetings/{mid}/metadata",
        headers=auth_headers,
        json={
            "title": "轴承诊断评审",
            "participants": "王工, 李工",
            "agenda": "诊断方法与迁移计划",
            "meeting_type": "评审",
            "description": "人工修订会议说明",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "轴承诊断评审"
    assert updated.json()["metadata_captured"] is True

    edited = client.patch(
        f"/api/v1/meetings/{mid}/segments/prod-0",
        headers=auth_headers,
        json={"text": "本次会议讨论轴承故障的智能诊断。"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["original_text"] == "本次会议讨论设备轴承故障诊断。"
    assert edited.json()["text"] == "本次会议讨论轴承故障的智能诊断。"
    assert edited.json()["edited_at"]

    with Session(engine) as db:
        segment = db.exec(
            select(Segment).where(
                Segment.meeting_id == mid, Segment.segment_id == "prod-0"
            )
        ).one()
        assert segment.original_text

    revised = client.patch(
        f"/api/v1/meetings/{mid}/minutes/1",
        headers=auth_headers,
        json={
            "summary": "人工修订后的摘要。",
            "topics": [
                {
                    "title": "方案选择",
                    "detail": "确认采用 B",
                    "conclusion": "完成迁移",
                }
            ],
            "todos": [{"task": "完成迁移并复盘", "owner": "王工", "due": "本周五"}],
        },
    )
    assert revised.status_code == 200, revised.text
    body = revised.json()
    assert body["version"] == 2
    assert body["edited"] is True
    assert body["summary"] == "人工修订后的摘要。"

    versions = client.get(
        f"/api/v1/meetings/{mid}/minutes/versions", headers=auth_headers
    )
    assert versions.status_code == 200
    assert [row["version"] for row in versions.json()] == [2, 1]

    actions = client.get(
        f"/api/v1/meetings/{mid}/actions", headers=auth_headers
    )
    assert actions.status_code == 200
    tasks = {row["task"] for row in actions.json()}
    assert "完成迁移并复盘" in tasks
    with Session(engine) as db:
        synced = db.exec(
            select(ActionItem).where(ActionItem.meeting_id == mid)
        ).all()
        assert any(item.source_version == 2 for item in synced)


def test_share_password_expiry_and_audio_permission(client, auth_headers):
    mid = _seed_meeting(title="分享测试")
    created = client.post(
        f"/api/v1/meetings/{mid}/shares",
        headers=auth_headers,
        json={"expires_hours": 24, "password": "1234", "allow_download": False},
    )
    assert created.status_code == 200, created.text
    share = created.json()
    assert share["has_password"] is True

    assert client.get(f"/api/v1/share/{share['token']}").status_code == 401
    assert (
        client.post(
            f"/api/v1/share/{share['token']}/open",
            json={"password": "bad"},
        ).status_code
        == 401
    )
    access = client.post(
        f"/api/v1/share/{share['token']}/open",
        json={"password": "1234"},
    )
    assert access.status_code == 200, access.text
    headers = {"Authorization": f"Bearer {access.json()['access_token']}"}
    opened = client.get(f"/api/v1/share/{share['token']}", headers=headers)
    assert opened.status_code == 200, opened.text
    assert opened.json()["meeting"]["id"] == mid
    assert opened.json()["allow_download"] is False
    assert (
        client.get(
            f"/api/v1/share/{share['token']}/audio",
            headers=headers,
        ).status_code
        == 403
    )

    listed = client.get(
        f"/api/v1/meetings/{mid}/shares", headers=auth_headers
    ).json()
    assert any(row["token"] == share["token"] for row in listed)
    revoked = client.delete(
        f"/api/v1/meetings/{mid}/shares/{share['token']}", headers=auth_headers
    )
    assert revoked.status_code == 204
    assert (
        client.post(
            f"/api/v1/share/{share['token']}/open",
            json={"password": "1234"},
        ).status_code
        == 404
    )


def test_ics_import_and_scheduled_list(client, auth_headers):
    ics = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "BEGIN:VEVENT",
            "SUMMARY:季度技术评审",
            "DTSTART:20261020T090000",
            "DESCRIPTION:评审轴承诊断方案",
            "ATTENDEE;CN=王工:mailto:wang@example.com",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )
    imported = client.post(
        "/api/v1/meetings/import-ics",
        headers=auth_headers,
        files={"file": ("calendar.ics", ics.encode("utf-8"), "text/calendar")},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["imported"] == 1
    meeting = imported.json()["meetings"][0]
    assert meeting["status"] == "scheduled"
    assert meeting["participants"] == "王工"
    assert meeting["agenda"] == "评审轴承诊断方案"

    again = client.post(
        "/api/v1/meetings/import-ics",
        headers=auth_headers,
        files={"file": ("calendar.ics", ics.encode("utf-8"), "text/calendar")},
    )
    assert again.json()["imported"] == 0
    assert again.json()["skipped"] >= 1

    scheduled = client.get(
        "/api/v1/meetings/scheduled", headers=auth_headers
    )
    assert scheduled.status_code == 200
    assert any(row["id"] == meeting["id"] for row in scheduled.json())


def test_knowledge_search_includes_topics_and_dashboard(client, auth_headers):
    _seed_meeting(title="知识库会议")
    found = client.get(
        "/api/v1/knowledge/search",
        headers=auth_headers,
        params={"q": "方案选择"},
    )
    assert found.status_code == 200, found.text
    assert found.json()["total"] >= 1

    topics = client.get("/api/v1/knowledge/topics", headers=auth_headers)
    assert topics.status_code == 200
    assert any(row["topic"] == "方案选择" for row in topics.json())

    dashboard = client.get("/api/v1/admin/dashboard", headers=auth_headers)
    assert dashboard.status_code == 200, dashboard.text
    body = dashboard.json()
    assert body["meetings"] >= 1
    assert body["segments"] >= 1

    audit = client.get("/api/v1/admin/audit", headers=auth_headers)
    assert audit.status_code == 200
    assert "items" in audit.json()


def test_docx_and_pdf_exports_are_real_documents(client, auth_headers):
    mid = _seed_meeting(title="文档导出")
    docx = client.get(
        f"/api/v1/meetings/{mid}/export?format=docx", headers=auth_headers
    )
    assert docx.status_code == 200, docx.text
    assert docx.content[:2] == b"PK"
    assert "wordprocessingml" in docx.headers["content-type"]

    pdf = client.get(
        f"/api/v1/meetings/{mid}/export?format=pdf", headers=auth_headers
    )
    assert pdf.status_code == 200, pdf.text
    assert pdf.content.startswith(b"%PDF")


def test_spoken_metadata_heuristic_and_manual_capture_guard(client):
    from imeeting.db import engine
    from imeeting.meeting_meta import capture_meeting_metadata
    from imeeting.models import Meeting, Segment, User
    from imeeting.settings import config_store

    with Session(engine) as db:
        user = db.exec(select(User).where(User.username == "admin")).one()
        meeting = Meeting(
            title="10月17日 09:30 会议",
            owner_id=user.id,
            status="recording",
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        db.add(
            Segment(
                meeting_id=meeting.id,
                segment_id="spoken-meta",
                text="本次会议主题是轴承故障诊断评审，参会人有王工和李工，主要讨论迁移计划。",
                is_final=True,
                start_ms=1000,
                end_ms=6000,
                speaker="说话人 1",
                seq=0,
            )
        )
        db.commit()
        mid = meeting.id

    config_store.set_overrides(
        {
            "meeting.metadata_capture_minutes": 3,
            "meeting.metadata_min_chars": 10,
        }
    )
    payload = asyncio.run(capture_meeting_metadata(mid))
    assert payload is not None
    assert "轴承故障诊断评审" in payload["title"]
    assert "说话人 1" in payload["participants"]

    # 自动提取只执行一次；用户手动保存后不会被后续结果覆盖。
    with Session(engine) as db:
        meeting = db.get(Meeting, mid)
        meeting.title = "人工确认标题"
        db.add(meeting)
        db.commit()
    assert asyncio.run(capture_meeting_metadata(mid)) is None


def test_resume_meeting_rejects_other_owner():
    from imeeting.api.ws.stream import _resolve_meeting
    from imeeting.db import engine
    from imeeting.models import Meeting, User, utcnow
    from imeeting.security import hash_password

    with Session(engine) as db:
        admin = db.exec(select(User).where(User.username == "admin")).one()
        admin_id = admin.id
        other = User(
            username="other-user",
            display_name="其他用户",
            password_hash=hash_password("other123"),
            role="user",
        )
        db.add(other)
        db.commit()
        db.refresh(other)
        meeting = Meeting(
            title="私有会议",
            owner_id=admin_id,
            status="finished",
            finished_at=utcnow(),
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        mid = meeting.id
        other_id = other.id

    assert _resolve_meeting({"meeting_id": mid}, other_id) is None
    assert _resolve_meeting({"meeting_id": mid}, admin_id) == mid
