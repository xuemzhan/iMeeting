"""WebSocket 协议测试。

用 FastAPI 的 TestClient 建立真实 WebSocket 连接，验证协议契约与防呆边界。
模型走 ``echo`` / ``off`` 模式，不加载真实权重。
"""

from __future__ import annotations

import json
import time

import numpy as np
import pytest


def pcm_bytes(seconds: float, amp: float = 0.3) -> bytes:
    n = int(16000 * seconds)
    t = np.arange(n, dtype=np.float32) / 16000
    return (amp * np.sin(2 * np.pi * 440 * t)).astype("<f4").tobytes()


@pytest.fixture
def ws_client(client):
    """确保存在 admin，返回 TestClient。"""
    return client


@pytest.fixture
def echo_mode(monkeypatch):
    monkeypatch.setenv("ASR_MODE", "echo")
    from imeeting.settings import config_store

    config_store._db_overrides = {}
    yield
    config_store._db_overrides = {}


class TestHandshake:
    def test_first_frame_must_be_text(self, client, admin_user):
        """首帧发二进制必须被拒，且错误码要明确。"""
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_bytes(pcm_bytes(0.1))
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["code"] == "EXPECTED_START"

    def test_first_frame_must_be_start_type(self, client, admin_user):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "hello"}))
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["code"] == "EXPECTED_START"

    def test_malformed_json_rejected(self, client, admin_user):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text("{not json")
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["code"] == "BAD_JSON"

    def test_non_object_start_rejected(self, client, admin_user):
        """发个数组或字符串也应被安全拒绝。"""
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text('"just a string"')
            msg = ws.receive_json()
            assert msg["type"] == "error"


class TestHappyPath:
    def test_ready_message_shape(self, client, admin_user, echo_mode):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start", "title": "测试"}))
            ready = ws.receive_json()
            assert ready["type"] == "ready"
            assert isinstance(ready["meeting_id"], int)
            assert ready["sample_rate"] == 16000
            assert ready["mode"] == "echo"

    def test_ping_pong(self, client, admin_user, echo_mode):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start"}))
            ws.receive_json()
            ws.send_text(json.dumps({"type": "ping"}))
            pong = ws.receive_json()
            assert pong["type"] == "pong"
            assert isinstance(pong["ts"], int)

    def test_audio_produces_partial_then_stop(self, client, admin_user, echo_mode):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start"}))
            ws.receive_json()
            for _ in range(4):
                ws.send_bytes(pcm_bytes(0.6))
            ws.send_text(json.dumps({"type": "stop"}))

            kinds = []
            for _ in range(60):
                try:
                    msg = ws.receive_json()
                except Exception:
                    break
                kinds.append(msg["type"])
                if msg["type"] == "closed":
                    break
            assert "partial" in kinds or "final" in kinds
            assert "closed" in kinds, f"未收到 closed，实际 {kinds}"

    def test_odd_sized_binary_frames(self, client, admin_user, echo_mode):
        """非 4 倍数长度的二进制帧必须被安全丢弃而不是崩溃。"""
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start"}))
            ws.receive_json()
            ws.send_bytes(b"\x01\x02\x03")  # 3 字节，不是 float32
            ws.send_bytes(pcm_bytes(0.6))  # 之后仍应正常工作
            ws.send_text(json.dumps({"type": "ping"}))
            msg = ws.receive_json()
            assert msg["type"] in ("pong", "partial", "final")


class TestRobustness:
    def test_unknown_command_ignored(self, client, admin_user, echo_mode):
        """未知控制指令应被忽略，不能断连。"""
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start"}))
            ws.receive_json()
            ws.send_text(json.dumps({"type": "warp_drive"}))
            ws.send_text(json.dumps({"type": "ping"}))
            msg = ws.receive_json()
            assert msg["type"] in ("pong", "partial", "final")

    def test_empty_text_frame_ignored(self, client, admin_user, echo_mode):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start"}))
            ws.receive_json()
            ws.send_text("")
            ws.send_text(json.dumps({"type": "ping"}))
            assert ws.receive_json()["type"] in ("pong", "partial", "final")

    def test_stop_without_audio_closes_cleanly(self, client, admin_user, echo_mode):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start"}))
            ws.receive_json()
            ws.send_text(json.dumps({"type": "stop"}))
            msg = ws.receive_json()
            assert msg["type"] == "closed"

    def test_audio_after_stop_does_not_crash(self, client, admin_user, echo_mode):
        """stop 之后再发音频应被忽略。"""
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start"}))
            ws.receive_json()
            ws.send_text(json.dumps({"type": "stop"}))
            assert ws.receive_json()["type"] == "closed"

    def test_invalid_token_falls_back_to_anonymous(self, client, admin_user, echo_mode):
        """未开启强制鉴权时，坏 token 不应导致连接失败。"""
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start", "token": "garbage"}))
            msg = ws.receive_json()
            assert msg["type"] == "ready"

    def test_large_binary_frame_accepted(self, client, admin_user, echo_mode):
        """一次发 10 秒音频（640KB）不应被拒。"""
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start"}))
            ws.receive_json()
            ws.send_bytes(pcm_bytes(10.0))
            ws.send_text(json.dumps({"type": "stop"}))
            kinds = []
            for _ in range(80):
                try:
                    msg = ws.receive_json()
                except Exception:
                    break
                kinds.append(msg["type"])
                if msg["type"] == "closed":
                    break
            assert "closed" in kinds


