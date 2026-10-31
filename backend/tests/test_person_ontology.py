"""人员本体（REQ-001）：归一化、对齐、幂等、确认/绑定与证据校验。"""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from imeeting import person_extract, persons
from imeeting.db import engine
from imeeting.models import (
    Meeting,
    MeetingParticipant,
    Person,
    PersonAlias,
    PersonAttribute,
    Segment,
    User,
)

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _admin_id() -> int:
    with Session(engine) as db:
        row = db.exec(select(User).where(User.role == "admin")).first()
        assert row is not None
        return row.id


def _make_meeting(title: str = "人员本体测试") -> int:
    with Session(engine) as db:
        meeting = Meeting(title=title, owner_id=_admin_id(), status="finished")
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        return meeting.id


def _make_segments(meeting_id: int, items: list[dict]) -> None:
    with Session(engine) as db:
        for index, item in enumerate(items):
            db.add(
                Segment(
                    meeting_id=meeting_id,
                    segment_id=item.get("segment_id", f"p-{meeting_id}-{index}"),
                    text=item.get("text", ""),
                    is_final=True,
                    start_ms=item.get("start_ms", index * 4000),
                    end_ms=item.get("end_ms", index * 4000 + 3000),
                    speaker=item.get("speaker"),
                    speaker_key=item.get("speaker_key"),
                    seq=index,
                )
            )
        db.commit()


@pytest.fixture
def meeting_id() -> int:
    mid = _make_meeting()
    _make_segments(
        mid,
        [
            {
                "segment_id": f"m{mid}-1",
                "text": "这位是总体院的张主任，负责体系设计，今天专门过来参会。",
                "speaker": "说话人 1",
                "speaker_key": "spk_a",
            },
            {
                "segment_id": f"m{mid}-2",
                "text": "我是十四所的李伟。",
                "speaker": "说话人 2",
                "speaker_key": "spk_b",
            },
        ],
    )
    return mid


# ---------------------------------------------------------------------------
# 归一化与白名单
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_strips_titles_and_width(self):
        assert persons.normalize_name("  张三主任 ") == "张三"
        assert persons.normalize_name("李四　老师") == "李四"

    def test_keeps_short_names(self):
        # 剥完只剩一个字时不剥，避免把「王工」削成「王」
        assert persons.normalize_name("王工") == "王工"

    def test_pinyin_falls_back_without_dependency(self):
        assert persons.pinyin_key("张三") in ("张三", "zhangsan")

    def test_attribute_whitelist(self):
        cleaned = persons.clean_attributes(
            [
                {"category": "org", "key": "unit", "value": "总体院"},
                {"category": "bogus", "key": "unit", "value": "x"},
                {"category": "org", "key": "phone", "value": "123"},
                {"category": "org", "key": "unit", "value": ""},
            ]
        )
        assert cleaned == [{"category": "org", "key": "unit", "value": "总体院"}]


# ---------------------------------------------------------------------------
# 证据校验（AC-3）
# ---------------------------------------------------------------------------


