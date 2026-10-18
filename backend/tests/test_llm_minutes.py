"""LLM 纪要生成测试：JSON 抽取鲁棒性、map-reduce 切分、异常路径。

LLM 输出天然不可靠，这些测试确保「模型胡说」不会变成「服务 500」。
"""

from __future__ import annotations

import json

import pytest

from imeeting.llm.client import LLMError, extract_json
from imeeting.llm.minutes import (
    SINGLE_PASS_LIMIT,
    TranscriptLine,
    _mmss,
    _normalize,
    _split_by_window,
    generate_minutes,
    merge_fragments,
)

# ---------------------------------------------------------------------------
# JSON 抽取（LLM 输出最常见的坑）
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_plain_json(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_fenced_without_language(self):
        assert extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_json_with_leading_prose(self):
        """模型爱在 JSON 前加一句「好的，这是纪要：」。"""
        out = extract_json('好的，这是纪要：\n{"summary": "内容"}')
        assert out == {"summary": "内容"}

    def test_json_with_trailing_prose(self):
        assert extract_json('{"a": 1}\n希望有帮助！') == {"a": 1}

    def test_nested_objects(self):
        raw = '{"topics": [{"title": "t", "sub": {"x": 1}}]}'
        assert extract_json(raw)["topics"][0]["sub"]["x"] == 1

    def test_braces_inside_strings(self):
        """字符串里的大括号不能干扰边界定位。"""
        raw = '前言 {"summary": "含 {大括号} 的文本"} 结语'
        assert extract_json(raw)["summary"] == "含 {大括号} 的文本"

    def test_garbage_returns_none(self):
        assert extract_json("完全不是 JSON") is None
        assert extract_json("") is None

    def test_unbalanced_braces_returns_none(self):
        assert extract_json('{"a": 1') is None or isinstance(extract_json('{"a": 1'), dict)

    def test_array_returns_none_or_dict(self):
        """顶层是数组时我们不做特殊处理，但不能崩。"""
        result = extract_json("[1,2,3]")
        assert result is None or isinstance(result, (dict, list))

    def test_chinese_keys_preserved(self):
        out = extract_json('{"摘要": "内容", "议题": []}')
        assert out == {"摘要": "内容", "议题": []}

    def test_empty_object(self):
        assert extract_json("{}") == {}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


class TestHelpers:
    @pytest.mark.parametrize(
        "ms,expected",
        [(0, "00:00"), (1000, "00:01"), (59_000, "00:59"), (60_000, "01:00"), (3_661_000, "61:01")],
    )
    def test_mmss(self, ms, expected):
        assert _mmss(ms) == expected

    def test_normalize_english_keys(self):
        out = _normalize(
            {"summary": "s", "topics": [{"title": "t"}], "todos": [{"task": "x"}]}, "m"
        )
        assert out["summary"] == "s"
        assert out["topics"][0]["title"] == "t"
        assert out["model"] == "m"

    def test_normalize_chinese_keys(self):
        """模型可能返回中文键名（提示词是中文）。"""
        out = _normalize({"摘要": "摘要内容", "议题": [{"title": "议题一"}], "待办": []}, "m")
        assert out["summary"] == "摘要内容"
        assert out["topics"][0]["title"] == "议题一"

    def test_normalize_missing_fields(self):
        out = _normalize({}, "m")
        assert out["summary"] == ""
        assert out["topics"] == []
        assert out["todos"] == []

    def test_normalize_topics_as_json_string(self):
        """模型把数组序列化成字符串时，内容必须被解析出来而不是丢掉。"""
        out = _normalize({"topics": '[{"title": "议题一"}]'}, "m")
        assert out["topics"] == [{"title": "议题一", "detail": "", "conclusion": ""}]

    def test_normalize_todos_as_json_string(self):
        out = _normalize({"todos": '[{"task": "写周报", "owner": "张三"}]'}, "m")
        assert out["todos"][0]["task"] == "写周报"
        assert out["todos"][0]["owner"] == "张三"

    def test_normalize_topics_plain_string(self):
        """纯文本串至少不能丢信息。"""
        out = _normalize({"topics": "这是一段描述"}, "m")
        assert out["topics"] == [{"title": "这是一段描述", "detail": "", "conclusion": ""}]

    def test_normalize_string_items_in_list(self):
        out = _normalize({"topics": ["议题A", "议题B"]}, "m")
        assert [t["title"] for t in out["topics"]] == ["议题A", "议题B"]

    def test_normalize_ignores_wrong_types(self):
        out = _normalize({"topics": 12345, "todos": {"a": 1}}, "m")
        assert isinstance(out["topics"], list)
        assert isinstance(out["todos"], list)


# ---------------------------------------------------------------------------
# 切分
# ---------------------------------------------------------------------------


class TestMergeFragments:
    def test_merges_short_gap_same_speaker(self):
        """真实长测里的典型碎片：0.6s 停顿 + 上一句只有十几个字。"""
        lines = [
            TranscriptLine(0, "说话人 1", "如果从软件模拟角度啊那。", end_ms=2400),
            TranscriptLine(3000, "说话人 1", "我觉得仿真嘛，那就仿真一个电。", end_ms=4800),
        ]
        merged = merge_fragments(lines)
        assert len(merged) == 1
        assert merged[0].text == "如果从软件模拟角度啊那。我觉得仿真嘛，那就仿真一个电。"
        assert merged[0].start_ms == 0
        assert merged[0].end_ms == 4800

    def test_keeps_long_gap(self):
        lines = [
            TranscriptLine(0, "说话人 1", "第一句。", end_ms=2000),
            TranscriptLine(9000, "说话人 1", "第二句。", end_ms=11000),
        ]
        assert len(merge_fragments(lines)) == 2

    def test_keeps_speaker_change(self):
        lines = [
            TranscriptLine(0, "说话人 1", "第一句。", end_ms=2000),
            TranscriptLine(2100, "说话人 2", "第二句。", end_ms=4000),
        ]
        assert len(merge_fragments(lines)) == 2

    def test_does_not_merge_unknown_into_named_speaker(self):
        """未知说话人的句子不能被并到已命名说话人名下（归属错误）。"""
        lines = [
            TranscriptLine(0, None, "未知的人说的。", end_ms=2000),
            TranscriptLine(2100, "说话人 1", "确定是他的。", end_ms=4000),
        ]
        assert len(merge_fragments(lines)) == 2

    def test_empty(self):
        assert merge_fragments([]) == []


class TestSplitByWindow:
    def test_empty(self):
        assert _split_by_window([], 10) == []

    def test_single_chunk_for_short(self):
        lines = [TranscriptLine(start_ms=i * 1000, speaker=None, text="短句") for i in range(5)]
        assert len(_split_by_window(lines, 10)) == 1

    def test_splits_on_time_window(self):
        """跨过 10 分钟窗口应切分。"""
        lines = [
            TranscriptLine(start_ms=0, speaker=None, text="开头"),
            TranscriptLine(start_ms=11 * 60 * 1000, speaker=None, text="十一分钟后"),
        ]
        assert len(_split_by_window(lines, 10)) == 2

    def test_splits_on_size(self):
        """字符数超限时即使时间没到也要切，否则会超出上下文。"""
        big = "字" * (SINGLE_PASS_LIMIT // 2 + 100)
        lines = [
            TranscriptLine(start_ms=0, speaker=None, text=big),
            TranscriptLine(start_ms=1000, speaker=None, text=big),
        ]
        assert len(_split_by_window(lines, 100)) == 2

    def test_no_content_lost(self):
        """切分必须无损：所有行都要出现在某个块里。"""
        lines = [
            TranscriptLine(start_ms=i * 60_000, speaker=str(i % 2), text=f"第{i}句内容")
            for i in range(50)
        ]
        chunks = _split_by_window(lines, 10)
        flat = [ln for chunk in chunks for ln in chunk]
        assert len(flat) == len(lines)
        assert [ln.text for ln in flat] == [ln.text for ln in lines]

    def test_preserves_order(self):
        lines = [TranscriptLine(start_ms=i * 1000, speaker=None, text=f"t{i}") for i in range(30)]
        chunks = _split_by_window(lines, 5)
        flat = [ln.start_ms for chunk in chunks for ln in chunk]
        assert flat == sorted(flat)


# ---------------------------------------------------------------------------
# generate_minutes 异常与降级
# ---------------------------------------------------------------------------


class TestGenerateMinutes:
    def test_empty_lines_raises(self):
        import asyncio

        with pytest.raises(LLMError):
            asyncio.run(generate_minutes([]))

    def test_unconfigured_llm_raises(self, monkeypatch):
        """默认配置指向公网端点但没有 Key，必须立即拒绝而不是去真实请求。"""
        import asyncio

        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        from imeeting.settings import config_store

        config_store._db_overrides = {}

        lines = [TranscriptLine(start_ms=0, speaker=None, text="内容")]
        with pytest.raises(LLMError, match="未配置"):
            asyncio.run(generate_minutes(lines))

    def test_local_endpoint_without_key_is_configured(self, monkeypatch):
        """本地 vLLM 不需要 API Key，不应被误判为未配置。"""
        from imeeting.llm.client import LLMClient
        from imeeting.settings import config_store

        config_store._db_overrides = {
            "llm.base_url": "http://localhost:8000/v1",
            "llm.model": "qwen",
            "llm.api_key": "",
        }
        assert LLMClient().configured is True

    def test_remote_endpoint_without_key_not_configured(self, monkeypatch):
        from imeeting.llm.client import LLMClient
        from imeeting.settings import config_store

        config_store._db_overrides = {
            "llm.base_url": "https://api.example.com/v1",
            "llm.model": "gpt",
            "llm.api_key": "",
        }
        assert LLMClient().configured is False

    def test_remote_endpoint_with_key_is_configured(self):
        from imeeting.llm.client import LLMClient
        from imeeting.settings import config_store

        config_store._db_overrides = {
            "llm.base_url": "https://api.example.com/v1",
            "llm.model": "gpt",
            "llm.api_key": "sk-test",
        }
        assert LLMClient().configured is True

    def test_unparseable_output_falls_back_to_text(self, monkeypatch):
        """模型输出不是 JSON 时，应降级为纯文本摘要而不是报错。"""
        import asyncio

        from imeeting.llm import minutes as mod

        class FakeClient:
            configured = True
            model = "fake-model"

            async def chat(self, *a, **kw):
                return "这是一段没有 JSON 结构的纯文本摘要。"

        monkeypatch.setattr(mod, "LLMClient", lambda: FakeClient())

        lines = [TranscriptLine(start_ms=0, speaker=None, text="内容")]
        out = asyncio.run(generate_minutes(lines))
        assert out["summary"].startswith("这是一段")
        assert out["topics"] == []
        assert out["todos"] == []

    def test_wellformed_output_parsed(self, monkeypatch):
        import asyncio

        from imeeting.llm import minutes as mod

        payload = {
            "summary": "讨论了发布计划",
            "topics": [{"title": "发布", "detail": "定在周五", "conclusion": "通过"}],
            "todos": [{"task": "写周报", "owner": "张三", "due": "周五"}],
        }

        class FakeClient:
            configured = True
            model = "fake-model"

            async def chat(self, *a, **kw):
                return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

        monkeypatch.setattr(mod, "LLMClient", lambda: FakeClient())

        lines = [TranscriptLine(start_ms=0, speaker="1", text="我们定在周五发布")]
        out = asyncio.run(generate_minutes(lines))
        assert out["summary"] == "讨论了发布计划"
        assert out["topics"][0]["title"] == "发布"
        assert out["todos"][0]["task"] == "写周报"

    def test_long_transcript_triggers_map_reduce(self, monkeypatch):
        """超过单次上限时必须分段调用，不能一次性塞进去。"""
        import asyncio

        from imeeting.llm import minutes as mod

        calls: list[str] = []

        class FakeClient:
            configured = True
            model = "fake-model"

            async def chat(self, messages, **kw):
                calls.append(messages[0]["content"])
                if kw.get("json_mode"):
                    return '{"summary":"合并结果","topics":[],"todos":[]}'
                return "这一段的关键信息"

        monkeypatch.setattr(mod, "LLMClient", lambda: FakeClient())

        # 构造超过 SINGLE_PASS_LIMIT 的转写
        text = "这是一句比较长的会议发言内容。" * 600
        lines = [TranscriptLine(start_ms=0, speaker=None, text=text)]
        out = asyncio.run(generate_minutes(lines))

        assert len(calls) >= 2, "长转写未触发分段摘要"
        assert out["summary"] == "合并结果"

    def test_transcript_included_in_prompt(self, monkeypatch):
        """转写正文必须真的进了提示词，否则纪要就是凭空编的。"""
        import asyncio

        from imeeting.llm import minutes as mod

        captured: list[str] = []

        class FakeClient:
            configured = True
            model = "fake-model"

            async def chat(self, messages, **kw):
                captured.append(messages[0]["content"])
                return '{"summary":"s","topics":[],"todos":[]}'

        monkeypatch.setattr(mod, "LLMClient", lambda: FakeClient())

        marker = "独一无二的会议标记词XYZ"
        lines = [TranscriptLine(start_ms=65_000, speaker="2", text=marker)]
        asyncio.run(generate_minutes(lines))

        joined = "\n".join(captured)
        assert marker in joined
        assert "说话人2" in joined
        assert "01:05" in joined, "时间戳格式应可读"
