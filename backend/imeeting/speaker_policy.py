"""说话人置信度策略。

相似度阈值不能只看一个全局数字：2 秒短句的声纹向量天然比 8 秒长句
不稳定，真实会议里 0.62~0.65 的短句往往是“刚好过线但不可信”。这里
把阈值策略集中到后端，WebSocket 和历史接口使用同一套判定，避免前端
各自硬编码。
"""

from __future__ import annotations

from .settings import get_config


def low_confidence_threshold(
    speech_ms: int | float,
    text: str | None,
    *,
    base: float | None = None,
) -> float:
    """返回该片段应使用的低置信阈值。"""
    base_value = (
        float(get_config("spk.low_confidence_threshold") or 0.62)
        if base is None
        else float(base)
    )
    short_ms = int(get_config("spk.short_confidence_ms") or 1500)
    short_chars = int(get_config("spk.short_confidence_chars") or 10)
    short_threshold = float(
        get_config("spk.short_confidence_threshold") or 0.70
    )
    speech = int(speech_ms or 0)
    chars = len((text or "").strip())
    is_short = (speech > 0 and speech < short_ms) or (
        chars > 0 and chars < short_chars
    )
    return max(base_value, short_threshold) if is_short else base_value


def is_low_confidence(
    score: float | None,
    speech_ms: int | float,
    text: str | None,
    *,
    source: str = "",
    base: float | None = None,
) -> bool:
    """是否应在界面上提示“低置信”。人工确认过的不再提示。"""
    if source == "manual" or score is None or float(score) <= 0:
        return False
    return float(score) < low_confidence_threshold(
        speech_ms, text, base=base
    )
