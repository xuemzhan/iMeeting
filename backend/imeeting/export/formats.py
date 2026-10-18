"""导出：SRT / VTT / Markdown / 纯文本 / JSON。"""

from __future__ import annotations

import json
from datetime import datetime


def _srt_time(ms: int) -> str:
    """00:00:01,234"""
    total = max(ms, 0)
    h, rem = divmod(total, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{msec:03d}"


def _vtt_time(ms: int) -> str:
    """00:00:01.234"""
    return _srt_time(ms).replace(",", ".")


def _speaker_label(speaker: str | None) -> str:
    return f"说话人{speaker}" if speaker else "发言"


def _as_dict_list(raw: object) -> list[dict]:
    """把可能畸形的 LLM 输出规整成 list[dict]。

    LLM 返回的 JSON 结构并不可靠，实际见过这些形态：
    - ``None`` / ``[]``                  → 空列表
    - JSON 字符串（数据库里就是这样存的） → 先 json.loads
    - ``[{"..."}, "..."]`` 混入字符串     → 字符串项包成 {"title": s} / {"task": s}
    - 整个对象 ``{"a": 1}``               → 包成单元素列表

    这里不抛异常：导出是用户可见路径，宁可少渲染几行也不能 500。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            # 不是 JSON，就当一段纯文本处理
            return [{"title": text, "task": text, "detail": text}]
    if isinstance(raw, dict):
        return [raw]
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, str) and item.strip():
            # 字符串项同时给出 title/task/detail 三个别名，交由调用方按需取用
            text = item.strip()
            out.append({"title": text, "task": text, "detail": text})
    return out


def _hms(ms: int) -> str:
    total = max(ms, 0) // 1000
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def to_srt(segments: list[dict]) -> str:
    blocks: list[str] = []
    for i, seg in enumerate(segments, 1):
        start = _srt_time(seg["start_ms"])
        # 零长度片段给个最小宽度，否则播放器可能不显示
        end = _srt_time(max(seg["end_ms"], seg["start_ms"] + 500))
        text = f"{_speaker_label(seg.get('speaker'))}：{seg['text']}" if seg.get("speaker") else seg["text"]
        blocks.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(blocks)


def to_vtt(segments: list[dict]) -> str:
    lines = ["WEBVTT", ""]
    for seg in segments:
        start = _vtt_time(seg["start_ms"])
        end = _vtt_time(max(seg["end_ms"], seg["start_ms"] + 500))
        text = f"{_speaker_label(seg.get('speaker'))}：{seg['text']}" if seg.get("speaker") else seg["text"]
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def to_markdown(meeting: dict, segments: list[dict], minutes: dict | None) -> str:
    lines: list[str] = []
    title = meeting.get("title") or "会议记录"
    lines.append(f"# {title}")
    lines.append("")

    created = meeting.get("created_at")
    if created:
        lines.append(f"- **时间**：{created}")
    lines.append(f"- **时长**：{_hms(meeting.get('duration_ms', 0))}")
    lines.append(f"- **片段数**：{len(segments)}")
    lines.append("")

    if minutes:
        if minutes.get("summary"):
            lines.append("## 会议摘要")
            lines.append("")
            lines.append(minutes["summary"])
            lines.append("")

        topics = _as_dict_list(minutes.get("topics"))
        if topics:
            lines.append("## 议题与结论")
            lines.append("")
            for topic in topics:
                title_text = str(topic.get("title", "") or "").strip()
                lines.append(f"### {title_text or '未命名议题'}")
                lines.append("")
                if topic.get("detail"):
                    lines.append(str(topic["detail"]))
                    lines.append("")
                if topic.get("conclusion"):
                    lines.append(f"> **结论**：{topic['conclusion']}")
                    lines.append("")

        todos = _as_dict_list(minutes.get("todos"))
        if todos:
            lines.append("## 待办事项")
            lines.append("")
            lines.append("| 事项 | 责任人 | 截止 |")
            lines.append("| --- | --- | --- |")
            for todo in todos:
                task = str(todo.get("task", "")).replace("|", "\\|")
                owner = str(todo.get("owner", "") or "—")
                due = str(todo.get("due", "") or "—")
                lines.append(f"| {task} | {owner} | {due} |")
            lines.append("")

    lines.append("## 完整转写")
    lines.append("")
    for seg in segments:
        label = _speaker_label(seg.get("speaker"))
        lines.append(f"**[{_hms(seg['start_ms'])}] {label}**：{seg['text']}")
        lines.append("")

    return "\n".join(lines)


def to_text(segments: list[dict]) -> str:
    return "\n".join(seg["text"] for seg in segments if seg.get("text"))


def to_json(meeting: dict, segments: list[dict], minutes: dict | None) -> str:
    return json.dumps(
        {
            "meeting": meeting,
            "minutes": minutes,
            "segments": segments,
            "exported_at": datetime.now().isoformat(),
        },
        ensure_ascii=False,
        indent=2,
    )


EXPORTERS = {
    "srt": ("srt", "application/x-subrip"),
    "vtt": ("vtt", "text/vtt"),
    "md": ("md", "text/markdown"),
    "txt": ("txt", "text/plain"),
    "json": ("json", "application/json"),
    "docx": (
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "pdf": ("pdf", "application/pdf"),
}
