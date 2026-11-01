"""2026-11-01 全面审查所报缺陷的回归用例。

每项都对应 `docs/reviews/2026-11-01-comprehensive-project-review.md` 里的一条
P0/P1/P2 发现。约定：用例先在旧实现上失败，再作为常规回归长期保留。

覆盖：
- P0-1 配置密钥重启后必须仍是明文（``probe_secret_reload`` 的常驻版）
- P0-2 生产形态下 bootstrap 不得回传管理员口令（``probe_bootstrap`` 的常驻版）
- P1-2 反代可信链缺失时要有启动提示
- P2-1 /metrics 的令牌与环回限制
- P2-2 WAV 头写时即有效 + 启动修复扫描
- P2-3 分享访问令牌 2 小时过期，与声明的 expires_in 一致
- P2-4 非法请求体返回 422 而不是 500
"""

from __future__ import annotations

import json
import os
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from sqlmodel import Session, select

# ---------------------------------------------------------------------------
# P0-1：密钥类配置重启后仍是明文
# ---------------------------------------------------------------------------


def _persist_encrypted_secret(secret: str) -> dict[str, str]:
    """按 PUT /api/v1/config 的路径写入，并回读数据库里的原始行。"""
    from imeeting.db import persist_config
    from imeeting.models import AppConfigRow
    from imeeting.security import encrypt_value

    persist_config({"llm.api_key": encrypt_value(secret)})
    with Session(_engine()) as db:
        return {row.key: row.value for row in db.exec(select(AppConfigRow)).all()}


def _engine():
    from imeeting.db import engine

    return engine


def _reload_store(rows: dict[str, str]):
    """模拟进程重启：新建 ConfigStore，只从数据库装载。"""
    from imeeting.settings import ConfigStore

    store = ConfigStore()
    store.load_overrides(rows)
    return store