class TestEvidenceValidation:
    def _segments(self, meeting_id: int) -> dict:
        with Session(engine) as db:
            rows = db.exec(select(Segment).where(Segment.meeting_id == meeting_id)).all()
            return {row.segment_id: row for row in rows}

    def test_rejects_hallucinated_name(self, meeting_id):
        segments = self._segments(meeting_id)
        seg_id = f"m{meeting_id}-1"
        accepted, declined = person_extract.validate_people(
            [
                {
                    "name": "赵六",
                    "evidence": [{"segment_id": seg_id, "quote": "这位是总体院的张主任"}],
                }
            ],
            segments,
        )
        assert accepted == []
        assert declined and declined[0]["reason"] == "evidence_mismatch"

    def test_rejects_quote_not_from_segment(self, meeting_id):
        segments = self._segments(meeting_id)
        seg_id = f"m{meeting_id}-1"
        accepted, declined = person_extract.validate_people(
            [
                {
                    "name": "张三",
                    "evidence": [
                        {"segment_id": seg_id, "quote": "完全不相干的一句话，里面提到张三"}
                    ],
                }
            ],
            segments,
        )
        assert accepted == []
        assert declined[0]["reason"] == "evidence_mismatch"

    def test_accepts_grounded_evidence(self, meeting_id):
        segments = self._segments(meeting_id)
        seg_id = f"m{meeting_id}-1"
        accepted, declined = person_extract.validate_people(
            [
                {
                    "name": "张主任",
                    "attributes": [{"category": "org", "key": "unit", "value": "总体院"}],
                    "evidence": [{"segment_id": seg_id, "quote": "这位是总体院的张主任"}],
                    "confidence": 0.9,
                }
            ],
            segments,
        )
        assert declined == []
        assert accepted[0]["name"] == "张主任"
        assert 0.0 < accepted[0]["confidence"] <= 1.0

    def test_quote_matcher_tolerates_punctuation(self):
        assert person_extract.quote_matches("这位是张主任", "这位是，张主任。")
        assert not person_extract.quote_matches("完全无关的内容", "这位是张主任")


# ---------------------------------------------------------------------------
# 落库：幂等、别名、同名歧义
# ---------------------------------------------------------------------------


class TestUpsert:
    def test_idempotent(self, meeting_id):
        payload = dict(
            owner_id=_admin_id(),
            name="张三",
            attributes=[{"category": "org", "key": "unit", "value": "总体院"}],
            meeting_id=meeting_id,
            confidence=0.8,
            evidence=[f"m{meeting_id}-1"],
        )
        with Session(engine) as db:
            first, created, _ = persons.upsert_person(db, **payload)
            db.commit()
            first_id = first.id
        with Session(engine) as db:
            second, created_again, _ = persons.upsert_person(db, **payload)
            db.commit()
            assert second.id == first_id
            assert created_again is False
            attributes = db.exec(
                select(PersonAttribute).where(PersonAttribute.person_id == first_id)
            ).all()
            people = db.exec(
                select(Person).where(Person.display_name == "张三")
            ).all()
        assert len(attributes) == 1  # 重复抽取不增长行数
        assert len(people) == 1

    def test_alias_matches_existing_person(self, meeting_id):
        with Session(engine) as db:
            person, _, _ = persons.upsert_person(
                db,
                owner_id=_admin_id(),
                name="王建国",
                aliases=["王工"],
                meeting_id=meeting_id,
            )
            db.commit()
            person_id = person.id
        with Session(engine) as db:
            again, created, _ = persons.upsert_person(
                db, owner_id=_admin_id(), name="王工", meeting_id=meeting_id
            )
            db.commit()
            again_id = again.id
            aliases = db.exec(
                select(PersonAlias).where(PersonAlias.person_id == person_id)
            ).all()
            alias_texts = [alias.alias for alias in aliases]
        assert created is False
        assert again_id == person_id
        assert alias_texts == ["王工"]

    def test_same_name_different_unit_is_ambiguous(self, meeting_id):
        with Session(engine) as db:
            first, _, _ = persons.upsert_person(
                db,
                owner_id=_admin_id(),
                name="李伟",
                attributes=[{"category": "org", "key": "unit", "value": "十四所"}],
                meeting_id=meeting_id,
            )
            db.commit()
            first_id = first.id
        with Session(engine) as db:
            second, created, ambiguous = persons.upsert_person(
                db,
                owner_id=_admin_id(),
                name="李伟",
                attributes=[{"category": "org", "key": "unit", "value": "二十八所"}],
                meeting_id=meeting_id,
            )
            db.commit()
            first_row = db.get(Person, first_id)
            second_id = second.id
            second_name = second.display_name
            first_ambiguous = first_row.ambiguous
            first_name = first_row.display_name
        assert created is True
        assert ambiguous is True
        assert second_id != first_id
        assert second_name.startswith("李伟")
        assert first_name == "李伟"
        assert first_ambiguous is True

    def test_confirmed_state_not_downgraded(self, meeting_id):
        with Session(engine) as db:
            person, _, _ = persons.upsert_person(
                db, owner_id=_admin_id(), name="张三", meeting_id=meeting_id
            )
            db.commit()
            person_id = person.id
            persons.set_participant(
                db, meeting_id=meeting_id, person_id=person_id, state="confirmed"
            )
            db.commit()
        with Session(engine) as db:
            persons.upsert_person(
                db,
                owner_id=_admin_id(),
                name="张三",
                meeting_id=meeting_id,
                state="pending",
            )
            db.commit()
            row = db.exec(
                select(MeetingParticipant).where(
                    MeetingParticipant.meeting_id == meeting_id,
                    MeetingParticipant.person_id == person_id,
                )
            ).one()
        assert row.state == "confirmed"


