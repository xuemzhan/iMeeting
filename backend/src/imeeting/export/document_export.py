"""Word / PDF 导出（含公司页眉、页脚）。"""

from __future__ import annotations

import io
import logging
import os
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

#: TTF 候选路径，按平台覆盖。命中第一个存在的文件即用。
_CJK_FONT_CANDIDATES: tuple[str, ...] = (
    # Linux（生产镜像装 fonts-noto-cjk 后命中第一条；arphic/wqy 为常见替代）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    # Windows
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
)

_TTF_FONT_NAME = "MeetingCJK"
_CID_FONT_NAME = "STSong-Light"
_FALLBACK_FONT_NAME = "Helvetica"

#: (显式配置值, 字体名, 来源说明)。None 表示尚未探测。缓存里带 override，
#: 这样后台改了 ``export.pdf_font_path`` 之后下一次导出立即生效，不必重启。
_font_cache: tuple[str, str, str] | None = None


def reset_pdf_font_cache() -> None:
    """清空字体探测缓存；供配置变更与测试使用。"""
    global _font_cache
    _font_cache = None


def _configured_font_path() -> str:
    """显式指定字体的来源：环境变量 > 配置项 > 空。

    环境变量优先，便于容器里挂载企业字体而无需改数据库（与三级回退一致）。
    """
    env_value = os.getenv("IMEETING_PDF_FONT", "").strip()
    if env_value:
        return env_value
    try:
        from ..settings import get_config

        return str(get_config("export.pdf_font_path") or "").strip()
    except Exception:  # pragma: no cover - 配置层不可用时不阻断导出
        return ""


def resolve_pdf_font() -> tuple[str, str]:
    """按降级链决定 PDF 正文字体，返回 ``(字体名, 来源)``。

    为什么要这条链：旧实现直接写死 ``C:\\Windows\\Fonts\\msyh.ttc``，在 Linux 容器里
    必然失败并回退 ``Helvetica``——而 Helvetica 没有 CJK 字形，**且 reportlab 不报错**，
    于是导出的中文 PDF 是一份"看起来正常"的废文件（现有测试只断言 ``%PDF`` 魔数，抓不到）。

    四级降级：显式指定 TTF → 常见 CJK 字体路径 → reportlab 内置 CID 字体 → Helvetica。
    内置 CID 字体不内嵌（PDF 内无 ``FontFile``），依赖阅读器自带字形，只作兜底。
    """
    global _font_cache
    override = _configured_font_path()
    if _font_cache is not None and _font_cache[0] == override:
        return _font_cache[1], _font_cache[2]

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    tried: set[str] = set()
    override = _configured_font_path()
    for path in (override, *_CJK_FONT_CANDIDATES):
        if not path or path in tried:
            continue
        tried.add(path)
        try:
            if not Path(path).is_file():
                raise FileNotFoundError(path)
            pdfmetrics.registerFont(TTFont(_TTF_FONT_NAME, path, subfontIndex=0))
        except Exception as exc:
            if path == override:
                # 显式配置失效必须显式告警，否则运维以为生效了。
                logger.warning(
                    "导出的 PDF 字体配置不可用（%s：%s），继续尝试内置候选", path, exc
                )
            else:
                logger.debug("跳过 PDF 字体候选 %s：%s", path, exc)
            continue
        _font_cache = (override, _TTF_FONT_NAME, f"ttf:{path}")
        logger.info("PDF 导出使用内嵌字体 %s", path)
        return _font_cache[1], _font_cache[2]

    try:
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont

        pdfmetrics.registerFont(UnicodeCIDFont(_CID_FONT_NAME))
        _font_cache = (override, _CID_FONT_NAME, "cid:builtin")
        logger.warning(
            "未找到可用的 CJK TTF 字体，PDF 导出回退内置 %s（不内嵌字形，"
            "严格阅读器/PDF-A 场景需安装字体或设置 IMEETING_PDF_FONT）",
            _CID_FONT_NAME,
        )
        return _font_cache[1], _font_cache[2]
    except Exception:
        logger.error(
            "PDF 导出无任何 CJK 字体可用，回退 Helvetica——中文将不可读；"
            "请安装 fonts-noto-cjk 或设置 IMEETING_PDF_FONT"
        )
        _font_cache = (override, _FALLBACK_FONT_NAME, "fallback:helvetica")
        return _font_cache[1], _font_cache[2]


def _mmss(ms: int) -> str:
    total = max(0, ms) // 1000
    return f"{total // 60:02d}:{total % 60:02d}"


def _minutes_dict(minutes) -> dict:
    if minutes is None:
        return {"summary": "", "topics": [], "todos": []}
    import json

    def load(raw):
        try:
            value = json.loads(raw or "[]")
            return value if isinstance(value, list) else []
        except Exception:
            return []

    return {
        "summary": minutes.summary,
        "topics": load(minutes.topics),
        "todos": load(minutes.todos),
    }


