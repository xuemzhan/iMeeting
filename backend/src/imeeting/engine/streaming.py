"""流式转写会话：双路 ASR 调度。

架构
----
一条音频流分成两条处理路径：

- **流式路径**：每收到一个分块就送进流式模型，产出 ``partial`` 结果，
  用于屏幕上的实时字幕。延迟低但精度有限。
- **离线路径**：VAD 判定一句结束后，把该句完整音频送进离线模型，
  产出 ``final`` 结果。精度高，但需要等整句结束。

两条路径用同一个 ``segment_id`` 关联：partial 先上屏，final 到了就覆盖。
前端按 id 更新卡片，不做重排。

``cache`` 必须按会话隔离 —— 不同会议共用会导致音频上下文串扰。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import numpy as np

from ..settings import get_config
from ..speaker_policy import is_low_confidence
from . import pool
from .adaptive import AdaptiveMeetingController
from .audio_enhance import AgcProcessor, normalize_segment, spectral_denoise
from .final_orderer import FinalOrderer, PendingFinal
from .metrics import MeetingMetrics
from .speaker import (
    SpeakerResolution,
    SpeakerResolver,
    extract_embedding,
    save_discovered,
)
from .speaker_context import SpeakerContext

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# 判定说话人所需的最短「有效语音」时长（毫秒）的兜底默认值。
# 实际取值来自配置 `spk.min_speech_ms`（管理员可在设置里调），这里只是
# 配置缺失时的保底。低于此值不抽声纹——短句的向量不稳定，会产生错误的
# 说话人类。阈值来自真实会议数据的实测（见 scripts/diag_threshold.py）。
SPK_MIN_SPEECH_MS = 1200.0


def _merge_delta(acc: str, delta: str) -> str:
    """把流式模型的一块输出并入已累积草稿。

    Paraformer-streaming 的 ``generate(is_final=False)`` 每次只返回**本块新增**
    的文本（增量）。但也存在两种需要兼容的情况：

    1. 模型偶发返回整段累积文本（部分实现/版本会这样）；
    2. 因编码器 look-back 重叠，返回的增量开头与已累积内容尾部重复。

    因此这里做三步归一：完全重复 → 不追加；前缀重复 → 只取新增部分；
    其余情况直接拼接。
    """
    delta = (delta or "").strip()
    if not delta:
        return acc
    if not acc:
        return delta
    # 情况 1：模型这次返回的是从头开始的累积全文，直接替换更可靠
    if delta.startswith(acc):
        return delta
    # 情况 2：增量与已累积尾部有重叠，取最长重叠后截断
    max_overlap = min(len(acc), len(delta))
    for k in range(max_overlap, 0, -1):
        if acc.endswith(delta[:k]):
            return acc + delta[k:]
    # 情况 3：无重叠，正常追加
    return acc + delta


@dataclass
class SegmentState:
    """一句话的状态。"""

    segment_id: str
    buffer: list[np.ndarray] = field(default_factory=list)
    text: str = ""
    is_final: bool = False
    start_ms: int = 0
    end_ms: int = 0
    speaker: str | None = None
    speaker_key: str | None = None
    speaker_score: float = 0.0
    speaker_source: str = ""
    asr_source: str = ""
    asr_error: str | None = None
    speech_ms: int = 0
    audio_rms: float = 0.0
    noise_floor: float = 0.0
    snr_db: float = 0.0
    adaptive_gain: float = 1.0
    profile_version: int = 0
    seq: int = 0
    partial_sent: bool = False
    #: 流式模型返回的是**增量文本**（每个 chunk 只给这一块新增的字），
    #: 必须按序累积才是完整草稿。这里保存已累积的草稿全文，
    #: 与 ``text`` 分离：``text`` 在定稿时会被离线结果覆盖。
    streaming_text: str = ""
    #: 断句原因：silence / max_duration / user / stop（指标用）
    finalize_reason: str = ""
    #: 定稿实际发生的单调时钟；用于计算端到端定稿延迟
    end_perf: float = 0.0
    offline_ms: float = 0.0
    speaker_ms: float = 0.0
    #: 离线模型没给出文本、降级用流式草稿封定稿
    fallback: bool = False
    #: 本段是否真正进入过语音（能量越过起始阈值）
    had_speech: bool = False

    def add_audio(self, chunk: np.ndarray) -> None:
        self.buffer.append(chunk)

    def take_audio(self) -> np.ndarray:
        if not self.buffer:
            return np.zeros(0, dtype=np.float32)
        merged = np.concatenate(self.buffer)
        self.buffer.clear()
        return merged

    @property
    def duration_ms(self) -> int:
        total = sum(len(c) for c in self.buffer)
        return int(total / SAMPLE_RATE * 1000)


class StreamSession:
    """单个会议会话的转写状态机。

    使用方式::

        session = StreamSession(meeting_id=1, emit=ws_send)
        await session.start()
        await session.push_audio(pcm)   # 反复调用
        await session.close()
    """

    #: 判为「有声」的 RMS 兜底阈值（安静环境下够用）
    _base_threshold: float = 0.008
    #: 自适应阈值相对噪声底的最小倍数
    _threshold_ratio: float = 2.5
    #: 自适应阈值的上下限，防止极端环境把判据拉飞
    _min_threshold: float = 0.006
    _max_threshold: float = 0.06
    #: 噪声底跟踪速度：越小越稳（只吸收"更安静"的观测）
    _noise_decay: float = 0.995

    def __init__(
        self,
        meeting_id: int,
        emit: Callable[[dict], Awaitable[None]],
        *,
        session_id: str | None = None,
        start_offset_ms: int = 0,
        start_audio_seq: int = 0,
        adaptive_profile: dict | None = None,
    ):
        self.meeting_id = meeting_id
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self._emit = emit
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # 单测里可能在循环外构造会话；此时只需禁用异步推送
            self._loop = None
        #: 本段会话在会议全局时间轴上的起点（重连后接着上一段）
        self._start_offset_ms = max(0, int(start_offset_ms))
        self.start_audio_seq = max(0, int(start_audio_seq))
        self._adaptive = AdaptiveMeetingController(adaptive_profile)
        self._adaptive_gain = self._adaptive.profile.gain
        self._max_segment_ms = self._adaptive.profile.max_segment_ms
        self._over_max_ms = 0.0
        self._last_profile_save = time.monotonic()

        # 流式模型的会话级 cache —— 必须隔离
        self._cache: dict = {}
        self._streaming_buffer: list[np.ndarray] = []
        self._stream_chunk_samples = self._resolve_chunk_samples()

        self._current: SegmentState | None = None
        self._speaker_context = SpeakerContext()
        self._silence_samples = 0
        self._total_samples = 0
        self._seq = 0
        self._closed = False
        self._pending_tasks: set[asyncio.Task] = set()
        #: 全会话是否出现过「有能量」的音频。全零音频说明采集端异常
        #: （死麦/设备被独占/静音开关），必须能在收尾时被识别出来，
        #: 否则表现为「会话正常结束但 0 段」，日志里看不出任何原因。
        self._has_signal = False
        #: 已连续收到全零 PCM 的时长（秒），用于**实时**提醒采集异常。
        #: 与收尾时的 `_has_signal` 不同：这里要在会话进行中就把消息推给前端，
        #: 用户不必等到会议结束才发现「整场没有声音」。
        self._zero_seconds = 0.0
        #: 是否已推送过采集异常提醒（整场只提醒一次，避免刷屏）
        self._signal_warned = False

        self._chunk_size = get_config("asr.chunk_size")
        self._enc_look_back = get_config("asr.encoder_chunk_look_back")
        self._dec_look_back = get_config("asr.decoder_chunk_look_back")
        self._silence_limit = int(get_config("vad.silence_ms") / 1000 * SAMPLE_RATE)
        # 软切分：超过 soft_segment_ms 后，一次短暂停顿就定稿，避免一路拖到
        # max_segment_ms 硬切（真实长测里 26% 的片段是 30s 强切）。
        _soft = get_config("vad.soft_segment_ms")
        self._soft_segment_ms = (
            self._adaptive.profile.soft_segment_ms
            if self._adaptive.enabled
            else (0 if _soft is None else int(_soft))
        )
        _short = get_config("vad.short_silence_ms")
        self._short_silence_samples = int(
            (250 if _short is None else int(_short)) / 1000 * SAMPLE_RATE
        )
        self._short_merge_ms = int(get_config("adaptive.short_merge_ms") or 1800)
        # 有效语音时长下限：低于此值的片段视为噪声丢弃。
        # 注意不能用 `get_config(...) or 300`——0 是合法值（表示不过滤），
        # 而 `0 or 300` 会得 300，把「设 0 表示不过滤」变成永远做不到。
        _min_speech = get_config("vad.min_speech_ms")
        self._min_speech_ms = 300.0 if _min_speech is None else float(_min_speech)
        # 噪声底与判据阈值（自适应，见 _update_noise_floor）
        self._noise_floor = 0.0015
        self._start_threshold = self._base_threshold
        self._continue_threshold = self._base_threshold * 0.6
        #: 当前段是否已经进入语音（迟滞：起始阈值高于持续阈值）
        self._in_speech = False
        #: 近 ~13 秒的块能量，用于估计环境噪声底与 SNR
        self._energy_hist: deque[float] = deque(maxlen=64)
        self._noise_warned = False
        self._low_snr_seconds = 0.0
        self._noise_warning_pending = False
        self._last_snr_db = 0.0
        self._noise_metric_tick = 0
        self._spk_enabled = get_config("spk.enabled")
        # 判定说话人的最短有效语音时长（ms）。短于此值不抽声纹，
        # 显示为「未知」——详见 _assign_speaker 的说明。
        # 同样不能用 `or`：0 表示不限制，是合法值。
        _spk_min = get_config("spk.min_speech_ms")
        self._spk_min_speech_ms = (
            SPK_MIN_SPEECH_MS if _spk_min is None else float(_spk_min)
        )
        # 短句回填：语音时长不够新建说话人类时，只与已有原型做一次高阈值匹配。
        # 目标是提高「说话人未知」的覆盖率，同时避免把短句拆成新说话人类。
        _short_ms = get_config("spk.short_match_ms")
        self._spk_short_ms = 700.0 if _short_ms is None else float(_short_ms)
        _short_thr = get_config("spk.short_match_threshold")
        _base_thr = float(get_config("spk.threshold") or 0.5)
        self._spk_short_threshold = max(
            0.68 if _short_thr is None else float(_short_thr), _base_thr + 0.05
        )
        # 会话级说话人判定器：FunASR 的 spk 是单次调用内的聚类下标，
        # 每句都会重新从 0 编号，必须自己跨句聚类（见 engine/speaker.py）
        self._speaker_resolver = SpeakerResolver(
            threshold=float(get_config("spk.threshold") or 0.6),
            meeting_id=meeting_id,
        )
        self._speaker_snapshot_every = max(
            1, int(get_config("spk.snapshot_every_n") or 5)
        )
        self._speaker_snapshot_count = 0
        self._speaker_snapshot_tasks: set[asyncio.Task] = set()
        self._final_order = FinalOrderer(
            max(0, int(get_config("asr.reorder_timeout_ms") or 0)) / 1000.0
        )
        # "normal" 走真实模型；"echo" 回显占位文本用于联调；"off" 不出字
        self._mode = get_config("asr.mode") or "normal"
        # 可选音频增强：默认关闭，开启后只作用于送模型的音频
        self._denoise_enabled = bool(get_config("audio.denoise_enabled"))
        self._denoise_strength = float(
            get_config("audio.denoise_strength") or 0.3
        )
        self._agc_enabled = bool(get_config("audio.agc_enabled"))
        self._agc_target = float(get_config("audio.agc_target_rms") or 0.05)
        self._agc = (
            AgcProcessor(target_rms=self._agc_target) if self._agc_enabled else None
        )
        _trim_enabled = get_config("asr.trim_silence_enabled")
        self._trim_silence_enabled = (
            True if _trim_enabled is None else bool(_trim_enabled)
        )
        _trim_margin = get_config("asr.trim_margin_ms")
        self._trim_margin_ms = (
            250 if _trim_margin is None else max(0, int(_trim_margin))
        )
        self._translate_enabled = bool(get_config("translate.enabled"))
        self._translate_lang = str(get_config("translate.target_lang") or "en")
        self._translation_tasks: set[asyncio.Task] = set()
        # 会话级指标（只观测；写盘/日志失败不影响主链路）
        self.metrics = MeetingMetrics(self.meeting_id, self.session_id, self._mode)
        self._metric_pending: dict[int, tuple[SegmentState, float]] = {}
        register_session(self)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def _resolve_chunk_samples(self) -> int:
        """流式模型每次推理需要的采样数。

        Paraformer-streaming 的 chunk_size=[0, N, M] 表示每块 N*960 个采样点
        （frontend 下采样后每帧 960 个采样点 / 60ms）。
        """
        chunk_size = get_config("asr.chunk_size") or [0, 10, 5]
        try:
            stride = int(chunk_size[1]) * 960
        except (IndexError, TypeError, ValueError):
            stride = 9600
        return max(stride, 960)

    async def start(self) -> None:
        await self._emit(
            {
                "type": "ready",
                "meeting_id": self.meeting_id,
                "session_id": self.session_id,
                "sample_rate": SAMPLE_RATE,
                "mode": self._mode,
                "metadata_capture_minutes": int(
                    get_config("meeting.metadata_capture_minutes") or 0
                ),
                "speaker_low_confidence_threshold": float(
                    get_config("spk.low_confidence_threshold") or 0.62
                ),
                "audio_chunk_seq": self.start_audio_seq,
                "adaptive_profile": self._adaptive.snapshot(),
            }
        )
        logger.info("会话 %s 已建立 (meeting=%s, mode=%s)", self.session_id, self.meeting_id, self._mode)

    async def close(self) -> None:
        """结束会话：冲刷剩余音频并定稿。"""
        if self._closed:
            return
        self._closed = True

        # 冲刷流式缓冲
        if self._streaming_buffer:
            await self._flush_streaming(is_final=True)

        # 剩余不足一句的音频也要定稿，否则尾部内容会丢
        if self._current is not None and self._current.buffer:
            await self._finalize_current(force=True, reason="stop")

        # 等所有离线定稿任务收尾
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
        await self._snapshot_all_speakers()

        # 兜底再排空一次：正常路径下每个任务结束都会 resolve 自己的 seq，
        # 但任务被取消等异常情况下可能残留，不能让定稿烂在缓冲里。
        await self._drain_finals()

        await self._emit({"type": "closed", "session_id": self.session_id})
        # 会话指标落盘（失败不影响主链路）
        try:
            self.metrics.adaptive_profile = self._adaptive.snapshot()
            self.metrics.finish(duration_ms=self.duration_ms)
        except Exception:
            logger.exception("会话指标收尾失败 meeting=%s", self.meeting_id)

        if not self._has_signal and self._total_samples > 0:
            # 收到过音频但能量恒为 0：几乎可以确定是采集端问题，而不是 ASR 问题。
            # 这条日志是排查「会议结束但没有任何转写」的关键路标。
            logger.warning(
                "会话 %s 全程未采集到任何有效音频（已收 %.1fs 全零 PCM）——"
                "疑似麦克风未工作/设备被独占/静音开关打开；本次落库 %d 段",
                self.session_id,
                self._total_samples / SAMPLE_RATE,
                self._seq,
            )

        logger.info("会话 %s 已关闭，共 %d 段", self.session_id, self._seq)
        unregister_session(self)

    # ------------------------------------------------------------------
    # 活跃会话改名
    # ------------------------------------------------------------------
    def rename_speaker_sync(self, from_label: str, to_name: str) -> int:
        """把本会话的显示名改掉，并推一条 speaker_update 给前端。

        运行中的会话持有自己的聚类标签；如果只改数据库，后续定稿仍会
        继续输出旧标签——真实会议里改名"完全没效果"就是这个原因。
        """
        changed = self._speaker_resolver.rename(from_label, to_name)
        if changed and self._loop is not None and not self._loop.is_closed():
            payload = {
                "type": "speaker_update",
                "meeting_id": self.meeting_id,
                "from": from_label,
                "to": to_name,
            }
            asyncio.run_coroutine_threadsafe(self._emit(payload), self._loop)
        return changed

    # ------------------------------------------------------------------
    # 音频输入
    # ------------------------------------------------------------------

    async def push_audio(self, pcm: np.ndarray) -> None:
        """推入一块 16kHz 单声道 float32 PCM。"""
        if self._closed:
            return
        if pcm.dtype != np.float32:
            pcm = pcm.astype(np.float32)
        if pcm.ndim > 1:
            pcm = pcm.mean(axis=1)

        self._total_samples += len(pcm)

        # --- 路径 A：流式，边收边出 ---
        model_pcm = (
            self._agc.process(pcm, self._noise_floor) if self._agc is not None else pcm
        )
        self._streaming_buffer.append(model_pcm)
        await self._flush_streaming(is_final=False)

        # --- 路径 B：静音检测，攒够一句送离线定稿 ---
        # VAD 始终使用原始 PCM，避免 AGC 改变噪声判据
        await self._detect_boundary(pcm)

    async def _flush_streaming(self, *, is_final: bool) -> None:
        """把缓冲按固定步长喂给流式模型，产出 partial。"""
        if self._mode == "off":
            self._streaming_buffer.clear()
            return

        # 回声模式：不加载模型，按音频长度生成可辨识的占位草稿，
        # 用来在模型未就绪时验证「partial 上屏 → final 覆盖」这条链路。
        if self._mode == "echo":
            await self._flush_streaming_echo(is_final=is_final)
            return

        if not pool.is_ready("streaming"):
            return

        buffered = sum(len(c) for c in self._streaming_buffer)
        while buffered >= self._stream_chunk_samples or (is_final and buffered > 0):
            take = min(self._stream_chunk_samples, buffered)
            if take == 0:
                break
            chunk = self._take_streaming(take)
            buffered -= take

            delta = await self._run_streaming(chunk, is_final=False)
            if not delta:
                continue

            seg = self._ensure_current()
            # 关键：流式模型返回的是**增量**（本块新增的字），不是全文。
            # 早先直接 `seg.text = text` 会把之前累积的内容整段冲掉，
            # 结果屏幕上只剩最后一帧的 1~2 个字（表现为「识别出来只有一个字」）。
            # 这里改为按序拼接，并去掉模型偶发的前后空白。
            seg.streaming_text = _merge_delta(seg.streaming_text, delta)
            if seg.streaming_text != seg.text:
                seg.text = seg.streaming_text
                seg.partial_sent = True
                self.metrics.note_partial()
                await self._emit(
                    {
                        "type": "partial",
                        "segment_id": seg.segment_id,
                        "text": seg.text,
                        "start_ms": seg.start_ms,
                        "end_ms": self._elapsed_ms(),
                    }
                )

        if is_final and pool.is_ready("streaming"):
            await self._run_streaming(np.zeros(0, dtype=np.float32), is_final=True)

    async def _flush_streaming_echo(self, *, is_final: bool) -> None:
        """回声模式的草稿产出：按累计时长滚动出占位文本。"""
        buffered = sum(len(c) for c in self._streaming_buffer)
        while buffered >= self._stream_chunk_samples:
            self._take_streaming(self._stream_chunk_samples)
            buffered -= self._stream_chunk_samples
        # 结束时把尾巴也清掉，避免残留
        if is_final and buffered:
            self._take_streaming(buffered)

        elapsed = self._elapsed_ms() / 1000
        if elapsed <= 0:
            return

        seg = self._ensure_current()
        text = f"（回声模式）已采集 {elapsed:0.1f} 秒音频，正在等待停顿以定稿…"
        if text != seg.text:
            seg.text = text
            seg.partial_sent = True
            await self._emit(
                {
                    "type": "partial",
                    "segment_id": seg.segment_id,
                    "text": text,
                    "start_ms": seg.start_ms,
                    "end_ms": self._elapsed_ms(),
                }
            )

    def _take_streaming(self, count: int) -> np.ndarray:
        out: list[np.ndarray] = []
        need = count
        while need > 0 and self._streaming_buffer:
            head = self._streaming_buffer[0]
            if len(head) <= need:
                out.append(self._streaming_buffer.pop(0))
                need -= len(head)
            else:
                out.append(head[:need])
                self._streaming_buffer[0] = head[need:]
                need = 0
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)

    async def _run_streaming(self, chunk: np.ndarray, *, is_final: bool) -> str:
        model = pool._models.get("streaming")
        if model is None:
            return ""

        def _infer() -> str:
            try:
                res = model.generate(
                    input=chunk,
                    cache=self._cache,
                    is_final=is_final,
                    chunk_size=self._chunk_size,
                    encoder_chunk_look_back=self._enc_look_back,
                    decoder_chunk_look_back=self._dec_look_back,
                )
                if res and isinstance(res, list) and res[0].get("text"):
                    return res[0]["text"]
            except Exception:
                logger.exception("流式推理失败")
            return ""

        return await pool.run_in_executor(_infer)

    # ------------------------------------------------------------------
    # 断句与定稿
    # ------------------------------------------------------------------

    async def _detect_boundary(self, pcm: np.ndarray) -> None:
        """基于能量的简易静音检测（自适应噪声底）。

        VAD 模型的完整接入放在离线路径（模型自带的 vad_model），
        这里用轻量能量判据驱动断句时机，避免每块都跑一次神经网络。

        固定阈值在真实会议室里不可靠：空调、投影仪、风扇会把底噪抬到
        阈值附近，导致「该断的句子不断」，一路滚到 ``vad.max_segment_ms``
        才被强切（还可能切在词中间）。因此改为跟踪环境噪声底，
        用相对倍数判定有声/无声。
        """
        seg = self._ensure_current()
        seg.add_audio(pcm)

        # 自适应增益只用于 ASR/声纹副本；VAD 保持原始能量，
        # 避免把低电平底噪放大后误判成语音。
        energy = self._rms(pcm)
        if energy > 0:
            self._has_signal = True
            self._zero_seconds = 0.0
        else:
            self._zero_seconds += len(pcm) / SAMPLE_RATE
            await self._warn_if_no_signal()
        self._update_noise_floor(energy, len(pcm) / SAMPLE_RATE)
        profile = self._adaptive.observe_energy(
            energy,
            in_speech=self._in_speech,
            chunk_seconds=len(pcm) / SAMPLE_RATE,
        )
        if self._adaptive.enabled and profile.updated_blocks > 0:
            self._adaptive_gain = profile.gain
            self._start_threshold = profile.start_threshold
            self._continue_threshold = profile.continue_threshold
            self._silence_limit = int(profile.silence_ms / 1000 * SAMPLE_RATE)
            self._soft_segment_ms = profile.soft_segment_ms
            self._max_segment_ms = profile.max_segment_ms
            seg.adaptive_gain = profile.gain
            seg.profile_version = profile.version
            seg.noise_floor = profile.noise_floor or self._noise_floor
            seg.snr_db = profile.snr_db or self._last_snr_db
        if self._noise_warning_pending:
            self._noise_warning_pending = False
            await self._emit(
                {
                    "type": "noise_warning",
                    "message": (
                        "环境噪声偏高，识别质量可能下降。"
                        "建议关闭风扇/空调直吹、靠近麦克风或使用定向麦克风。"
                    ),
                    "snr_db": round(self._last_snr_db, 1),
                }
            )

        # 迟滞：进入语音用较高阈值，已经开口后改用较低阈值，
        # 避免句尾轻声被噪声底误判、也避免低电平噪声反复触发新句。
        if energy >= self._start_threshold:
            self._in_speech = True
            seg.had_speech = True
        threshold = (
            self._continue_threshold if self._in_speech else self._start_threshold
        )

        if energy < threshold:
            self._silence_samples += len(pcm)
        else:
            self._silence_samples = 0

        # 静音足够长 → 一句话结束
        effective_silence = self._silence_limit
        if self._short_merge_ms > 0 and seg.duration_ms < self._short_merge_ms:
            effective_silence = max(
                effective_silence,
                int(1.5 * SAMPLE_RATE),
            )
        if self._silence_samples >= effective_silence and seg.buffer:
            await self._finalize_current(reason="silence")

        # 软切分：已经足够长，且出现一次短停顿 → 在自然停顿处定稿
        elif (
            self._soft_segment_ms > 0
            and seg.duration_ms >= self._soft_segment_ms
            and self._silence_samples >= self._short_silence_samples
            and seg.buffer
        ):
            await self._finalize_current(reason="adaptive_silence")

        # 单段超长保护：到达上限后先等最多 1.5 秒，优先在局部低能量点切分；
        # 若一直连续说话才强制切，避免把词切成两半。
        elif seg.duration_ms >= self._max_segment_ms:
            self._over_max_ms += len(pcm) / SAMPLE_RATE * 1000
            local_valley = energy < max(
                self._continue_threshold * 1.35,
                self._noise_floor * 1.5,
            )
            if local_valley or self._over_max_ms >= 1500:
                await self._finalize_current(
                    reason="max_duration_valley"
                    if local_valley
                    else "max_duration"
                )

    @staticmethod
    def _rms(pcm: np.ndarray) -> float:
        if len(pcm) == 0:
            return 0.0
        return float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))

    async def _warn_if_no_signal(self) -> None:
        """会话进行中连续收到全零 PCM 时，主动推一条提醒给前端。

        为什么需要：前端虽然也有静音探测（`onSilence`），但那是基于 worklet
        已产出的块。如果某些情况下前端探测失灵（例如被暂停状态吞掉），
        服务端仍能在**收到音频**这一事实的基础上独立判断「音频流是空的」，
        两者互为独立证据，避免这种故障再次变成黑盒。

        阈值 5s 足够长，不会因为短暂的静音间隙误报。
        """
        if self._signal_warned or self._zero_seconds < 5.0:
            return
        self._signal_warned = True
        logger.warning(
            "会话 %s 已连续 %.1fs 收到全零 PCM —— 采集端疑似无信号"
            "（麦克风未选中/被独占/静音开关打开）",
            self.session_id,
            self._zero_seconds,
        )
        await self._emit(
            {
                "type": "signal_warning",
                "message": (
                    "已连续数秒未收到任何有效音频。请检查麦克风设备选择、"
                    "静音开关，以及是否有其它程序独占麦克风。"
                ),
                "silent_seconds": round(self._zero_seconds, 1),
            }
        )

    def _update_noise_floor(self, energy: float, seconds: float) -> None:
        """用近 ~13 秒能量的 20 分位数估计环境噪声底。

        旧实现只让噪声底下降、从不上升：空调/风扇变响后阈值仍停在旧值，
        低电平噪声会被当成语音，反复触发片段。这里改为滚动分位数 + 慢 EMA，
        既能跟随环境变化，又不会被一段讲话瞬间拉高。
        """
        if energy <= 0:
            return
        self._energy_hist.append(energy)
        if len(self._energy_hist) < 8:
            self._noise_floor = min(self._noise_floor, energy)
            return

        # 10 分位比 20 分位更不容易被“说话占多数”的窗口抬起来
        measured = float(np.percentile(self._energy_hist, 10))
        target = min(measured, self._max_threshold / 3)
        self._noise_floor = 0.92 * self._noise_floor + 0.08 * target
        self._noise_floor = max(
            min(self._noise_floor, self._max_threshold / 2), 1e-5
        )
        self._start_threshold = min(
            max(self._noise_floor * 3.5, 0.005), self._max_threshold
        )
        self._continue_threshold = min(
            max(self._noise_floor * 2.0, 0.003), self._max_threshold
        )

        speech = float(np.percentile(self._energy_hist, 90))
        # 窗口里有明显语音动态时，给阈值加一个“语音电平上限”，
        # 避免噪声底抬高后把正常说话一起滤掉；上限仍保持在噪声底之上，
        # 纯噪声窗口不会被放宽。
        dynamic = speech / (self._noise_floor + 1e-6)
        if dynamic >= 2.0:
            self._start_threshold = min(
                self._start_threshold,
                max(speech * 0.45, self._noise_floor * 1.6),
            )
            self._continue_threshold = min(
                self._continue_threshold,
                max(speech * 0.30, self._noise_floor * 1.2),
            )
        self._last_snr_db = float(
            20 * np.log10((speech + 1e-9) / (self._noise_floor + 1e-9))
        )
        self._noise_metric_tick += 1
        if self._noise_metric_tick % 5 == 0:
            self.metrics.note_noise(self._noise_floor, self._last_snr_db)

        if self._last_snr_db < 12.0:
            self._low_snr_seconds += seconds
        elif self._last_snr_db > 15.0:
            self._low_snr_seconds = 0.0
        if self._low_snr_seconds >= 5.0 and not self._noise_warned:
            self._noise_warned = True
            self._noise_warning_pending = True
            self.metrics.note_noise_warning()

    async def _finalize_current(
        self, *, force: bool = False, reason: str = "silence"
    ) -> None:
        """把当前句送离线模型定稿，并开新句。"""
        seg = self._current
        if seg is None:
            return

        # 长静音期间状态机会反复调用 finalize。没有语音、也没有流式草稿时
        # 直接丢掉缓冲，不拼接音频、不进入异步定稿队列；#231 这类会议每场
        # 会产生 200 多次这种纯静音 finalize。
        if not seg.had_speech and not seg.text:
            seg.buffer.clear()
            self._current = None
            self._over_max_ms = 0.0
            self._in_speech = False
            self._silence_samples = 0
            self.metrics.note_drop("no_speech")
            return

        audio = seg.take_audio()
        seg.end_ms = self._elapsed_ms()
        seg.end_perf = time.perf_counter()
        seg.finalize_reason = reason
        self._over_max_ms = 0.0
        self._in_speech = False
        self._silence_samples = 0

        self._current = None

        if len(audio) == 0:
            self.metrics.note_drop("empty")
            return

        # 太短的片段视为噪声，直接丢弃（force 时放宽下限）
        min_samples = SAMPLE_RATE * (0.1 if force else 0.2)
        if len(audio) < min_samples:
            self.metrics.note_drop("too_short")
            return

        # 整段从未越过语音起始阈值：只是底噪/空调声，force（stop）时也不能
        # 强行封成一条定稿，否则每次会议结尾都会多出一条噪声文本。
        if not seg.had_speech:
            self.metrics.note_drop("no_speech")
            return

        # 纯静音尾巴不产生片段：会议结束时尾部的静音填充会走到这里，
        # 若不拦掉，每场会议末尾都会多出一条无文字的空记录。
        if not force and self._is_silent(audio):
            self.metrics.note_drop("silent")
            return

        # 已经出过草稿但整段没有语音能量（例如只剩静音），同样丢弃
        if not seg.text and self._is_silent(audio):
            self.metrics.note_drop("silent")
            return

        # 有效语音太短的片段视为噪声（键盘声、杯子碰撞、关门声），直接丢弃。
        # 注意：不能只看整段 RMS —— 段里大部分是静音填充时 RMS 会被稀释，
        # 一个 10ms 的爆音配上 1.5s 静音，整段 RMS 仍有 0.017（高于阈值），
        # 结果就是每次敲键盘都多出一条转写记录。因此必须统计「有效语音时长」。
        speech_ms = self._speech_ms(audio)
        if speech_ms < self._min_speech_ms:
            self.metrics.note_drop("low_speech")
            logger.debug(
                "丢弃语音过短的片段 segment=%s (语音 %.0fms)",
                seg.segment_id,
                speech_ms,
            )
            return

        # 低电平噪声突发：能量贴近噪声底、有效语音又很短，直接丢弃。
        # 这类片段通常来自键盘、杯子碰撞或空调瞬态，送进 ASR 只会产生
        # 「嗯/对/这个」之类的垃圾文本，并污染说话人聚类。
        if (
            self._rms(audio) < max(self._noise_floor * 3.0, 0.003)
            and speech_ms < 600
        ):
            self.metrics.note_drop("noise_burst")
            logger.debug(
                "丢弃低电平噪声片段 segment=%s (rms=%.4f speech=%.0fms)",
                seg.segment_id,
                self._rms(audio),
                speech_ms,
            )
            return

        seg.seq = self._seq
        self._seq += 1
        seg.speech_ms = int(round(speech_ms))
        seg.audio_rms = round(self._rms(audio), 6)
        self._adaptive.observe_segment(int(len(audio) / SAMPLE_RATE * 1000))
        self.metrics.note_finalize(
            reason=reason, audio_ms=len(audio) / SAMPLE_RATE * 1000, speech_ms=speech_ms
        )

        task = asyncio.create_task(self._offline_and_emit(seg, audio))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    def _is_silent(self, audio: np.ndarray, threshold: float | None = None) -> bool:
        """整段音频是否基本无声。

        默认使用会话当前的**自适应阈值**，与断句判据保持一致；
        若两处用不同阈值，会出现「按 A 判据断句、按 B 判据丢弃」的矛盾。
        """
        if len(audio) == 0:
            return True
        return self._rms(audio) < (
            self._continue_threshold if threshold is None else threshold
        )

    def _speech_ms(self, audio: np.ndarray, threshold: float | None = None) -> float:
        """统计「超过能量阈值」的采样点数，换算成毫秒。

        按 20ms 一帧计算帧能量，只有帧能量过线才计入语音时长。
        这比整段 RMS 更能反映「真的有人说话」。
        """
        if len(audio) == 0:
            return 0.0
        thr = self._continue_threshold if threshold is None else threshold
        frame = int(SAMPLE_RATE * 0.02)  # 20ms
        total_frames = len(audio) // frame
        if total_frames == 0:
            # 不足一帧，用整体能量近似
            return (len(audio) / SAMPLE_RATE * 1000) if not self._is_silent(
                audio, thr
            ) else 0.0
        trimmed = audio[: total_frames * frame].reshape(total_frames, frame)
        frame_rms = np.sqrt(np.mean(trimmed.astype(np.float32) ** 2, axis=1))
        voiced = int(np.count_nonzero(frame_rms >= thr))
        return voiced * 20.0

    def _trim_silence_for_asr(self, audio: np.ndarray) -> np.ndarray:
        """只裁剪送 ASR/声纹的首尾静音，保留可配置余量。

        VAD 的时间轴、落库 start_ms/end_ms 和原始音频都不变；这里的输出只
        用于模型推理。真实会议 #231 中片段内语音只占 48.6%，长静音会稀释
        声纹向量并增加离线推理时间。
        """
        if not self._trim_silence_enabled or len(audio) == 0:
            return audio
        frame = int(SAMPLE_RATE * 0.02)
        total = len(audio) // frame
        if total < 8:
            return audio
        frames = audio[: total * frame].reshape(total, frame).astype(np.float32)
        energy = np.sqrt(np.mean(frames**2, axis=1))
        voiced = np.flatnonzero(energy >= self._continue_threshold)
        if voiced.size == 0:
            return audio
        margin = int(self._trim_margin_ms / 1000 * SAMPLE_RATE)
        start = max(0, int(voiced[0]) * frame - margin)
        end = min(len(audio), (int(voiced[-1]) + 1) * frame + margin)
        if end - start >= len(audio) * 0.9:
            return audio
        trimmed = np.ascontiguousarray(audio[start:end])
        self.metrics.note_trim(
            len(audio) / SAMPLE_RATE * 1000,
            len(trimmed) / SAMPLE_RATE * 1000,
        )
        return trimmed

    async def _offline_and_emit(self, seg: SegmentState, audio: np.ndarray) -> None:
        """离线定稿任务的入口：保证**按 seq 顺序**把定稿发出去。

        为什么要这层包装：定稿是用 `create_task` 并发跑的（离线推理与声纹
        抽取都是耗时调用），完成顺序天然可能乱序。实测出现过
        `seq = [3, 1, 5, 13, 9, 0, ...]` 这种发出顺序，字幕会前后颠倒。

        这里不改并发，只在出口处按 seq 排队：每个任务无论是否产出定稿，
        结束前都要 `_resolve_seq`，否则被丢弃的 seq 会永久卡住后续所有定稿。
        """
        try:
            await self._offline_compute(seg, audio)
        finally:
            await self._resolve_seq(seg.seq)

    async def _offline_compute(self, seg: SegmentState, audio: np.ndarray) -> None:
        """离线精确定稿，用同一个 segment_id 覆盖 partial。"""
        if self._mode == "off":
            return

        # 回声模式：不调用模型，直接给出带时长的占位定稿
        if self._mode == "echo":
            seconds = len(audio) / SAMPLE_RATE
            seg.text = f"（回声模式）本句约 {seconds:0.1f} 秒音频已定稿"
            seg.speaker = None
            seg.is_final = True
            seg.asr_source = "echo"
            await self._emit_final(seg)
            return

        if not pool.is_ready("offline"):
            # 离线模型未就绪时，用流式结果兜底，避免用户看到空白
            await self._emit_draft_fallback(seg, audio, "offline_model_not_ready")
            return

        # 先裁掉首尾静音，再施加自适应增益；原始音频与时间轴保持不变。
        asr_source = self._trim_silence_for_asr(audio)
        # 自适应增益先作用于 ASR/声纹副本；原始音频仍完整留存。
        asr_audio = (
            np.clip(asr_source * self._adaptive_gain, -1.0, 1.0)
            if self._adaptive_gain > 1.01
            else asr_source
        )
        if self._denoise_enabled or self._agc_enabled:
            def _prepare() -> np.ndarray:
                out = asr_audio
                if self._denoise_enabled:
                    out = spectral_denoise(
                        out, strength=self._denoise_strength
                    )
                if self._agc_enabled:
                    out = normalize_segment(out, target_rms=self._agc_target)
                return out

            asr_audio = await pool.run_in_executor(_prepare)

        def _infer() -> dict:
            from ..glossary import hotwords_text

            model = pool._models["offline"]
            try:
                return model.generate(
                    input=asr_audio,
                    batch_size_s=get_config("asr.batch_size_s"),
                    use_itn=get_config("use_itn"),
                    return_spk_res=self._spk_enabled,
                    sentence_timestamp=True,
                    hotword=hotwords_text(get_config("hotwords")),
                )
            except Exception:
                logger.exception("离线推理失败")
                return {}

        try:
            t_infer = time.perf_counter()
            res = await pool.run_in_executor(_infer) or {}
            seg.offline_ms = (time.perf_counter() - t_infer) * 1000
            text = ""

            if isinstance(res, list) and res:
                item = res[0]
                text = item.get("text", "") or ""
                # 注意：这里**故意不用** item["sentence_info"][*]["spk"]。
                # 那个 spk 是单次 generate() 内部的聚类下标，每次调用都从 0
                # 重新编号；而我们是「一句一调用」，于是每句都被独立聚成第 0 类，
                # 直接用会让所有片段的说话人都变成 0。改用下面的声纹方案。

            # 模型没给出文本时，不能把「未经验证的流式草稿」直接封成定稿。
            # 否则模型什么也没听清（空文本）时，屏幕上会出现一条以草稿内容为准的
            # 假定稿，用户无法分辨。此时只有确认段内确实有足够语音才降级使用草稿。
            if not text:
                if self._is_silent(audio) or self._speech_ms(audio) < self._min_speech_ms:
                    self.metrics.note_drop("offline_empty")
                    logger.debug("丢弃无文本且语音不足的定稿 segment=%s", seg.segment_id)
                    return
                if not seg.text:
                    self.metrics.note_drop("offline_empty_no_draft")
                    logger.debug("丢弃无文本的空定稿 segment=%s", seg.segment_id)
                    return
                seg.fallback = True
                seg.asr_source = "streaming_fallback"
                seg.asr_error = "offline_empty"
                logger.warning(
                    "离线模型返回空文本，降级使用流式草稿 segment=%s", seg.segment_id
                )

            # 兜底文本必须过质量门禁，避免把“个”“的户的资金对说说”
            # 这类声学乱码写成正式定稿。
            final_text = text
            if not final_text and seg.fallback and seg.text:
                final_text = self._accepted_fallback_text(seg)
                if not final_text:
                    return

            if final_text:
                from ..glossary import apply_terms

                seg.text = apply_terms(final_text)
                if text:
                    seg.asr_source = "offline"
                t_spk = time.perf_counter()
                resolved = await self._resolve_speaker(asr_audio, seg.start_ms)
                self._apply_speaker(seg, resolved)
                seg.speaker_ms = (time.perf_counter() - t_spk) * 1000
                self._maybe_snapshot_speaker(seg)
            seg.is_final = True
            await self._emit_final(seg)
            if self._translate_enabled and seg.text:
                task = asyncio.create_task(self._translate_segment(seg))
                self._translation_tasks.add(task)
                task.add_done_callback(self._translation_tasks.discard)
        except Exception:
            logger.exception("定稿流程异常 segment=%s", seg.segment_id)
            if not seg.is_final:
                await self._emit_draft_fallback(seg, audio, "offline_exception")

    def _accepted_fallback_text(self, seg: SegmentState) -> str | None:
        """校验流式草稿能否作为定稿兜底；不合格时记录指标并返回 None。"""
        if not seg.text:
            self.metrics.note_drop("offline_empty_no_draft")
            return None
        from ..text_quality import fallback_quality

        usable, reason = fallback_quality(seg.text)
        min_chars = int(get_config("asr.fallback_min_chars") or 4)
        if len(seg.text.strip()) < min_chars:
            usable, reason = False, "below_min_chars"
        if not usable:
            seg.asr_error = f"fallback_rejected:{reason}"
            self.metrics.note_fallback_rejected()
            self.metrics.note_drop("fallback_quality")
            logger.info(
                "拒收低质量草稿兜底 segment=%s reason=%s text=%r",
                seg.segment_id,
                reason,
                seg.text[:80],
            )
            return None
        return seg.text

    def _apply_speaker(
        self, seg: SegmentState, resolved: SpeakerResolution | None
    ) -> None:
        """把声纹判定写回片段，并维护上下文回填所需的近期说话人。"""
        if resolved is None:
            return
        seg.speaker = resolved.label
        seg.speaker_key = resolved.key
        seg.speaker_score = round(resolved.score, 4)
        seg.speaker_source = resolved.source
        self._speaker_context.record(resolved.label, resolved.key, seg.end_ms)

    def _maybe_snapshot_speaker(self, seg: SegmentState) -> None:
        """每 N 段把匿名簇当前质心写回临时声纹。"""
        if (
            seg.speaker_source != "cluster"
            or not seg.speaker_key
            or not seg.speaker_key.startswith("spk_")
        ):
            return
        self._speaker_snapshot_count += 1
        if self._speaker_snapshot_count % self._speaker_snapshot_every:
            return
        vector = self._speaker_resolver.cluster_vector(seg.speaker_key)
        if vector is None:
            return
        task = asyncio.create_task(
            self._save_speaker_snapshot(
                seg.speaker_key, seg.speaker or "说话人", vector
            )
        )
        self._speaker_snapshot_tasks.add(task)
        task.add_done_callback(self._speaker_snapshot_tasks.discard)

    async def _save_speaker_snapshot(
        self, speaker_key: str, label: str, vector: np.ndarray
    ) -> None:
        try:
            await pool.run_in_executor(
                lambda: save_discovered(
                    label,
                    vector,
                    self.meeting_id,
                    speaker_key=speaker_key,
                    meeting_id=self.meeting_id,
                )
            )
        except Exception:
            logger.exception(
                "临时声纹快照失败 meeting=%s key=%s",
                self.meeting_id,
                speaker_key,
            )

    async def _snapshot_all_speakers(self) -> None:
        if self._speaker_snapshot_tasks:
            await asyncio.gather(
                *self._speaker_snapshot_tasks, return_exceptions=True
            )
        for label, key, vector in self._speaker_resolver.cluster_items():
            if key.startswith("spk_"):
                await self._save_speaker_snapshot(key, label, vector)

    async def _emit_draft_fallback(
        self, seg: SegmentState, audio: np.ndarray, reason: str
    ) -> None:
        """离线模型不可用时的兜底定稿。

        兜底稿同样要过文本质量门禁并执行声纹判定；否则模型加载失败或推理
        异常时，界面上会重新出现“个”“的户的资金对说说”这类乱码，且所有
        片段都退化成未知说话人，正好绕开本轮修复。
        """
        if seg.is_final:
            return
        final_text = self._accepted_fallback_text(seg)
        if not final_text:
            return
        from ..glossary import apply_terms

        seg.text = apply_terms(final_text)
        seg.fallback = True
        seg.asr_source = "streaming_fallback"
        seg.asr_error = reason
        t_spk = time.perf_counter()
        resolved = await self._resolve_speaker(audio, seg.start_ms)
        self._apply_speaker(seg, resolved)
        seg.speaker_ms = (time.perf_counter() - t_spk) * 1000
        self._maybe_snapshot_speaker(seg)
        seg.is_final = True
        await self._emit_final(seg)
        if self._translate_enabled and seg.text:
            task = asyncio.create_task(self._translate_segment(seg))
            self._translation_tasks.add(task)
            task.add_done_callback(self._translation_tasks.discard)

    async def _translate_segment(self, seg: SegmentState) -> None:
        """定稿后台翻译；失败只记日志，不影响字幕。"""
        try:
            from ..llm.client import LLMClient

            client = LLMClient()
            if not client.configured:
                return
            model = str(get_config("llm.translate_model") or "") or client.model
            raw = await client.chat(
                [
                    {
                        "role": "user",
                        "content": (
                            f"把下面的会议转写翻译成{self._translate_lang}，"
                            "只输出译文，不要解释：\n" + seg.text
                        ),
                    }
                ],
                model=model,
                meeting_id=self.meeting_id,
                purpose="translate",
            )
            text = (raw or "").strip()
            if text:
                await self._emit(
                    {
                        "type": "translation",
                        "segment_id": seg.segment_id,
                        "text": text,
                    }
                )
        except Exception:
            logger.exception("字幕翻译失败 segment=%s", seg.segment_id)

    async def _resolve_speaker(
        self, audio: np.ndarray, start_ms: int = 0
    ) -> SpeakerResolution | None:
        """用 CAM++ 声纹向量判定本句说话人。

        - 命中已录入声纹 → 返回 (姓名, vp_<id>)
        - 否则与本会议已出现的说话人聚类 → 返回 (「说话人 N」, 稳定 key)

        失败一律返回 None（前端会显示为「未知」），绝不因为声纹问题
        影响转写主流程。
        """
        if not self._spk_enabled:
            return None
        # 有效语音太短的句子不判说话人：实测「别。」这类一两个字的片段，
        # 声纹向量与任何人都对不上（相似度仅 0.15~0.26），会给每句话都
        # 新开一个说话人类，把单人的会议拆成「说话人 1/2/3/4」。
        # 这里按「有效语音时长」而不是音频长度判断——段尾的静音填充不算数。
        # 阈值可配（spk.min_speech_ms），设 0 表示不限制。
        speech_ms = self._speech_ms(audio)
        if self._spk_min_speech_ms > 0 and speech_ms < self._spk_min_speech_ms:
            # 短句只做「匹配已有说话人」，不新建类别、不更新质心。
            if self._spk_short_ms <= 0 or speech_ms >= self._spk_short_ms:
                emb = await pool.run_in_executor(lambda: extract_embedding(audio))
                hit = (
                    self._speaker_resolver.match_known_meta(
                        emb, self._spk_short_threshold
                    )
                    if emb is not None
                    else None
                )
                # 只有真正发起比对才计入，便于下次长测评估命中率
                self.metrics.note_short_match(hit is not None)
                if hit:
                    logger.debug(
                        "短句回填说话人 segment=%s (%.0fms -> %s)",
                        self.session_id,
                        speech_ms,
                        hit.label,
                    )
                    return hit
            logger.debug(
                "语音过短，跳过说话人判定 segment=%s (%.0fms)",
                self.session_id,
                speech_ms,
            )
            return self._context_speaker(start_ms, source="context_short")
        try:
            emb = await pool.run_in_executor(lambda: extract_embedding(audio))
            if emb is None:
                return self._context_speaker(start_ms, source="context_no_embedding")
            result = self._speaker_resolver.resolve_with_meta(emb)

            # 把本场新聚出来的「陌生说话人」落库为待命名声纹。
            # 以 (meeting_id, speaker_key) 为命名空间，不再按全局名字去重；
            # 否则「说话人 1」已存在会让本场向量永远不入库。
            while self._speaker_resolver.new_clusters:
                new_key, new_label, vec = self._speaker_resolver.new_clusters.pop(0)
                await pool.run_in_executor(
                    lambda k=new_key, n=new_label, v=vec: save_discovered(
                        n,
                        v,
                        self.meeting_id,
                        speaker_key=k,
                        meeting_id=self.meeting_id,
                    )
                )
            return result
        except Exception:
            logger.exception("说话人判定失败 segment=%s", self.session_id)
            return self._context_speaker(start_ms, source="context_error")

    def _context_speaker(
        self, start_ms: int, *, source: str
    ) -> SpeakerResolution | None:
        """短句/向量失败时，仅在最近上下文高度一致时沿用邻近说话人。"""
        if not bool(get_config("spk.context_fallback_enabled")):
            return None
        max_gap = int(get_config("spk.context_max_gap_ms") or 5000)
        return self._speaker_context.resolve(
            start_ms,
            max_gap_ms=max_gap,
            source=source,
        )

    async def _emit_final(self, seg: SegmentState) -> None:
        """把定稿放进按 seq 排序的待发缓冲（真正发出在 _drain_finals）。"""
        ready_at = time.perf_counter()
        payload = {
            "type": "final",
            "session_id": self.session_id,
            "segment_id": seg.segment_id,
            "text": seg.text,
            "start_ms": seg.start_ms,
            "end_ms": seg.end_ms,
            "speaker": seg.speaker,
            "speaker_key": seg.speaker_key,
            "speaker_score": seg.speaker_score,
            "speaker_source": seg.speaker_source,
            "speaker_low_confidence": is_low_confidence(
                seg.speaker_score,
                seg.speech_ms,
                seg.text,
                source=seg.speaker_source,
            ),
            "asr_source": seg.asr_source,
            "asr_error": seg.asr_error,
            "finalize_reason": seg.finalize_reason,
            "speech_ms": seg.speech_ms,
            "audio_rms": seg.audio_rms,
            "noise_floor": seg.noise_floor,
            "snr_db": seg.snr_db,
            "adaptive_gain": seg.adaptive_gain,
            "profile_version": seg.profile_version,
            "seq": seg.seq,
        }
        self._final_order.submit(seg.seq, payload, ready_at)
        self._metric_pending[seg.seq] = (seg, ready_at)

    async def _resolve_seq(self, seq: int) -> None:
        """标记某个 seq 已处理完（可能没有定稿），然后按序发出可发的部分。"""
        self._final_order.resolve(seq)
        await self._drain_finals()

    async def _emit_ready(self, seq: int, item: PendingFinal) -> None:
        payload = item.payload
        metric = self._metric_pending.pop(seq, None)
        if payload is None:
            return
        if metric is not None:
            seg, ready_at = metric
            now = time.perf_counter()
            # 定稿延迟从「一句话真正说完」算起：silence 断句时，
            # end_perf 已包含 vad.silence_ms 的静音尾巴，这里补偿回来。
            tail_ms = (
                self._silence_limit / SAMPLE_RATE * 1000
                if seg.finalize_reason == "silence"
                else 0.0
            )
            self.metrics.note_final(
                offline_ms=seg.offline_ms,
                speaker_ms=seg.speaker_ms,
                queue_wait_ms=(now - ready_at) * 1000,
                final_latency_ms=(now - seg.end_perf) * 1000 + tail_ms,
                chars=len(seg.text or ""),
                speaker=seg.speaker,
                fallback=seg.fallback,
                duration_ms=seg.end_ms - seg.start_ms,
                asr_source=seg.asr_source,
                finalize_reason=seg.finalize_reason,
                speaker_source=seg.speaker_source,
                speaker_score=seg.speaker_score,
                speaker_low_confidence=is_low_confidence(
                    seg.speaker_score,
                    seg.speech_ms,
                    seg.text,
                    source=seg.speaker_source,
                ),
                segment_id=seg.segment_id,
            )
        await self._emit(payload)

    async def _drain_finals(self) -> None:
        """按 seq 顺序发定稿；慢片段阻塞超过阈值时允许后续片段先发。

        严格有序会让一个 20–30s 的离线任务堵住后面所有字幕（真实长测里
        队列等待 p95 3.7s、最大 8.5s）。超时后先发已就绪的片段，前端按
        start_ms 插入，观感仍然按时间轴排列。
        """
        while True:
            result = self._final_order.take_next(time.perf_counter())
            if result is None:
                break
            seq, item, out_of_order = result
            await self._emit_ready(seq, item)
            if out_of_order:
                self.metrics.note_out_of_order()

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def _ensure_current(self) -> SegmentState:
        if self._current is None:
            self._current = SegmentState(
                segment_id=f"s-{self.session_id}-{self._seq}-{uuid.uuid4().hex[:4]}",
                start_ms=self._elapsed_ms(),
            )
        return self._current

    def _elapsed_ms(self) -> int:
        """会议全局时间轴上的当前毫秒数（含重连偏移）。"""
        return self._start_offset_ms + int(self._total_samples / SAMPLE_RATE * 1000)

    @property
    def duration_ms(self) -> int:
        """本段会话自身的时长（不含偏移）。"""
        return int(self._total_samples / SAMPLE_RATE * 1000)

    @property
    def meeting_end_ms(self) -> int:
        """本段会话结束时，会议全局时间轴上的位置。"""
        return self._start_offset_ms + self.duration_ms

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def adaptive_snapshot(self) -> dict:
        return self._adaptive.snapshot()

    def speaker_cluster_vector(self, speaker_key: str) -> np.ndarray | None:
        return self._speaker_resolver.cluster_vector(speaker_key)


# ---------------------------------------------------------------------------
# 活跃会话注册表：会议进行中改名时同步到运行中的 resolver
# ---------------------------------------------------------------------------

_SESSIONS_BY_MEETING: dict[int, set[StreamSession]] = {}
_REGISTRY_LOCK = threading.Lock()


def register_session(session: StreamSession) -> None:
    with _REGISTRY_LOCK:
        _SESSIONS_BY_MEETING.setdefault(session.meeting_id, set()).add(session)


def unregister_session(session: StreamSession) -> None:
    with _REGISTRY_LOCK:
        bucket = _SESSIONS_BY_MEETING.get(session.meeting_id)
        if bucket is None:
            return
        bucket.discard(session)
        if not bucket:
            _SESSIONS_BY_MEETING.pop(session.meeting_id, None)


def rename_active_speaker(meeting_id: int, from_label: str, to_name: str) -> int:
    """把某场会议所有活跃会话的旧标签同步改成新姓名。"""
    with _REGISTRY_LOCK:
        sessions = list(_SESSIONS_BY_MEETING.get(meeting_id, ()))
    return sum(s.rename_speaker_sync(from_label, to_name) for s in sessions)


def active_speaker_vector(meeting_id: int, speaker_key: str) -> np.ndarray | None:
    """取活跃会话里某个 speaker_key 的当前质心，用于纠正错误匹配。"""
    with _REGISTRY_LOCK:
        sessions = list(_SESSIONS_BY_MEETING.get(meeting_id, ()))
    for session in sessions:
        vector = session.speaker_cluster_vector(speaker_key)
        if vector is not None:
            return vector
    return None
