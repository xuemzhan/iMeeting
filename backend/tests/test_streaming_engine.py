"""流式转写状态机测试。

策略
----
不加载真实模型（那要 30s 且不可控），而是往模型池里**注入假模型**，
从而确定性地验证状态机的行为：

- 分块步长与缓冲消费是否正确（多喂/少喂都会导致音频丢失）
- partial / final 是否共用同一 segment_id（前端靠它覆盖而非追加）
- 静音断句时机、尾静音是否被丢弃
- session 之间 cache 是否隔离（串音 = 生产事故）
- 三种模式 normal / echo / off 的分支
"""

from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest

from imeeting.engine import pool
from imeeting.engine.streaming import SAMPLE_RATE, SegmentState, StreamSession

# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


class FakeStreamingModel:
    """流式模型替身。

    ``texts`` 逐次取用；用完后回落到 ``fallback``（默认 ``""`` 表示不再出新草稿）。
    显式设置 fallback 能避免测试依赖「第 N 次调用」这种脆弱假设。
    """

    def __init__(self, texts: list[str] | None = None, fallback: str = ""):
        self.texts = list(texts or [])
        self.fallback = fallback
        self.calls: list[dict] = []
        self.caches: list[int] = []

    def generate(self, *, input, cache, is_final, chunk_size, **kw):
        self.calls.append({"samples": len(input), "is_final": is_final})
        self.caches.append(id(cache))
        if not len(input):
            return []
        if self.texts:
            return [{"text": self.texts.pop(0)}]
        if self.fallback:
            return [{"text": self.fallback}]
        return []


class FakeOfflineModel:
    """离线模型替身：返回固定定稿文本与说话人。"""

    def __init__(self, text: str = "定稿内容", speaker: str | None = "0"):
        self.text = text
        self.speaker = speaker
        self.calls: list[int] = []

    def generate(self, *, input, **kw):
        self.calls.append(len(input))
        if not len(input):
            return []
        res: dict = {"text": self.text}
        if self.speaker is not None:
            res["sentence_info"] = [{"spk": self.speaker}]
        return [res]


@pytest.fixture
def env(monkeypatch):
    """把模型池替换为可控替身，并按需调整配置。"""
    monkeypatch.setenv("ASR_MODE", "normal")
    from imeeting.settings import config_store

    config_store._db_overrides = {}

    # 声纹抽取默认替身化为「拿不到向量」，避免单测真的去加载 CAM++ 模型
    # （2 秒加载 + 每句 0.25 秒推理，会让整个套件慢一个量级）。
    # 需要验证说话人的用例自己再 monkeypatch 覆盖。
    from imeeting.engine import streaming as streaming_mod

    monkeypatch.setattr(streaming_mod, "extract_embedding", lambda _pcm: None)

    saved_models = dict(pool._models)
    saved_keys = dict(pool._build_keys)
    pool._models.clear()
    pool._build_keys.clear()

    yield

    pool._models.clear()
    pool._models.update(saved_models)
    pool._build_keys.clear()
    pool._build_keys.update(saved_keys)
    config_store._db_overrides = {}


class Collector:
    """收集 emit 出来的消息。"""

    def __init__(self):
        self.messages: list[dict] = []

    async def __call__(self, payload: dict) -> None:
        self.messages.append(payload)

    def of(self, kind: str) -> list[dict]:
        return [m for m in self.messages if m["type"] == kind]

    @property
    def kinds(self) -> list[str]:
        return [m["type"] for m in self.messages]


def tone(seconds: float, amp: float = 0.3, freq: float = 440.0) -> np.ndarray:
    """生成有声正弦波（能量远高于静音阈值）。"""
    n = int(SAMPLE_RATE * seconds)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32)


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_start_emits_ready(self, env):
        async def run():
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            return c

        c = asyncio.run(run())
        ready = c.of("ready")
        assert len(ready) == 1
        assert ready[0]["meeting_id"] == 1
        assert ready[0]["sample_rate"] == SAMPLE_RATE
        assert "session_id" in ready[0]

    def test_ready_reports_mode(self, env, monkeypatch):
        async def run():
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            return c

        monkeypatch.setenv("ASR_MODE", "echo")
        from imeeting.settings import config_store

        config_store._db_overrides = {}
        c = asyncio.run(run())
        # 环境变量优先级最高，所以这里必然是 echo
        assert c.of("ready")[0]["mode"] == "echo"

    def test_close_is_idempotent(self, env):
        async def run():
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.close()
            await s.close()
            await s.close()
            return c

        c = asyncio.run(run())
        assert len(c.of("closed")) == 1, "重复 close 应只发出一次 closed"

    def test_push_after_close_is_ignored(self, env):
        async def run():
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.close()
            before = len(c.messages)
            await s.push_audio(tone(1.0))
            return c, before

        c, before = asyncio.run(run())
        assert len(c.messages) == before, "close 之后不应再有任何输出"

    def test_empty_session_closes_cleanly(self, env):
        async def run():
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.close()
            return c

        c = asyncio.run(run())
        assert c.kinds == ["ready", "closed"]


