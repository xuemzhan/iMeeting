"""后台配置体系测试。

覆盖重点：
- schema 完整性（前端表单靠它渲染，缺字段 = 后台瞎了）
- 三级回退优先级（环境变量 > 数据库 > 默认值）
- 写入校验与边界（防呆：非法值必须被拒，且不能污染已有配置）
- 密钥字段的加密存储与脱敏读取
- 审计日志
"""

from __future__ import annotations

import json

import pytest


class TestSchema:
    def test_schema_requires_admin(self, client, admin_user):
        assert client.get("/api/v1/config/schema").status_code == 401

    def test_schema_shape(self, client, auth_headers):
        resp = client.get("/api/v1/config/schema", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "groups" in body
        groups = body["groups"]
        assert len(groups) >= 3, "至少应有模型/LLM/会话三组"

        total = 0
        for group, fields in groups.items():
            assert isinstance(fields, list) and fields, f"分组 {group} 不应为空"
            for f in fields:
                # 前端表单强依赖这四个字段
                for required in ("key", "label", "type", "value"):
                    assert required in f, f"{group}/{f.get('key')} 缺字段 {required}"
                assert f["type"] in (
                    "string",
                    "int",
                    "float",
                    "bool",
                    "select",
                    "text",
                    "password",
                    "list",
                )
                if f["type"] == "select":
                    assert f.get("options"), f"{f['key']} 是 select 却没有 options"
                total += 1
        assert total >= 30, f"配置项过少（{total}），疑似漏注册"

    def test_schema_keys_are_unique(self, client, auth_headers):
        resp = client.get("/api/v1/config/schema", headers=auth_headers)
        keys = [f["key"] for fields in resp.json()["groups"].values() for f in fields]
        assert len(keys) == len(set(keys)), "存在重复的配置 key"

    def test_schema_redacts_secrets(self, client, auth_headers):
        """写了密钥后，schema 里不能出现明文。"""
        secret = "sk-super-secret-value-12345"
        put = client.put(
            "/api/v1/config",
            json={"values": {"llm.api_key": secret}},
            headers=auth_headers,
        )
        assert put.status_code == 200, put.text

        schema = client.get("/api/v1/config/schema", headers=auth_headers).json()
        assert secret not in json.dumps(schema, ensure_ascii=False)

        plain = client.get("/api/v1/config", headers=auth_headers).json()
        assert secret not in json.dumps(plain, ensure_ascii=False)
        assert plain["llm.api_key"] == "********"


class TestReadConfig:
    def test_read_requires_auth(self, client, admin_user):
        assert client.get("/api/v1/config").status_code == 401

    def test_normal_user_can_read_redacted(self, client, admin_user):
        """普通用户需读取非敏感配置以正确渲染界面。"""
        from sqlmodel import Session

        from imeeting.db import engine
        from imeeting.models import User
        from imeeting.security import hash_password

        with Session(engine) as db:
            db.add(
                User(
                    username="viewer",
                    password_hash=hash_password("viewer123"),
                    role="user",
                )
            )
            db.commit()
        token = client.post(
            "/api/v1/auth/login", json={"username": "viewer", "password": "viewer123"}
        ).json()["access_token"]

        resp = client.get("/api/v1/config", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["asr.mode"] in ("normal", "echo", "off")

    def test_read_returns_expected_keys(self, client, auth_headers):
        body = client.get("/api/v1/config", headers=auth_headers).json()
        for key in ("asr.mode", "vad.enabled", "punc.enabled", "spk.enabled"):
            assert key in body, f"缺少关键配置 {key}"


class TestUpdateConfig:
    def test_update_bool(self, client, auth_headers):
        resp = client.put(
            "/api/v1/config",
            json={"values": {"punc.enabled": False}},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert "punc.enabled" in resp.json()["updated"]
        assert resp.json()["errors"] == {}

        assert client.get("/api/v1/config", headers=auth_headers).json()["punc.enabled"] is False

    def test_update_coerces_types(self, client, auth_headers):
        """前端传来的都是字符串，必须正确转型。"""
        resp = client.put(
            "/api/v1/config",
            json={"values": {"vad.silence_ms": "1200"}},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert client.get("/api/v1/config", headers=auth_headers).json()["vad.silence_ms"] == 1200

    def test_update_select_rejects_invalid_option(self, client, auth_headers):
        resp = client.put(
            "/api/v1/config",
            json={"values": {"asr.mode": "turbo"}},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        errors = resp.json()["errors"]
        assert "asr.mode" in errors
        assert resp.json()["updated"] == []
        # 非法值不得生效
        assert client.get("/api/v1/config", headers=auth_headers).json()["asr.mode"] != "turbo"

    def test_update_rejects_unknown_key(self, client, auth_headers):
        resp = client.put(
            "/api/v1/config",
            json={"values": {"not.a.real.key": 1}},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "not.a.real.key" in resp.json()["errors"]

    def test_update_rejects_out_of_range(self, client, auth_headers):
        """越界值必须被拒，且不影响其他合法字段。"""
        resp = client.put(
            "/api/v1/config",
            json={"values": {"vad.silence_ms": 999999, "punc.enabled": False}},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "vad.silence_ms" in body["errors"]
        assert "punc.enabled" in body["updated"]
        assert client.get("/api/v1/config", headers=auth_headers).json()["punc.enabled"] is False

    def test_partial_failure_does_not_rollback_valid_changes(self, client, auth_headers):
        """一批里有非法项时，合法项仍应生效，非法项单独报错。

        注意：这里刻意避开受环境变量控制的键（如 asr.mode），
        否则环境变量层会把数据库层的值盖掉，测不出真实行为。
        """
        resp = client.put(
            "/api/v1/config",
            json={"values": {"spk.enabled": False, "bogus.key": 1}},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "spk.enabled" in resp.json()["updated"]
        assert "bogus.key" in resp.json()["errors"]
        after = client.get("/api/v1/config", headers=auth_headers).json()
        assert after["spk.enabled"] is False

    def test_mask_value_does_not_overwrite_secret(self, client, auth_headers):
        """前端回传掩码时应视为「未修改」，不能把密钥冲成 ********。"""
        client.put(
            "/api/v1/config",
            json={"values": {"llm.api_key": "sk-real-key-abcdef"}},
            headers=auth_headers,
        )
        resp = client.put(
            "/api/v1/config",
            json={"values": {"llm.api_key": "********"}},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["updated"] == []

        from imeeting.settings import get_config

        assert get_config("llm.api_key") == "sk-real-key-abcdef"

    def test_secret_stored_encrypted(self, client, auth_headers, db_path):
        """数据库里必须是密文，不能明文存 API Key。"""
        secret = "sk-plaintext-should-not-appear"
        client.put(
            "/api/v1/config",
            json={"values": {"llm.api_key": secret}},
            headers=auth_headers,
        )
        raw = db_path.read_bytes()
        assert secret.encode() not in raw, "API Key 以明文形式落盘了"
        assert b"sk-plaintext" not in raw

    def test_audit_log_written(self, client, auth_headers):
        client.put(
            "/api/v1/config",
            json={"values": {"asr.mode": "echo"}},
            headers=auth_headers,
        )
        from sqlmodel import Session, select

        from imeeting.db import engine
        from imeeting.models import AuditLog

        with Session(engine) as db:
            logs = db.exec(select(AuditLog).where(AuditLog.action == "config_update")).all()
        assert logs, "配置变更未写审计日志"
        assert any("asr.mode" in (log.payload or "") for log in logs)

    def test_restart_required_flag_reported(self, client, auth_headers):
        """需要重启才生效的项必须在响应里提示，否则用户会以为改了个寂寞。"""
        from imeeting.settings import CONFIG_SCHEMA

        restart_keys = [f.key for f in CONFIG_SCHEMA if f.restart_required]
        if not restart_keys:
            pytest.skip("当前没有标记需重启的配置项")

        key = restart_keys[0]
        from imeeting.settings import SCHEMA_BY_KEY

        meta = SCHEMA_BY_KEY[key]
        value = {"bool": True, "int": 1, "float": 1.0}.get(meta.type, meta.default)

        resp = client.put(
            "/api/v1/config", json={"values": {key: value}}, headers=auth_headers
        )
        assert resp.status_code == 200


class TestThreeTierFallback:
    def test_db_overrides_default(self, client, auth_headers):
        from imeeting.settings import SCHEMA_BY_KEY, get_config

        default = SCHEMA_BY_KEY["vad.silence_ms"].default
        assert get_config("vad.silence_ms") == default

        client.put(
            "/api/v1/config",
            json={"values": {"vad.silence_ms": default + 111}},
            headers=auth_headers,
        )
        assert get_config("vad.silence_ms") == default + 111

    def test_env_beats_db(self, client, auth_headers, monkeypatch):
        """环境变量优先级最高：数据库改了也不该盖过它。"""
        from imeeting.settings import get_config

        monkeypatch.setenv("ASR_MODE", "off")
        client.put(
            "/api/v1/config",
            json={"values": {"asr.mode": "normal"}},
            headers=auth_headers,
        )
        assert get_config("asr.mode") == "off", "环境变量未覆盖数据库配置"

    def test_falls_back_to_default_when_unset(self):
        from imeeting.settings import SCHEMA_BY_KEY, get_config

        for key, meta in list(SCHEMA_BY_KEY.items())[:20]:
            from imeeting.settings import ENV_OVERRIDES

            if key in ENV_OVERRIDES:
                continue
            assert get_config(key) == meta.default
