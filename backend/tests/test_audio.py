"""音频留存测试。

覆盖三类风险：

1. **WAV 头正确性** —— 头写错则文件根本播不了，且往往到用户端才发现。
2. **失败不影响转写** —— 落盘是附加能力，磁盘满/权限错都不能拖垮主链路。
3. **过期清理与越权删除** —— 清理要真删掉过期文件、保留未过期；
   删除接口不能被构造路径利用去删数据目录外的文件。
"""

from __future__ import annotations

import struct
import time
from pathlib import Path

import numpy as np
import pytest

from imeeting.engine import audio


@pytest.fixture
def audio_tmp(tmp_path, monkeypatch):
    """把音频目录指到临时目录，绝不碰真实 data/。"""
    d = tmp_path / "audio"
    d.mkdir()
    monkeypatch.setattr(audio, "audio_dir", lambda: d)
    return d


def _read_wav_header(path: Path) -> dict:
    raw = path.read_bytes()
    riff, size, wave, fmt, fmt_len, audio_fmt, channels, rate, byte_rate, align, bits = struct.unpack(
        "<4sI4s4sIHHIIHH", raw[:36]
    )
    assert riff == b"RIFF"
    assert wave == b"WAVE"
    assert fmt == b"fmt "
    return {
        "riff_size": size,
        "fmt_len": fmt_len,
        "audio_fmt": audio_fmt,
        "channels": channels,
        "rate": rate,
        "byte_rate": byte_rate,
        "align": align,
        "bits": bits,
        "data_tag": raw[36:40],
        "data_size": struct.unpack("<I", raw[40:44])[0],
        "total_bytes": len(raw),
    }


class TestWavHeader:
    def test_header_is_valid_16k_mono_16bit(self, audio_tmp, monkeypatch):
        from imeeting.settings import config_store

        config_store._db_overrides = {"storage.audio_retention_days": 7}

        sink = audio.AudioSink(1)
        sink.push(np.zeros(16000, dtype=np.float32))
        path = sink.close()

        assert path is not None and path.exists()
        h = _read_wav_header(path)
        assert h["audio_fmt"] == 1, "必须是 PCM"
        assert h["channels"] == 1, "必须单声道"
        assert h["rate"] == 16000, "必须 16kHz"
        assert h["bits"] == 16, "必须 16bit"
        assert h["byte_rate"] == 16000 * 2
        assert h["align"] == 2
        assert h["data_tag"] == b"data"

    def test_sizes_are_consistent(self, audio_tmp):
        """RIFF/data 长度字段必须与实际字节数自洽，否则播放器会截断。"""
        from imeeting.settings import config_store

        config_store._db_overrides = {"storage.audio_retention_days": 7}

        sink = audio.AudioSink(2)
        sink.push(np.zeros(8000, dtype=np.float32))
        sink.push(np.zeros(4000, dtype=np.float32))
        path = sink.close()

        h = _read_wav_header(path)
        data_bytes = 12000 * 2  # 12000 采样点 × 16bit
        assert h["data_size"] == data_bytes
        assert h["riff_size"] == 36 + data_bytes
        assert h["total_bytes"] == 44 + data_bytes

    def test_amplitude_survives_roundtrip(self, audio_tmp):
        """写入的波形要能原样读回（验证 float→int16 转换没搞反/没削顶）。"""
        import soundfile as sf

        from imeeting.settings import config_store

        config_store._db_overrides = {"storage.audio_retention_days": 7}

        t = np.arange(16000, dtype=np.float32) / 16000
        wave = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        sink = audio.AudioSink(3)
        sink.push(wave)
        path = sink.close()

        read_back, sr = sf.read(str(path), dtype="float32")
        assert sr == 16000
        assert len(read_back) == len(wave)
        # int16 量化误差上限约 1/32767，留一点余量
        assert np.max(np.abs(read_back - wave)) < 1e-3

    def test_clipping_is_handled(self, audio_tmp):
        """超出 [-1,1] 的样本必须被削顶而不是整数溢出回绕。"""
        import soundfile as sf

        from imeeting.settings import config_store

        config_store._db_overrides = {"storage.audio_retention_days": 7}

        sink = audio.AudioSink(4)
        sink.push(np.array([2.0, -2.0, 0.0], dtype=np.float32))
        path = sink.close()

        read_back, _ = sf.read(str(path), dtype="float32")
        assert read_back[0] == pytest.approx(1.0, abs=1e-3)
        assert read_back[1] == pytest.approx(-1.0, abs=1e-3)


class TestDisabled:
    def test_zero_days_means_no_file(self, audio_tmp):
        from imeeting.settings import config_store

        config_store._db_overrides = {"storage.audio_retention_days": 0}

        sink = audio.AudioSink(5)
        assert sink.enabled is False
        sink.push(np.zeros(16000, dtype=np.float32))
        assert sink.close() is None
        assert list(audio_tmp.glob("*.wav")) == []

    def test_empty_push_produces_no_file(self, audio_tmp):
        """一帧音频都没推过就不该产生文件（避免每场空会议都留个空文件）。"""
        from imeeting.settings import config_store

        config_store._db_overrides = {"storage.audio_retention_days": 7}

        sink = audio.AudioSink(6)
        assert sink.close() is None
        assert list(audio_tmp.glob("*.wav")) == []

    def test_close_is_idempotent(self, audio_tmp):
        from imeeting.settings import config_store

        config_store._db_overrides = {"storage.audio_retention_days": 7}

        sink = audio.AudioSink(9)
        sink.push(np.zeros(1600, dtype=np.float32))
        assert sink.close() is not None
        assert sink.close() is None