# ---------------------------------------------------------------------------
# partial 语义
# ---------------------------------------------------------------------------


class TestPartial:
    def test_partial_carries_segment_id(self, env):
        async def run():
            pool._models["streaming"] = FakeStreamingModel(fallback="草稿")
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(1.0))
            await s.close()
            return c

        c = asyncio.run(run())
        partials = c.of("partial")
        assert partials, "有声音频应产出 partial"
        for p in partials:
            assert p.get("segment_id"), "partial 必须带 segment_id"

    def test_partial_end_ms_monotonic(self, env):
        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(6):
                await s.push_audio(tone(0.6))
            await s.close()
            return c

        c = asyncio.run(run())
        ends = [p["end_ms"] for p in c.of("partial")]
        assert ends == sorted(ends), "end_ms 必须单调不减，否则前端时间轴会跳"

    def test_identical_text_not_resent(self, env):
        """同样的草稿不要反复推送，省带宽也避免前端无谓重渲染。"""
        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=["同"] * 20)
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(5):
                await s.push_audio(tone(0.6))
            await s.close()
            return c

        c = asyncio.run(run())
        assert len(c.of("partial")) == 1

    def test_partial_accumulates_incremental_deltas(self, env):
        """流式模型返回的是**增量**，后端必须按序累积成全文。

        回归用例：早先前端表现为「识别出来只有一个字」。原因是后端把每次
        返回的增量直接赋值给 ``seg.text``（覆盖），导致屏幕只剩最后一帧。
        正确行为是不断累积，且每次 partial 都是「到目前为止的完整草稿」。
        """
        async def run():
            # 模拟真实模型：每次给一小段增量
            pool._models["streaming"] = FakeStreamingModel(
                texts=["欢迎大", "家来", "体验达"]
            )
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(4):
                await s.push_audio(tone(0.6))
            await s.close()
            return c

        c = asyncio.run(run())
        texts = [p["text"] for p in c.of("partial")]
        assert texts, "应产出 partial"
        # 每次上屏的都是累积结果，而不是单个增量片段
        assert texts[0] == "欢迎大"
        assert texts[1] == "欢迎大家来"
        assert texts[-1] == "欢迎大家来体验达"
        # 关键断言：不再是「只有一个字」那种被覆盖的形态
        assert len(texts[-1]) > 1, "最终 partial 不应退化成单字"
        assert texts[-1].startswith(texts[0]), "后续草稿必须包含先前内容（累积而非覆盖）"

    def test_partial_handles_cumulative_model_output(self, env):
        """兼容部分实现返回「累积全文」的情况，不能把全文重复拼一遍。"""
        async def run():
            pool._models["streaming"] = FakeStreamingModel(
                texts=["欢迎", "欢迎大家", "欢迎大家来体验"]
            )
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(4):
                await s.push_audio(tone(0.6))
            await s.close()
            return c

        c = asyncio.run(run())
        texts = [p["text"] for p in c.of("partial")]
        assert texts[-1] == "欢迎大家来体验", f"不应重复拼接，实际 {texts[-1]!r}"

    def test_partial_dedupes_overlapping_delta(self, env):
        """编码器 look-back 可能让增量开头与已累积尾部重叠，需去重。"""
        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=["欢迎大", "大"])
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(3):
                await s.push_audio(tone(0.6))
            await s.close()
            return c

        c = asyncio.run(run())
        texts = [p["text"] for p in c.of("partial")]
        assert texts[-1] == "欢迎大", f"重叠增量应被去重，实际 {texts[-1]!r}"


# ---------------------------------------------------------------------------
# 双路覆盖语义（核心）
# ---------------------------------------------------------------------------