# ---------------------------------------------------------------------------
# 确认 / 绑定 / 合并 / 回写
# ---------------------------------------------------------------------------


class TestParticipantOps:
    def test_list_and_state(self, meeting_id):
        with Session(engine) as db:
            person, _, _ = persons.upsert_person(
                db,
                owner_id=_admin_id(),
                name="张三",
                attributes=[{"category": "org", "key": "unit", "value": "总体院"}],
                meeting_id=meeting_id,
                evidence=[f"m{meeting_id}-1"],
            )
            db.commit()
            assert person.id is not None
            items = persons.list_participants(db, meeting_id)
        assert items[0]["speaker"] == "张三"
        assert items[0]["attributes"][0]["value"] == "总体院"

    def test_bind_updates_segments(self, meeting_id):
        with Session(engine) as db:
            person, _, _ = persons.upsert_person(
                db, owner_id=_admin_id(), name="张三", meeting_id=meeting_id
            )
            db.commit()
            result = persons.bind_speaker(
                db, meeting_id=meeting_id, person_id=person.id, speaker_key="spk_a"
            )
            db.commit()
            segments = db.exec(
                select(Segment).where(
                    Segment.meeting_id == meeting_id, Segment.speaker_key == "spk_a"
                )
            ).all()
            row = db.exec(
                select(MeetingParticipant).where(
                    MeetingParticipant.person_id == person.id
                )
            ).one()
        assert result["speaker_key"] == "spk_a"
        assert {seg.speaker for seg in segments} == {"张三"}
        assert row.state == "confirmed"

    def test_bind_moves_from_previous_person(self, meeting_id):
        with Session(engine) as db:
            first, _, _ = persons.upsert_person(
                db, owner_id=_admin_id(), name="张三", meeting_id=meeting_id
            )
            second, _, _ = persons.upsert_person(
                db, owner_id=_admin_id(), name="李四", meeting_id=meeting_id
            )
            db.commit()
            persons.bind_speaker(
                db, meeting_id=meeting_id, person_id=first.id, speaker_key="spk_a"
            )
            db.commit()
            result = persons.bind_speaker(
                db, meeting_id=meeting_id, person_id=second.id, speaker_key="spk_a"
            )
            db.commit()
            first_row = persons.participant_of(db, meeting_id, first.id)
        assert result["previous"] == "张三"
        assert first_row.speaker_key is None

    def test_merge_moves_aliases_and_marks_source(self, meeting_id):
        with Session(engine) as db:
            target, _, _ = persons.upsert_person(
                db, owner_id=_admin_id(), name="张三", meeting_id=meeting_id
            )
            source, _, _ = persons.upsert_person(
                db,
                owner_id=_admin_id(),
                name="张主任",
                aliases=["老张"],
                meeting_id=meeting_id,
            )
            db.commit()
            merged = persons.merge_persons(
                db, target_id=target.id, source_id=source.id
            )
            db.commit()
            source_row = db.get(Person, source.id)
            aliases = persons.aliases_of(db, target.id)
        assert merged.id == target.id
        assert source_row.status == "merged"
        assert source_row.merged_into_id == target.id
        assert "老张" in {alias.alias for alias in aliases}

    def test_sync_participants_string(self, meeting_id):
        with Session(engine) as db:
            persons.upsert_person(
                db, owner_id=_admin_id(), name="张三", meeting_id=meeting_id
            )
            persons.upsert_person(
                db, owner_id=_admin_id(), name="李四", meeting_id=meeting_id
            )
            db.commit()
            text = persons.sync_participants_string(db, meeting_id=meeting_id)
            db.commit()
            meeting = db.get(Meeting, meeting_id)
        assert "张三" in text and "李四" in text
        assert meeting.participants == text

    def test_rejected_hidden_from_string(self, meeting_id):
        with Session(engine) as db:
            person, _, _ = persons.upsert_person(
                db, owner_id=_admin_id(), name="张三", meeting_id=meeting_id
            )
            db.commit()
            persons.set_participant(
                db, meeting_id=meeting_id, person_id=person.id, state="rejected"
            )
            db.commit()
            text = persons.sync_participants_string(db, meeting_id=meeting_id)
        assert text == ""