def build_docx(meeting, segments, minutes, *, company="", footer="") -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(10.5)

    title = doc.add_heading(meeting.title, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta = []
    if meeting.scheduled_at:
        meta.append(
            f"会议时间：{meeting.scheduled_at.strftime('%Y-%m-%d %H:%M')}"
        )
    if meeting.participants:
        meta.append(f"参会人：{meeting.participants}")
    if meeting.meeting_type:
        meta.append(f"类型：{meeting.meeting_type}")
    if meeting.agenda:
        meta.append(f"议程：{meeting.agenda}")
    for line in meta:
        p = doc.add_paragraph(line)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    data = _minutes_dict(minutes)
    if data["summary"]:
        doc.add_heading("会议摘要", level=1)
        doc.add_paragraph(data["summary"])
    if data["topics"]:
        doc.add_heading("议题与结论", level=1)
        for topic in data["topics"]:
            doc.add_heading(str(topic.get("title") or "议题"), level=2)
            if topic.get("detail"):
                doc.add_paragraph(str(topic["detail"]))
            if topic.get("conclusion"):
                doc.add_paragraph(f"结论：{topic['conclusion']}")
    if data["todos"]:
        doc.add_heading("待办事项", level=1)
        for todo in data["todos"]:
            owner = f"（{todo.get('owner')}）" if todo.get("owner") else ""
            due = f" 截止：{todo.get('due')}" if todo.get("due") else ""
            doc.add_paragraph(
                f"• {todo.get('task', '')}{owner}{due}", style="List Bullet"
            )

    doc.add_heading("完整转写", level=1)
    for seg in segments:
        speaker = f"{seg.speaker}：" if seg.speaker else ""
        doc.add_paragraph(f"[{_mmss(seg.start_ms)}] {speaker}{seg.text}")

    section = doc.sections[0]
    if company:
        header = section.header.paragraphs[0]
        header.text = company
        header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    if footer:
        section.footer.paragraphs[0].text = footer

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def build_pdf(meeting, segments, minutes, *, company="", footer="") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    # 字体由降级链决定（见 resolve_pdf_font）：不得再写死平台路径。
    font_name, font_source = resolve_pdf_font()
    logger.debug("PDF 导出字体：%s（%s）", font_name, font_source)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Chtitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=18,
        leading=24,
        spaceAfter=12,
    )
    h1 = ParagraphStyle(
        "Chh1", parent=styles["Heading1"], fontName=font_name, fontSize=14, leading=20
    )
    h2 = ParagraphStyle(
        "Chh2", parent=styles["Heading2"], fontName=font_name, fontSize=12, leading=18
    )
    body = ParagraphStyle(
        "Chbody", parent=styles["BodyText"], fontName=font_name, fontSize=10.5, leading=16
    )
    small = ParagraphStyle(
        "Chsmall", parent=body, fontSize=9, leading=13, textColor=colors.grey
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=42,
        rightMargin=42,
        topMargin=48,
        bottomMargin=42,
    )
    story = [Paragraph(escape(meeting.title), title_style)]
    meta = []
    if meeting.scheduled_at:
        meta.append(f"会议时间：{meeting.scheduled_at.strftime('%Y-%m-%d %H:%M')}")
    if meeting.participants:
        meta.append(f"参会人：{meeting.participants}")
    if meeting.agenda:
        meta.append(f"议程：{meeting.agenda}")
    if meta:
        story.append(Paragraph("<br/>".join(escape(x) for x in meta), small))
        story.append(Spacer(1, 10))

    data = _minutes_dict(minutes)
    if data["summary"]:
        story.append(Paragraph("会议摘要", h1))
        story.append(Paragraph(escape(data["summary"]), body))
    if data["topics"]:
        story.append(Paragraph("议题与结论", h1))
        for topic in data["topics"]:
            story.append(Paragraph(escape(str(topic.get("title") or "议题")), h2))
            if topic.get("detail"):
                story.append(Paragraph(escape(str(topic["detail"])), body))
            if topic.get("conclusion"):
                story.append(
                    Paragraph(f"结论：{escape(str(topic['conclusion']))}", body)
                )
    if data["todos"]:
        story.append(Paragraph("待办事项", h1))
        for todo in data["todos"]:
            owner = f"（{todo.get('owner')}）" if todo.get("owner") else ""
            due = f" 截止：{todo.get('due')}" if todo.get("due") else ""
            story.append(
                Paragraph(
                    f"• {escape(str(todo.get('task', '')))}{escape(owner)}"
                    f"{escape(due)}",
                    body,
                )
            )

    story.append(PageBreak())
    story.append(Paragraph("完整转写", h1))
    for seg in segments:
        speaker = f"{seg.speaker}：" if seg.speaker else ""
        story.append(
            Paragraph(
                f"[{_mmss(seg.start_ms)}] {escape(speaker)}"
                f"{escape(seg.text or '')}",
                body,
            )
        )

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(font_name, 8)
        canvas.setFillColor(colors.grey)
        text = footer or company or ""
        if text:
            canvas.drawString(42, 24, text[:120])
        canvas.drawRightString(A4[0] - 42, 24, str(datetime.now().year))
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
