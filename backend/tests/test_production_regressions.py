"""Production failures reproduced with isolated storage and no model downloads."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlmodel import Session

from imeeting import share_throttle
from imeeting.application import jobs
from imeeting.db import engine
from imeeting.models import Job, Meeting, utcnow


def test_restart_recovers_recent_running_job() -> None:
    queued = jobs.enqueue_job(kind="export_single")
    claimed = jobs._claim_job()
    assert claimed is not None and claimed.id == queued.id
    jobs._recover_stale_jobs()
    with Session(engine) as db:
        assert db.get(Job, queued.id).status == "pending"


def test_restart_does_not_retry_exhausted_job() -> None:
    queued = jobs.enqueue_job(kind="export_single", max_attempts=1)
    jobs._claim_job()
    jobs._recover_stale_jobs()
    with Session(engine) as db:
        assert db.get(Job, queued.id).status == "failed"


def test_queue_age_handles_sqlite_naive_datetimes() -> None:
    jobs.enqueue_job(kind="export_single", run_after=utcnow() - timedelta(seconds=60))
    metrics = jobs.job_metrics()
    assert metrics["oldest_pending_seconds"] >= 59
    jobs._claim_job()
    assert jobs.job_metrics()["oldest_running_seconds"] >= 0


def test_unknown_share_checks_do_not_allocate_state() -> None:
    share_throttle._failures.clear()
    for number in range(200):
        assert share_throttle.check_allowed(f"invalid-{number}", "test")[0]
    assert not share_throttle._failures


def test_share_lock_expires_at_advertised_time(monkeypatch) -> None:
    share_throttle._failures.clear()
    clock = [1000.0]
    monkeypatch.setattr(share_throttle.time, "time", lambda: clock[0])
    monkeypatch.setattr(share_throttle, "get_config", lambda key: {
        "share.max_failures": 2, "share.lock_seconds": 60,
    }.get(key))
    share_throttle.record_failure("token", "ip")
    share_throttle.record_failure("token", "ip")
    assert share_throttle.check_allowed("token", "ip")[0] is False
    clock[0] += 61
    assert share_throttle.check_allowed("token", "ip")[0] is True


@pytest.mark.parametrize("value", [None, [], "text", 1, True])
def test_non_object_control_does_not_abort_recording(client, echo_mode, value) -> None:
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({"type": "start"})
        assert ws.receive_json()["type"] == "ready"
        ws.send_text(json.dumps(value))
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["type"] == "closed"


@pytest.fixture
def echo_mode(monkeypatch) -> None:
    monkeypatch.setenv("ASR_MODE", "echo")


def test_deleted_meeting_revokes_share_audio(client, auth_headers, tmp_path) -> None:
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"audio-fixture")
    with Session(engine) as db:
        meeting = Meeting(owner_id=1, status="finished", audio_path=str(audio))
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        mid = meeting.id
    share = client.post(f"/api/v1/meetings/{mid}/shares", headers=auth_headers,
                        json={"expires_hours": 24, "allow_download": True}).json()
    url = f"/api/v1/share/{share['token']}"
    opened = client.post(f"{url}/open", json={"password": ""})
    assert opened.status_code == 200, opened.text
    access = {"Authorization": f"Bearer {opened.json()['access_token']}"}
    assert client.get(f"{url}/audio", headers=access).status_code == 200
    with Session(engine) as db:
        meeting = db.get(Meeting, mid)
        meeting.deleted_at = utcnow()
        db.add(meeting)
        db.commit()
    assert client.get(f"{url}/audio", headers=access).status_code == 404
    assert client.post(f"{url}/open", json={"password": ""}).status_code == 404


def test_claim_is_atomic_across_threads() -> None:
    expected = {jobs.enqueue_job(kind="export_single").id for _ in range(8)}
    with ThreadPoolExecutor(max_workers=8) as executor:
        claimed = list(executor.map(lambda _: jobs._claim_job(), range(8)))
    assert {job.id for job in claimed if job is not None} == expected


def test_share_failure_state_is_bounded_and_expires(monkeypatch) -> None:
    share_throttle._failures.clear()
    monkeypatch.setattr(share_throttle, "_MAX_ENTRIES", 4)
    clock = [1000.0]
    monkeypatch.setattr(share_throttle.time, "time", lambda: clock[0])
    for number in range(20):
        share_throttle.record_failure(str(number), "ip")
    assert len(share_throttle._failures) == 4
    clock[0] += 3600
    assert share_throttle.check_allowed("unknown", "ip")[0]
    assert not share_throttle._failures


@pytest.mark.parametrize("missing", ["exp", "iat", "sub"])
def test_signed_token_missing_required_claim_is_rejected(missing) -> None:
    import jwt

    from imeeting import security

    token = security.create_access_token("1", "user")
    claims = security.decode_access_token(token)
    del claims[missing]
    incomplete = jwt.encode(claims, security._secret(), algorithm=security.ALGORITHM)
    assert security.decode_access_token(incomplete) is None


def test_changed_password_revokes_previous_http_and_ws_credentials(
    client, auth_headers, monkeypatch,
) -> None:
    changed = client.post("/api/v1/auth/password", headers=auth_headers,
                          json={"current_password": "admin123", "new_password": "rotated-password-123"})
    assert changed.status_code == 200, changed.text
    assert client.get("/api/v1/auth/me", headers=auth_headers).status_code == 401
    replacement = changed.json()["access_token"]
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {replacement}"}).status_code == 200
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    monkeypatch.setenv("ASR_MODE", "echo")
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({"type": "start", "token": auth_headers["Authorization"].split()[1]})
        assert ws.receive_json()["code"] == "UNAUTHORIZED"
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({"type": "start", "token": replacement})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["type"] == "closed"


@pytest.mark.parametrize("offline_error", [None, "load failed"])
def test_models_not_ready_reject_probe_and_new_stream(client, monkeypatch, offline_error) -> None:
    from imeeting.engine import pool

    monkeypatch.setenv("ASR_MODE", "normal")
    monkeypatch.setattr(pool, "get_status", lambda: {
        name: {"name": name, "loaded": loaded, "loading": not loaded,
               "error": error, "load_seconds": 0, "device": "cpu"}
        for name, loaded, error in [("streaming", True, None), ("offline", False, offline_error)]
    })
    monkeypatch.setattr(pool, "is_ready", lambda name: name == "streaming")
    expected = "degraded" if offline_error else "loading"
    assert client.get("/api/v1/health").json()["status"] == expected
    assert client.get("/api/v1/ready").status_code == 503
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({"type": "start"})
        assert ws.receive_json()["code"] == "MODELS_NOT_READY"


def test_idle_ws_and_handshake_have_deadlines(client, echo_mode, monkeypatch) -> None:
    from imeeting.api.ws import stream

    monkeypatch.setattr(stream, "_HANDSHAKE_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(stream, "_IDLE_TIMEOUT_SECONDS", 0.02)
    with client.websocket_connect("/ws/stream") as ws:
        assert ws.receive_json()["code"] == "START_TIMEOUT"
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({"type": "start"})
        mid = ws.receive_json()["meeting_id"]
        assert ws.receive_json()["code"] == "SESSION_TIMEOUT"
        assert ws.receive_json()["type"] == "closed"
    with Session(engine) as db:
        assert db.get(Meeting, mid).status == "abandoned"


def test_session_audio_duration_limit_is_enforced(client, echo_mode) -> None:
    from imeeting.settings import config_store

    # Use a short test-only limit; production configuration uses whole minutes.
    config_store._db_overrides["session.max_duration_minutes"] = 0.01
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({"type": "start"})
        mid = ws.receive_json()["meeting_id"]
        ws.send_bytes(b"\x00" * 64000)
        assert ws.receive_json()["code"] == "SESSION_LIMIT"
        assert ws.receive_json()["type"] == "closed"
    with Session(engine) as db:
        assert db.get(Meeting, mid).status == "abandoned"


def test_auth_version_migration_preserves_legacy_users(monkeypatch) -> None:
    from sqlalchemy import inspect, text
    from sqlmodel import SQLModel, create_engine

    from imeeting import db as database

    legacy = create_engine("sqlite://")
    with legacy.begin() as conn:
        conn.execute(text("CREATE TABLE user (id INTEGER PRIMARY KEY, username TEXT, role TEXT, must_change_password BOOLEAN)"))
        conn.execute(text("INSERT INTO user VALUES (7, 'legacy', 'user', 0)"))
    SQLModel.metadata.create_all(legacy)
    monkeypatch.setattr(database, "engine", legacy)
    database._migrate_schema()
    database._migrate_schema()
    assert "auth_version" in {column["name"] for column in inspect(legacy).get_columns("user")}
    with legacy.connect() as conn:
        assert conn.execute(text("SELECT username, auth_version FROM user WHERE id=7")).one() == ("legacy", 0)
    legacy.dispose()


def test_ws_connections_do_not_leak_audio_writer_threads(client, echo_mode) -> None:
    def writers():
        return {thread.ident for thread in threading.enumerate()
                if thread.name.startswith("audio-writer-")}

    baseline = writers()
    for _ in range(3):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_json({"type": "start"})
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "stop"})
            assert ws.receive_json()["type"] == "closed"
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_json({"type": "invalid"})
            assert ws.receive_json()["code"] == "EXPECTED_START"
    assert writers() == baseline