class TestDualPath:
    def test_final_and_partial_share_segment_id(self, env):
        """这是双路设计的核心契约：前端据此覆盖而不是追加。"""
        async def run():
            pool._models["streaming"] = FakeStreamingModel(fallback="草稿")
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(1.0))
            await s.push_audio(silence(1.2))  # 触发断句
            await s.close()
            return c

        c = asyncio.run(run())
        partial_ids = {p["segment_id"] for p in c.of("partial")}
        final_ids = {f["segment_id"] for f in c.of("final")}
        assert partial_ids, "应先产出 partial 草稿"
        assert partial_ids & final_ids, "final 必须复用 partial 的 segment_id"

    def test_final_replaces_text(self, env):
        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=["草"])
            pool._models["offline"] = FakeOfflineModel(text="这是完整定稿。")
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(1.0))
            await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        finals = c.of("final")
        assert finals
        assert finals[0]["text"] == "这是完整定稿。"

    def test_seq_increments_per_final(self, env):
        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(3):
                await s.push_audio(tone(1.0))
                await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        seqs = [f["seq"] for f in c.of("final")]
        assert seqs == list(range(len(seqs))), f"seq 应连续递增，实际 {seqs}"

    def test_final_carries_speaker(self, env, monkeypatch):
        """声纹向量可用时，定稿应带上说话人标签。

        注意：**不能**依赖离线模型返回的 `spk`。那是单次 generate() 内部的
        聚类下标，每次调用都从 0 重新编号；本项目一句一调用，于是每句都被
        独立聚成第 0 类，所有片段说话人恒为 0。说话人改由 CAM++ 声纹向量在
        会话层面判定，所以这里替身化 extract_embedding。
        """
        import numpy as np

        from imeeting.engine import speaker as speaker_mod
        from imeeting.engine import streaming as streaming_mod

        vec = np.ones(192, dtype=np.float32)
        vec /= np.linalg.norm(vec)
        monkeypatch.setattr(streaming_mod, "extract_embedding", lambda _pcm: vec)
        monkeypatch.setattr(speaker_mod, "match_enrolled", lambda *args, **kwargs: None)

        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            # 故意让模型返回一个「诱饵」spk：必须被忽略，不能透传到定稿
            pool._models["offline"] = FakeOfflineModel(speaker="7")
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(1.8))
            await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        finals = c.of("final")
        assert finals[0]["speaker"] == "说话人 1"
        # 模型给的 spk 不得泄漏到结果里
        assert finals[0]["speaker"] != "7"

    def test_same_speaker_keeps_same_label(self, env, monkeypatch):
        """同一声纹向量跨多句必须落到同一个说话人编号。"""
        import numpy as np

        from imeeting.engine import streaming as streaming_mod

        vec = np.ones(192, dtype=np.float32)
        vec /= np.linalg.norm(vec)
        monkeypatch.setattr(streaming_mod, "extract_embedding", lambda _pcm: vec)

        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(3):
                await s.push_audio(tone(1.8))
                await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        speakers = {f["speaker"] for f in c.of("final")}
        assert speakers == {"说话人 1"}, f"同一说话人被分成了多个标签: {speakers}"

    def test_short_utterance_gets_no_speaker(self, env, monkeypatch):
        """过短的语音不判说话人，也不应去抽声纹。

        这是回归用例：实测「别。」这类一两个字的片段，声纹向量与任何人都
        对不上（相似度仅 0.15~0.26），会让每一句都新开一个说话人类，
        把单人的会议拆成「说话人 1/2/3/4」，并污染声纹库。
        """
        import numpy as np

        from imeeting.engine import streaming as streaming_mod

        calls: list[int] = []
        vec = np.ones(192, dtype=np.float32)
        vec /= np.linalg.norm(vec)

        def fake_extract(pcm):
            calls.append(len(pcm))
            return vec

        monkeypatch.setattr(streaming_mod, "extract_embedding", fake_extract)

        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(0.4))  # 有效语音仅 ~400ms
            await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        finals = c.of("final")
        assert finals, "短语音仍应产出定稿，只是不说话人"
        assert finals[0]["speaker"] is None
        assert calls == [], "过短的片段不应调用声纹抽取"

    def test_short_utterance_matches_known_speaker(self, env, monkeypatch):
        """短句不做新建聚类，但可以回填到已存在的说话人原型。

        真实长测里 68% 的片段因为有效语音 <1.2s 被标成「未知」，
        其中相当一部分其实与前面出现的说话人高度相似。
        """
        import numpy as np

        from imeeting.engine import speaker as speaker_mod
        from imeeting.engine import streaming as streaming_mod

        vec = np.ones(192, dtype=np.float32)
        vec /= np.linalg.norm(vec)
        monkeypatch.setattr(streaming_mod, "extract_embedding", lambda _pcm: vec)
        monkeypatch.setattr(speaker_mod, "match_enrolled", lambda *args, **kwargs: None)

        async def run():
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            # 先建立「说话人 1」原型
            assert s._speaker_resolver.resolve(vec) == "说话人 1"
            # 1.0s 有效语音：低于 1.2s 的新建阈值，但高于 0.7s 的回填阈值
            hit = await s._resolve_speaker(tone(1.0))
            return hit, s

        hit, s = asyncio.run(run())
        assert hit is not None and hit.label == "说话人 1"
        assert len(s._speaker_resolver._clusters) == 1, "短句不应新建说话人类"

    def test_short_utterance_without_match_stays_unknown(self, env, monkeypatch):
        """短句匹配不到已有说话人时仍然是未知，且不得污染原型集合。"""
        import numpy as np

        from imeeting.engine import speaker as speaker_mod
        from imeeting.engine import streaming as streaming_mod

        known = np.zeros(192, dtype=np.float32)
        known[0] = 1.0
        other = np.zeros(192, dtype=np.float32)
        other[1] = 1.0
        monkeypatch.setattr(streaming_mod, "extract_embedding", lambda _pcm: other)
        monkeypatch.setattr(speaker_mod, "match_enrolled", lambda *args, **kwargs: None)

        async def run():
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            assert s._speaker_resolver.resolve(known) == "说话人 1"
            label = await s._resolve_speaker(tone(1.0))
            return label, s

        label, s = asyncio.run(run())
        assert label is None
        assert len(s._speaker_resolver._clusters) == 1

    def test_soft_segment_cuts_on_short_pause(self, env, monkeypatch):
        """超过软切分起点后，一次短暂停顿就定稿，不再拖到 30s 硬切。"""
        from imeeting.settings import config_store

        monkeypatch.setenv("ASR_MODE", "echo")
        config_store._db_overrides = {
            "vad.soft_segment_ms": 3000,
            "vad.short_silence_ms": 250,
            "vad.max_segment_ms": 30000,
            "vad.silence_ms": 800,
        }

        async def run():
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(3.2))
            await s.push_audio(silence(0.3))
            return c, s

        c, s = asyncio.run(run())
        assert c.of("final")
        summary = s.metrics.summary(duration_ms=s.duration_ms)
        assert summary["finalize_reasons"].get("adaptive_silence", 0) >= 1

    def test_out_of_order_emit_after_reorder_timeout(self, env):
        """慢片段阻塞超过阈值后，后面的定稿允许先发。"""

        async def run():
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            seg = SegmentState(
                segment_id="s-x-1",
                start_ms=0,
                end_ms=1000,
                text="后到先发",
                speaker="说话人 1",
                seq=1,
            )
            seg.end_perf = time.perf_counter()
            seg.finalize_reason = "silence"
            s._final_ready[1] = {
                "type": "final",
                "segment_id": "s-x-1",
                "text": "后到先发",
                "start_ms": 0,
                "end_ms": 1000,
                "speaker": "说话人 1",
                "speaker_key": None,
                "seq": 1,
            }
            s._metric_pending[1] = (seg, time.perf_counter() - 5)
            await s._drain_finals()
            return c, s

        c, s = asyncio.run(run())
        finals = c.of("final")
        assert finals and finals[0]["seq"] == 1
        assert s.metrics.out_of_order_emits == 1

    def test_finals_emitted_in_seq_order(self, env, monkeypatch):
        """定稿任务并发执行，但发出顺序必须严格按 seq 递增。

        这是回归用例：加了声纹抽取之后，各定稿任务的耗时差异变大，
        完成顺序开始乱序，实测出现过 seq=[3,1,5,13,9,0,...] 的发出顺序。
        """
        import numpy as np

        from imeeting.engine import streaming as streaming_mod

        vec = np.ones(192, dtype=np.float32)
        vec /= np.linalg.norm(vec)
        monkeypatch.setattr(streaming_mod, "extract_embedding", lambda _pcm: vec)

        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(8):
                await s.push_audio(tone(1.8))
                await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        seqs = [f["seq"] for f in c.of("final")]
        assert seqs == list(range(len(seqs))), f"定稿发出顺序乱序: {seqs}"


