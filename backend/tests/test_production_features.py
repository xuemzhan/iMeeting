"""生产级能力验收：任务、排空、导出、词表、脱敏、证据链与 ACL。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from sqlmodel import Session, select


def _seed(title="生产验收") -> tuple[int, int]:
    from imeeting.db import engine
    from imeeting.models import Meeting, Minutes, Segment, User

    with Session(engine) as db:
        user = db.exec(select(User).where(User.username == "admin")).one()
        meeting = Meeting(
            title=title,
            owner_id=user.id,
            status="finished",
            duration_ms=6000,
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        db.add(
            Segment(
                meeting_id=meeting.id,
                segment_id="s-prod-0",
                text="联系人手机号是13800138000，决定采用方案B。",
                is_final=True,
                start_ms=0,
                end_ms=3000,
                speaker="王工",
                seq=0,
            )
        )
        db.add(
            Minutes(
                meeting_id=meeting.id,
                summary="联系 13800138000 并采用方案 B。",
                topics=json.dumps(
                    [{"title": "方案B", "detail": "联系人 13800138000", "conclusion": "采用"}],
                    ensure_ascii=False,
                ),
                todos=json.dumps(
                    [{"task": "联系 13800138000", "owner": "王工", "due": "明天"}],
                    ensure_ascii=False,
                ),
                status="ready",
                version=1,
                is_draft=False,
            )
        )
        db.commit()
        return meeting.id, user.id


def test_durable_export_job_and_file(client, auth_headers):
    mid, _ = _seed("异步导出")
    response = client.post(
        f"/api/v1/meetings/{mid}/export-jobs",
        headers=auth_headers,
        json={"format": "md"},
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]
    job = None
    for _ in range(100):
        job = client.get(
            f"/api/v1/export-jobs/{job_id}", headers=auth_headers
        ).json()
        if job["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    assert job is not None and job["status"] == "succeeded", job
    assert job["result_path"] and Path(job["result_path"]).exists()
    download = client.get(
        f"/api/v1/export-jobs/{job_id}/download", headers=auth_headers
    )
    assert download.status_code == 200
    assert "异步导出" in download.content.decode("utf-8")


def test_drain_resume_and_ready(client, auth_headers):
    from imeeting.maintenance import set_draining

    try:
        drain = client.post("/api/v1/admin/maintenance/drain", headers=auth_headers)
        assert drain.status_code == 200, drain.text
        assert client.get("/api/v1/ready").status_code == 503
        resume = client.post("/api/v1/admin/maintenance/resume", headers=auth_headers)
        assert resume.status_code == 200
        assert client.get("/api/v1/ready").status_code == 200
    finally:
        set_draining(False)


def test_audit_hash_chain_and_export(client, auth_headers):
    _seed("审计")
    verified = client.get("/api/v1/admin/audit/verify", headers=auth_headers)
    assert verified.status_code == 200, verified.text
    assert verified.json()["valid"] is True
    exported = client.get("/api/v1/admin/audit/export", headers=auth_headers)
    assert exported.status_code == 200
    assert exported.content.startswith(b"\xef\xbb\xbf")


def test_glossary_alias_and_hotwords(client, auth_headers):
    from imeeting.glossary import apply_terms, hotwords_text, invalidate_cache

    created = client.post(
        "/api/v1/glossary",
        headers=auth_headers,
        json={"term": "XX-1000", "aliases": "叉叉一千,XX1000", "category": "型号"},
    )
    assert created.status_code == 200, created.text
    invalidate_cache()
    assert apply_terms("今天讨论XX1000的故障") == "今天讨论XX-1000的故障"
    assert "XX-1000" in (hotwords_text() or "")


def test_redaction_evidence_and_retention(client, auth_headers):
    from imeeting.db import engine
    from imeeting.evidence import attach_evidence
    from imeeting.llm.minutes import TranscriptLine
    from imeeting.models import Minutes, Segment

    mid, _ = _seed("脱敏")
    preview = client.post(
        f"/api/v1/meetings/{mid}/redact",
        headers=auth_headers,
        json={"dry_run": True},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["replacements"] >= 2
    applied = client.post(
        f"/api/v1/meetings/{mid}/redact",
        headers=auth_headers,
        json={"dry_run": False},
    )
    assert applied.status_code == 200
    with Session(engine) as db:
        segment = db.exec(
            select(Segment).where(Segment.meeting_id == mid)
        ).one()
        minutes = db.exec(
            select(Minutes).where(Minutes.meeting_id == mid)
        ).one()
        assert "13800138000" not in segment.text
        assert "手机号" in segment.text
        assert "13800138000" not in minutes.summary

    result = attach_evidence(
        {"topics": [{"title": "方案B", "detail": "采用", "conclusion": ""}], "todos": []},
        [
            TranscriptLine(
                start_ms=0,
                end_ms=1000,
                speaker="王工",
                text="我们决定采用方案B",
                segment_id="seg-1",
                segment_ids=["seg-1"],
            )
        ],
    )
    assert result["topics"][0]["evidence"][0]["segment_id"] == "seg-1"

    retention = client.patch(
        f"/api/v1/meetings/{mid}/retention",
        headers=auth_headers,
        json={"retention_days": 7, "legal_hold": True},
    )
    assert retention.status_code == 200
    assert retention.json()["legal_hold"] is True


def test_meeting_member_read_and_write_permissions(client, auth_headers):
    from imeeting.db import engine
    from imeeting.models import User
    from imeeting.security import hash_password

    mid, _ = _seed("权限")
    with Session(engine) as db:
        for username in ("viewer-user", "editor-user"):
            if db.exec(select(User).where(User.username == username)).first() is None:
                db.add(
                    User(
                        username=username,
                        display_name=username,
                        password_hash=hash_password("pass1234"),
                        role="user",
                    )
                )
        db.commit()
    for username, role in (("viewer-user", "viewer"), ("editor-user", "editor")):
        added = client.post(
            f"/api/v1/meetings/{mid}/members",
            headers=auth_headers,
            json={"username": username, "role": role},
        )
        assert added.status_code == 200, added.text

    viewer = client.post(
        "/api/v1/auth/login",
        json={"username": "viewer-user", "password": "pass1234"},
    ).json()["access_token"]
    editor = client.post(
        "/api/v1/auth/login",
        json={"username": "editor-user", "password": "pass1234"},
    ).json()["access_token"]
    viewer_headers = {"Authorization": f"Bearer {viewer}"}
    editor_headers = {"Authorization": f"Bearer {editor}"}
    assert client.get(f"/api/v1/meetings/{mid}", headers=viewer_headers).status_code == 200
    denied = client.patch(
        f"/api/v1/meetings/{mid}/metadata",
        headers=viewer_headers,
        json={"title": "不应成功"},
    )
    assert denied.status_code == 403
    allowed = client.patch(
        f"/api/v1/meetings/{mid}/metadata",
        headers=editor_headers,
        json={"title": "编辑者修改"},
    )
    assert allowed.status_code == 200


def test_share_password_throttle(client, auth_headers):
    from imeeting.settings import config_store

    mid, _ = _seed("分享限流")
    share = client.post(
        f"/api/v1/meetings/{mid}/shares",
        headers=auth_headers,
        json={"expires_hours": 1, "password": "secret"},
    ).json()
    config_store.set_overrides(
        {"share.max_failures": 2, "share.lock_seconds": 30}
    )
    for _ in range(2):
        assert (
            client.post(
                f"/api/v1/share/{share['token']}/open",
                json={"password": "bad"},
            ).status_code
            == 401
        )
    limited = client.post(
        f"/api/v1/share/{share['token']}/open",
        json={"password": "bad"},
    )
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0


def test_audio_chunk_sequence_is_monotonic(client):
    from imeeting.ws.stream import _load_audio_seq, _record_audio_seq

    mid, _ = _seed("音频序号")
    assert _load_audio_seq(mid) == 0
    _record_audio_seq(mid, 5)
    _record_audio_seq(mid, 3)
    assert _load_audio_seq(mid) == 5
