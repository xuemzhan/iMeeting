"""会话级指标采集的回归测试。"""

from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest

from imeeting.engine import pool
from imeeting.engine.metrics import MeetingMetrics, describe, metrics_dir
from imeeting.engine.streaming import SAMPLE_RATE, StreamSession


def tone(seconds: float, amp: float = 0.3, freq: float = 440.0) -> np.ndarray:
    n = int(SAMPLE_RATE * seconds)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32)


class Collector:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, payload: dict) -> None:
        self.messages.append(payload)

    def of(self, kind: str) -> list[dict]:
        return [m for m in self.messages if m["type"] == kind]


@pytest.fixture
def env(monkeypatch):
    from imeeting.settings import config_store

    saved_models = dict(pool._models)
    saved_keys = dict(pool._build_keys)
    pool._models.clear()
    pool._build_keys.clear()
    config_store._db_overrides = {}
    yield
    pool._models.clear()
    pool._models.update(saved_models)
    pool._build_keys.clear()
    pool._build_keys.update(saved_keys)
    config_store._db_overrides = {}


def test_describe_of_empty_and_values():
    assert describe([])["n"] == 0
    stats = describe([10.0, 20.0, 30.0, 40.0])
    assert stats["n"] == 4
    assert stats["p50"] == 25.0
    assert stats["max"] == 40.0


def test_default_metrics_dir_is_backend_data(monkeypatch):
    monkeypatch.delenv("MEETING_METRICS_DIR", raising=False)
    path = metrics_dir()
    assert path.parent.name == "data"
    assert path.parent.parent.name == "backend"
    assert path.name == "metrics"


def test_metrics_summary_counts():
    m = MeetingMetrics(7, "sess", "normal")
    m.note_partial()
    m.note_short_match(True)
    m.note_short_match(False)
    m.note_noise(0.0015, 18.9)
    m.note_noise(0.0020, 15.0)
    m.note_noise_warning()
    m.note_finalize(reason="silence", audio_ms=3000, speech_ms=2000)
    m.note_final(
        offline_ms=120,
        speaker_ms=30,
        queue_wait_ms=1,
        final_latency_ms=700,
        chars=12,
        speaker=None,
        fallback=True,
    )
    summary = m.summary(duration_ms=5000)
    assert summary["segments"] == 1
    assert summary["finalize_reasons"] == {"silence": 1}
    assert summary["unknown_speaker"] == 1
    assert summary["fallback_final"] == 1
    assert summary["short_match_attempts"] == 2
    assert summary["short_match_hits"] == 1
    assert summary["snr_db_min"] == 15.0
    assert summary["noise_warnings"] == 1
    assert summary["noise_floor_p50"] is not None
    assert summary["speech_ratio"] == pytest.approx(0.667, abs=0.002)
    assert summary["final_latency_ms"]["p50"] == 700.0


def test_metrics_low_confidence_and_trim_counts():
    m = MeetingMetrics(8, "sess", "normal")
    m.note_trim(4000, 2000)
    m.note_final(
        offline_ms=100,
        speaker_ms=20,
        queue_wait_ms=0,
        final_latency_ms=500,
        chars=5,
        speaker="陈建峰",
        fallback=False,
        speaker_score=0.63,
        speaker_low_confidence=True,
    )
    summary = m.summary(duration_ms=4000)
    assert summary["low_confidence_speakers"] == 1
    assert summary["trimmed_segments"] == 1
    assert summary["trim_original_seconds"] == 4.0
    assert summary["trim_kept_seconds"] == 2.0
    assert summary["trim_saved_ratio"] == 0.5


def test_echo_session_writes_metrics_file(env, monkeypatch, tmp_path):
    monkeypatch.setenv("ASR_MODE", "echo")
    monkeypatch.setenv("MEETING_METRICS_DIR", str(tmp_path))

    async def run():
        c = Collector()
        s = StreamSession(meeting_id=42, emit=c)
        await s.start()
        await s.push_audio(tone(1.0))
        await s.push_audio(silence(1.2))
        await s.close()
        return c

    c = asyncio.run(run())
    assert c.of("final"), "echo 模式应产出定稿"
    files = sorted(tmp_path.glob("meeting-42-*.json"))
    assert files, "指标文件应按会议+会话命名"
    path = files[0]
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["meeting_id"] == 42
    assert data["segments"] == len(c.of("final"))
    assert data["final_latency_ms"]["n"] == data["segments"]
    assert data["final_latency_ms"]["max"] >= 0
    assert data["finalize_reasons"].get("silence", 0) >= 1
