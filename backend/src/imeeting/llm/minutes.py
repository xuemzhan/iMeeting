"""会议纪要生成：map-reduce 分段摘要。

长会议无法一次性塞进 LLM 上下文，因此：
1. 按时间窗口切成若干段（map），每段单独摘要；
2. 把各段摘要合并后再做一次总结（reduce）。

短会议直接走单次调用，省掉不必要的开销。
"""

from __future__ import annotations

import json
import logging
import re

from ..settings import get_config
from .client import LLMClient, LLMError, extract_json
from .types import TranscriptLine

logger = logging.getLogger(__name__)

# 单次调用能安全处理的字符数上限（约 4000 token 的中文）
SINGLE_PASS_LIMIT = 8000


#: 这些词开头的片段通常是上一句的延续，而不是新话题
CONTINUATION_PREFIXES = (
    "然后", "但是", "那", "如果", "所以", "因为", "就是", "不过",
    "而且", "并且", "比如说", "比如", "还有", "另外", "同时",
)

#: 短停顿合并：把被 0.6s 静音切碎的口语碎片重新拼成完整句子
MERGE_GAP_MS = 2000
MERGE_PREV_MAX_CHARS = 40
MERGE_MAX_CHARS = 220


def merge_fragments(lines: list[TranscriptLine]) -> list[TranscriptLine]:
    """把明显被切断的相邻片段合并成更完整的句子（只用于纪要/导出）。

    原始片段仍按时间轴保留，这里只构造一份「语义视图」：

    - **说话人完全相同**（包括两边都未知）且停顿不超过 ``MERGE_GAP_MS``；
    - 上一句偏短（<= 40 字）或下一句以连接词开头；
    - 合并后不超过 ``MERGE_MAX_CHARS``，避免一句滚成整段。

    真实长测里 42% 的片段以「然后/但是/那/如果」开头，且每段都被单独
    补了句号——直接喂给 LLM 会让纪要误以为是一连串独立结论。

    不跨「未知/已命名」边界合并：那会把一个未知说话人的句子错误地归到
    已知说话人名下（真实数据里这种跨边界合并有 12 处），宁可少合并。
    """
    if not lines:
        return []

    merged: list[TranscriptLine] = [lines[0]]
    for cur in lines[1:]:
        prev = merged[-1]
        if prev.speaker != cur.speaker:
            merged.append(cur)
            continue

        gap_ms = (
            cur.start_ms - prev.end_ms
            if prev.end_ms is not None
            else cur.start_ms - prev.start_ms
        )
        continuation = (cur.text or "").lstrip().startswith(CONTINUATION_PREFIXES)
        joiner = " " if prev.text[-1:].isascii() and prev.text[-1:].isalnum() else ""
        combined = f"{prev.text}{joiner}{cur.text}"
        if (
            gap_ms <= MERGE_GAP_MS
            and (len(prev.text) <= MERGE_PREV_MAX_CHARS or continuation)
            and len(combined) <= MERGE_MAX_CHARS
        ):
            merged[-1] = TranscriptLine(
                start_ms=prev.start_ms,
                speaker=prev.speaker,
                text=combined,
                end_ms=cur.end_ms if cur.end_ms is not None else prev.end_ms,
                segment_id=prev.segment_id,
                segment_ids=[
                    *prev.segment_ids,
                    *cur.segment_ids,
                    *([cur.segment_id] if cur.segment_id else []),
                ],
            )
        else:
            merged.append(cur)
    return merged


def _format_lines(lines: list[TranscriptLine]) -> str:
    out: list[str] = []
    for line in lines:
        ts = _mmss(line.start_ms)
        who = f"说话人{line.speaker}" if line.speaker else "发言"
        out.append(f"[{ts}] {who}：{line.text}")
    return "\n".join(out)


def _mmss(ms: int) -> str:
    total = ms // 1000
    return f"{total // 60:02d}:{total % 60:02d}"


