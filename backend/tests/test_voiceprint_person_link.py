"""声纹与人员的永久关联（REQ-001 身份统一 P1）。

覆盖本轮新增的 ``voiceprint.person_id``：绑定/确认时建立，合并时改挂，删除人员
时解除但保留生物特征；跨账号一律拒绝；旧库迁移只回填「唯一且同 owner」的关联。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlmodel import Session, create_engine, select

from imeeting import persons
from imeeting.db import engine
from imeeting.engine import speaker, voice_match, voice_template
from imeeting.engine.voice_template import Scenario
from imeeting.models import (
    Meeting,
    MeetingParticipant,
    Person,
    Segment,
    Voiceprint,
    VoiceprintTemplate,
)

from .test_doc_code_gap_regressions import header, setup_pair, unit

KEY = "spk_link"


def _meeting(owner_id: int, *, status: str = "finished") -> int:
    with Session(engine) as db:
        meeting = Meeting(owner_id=owner_id, title="声纹关联", status=status)
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        return meeting.id


def _person(owner_id: int, name: str, *, meeting_id: int | None = None, speaker_key: str | None = None) -> int:
    with Session(engine) as db:
        person = Person(owner_id=owner_id, display_name=name)
        db.add(person)
        db.flush()
        if meeting_id is not None:
            db.add(
                MeetingParticipant(
                    meeting_id=meeting_id,
                    person_id=person.id,
                    speaker_key=speaker_key,
                    state="confirmed" if speaker_key else "pending",
                )
            )
        db.commit()
        db.refresh(person)
        return person.id


def _voiceprint(owner_id: int, meeting_id: int, speaker_key: str, name: str = "声纹") -> int:
    with Session(engine) as db:
        row = Voiceprint(
            owner_id=owner_id,
            meeting_id=meeting_id,
            speaker_key=speaker_key,
            name=name,
            embedding=unit().tobytes(),
            sample_ms=4000,
            sample_count=4,
            provisional=False,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _person_of(voiceprint_id: int) -> int | None:
    with Session(engine) as db:
        return db.get(Voiceprint, voiceprint_id).person_id


@pytest.fixture(autouse=True)
def _reset_caches():
    speaker.invalidate_cache()
    voice_match.invalidate_cache()
    yield
    speaker.invalidate_cache()
    voice_match.invalidate_cache()


def test_bind_speaker_creates_permanent_link():
    owner, _ = setup_pair()
    meeting_id = _meeting(owner)
    person_id = _person(owner, "张三", meeting_id=meeting_id)
    voice_id = _voiceprint(owner, meeting_id, KEY, "旧显示名")
    assert _person_of(voice_id) is None

    with Session(engine) as db:
        persons.bind_speaker(db, meeting_id=meeting_id, person_id=person_id, speaker_key=KEY)
        db.commit()

    assert _person_of(voice_id) == person_id
    with Session(engine) as db:
        voice = db.get(Voiceprint, voice_id)
        assert persons.voiceprint_person(db, voice).display_name == "张三"


def test_rename_is_visible_through_the_link_without_touching_the_voiceprint(client, monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    owner, _ = setup_pair()
    meeting_id = _meeting(owner)
    person_id = _person(owner, "张三", meeting_id=meeting_id)
    voice_id = _voiceprint(owner, meeting_id, KEY, "旧显示名")
    with Session(engine) as db:
        persons.bind_speaker(db, meeting_id=meeting_id, person_id=person_id, speaker_key=KEY)
        persons.set_participant(db, meeting_id=meeting_id, person_id=person_id, display_name="张三丰")
        db.commit()

    with Session(engine) as db:
        assert db.get(Voiceprint, voice_id).name == "旧显示名", "声纹自身的名字不必被改写"

    response = client.get(f"/api/v1/voiceprints/{voice_id}/templates", headers=header(owner))
    assert response.status_code == 200, response.text
    assert response.json()["person"] == {"person_id": person_id, "display_name": "张三丰"}


def test_merge_repoints_voiceprints_to_the_survivor():
    owner, _ = setup_pair()
    meeting_id = _meeting(owner)
    target_id = _person(owner, "张三", meeting_id=meeting_id)
    source_id = _person(owner, "张三（旧）")
    voice_id = _voiceprint(owner, meeting_id, KEY)
    with Session(engine) as db:
        db.add(
            MeetingParticipant(
                meeting_id=meeting_id, person_id=source_id, speaker_key=KEY, state="confirmed"
            )
        )
        db.commit()
        voice_template.store_template(
            db,
            voiceprint_id=voice_id,
            embedding=unit(),
            scenario=Scenario(),
            quality=0.9,
            confirmed=True,
        )
        db.commit()
        persons.link_voiceprints_by_speaker(
            db, person_id=source_id, meeting_id=meeting_id, speaker_key=KEY
        )
        db.commit()

    assert _person_of(voice_id) == source_id
    with Session(engine) as db:
        persons.merge_persons(db, target_id=target_id, source_id=source_id)
        db.commit()

    assert _person_of(voice_id) == target_id
    with Session(engine) as db:
        assert db.get(Person, source_id).status == "merged"


def test_delete_person_detaches_but_keeps_biometrics():
    owner, _ = setup_pair()
    meeting_id = _meeting(owner)
    person_id = _person(owner, "李四", meeting_id=meeting_id)
    voice_id = _voiceprint(owner, meeting_id, KEY)
    with Session(engine) as db:
        voice_template.store_template(
            db, voiceprint_id=voice_id, embedding=unit(), scenario=Scenario(), quality=0.9, confirmed=True
        )
        persons.link_voiceprints_by_speaker(
            db, person_id=person_id, meeting_id=meeting_id, speaker_key=KEY
        )
        db.commit()
        persons.delete_person(db, person_id=person_id)
        db.commit()

    with Session(engine) as db:
        voice = db.get(Voiceprint, voice_id)
        assert voice is not None, "删除人员不应连带删除声纹"
        assert voice.person_id is None
        assert len(db.exec(select(VoiceprintTemplate).where(VoiceprintTemplate.voiceprint_id == voice_id)).all()) == 1


def test_cross_owner_link_is_refused():
    owner, other = setup_pair()
    meeting_id = _meeting(owner)
    person_id = _person(owner, "王五", meeting_id=meeting_id)
    foreign_voice = _voiceprint(other, meeting_id, KEY, "别人的声纹")

    with Session(engine) as db:
        changed = persons.link_voiceprints_by_speaker(
            db, person_id=person_id, meeting_id=meeting_id, speaker_key=KEY
        )
        db.commit()

    assert changed == 0
    assert _person_of(foreign_voice) is None


def test_confirm_sample_links_person_through_speaker_key(client, auth_headers):
    owner, _ = setup_pair()
    meeting_id = _meeting(owner)
    person_id = _person(owner, "赵六", meeting_id=meeting_id, speaker_key="spk_confirm")
    voice_id = _voiceprint(owner, meeting_id, "spk_confirm")
    with Session(engine) as db:
        db.add(
            Segment(
                meeting_id=meeting_id,
                segment_id="confirm-1",
                text="我是赵六",
                is_final=True,
                start_ms=0,
                end_ms=3000,
                speech_ms=3000,
                speaker_key="spk_confirm",
            )
        )
        db.commit()

    response = client.post(
        "/api/v1/voiceprints/confirm-sample",
        headers=auth_headers,
        json={
            "voiceprint_id": voice_id,
            "meeting_id": meeting_id,
            "segment_id": "confirm-1",
            "embedding": np.asarray(unit(), dtype=np.float32).tolist(),
            "decision": "confirm_self",
        },
    )
    assert response.status_code == 200, response.text
    assert _person_of(voice_id) == person_id


def test_voiceprint_merge_inherits_the_person_link(client, auth_headers):
    admin = auth_headers  # 管理员同时是两声纹的 owner，满足同账号合并
    with Session(engine) as db:
        from imeeting.models import User

        admin_id = db.exec(select(User).where(User.username == "admin")).first().id
    meeting_id = _meeting(admin_id)
    person_id = _person(admin_id, "钱七", meeting_id=meeting_id, speaker_key="spk_src")
    source_id = _voiceprint(admin_id, meeting_id, "spk_src", "说话人 2")
    target_id = _voiceprint(admin_id, meeting_id, "spk_tgt", "说话人 3")
    with Session(engine) as db:
        persons.link_voiceprints_by_speaker(
            db, person_id=person_id, meeting_id=meeting_id, speaker_key="spk_src"
        )
        db.commit()
    assert _person_of(source_id) == person_id and _person_of(target_id) is None

    response = client.post(
        f"/api/v1/voiceprints/{source_id}/merge/{target_id}", headers=admin
    )
    assert response.status_code == 200, response.text
    assert _person_of(target_id) == person_id, "合并后身份关联必须跟着 target 保留"


def test_migration_backfills_unambiguous_links_only(tmp_path):
    migration_root = Path(__file__).resolve().parents[1] / "migrations/versions"
    isolated = create_engine(f"sqlite:///{(tmp_path / 'link.db').as_posix()}")
    with isolated.begin() as conn:
        conn.execute(sa.text("CREATE TABLE meeting (id INTEGER PRIMARY KEY)"))
        conn.execute(
            sa.text(
                "CREATE TABLE person (id INTEGER PRIMARY KEY, owner_id INTEGER, status TEXT)"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TABLE meeting_participant (id INTEGER PRIMARY KEY, meeting_id INTEGER,"
                " person_id INTEGER, speaker_key TEXT)"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TABLE voiceprint (id INTEGER PRIMARY KEY, owner_id INTEGER,"
                " meeting_id INTEGER, speaker_key TEXT, name TEXT)"
            )
        )
        conn.execute(sa.text("INSERT INTO person VALUES (1,1,'active'),(2,2,'active')"))
        conn.execute(
            sa.text(
                "INSERT INTO meeting_participant VALUES (1,1,1,'k1'),(2,2,2,'k3')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO voiceprint VALUES"
                " (1,1,1,'k1','同 owner 唯一命中'),"
                " (2,2,1,'k1','owner 不一致'),"
                " (3,1,1,'k2','没有参会关系'),"
                " (4,NULL,2,'k3','旧数据 owner 为空')"
            )
        )
        spec = importlib.util.spec_from_file_location(
            "voiceprint_person_link", migration_root / "20261031_06_voiceprint_person_link.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        assert "person_id" in {c["name"] for c in sa.inspect(conn).get_columns("voiceprint")}
        rows = dict(conn.execute(sa.text("SELECT id, person_id FROM voiceprint")).fetchall())
        assert rows[1] == 1, "同 owner 且唯一命中应回填"
        assert rows[2] is None, "owner 不一致必须留空等人工处理"
        assert rows[3] is None, "没有参会关系不能猜"
        assert rows[4] == 2, "旧的无 owner 声纹可按参会关系回填"
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        assert "person_id" not in {c["name"] for c in sa.inspect(conn).get_columns("voiceprint")}
    isolated.dispose()
