"""说话人重要程度（关键 / 次要 / 一般）与纪要加权测试。

覆盖：
- 重要程度归一化（非法值不能炸，也不该把「一般」当成「关键」）
- 纪要先验：关键参会人进入 prompt 主约束、分段摘要的每句带角色标记
- 接口：设置/读取、排序、非法值、未知说话人、改名后不丢设置
- 数据：同一说话人重复设置只保留一行；改名迁移 label 兜底键
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlmodel import Session, select

from imeeting.db import engine
from imeeting.llm.minutes import _format_lines, generate_minutes
from imeeting.llm.types import TranscriptLine
from imeeting.models import Meeting, Segment, SpeakerRole
from imeeting.speaker_roles import (
    IMPORTANCE_KEY,
    IMPORTANCE_NORMAL,
    IMPORTANCE_SECONDARY,
    STATE_CONFIRMED,
    STATE_DELETED,
    STATE_VISIBLE,
    importance_prompt_section,
    migrate_label_key,
    normalize_importance,
    normalize_state,
    set_importance,
    set_state,
)

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _make_meeting(owner_id: int, title: str = "重要程度测试") -> int:
    with Session(engine) as db:
        meeting = Meeting(title=title, owner_id=owner_id, status="finished")
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        return meeting.id


def _make_segments(meeting_id: int, items: list[dict]) -> None:
    with Session(engine) as db:
        for i, item in enumerate(items):
            db.add(
                Segment(
                    meeting_id=meeting_id,
                    segment_id=item.get("segment_id", f"spk-test-{meeting_id}-{i}"),
                    text=item.get("text", ""),
                    is_final=True,
                    start_ms=item.get("start_ms", i * 4000),
                    end_ms=item.get("end_ms", i * 4000 + 3000),
                    speaker=item.get("speaker"),
                    speaker_key=item.get("speaker_key"),
                    seq=i,
                )
            )
        db.commit()


@pytest.fixture
def meeting_with_speakers(admin_user) -> int:
    meeting_id = _make_meeting(admin_user.id)
    _make_segments(
        meeting_id,
        [
            {"text": "先定一下发布节奏。", "speaker": "张三", "speaker_key": "spk_a"},
            {"text": "我补充一下风险点。", "speaker": "李四", "speaker_key": "spk_b"},
            {"text": "我记录一下。", "speaker": "王五", "speaker_key": "spk_c"},
        ],
    )
    return meeting_id


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------


class TestNormalizeImportance:
    @pytest.mark.parametrize("value", [1, 2, 3, "1", "2", "3"])
    def test_valid_values_kept(self, value):
        assert normalize_importance(value) in (1, 2, 3)

    @pytest.mark.parametrize("value", [0, 4, -1, 99, None, "", "abc", object()])
    def test_invalid_falls_back_to_normal(self, value):
        assert normalize_importance(value) == IMPORTANCE_NORMAL

    def test_one_is_most_important(self):
        assert IMPORTANCE_KEY < IMPORTANCE_SECONDARY < IMPORTANCE_NORMAL


# ---------------------------------------------------------------------------
# 纪要 prompt
# ---------------------------------------------------------------------------


class TestMinutesPrompt:
    def _lines(self):
        return [
            TranscriptLine(
                start_ms=0,
                speaker="张三",
                text="方案按 A 推进。",
                speaker_key="spk_a",
                speaker_importance=IMPORTANCE_KEY,
            ),
            TranscriptLine(
                start_ms=4000,
                speaker="李四",
                text="我建议补一个回滚预案。",
                speaker_key="spk_b",
                speaker_importance=IMPORTANCE_SECONDARY,
            ),
            TranscriptLine(
                start_ms=8000,
                speaker="王五",
                text="我没意见。",
                speaker_key="spk_c",
                speaker_importance=IMPORTANCE_NORMAL,
            ),
        ]

    def test_lines_carry_role_tag(self):
        text = _format_lines(self._lines())
        assert "张三（关键）" in text
        assert "李四（次要）" in text
        # 一般参会人不加标记，避免整屏标签噪音
        assert "王五（" not in text

    def test_prompt_section_lists_roles_and_rules(self):
        section = importance_prompt_section(self._lines())
        assert "关键参会人（1，最高）：张三" in section
        assert "次要参会人（2）：李四" in section
        assert "以**关键参会人**的发言为主干" in section
        assert "不得推翻或改写" in section
        # 一般参会人不应被写进清单（默认不显式设置）
        assert "王五" not in section

    def test_section_empty_without_explicit_roles(self):
        lines = [
            TranscriptLine(start_ms=0, speaker="张三", text="随便说说"),
            TranscriptLine(
                start_ms=1000,
                speaker="李四",
                text="我也一般",
                speaker_importance=IMPORTANCE_NORMAL,
            ),
        ]
        assert importance_prompt_section(lines) == ""

    def test_generate_minutes_includes_priority_constraints(self, monkeypatch):
        from imeeting.llm import minutes as mod

        captured: list[str] = []

        class FakeClient:
            configured = True
            model = "fake-model"

            async def chat(self, messages, **kwargs):
                captured.append(messages[0]["content"])
                return json.dumps(
                    {
                        "summary": "按 A 推进并补回滚预案",
                        "topics": [{"title": "发布", "detail": "", "conclusion": "确认"}],
                        "todos": [],
                    },
                    ensure_ascii=False,
                )

        monkeypatch.setattr(mod, "LLMClient", lambda: FakeClient())
        out = asyncio.run(generate_minutes(self._lines()))
        assert out["summary"]
        prompt = captured[0]
        assert "参会人重要程度" in prompt
        assert "关键参会人（1，最高）：张三" in prompt
        assert "张三（关键）" in prompt


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------


class TestSpeakerImportanceApi:
    def test_defaults_to_normal(self, client, auth_headers, meeting_with_speakers):
        mid = meeting_with_speakers
        res = client.get(f"/api/v1/meetings/{mid}/speakers", headers=auth_headers)
        assert res.status_code == 200, res.text
        items = res.json()
        names = [item["speaker"] for item in items]
        assert names == ["张三", "李四", "王五"]
        assert all(item["importance"] == 3 for item in items)
        assert all(item["segments"] == 1 for item in items)
        assert all(item["speech_ms"] == 3000 for item in items)

    def test_set_and_read_back(self, client, auth_headers, meeting_with_speakers):
        mid = meeting_with_speakers
        res = client.put(
            f"/api/v1/meetings/{mid}/speakers/importance",
            headers=auth_headers,
            json={"speaker": "李四", "importance": 1},
        )
        assert res.status_code == 200, res.text
        items = res.json()
        assert items[0]["speaker"] == "李四"
        assert items[0]["importance"] == 1

        # 排序：1 关键 → 2 次要 → 3 一般；一般之间按发言时长
        res = client.put(
            f"/api/v1/meetings/{mid}/speakers/importance",
            headers=auth_headers,
            json={"speaker": "王五", "importance": 2},
        )
        order = [(i["speaker"], i["importance"]) for i in res.json()]
        assert order == [("李四", 1), ("王五", 2), ("张三", 3)]

        # 读取回到同样的结果
        reread = client.get(f"/api/v1/meetings/{mid}/speakers", headers=auth_headers)
        assert [(i["speaker"], i["importance"]) for i in reread.json()] == order

    def test_meeting_detail_embeds_speakers(
        self, client, auth_headers, meeting_with_speakers
    ):
        mid = meeting_with_speakers
        client.put(
            f"/api/v1/meetings/{mid}/speakers/importance",
            headers=auth_headers,
            json={"speaker": "张三", "importance": 1},
        )
        res = client.get(f"/api/v1/meetings/{mid}", headers=auth_headers)
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["speakers"][0]["speaker"] == "张三"
        assert data["speakers"][0]["importance"] == 1
        # 片段也要带 speaker_key，前端才能按人对齐
        assert data["segments"][0]["speaker_key"] == "spk_a"

    def test_invalid_importance_rejected(
        self, client, auth_headers, meeting_with_speakers
    ):
        mid = meeting_with_speakers
        for bad in (0, 4, -1):
            res = client.put(
                f"/api/v1/meetings/{mid}/speakers/importance",
                headers=auth_headers,
                json={"speaker": "张三", "importance": bad},
            )
            assert res.status_code == 422, res.text

    def test_unknown_speaker_404(self, client, auth_headers, meeting_with_speakers):
        mid = meeting_with_speakers
        res = client.put(
            f"/api/v1/meetings/{mid}/speakers/importance",
            headers=auth_headers,
            json={"speaker": "查无此人", "importance": 1},
        )
        assert res.status_code == 404

    def test_setting_twice_keeps_single_row(
        self, client, auth_headers, meeting_with_speakers
    ):
        mid = meeting_with_speakers
        for value in (1, 2):
            client.put(
                f"/api/v1/meetings/{mid}/speakers/importance",
                headers=auth_headers,
                json={"speaker": "张三", "importance": value},
            )
        with Session(engine) as db:
            rows = db.exec(
                select(SpeakerRole).where(SpeakerRole.meeting_id == mid)
            ).all()
        assert len(rows) == 1
        assert rows[0].importance == 2

    def test_missing_meeting_404(self, client, auth_headers):
        """接口复用 _owned，不存在的会议一律 404（不泄露是否存在）。"""
        res = client.get("/api/v1/meetings/987654/speakers", headers=auth_headers)
        assert res.status_code == 404
        res = client.put(
            "/api/v1/meetings/987654/speakers/importance",
            headers=auth_headers,
            json={"speaker": "张三", "importance": 1},
        )
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# 数据层
# ---------------------------------------------------------------------------


class TestSpeakerRoleStore:
    def test_rename_migrates_label_fallback_key(self, admin_user):
        """历史数据没有 speaker_key 时按 label 存；改名后要迁移过去。"""
        meeting_id = _make_meeting(admin_user.id, "改名迁移")
        _make_segments(
            meeting_id,
            [{"text": "没有 speaker_key 的老片段", "speaker": "说话人 1"}],
        )
        with Session(engine) as db:
            items = set_importance(db, meeting_id, "说话人 1", 1)
            assert items[0]["importance"] == 1
            key = items[0]["speaker_key"]
            assert key == "label:说话人 1"

            moved = migrate_label_key(db, meeting_id, "说话人 1", "张三")
            assert moved == 1
            row = db.exec(
                select(SpeakerRole).where(SpeakerRole.meeting_id == meeting_id)
            ).one()
            assert row.speaker_key == "label:张三"
            assert row.importance == 1

    def test_rename_keeps_more_important_role(self, admin_user):
        """目标键已存在时，改名不能把关键参会人降级成一般。"""
        meeting_id = _make_meeting(admin_user.id, "改名冲突")
        _make_segments(
            meeting_id,
            [{"text": "老片段", "speaker": "说话人 1"}],
        )
        with Session(engine) as db:
            set_importance(db, meeting_id, "说话人 1", 1)
            db.add(
                SpeakerRole(
                    meeting_id=meeting_id,
                    speaker_key="label:张三",
                    label="张三",
                    importance=3,
                )
            )
            db.commit()
            migrate_label_key(db, meeting_id, "说话人 1", "张三")
            rows = db.exec(
                select(SpeakerRole).where(SpeakerRole.meeting_id == meeting_id)
            ).all()
        assert len(rows) == 1
        assert rows[0].speaker_key == "label:张三"
        assert rows[0].importance == 1


# ---------------------------------------------------------------------------
# 确认 / 删除（参会人面板里逐行操作）
# ---------------------------------------------------------------------------


class TestSpeakerStateApi:
    def test_confirm_marks_state_and_keeps_transcript(
        self, client, auth_headers, meeting_with_speakers
    ):
        mid = meeting_with_speakers
        res = client.put(
            f"/api/v1/meetings/{mid}/speakers/state",
            headers=auth_headers,
            json={"speaker": "张三", "state": STATE_CONFIRMED},
        )
        assert res.status_code == 200, res.text
        by_name = {item["speaker"]: item for item in res.json()}
        assert by_name["张三"]["state"] == STATE_CONFIRMED
        assert by_name["李四"]["state"] == STATE_VISIBLE
        # 只标记状态：重要程度、片段数、发言时长都不动
        assert by_name["张三"]["importance"] == IMPORTANCE_NORMAL
        assert by_name["张三"]["segments"] == 1
        assert by_name["张三"]["speech_ms"] == 3000

    def test_delete_marks_state(self, client, auth_headers, meeting_with_speakers):
        mid = meeting_with_speakers
        res = client.put(
            f"/api/v1/meetings/{mid}/speakers/state",
            headers=auth_headers,
            json={"speaker": "王五", "state": STATE_DELETED},
        )
        assert res.status_code == 200, res.text
        by_name = {item["speaker"]: item for item in res.json()}
        assert by_name["王五"]["state"] == STATE_DELETED

        reread = client.get(f"/api/v1/meetings/{mid}/speakers", headers=auth_headers)
        assert {item["speaker"]: item["state"] for item in reread.json()}["王五"] == (
            STATE_DELETED
        )

    def test_invalid_state_rejected(
        self, client, auth_headers, meeting_with_speakers
    ):
        mid = meeting_with_speakers
        for bad in (0, 3, -1):
            res = client.put(
                f"/api/v1/meetings/{mid}/speakers/state",
                headers=auth_headers,
                json={"speaker": "张三", "state": bad},
            )
            assert res.status_code == 422, res.text

    def test_unknown_speaker_404(self, client, auth_headers, meeting_with_speakers):
        mid = meeting_with_speakers
        res = client.put(
            f"/api/v1/meetings/{mid}/speakers/state",
            headers=auth_headers,
            json={"speaker": "查无此人", "state": STATE_CONFIRMED},
        )
        assert res.status_code == 404

    def test_importance_change_keeps_state(
        self, client, auth_headers, meeting_with_speakers
    ):
        """先删除再加重要程度：不能把状态重置回「待处理」。"""
        mid = meeting_with_speakers
        client.put(
            f"/api/v1/meetings/{mid}/speakers/state",
            headers=auth_headers,
            json={"speaker": "张三", "state": STATE_DELETED},
        )
        res = client.put(
            f"/api/v1/meetings/{mid}/speakers/importance",
            headers=auth_headers,
            json={"speaker": "张三", "importance": 1},
        )
        assert res.status_code == 200, res.text
        row = {item["speaker"]: item for item in res.json()}["张三"]
        assert row["importance"] == 1
        assert row["state"] == STATE_DELETED


class TestSpeakerIdentityDisambiguation:
    """同名不同声音：只按姓名写会落到别人的行上，必须用 speaker_key 精确定位。

    现场数据（2026-11-01 会议 13）里「周YJ」同时对应 spk_e8cfb8ba 与
    spk_9804104f，只发姓名时后端只能猜「最近发言的那一行」。
    """

    @staticmethod
    def _two_same_names(admin_user) -> int:
        meeting_id = _make_meeting(admin_user.id, "同名两个声音")
        _make_segments(
            meeting_id,
            [
                {"text": "我先说。", "speaker": "周YJ", "speaker_key": "spk_a", "start_ms": 0},
                {"text": "我也叫周YJ。", "speaker": "周YJ", "speaker_key": "spk_b", "start_ms": 9000},
            ],
        )
        return meeting_id

    def test_state_by_key_deletes_only_that_speaker(
        self, client, auth_headers, admin_user
    ):
        mid = self._two_same_names(admin_user)
        res = client.put(
            f"/api/v1/meetings/{mid}/speakers/state",
            headers=auth_headers,
            json={"speaker": "周YJ", "speaker_key": "spk_a", "state": STATE_DELETED},
        )
        assert res.status_code == 200, res.text
        by_key = {item["speaker_key"]: item["state"] for item in res.json()}
        assert by_key["spk_a"] == STATE_DELETED
        assert by_key.get("spk_b", STATE_VISIBLE) == STATE_VISIBLE

    def test_state_without_key_keeps_legacy_name_lookup(
        self, client, auth_headers, admin_user
    ):
        mid = self._two_same_names(admin_user)
        res = client.put(
            f"/api/v1/meetings/{mid}/speakers/state",
            headers=auth_headers,
            json={"speaker": "周YJ", "state": STATE_DELETED},
        )
        assert res.status_code == 200, res.text
        by_key = {item["speaker_key"]: item["state"] for item in res.json()}
        # 旧口径：按显示名定位到最近发言的那一条
        assert by_key["spk_b"] == STATE_DELETED

    def test_state_unknown_key_is_404(self, client, auth_headers, admin_user):
        mid = self._two_same_names(admin_user)
        res = client.put(
            f"/api/v1/meetings/{mid}/speakers/state",
            headers=auth_headers,
            json={"speaker": "周YJ", "speaker_key": "spk_不存在", "state": STATE_DELETED},
        )
        assert res.status_code == 404

    def test_importance_by_key_hits_exact_speaker(
        self, client, auth_headers, admin_user
    ):
        mid = self._two_same_names(admin_user)
        res = client.put(
            f"/api/v1/meetings/{mid}/speakers/importance",
            headers=auth_headers,
            json={"speaker": "周YJ", "speaker_key": "spk_b", "importance": 1},
        )
        assert res.status_code == 200, res.text
        by_key = {item["speaker_key"]: item["importance"] for item in res.json()}
        assert by_key["spk_b"] == 1
        assert by_key["spk_a"] == IMPORTANCE_NORMAL

    def test_label_key_targets_speaker_without_key(self, client, auth_headers, admin_user):
        mid = _make_meeting(admin_user.id, "无 key 的历史会议")
        _make_segments(mid, [{"text": "老数据。", "speaker": "张三", "speaker_key": None}])
        res = client.put(
            f"/api/v1/meetings/{mid}/speakers/state",
            headers=auth_headers,
            json={"speaker": "张三", "speaker_key": "label:张三", "state": STATE_CONFIRMED},
        )
        assert res.status_code == 200, res.text
        by_key = {item["speaker_key"]: item["state"] for item in res.json()}
        assert by_key["label:张三"] == STATE_CONFIRMED


class TestSpeakerStateStore:
    def test_set_state_is_idempotent_single_row(self, admin_user):
        meeting_id = _make_meeting(admin_user.id, "状态幂等")
        _make_segments(
            meeting_id,
            [{"text": "一句话", "speaker": "张三", "speaker_key": "spk_a"}],
        )
        with Session(engine) as db:
            set_state(db, meeting_id, "张三", STATE_CONFIRMED)
            items = set_state(db, meeting_id, "张三", STATE_CONFIRMED)
            assert items[0]["state"] == STATE_CONFIRMED
            rows = db.exec(
                select(SpeakerRole).where(SpeakerRole.meeting_id == meeting_id)
            ).all()
        assert len(rows) == 1

    def test_normalize_state_falls_back(self):
        for value in (0, 3, -1, None, "", "abc"):
            assert normalize_state(value) == STATE_VISIBLE
        assert normalize_state("1") == STATE_CONFIRMED
        assert normalize_state(2) == STATE_DELETED
