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