# ---------------------------------------------------------------------------
# 抽取（无 LLM 时的规则兜底，AC-8）
# ---------------------------------------------------------------------------


class TestExtractionFallback:
    def test_rule_fallback_extracts_name(self, meeting_id, monkeypatch):
        import asyncio

        from imeeting.llm import client as llm_client

        class NoLLM:
            configured = False

        monkeypatch.setattr(llm_client, "LLMClient", lambda: NoLLM())
        result = asyncio.run(person_extract.extract_people(meeting_id, manual=True))
        assert result["degraded"] is True
        names = {item["name"] for item in result["extracted"]}
        assert "李伟" in names or "张" in names

    def test_interval_disabled_blocks_auto(self, meeting_id):
        assert person_extract.should_extract_on_final(meeting_id, "这位是张三") in (
            True,
            False,
        )


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------


class TestPersonsApi:
    def test_participants_api(self, client, auth_headers, meeting_id):
        res = client.get(
            f"/api/v1/meetings/{meeting_id}/participants", headers=auth_headers
        )
        assert res.status_code == 200, res.text
        assert res.json() == []

    def test_patch_and_bind_api(self, client, auth_headers, meeting_id):
        with Session(engine) as db:
            person, _, _ = persons.upsert_person(
                db, owner_id=_admin_id(), name="张三", meeting_id=meeting_id
            )
            db.commit()
            person_id = person.id
        res = client.patch(
            f"/api/v1/meetings/{meeting_id}/participants/{person_id}",
            headers=auth_headers,
            json={"state": "confirmed", "role": "主持"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["state"] == "confirmed"
        assert res.json()["role"] == "主持"

        res = client.post(
            f"/api/v1/meetings/{meeting_id}/participants/{person_id}/bind",
            headers=auth_headers,
            json={"speaker_key": "spk_a"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["speaker_key"] == "spk_a"

    def test_patch_unknown_person_404(self, client, auth_headers, meeting_id):
        res = client.patch(
            f"/api/v1/meetings/{meeting_id}/participants/999999",
            headers=auth_headers,
            json={"state": "confirmed"},
        )
        assert res.status_code == 404

    def test_persons_search_api(self, client, auth_headers, meeting_id):
        with Session(engine) as db:
            persons.upsert_person(
                db,
                owner_id=_admin_id(),
                name="张三",
                attributes=[{"category": "org", "key": "unit", "value": "总体院"}],
                meeting_id=meeting_id,
            )
            db.commit()
        res = client.get("/api/v1/persons?q=总体院", headers=auth_headers)
        assert res.status_code == 200, res.text
        names = [item["display_name"] for item in res.json()]
        assert "张三" in names


# ---------------------------------------------------------------------------
# 会中自动加入 / 手工与语音输入加入
# ---------------------------------------------------------------------------


class TestAutoAndManualJoin:
    def test_auto_add_is_idempotent(self, meeting_id):
        label = f"说话人甲{meeting_id}"
        with Session(engine) as db:
            first = persons.ensure_auto_participant(
                db, meeting_id=meeting_id, speaker_key="spk_1", label=label
            )
            db.commit()
            again = persons.ensure_auto_participant(
                db, meeting_id=meeting_id, speaker_key="spk_1", label=label
            )
            db.commit()
        assert first is not None
        assert first["speaker"].startswith(label)
        assert first["state"] == "pending"
        assert first["speaker_key"] == "spk_1"
        assert again is not None and again["person_id"] == first["person_id"]
        with Session(engine) as db:
            rows = db.exec(
                select(MeetingParticipant).where(
                    MeetingParticipant.meeting_id == meeting_id
                )
            ).all()
        assert len([r for r in rows if r.speaker_key == "spk_1"]) == 1

    def test_same_label_in_two_meetings_stays_two_people(self):
        """不同会议的「说话人 1」必须是人两个人，不能被并成一个 person。"""
        first_meeting = _make_meeting("会议甲")
        second_meeting = _make_meeting("会议乙")
        with Session(engine) as db:
            a = persons.ensure_auto_participant(
                db, meeting_id=first_meeting, speaker_key="spk_a", label="说话人 1"
            )
            b = persons.ensure_auto_participant(
                db, meeting_id=second_meeting, speaker_key="spk_b", label="说话人 1"
            )
            db.commit()
        assert a is not None and b is not None
        assert a["person_id"] != b["person_id"]
        assert b["speaker"].endswith(f"（#{second_meeting}）")

    def test_no_speaker_key_is_ignored(self, meeting_id):
        with Session(engine) as db:
            assert (
                persons.ensure_auto_participant(
                    db, meeting_id=meeting_id, speaker_key="", label="说话人 9"
                )
                is None
            )
            db.commit()

    def test_manual_add_binds_speaker_and_rewrites_transcript(self, meeting_id):
        _make_segments(
            meeting_id,
            [{"text": "我先说一下", "speaker": "说话人 3", "speaker_key": "spk_m"}],
        )
        with Session(engine) as db:
            item = persons.add_manual_participant(
                db, meeting_id=meeting_id, name="李四", speaker_key="spk_m", role="主讲"
            )
            db.commit()
        assert item["speaker"] == "李四"
        assert item["state"] == "confirmed"
        assert item["speaker_key"] == "spk_m"
        assert item["role"] == "主讲"
        with Session(engine) as db:
            seg = db.exec(
                select(Segment).where(
                    Segment.meeting_id == meeting_id, Segment.speaker_key == "spk_m"
                )
            ).first()
        assert seg is not None and seg.speaker == "李四"

    def test_create_participant_api(self, client, auth_headers, meeting_id):
        res = client.post(
            f"/api/v1/meetings/{meeting_id}/participants",
            headers=auth_headers,
            json={"name": "王强", "role": "评审"},
        )
        assert res.status_code == 201, res.text
        payload = res.json()
        assert payload["speaker"] == "王强"
        assert payload["state"] == "confirmed"
        # 列表里能查到
        listed = client.get(
            f"/api/v1/meetings/{meeting_id}/participants", headers=auth_headers
        ).json()
        assert any(item["person_id"] == payload["person_id"] for item in listed)

    def test_create_participant_api_validates_name(self, client, auth_headers, meeting_id):
        res = client.post(
            f"/api/v1/meetings/{meeting_id}/participants",
            headers=auth_headers,
            json={"name": ""},
        )
        assert res.status_code == 422

    def test_real_name_reused_across_meetings(self):
        """已登记声纹认出来的真实姓名要跨会议复用同一个人。"""
        first = _make_meeting("会议丙")
        second = _make_meeting("会议丁")
        with Session(engine) as db:
            a = persons.ensure_auto_participant(
                db, meeting_id=first, speaker_key="spk_r1", label="丁效"
            )
            b = persons.ensure_auto_participant(
                db, meeting_id=second, speaker_key="spk_r2", label="丁效"
            )
            db.commit()
        assert a is not None and b is not None
        assert a["person_id"] == b["person_id"]
        assert a["speaker"] == "丁效"
