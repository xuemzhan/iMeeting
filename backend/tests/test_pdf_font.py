"""PDF 导出字体降级链测试（IM-PLN-004 X-2）。

背景：旧实现把字体写死为 ``C:\\Windows\\Fonts\\msyh.ttc``，Linux 容器里必然失败并回退
``Helvetica``——而 Helvetica 没有 CJK 字形，且 reportlab **不报错**。因此"PDF 能生成"
不能作为通过标准；下面的用例断言的是"字体不是 Helvetica"与"PDF 真正引用了 CJK 字体"。

这些用例刻意与宿主机字体环境解耦：Windows 开发机上有 msyh.ttc，Linux CI 上没有，
两条路径都必须通过。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from imeeting.export import document_export as doc
from imeeting.export.document_export import build_pdf, resolve_pdf_font


@pytest.fixture(autouse=True)
def _clear_font_cache():
    """字体探测有进程级缓存，用例之间必须隔离。"""
    doc.reset_pdf_font_cache()
    yield
    doc.reset_pdf_font_cache()


def _meeting(title: str = "季度评审会议") -> SimpleNamespace:
    return SimpleNamespace(
        title=title,
        scheduled_at=None,
        participants="张三，李四",
        agenda="评审进度",
    )


def _segment(text: str = "这里是中文转写内容") -> SimpleNamespace:
    return SimpleNamespace(speaker="张三", start_ms=0, text=text)


def test_falls_back_to_builtin_cid_font_when_no_ttf_present(monkeypatch):
    """宿主没有任何 CJK TTF 时，必须落到内置 CID 字体，而不是 Helvetica。"""
    monkeypatch.setattr(doc, "_CJK_FONT_CANDIDATES", ())
    monkeypatch.setattr(doc, "_configured_font_path", lambda: "")

    font_name, source = resolve_pdf_font()

    assert font_name != "Helvetica", "无 TTF 时应使用内置 CID 字体，不能用无 CJK 字形的 Helvetica"
    assert font_name == "STSong-Light"
    assert source == "cid:builtin"


def test_missing_configured_font_warns_and_still_falls_back(monkeypatch, caplog):
    """显式配置了不存在的字体路径：必须告警，然后继续走降级链（而不是静默用坏字体）。"""
    monkeypatch.setattr(doc, "_CJK_FONT_CANDIDATES", ())
    monkeypatch.setattr(doc, "_configured_font_path", lambda: "/nonexistent/font.ttf")

    with caplog.at_level(logging.WARNING, logger=doc.__name__):
        font_name, _source = resolve_pdf_font()

    assert font_name != "Helvetica"
    assert any(
        "字体" in record.getMessage() and "不可用" in record.getMessage()
        for record in caplog.records
    ), "配置的字体失效时必须留下告警日志，否则运维无法察觉"


def test_without_any_cjk_font_it_degrades_loudly(monkeypatch, caplog):
    """连内置 CID 字体都不可用（极端环境）：降级到 Helvetica 并记 ERROR，但要明确告知。

    这条用例保证"最后一级兜底"是有日志的——旧实现的静默失败正是问题本身。
    """
    import reportlab.pdfbase.cidfonts as cidfonts

    monkeypatch.setattr(doc, "_CJK_FONT_CANDIDATES", ())
    monkeypatch.setattr(doc, "_configured_font_path", lambda: "")

    class _Boom:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("no cid fonts")

    monkeypatch.setattr(cidfonts, "UnicodeCIDFont", _Boom)

    with caplog.at_level(logging.ERROR, logger=doc.__name__):
        font_name, source = resolve_pdf_font()

    assert font_name == "Helvetica"
    assert source == "fallback:helvetica"
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def test_exported_pdf_references_a_cjk_font(monkeypatch):
    """导出的 PDF 必须引用 CJK 字体资源（内嵌 TTF 或 CID），而不是纯 Helvetica。"""
    monkeypatch.setattr(doc, "_CJK_FONT_CANDIDATES", ())
    monkeypatch.setattr(doc, "_configured_font_path", lambda: "")

    pdf = build_pdf(_meeting(), [_segment()], None)

    assert pdf.startswith(b"%PDF"), "仍是合法 PDF（回归保护）"
    assert b"FontFile2" in pdf or b"CIDFontType0" in pdf, (
        "PDF 未引用任何 CJK 字体资源，导出的中文会是缺字的废文件"
    )
    assert b"STSong" in pdf or b"FontFile2" in pdf


def test_font_choice_takes_effect_without_restart(monkeypatch):
    """后台改了 export.pdf_font_path 后，下一次导出必须重新探测（缓存带 override 键）。"""
    monkeypatch.setattr(doc, "_CJK_FONT_CANDIDATES", ())
    monkeypatch.setattr(doc, "_configured_font_path", lambda: "")

    first = resolve_pdf_font()
    assert first[1] == "cid:builtin"

    # 模拟管理员改成另一个（同样不存在的）路径：必须重新探测，而不是沿用缓存
    monkeypatch.setattr(doc, "_configured_font_path", lambda: "/another/missing.ttf")
    second = resolve_pdf_font()
    assert second[1] != "Helvetica"
    # 重新探测会尝试并失败 override，最终仍落到 CID；来源字符串不变但过程确实重跑过
    assert doc._font_cache is not None and doc._font_cache[0] == "/another/missing.ttf"


def test_resolver_is_cached_within_same_config(monkeypatch):
    """同一配置下重复调用不能反复扫描磁盘（每次导出都扫一遍会拖慢批量导出）。"""
    monkeypatch.setattr(doc, "_CJK_FONT_CANDIDATES", ())
    monkeypatch.setattr(doc, "_configured_font_path", lambda: "")

    calls = {"count": 0}
    real_is_file = doc.Path.is_file

    def counting_is_file(self):  # noqa: ANN001
        calls["count"] += 1
        return real_is_file(self)

    monkeypatch.setattr(doc.Path, "is_file", counting_is_file)

    resolve_pdf_font()
    first_round = calls["count"]
    resolve_pdf_font()
    resolve_pdf_font()

    assert calls["count"] == first_round, "同配置下不应重复探测字体文件"


def _pdf_text(pdf: bytes) -> str:
    """把导出的 PDF 重新解析成文本（X-2 的"可读"判据）。"""
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_cid_font_pdf_text_is_actually_readable(monkeypatch):
    """只用内置 CID 字体（Linux 容器的默认路径）时，中文必须仍能被读出。

    为什么这条比"PDF 里有 CIDFontType0"更硬：旧实现在缺字体时静默回退 Helvetica，
    生成的 PDF 照样有合法结构、照样能打开——只是中文全是缺字。只有把文本抽出来
    比对，才能区分"看起来正常的废文件"和"真的能读"。

    判别性已验证：强制 Helvetica 路径时，下面的中文断言全部为 False。
    """
    monkeypatch.setattr(doc, "_CJK_FONT_CANDIDATES", ())
    monkeypatch.setattr(doc, "_configured_font_path", lambda: "")
    assert resolve_pdf_font()[1] == "cid:builtin"

    pdf = build_pdf(_meeting(), [_segment()], None)
    text = _pdf_text(pdf)

    assert "季度评审会议" in text, "标题里的中文没能被读出——导出的是缺字废文件"
    assert "这里是中文转写内容" in text, "转写正文里的中文没能被读出"
    assert "张三" in text


def test_pdf_text_is_readable_whatever_font_this_host_offers():
    """不 mock 字体探测：这台机器能给什么字体就给什么，中文都必须读得出来。

    Windows 开发机走内嵌 TTF、Linux CI 走内置 CID，两条路径都要过——
    这正是 X-2"PDF 中文在 Linux 上不可读"的自动化版本。
    """
    _font, source = resolve_pdf_font()
    assert source != "fallback:helvetica", (
        "本机没有可用的 CJK 字体，降级到了 Helvetica；"
        "这条用例覆盖不到真实路径，请安装 fonts-noto-cjk 或配置 export.pdf_font_path"
    )

    pdf = build_pdf(_meeting(), [_segment()], None)
    text = _pdf_text(pdf)

    assert "季度评审会议" in text
    assert "这里是中文转写内容" in text