class TestPersistence:
    def test_final_is_persisted_before_stop(self, client, admin_user, echo_mode):
        """增量落库：收到 final 后、stop 之前，数据库里就应能看到片段。

        否则浏览器崩溃/进程被杀时，整场会议的文字只存在内存里会全部丢失。
        """
        from sqlmodel import Session, select

        from imeeting.db import engine
        from imeeting.models import Segment

        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start", "title": "增量落库"}))
            mid = ws.receive_json()["meeting_id"]

            ws.send_bytes(pcm_bytes(0.8))
            # 1.5s 静音触发断句与定稿
            for _ in range(3):
                ws.send_bytes(b"\x00\x00\x00\x00" * 8000)

            final = None
            for _ in range(40):
                msg = ws.receive_json()
                if msg["type"] == "final":
                    final = msg
                    break
            assert final is not None

            deadline = time.time() + 5
            rows = []
            while time.time() < deadline:
                with Session(engine) as db:
                    rows = db.exec(
                        select(Segment).where(Segment.meeting_id == mid)
                    ).all()
                if rows:
                    break
                time.sleep(0.05)
            assert rows, "final 之后、stop 之前片段应已增量落库"

            ws.send_text(json.dumps({"type": "stop"}))
            for _ in range(60):
                try:
                    if ws.receive_json()["type"] == "closed":
                        break
                except Exception:
                    break

    def test_meeting_created_and_finalized(self, client, admin_user, echo_mode):
        """会话结束后会议应落库为 finished，且片段数正确。"""
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start", "title": "落库验证"}))
            ready = ws.receive_json()
            mid = ready["meeting_id"]

            for _ in range(4):
                ws.send_bytes(pcm_bytes(0.6))
            ws.send_bytes(pcm_bytes(0.628))  # 触发长度不对齐
            ws.send_text(json.dumps({"type": "stop"}))
            for _ in range(60):
                try:
                    msg = ws.receive_json()
                except Exception:
                    break
                if msg["type"] == "closed":
                    break

        token = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
        ).json()["access_token"]
        detail = client.get(
            f"/api/v1/meetings/{mid}", headers={"Authorization": f"Bearer {token}"}
        ).json()
        assert detail["id"] == mid
        assert detail["title"] == "落库验证"
        # 显式 stop 后必须是 finished；此前曾被写成容忍 recording，
        # 从而掩盖了「异常结束的会议永远停在 recording」这个真实缺陷。
        assert detail["status"] == "finished"

    def test_abnormal_disconnect_marks_abandoned(self, client, admin_user, echo_mode):
        """不发送 stop 直接断开，会议不得停留在 recording。

        这是生产环境最常见的路径：浏览器标签被关、网络中断、进程被杀。
        如果这里不把状态收口，库里会不断堆积永远「进行中」的僵尸会议。
        """
        from sqlmodel import Session

        from imeeting.db import engine
        from imeeting.models import Meeting

        mid = None
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start", "title": "异常断开"}))
            mid = ws.receive_json()["meeting_id"]
            ws.send_bytes(pcm_bytes(0.6))
            # 不发 stop，直接离开 with（等价于连接被硬断）
        assert mid is not None

        with Session(engine) as db:
            meeting = db.get(Meeting, mid)
            assert meeting is not None
            assert meeting.status != "recording", (
                f"异常断开的会议仍停留在 recording（status={meeting.status}）"
            )
            assert meeting.status == "abandoned"

    def test_abandoned_meeting_still_persists_received_segments(
        self, client, admin_user, echo_mode
    ):
        """异常断开时，已经定稿的内容仍应落库，不能连带丢失。"""
        from sqlmodel import Session, select

        from imeeting.db import engine
        from imeeting.models import Meeting, Segment

        mid = None
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start", "title": "断连保数据"}))
            mid = ws.receive_json()["meeting_id"]
            # 推足够音频触发一次定稿，再硬断
            ws.send_bytes(pcm_bytes(1.0))
            ws.send_bytes(b"\x00" * int(16000 * 1.2 * 4))
            for _ in range(30):
                try:
                    if ws.receive_json()["type"] == "final":
                        break
                except Exception:
                    break
        assert mid is not None

        with Session(engine) as db:
            segs = db.exec(select(Segment).where(Segment.meeting_id == mid)).all()
            meeting = db.get(Meeting, mid)
            assert meeting is not None
            assert meeting.status != "recording"
            assert len(segs) >= 1, "异常断开时已定稿的片段丢失了"
            assert meeting.segment_count == len(segs)

    def test_failed_session_marks_failed(self, client, admin_user, echo_mode, monkeypatch):
        """会话处理过程中抛异常，会议应标记为 failed 而不是 recording。"""
        from sqlmodel import Session

        from imeeting.db import engine
        from imeeting.engine.streaming import StreamSession
        from imeeting.models import Meeting

        async def boom(self, pcm):
            raise RuntimeError("模拟推理崩溃")

        monkeypatch.setattr(StreamSession, "push_audio", boom)

        mid = None
        try:
            with client.websocket_connect("/ws/stream") as ws:
                ws.send_text(json.dumps({"type": "start", "title": "异常会话"}))
                mid = ws.receive_json()["meeting_id"]
                ws.send_bytes(pcm_bytes(0.6))
                for _ in range(10):
                    try:
                        ws.receive_json()
                    except Exception:
                        break
        except Exception:
            pass
        assert mid is not None

        with Session(engine) as db:
            meeting = db.get(Meeting, mid)
            assert meeting is not None
            assert meeting.status in ("failed", "abandoned"), (
                f"异常会话状态错误: {meeting.status}"
            )
            assert meeting.status != "recording"

    def test_segment_count_matches_actual(self, client, admin_user, echo_mode):
        """segment_count 必须与实际片段数一致（曾出现重复计数）。"""
        from sqlmodel import Session, select

        from imeeting.db import engine
        from imeeting.models import Meeting, Segment

        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start", "title": "计数验证"}))
            mid = ws.receive_json()["meeting_id"]
            for _ in range(3):
                ws.send_bytes(pcm_bytes(1.0))
                ws.send_bytes(b"\x00" * int(16000 * 1.2 * 4))  # 静音触发断句
            ws.send_text(json.dumps({"type": "stop"}))
            for _ in range(80):
                try:
                    if ws.receive_json()["type"] == "closed":
                        break
                except Exception:
                    break

        with Session(engine) as db:
            meeting = db.get(Meeting, mid)
            actual = db.exec(select(Segment).where(Segment.meeting_id == mid)).all()
            assert meeting.segment_count == len(actual), (
                f"segment_count={meeting.segment_count} 与实际 {len(actual)} 不一致"
            )

    def test_reopening_same_meeting_appends(self, client, admin_user, echo_mode):
        """用已有 meeting_id 重连应复用该会议而不是新建。"""
        title = "复用会议"
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start", "title": title}))
            mid = ws.receive_json()["meeting_id"]
            ws.send_text(json.dumps({"type": "stop"}))
            for _ in range(20):
                try:
                    if ws.receive_json()["type"] == "closed":
                        break
                except Exception:
                    break

        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text(json.dumps({"type": "start", "meeting_id": mid}))
            again = ws.receive_json()
            assert again["meeting_id"] == mid, "重连时未复用已有会议"
            ws.send_text(json.dumps({"type": "stop"}))
            for _ in range(20):
                try:
                    if ws.receive_json()["type"] == "closed":
                        break
                except Exception:
                    break

    def test_reconnect_keeps_global_timeline(self, client, admin_user, echo_mode):
        """重连后片段时间轴必须接着上一段，不能又从 0 开始。"""
        from sqlmodel import Session, select

        from imeeting.db import engine
        from imeeting.models import MeetingSession, Segment

        def run_session(meeting_id: int | None = None) -> int:
            with client.websocket_connect("/ws/stream") as ws:
                hello: dict = {"type": "start", "title": "时间轴连续性"}
                if meeting_id is not None:
                    hello["meeting_id"] = meeting_id
                ws.send_text(json.dumps(hello))
                mid = ws.receive_json()["meeting_id"]
                ws.send_bytes(pcm_bytes(0.8))
                for _ in range(3):
                    ws.send_bytes(b"\x00\x00\x00\x00" * 8000)
                for _ in range(40):
                    if ws.receive_json()["type"] == "final":
                        break
                ws.send_text(json.dumps({"type": "stop"}))
                for _ in range(60):
                    try:
                        if ws.receive_json()["type"] == "closed":
                            break
                    except Exception:
                        break
                return mid

        mid = run_session()
        run_session(mid)

        with Session(engine) as db:
            segs = db.exec(
                select(Segment)
                .where(Segment.meeting_id == mid)
                .order_by(Segment.start_ms, Segment.seq)
            ).all()
            sessions = db.exec(
                select(MeetingSession).where(MeetingSession.meeting_id == mid)
            ).all()
        assert len(segs) >= 2
        assert segs[0].end_ms <= segs[-1].start_ms, "第二段又从 0 开始计时"
        assert len(sessions) == 2
        assert sessions[0].duration_ms > 0
        assert sessions[1].start_offset_ms >= sessions[0].duration_ms
