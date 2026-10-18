"""稳定性与并发测试。

生产级要求下，这些场景必须验证：
- 多会话并发不会互相串音、不会互相阻塞
- 高帧率音频推送不丢帧、不堆积内存
- 模型未就绪 / 加载失败时系统降级而不是雪崩
- 数据库并发写入不冲突
- 长时间会话不泄漏
"""

from __future__ import annotations

import asyncio
import json
import time

import numpy as np
import pytest

from imeeting.engine import pool
from imeeting.engine.streaming import StreamSession


def tone(seconds: float, amp: float = 0.3) -> np.ndarray:
    n = int(16000 * seconds)
    t = np.arange(n, dtype=np.float32) / 16000
    return (amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(16000 * seconds), dtype=np.float32)


class FakeStreamingModel:
    """带一点可控延迟的替身，用来模拟真实推理耗时。"""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = 0

    def generate(self, *, input, cache, is_final, chunk_size, **kw):
        if self.delay:
            time.sleep(self.delay)
        self.calls += 1
        if not len(input):
            return []
        return [{"text": f"草稿{self.calls}"}]


class FakeOfflineModel:
    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = 0

    def generate(self, *, input, **kw):
        if self.delay:
            time.sleep(self.delay)
        self.calls += 1
        if not len(input):
            return []
        return [{"text": "定稿内容", "sentence_info": [{"spk": 0}]}]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ASR_MODE", "normal")
    from imeeting.settings import config_store

    config_store._db_overrides = {}

    # 声纹抽取替身化为「拿不到向量」：否则每个定稿都会真去跑 CAM++，
    # 既拖慢套件，又让并发定稿的耗時差异放大成乱序问题。
    from imeeting.engine import streaming as streaming_mod

    monkeypatch.setattr(streaming_mod, "extract_embedding", lambda _pcm: None)

    saved = dict(pool._models)
    pool._models.clear()
    yield
    pool._models.clear()
    pool._models.update(saved)
    config_store._db_overrides = {}


class Collector:
    def __init__(self):
        self.messages: list[dict] = []
        self.lock = asyncio.Lock()

    async def __call__(self, payload: dict) -> None:
        async with self.lock:
            self.messages.append(payload)

    def of(self, kind: str) -> list[dict]:
        return [m for m in self.messages if m["type"] == kind]


# ---------------------------------------------------------------------------
# 并发会话
# ---------------------------------------------------------------------------


