"""会议附属文件清理：音频与指标。"""

from __future__ import annotations

import logging
from pathlib import Path

from .engine import audio as audio_module
from .settings import BASE_DIR

logger = logging.getLogger(__name__)


def remove_audio_file(path: str) -> None:
    """删除音频文件；只允许删数据目录内的文件。"""
    try:
        target = Path(path).resolve()
        base = audio_module.audio_dir().resolve()
        if base not in target.parents and target != base:
            logger.warning("拒绝删除数据目录外的文件: %s", target)
            return
        target.unlink(missing_ok=True)
    except Exception:
        logger.exception("删除音频文件失败: %s", path)


def cleanup_meeting_files(meeting_id: int, audio_path: str | None) -> None:
    if audio_path:
        remove_audio_file(audio_path)
    base = audio_module.audio_dir()
    for pattern in (f"{meeting_id}-*.wav", f"{meeting_id}.wav"):
        for path in base.glob(pattern):
            remove_audio_file(str(path))

    metrics_dir = BASE_DIR / "data" / "metrics"
    for path in metrics_dir.glob(f"meeting-{meeting_id}-*.json"):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            logger.exception("删除会议指标失败: %s", path)
