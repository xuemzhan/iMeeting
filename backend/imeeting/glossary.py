"""企业词表：ASR 热词、别名归一和纪要提示。"""

from __future__ import annotations

import re
import threading
import time

from sqlmodel import select

from .db import session_scope
from .models import GlossaryTerm

_lock = threading.Lock()
_cache: list[GlossaryTerm] | None = None
_cached_at = 0.0


def invalidate_cache() -> None:
    global _cache, _cached_at
    with _lock:
        _cache = None
        _cached_at = 0.0


def load_terms() -> list[GlossaryTerm]:
    global _cache, _cached_at
    with _lock:
        if _cache is not None and time.time() - _cached_at < 30:
            return list(_cache)
        with session_scope() as db:
            rows = db.exec(
                select(GlossaryTerm)
                .where(GlossaryTerm.enabled == True)  # noqa: E712
                .order_by(GlossaryTerm.term)
            ).all()
            for row in rows:
                db.expunge(row)
        _cache = rows
        _cached_at = time.time()
        return list(rows)


def hotwords_text(extra: object = None) -> str | None:
    terms = [row.term for row in load_terms()]
    if isinstance(extra, str):
        terms.extend(x.strip() for x in extra.split() if x.strip())
    elif isinstance(extra, (list, tuple)):
        terms.extend(str(x).strip() for x in extra if str(x).strip())
    values = list(dict.fromkeys(terms))
    return " ".join(values) if values else None


def apply_terms(text: str) -> str:
    if not text:
        return text
    mapping: dict[str, str] = {}
    for row in load_terms():
        aliases = [
            item.strip()
            for item in (row.aliases or "").replace("，", ",").split(",")
            if item.strip()
        ]
        for alias in aliases:
            if alias != row.term:
                mapping.setdefault(alias.lower(), row.term)
    if not mapping:
        return text

    # 必须单次扫描替换。旧实现按别名逐个 replace，先把“拓部结构”改成
    # “拓扑结构”，随后“拓扑”这个别名又命中新文本，得到“拓扑结构结构”。
    # 用“最长别名优先”的交替正则，每个位置只替换一次，彻底避免级联。
    parts: list[str] = []
    for alias in sorted(mapping, key=len, reverse=True):
        escaped = re.escape(alias)
        term = mapping[alias]
        # 别名是规范词的“前缀”时，避免在已经正确的规范词上再次命中。
        # 例如“拓扑”是“拓扑结构”的前缀，“智能汽”是“智能汽车”的前缀；
        # 单次替换仍会得到“拓扑结构结构/智能汽车车”。加一个否定前瞻，
        # 只替换独立出现的别名。
        if term.lower().startswith(alias) and len(term) > len(alias):
            suffix = re.escape(term[len(alias) :])
            escaped += rf"(?!{suffix})"
        if re.fullmatch(r"[a-z0-9_.-]+", alias):
            parts.append(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])")
        else:
            parts.append(escaped)
    pattern = re.compile("|".join(parts), re.IGNORECASE)
    return pattern.sub(
        lambda match: mapping.get(match.group(0).lower(), match.group(0)),
        text,
    )


def prompt_section() -> str:
    rows = load_terms()
    if not rows:
        return ""
    lines = [f"- {row.term}" + (f"（别名：{row.aliases}）" if row.aliases else "") for row in rows[:200]]
    return "企业词表（规范写法以左侧为准，不要改写为其他名称）：\n" + "\n".join(lines) + "\n\n"