def test_encrypted_secret_survives_restart(admin_user, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    secret = "sk-live-key-should-survive-restart"
    rows = _persist_encrypted_secret(secret)

    stored = json.loads(rows["llm.api_key"])
    assert stored.startswith("gAAAAA"), "落库值应当是加密后的令牌"

    store = _reload_store(rows)
    assert store.get("llm.api_key") == secret, "重启后取到的必须是明文，不能是密文"


def test_secret_reload_skips_legacy_plaintext(admin_user):
    from imeeting.db import persist_config

    legacy = "sk-legacy-plaintext-key"
    persist_config({"llm.api_key": legacy})
    with Session(_engine()) as db:
        from imeeting.models import AppConfigRow

        rows = {row.key: row.value for row in db.exec(select(AppConfigRow)).all()}

    # 旧版本明文落库的行必须原样保留，升级不能把密钥清空
    assert _reload_store(rows).get("llm.api_key") == legacy


def test_undecryptable_secret_degrades_to_empty(admin_user):
    from imeeting.db import persist_config
    from imeeting.models import AppConfigRow
    from imeeting.security import encrypt_value

    persist_config({"llm.api_key": encrypt_value("sk-will-be-rotated")})
    with Session(_engine()) as db:
        rows = {row.key: row.value for row in db.exec(select(AppConfigRow)).all()}

    # 换掉 SECRET_KEY 等价于密文不可解：应回退为「未配置」，而不是把密文当密钥发出去
    from imeeting.settings import SERVER_CONFIG

    original = SERVER_CONFIG["secret_key"]
    try:
        SERVER_CONFIG["secret_key"] = "rotated-secret-key-0123456789abcdef"
        assert _reload_store(rows).get("llm.api_key") == ""
    finally:
        SERVER_CONFIG["secret_key"] = original


# ---------------------------------------------------------------------------
# P0-2：bootstrap 不得在生产形态回传口令
# ---------------------------------------------------------------------------


def test_bootstrap_hides_password_in_production(client, admin_user, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ALLOW_BOOTSTRAP_PASSWORD", raising=False)
    from imeeting.api.http.routes.auth import _reset_bootstrap_cache

    _reset_bootstrap_cache()
    body = client.get("/api/v1/auth/bootstrap").json()
    assert body["using_default_password"] is True
    assert body["default_password"] is None
    assert body["default_username"] == "admin"


def test_bootstrap_hides_password_when_auth_required(client, admin_user, monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    monkeypatch.delenv("ALLOW_BOOTSTRAP_PASSWORD", raising=False)
    from imeeting.api.http.routes.auth import _reset_bootstrap_cache

    _reset_bootstrap_cache()
    assert client.get("/api/v1/auth/bootstrap").json()["default_password"] is None


def test_bootstrap_password_can_be_opted_in(client, admin_user, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_BOOTSTRAP_PASSWORD", "1")
    from imeeting.api.http.routes.auth import _reset_bootstrap_cache

    _reset_bootstrap_cache()
    body = client.get("/api/v1/auth/bootstrap").json()
    assert body["default_password"] == "admin123"


# ---------------------------------------------------------------------------
# P1-2：反代可信链缺口要有启动提示
# ---------------------------------------------------------------------------


def test_production_warnings_flag_missing_proxy_trust():
    from imeeting.runtime_checks import production_config_warnings

    env = {"APP_ENV": "production"}
    warnings = production_config_warnings(env)
    assert any("FORWARDED_ALLOW_IPS" in item for item in warnings)
    assert any("METRICS_TOKEN" in item for item in warnings)

    trusted = {
        "APP_ENV": "production",
        "FORWARDED_ALLOW_IPS": "172.16.0.0/12",
        "METRICS_TOKEN": "m-token",
    }
    assert production_config_warnings(trusted) == []


def test_no_warnings_outside_production():
    from imeeting.runtime_checks import production_config_warnings

    assert production_config_warnings({"APP_ENV": "development"}) == []


# ---------------------------------------------------------------------------
# P2-1：/metrics 访问控制
# ---------------------------------------------------------------------------


def test_metrics_open_in_development(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "meeting_" in resp.text


def test_metrics_requires_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "s3cret-metrics")
    assert client.get("/metrics").status_code == 403
    assert client.get("/metrics?token=wrong").status_code == 403
    assert client.get("/metrics?token=s3cret-metrics").status_code == 200
    assert (
        client.get("/metrics", headers={"Authorization": "Bearer s3cret-metrics"}).status_code
        == 200
    )


def test_metrics_closed_to_remote_clients_in_production(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("METRICS_TOKEN", raising=False)
    # TestClient 的来源地址是 "testclient"，等价于「非环回」，应当被拒绝
    assert client.get("/metrics").status_code == 403


# ---------------------------------------------------------------------------
# P2-2：WAV 头写时即有效 + 启动修复
# ---------------------------------------------------------------------------


@pytest.fixture
def audio_enabled(monkeypatch):
    from imeeting.settings import config_store

    monkeypatch.setitem(config_store._db_overrides, "storage.audio_retention_days", 7)


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_wav_is_readable_before_close(audio_enabled, tmp_path, monkeypatch):
    """不调用 close，文件也必须是可播放的 WAV（崩溃场景）。"""
    from imeeting.engine import audio

    monkeypatch.setattr(audio, "audio_dir", lambda: tmp_path)
    sink = audio.AudioSink(meeting_id=1, session_id="s1")
    try:
        sink.push(np.zeros(16000, dtype=np.float32))
        assert _wait_for(lambda: sink.path is not None and Path(sink.path).stat().st_size > 44)
        with wave.open(str(sink.path), "rb") as handle:
            assert handle.getnframes() > 0
            assert handle.getframerate() == 16000
    finally:
        sink.close()


def test_header_matches_data_without_close(audio_enabled, tmp_path, monkeypatch):
    """不调用 close，头里声明的数据长度也必须等于真实长度。"""
    from imeeting.engine import audio

    monkeypatch.setattr(audio, "audio_dir", lambda: tmp_path)
    sink = audio.AudioSink(meeting_id=2, session_id="s2")
    try:
        sink.push(np.zeros(8000, dtype=np.float32))  # 16 KB PCM
        sink.push(np.zeros(8000, dtype=np.float32))
        assert _wait_for(lambda: sink.path is not None and Path(sink.path).stat().st_size > 44)
        size = Path(sink.path).stat().st_size
        with wave.open(str(sink.path), "rb") as handle:
            frames = handle.getnframes()
        assert frames == 16000
        assert size == audio._HEADER_BYTES + frames * 2
    finally:
        sink.close()


def test_repair_interrupted_wavs_fixes_zero_header(tmp_path):
    from imeeting.engine import audio

    broken = tmp_path / "9-deadbeef.wav"
    payload = b"\x01\x00" * 1600  # 3200 字节 = 1600 帧
    broken.write_bytes(b"\x00" * audio._HEADER_BYTES + payload)
    old = time.time() - 3600
    os.utime(broken, (old, old))

    repaired = audio.repair_interrupted_wavs(tmp_path)
    assert repaired == [broken]
    with wave.open(str(broken), "rb") as handle:
        assert handle.getnframes() == 1600

    # 修好之后不再重复处理
    assert audio.repair_interrupted_wavs(tmp_path) == []


def test_repair_skips_recent_files(tmp_path):
    """正在写的文件（mtime 很新）不能被误修。"""
    from imeeting.engine import audio

    fresh = tmp_path / "10-active.wav"
    fresh.write_bytes(b"\x00" * audio._HEADER_BYTES + b"\x00" * 64)
    assert audio.repair_interrupted_wavs(tmp_path) == []


# ---------------------------------------------------------------------------
# P2-3：分享访问令牌与声明的有效期一致
# ---------------------------------------------------------------------------


def test_share_access_token_lasts_two_hours(client, auth_headers, admin_user):
    from imeeting.api.http.routes import meeting_extras
    from imeeting.db import engine
    from imeeting.models import Meeting, User
    from imeeting.security import decode_access_token

    with Session(engine) as db:
        owner = db.exec(select(User).where(User.username == "admin")).one()
        meeting = Meeting(title="分享有效期", owner_id=owner.id, status="finished")
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        meeting_id = meeting.id

    created = client.post(
        f"/api/v1/meetings/{meeting_id}/shares",
        headers=auth_headers,
        json={"expires_hours": 24, "password": "", "allow_download": False},
    )
    assert created.status_code == 200, created.text
    token = created.json()["token"]

    opened = client.post(f"/api/v1/share/{token}/open", json={"password": ""})
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["expires_in"] == meeting_extras.SHARE_ACCESS_MINUTES * 60

    payload = decode_access_token(body["access_token"])
    assert payload is not None
    lifetime_minutes = (payload["exp"] - payload["iat"]) / 60
    assert lifetime_minutes == pytest.approx(meeting_extras.SHARE_ACCESS_MINUTES)
    assert meeting_extras.SHARE_ACCESS_MINUTES == 120


# ---------------------------------------------------------------------------
# P2-4：非法请求体 → 422，不是 500
# ---------------------------------------------------------------------------


def test_bulk_export_jobs_rejects_non_numeric_ids(client, auth_headers, admin_user):
    resp = client.post(
        "/api/v1/meetings/bulk-export-jobs",
        headers=auth_headers,
        json={"ids": ["x"], "format": "md"},
    )
    assert resp.status_code == 422, resp.text


def test_bulk_export_jobs_rejects_empty_ids(client, auth_headers, admin_user):
    resp = client.post(
        "/api/v1/meetings/bulk-export-jobs",
        headers=auth_headers,
        json={"ids": [], "format": "md"},
    )
    assert resp.status_code == 422, resp.text


def test_denoise_eval_rejects_bad_payload(client, auth_headers, admin_user):
    resp = client.post(
        "/api/v1/admin/denoise-eval",
        headers=auth_headers,
        json={"meeting_id": 0, "limit": 999, "strength": 5},
    )
    assert resp.status_code == 422, resp.text


def test_retention_rejects_out_of_range(client, auth_headers, admin_user):
    from imeeting.db import engine
    from imeeting.models import Meeting, User

    with Session(engine) as db:
        owner = db.exec(select(User).where(User.username == "admin")).one()
        meeting = Meeting(title="保留策略", owner_id=owner.id, status="finished")
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        meeting_id = meeting.id

    resp = client.patch(
        f"/api/v1/meetings/{meeting_id}/retention",
        headers=auth_headers,
        json={"retention_days": 99999},
    )
    assert resp.status_code == 422, resp.text

    ok = client.patch(
        f"/api/v1/meetings/{meeting_id}/retention",
        headers=auth_headers,
        json={"retention_days": 30},
    )
    assert ok.status_code == 200, ok.text