class TestFailureIsolation:
    def test_write_failure_does_not_raise(self, audio_tmp, monkeypatch):
        """磁盘/权限异常不能把转写主链路带崩。"""
        from imeeting.settings import config_store

        config_store._db_overrides = {"storage.audio_retention_days": 7}

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr("builtins.open", boom)

        sink = audio.AudioSink(7)
        # 不应抛异常
        sink.push(np.zeros(1600, dtype=np.float32))
        assert sink.close() is None

    def test_failure_does_not_retry_every_chunk(self, audio_tmp, monkeypatch):
        """首次失败后应放弃，否则每块音频都会重复触发异常与日志。"""
        from imeeting.settings import config_store

        config_store._db_overrides = {"storage.audio_retention_days": 7}

        calls = {"n": 0}
        real_open = open

        def counting_open(*a, **k):
            calls["n"] += 1
            raise OSError("nope")

        monkeypatch.setattr("builtins.open", counting_open)

        sink = audio.AudioSink(8)
        for _ in range(5):
            sink.push(np.zeros(1600, dtype=np.float32))
        assert sink.close() is None
        assert calls["n"] == 1, f"重复尝试打开文件 {calls['n']} 次"
        monkeypatch.setattr("builtins.open", real_open)


class TestCleanup:
    def test_removes_expired_keeps_fresh(self, audio_tmp):
        from imeeting.settings import config_store

        config_store._db_overrides = {"storage.audio_retention_days": 7}

        old = audio_tmp / "1.wav"
        fresh = audio_tmp / "2.wav"
        old.write_bytes(b"x")
        fresh.write_bytes(b"x")

        now = time.time()
        # 10 天前 = 已过期
        old_mtime = now - 10 * 86400
        import os

        os.utime(old, (old_mtime, old_mtime))

        removed = audio.cleanup_expired(now=now)
        assert removed == 1
        assert not old.exists()
        assert fresh.exists()

    def test_zero_days_clears_everything(self, audio_tmp):
        """留存 0 天 = 不保存，清理应把存量也删干净。"""
        from imeeting.settings import config_store

        config_store._db_overrides = {"storage.audio_retention_days": 0}

        for i in range(3):
            (audio_tmp / f"{i}.wav").write_bytes(b"x")

        removed = audio.cleanup_expired()
        assert removed == 3
        assert list(audio_tmp.glob("*.wav")) == []

    def test_no_files_is_not_an_error(self, audio_tmp):
        from imeeting.settings import config_store

        config_store._db_overrides = {"storage.audio_retention_days": 7}
        assert audio.cleanup_expired() == 0

    def test_ignores_non_wav(self, audio_tmp):
        """同目录若有其它文件，不能被误删。"""
        from imeeting.settings import config_store

        config_store._db_overrides = {"storage.audio_retention_days": 0}

        keep = audio_tmp / "notes.txt"
        keep.write_text("keep me")
        audio.cleanup_expired()
        assert keep.exists()


class TestPathSafety:
    def test_path_is_inside_audio_dir(self, audio_tmp):
        p = audio.audio_path_for(42)
        assert p.parent == audio_tmp
        assert p.name == "42.wav"

    def test_remove_refuses_outside_data_dir(self, tmp_path, monkeypatch):
        """越权路径必须被拒绝，防止构造路径删除任意文件。"""
        from imeeting import meeting_files

        outside = tmp_path / "important.txt"
        outside.write_text("do not delete")

        audio_tmp = tmp_path / "audio"
        audio_tmp.mkdir()
        monkeypatch.setattr(audio, "audio_dir", lambda: audio_tmp)

        meeting_files.remove_audio_file(str(outside))
        assert outside.exists(), "数据目录外的文件被删除了！"

    def test_remove_deletes_inside_audio_dir(self, tmp_path, monkeypatch):
        from imeeting import meeting_files

        audio_tmp = tmp_path / "audio"
        audio_tmp.mkdir()
        monkeypatch.setattr(audio, "audio_dir", lambda: audio_tmp)

        victim = audio_tmp / "5.wav"
        victim.write_bytes(b"x")

        meeting_files.remove_audio_file(str(victim))
        assert not victim.exists()

    def test_remove_missing_file_is_silent(self, tmp_path, monkeypatch):
        from imeeting import meeting_files

        audio_tmp = tmp_path / "audio"
        audio_tmp.mkdir()
        monkeypatch.setattr(audio, "audio_dir", lambda: audio_tmp)

        meeting_files.remove_audio_file(str(audio_tmp / "nope.wav"))  # 不应抛异常
