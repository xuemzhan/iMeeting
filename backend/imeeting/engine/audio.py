"""会话音频落盘。

设计要点
--------
- **增量写 + 收尾补头**：会话期间只追加 PCM 数据，结束时回填 WAV 头。
  这样即使进程被杀，文件里仍有全部音频（只是头信息不完整，可修复）。
- **开关与留存**：由 ``storage.audio_retention_days`` 控制；0 表示不落盘。
  超过留存期的文件由 :func:`cleanup_expired` 清理。
- **失败不影响转写**：音频落盘是"尽力而为"能力，任何 IO 错误都只记日志，
  绝不向上抛——转写本身才是主链路。
"""

from __future__ import annotations

import logging
import struct
import time
from pathlib import Path

import numpy as np

from ..settings import SERVER_CONFIG, get_config

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
_HEADER_BYTES = 44


def audio_dir() -> Path:
    """音频存放目录（不存在则创建）。"""
    d = Path(SERVER_CONFIG["data_dir"]) / "audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def audio_path_for(meeting_id: int, session_id: str | None = None) -> Path:
    """音频文件名。

    同一会议的每次 WS 连接都会产生新文件：旧实现固定写 ``<meeting_id>.wav``，
    浏览器刷新/断线重连会把前面几十分钟的录音直接覆盖掉（真实长测里
    74.1MB 的 40 分钟录音就是这样丢的）。
    """
    suffix = f"-{session_id}" if session_id else ""
    return audio_dir() / f"{meeting_id}{suffix}.wav"


def _wav_header(data_bytes: int) -> bytes:
    """生成 16kHz / 单声道 / 16bit PCM 的 WAV 头。"""
    byte_rate = SAMPLE_RATE * 2
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_bytes,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        1,  # 单声道
        SAMPLE_RATE,
        byte_rate,
        2,  # block align
        16,  # bits per sample
        b"data",
        data_bytes,
    )


def _to_pcm16(pcm: np.ndarray) -> bytes:
    """float32 [-1,1] → int16 小端字节。"""
    clipped = np.clip(pcm, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


class AudioSink:
    """一场会议的音频写入器。

    典型用法::

        sink = AudioSink(meeting_id)
        sink.push(pcm)          # 每次收到音频块
        path = sink.close()     # 收尾补头，返回落盘路径（未启用则为 None）
    """

    def __init__(self, meeting_id: int, session_id: str | None = None) -> None:
        self.meeting_id = meeting_id
        self.session_id = session_id
        self.enabled = self._should_enable()
        self._fh = None
        self._data_bytes = 0
        self._failed = False
        self.path: Path | None = None

    @staticmethod
    def _should_enable() -> bool:
        try:
            days = int(get_config("storage.audio_retention_days") or 0)
        except (TypeError, ValueError):
            days = 0
        return days > 0

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def push(self, pcm: np.ndarray) -> None:
        """追加一块 float32 PCM。未启用或已失败时静默跳过。"""
        if not self.enabled or self._failed or len(pcm) == 0:
            return
        try:
            if self._fh is None:
                self.path = audio_path_for(self.meeting_id, self.session_id)
                # 先占位 44 字节头，结束时回填真实长度
                self._fh = open(self.path, "wb")
                self._fh.write(b"\x00" * _HEADER_BYTES)

            raw = _to_pcm16(pcm)
            self._fh.write(raw)
            self._data_bytes += len(raw)
        except Exception:
            # 音频落盘属附加能力，失败不能影响转写主链路
            self._failed = True
            logger.exception("音频写入失败 meeting=%s，本次会话放弃留存", self.meeting_id)
            self._close_quietly()

    def close(self) -> Path | None:
        """收尾：回填 WAV 头。返回落盘路径；未启用或失败返回 None。"""
        if not self.enabled or self._failed:
            self._close_quietly()
            return None
        if self._fh is None:
            return None

        try:
            self._fh.flush()
            self._fh.seek(0)
            self._fh.write(_wav_header(self._data_bytes))
            self._fh.flush()
            self._fh.close()
            self._fh = None
            logger.info(
                "音频已留存 meeting=%s (%.1fMB)",
                self.meeting_id,
                self._data_bytes / 1024 / 1024,
            )
            return self.path
        except Exception:
            logger.exception("音频收尾失败 meeting=%s", self.meeting_id)
            self._close_quietly()
            return None

    def _close_quietly(self) -> None:
        try:
            if self._fh is not None:
                self._fh.close()
        except Exception:
            pass
        self._fh = None

    @property
    def bytes_written(self) -> int:
        return self._data_bytes


# ---------------------------------------------------------------------------
# 清理
# ---------------------------------------------------------------------------


def cleanup_expired(now: float | None = None) -> int:
    """删除超过留存期的音频文件，返回删除数量。

    ``storage.audio_retention_days=0`` 时删除全部音频（等于不保存）。
    """
    try:
        days = int(get_config("storage.audio_retention_days") or 0)
    except (TypeError, ValueError):
        days = 0

    if days <= 0:
        cutoff = float("inf")  # 全部视为过期
    else:
        cutoff = (now if now is not None else time.time()) - days * 86400

    removed = 0
    try:
        for f in audio_dir().glob("*.wav"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                logger.debug("删除音频失败: %s", f)
    except Exception:
        logger.exception("音频清理异常")

    if removed:
        logger.info("已清理 %d 个过期音频文件（留存 %d 天）", removed, days)
    return removed
