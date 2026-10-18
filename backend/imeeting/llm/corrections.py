"""应用大模型提出的 ASR 术语纠正，并做白名单校验。

LLM 只允许把错误写法映射到知识库中的规范词；不在白名单里的“纠正”
一律丢弃，防止为了通顺而补出一个错误的人名、年份或模型版本。
"""

from __future__ import annotations

from dataclasses import replace

from ..glossary import apply_terms, load_terms

_REPEATED_WORDS = (
    "网络",
    "行动",
    "模型",
    "数据",
    "安全",
    "智能",
    "攻击",
    "系统",
    "信息",
    "会议",
)


def canonical_terms() -> set[str]:
    return {row.term for row in load_terms() if row.term}


def validate_corrections(
    raw: object,
    transcript_text: str,
    *,
    min_confidence: float = 0.75,
) -> list[tuple[str, str]]:
    """返回可信的 (错误片段, 规范词) 列表。"""
    if not isinstance(raw, list):
        return []
    allowed = canonical_terms()
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        wrong = str(item.get("from") or "").strip()
        right = str(item.get("to") or "").strip()
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        if (
            len(wrong) < 2
            or not right
            or wrong == right
            or confidence < min_confidence
            or right not in allowed
            or wrong not in transcript_text
        ):
            continue
        key = (wrong, right)
        if key not in seen:
            seen.add(key)
            out.append(key)
    # 长片段优先，避免短词先替换后破坏长词。
    return sorted(out, key=lambda item: len(item[0]), reverse=True)


def _fix_text(text: str, corrections: list[tuple[str, str]]) -> str:
    for wrong, right in corrections:
        text = text.replace(wrong, right)
    text = apply_terms(text)
    # 大模型偶尔会把相邻词重复一次；只清理常见词的完全重复，
    # 不处理“人人/常常”这类正常叠词。
    for word in _REPEATED_WORDS:
        text = text.replace(word + word, word)
    return text


def correct_lines(lines: list, corrections: list[tuple[str, str]]) -> list:
    if not corrections:
        return lines
    return [replace(line, text=_fix_text(line.text, corrections)) for line in lines]


def apply_to_result(result: dict, corrections: list[tuple[str, str]]) -> dict:
    if not corrections:
        # 仍然做一次词表归一化，修正 LLM 从旧文本复制的已知别名。
        corrections = []
    result["summary"] = _fix_text(str(result.get("summary") or ""), corrections)
    for topic in result.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        for key in ("title", "detail", "conclusion"):
            topic[key] = _fix_text(str(topic.get(key) or ""), corrections)
        for evidence in topic.get("evidence") or []:
            if isinstance(evidence, dict) and evidence.get("text"):
                evidence["text"] = _fix_text(str(evidence["text"]), corrections)
    for todo in result.get("todos") or []:
        if not isinstance(todo, dict):
            continue
        for key in ("task", "owner", "due"):
            todo[key] = _fix_text(str(todo.get(key) or ""), corrections)
        for evidence in todo.get("evidence") or []:
            if isinstance(evidence, dict) and evidence.get("text"):
                evidence["text"] = _fix_text(str(evidence["text"]), corrections)
    return result
