"""为会议纪要构建知识库校正上下文。

ASR 的同音/近音错误不能靠全局字符串替换全部解决。这里把“本场命中的
规范术语 + 历史已确认纪要”整理成受约束的参考上下文，交给大模型做保守
判断：能明确对应才改，不能确认的数字/年份/实体就概括或省略。
"""

from __future__ import annotations

from sqlmodel import select

from ..db import session_scope
from ..glossary import load_terms
from ..models import Meeting, Minutes
from .types import TranscriptLine

_RULES = (
    "知识库校正规则（必须遵守）：\n"
    "1. 下面的规范术语和历史会议知识只用于纠正 ASR 的同音字、错别字、"
    "专有名词和实体名，不得把历史会议中未在本场出现的事实写成当前会议事实。\n"
    "2. 能明确对应的术语使用规范写法；无法确认的数字、年份、模型版本、"
    "人名、机构名和事件细节不要猜，改用概括表述或省略。\n"
    "3. 对明显乱码不要原样复述；上下文不足以还原时，宁可少写细节，"
    "也不要补全一个看似合理的错误事实。\n"
    "4. 上一版纪要中的明显 ASR 错词可以在新版修正，但已确认的结论和待办"
    "不能因为改写而丢失。"
)

#: 领域基础词表：即使本场 ASR 还没正确写出某个词，也让 LLM 有规范候选。
#: 它只用于纠错，不能据此断言本场一定讨论了这些主题。
_DOMAIN_TERMS = (
    "数据确权、个人信息保护、个人信息保护法、数据安全、数据安全法、"
    "网络安全法、敏感个人信息、告知同意、数据出境、个人信息过度收集、"
    "第三方披露、数据泄露、数据资产、滴滴、哈梅内伊、美以联合行动、"
    "震网病毒、Claude 3 Opus、GPT-6.5、护网行动、供应链攻击、"
    "认知战、舆论战、智能体"
)


def _aliases(row) -> list[str]:
    return [
        item.strip()
        for item in str(getattr(row, "aliases", "") or "")
        .replace("，", ",")
        .split(",")
        if item.strip()
    ]


def matching_terms(lines: list[TranscriptLine], limit: int = 60) -> list:
    """找出本场转写里实际命中的规范术语。"""
    text = "\n".join(line.text for line in lines if line.text)
    if not text:
        return []
    out = []
    for row in load_terms():
        values = [row.term, *_aliases(row)]
        if any(value and value in text for value in values):
            out.append(row)
        if len(out) >= limit:
            break
    return out


def build_knowledge_context(
    meeting_id: int | None,
    lines: list[TranscriptLine],
    *,
    max_snippets: int = 3,
) -> str:
    """返回可直接拼入 LLM prompt 的术语/历史知识上下文。"""
    terms = matching_terms(lines)
    parts = [
        _RULES,
        "领域规范词（仅用于同音/近音纠错，不代表本场均出现）：\n"
        + _DOMAIN_TERMS,
    ]

    if terms:
        term_lines = []
        for row in terms:
            aliases = _aliases(row)
            suffix = f"（常见 ASR 写法：{'、'.join(aliases[:4])}）" if aliases else ""
            term_lines.append(f"- {row.term}{suffix}")
        parts.append("本场命中的规范术语：\n" + "\n".join(term_lines))

    if meeting_id is None or not terms:
        return "\n\n".join(parts)

    keys = [row.term for row in terms if len(row.term) >= 2]
    snippets: list[tuple[int, str, str]] = []
    with session_scope() as db:
        rows = db.exec(
            select(Minutes)
            .where(
                Minutes.meeting_id != meeting_id,
                Minutes.is_draft == False,  # noqa: E712
            )
            .order_by(Minutes.created_at.desc())
            .limit(60)
        ).all()
        for row in rows:
            blob = f"{row.summary or ''}\n{row.topics or ''}"
            score = sum(1 for key in keys if key in blob)
            if score <= 0:
                continue
            meeting = db.get(Meeting, row.meeting_id)
            title = meeting.title if meeting is not None else f"会议 {row.meeting_id}"
            summary = (row.summary or "").strip()
            if summary:
                snippets.append((score, title, summary[:240]))

    snippets.sort(key=lambda item: item[0], reverse=True)
    if snippets:
        lines_out = [
            f"- [{title}] {summary}"
            for _score, title, summary in snippets[:max_snippets]
        ]
        parts.append(
            "历史会议知识（仅用于术语/背景校正，不得写成当前会议事实）：\n"
            + "\n".join(lines_out)
        )
    return "\n\n".join(parts)
