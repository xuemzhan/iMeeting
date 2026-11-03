"""不变量守门测试（IM-PLN-004 §8）。

计划 §8 要求：重构这些区域时，下列断言必须始终成立。这里把"必须始终成立的断言"
落成 5 条可执行用例，任何一条失败都说明重构破坏了安全/一致性前提。

与既有测试的分工：功能行为由各自的 test_*_api 覆盖；本文件只钉**不变量**，
所以用例都很短，断言的是"绝不能发生的事"。
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import Session, select


@pytest.fixture
def echo_mode(monkeypatch):
    """同 test_ws_protocol 的口径：不加载模型，只走链路。"""
    monkeypatch.setenv("ASR_MODE", "echo")
    from imeeting.settings import config_store

    config_store._db_overrides = {}
    yield
    config_store._db_overrides = {}


def test_config_endpoint_never_returns_plaintext_secrets(client, auth_headers):
    """密钥类配置只能以 ******** 出库（IM-PLN-004 §8 第 1 条）。"""
    from imeeting.settings import CONFIG_SCHEMA

    secret_keys = {f.key for f in CONFIG_SCHEMA if f.secret}
    assert secret_keys, "schema 里应至少有一个 secret 字段"

    # 先写入一个 secret，确认读回来仍是掩码
    put = client.put(
        "/api/v1/config",
        headers=auth_headers,
        json={"values": {"llm.api_key": "sk-this-must-never-leak"}},
    )
    assert put.status_code == 200, put.text

    body = client.get("/api/v1/config", headers=auth_headers).json()
    values = body.get("values", body)
    for key in secret_keys:
        if key in values:
            assert values[key] in ("", "********"), (
                f"{key} 明文出库了：{values[key]!r}"
            )
    assert "sk-this-must-never-leak" not in json.dumps(body), "接口响应里出现了密钥明文"


def test_undecryptable_secret_degrades_to_empty_not_ciphertext():
    """密文解不开时返回空串，绝不把密文当密钥（IM-PLN-004 §8 第 2 条）。"""
    from imeeting.settings import _decode_secret

    # 长得像 Fernet 密文但无法解密（前缀对、内容是坏的）
    bogus = "gAAAAA" + "x" * 40
    assert _decode_secret("llm.api_key", bogus) == "", (
        "解密失败必须降级为空串；把密文当密钥会让供应商一直 401，且后台只显示掩码"
    )
    # 明文（旧库）与空值原样返回，不因升级丢密钥
    assert _decode_secret("llm.api_key", "plain-old-key") == "plain-old-key"
    assert _decode_secret("llm.api_key", "") == ""


def test_audit_chain_stays_linked_under_repeated_writes(client, auth_headers, admin_user):
    """多次写入后审计链仍然完整（IM-PLN-004 §8 第 3 条）。"""
    from sqlalchemy import text

    from imeeting.audit import verify_audit_chain
    from imeeting.db import engine

    # 触发若干条审计记录（配置更新会写审计）
    for i in range(3):
        resp = client.put(
            "/api/v1/config",
            headers=auth_headers,
            json={"values": {"export.company_name": f"公司-{i}"}},
        )
        assert resp.status_code == 200, resp.text

    with engine.connect() as conn:
        result = verify_audit_chain(conn)
        rows = conn.execute(text("SELECT count(*) FROM audit_log")).scalar()
    assert rows and rows >= 3, f"审计记录过少：{rows}"
    assert result["valid"] is True, f"审计链断裂：{result}"


def test_share_token_is_stored_only_as_digest(client, auth_headers, admin_user):
    """分享令牌只以摘要落库（IM-PLN-004 §8 第 4 条）。"""
    from imeeting.db import engine
    from imeeting.models import Meeting, ShareLink
    from imeeting.security import hash_share_token

    with Session(engine) as db:
        meeting = Meeting(title="分享摘要", owner_id=admin_user.id, status="finished")
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        mid = meeting.id

    created = client.post(
        f"/api/v1/meetings/{mid}/shares",
        headers=auth_headers,
        json={"expires_in_hours": 1, "password": ""},
    )
    assert created.status_code == 200, created.text
    token = created.json()["token"]
    assert token

    with Session(engine) as db:
        row = db.exec(select(ShareLink).where(ShareLink.meeting_id == mid)).one()
        stored = (row.token or "")
    assert token not in stored, "库里出现了分享令牌明文"
    assert stored == hash_share_token(token), "落库值应为令牌摘要"


def test_every_session_end_leaves_no_recording_meeting(client, admin_user, echo_mode):
    """任何结束路径都不能把会议留在 recording（IM-PLN-004 §8 第 6 条）。"""
    from imeeting.db import engine
    from imeeting.models import Meeting

    mid = None
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_text(json.dumps({"type": "start", "title": "状态收口"}))
        mid = ws.receive_json()["meeting_id"]
        # 不发 stop，直接硬断
    assert mid is not None

    with Session(engine) as db:
        meeting = db.get(Meeting, mid)
    assert meeting is not None
    assert meeting.status in {"finished", "abandoned", "failed"}, meeting.status
    assert meeting.finished_at is not None, "四种状态都必须写 finished_at"