# ---------------------------------------------------------------------------
# 静音与噪声处理（防呆）
# ---------------------------------------------------------------------------


class TestSilenceHandling:
    def test_pure_silence_produces_no_final(self, env):
        """全程静音不应产出任何定稿——否则会议列表里全是空记录。"""
        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=[])
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(10):
                await s.push_audio(silence(0.6))
            await s.close()
            return c

        c = asyncio.run(run())
        assert c.of("final") == [], f"纯静音却产出了定稿: {c.of('final')}"

    def test_live_signal_warning_on_zero_pcm(self, env):
        """会话进行中连续收到全零 PCM 时，必须主动推一条 signal_warning。

        这是「死麦」的实时路标：此前只有会话结束时的日志，用户整场
        无从察觉。服务端基于「收到音频但能量恒为 0」独立判断。
        """
        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=[])
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            # 推 12 秒全零（超过 5s 阈值）
            for _ in range(20):
                await s.push_audio(silence(0.6))
            await s.close()
            return c

        c = asyncio.run(run())
        warns = c.of("signal_warning")
        assert len(warns) == 1, f"应恰好推送一次采集异常提醒，实际 {len(warns)}"
        assert warns[0]["silent_seconds"] >= 5.0
        assert "麦克风" in warns[0]["message"]

    def test_signal_warning_not_sent_when_audio_present(self, env):
        """有真实音频时不得误报。"""
        async def run():
            pool._models["streaming"] = FakeStreamingModel(fallback="话")
            pool._models["offline"] = FakeOfflineModel(text="说话。")
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(20):
                await s.push_audio(tone(0.6))
            await s.close()
            return c

        c = asyncio.run(run())
        assert c.of("signal_warning") == [], "有音频却报了采集异常"

    def test_signal_warning_sent_only_once(self, env):
        """长时间静音也只提醒一次，避免刷屏。"""
        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=[])
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(40):
                await s.push_audio(silence(0.6))
            await s.close()
            return c

        c = asyncio.run(run())
        assert len(c.of("signal_warning")) == 1

    def test_trailing_silence_after_speech_no_empty_final(self, env):
        """有声 + 尾静音：只能有一条有内容的定稿。"""
        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=["话"])
            pool._models["offline"] = FakeOfflineModel(text="说了一句话。")
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(1.0))
            await s.push_audio(silence(3.0))  # 尾部长静音
            await s.close()
            return c

        c = asyncio.run(run())
        finals = c.of("final")
        assert len(finals) == 1, f"应为 1 条定稿，实际 {len(finals)}: {finals}"
        assert finals[0]["text"] == "说了一句话。"

    def test_too_short_segment_dropped(self, env):
        """10ms 爆音 + 静音：有效语音仅 10ms，应被当作噪声丢弃。

        段总长会被后随静音填充到 1.5s 以上，整段 RMS 也过线，
        所以必须靠「有效语音时长」判据才能拦住。
        """
        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=[])
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(0.01))
            await s.push_audio(silence(1.5))
            await s.close()
            return c

        c = asyncio.run(run())
        assert c.of("final") == [], f"瞬时噪声不应定稿: {c.of('final')}"

    def test_short_burst_among_silence_not_transcribed(self, env):
        """敲键盘场景：3 次短促爆音混在静音里，都不该出字。"""
        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=[])
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(3):
                await s.push_audio(tone(0.03))
                await s.push_audio(silence(1.0))
            await s.close()
            return c

        c = asyncio.run(run())
        assert c.of("final") == []

    def test_real_speech_still_passes_min_length(self, env):
        """正常说话 1s 必须能定稿——防呆不能把真话也滤掉。"""
        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=["正"])
            pool._models["offline"] = FakeOfflineModel(text="正常说话。")
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(1.0))
            await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        assert len(c.of("final")) == 1
        assert c.of("final")[0]["text"] == "正常说话。"

    def test_offline_empty_text_on_silence_not_emitted(self, env):
        """模型对静音返回空文本时，不应发出空定稿。"""

        class EmptyOffline:
            def generate(self, *, input, **kw):
                return [{"text": ""}]

        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=["有字"])
            pool._models["offline"] = EmptyOffline()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(1.0))
            await s.push_audio(silence(3.0))
            await s.close()
            return c

        c = asyncio.run(run())
        # 有真实语音但模型没出字 → 降级保留草稿（好过什么都不显示）
        finals = c.of("final")
        assert len(finals) <= 1
        if finals:
            assert finals[0]["text"] == "有字", "降级时应保留草稿文本"

    def test_empty_text_does_not_promote_bare_draft(self, env):
        """模型返回空文本、且段内无有效语音时，不能把流式草稿封成定稿。

        草稿是未经验证的中间态，直接封成 final 会让用户以为「这句话定了」，
        实际上模型根本没听清。
        """

        class EmptyOffline:
            def generate(self, *, input, **kw):
                return [{"text": ""}]

        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=["草稿文本"])
            pool._models["offline"] = EmptyOffline()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(0.02))  # 仅 20ms，不足最短语音
            await s.push_audio(silence(1.5))
            await s.close()
            return c

        c = asyncio.run(run())
        assert c.of("final") == [], "未经验证的草稿被错误封成了定稿"

    def test_empty_text_falls_back_to_draft_when_speech_present(self, env):
        """有真实语音但模型没出字时，降级保留草稿好过什么都不显示。"""

        class EmptyOffline:
            def generate(self, *, input, **kw):
                return [{"text": ""}]

        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=["有语音的草稿"])
            pool._models["offline"] = EmptyOffline()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(1.0))
            await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        finals = c.of("final")
        assert len(finals) == 1
        assert finals[0]["text"] == "有语音的草稿"

    def test_offline_returns_empty_list(self, env):
        """模型返回空列表（而非 [{"text": ""}]）时同样不能崩。"""

        class NoneOffline:
            def generate(self, *, input, **kw):
                return []

        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=[])
            pool._models["offline"] = NoneOffline()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(1.0))
            await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        assert c.of("final") == []
        assert "closed" in c.kinds

    def test_speech_after_silence_creates_second_segment(self, env):
        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(1.0))
            await s.push_audio(silence(1.2))
            await s.push_audio(tone(1.0))
            await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        finals = c.of("final")
        assert len(finals) == 2
        assert finals[0]["segment_id"] != finals[1]["segment_id"], "两句话不能共用 segment_id"
        assert finals[0]["start_ms"] < finals[1]["start_ms"]


