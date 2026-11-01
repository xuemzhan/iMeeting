"""会话音频落盘。

设计要点
--------
- **写时即有效**：开头写合法 44 字节头，之后**每写一块就回填一次长度并 flush**。
  进程被杀、机器断电时文件仍是「声明长度 = 实际长度」的可播放 WAV，最多丢当前块；
  :func:`repair_interrupted_wavs` 负责修历史遗留的坏头。
  旧实现先占位 44 个零字节、只在 close 回填，且数据留在 Python 缓冲里——现场那次
  75 MB / 39 分钟的录音就是这样变成「不可读」甚至「0 字节」的。
- **开关与留存**：由 ``storage.audio_retention_days`` 控制；0 表示不落盘。
  超过留存期的文件由 :func:`cleanup_expired` 清理。
- **失败不影响转写**：音频落盘是"尽力而为"能力，任何 IO 错误都只记日志，
  绝不向上抛——转写本身才是主链路。
"""

from __future__ import annotations

import logging
import queue
import struct
import threading
import time
from pathlib import Path

import numpy as np

from ..settings import SERVER_CONFIG, get_config

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
_HEADER_BYTES = 44
_WRITE_QUEUE_CHUNKS = 256
_CLOSE_TIMEOUT_SECONDS = 30.0
#: 修复扫描时，跳过最近这么多秒内还在写的文件。
_REPAIR_MIN_AGE_SECONDS = 120.0
_STOP = object()


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
        self._queue: queue.Queue[object] | None = None
        self._writer: threading.Thread | None = None
        self._fh = None
        self._data_bytes = 0
        self._header_bytes = 0
        self._failed = False
        self.path: Path | None = None
        if self.enabled:
            self._queue = queue.Queue(maxsize=_WRITE_QUEUE_CHUNKS)
            self._writer = threading.Thread(
                target=self._run_writer,
                name=f"audio-writer-{meeting_id}",
                daemon=True,
            )
            self._writer.start()

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
        if self._queue is None:
            return
        try:
            # 正常情况下远快于写入；队列满说明磁盘持续变慢，用背压避免内存无界增长。
            self._queue.put(pcm, timeout=1.0)
        except queue.Full:
            self._failed = True
            logger.error("音频写入积压，放弃本次留存 meeting=%s", self.meeting_id)

    def _run_writer(self) -> None:
        if self._queue is None:
            return
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                if self._failed:
                    continue
                self._write_chunk(item)
            finally:
                self._queue.task_done()

    def _write_chunk(self, pcm: object) -> None:
        try:
            audio = pcm if isinstance(pcm, np.ndarray) else np.asarray(pcm)
            if self._fh is None:
                self.path = audio_path_for(self.meeting_id, self.session_id)
                self._fh = open(self.path, "wb")
                # 立刻写一个合法头（0 字节数据），保证任何时刻文件都是可解析的 WAV。
                self._fh.write(_wav_header(0))
                self._header_bytes = 0

            raw = _to_pcm16(audio)
            self._fh.write(raw)
            self._data_bytes += len(raw)
            # 每块都回填头（44 字节的 seek+write，代价可忽略），这样文件在任何时刻
            # 都是「声明长度 = 实际长度」的合法 WAV；进程被杀最多丢当前这一块。
            # 同时 flush，避免数据只躺在 Python 缓冲里（否则崩溃后文件是 0 字节）。
            self._refresh_header()
        except Exception:
            # 音频落盘属附加能力，失败不能影响转写主链路
            self._failed = True
            logger.exception("音频写入失败 meeting=%s，本次会话放弃留存", self.meeting_id)
            self._close_quietly()

    def _refresh_header(self) -> None:
        """把当前数据长度回填进 WAV 头，不改变写入位置。"""
        if self._fh is None:
            return
        self._fh.seek(0)
        self._fh.write(_wav_header(self._data_bytes))
        self._fh.seek(0, 2)
        self._fh.flush()
        self._header_bytes = self._data_bytes

    def close(self) -> Path | None:
        """收尾：回填 WAV 头。返回落盘路径；未启用或失败返回 None。"""
        if not self.enabled:
            return None
        if self._queue is not None:
            try:
                self._queue.put(_STOP, timeout=_CLOSE_TIMEOUT_SECONDS)
            except queue.Full:
                self._failed = True
                logger.error("音频写入队列无法排空 meeting=%s", self.meeting_id)
                self._close_quietly()
                return None
        if self._writer is not None:
            self._writer.join(timeout=_CLOSE_TIMEOUT_SECONDS)
            if self._writer.is_alive():
                self._failed = True
                logger.error("音频写入线程超时 meeting=%s", self.meeting_id)
                self._close_quietly()
                return None
            self._writer = None
            self._queue = None
        if self._failed:
            self._close_quietly()
            return None
        if self._fh is None:
            return None

        try:
            self._refresh_header()
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

def _wav_data_size_ok(path: Path) -> bool:
    """文件头声明的数据长度与真实文件长度是否一致。"""
    try:
        total = path.stat().st_size
        if total <= _HEADER_BYTES:
            return True
        with path.open("rb") as fh:
            header = fh.read(_HEADER_BYTES)
    except OSError:
        return True
    if len(header) < _HEADER_BYTES or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        return False
    declared = struct.unpack("<I", header[40:44])[0]
    return declared == total - _HEADER_BYTES


def repair_interrupted_wavs(root: Path | None = None) -> list[Path]:
    """修复崩溃/断电留下的不完整 WAV 头，返回被修复的文件。

    旧版本先写 44 个零字节占位、只在会话正常结束时回填，进程被杀就会留下
    「PCM 数据完整但头全零」的文件——播放器直接判定不可读，只能人工按
    文件长度回填。现在虽然改成写时就写合法头，但历史文件仍需一次性修复，
    这里在服务启动时扫一遍。

    只处理超过 ``_REPAIR_MIN_AGE_SECONDS`` 没被改动的文件，避免碰到正在录音的
    新文件（它们由 :class:`AudioSink` 自己维护头）。
    """
    directory = root or audio_dir()
    repaired: list[Path] = []
    now = time.time()
    try:
        candidates = list(directory.glob("*.wav"))
    except OSError:
        return repaired
    for path in candidates:
        try:
            stat = path.stat()
            if now - stat.st_mtime < _REPAIR_MIN_AGE_SECONDS:
                continue
            if stat.st_size <= _HEADER_BYTES or _wav_data_size_ok(path):
                continue
            with path.open("r+b") as fh:
                fh.seek(0)
                fh.write(_wav_header(stat.st_size - _HEADER_BYTES))
                fh.flush()
            repaired.append(path)
            logger.warning(
                "已修复不完整的 WAV 头：%s（%.1fMB）", path.name, stat.st_size / 1024 / 1024
            )
        except OSError:
            logger.debug("修复 WAV 头失败: %s", path, exc_info=True)
    if repaired:
        logger.warning("本次启动修复了 %d 个 WAV 文件头", len(repaired))
    return repaired

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
