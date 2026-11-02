"""scripts/repair_template_slots.py —— 历史越界槽位（slot > 4）的整理路径。

背景：`imeeting.db._migrate_schema` 在发现 `slot > 4` 时拒绝启动，旧版本文档只给了
一条手工 UPDATE。本用例锁定脚本必须满足的三件事：dry-run 不写库、apply 之后全部
落在 0..4、多出来的模板按引擎既有口径退役（负槽位 + superseded_by）且可重复执行。
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from imeeting.models import Voiceprint, VoiceprintTemplate

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "repair_template_slots.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("repair_template_slots", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S.%f")


def _seed_template(path: Path, *, voiceprint_id: int, slots: list[int], pinned_slot: int | None = None) -> None:
    """Insert templates that a pre-check-constraint release could have written.

    The model carries ``CheckConstraint("slot <= 4")``, so the fixture has to
    relax it for the INSERT — exactly the situation of a legacy database that
    was created before that constraint existed.
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA ignore_check_constraints=ON")
        for index, slot in enumerate(slots):
            created = datetime(2026, 10, 1, 0, 0, index)
            conn.execute(
                "INSERT INTO voiceprint_template ("
                "voiceprint_id, slot, disabled, embedding, dim, scenario_device, scenario_distance,"
                "scenario_noise, scenario_voice, quality, sample_ms, snr_db, source_meeting_id,"
                "source_segment_id, confirmed, confirmed_by, confirmed_at, superseded_by, pinned,"
                "created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    voiceprint_id,
                    slot,
                    0,
                    b"",
                    192,
                    "",
                    "",
                    "",
                    "",
                    0.5 + index / 100.0,
                    4000,
                    0.0,
                    None,
                    None,
                    1,
                    None,
                    None,
                    None,
                    1 if slot == pinned_slot else 0,
                    _stamp(created),
                    _stamp(created),
                ),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def legacy(tmp_path):
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        row = Voiceprint(owner_id=1, name="历史越界", embedding=b"", sample_ms=0, sample_count=1, provisional=True)
        db.add(row)
        db.commit()
        db.refresh(row)
        voiceprint_id = row.id
    _seed_template(path, voiceprint_id=voiceprint_id, slots=[6, 7, 0, 1, 2, 3], pinned_slot=6)
    return engine, path, voiceprint_id


def _slots(engine, voiceprint_id: int) -> list[tuple[int, int, object]]:
    with Session(engine) as db:
        rows = db.exec(
            select(VoiceprintTemplate)
            .where(VoiceprintTemplate.voiceprint_id == voiceprint_id)
            .order_by(VoiceprintTemplate.slot)
        ).all()
        return [(row.id, row.slot, row.superseded_by) for row in rows]


def test_healthy_group_needs_no_rewrite():
    module = _load_script()

    class Row:
        def __init__(self, slot, pinned=False, confirmed=True, quality=0.5, created_at=None, row_id=1):
            self.slot = slot
            self.pinned = pinned
            self.confirmed = confirmed
            self.quality = quality
            self.created_at = created_at or datetime(2026, 1, 1)
            self.id = row_id
            self.voiceprint_id = 1

    plan = module.plan_group([Row(0, row_id=1), Row(1, row_id=2)])
    assert plan == {"moves": [], "retires": []}


def test_dry_run_reports_without_writing(legacy):
    module = _load_script()
    engine, _, voiceprint_id = legacy
    before = _slots(engine, voiceprint_id)

    result = module.repair(engine, apply=False)

    assert result["violating_voiceprints"] == 1
    assert result["moves"] + result["retires"] > 0
    assert _slots(engine, voiceprint_id) == before


def test_apply_normalises_slots_and_retires_extras(legacy):
    module = _load_script()
    engine, _, voiceprint_id = legacy

    result = module.repair(engine, apply=True)
    assert result["applied"] is True

    rows = _slots(engine, voiceprint_id)
    active = [(row_id, slot) for row_id, slot, superseded in rows if slot >= 0]
    assert all(0 <= slot <= 4 for _, slot in active), active
    assert len(active) <= 5, active
    # 越界行必须离开活跃集合，但保留行本体与退役指向。
    retired = [(row_id, slot, superseded) for row_id, slot, superseded in rows if slot < 0]
    assert retired, "多出来的模板应退役而不是删除"
    assert all(superseded is not None for _, _, superseded in retired)
    # 主模板优先保留：原 slot=6 的 pinned 行必须仍在活跃集合里。
    with Session(engine) as db:
        pinned = db.exec(
            select(VoiceprintTemplate).where(
                VoiceprintTemplate.voiceprint_id == voiceprint_id,
                VoiceprintTemplate.pinned.is_(True),
            )
        ).one()
        assert pinned.slot >= 0
        assert pinned.superseded_by is None


def test_apply_is_idempotent(legacy):
    module = _load_script()
    engine, _, voiceprint_id = legacy

    module.repair(engine, apply=True)
    after_first = _slots(engine, voiceprint_id)
    second = module.repair(engine, apply=True)

    assert second["violating_voiceprints"] == 0
    assert _slots(engine, voiceprint_id) == after_first