# ---------------------------------------------------------------------------
# 自适应噪声底
# ---------------------------------------------------------------------------


def band_noise(seconds: float, amp: float) -> np.ndarray:
    """生成稳定的宽带底噪（模拟空调/投影仪），能量均匀且无语音特征。"""
    n = int(SAMPLE_RATE * seconds)
    rng = np.random.default_rng(1234)
    return (rng.standard_normal(n).astype(np.float32) * amp)


class TestAdaptiveNoiseFloor:
    """固定阈值在嘈杂会议室会失效，因此用自适应噪声底。

    核心诉求：底噪抬高时，判据阈值要跟着抬高，
    让「底噪」仍被判为静音、真实说话仍被判为有声。
    """

    def _session(self):
        c = Collector()
        return StreamSession(meeting_id=1, emit=c), c

    def test_threshold_rises_with_noise_floor(self, env):
        """持续底噪必须把阈值抬上去，否则底噪会被当成语音。"""
        async def run():
            s, _ = self._session()
            base = s._start_threshold
            for _ in range(20):
                await s.push_audio(band_noise(0.1, 0.004))
            return base, s._start_threshold

        base, raised = asyncio.run(run())
        assert raised > base, f"阈值未随底噪上升: {base} → {raised}"

    def test_noise_floor_does_not_run_away(self, env):
        """阈值有上限，防止极端噪声把判据拉到听不见说话。"""
        async def run():
            s, _ = self._session()
            for _ in range(50):
                await s.push_audio(band_noise(0.1, 0.5))  # 很吵
            return s._start_threshold

        thr = asyncio.run(run())
        assert thr <= StreamSession._max_threshold + 1e-9, f"阈值失控: {thr}"

    def test_quiet_environment_keeps_threshold_low(self, env):
        """安静环境下阈值应贴近下限，保证能捕捉轻声说话。"""
        async def run():
            s, _ = self._session()
            for _ in range(30):
                await s.push_audio(silence(0.1))
            return s._start_threshold

        thr = asyncio.run(run())
        assert thr <= StreamSession._base_threshold + 1e-6

    def test_noise_is_not_treated_as_speech(self, env):
        """持续底噪（无人说话）不应产出任何定稿。"""
        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=[])
            pool._models["offline"] = FakeOfflineModel()
            s, c = self._session()
            await s.start()
            for _ in range(30):
                await s.push_audio(band_noise(0.2, 0.004))
            await s.close()
            return c

        c = asyncio.run(run())
        assert c.of("final") == [], f"底噪被误判为语音: {c.of('final')}"

    def test_speech_detected_on_top_of_noise(self, env):
        """在有底噪的环境里说话仍必须能定稿——自适应不能把真话滤掉。"""
        async def run():
            pool._models["streaming"] = FakeStreamingModel(texts=["说"])
            pool._models["offline"] = FakeOfflineModel(text="在噪声中说话。")
            s, c = self._session()
            await s.start()
            # 先把噪声底建立起来
            for _ in range(10):
                await s.push_audio(band_noise(0.1, 0.004))
            # 再说话（能量远高于底噪）
            await s.push_audio(tone(1.0, amp=0.3))
            await s.push_audio(band_noise(1.5, 0.004))
            await s.close()
            return c

        c = asyncio.run(run())
        finals = c.of("final")
        assert len(finals) == 1, f"底噪环境下正常说话应产出 1 条定稿: {finals}"
        assert finals[0]["text"] == "在噪声中说话。"

    def test_silence_and_speech_judgements_use_same_threshold(self, env):
        """断句判据与丢噪判据必须同源，否则会出现自相矛盾的行为。"""
        async def run():
            s, _ = self._session()
            for _ in range(10):
                await s.push_audio(band_noise(0.1, 0.004))
            quiet = band_noise(0.2, 0.004)
            loud = tone(0.2, amp=0.3)
            return s._is_silent(quiet), s._is_silent(loud)

        quiet_silent, loud_silent = asyncio.run(run())
        assert quiet_silent is True, "底噪应被判为静音"
        assert loud_silent is False, "说话应被判为有声"

    def test_low_snr_emits_noise_warning(self, env):
        """持续低信噪比应向客户端推送一次噪声告警。"""

        async def run():
            s, c = self._session()
            await s.start()
            for _ in range(60):
                await s.push_audio(band_noise(0.2, 0.02))
            return s, c

        s, c = asyncio.run(run())
        warnings = c.of("noise_warning")
        assert warnings, "低信噪比未触发噪声告警"
        assert warnings[0]["snr_db"] < 12
        summary = s.metrics.summary(duration_ms=s.duration_ms)
        assert summary["noise_warnings"] >= 1
        assert summary["noise_floor_p50"] is not None


