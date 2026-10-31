"""主模板（REQ-002 FR-5 的 ``pinned``）。

每人最多一条主模板：用户显式选定后，自动淘汰不得挤掉它，停用/替换必须先取消；
没选定则按质量推导一个 ``primary`` 候选。库层用 SQLite 部分唯一索引兜底。
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

from imeeting.db import engine
from imeeting.engine import speaker, voice_match, voice_template
from imeeting.engine.voice_template import Scenario
from imeeting.models import Voiceprint, VoiceprintTemplate

from .test_doc_code_gap_regressions import setup_pair, unit

DIM = 192


def _voiceprint(owner: int) -> int:
    with Session(engine) as db:
        row = Voiceprint(
            owner_id=owner,
            name="主模板测试",
            embedding=unit(0).tobytes(),
            sample_ms=4000,
            sample_count=4,
            provisional=False,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _template(voiceprint_id: int, vector: np.ndarray, *, quality: float = 0.5) -> int:
    with Session(engine) as db:
        row = voice_template.store_template(
            db,
            voiceprint_id=voiceprint_id,
            embedding=vector,
            scenario=Scenario(),
            quality=quality,
            confirmed=True,
        )
        db.commit()
        return row.id


def _flags(template_ids: list[int]) -> dict[int, dict]:
    with Session(engine) as db:
        return {
            row.id: {"pinned": row.pinned, "disabled": row.disabled, "slot": row.slot}
            for row in db.exec(
                select(VoiceprintTemplate).where(VoiceprintTemplate.id.in_(template_ids))
            ).all()
        }


@pytest.fixture(autouse=True)
def _reset_caches():
    speaker.invalidate_cache()
    voice_match.invalidate_cache()
    yield
    speaker.invalidate_cache()
    voice_match.invalidate_cache()


def test_pin_unpins_others_and_becomes_primary():
    owner, _ = setup_pair()
    voice_id = _voiceprint(owner)
    first = _template(voice_id, unit(0), quality=0.5)
    second = _template(voice_id, unit(1), quality=0.9)

    with Session(engine) as db:
        voice_match.set_template_flags(db, template_id=second, pinned=True)
        db.commit()
    assert _flags([first, second])[second]["pinned"] is True
    assert _flags([first, second])[first]["pinned"] is False

    with Session(engine) as db:
        voice_match.set_template_flags(db, template_id=first, pinned=True)
        db.commit()
    flags = _flags([first, second])
    assert flags[first]["pinned"] is True and flags[second]["pinned"] is False

    items = {item["template_id"]: item for item in voice_match.list_templates(voice_id)}
    assert items[first]["primary"] is True and items[first]["primary_source"] == "pinned"
    assert items[second]["primary"] is False


def test_primary_falls_back_to_quality_when_nothing_pinned():
    owner, _ = setup_pair()
    voice_id = _voiceprint(owner)
    low = _template(voice_id, unit(0), quality=0.3)
    high = _template(voice_id, unit(1), quality=0.95)
    items = {item["template_id"]: item for item in voice_match.list_templates(voice_id)}
    assert items[high]["primary"] is True and items[high]["primary_source"] == "quality"
    assert items[low]["primary"] is False


def test_only_active_templates_can_be_pinned():
    owner, _ = setup_pair()
    voice_id = _voiceprint(owner)
    row_id = _template(voice_id, unit(0))
    with Session(engine) as db:
        voice_match.set_template_flags(db, template_id=row_id, disabled=True)
    with Session(engine) as db, pytest.raises(ValueError, match="活跃模板"):
        voice_match.set_template_flags(db, template_id=row_id, pinned=True)

    with Session(engine) as db:
        row = db.get(VoiceprintTemplate, row_id)
        row.superseded_by = 999
        db.add(row)
        db.commit()
    with Session(engine) as db, pytest.raises(ValueError, match="活跃模板"):
        voice_match.set_template_flags(db, template_id=row_id, pinned=True)


def test_pinned_template_cannot_be_disabled_directly():
    owner, _ = setup_pair()
    voice_id = _voiceprint(owner)
    row_id = _template(voice_id, unit(0))
    with Session(engine) as db:
        voice_match.set_template_flags(db, template_id=row_id, pinned=True)
    with Session(engine) as db, pytest.raises(ValueError, match="主模板"):
        voice_match.set_template_flags(db, template_id=row_id, disabled=True)
    assert _flags([row_id])[row_id]["disabled"] is False
    with Session(engine) as db:
        voice_match.set_template_flags(db, template_id=row_id, pinned=False)
        voice_match.set_template_flags(db, template_id=row_id, disabled=True)
    assert _flags([row_id])[row_id]["disabled"] is True


def test_auto_eviction_never_retires_the_pinned_template():
    owner, _ = setup_pair()
    voice_id = _voiceprint(owner)
    # 5 条活跃模板占满槽位，主模板刻意选质量最低的那条
    ids = [_template(voice_id, unit(index), quality=0.2 + index / 10) for index in range(5)]
    pinned_id = ids[0]
    with Session(engine) as db:
        voice_match.set_template_flags(db, template_id=pinned_id, pinned=True)
    assert _flags(ids)[pinned_id]["pinned"] is True

    with Session(engine) as db:
        created = voice_template.store_template(
            db,
            voiceprint_id=voice_id,
            embedding=unit(11),
            scenario=Scenario(),
            quality=0.95,
            confirmed=True,
        )
        db.commit()
        new_id = created.id

    flags = _flags(ids + [new_id])
    assert flags[pinned_id]["pinned"] is True, "自动淘汰不能挤掉主模板"
    assert flags[pinned_id]["slot"] >= 0
    retired = [row_id for row_id in ids if flags[row_id]["slot"] < 0]
    assert len(retired) == 1, "只应淘汰一条非主模板"
    with Session(engine) as db:
        active = voice_template.active_templates(db, voice_id)
    assert len(active) == 5


def test_explicit_replacement_of_the_pinned_slot_is_refused():
    owner, _ = setup_pair()
    voice_id = _voiceprint(owner)
    row_id = _template(voice_id, unit(0))
    with Session(engine) as db:
        voice_match.set_template_flags(db, template_id=row_id, pinned=True)
    slot = _flags([row_id])[row_id]["slot"]
    with Session(engine) as db, pytest.raises(ValueError, match="主模板"):
        voice_template.store_template(
            db,
            voiceprint_id=voice_id,
            embedding=unit(2),
            scenario=Scenario(),
            quality=0.9,
            confirmed=True,
            preferred_slot=slot,
        )


def test_patch_endpoint_sets_and_clears_pinned(client, auth_headers):
    owner, _ = setup_pair()
    voice_id = _voiceprint(owner)
    first = _template(voice_id, unit(0))
    second = _template(voice_id, unit(1), quality=0.9)

    response = client.patch(
        f"/api/v1/voiceprints/{voice_id}/templates/{first}",
        headers=auth_headers,
        json={"pinned": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["pinned"] is True

    listing = client.get(f"/api/v1/voiceprints/{voice_id}/templates", headers=auth_headers)
    assert listing.status_code == 200, listing.text
    items = {item["template_id"]: item for item in listing.json()["templates"]}
    assert items[first]["primary"] is True
    assert items[second]["pinned"] is False

    cleared = client.patch(
        f"/api/v1/voiceprints/{voice_id}/templates/{first}",
        headers=auth_headers,
        json={"pinned": False},
    )
    assert cleared.status_code == 200 and cleared.json()["pinned"] is False

    with Session(engine) as db:
        voice_match.set_template_flags(db, template_id=second, disabled=True)
    refused = client.patch(
        f"/api/v1/voiceprints/{voice_id}/templates/{second}",
        headers=auth_headers,
        json={"pinned": True},
    )
    assert refused.status_code == 409, refused.text


def test_migration_adds_partial_unique_index(tmp_path):
    migration_root = Path(__file__).resolve().parents[1] / "migrations/versions"
    isolated = create_engine(f"sqlite:///{(tmp_path / 'pin.db').as_posix()}")
    with isolated.begin() as conn:
        conn.execute(sa.text("CREATE TABLE voiceprint (id INTEGER PRIMARY KEY)"))
        spec = importlib.util.spec_from_file_location(
            "voiceprint_templates_migration", migration_root / "20261031_03_voiceprint_templates.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()

        pin_spec = importlib.util.spec_from_file_location(
            "template_pin_migration", migration_root / "20261031_07_template_pin.py"
        )
        pin_module = importlib.util.module_from_spec(pin_spec)
        pin_spec.loader.exec_module(pin_module)
        with Operations.context(MigrationContext.configure(conn)):
            pin_module.upgrade()

        assert "pinned" in {c["name"] for c in sa.inspect(conn).get_columns("voiceprint_template")}
        conn.execute(
            sa.text("INSERT INTO voiceprint_template(voiceprint_id,slot,embedding,pinned) VALUES (1,0,X'00',1)")
        )
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text("INSERT INTO voiceprint_template(voiceprint_id,slot,embedding,pinned) VALUES (1,1,X'00',1)")
            )
        # 不同声纹各自可以有一条主模板
        conn.execute(
            sa.text("INSERT INTO voiceprint_template(voiceprint_id,slot,embedding,pinned) VALUES (2,0,X'00',1)")
        )
        # 同一声纹的非主模板不受限制
        conn.execute(
            sa.text("INSERT INTO voiceprint_template(voiceprint_id,slot,embedding,pinned) VALUES (1,2,X'00',0)")
        )
        with Operations.context(MigrationContext.configure(conn)):
            pin_module.downgrade()
        assert "pinned" not in {c["name"] for c in sa.inspect(conn).get_columns("voiceprint_template")}
    isolated.dispose()
