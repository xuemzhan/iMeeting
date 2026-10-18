"""会议与转写片段接口测试：CRUD、权限隔离、导出、纪要。

覆盖重点：
- **权限隔离**：A 用户绝不能看到/改/删 B 用户的会议（生产级红线）
- **片段排序与 partial/final 覆盖语义**
- **导出格式正确性**（SRT 逗号、VTT 头、时间码换算）
- **纪要**：无转写内容时应报错而不是产出空纪要
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import Session, select

from imeeting.db import engine
from imeeting.models import Meeting, Segment

# ---------------------------------------------------------------------------
# 夹具：造数据
# ---------------------------------------------------------------------------


def _make_meeting(owner_id: int, title: str = "测试会议", **kw) -> int:
    with Session(engine) as db:
        m = Meeting(title=title, owner_id=owner_id, **kw)
        db.add(m)
        db.commit()
        db.refresh(m)
        return m.id


def _make_segments(meeting_id: int, items: list[dict]) -> None:
    with Session(engine) as db:
        for i, item in enumerate(items):
            db.add(
                Segment(
                    meeting_id=meeting_id,
                    segment_id=item.get("segment_id", f"s-{meeting_id}-{i}"),
                    text=item.get("text", ""),
                    is_final=item.get("is_final", True),
                    start_ms=item.get("start_ms", i * 2000),
                    end_ms=item.get("end_ms", i * 2000 + 1800),
                    speaker=item.get("speaker"),
                    seq=item.get("seq", i),
                )
            )
        db.commit()


def _set_audio_path(meeting_id: int, path: str) -> None:
    """直接给会议挂上音频路径（绕过真实录音，聚焦接口行为）。"""
    with Session(engine) as db:
        m = db.get(Meeting, meeting_id)
        assert m is not None
        m.audio_path = path
        db.add(m)
        db.commit()


def _other_user_token(client, username: str = "eve") -> str:
    from imeeting.models import User
    from imeeting.security import hash_password

    with Session(engine) as db:
        db.add(User(username=username, password_hash=hash_password("eve12345"), role="user"))
        db.commit()
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": "eve12345"}
    ).json()["access_token"]


# ---------------------------------------------------------------------------
# 列表与详情
# ---------------------------------------------------------------------------


class TestMeetingCrud:
    def test_requires_auth_when_enforced(self, client, admin_user, monkeypatch):
        """开启鉴权（REQUIRE_AUTH=1）时，未登录必须 401。"""
        monkeypatch.setenv("REQUIRE_AUTH", "1")
        assert client.get("/api/v1/meetings").status_code == 401

    def test_anonymous_allowed_in_local_mode(self, client, admin_user, monkeypatch):
        """默认 REQUIRE_AUTH=0：本地不登录也能用。

        与 WS 的匿名策略保持一致——否则会出现「转写能用、历史却报
        未提供访问令牌」的前后矛盾（实际踩过）。
        """
        monkeypatch.setenv("REQUIRE_AUTH", "0")
        assert client.get("/api/v1/meetings").status_code == 200

    def test_list_empty(self, client, auth_headers):
        resp = client.get("/api/v1/meetings", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_own_meetings(self, client, auth_headers, admin_user):
        _make_meeting(admin_user.id, "周一站会")
        _make_meeting(admin_user.id, "需求评审")
        resp = client.get("/api/v1/meetings", headers=auth_headers)
        assert resp.status_code == 200
        titles = [m["title"] for m in resp.json()]
        assert set(titles) == {"周一站会", "需求评审"}

    def test_detail_includes_segments(self, client, auth_headers, admin_user):
        mid = _make_meeting(admin_user.id)
        _make_segments(mid, [{"text": "第一句"}, {"text": "第二句"}])
        resp = client.get(f"/api/v1/meetings/{mid}", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["segments"]) == 2
        assert body["segments"][0]["text"] == "第一句"

    def test_detail_segments_sorted_by_timeline(self, client, auth_headers, admin_user):
        """片段必须按全局时间轴返回，否则多会话会议字幕会错位。

        同一会话内 start_ms 与 seq 一致；重连后 seq 会从 0 重来，
        因此排序主键必须是 start_ms（重连时已累加偏移），seq 只做兜底。
        """
        mid = _make_meeting(admin_user.id)
        _make_segments(
            mid,
            [
                {"text": "第三句", "seq": 2, "start_ms": 4000},
                {"text": "第一句", "seq": 0, "start_ms": 0},
                {"text": "第二句", "seq": 1, "start_ms": 2000},
            ],
        )
        body = client.get(f"/api/v1/meetings/{mid}", headers=auth_headers).json()
        assert [s["text"] for s in body["segments"]] == ["第一句", "第二句", "第三句"]

    def test_missing_meeting_404(self, client, auth_headers):
        assert client.get("/api/v1/meetings/999999", headers=auth_headers).status_code == 404


class TestMeetingPermissionIsolation:
    """权限隔离是生产级红线，必须逐条验证。"""

    def test_other_user_cannot_read(self, client, admin_user, auth_headers):
        mid = _make_meeting(admin_user.id, "机密会议")
        token = _other_user_token(client)
        resp = client.get(f"/api/v1/meetings/{mid}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (403, 404), "普通用户读到了别人的会议"

    def test_other_user_cannot_delete(self, client, admin_user, auth_headers):
        mid = _make_meeting(admin_user.id)
        token = _other_user_token(client)
        resp = client.delete(
            f"/api/v1/meetings/{mid}", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code in (403, 404)
        assert client.get(f"/api/v1/meetings/{mid}", headers=auth_headers).status_code == 200

    def test_other_user_cannot_rename(self, client, admin_user, auth_headers):
        mid = _make_meeting(admin_user.id, "原名")
        token = _other_user_token(client)
        resp = client.patch(
            f"/api/v1/meetings/{mid}",
            json={"title": "被改名"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (403, 404)
        assert client.get(f"/api/v1/meetings/{mid}", headers=auth_headers).json()["title"] == "原名"

    def test_other_user_cannot_export(self, client, admin_user, auth_headers):
        mid = _make_meeting(admin_user.id)
        _make_segments(mid, [{"text": "secret content"}])
        token = _other_user_token(client)
        resp = client.get(
            f"/api/v1/meetings/{mid}/export?format=txt",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (403, 404)
        assert "secret content" not in resp.text

    def test_list_does_not_leak_others_meetings(self, client, admin_user, auth_headers):
        _make_meeting(admin_user.id, "我的会议")
        token = _other_user_token(client)
        body = client.get(
            "/api/v1/meetings", headers={"Authorization": f"Bearer {token}"}
        ).json()
        assert body == [], "列表泄漏了其他用户的会议"

    def test_other_user_cannot_download_audio(self, client, admin_user, auth_headers, tmp_path, monkeypatch):
        """音频是原始录音，权限必须与转写内容同等严格。"""
        from imeeting.engine import audio as audio_mod

        d = tmp_path / "audio"
        d.mkdir()
        monkeypatch.setattr(audio_mod, "audio_dir", lambda: d)

        mid = _make_meeting(admin_user.id)
        wav = d / f"{mid}.wav"
        wav.write_bytes(b"RIFF....WAVE")
        _set_audio_path(mid, str(wav))

        token = _other_user_token(client)
        resp = client.get(
            f"/api/v1/meetings/{mid}/audio",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (403, 404)


class TestAudioPlayback:
    def test_has_audio_flag_reflects_file_presence(self, client, auth_headers, admin_user):
        plain = _make_meeting(admin_user.id, "无音频")
        body = client.get(f"/api/v1/meetings/{plain}", headers=auth_headers).json()
        assert body["has_audio"] is False

    def test_audio_404_when_not_present(self, client, auth_headers, admin_user):
        mid = _make_meeting(admin_user.id)
        resp = client.get(f"/api/v1/meetings/{mid}/audio", headers=auth_headers)
        assert resp.status_code == 404

    def test_audio_served_with_correct_media_type(
        self, client, auth_headers, admin_user, tmp_path, monkeypatch
    ):
        """验证能真正取到字节，且 Content-Type 是 wav（否则播放器不认）。"""
        from imeeting.engine import audio as audio_mod
        from imeeting.engine.audio import AudioSink

        d = tmp_path / "audio"
        d.mkdir()
        monkeypatch.setattr(audio_mod, "audio_dir", lambda: d)

        mid = _make_meeting(admin_user.id)

        # 用真实写入路径生成合法 wav
        import numpy as np

        from imeeting.settings import config_store

        config_store._db_overrides = {"storage.audio_retention_days": 7}
        sink = AudioSink(mid)
        sink.push(np.zeros(1600, dtype=np.float32))
        path = sink.close()
        assert path is not None

        _set_audio_path(mid, str(path))
        config_store._db_overrides = {}

        resp = client.get(f"/api/v1/meetings/{mid}/audio", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("audio/")
        assert resp.content[:4] == b"RIFF"
        assert len(resp.content) > 44

    def test_meeting_detail_shows_has_audio(
        self, client, auth_headers, admin_user, tmp_path, monkeypatch
    ):
        from imeeting.engine import audio as audio_mod

        d = tmp_path / "audio"
        d.mkdir()
        monkeypatch.setattr(audio_mod, "audio_dir", lambda: d)

        mid = _make_meeting(admin_user.id)
        wav = d / f"{mid}.wav"
        wav.write_bytes(b"RIFF....WAVE")
        _set_audio_path(mid, str(wav))

        body = client.get(f"/api/v1/meetings/{mid}", headers=auth_headers).json()
        assert body["has_audio"] is True
        # 不能把服务端路径暴露给前端
        assert "audio_path" not in body

    def test_deleting_meeting_removes_audio(
        self, client, auth_headers, admin_user, tmp_path, monkeypatch
    ):
        """软删除保留音频；彻底删除时才清理磁盘文件。"""
        from imeeting.engine import audio as audio_mod

        d = tmp_path / "audio"
        d.mkdir()
        monkeypatch.setattr(audio_mod, "audio_dir", lambda: d)

        mid = _make_meeting(admin_user.id)
        wav = d / f"{mid}.wav"
        wav.write_bytes(b"RIFF....WAVE")
        _set_audio_path(mid, str(wav))

        assert client.delete(f"/api/v1/meetings/{mid}", headers=auth_headers).status_code == 204
        assert wav.exists(), "移入回收站阶段不应删除音频"
        assert (
            client.delete(f"/api/v1/meetings/{mid}/purge", headers=auth_headers).status_code
            == 204
        )
        assert not wav.exists(), "彻底删除后音频仍残留在磁盘上"


class TestRenameAndDelete:
    def test_rename(self, client, auth_headers, admin_user):
        mid = _make_meeting(admin_user.id, "旧名")
        resp = client.patch(
            f"/api/v1/meetings/{mid}", json={"title": "新名"}, headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "新名"

    def test_rename_empty_rejected(self, client, auth_headers, admin_user):
        mid = _make_meeting(admin_user.id, "旧名")
        resp = client.patch(f"/api/v1/meetings/{mid}", json={"title": ""}, headers=auth_headers)
        assert resp.status_code in (400, 422)

    def test_delete_cascades_segments(self, client, auth_headers, admin_user):
        """移入回收站保留片段；彻底删除时级联清理。"""
        mid = _make_meeting(admin_user.id)
        _make_segments(mid, [{"text": "a"}, {"text": "b"}])
        assert client.delete(f"/api/v1/meetings/{mid}", headers=auth_headers).status_code == 204

        with Session(engine) as db:
            meeting = db.get(Meeting, mid)
            assert meeting is not None and meeting.deleted_at is not None
            assert db.exec(select(Segment).where(Segment.meeting_id == mid)).all()

        assert (
            client.delete(f"/api/v1/meetings/{mid}/purge", headers=auth_headers).status_code
            == 204
        )
        with Session(engine) as db:
            assert db.get(Meeting, mid) is None
            assert db.exec(select(Segment).where(Segment.meeting_id == mid)).all() == []

    def test_delete_twice_second_is_404(self, client, auth_headers, admin_user):
        mid = _make_meeting(admin_user.id)
        assert client.delete(f"/api/v1/meetings/{mid}", headers=auth_headers).status_code == 204
        assert client.delete(f"/api/v1/meetings/{mid}", headers=auth_headers).status_code == 404


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


class TestExport:
    @pytest.fixture
    def meeting_with_content(self, admin_user) -> int:
        mid = _make_meeting(admin_user.id, "导出测试")
        _make_segments(
            mid,
            [
                {"text": "大家好。", "start_ms": 0, "end_ms": 1800, "seq": 0},
                {"text": "我们开始吧。", "start_ms": 2000, "end_ms": 4500, "seq": 1},
                {"text": "第三句话。", "start_ms": 3661000, "end_ms": 3664000, "seq": 2},
            ],
        )
        return mid

    def test_txt_export(self, client, auth_headers, meeting_with_content):
        resp = client.get(
            f"/api/v1/meetings/{meeting_with_content}/export?format=txt", headers=auth_headers
        )
        assert resp.status_code == 200
        text = resp.content.decode("utf-8")
        assert "大家好。" in text and "我们开始吧。" in text
        assert "第三句话。" in text

    def test_srt_format(self, client, auth_headers, meeting_with_content):
        resp = client.get(
            f"/api/v1/meetings/{meeting_with_content}/export?format=srt", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.content.decode("utf-8")
        # SRT 用逗号做毫秒分隔，且序号从 1 开始
        assert "00:00:00,000 --> 00:00:01,800" in body, body[:200]
        assert body.lstrip().startswith("1"), "SRT 首条序号应为 1"
        # 超过 1 小时要正确进位
        assert "01:01:01,000" in body, "长时长进位换算错误"

    def test_vtt_format(self, client, auth_headers, meeting_with_content):
        resp = client.get(
            f"/api/v1/meetings/{meeting_with_content}/export?format=vtt", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.content.decode("utf-8")
        assert body.lstrip().startswith("WEBVTT"), "VTT 缺少文件头"
        # VTT 用点号做毫秒分隔
        assert "00:00:00.000 --> 00:00:01.800" in body, body[:200]

    def test_md_export_has_structure(self, client, auth_headers, meeting_with_content):
        resp = client.get(
            f"/api/v1/meetings/{meeting_with_content}/export?format=md", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.content.decode("utf-8")
        assert body.strip().startswith("#"), "Markdown 应有标题"
        assert "导出测试" in body

    def test_json_export_is_valid(self, client, auth_headers, meeting_with_content):
        resp = client.get(
            f"/api/v1/meetings/{meeting_with_content}/export?format=json", headers=auth_headers
        )
        assert resp.status_code == 200
        data = json.loads(resp.content.decode("utf-8"))
        assert isinstance(data, (dict, list))
        assert "大家好。" in json.dumps(data, ensure_ascii=False)

    def test_unsupported_format_rejected(self, client, auth_headers, meeting_with_content):
        resp = client.get(
            f"/api/v1/meetings/{meeting_with_content}/export?format=xlsx", headers=auth_headers
        )
        assert resp.status_code == 400

    def test_export_sets_download_filename(self, client, auth_headers, meeting_with_content):
        resp = client.get(
            f"/api/v1/meetings/{meeting_with_content}/export?format=srt", headers=auth_headers
        )
        dispo = resp.headers.get("content-disposition", "")
        assert "attachment" in dispo.lower(), "导出应带 attachment 头，否则浏览器会直接打开"
        assert ".srt" in dispo

    def test_export_empty_meeting_does_not_crash(self, client, auth_headers, admin_user):
        mid = _make_meeting(admin_user.id, "空会议")
        for fmt in ("srt", "vtt", "md", "txt", "json"):
            resp = client.get(f"/api/v1/meetings/{mid}/export?format={fmt}", headers=auth_headers)
            assert resp.status_code == 200, f"{fmt} 导出空会议失败: {resp.text}"

    def test_export_escapes_html_unsafe_chars(self, client, auth_headers, admin_user):
        """转写文本可能含 <script>，导出不能破坏结构（SRT 至少不能崩溃）。"""
        mid = _make_meeting(admin_user.id)
        _make_segments(mid, [{"text": "<script>alert(1)</script> & <b>粗</b>"}])
        resp = client.get(f"/api/v1/meetings/{mid}/export?format=srt", headers=auth_headers)
        assert resp.status_code == 200
        assert "<script>" in resp.content.decode("utf-8")


# ---------------------------------------------------------------------------
# 纪要
# ---------------------------------------------------------------------------


class TestMinutes:
    def test_minutes_requires_body(self, client, auth_headers, admin_user):
        """生成纪要是 POST，必须带请求体（force 等参数）。"""
        mid = _make_meeting(admin_user.id, "空会议")
        resp = client.post(f"/api/v1/meetings/{mid}/minutes", headers=auth_headers)
        assert resp.status_code == 422

    def test_minutes_requires_content(self, client, auth_headers, admin_user):
        """空会议生成纪要应明确报错，而不是产出幻觉内容。"""
        mid = _make_meeting(admin_user.id, "空会议")
        resp = client.post(
            f"/api/v1/meetings/{mid}/minutes", json={}, headers=auth_headers
        )
        assert resp.status_code == 400, resp.text

    def test_get_minutes_none_yet(self, client, auth_headers, admin_user):
        mid = _make_meeting(admin_user.id)
        resp = client.get(f"/api/v1/meetings/{mid}/minutes", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() is None

    def test_minutes_requires_auth(self, client, admin_user, monkeypatch):
        monkeypatch.setenv("REQUIRE_AUTH", "1")
        mid = _make_meeting(admin_user.id)
        assert client.post(f"/api/v1/meetings/{mid}/minutes", json={}).status_code == 401

    def test_minutes_other_user_forbidden(self, client, admin_user, auth_headers):
        mid = _make_meeting(admin_user.id)
        _make_segments(mid, [{"text": "内容"}])
        token = _other_user_token(client)
        resp = client.post(
            f"/api/v1/meetings/{mid}/minutes",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code in (403, 404)

    def test_minutes_without_llm_configured_reports_error(self, client, auth_headers, admin_user):
        """LLM 没配好时应给明确的可读错误，而不是 500。"""
        mid = _make_meeting(admin_user.id)
        _make_segments(mid, [{"text": "我们讨论了发布计划"}])
        resp = client.post(
            f"/api/v1/meetings/{mid}/minutes", json={}, headers=auth_headers
        )
        # LLM 未配置 → 502（上游不可用）；配置了但调用失败同样不该是 500
        assert resp.status_code in (502, 200), resp.text


# ---------------------------------------------------------------------------
# 系统
# ---------------------------------------------------------------------------


class TestSystemEndpoints:
    def test_health_public(self, client, admin_user):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        # 模型语义随 asr.mode 变化：正常模式下 loading/ok；off/echo 下 disabled
        assert body["status"] in ("ok", "loading", "degraded", "disabled")
        assert "version" in body
        assert "active_sessions" in body

    def test_health_reports_disabled_when_models_not_needed(self, client, admin_user, monkeypatch):
        """off/echo 模式下不该永远停在 loading，否则监控会误判服务未就绪。"""
        monkeypatch.setenv("ASR_MODE", "echo")
        from imeeting.settings import config_store

        config_store._db_overrides = {}
        body = client.get("/api/v1/health").json()
        assert body["status"] == "disabled"

    def test_health_reflects_active_sessions(self, client, admin_user):
        assert client.get("/api/v1/health").json()["active_sessions"] == 0

    def test_models_status_public(self, client, admin_user):
        resp = client.get("/api/v1/models/status")
        assert resp.status_code == 200
        body = resp.json()
        for name in ("streaming", "offline"):
            assert name in body
            for key in ("loaded", "loading", "error", "device"):
                assert key in body[name]

    def test_root_serves_something(self, client, admin_user):
        resp = client.get("/")
        assert resp.status_code in (200, 404)