# ---------------------------------------------------------------------------
# 会话隔离（串音防护）
# ---------------------------------------------------------------------------


class TestSessionIsolation:
    def test_caches_are_distinct_objects(self, env):
        """两个会话必须用不同的 cache 对象，否则会串音。"""
        pool._models["streaming"] = FakeStreamingModel()
        m = pool._models["streaming"]

        async def run():
            c1, c2 = Collector(), Collector()
            s1 = StreamSession(meeting_id=1, emit=c1)
            s2 = StreamSession(meeting_id=2, emit=c2)
            await s1.start()
            await s2.start()
            await s1.push_audio(tone(0.6))
            await s2.push_audio(tone(0.6))
            await s1.close()
            await s2.close()

        asyncio.run(run())
        assert len(set(m.caches)) >= 2, "两个会话复用了同一个 cache 对象"

    def test_session_ids_unique(self, env):
        ids = {StreamSession(meeting_id=1, emit=Collector()).session_id for _ in range(50)}
        assert len(ids) == 50

    def test_sessions_do_not_share_segment_ids(self, env):
        a = StreamSession(meeting_id=1, emit=Collector())
        b = StreamSession(meeting_id=1, emit=Collector())
        assert a._ensure_current().segment_id != b._ensure_current().segment_id


# ---------------------------------------------------------------------------
# 三种模式
# ---------------------------------------------------------------------------