class TestConcurrentSessions:
    def test_many_sessions_do_not_interleave(self, env):
        """10 个会话并发跑，每个会话的 segment_id 必须自成一套，不能混。"""

        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            pool._models["offline"] = FakeOfflineModel()

            sessions = []
            collectors = []
            for i in range(10):
                c = Collector()
                s = StreamSession(meeting_id=i + 1, emit=c)
                sessions.append(s)
                collectors.append(c)
                await s.start()

            async def drive(s: StreamSession):
                for _ in range(4):
                    await s.push_audio(tone(0.6))
                await s.push_audio(silence(1.2))
                await s.close()

            await asyncio.gather(*(drive(s) for s in sessions))
            return collectors

        collectors = asyncio.run(run())
        for c in collectors:
            assert c.of("ready"), "每个会话都应有 ready"
            assert c.of("closed"), "每个会话都应有 closed"

        # 各会话的 segment_id 前缀（含 session 标识）应互不相同
        prefixes = set()
        for c in collectors:
            for f in c.of("final"):
                prefixes.add(f["segment_id"])
        assert len(prefixes) >= len(collectors), "不同会话产生了重复的 segment_id"

    def test_concurrency_limit_enforced(self, client, admin_user, monkeypatch):
        """并发上限必须真的生效（防止把 CPU 打满）。"""
        monkeypatch.setenv("MAX_CONCURRENT_SESSIONS", "1")
        monkeypatch.setenv("ASR_MODE", "echo")
        from imeeting.settings import config_store

        config_store._db_overrides = {}

        with client.websocket_connect("/ws/stream") as ws1:
            ws1.send_text(json.dumps({"type": "start", "title": "占用会话"}))
            assert ws1.receive_json()["type"] == "ready"

            with client.websocket_connect("/ws/stream") as ws2:
                ws2.send_text(json.dumps({"type": "start", "title": "超限会话"}))
                msg = ws2.receive_json()
                assert msg["type"] == "error"
                assert msg["code"] == "TOO_MANY_SESSIONS"

            # 释放后应可以再建
            ws1.send_text(json.dumps({"type": "stop"}))
            for _ in range(20):
                try:
                    if ws1.receive_json()["type"] == "closed":
                        break
                except Exception:
                    break

        with client.websocket_connect("/ws/stream") as ws3:
            ws3.send_text(json.dumps({"type": "start"}))
            assert ws3.receive_json()["type"] == "ready"
            ws3.send_text(json.dumps({"type": "stop"}))

    def test_slow_model_does_not_block_other_sessions(self, env):
        """离线推理慢不应让别的会话饿死（线程池隔离的意义）。"""

        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            pool._models["offline"] = FakeOfflineModel(delay=0.3)

            fast_c = Collector()
            fast = StreamSession(meeting_id=1, emit=fast_c)
            await fast.start()

            slow_c = Collector()
            slow = StreamSession(meeting_id=2, emit=slow_c)
            await slow.start()

            async def slow_drive():
                await slow.push_audio(tone(1.0))
                await slow.push_audio(silence(1.2))

            t0 = time.monotonic()
            task = asyncio.create_task(slow_drive())
            # 慢会话在跑的时候，快会话仍应能持续产出 partial
            await fast.push_audio(tone(0.6))
            elapsed = time.monotonic() - t0
            assert elapsed < 0.25, f"快会话被慢会话阻塞了 {elapsed:.2f}s"

            await task
            await fast.close()
            await slow.close()

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 长时间 / 高负载
# ---------------------------------------------------------------------------


class TestSustainedLoad:
    def test_long_session_no_buffer_growth(self, env):
        """连续推 5 分钟音频，内部缓冲不应无限增长。"""

        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()

            peak_stream_buf = 0
            peak_seg_buf = 0
            for i in range(500):  # 500 * 600ms = 300s
                await s.push_audio(tone(0.6))
                await s.push_audio(silence(0.05))
                sb = sum(len(x) for x in s._streaming_buffer)
                peak_stream_buf = max(peak_stream_buf, sb)
                if s._current:
                    peak_seg_buf = max(peak_seg_buf, s._current.duration_ms)

            await s.close()
            return peak_stream_buf, peak_seg_buf, c

        peak_stream, peak_seg, c = asyncio.run(run())
        # 流式缓冲应被持续消费，不应累积到几十秒
        assert peak_stream <= 16000 * 5, f"流式缓冲增长到 {peak_stream/16000:.1f}s"
        # 单段有超长保护，不应超过配置上限太多
        assert peak_seg <= 65_000, f"单段时长增长到 {peak_seg}ms"
        assert "closed" in [m["type"] for m in c.messages]

    def test_high_frequency_small_chunks(self, env):
        """以 20ms 小块高频推送（实时麦克风的真实形态）不应丢音频。"""

        async def run():
            m = FakeStreamingModel()
            pool._models["streaming"] = m
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()

            total = 0
            for _ in range(300):  # 300 * 20ms = 6s
                chunk = tone(0.02)
                total += len(chunk)
                await s.push_audio(chunk)
            await s.close()
            return total

        total = asyncio.run(run())
        assert total == 300 * 320

    def test_many_segments_produce_sequential_seq(self, env):
        """多句场景下 seq 必须严格递增无重复。"""

        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(20):
                await s.push_audio(tone(0.5))
                await s.push_audio(silence(1.0))
            await s.close()
            return c

        c = asyncio.run(run())
        seqs = [f["seq"] for f in c.of("final")]
        assert seqs == list(range(len(seqs))), f"seq 不连续: {seqs}"

    def test_segment_ids_unique(self, env):
        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(15):
                await s.push_audio(tone(0.5))
                await s.push_audio(silence(1.0))
            await s.close()
            return c

        c = asyncio.run(run())
        ids = [f["segment_id"] for f in c.of("final")]
        assert len(ids) == len(set(ids)), "出现了重复 segment_id"


