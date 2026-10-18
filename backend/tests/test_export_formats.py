"""导出格式单元测试：时间码换算与边界。

这些函数是纯函数，适合做穷尽式边界测试——导出错了用户拿到的是废文件。
"""

from __future__ import annotations

import json

import pytest

from imeeting.export.formats import (
    _hms,
    _srt_time,
    _vtt_time,
    to_json,
    to_markdown,
    to_srt,
    to_text,
    to_vtt,
)


class TestTimeFormatting:
    @pytest.mark.parametrize(
        "ms,expected",
        [
            (0, "00:00:00,000"),
            (1, "00:00:00,001"),
            (999, "00:00:00,999"),
            (1000, "00:00:01,000"),
            (59_999, "00:00:59,999"),
            (60_000, "00:01:00,000"),
            (3_599_999, "00:59:59,999"),
            (3_600_000, "01:00:00,000"),
            (86_400_000, "24:00:00,000"),
            (3_661_001, "01:01:01,001"),
        ],
    )
    def test_srt_time(self, ms, expected):
        assert _srt_time(ms) == expected

    def test_negative_clamped_to_zero(self):
        """负数时间戳不能产出 "-1:00" 这种废时间码。"""
        assert _srt_time(-1) == "00:00:00,000"
        assert _srt_time(-99999) == "00:00:00,000"

    def test_vtt_uses_dot(self):
        assert _vtt_time(1_234) == "00:00:01.234"
        assert "," not in _vtt_time(3_661_001)

    @pytest.mark.parametrize(
        "ms,expected",
        [
            (0, "00:00"),
            (59_000, "00:59"),
            (60_000, "01:00"),
            (3_600_000, "01:00:00"),
            (3_661_000, "01:01:01"),
        ],
    )
    def test_hms(self, ms, expected):
        assert _hms(ms) == expected


class TestSrt:
    def test_empty_segments(self):
        assert to_srt([]).strip() == ""

    def test_sequence_starts_at_one(self):
        out = to_srt([{"start_ms": 0, "end_ms": 1000, "text": "a"}])
        assert out.startswith("1\n")

    def test_multiple_blocks_numbered(self):
        out = to_srt(
            [
                {"start_ms": 0, "end_ms": 1000, "text": "a"},
                {"start_ms": 2000, "end_ms": 3000, "text": "b"},
            ]
        )
        assert "1\n00:00:00,000 --> 00:00:01,000" in out
        assert "2\n00:00:02,000 --> 00:00:03,000" in out

    def test_zero_length_segment_gets_min_width(self):
        """零长度片段播放器会跳过，必须补到至少 500ms。"""
        out = to_srt([{"start_ms": 1000, "end_ms": 1000, "text": "x"}])
        assert "00:00:01,000 --> 00:00:01,500" in out

    def test_end_before_start_is_corrected(self):
        out = to_srt([{"start_ms": 5000, "end_ms": 1000, "text": "x"}])
        start, _, end = out.split("\n")[1].partition(" --> ")
        assert start < end

    def test_speaker_prefix_added(self):
        out = to_srt([{"start_ms": 0, "end_ms": 1000, "text": "你好", "speaker": "1"}])
        assert "说话人1：你好" in out

    def test_no_speaker_no_prefix(self):
        out = to_srt([{"start_ms": 0, "end_ms": 1000, "text": "你好"}])
        assert "你好" in out
        assert "说话人" not in out

    def test_crlf_free(self):
        """SRT 规范用 LF；混入 CR 可能导致部分播放器解析异常。"""
        assert "\r" not in to_srt([{"start_ms": 0, "end_ms": 1000, "text": "a"}])


class TestVtt:
    def test_starts_with_webvtt_header(self):
        out = to_vtt([{"start_ms": 0, "end_ms": 1000, "text": "a"}])
        assert out.startswith("WEBVTT")

    def test_header_blank_line_separates_cues(self):
        out = to_vtt([{"start_ms": 0, "end_ms": 1000, "text": "a"}])
        assert out.splitlines()[1] == ""

    def test_no_numeric_sequence_numbers(self):
        """VTT 的 cue 编号是可选的，但我们不输出，避免与时间戳行混淆。"""
        out = to_vtt([{"start_ms": 0, "end_ms": 1000, "text": "a"}])
        lines = out.splitlines()
        assert lines[2].startswith("00:00:00.000")

    def test_uses_dot_separator(self):
        out = to_vtt([{"start_ms": 0, "end_ms": 1234, "text": "a"}])
        assert "00:00:00.000 --> 00:00:01.234" in out

    def test_empty_segments_still_valid(self):
        out = to_vtt([])
        assert out.startswith("WEBVTT")


class TestText:
    def test_plain_concat(self):
        out = to_text(
            [
                {"start_ms": 0, "end_ms": 1000, "text": "第一句"},
                {"start_ms": 1000, "end_ms": 2000, "text": "第二句"},
            ]
        )
        assert "第一句" in out and "第二句" in out

    def test_empty(self):
        assert to_text([]).strip() == ""

    def test_no_timecodes_in_plain_text(self):
        out = to_text([{"start_ms": 0, "end_ms": 1000, "text": "内容"}])
        assert "-->" not in out


class TestMarkdown:
    def test_has_h1_title(self):
        out = to_markdown({"title": "季度复盘"}, [], None)
        assert out.lstrip().startswith("# 季度复盘")

    def test_untitled_fallback(self):
        out = to_markdown({}, [], None)
        assert out.lstrip().startswith("#")

    def test_includes_segments(self):
        out = to_markdown(
            {"title": "T"},
            [{"start_ms": 65_000, "end_ms": 67_000, "text": "内容", "speaker": "1"}],
            None,
        )
        assert "内容" in out
        assert "说话人1" in out

    def test_includes_minutes_when_present(self):
        minutes = {
            "summary": "本次会议讨论了发布计划",
            "topics": json.dumps([{"title": "发布", "detail": "定在周五"}], ensure_ascii=False),
            "todos": json.dumps([{"task": "写周报", "owner": "张三"}], ensure_ascii=False),
        }
        out = to_markdown({"title": "T"}, [], minutes)
        assert "发布计划" in out
        assert "发布" in out
        assert "写周报" in out

    def test_malformed_minutes_json_does_not_crash(self):
        """LLM 可能吐出坏 JSON，导出不能因此 500。"""
        minutes = {"summary": "s", "topics": "{not json", "todos": "[bad"}
        out = to_markdown({"title": "T"}, [], minutes)
        assert isinstance(out, str) and out


class TestJsonExport:
    def test_valid_json(self):
        out = to_json({"title": "T", "id": 1}, [], None)
        assert isinstance(json.loads(out), (dict, list))

    def test_contains_meeting_and_segments(self):
        out = to_json(
            {"title": "T", "id": 7},
            [{"start_ms": 0, "end_ms": 100, "text": "你好"}],
            None,
        )
        data = json.loads(out)
        assert "你好" in json.dumps(data, ensure_ascii=False)
        assert "7" in json.dumps(data)

    def test_chinese_not_escaped(self):
        """ensure_ascii 必须为 False，否则中文变成 \\uXXXX，用户看不懂。"""
        out = to_json({"title": "中文标题"}, [], None)
        assert "中文标题" in out
        assert "\\u4e2d" not in out

    def test_empty_inputs(self):
        assert json.loads(to_json({}, [], None)) is not None
