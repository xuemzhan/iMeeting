"""会话级运行指标：只观测，不改变转写行为。

为什么需要
----------
真实长会上，``segment.created_at`` 是「定稿落库时间」，而当前实现是在会议
结束时批量落库，所以它**不能**用来衡量定稿延迟。断句原因、离线推理耗时、
声纹耗时、排队等待、说话人覆盖率也都无法从现有日志还原。

这里在内存里按会话累计这些量，会议结束时：

1. 打一条 ``meeting_metrics {json}`` 结构化日志；
2. 写一份 ``data/metrics/meeting-<id>.json``，供后续离线分析脚本读取。

任何采集失败都不得影响转写主链路（全部静默降级）。
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def metrics_dir() -> Path:
    """指标输出目录；测试可用 ``MEETING_METRICS_DIR`` 覆盖。"""
    override = os.getenv("MEETING_METRICS_DIR")
    if override:
        return Path(override)
    # imeeting/engine/metrics.py → parents[0]=engine, [1]=app, [2]=backend
    return Path(__file__).resolve().parents[2] / "data" / "metrics"


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[idx]


def describe(values: list[float]) -> dict[str, float | int]:
    """分布摘要（毫秒）。"""
    if not values:
        return {"n": 0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "max": 0.0, "mean": 0.0}
    return {
        "n": len(values),
        "p50": round(statistics.median(values), 1),
        "p90": round(_percentile(values, 0.9), 1),
        "p95": round(_percentile(values, 0.95), 1),
        "max": round(max(values), 1),
        "mean": round(statistics.fmean(values), 1),
    }


def _rss_mb() -> float | None:
    try:
        import psutil  # 可选依赖：缺失时只丢这一个指标

        return round(psutil.Process().memory_info().rss / 1024 / 1024, 1)
    except Exception:
        return None


class MeetingMetrics:
    """一个会议会话的指标累加器。"""

    def __init__(self, meeting_id: int, session_id: str, mode: str) -> None:
        self.meeting_id = meeting_id
        self.session_id = session_id
        self.mode = mode
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._cpu0 = time.process_time()
        self._wall0 = time.perf_counter()

        self.partial_events = 0
        self.segments = 0
        self.drops: dict[str, int] = {}
        self.finalize_reasons: dict[str, int] = {}
        self.offline_ms: list[float] = []
        self.speaker_ms: list[float] = []
        self.queue_wait_ms: list[float] = []
        self.final_latency_ms: list[float] = []
        self.audio_ms_total = 0.0
        self.speech_ms_total = 0.0
        self.chars_total = 0
        self.unknown_speaker = 0
        self.labeled_speaker = 0
        self.fallback_final = 0
        self.fallback_details: list[dict[str, Any]] = []
        self.fallback_rejected = 0
        self.asr_sources: dict[str, int] = {}
        self.speaker_sources: dict[str, int] = {}
        self.speaker_scores: list[float] = []
        self.low_confidence_speakers = 0
        self.trimmed_segments = 0
        self.trim_original_ms = 0.0
        self.trim_kept_ms = 0.0
        self.long_segments = 0
        self.tiny_segments = 0
        self.short_match_attempts = 0
        self.short_match_hits = 0
        self.out_of_order_emits = 0
        self.noise_floor_values: list[float] = []
        self.snr_db_values: list[float] = []
        self.noise_warnings = 0
        self.peak_rss_mb = 0.0
        self.adaptive_profile: dict[str, Any] = {}

    # ---- 事件 -----------------------------------------------------------
    def note_partial(self) -> None:
        self.partial_events += 1

    def note_drop(self, reason: str) -> None:
        self.drops[reason] = self.drops.get(reason, 0) + 1

    def note_short_match(self, hit: bool) -> None:
        """短句回填尝试/命中计数（判断该策略是否真的有效）。"""
        self.short_match_attempts += 1
        if hit:
            self.short_match_hits += 1

    def note_out_of_order(self) -> None:
        """有界重排触发次数（慢片段阻塞后允许后续片段先发）。"""
        self.out_of_order_emits += 1

    def note_noise(self, noise_floor: float, snr_db: float) -> None:
        self.noise_floor_values.append(noise_floor)
        self.snr_db_values.append(snr_db)

    def note_noise_warning(self) -> None:
        self.noise_warnings += 1

    def note_trim(self, original_ms: float, kept_ms: float) -> None:
        """统一定稿前静音裁剪的收益，供下一场真实会议核对。"""
        if kept_ms <= 0 or kept_ms >= original_ms:
            return
        self.trimmed_segments += 1
        self.trim_original_ms += original_ms
        self.trim_kept_ms += kept_ms

    def note_fallback_rejected(self) -> None:
        self.fallback_rejected += 1

    def note_finalize(self, *, reason: str, audio_ms: float, speech_ms: float) -> None:
        self.finalize_reasons[reason] = self.finalize_reasons.get(reason, 0) + 1
        self.audio_ms_total += audio_ms
        self.speech_ms_total += speech_ms

    def note_final(
        self,
        *,
        offline_ms: float,
        speaker_ms: float,
        queue_wait_ms: float,
        final_latency_ms: float,
        chars: int,
        speaker: str | None,
        fallback: bool,
        duration_ms: float = 0.0,
        asr_source: str = "",
        finalize_reason: str = "",
        speaker_source: str = "",
        speaker_score: float = 0.0,
        speaker_low_confidence: bool = False,
        segment_id: str = "",
    ) -> None:
        self.segments += 1
        self.offline_ms.append(offline_ms)
        self.speaker_ms.append(speaker_ms)
        self.queue_wait_ms.append(queue_wait_ms)
        self.final_latency_ms.append(final_latency_ms)
        self.chars_total += chars
        if speaker:
            self.labeled_speaker += 1
        else:
            self.unknown_speaker += 1
        if fallback:
            self.fallback_final += 1
            if len(self.fallback_details) < 100:
                self.fallback_details.append(
                    {
                        "segment_id": segment_id,
                        "reason": finalize_reason,
                        "duration_ms": round(duration_ms, 1),
                        "chars": chars,
                    }
                )
        if asr_source:
            self.asr_sources[asr_source] = self.asr_sources.get(asr_source, 0) + 1
        if speaker_source:
            self.speaker_sources[speaker_source] = (
                self.speaker_sources.get(speaker_source, 0) + 1
            )
        if speaker_score > 0:
            self.speaker_scores.append(speaker_score)
        if speaker_low_confidence:
            self.low_confidence_speakers += 1
        if duration_ms > 20000:
            self.long_segments += 1
        if chars <= 5:
            self.tiny_segments += 1
        rss = _rss_mb()
        if rss is not None:
            self.peak_rss_mb = max(self.peak_rss_mb, rss)

    # ---- 收尾 -----------------------------------------------------------
    def summary(self, *, duration_ms: int) -> dict[str, Any]:
        audio_seconds = self.audio_ms_total / 1000
        return {
            "event": "meeting_metrics",
            "meeting_id": self.meeting_id,
            "session_id": self.session_id,
            "mode": self.mode,
            "started_at": self.started_at,
            "duration_ms": duration_ms,
            "wall_seconds": round(time.perf_counter() - self._wall0, 1),
            "segments": self.segments,
            "partial_events": self.partial_events,
            "finalize_reasons": dict(self.finalize_reasons),
            "drops": dict(self.drops),
            "speech_seconds": round(self.speech_ms_total / 1000, 1),
            "segment_audio_seconds": round(audio_seconds, 1),
            "speech_ratio": round(self.speech_ms_total / self.audio_ms_total, 3)
            if self.audio_ms_total
            else None,
            "chars": self.chars_total,
            "unknown_speaker": self.unknown_speaker,
            "labeled_speaker": self.labeled_speaker,
            "short_match_attempts": self.short_match_attempts,
            "short_match_hits": self.short_match_hits,
            "out_of_order_emits": self.out_of_order_emits,
            "noise_floor_p50": round(
                statistics.median(self.noise_floor_values), 5
            )
            if self.noise_floor_values
            else None,
            "snr_db_p50": round(statistics.median(self.snr_db_values), 1)
            if self.snr_db_values
            else None,
            "snr_db_min": round(min(self.snr_db_values), 1)
            if self.snr_db_values
            else None,
            "noise_warnings": self.noise_warnings,
            "fallback_final": self.fallback_final,
            "fallback_rejected": self.fallback_rejected,
            "fallback_details": self.fallback_details,
            "asr_sources": dict(self.asr_sources),
            "speaker_sources": dict(self.speaker_sources),
            "speaker_score_p50": round(
                statistics.median(self.speaker_scores), 3
            )
            if self.speaker_scores
            else None,
            "low_confidence_speakers": self.low_confidence_speakers,
            "trimmed_segments": self.trimmed_segments,
            "trim_original_seconds": round(self.trim_original_ms / 1000, 1),
            "trim_kept_seconds": round(self.trim_kept_ms / 1000, 1),
            "trim_saved_ratio": round(
                1 - self.trim_kept_ms / self.trim_original_ms, 3
            )
            if self.trim_original_ms > 0
            else None,
            "long_segments_gt20s": self.long_segments,
            "tiny_segments_le5chars": self.tiny_segments,
            "offline_ms": describe(self.offline_ms),
            "speaker_ms": describe(self.speaker_ms),
            "queue_wait_ms": describe(self.queue_wait_ms),
            "final_latency_ms": describe(self.final_latency_ms),
            "cpu_seconds": round(time.process_time() - self._cpu0, 1),
            "peak_rss_mb": self.peak_rss_mb or None,
            "adaptive_profile": self.adaptive_profile,
        }

    def finish(self, *, duration_ms: int) -> dict[str, Any]:
        summary = self.summary(duration_ms=duration_ms)
        try:
            target_dir = metrics_dir()
            target_dir.mkdir(parents=True, exist_ok=True)
            # 按会话命名：同一会议重连多次时，旧实现会互相覆盖，
            # 40 分钟主会话的指标就是这样丢的。
            path = target_dir / f"meeting-{self.meeting_id}-{self.session_id}.json"
            path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            logger.exception("会议指标写盘失败 meeting=%s", self.meeting_id)
        try:
            logger.info(
                "meeting_metrics %s", json.dumps(summary, ensure_ascii=False, default=str)
            )
        except Exception:
            logger.exception("会议指标日志输出失败 meeting=%s", self.meeting_id)
        return summary