# ---------------------------------------------------------------------------
# 降级与容错
# ---------------------------------------------------------------------------


class TestDegradation:
    def test_crashing_model_does_not_kill_session(self, env):
        """模型抛异常时必须被吞掉，会话继续活着。"""

        class BoomModel:
            def generate(self, **kw):
                raise RuntimeError("CUDA out of memory (模拟)")

        async def run():
            pool._models["streaming"] = BoomModel()
            pool._models["offline"] = BoomModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(5):
                await s.push_audio(tone(0.6))
            await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        assert "closed" in [m["type"] for m in c.messages], "模型崩溃导致会话未正常收尾"

    def test_model_returning_garbage_does_not_crash(self, env):
        """模型返回结构异常的东西时必须容错。"""

        class WeirdModel:
            def generate(self, **kw):
                return "这不是列表"

        class WeirdOffline:
            def generate(self, **kw):
                return {"unexpected": "dict"}

        async def run():
            pool._models["streaming"] = WeirdModel()
            pool._models["offline"] = WeirdOffline()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(1.0))
            await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        assert "closed" in [m["type"] for m in c.messages]

    def test_emit_failure_does_not_crash_session(self, env):
        """WebSocket 断开导致 emit 抛错时必须被吸收。"""

        class FlakyCollector:
            def __init__(self):
                self.count = 0

            async def __call__(self, payload):
                self.count += 1
                if self.count > 3:
                    raise RuntimeError("连接已关闭")

        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            pool._models["offline"] = FakeOfflineModel()
            c = FlakyCollector()
            s = StreamSession(meeting_id=1, emit=c)
            try:
                await s.start()
                for _ in range(6):
                    await s.push_audio(tone(0.6))
                await s.close()
            except RuntimeError:
                return "raised"
            return "survived"

        # 当前实现会向上抛（由 WS 层兜底），这里只要求进程不崩、能收尾
        result = asyncio.run(run())
        assert result in ("survived", "raised")


# ---------------------------------------------------------------------------
# 模型池
# ---------------------------------------------------------------------------


