"""为纪要主题、待办和脑图生成可追溯的原始片段证据。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .glossary import apply_terms
from .llm.minutes import TranscriptLine

_STOP = {
    "这个", "那个", "然后", "就是", "我们", "他们", "大家", "可以", "需要",
    "一个", "进行", "以及", "因为", "所以", "如果", "但是", "还有", "已经",
    "今天", "本次", "会议", "讨论", "认为", "表示", "的话",
}


def _tokens(text: str) -> set[str]:
    text = re.sub(r"\s+", "", text.lower())
    words = set(re.findall(r"[a-z0-9_]{2,}", text))
    chinese = re.sub(r"[^\u4e00-\u9fff]", "", text)
    words.update(
        chinese[i : i + 2]
        for i in range(max(0, len(chinese) - 1))
        if chinese[i : i + 2] not in _STOP
    )
    return words


def _best_evidence(text: str, lines: list[TranscriptLine], limit: int = 3) -> list[dict]:
    if not text.strip() or not lines:
        return []
    target = _tokens(text)
    scored: list[tuple[float, TranscriptLine]] = []
    for line in lines:
        source = _tokens(line.text)
        overlap = len(target & source) / max(1, len(target))
        fuzzy = SequenceMatcher(None, text, line.text).ratio() * 0.25
        score = overlap + fuzzy
        if score > 0.04:
            scored.append((score, line))
    scored.sort(key=lambda item: item[0], reverse=True)
    out: list[dict] = []
    for _score, line in scored[:limit]:
        out.append(
            {
                "segment_id": line.segment_id,
                "start_ms": line.start_ms,
                "end_ms": line.end_ms or line.start_ms,
                "text": apply_terms(line.text)[:260],
            }
        )
    return out


def _normalize_evidence(items: list) -> list[dict]:
    """对已有 evidence 再做一次词表归一化。

    LLM 可能在生成 topics 时直接带上 evidence；这些文本可能来自旧词表
    或模型原样复制，需要在落库前统一规范化。
    """
    out: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        text = str(row.get("text") or "")
        if text:
            row["text"] = apply_terms(text)[:260]
        out.append(row)
    return out


def attach_evidence(result: dict, lines: list[TranscriptLine]) -> dict:
    for topic in result.get("topics") or []:
        text = " ".join(
            str(topic.get(key) or "")
            for key in ("title", "detail", "conclusion")
        )
        topic["evidence"] = (
            _normalize_evidence(topic.get("evidence") or [])
            or _best_evidence(text, lines)
        )
    for todo in result.get("todos") or []:
        text = " ".join(
            str(todo.get(key) or "") for key in ("task", "owner", "due")
        )
        todo["evidence"] = (
            _normalize_evidence(todo.get("evidence") or [])
            or _best_evidence(text, lines)
        )
    return result


def preserve_previous_evidence(previous: dict, current: dict) -> dict:
    old = {
        str(item.get("title") or ""): item.get("evidence") or []
        for item in previous.get("topics") or []
        if isinstance(item, dict)
    }
    for topic in current.get("topics") or []:
        title = str(topic.get("title") or "")
        if not topic.get("evidence") and old.get(title):
            topic["evidence"] = old[title]
    return current
