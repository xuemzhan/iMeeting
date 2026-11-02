"""Normalise legacy voiceprint-template slots that fall outside the five-slot range.

The application refuses to start when any ``voiceprint_template.slot`` is greater
than 4 (see ``imeeting.db._migrate_schema``).  Older releases could write such
rows, and the only documented way out used to be a hand-written ``UPDATE``.  This
script performs that repair deterministically instead:

* groups that are already healthy are never touched;
* inside a broken group the keepers are ordered deterministically
  (pinned -> confirmed -> quality -> created_at -> id) and re-slotted into 0..4;
* anything beyond five keepers is retired the same way the engine retires a
  replaced template (negative slot + ``superseded_by`` pointing at the keeper),
  so it leaves the active set without losing the row or its sources.

Dry-run is the default.  ``--apply`` writes and prints the same plan it executed.

Usage (from ``backend/``)::

    python scripts/repair_template_slots.py --db data/meeting.db
    python scripts/repair_template_slots.py --db data/meeting.db --apply

Back up the database before ``--apply``: ``deploy/backup_sqlite.sh`` does an
online backup with an integrity check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SLOT_MIN = 0
SLOT_MAX = 4


def _database_url(db_path: str | None) -> str:
    if db_path:
        return f"sqlite:///{Path(db_path).as_posix()}"
    configured = os.getenv("DATABASE_URL")
    if configured:
        return configured
    default = Path(__file__).resolve().parents[1] / "data" / "meeting.db"
    return f"sqlite:///{default.as_posix()}"


def _sort_key(row) -> tuple:
    created = row.created_at or datetime.min.replace(tzinfo=timezone.utc)
    return (
        0 if row.pinned else 1,
        0 if row.confirmed else 1,
        -(row.quality or 0.0),
        created,
        row.id or 0,
    )


def plan_group(rows: list) -> dict:
    """Return the rewrite plan for one voiceprint's templates.

    ``rows`` are all templates of a single voiceprint.  The plan only contains
    entries that actually change, so a healthy group plans nothing.
    """
    active = [row for row in rows if (row.slot or 0) >= SLOT_MIN]
    if all(row.slot <= SLOT_MAX for row in active):
        return {"moves": [], "retires": []}

    ordered = sorted(active, key=_sort_key)
    keepers = ordered[: SLOT_MAX + 1]
    extras = ordered[SLOT_MAX + 1 :]

    moves = []
    for index, row in enumerate(keepers):
        if row.slot != index:
            moves.append({"id": row.id, "voiceprint_id": row.voiceprint_id, "from": row.slot, "to": index})

    anchor = keepers[0].id if keepers else None
    retires = []
    for row in extras:
        target_slot = -int(row.id or 0) or -1
        retires.append(
            {
                "id": row.id,
                "voiceprint_id": row.voiceprint_id,
                "from": row.slot,
                "to": target_slot,
                "superseded_by": anchor,
            }
        )
    return {"moves": moves, "retires": retires}


def repair(engine, *, apply: bool) -> dict:
    from sqlmodel import Session, select

    from imeeting.models import VoiceprintTemplate

    with Session(engine) as db:
        rows = list(db.exec(select(VoiceprintTemplate).order_by(VoiceprintTemplate.voiceprint_id, VoiceprintTemplate.id)).all())
        grouped: dict[int, list] = {}
        for row in rows:
            grouped.setdefault(row.voiceprint_id, []).append(row)

        plan: dict[int, dict] = {}
        for voiceprint_id, group in grouped.items():
            group_plan = plan_group(group)
            if group_plan["moves"] or group_plan["retires"]:
                plan[voiceprint_id] = group_plan

        if apply and plan:
            # Free the target range first: a second pass assigns 0..4 only after
            # every out-of-range row has been parked, so the unique
            # (voiceprint_id, slot) constraint cannot fire mid-rewrite.
            for group_plan in plan.values():
                for item in group_plan["moves"] + group_plan["retires"]:
                    row = db.get(VoiceprintTemplate, item["id"])
                    if row is None:
                        continue
                    row.slot = -abs(int(row.id or 1))
                    db.add(row)
            db.flush()
            for group_plan in plan.values():
                for item in group_plan["moves"]:
                    row = db.get(VoiceprintTemplate, item["id"])
                    if row is None:
                        continue
                    row.slot = item["to"]
                    db.add(row)
                for item in group_plan["retires"]:
                    row = db.get(VoiceprintTemplate, item["id"])
                    if row is None:
                        continue
                    row.slot = item["to"]
                    row.superseded_by = item["superseded_by"]
                    db.add(row)
            db.commit()

        total_moves = sum(len(item["moves"]) for item in plan.values())
        total_retires = sum(len(item["retires"]) for item in plan.values())
        return {
            "applied": bool(apply),
            "database": str(engine.url),
            "scanned_templates": len(rows),
            "scanned_voiceprints": len(grouped),
            "violating_voiceprints": len(plan),
            "moves": total_moves,
            "retires": total_retires,
            "plan": {str(key): value for key, value in plan.items()},
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", help="SQLite database path; defaults to DATABASE_URL or data/meeting.db")
    parser.add_argument("--apply", action="store_true", help="write the repair (dry-run otherwise)")
    parser.add_argument("--json", action="store_true", help="print the full plan as JSON")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    try:
        from sqlmodel import create_engine
    except ModuleNotFoundError:  # pragma: no cover - dependency guard
        print("sqlmodel is required: pip install -e .[dev]", file=sys.stderr)
        return 2

    engine = create_engine(_database_url(args.db))
    result = repair(engine, apply=args.apply)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif result["violating_voiceprints"] == 0:
        print(f"OK: {result['scanned_templates']} 条模板均在 0..4 槽位范围内，无需整理。")
    else:
        verb = "已整理" if args.apply else "待整理（dry-run，未写入）"
        print(
            f"{verb}: {result['violating_voiceprints']} 个声纹、{result['moves']} 条改槽、"
            f"{result['retires']} 条退役（共扫描 {result['scanned_templates']} 条模板）。"
        )
        print("槽位迁移与退役明细：" + json.dumps(result["plan"], ensure_ascii=False, default=str))
        if not args.apply:
            print("确认无误后加 --apply 执行；执行前请先备份数据库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