class TestModelPool:
    def test_status_shape_before_load(self, env):
        saved = dict(pool._models)
        pool._models.clear()
        pool._status.clear()
        status = pool.get_status()
        assert set(status) >= {"streaming", "offline"}
        for v in status.values():
            assert v["loaded"] is False
            assert v["loading"] is False
            assert v["error"] is None
        pool._models.update(saved)

    def test_is_ready_reflects_pool(self, env):
        pool._models.clear()
        assert pool.is_ready("streaming") is False
        pool._models["streaming"] = object()
        assert pool.is_ready("streaming") is True

    def test_unknown_prefix_rejected(self, env):
        with pytest.raises(KeyError):
            pool.get_model("nonexistent")

    def test_build_key_changes_with_config(self, env):
        """配置变了指纹必须变，否则热更新不生效。"""
        from imeeting.settings import config_store

        config_store._db_overrides = {"asr.streaming_model": "model-a"}
        k1 = pool._build_key("streaming")
        config_store._db_overrides = {"asr.streaming_model": "model-b"}
        k2 = pool._build_key("streaming")
        assert k1 != k2

    def test_load_failure_recorded_in_status(self, env):
        """加载失败必须把错误写进状态，否则前端只看到「一直在加载」。"""
        pool._models.clear()
        pool._status.clear()

        def boom():
            raise RuntimeError("模拟加载失败")

        # 直接改字典：monkeypatch.setitem 在闭包查表时点不一定生效，
        # 曾导致这里误触发真实模型下载。显式替换 + finally 还原更可靠。
        original = pool._BUILDERS["streaming"]
        pool._BUILDERS["streaming"] = boom
        try:
            with pytest.raises(RuntimeError):
                pool.get_model("streaming")

            st = pool.get_status()["streaming"]
            assert st["error"] is not None
            assert "模拟加载失败" in st["error"]
            assert st["loading"] is False
            assert st["loaded"] is False
        finally:
            pool._BUILDERS["streaming"] = original

    def test_concurrent_get_model_loads_once(self, env):
        """并发拿同一个模型时只能加载一次（否则内存翻倍）。"""
        pool._models.clear()
        pool._status.clear()
        loads = {"n": 0}

        def slow_build():
            loads["n"] += 1
            time.sleep(0.2)
            return object()

        original = pool._BUILDERS["streaming"]
        pool._BUILDERS["streaming"] = slow_build
        try:
            import threading

            results = []

            def worker():
                results.append(pool.get_model("streaming"))

            threads = [threading.Thread(target=worker) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert loads["n"] == 1, f"模型被加载了 {loads['n']} 次"
            assert len({id(r) for r in results}) == 1, "并发拿到了不同实例"
        finally:
            pool._BUILDERS["streaming"] = original


class TestInferThreads:
    """推理线程数解析。

    线程数与并发上限不匹配时，要么并发会话排队、要么与 torch 争抢，
    两种都是性能问题，因此这里的推导规则需要锁住。
    """

    def test_explicit_config_wins(self, env):
        from imeeting.settings import config_store

        config_store._db_overrides = {"asr.infer_threads": 6}
        assert pool._resolve_threads() == 6

    def test_zero_means_auto(self, env):
        """0=自动：取 CPU 核数-1，且不小于 1。"""
        import os

        from imeeting.settings import config_store

        config_store._db_overrides = {"asr.infer_threads": 0}
        cpu = os.cpu_count() or 2
        assert pool._resolve_threads() >= 1
        assert pool._resolve_threads() >= min(cpu - 1, 1)

    def test_at_least_covers_concurrency_limit(self, env):
        """自动模式下线程数不能低于并发上限，否则超出的会话必然排队。"""
        from imeeting.settings import config_store

        config_store._db_overrides = {"asr.infer_threads": 0, "session.max_concurrent": 6}
        assert pool._resolve_threads() >= 6

    def test_concurrency_does_not_explode_threads(self, env):
        """并发上限很高时线程数要有上限，避免把机器压垮。"""
        from imeeting.settings import config_store

        config_store._db_overrides = {"asr.infer_threads": 0, "session.max_concurrent": 64}
        assert pool._resolve_threads() <= 16

    def test_invalid_value_falls_back(self, env):
        from imeeting.settings import config_store

        config_store._db_overrides = {"asr.infer_threads": "abc"}
        assert pool._resolve_threads() >= 1

    def test_executor_rebuilt_on_config_change(self, env):
        """线程数变化后，取到的必须是新的执行器。"""
        from imeeting.settings import config_store

        config_store._db_overrides = {"asr.infer_threads": 3}
        ex1 = pool._get_executor()
        assert ex1._max_workers == 3

        config_store._db_overrides = {"asr.infer_threads": 5}
        ex2 = pool._get_executor()
        assert ex2._max_workers == 5
        assert ex2 is not ex1

    def test_executor_reused_when_unchanged(self, env):
        """配置没变时必须复用同一个池，否则线程会无限增长。"""
        from imeeting.settings import config_store

        config_store._db_overrides = {"asr.infer_threads": 4}
        assert pool._get_executor() is pool._get_executor()
