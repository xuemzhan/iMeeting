"""流式草稿兜底文本的质量门禁。

离线 ASR 为空时，流式草稿可能包含单字、重复字或声学乱码。这里使用保守
规则只过滤明显无信息量的片段，不尝试替代语言模型判断正常短句。
"""

from __future__ import annotations

import re
from collections import Counter

_PUNCT = re.compile(r"[\s，。！？、；：,.!?;:\-—_（）()\[\]【】\"'“”‘’]")
_FUNCTION_CHARS = set("的了吗呢啊嗯呃就都也说是在和个我你他她它们要这那有对")
_REPEATED_BIGRAMS = {"的的", "了了", "是是", "我我", "你你", "他他", "说说", "力力", "个个"}


def fallback_quality(text: str) -> tuple[bool, str]:
    """返回 (是否可用, 拒绝原因)。"""
    raw = (text or "").strip()
    normalized = _PUNCT.sub("", raw)
    if len(normalized) < 4:
        return False, "too_short"
    counts = Counter(normalized)
    unique_ratio = len(counts) / max(1, len(normalized))
    max_ratio = max(counts.values()) / max(1, len(normalized))
    function_ratio = sum(ch in _FUNCTION_CHARS for ch in normalized) / len(normalized)
    if unique_ratio < 0.38:
        return False, "low_unique_ratio"
    if max_ratio > 0.28:
        return False, "repeated_char"
    # 短句里“对，就是这样”几乎全是功能词，但它是正常口语。只有长片段
    # 且功能词占比极高时才视为乱码，避免把真实短回答误杀。
    if len(normalized) >= 12 and function_ratio > 0.85:
        return False, "function_word_noise"
    for bigram in _REPEATED_BIGRAMS:
        if bigram in normalized:
            return False, "repeated_bigram"
    return True, ""