class TestModes:
    def test_echo_mode_does_not_touch_models(self, env, monkeypatch):
        """echo 模式跑的是占位逻辑，绝不能去加载模型。"""
        monkeypatch.setenv("ASR_MODE", "echo")
        from imeeting.settings import config_store

        config_store._db_overrides = {}

        async def run():
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(1.0))
            await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        assert c.of("partial"), "echo 模式应产出占位 partial"
        assert c.of("final"), "echo 模式应产出占位 final"
        assert pool._models == {}, "echo 模式不应加载任何真实模型"

    def test_echo_final_mentions_duration(self, env, monkeypatch):
        monkeypatch.setenv("ASR_MODE", "echo")
        from imeeting.settings import config_store

        config_store._db_overrides = {}

        async def run():
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(1.0))
            await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        assert "回声" in c.of("final")[0]["text"]

    def test_off_mode_produces_no_text(self, env, monkeypatch):
        monkeypatch.setenv("ASR_MODE", "off")
        from imeeting.settings import config_store

        config_store._db_overrides = {}

        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            pool._models["offline"] = FakeOfflineModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(1.0))
            await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        assert c.of("partial") == []
        assert c.of("final") == []
        assert c.kinds == ["ready", "closed"]

    def test_normal_without_models_degrades_gracefully(self, env):
        """模型没就绪时不能崩，只是暂时不出字。"""
        async def run():
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(1.0))
            await s.push_audio(silence(1.2))
            await s.close()
            return c

        c = asyncio.run(run())
        assert c.of("partial") == []
        assert c.of("final") == []
        assert "closed" in c.kinds