async def generate_minutes(
    lines: list[TranscriptLine],
    *,
    model_label: str = "",
    draft: bool = False,
    template: str | None = None,
    meeting_id: int | None = None,
) -> dict:
    """生成结构化纪要。

    Returns:
        {"summary": str, "topics": [...], "todos": [...], "raw_markdown": str, "model": str}

    Raises:
        LLMError: 调用失败或输出无法解析
    """
    if not lines:
        raise LLMError("没有可用的转写内容")

    # 先做一次「短停顿合并」，让 LLM 看到的是完整句子而不是 2.4s 碎片
    lines = merge_fragments(lines)

    client = LLMClient()
    if not client.configured:
        raise LLMError("LLM 未配置，请在后台「LLM 纪要」中填写 API 端点与模型名")

    transcript = _format_lines(lines)
    from ..glossary import prompt_section

    glossary_prompt = prompt_section()
    prompt_template = (template or "").strip() or str(
        get_config("llm.minutes_prompt") or ""
    )
    if glossary_prompt:
        prompt_template = glossary_prompt + prompt_template
    if meeting_id is not None:
        from .knowledge_context import build_knowledge_context

        knowledge_prompt = build_knowledge_context(meeting_id, lines)
        if knowledge_prompt:
            prompt_template = knowledge_prompt + "\n\n" + prompt_template
    if meeting_id is not None:
        prompt_template = (
            '输出 JSON 时额外给出 corrections 字段：'
            '[{"from":"转写中的错误片段","to":"知识库规范词","confidence":0.9}]。'
            "只列 from 在转写中真实出现、to 在知识库规范词中且你有把握的项；"
            "不确定就不要列。\n\n"
            + prompt_template
        )
    if "{transcript}" not in prompt_template:
        prompt_template = (
            prompt_template + "\n\n会议转写内容：\n---\n{transcript}\n---"
        )

    quality_note = (
        "纪要质量要求：数字、日期、专有名词和数量词只能来自转写；"
        "不要自行添加“三大/四大/五项”等数量词。"
        "如果列举项数量与数量词不一致，请删除数量词或按原文修正。\n\n"
    )
    draft_note = quality_note
    if draft:
        draft_note += (
        "这是会议进行中的阶段性转写，会议尚未结束。"
        "请只基于已有内容输出阶段性纪要，不要把未完成的部分补全或编造。\n\n"
        )

    if len(transcript) <= SINGLE_PASS_LIMIT:
        # 短会议：一次搞定
        prompt = draft_note + prompt_template.replace("{transcript}", transcript)
        raw = await client.chat(
            [{"role": "user", "content": prompt}],
            json_mode=True,
            meeting_id=meeting_id,
            purpose="minutes_draft" if draft else "minutes",
        )
        parsed = extract_json(raw)
        if parsed is None:
            logger.warning("模型输出无法解析为 JSON，降级为纯文本摘要")
            return {
                "summary": raw.strip(),
                "topics": [],
                "todos": [],
                "raw_markdown": raw.strip(),
                "model": client.model,
            }
        return _finalize_minutes(parsed, lines, client.model)

    # 长会议：map-reduce
    logger.info("转写内容 %d 字符，超出单次上限，启用分段摘要", len(transcript))
    chunks = _split_by_window(lines, int(get_config("llm.summary_window_minutes") or 10))
    logger.info("切分为 %d 段", len(chunks))

    partials: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        chunk_text = _format_lines(chunk)
        raw = await client.chat(
            [
                {
                    "role": "user",
                    "content": (
                        f"这是会议的第 {i}/{len(chunks)} 部分转写内容。"
                    + (f"\n{glossary_prompt}" if glossary_prompt else "")
                        + "请提炼关键信息，保留议题、结论、待办及其责任人，不要添加原文没有的内容。\n\n"
                        f"---\n{chunk_text}\n---"
                    ),
                }
            ],
            temperature=0.2,
            meeting_id=meeting_id,
            purpose="minutes_map",
        )
        partials.append(f"【第 {i} 部分】\n{raw.strip()}")

    merged = "\n\n".join(partials)
    final_prompt = (
        draft_note
        + "以下是同一场会议各部分的摘要，请合并为一份完整的会议纪要。\n"
        "要求：合并重复议题，保持时间顺序，不要丢失任何待办事项。\n\n"
        "输出 JSON，结构如下：\n"
        '{"summary":"整体摘要","topics":[{"title":"议题","detail":"说明","conclusion":"结论"}],'
        '"todos":[{"task":"待办","owner":"责任人","due":"截止时间"}]}\n\n'
        f"各部分摘要：\n---\n{merged}\n---"
    )

    raw = await client.chat(
        [{"role": "user", "content": final_prompt}],
        json_mode=True,
        meeting_id=meeting_id,
        purpose="minutes_reduce",
    )
    parsed = extract_json(raw)
    if parsed is None:
        return {
            "summary": raw.strip(),
            "topics": [],
            "todos": [],
            "raw_markdown": merged,
            "model": client.model,
        }

    result = _finalize_minutes(parsed, lines, client.model)
    result["raw_markdown"] = merged
    return result


def _finalize_minutes(
    parsed: dict, lines: list[TranscriptLine], model: str
) -> dict:
    """规整、校验术语纠正、生成证据，形成最终结果。"""
    from ..evidence import attach_evidence
    from .corrections import (
        apply_to_result,
        correct_lines,
        validate_corrections,
    )

    result = _normalize(parsed, model)
    transcript_text = "\n".join(line.text for line in lines)
    corrections = validate_corrections(
        result.get("corrections"), transcript_text
    )
    if corrections:
        logger.info(
            "纪要 ASR 术语纠正 count=%d pairs=%s",
            len(corrections),
            "；".join(f"{wrong}->{right}" for wrong, right in corrections[:10]),
        )
    fixed_lines = correct_lines(lines, corrections)
    result = apply_to_result(result, corrections)
    return attach_evidence(result, fixed_lines)