# ---------------------------------------------------------------------------
# 音频输入健壮性
# ---------------------------------------------------------------------------


class TestAudioRobustness:
    def test_int16_input_is_converted(self, env):
        """客户端若误发 int16，不应崩，应自动转 float32。"""
        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            pcm = (tone(0.6) * 32767).astype(np.int16)
            await s.push_audio(pcm)  # type: ignore[arg-type]
            await s.close()
            return c

        c = asyncio.run(run())
        assert "closed" in c.kinds

    def test_stereo_input_is_downmixed(self, env):
        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            mono = tone(0.6)
            stereo = np.stack([mono, mono], axis=1)
            await s.push_audio(stereo)
            await s.close()
            return c

        c = asyncio.run(run())
        assert "closed" in c.kinds

    def test_empty_chunk_does_not_crash(self, env):
        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            for _ in range(5):
                await s.push_audio(np.zeros(0, dtype=np.float32))
            await s.close()
            return c

        c = asyncio.run(run())
        assert "closed" in c.kinds

    def test_odd_sized_chunks_preserved(self, env):
        """任意长度的块都必须被完整消费，不能因为不对齐就丢音频。"""
        async def run():
            m = FakeStreamingModel()
            pool._models["streaming"] = m
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            total = 0
            for _ in range(17):  # 质数次数，确保边界不对齐
                chunk = tone(0.13)
                total += len(chunk)
                await s.push_audio(chunk)
            await s.close()
            fed = sum(call["samples"] for call in m.calls)
            return total, fed

        total, fed = asyncio.run(run())
        # 最后 is_final 那次调用喂的是 0 长度（用于冲刷）
        assert fed <= total
        assert fed >= total - 9600, f"音频被吞了：投入 {total}，喂给模型 {fed}"

    def test_nan_input_does_not_crash(self, env):
        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            bad = tone(0.6).copy()
            bad[0] = np.nan
            await s.push_audio(bad)
            await s.close()
            return c

        c = asyncio.run(run())
        assert "closed" in c.kinds


# ---------------------------------------------------------------------------
# 时长统计
# ---------------------------------------------------------------------------


class TestDuration:
    def test_duration_tracks_input(self, env):
        async def run():
            pool._models["streaming"] = FakeStreamingModel()
            c = Collector()
            s = StreamSession(meeting_id=1, emit=c)
            await s.start()
            await s.push_audio(tone(2.0))
            d = s.duration_ms
            await s.close()
            return d

        d = asyncio.run(run())
        assert 1900 < d < 2100, f"2 秒音频的时长统计异常: {d}"

    def test_segment_state_duration(self, env):
        from imeeting.engine.streaming import SegmentState

        seg = SegmentState(segment_id="x")
        seg.add_audio(tone(1.5))
        assert 1400 < seg.duration_ms < 1600

    def test_take_audio_clears_buffer(self, env):
        from imeeting.engine.streaming import SegmentState

        seg = SegmentState(segment_id="x")
        seg.add_audio(tone(0.5))
        first = seg.take_audio()
        assert len(first) > 0
        assert len(seg.take_audio()) == 0
        assert seg.duration_ms == 0