def _split_by_window(lines: list[TranscriptLine], window_minutes: int) -> list[list[TranscriptLine]]:
    """按时间窗口切分转写内容。窗口内字符数过多时进一步切分。"""
    if not lines:
        return []

    window_ms = window_minutes * 60 * 1000
    chunks: list[list[TranscriptLine]] = []
    current: list[TranscriptLine] = []
    current_chars = 0
    window_start = lines[0].start_ms

    for line in lines:
        elapsed = line.start_ms - window_start
        overflow_time = elapsed >= window_ms
        overflow_size = current_chars + len(line.text) > SINGLE_PASS_LIMIT

        if current and (overflow_time or overflow_size):
            chunks.append(current)
            current = []
            current_chars = 0
            window_start = line.start_ms

        current.append(line)
        current_chars += len(line.text)

    if current:
        chunks.append(current)

    return chunks


def _as_list(raw: object) -> list:
    """把可能是 JSON 字符串的字段规整成 list。

    模型有时会把数组序列化成字符串塞回来（``"topics": "[{...}]"``）。
    不处理的话会静默丢内容——用户看到纪要里「议题」整块是空的。
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            # 退化成整段纯文本，至少不丢信息
            return [text]
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
        return [text]
    return []


def _normalize(parsed: dict, model: str) -> dict:
    """把模型输出规整成统一结构，容忍字段缺失或命名差异。"""
    summary = _sanitize_numeric_claims(
        str(parsed.get("summary") or parsed.get("摘要") or parsed.get("概述") or "").strip()
    )

    raw_topics = _as_list(parsed.get("topics") or parsed.get("议题") or [])
    topics: list[dict] = []
    for item in raw_topics:
        if isinstance(item, str):
            topics.append({"title": item, "detail": "", "conclusion": ""})
        elif isinstance(item, dict):
            topics.append(
                {
                    "title": str(item.get("title") or item.get("议题") or item.get("名称") or ""),
                    "detail": str(item.get("detail") or item.get("说明") or item.get("内容") or ""),
                    "conclusion": str(
                        item.get("conclusion") or item.get("结论") or item.get("共识") or ""
                    ),
                }
            )

    raw_todos = _as_list(parsed.get("todos") or parsed.get("待办") or parsed.get("待办事项") or [])
    todos: list[dict] = []
    for item in raw_todos:
        if isinstance(item, str):
            todos.append({"task": item, "owner": "", "due": ""})
        elif isinstance(item, dict):
            todos.append(
                {
                    "task": str(item.get("task") or item.get("待办") or item.get("事项") or ""),
                    "owner": str(item.get("owner") or item.get("责任人") or item.get("负责人") or ""),
                    "due": str(item.get("due") or item.get("截止") or item.get("截止时间") or ""),
                }
                )

    return {
        "summary": summary,
        "topics": topics,
        "todos": todos,
        "raw_markdown": "",
        "model": model,
        "corrections": _as_list(parsed.get("corrections") or []),
    }


_CN_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_NUMBER_CLAIM = re.compile(
    r"([一二两三四五六七八九十]+)(大|个|项|类|种|方面|方向|条|步|阶段)"
    r"([^。；\n]{0,100})"
)
_PAREN_LIST = re.compile(r"[（(]([^）)]{4,})[）)]")


def _sanitize_numeric_claims(text: str) -> str:
    """修正“四大方向”却列了七项这类数量断言。

    只处理“数量词 + 修饰语 + 括号列表”这一种明确结构；数量与列表一致时
    不动，找不到列表时也不猜。改写为“相关方向”比保留错误数量更可信。
    """

    def replace(match: re.Match) -> str:
        number = _CN_NUMBERS.get(match.group(1))
        rest = match.group(3)
        listed = _PAREN_LIST.search(rest)
        if number is None or listed is None:
            return match.group(0)
        items = [
            item.strip()
            for item in re.split(r"[、,，;；]", listed.group(1))
            if item.strip() and item.strip() not in ("等", "等等")
        ]
        if len(items) < 2 or len(items) == number:
            return match.group(0)
        noun = re.split(r"[（(]", rest, maxsplit=1)[0].strip()
        logger.warning(
            "纪要数量断言不一致：%s%s 后列出 %d 项，改为相关%s",
            match.group(1),
            match.group(2),
            len(items),
            noun or "内容",
        )
        return f"相关{noun}" if noun else "相关内容"

    return _NUMBER_CLAIM.sub(replace, text or "")
