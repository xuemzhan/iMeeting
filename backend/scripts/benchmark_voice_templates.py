"""Measure scoped template loading against a private SQLite fixture, without models."""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 500])
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if args.runs < 3 or any(size < 1 for size in args.sizes):
        parser.error("use at least three runs and positive fixture sizes")
    with tempfile.TemporaryDirectory(prefix="imeeting-template-benchmark-") as directory:
        os.environ.update(DATA_DIR=directory, DATABASE_URL=f"sqlite:///{Path(directory).as_posix()}/benchmark.db", ASR_MODE="off", MEETING_SKIP_ALEMBIC="1")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        import numpy as np
        import sqlalchemy as sa
        from sqlmodel import Session, SQLModel, create_engine

        from imeeting.engine import voice_template
        from imeeting.models import Meeting, User, Voiceprint, VoiceprintTemplate

        isolated = create_engine(os.environ["DATABASE_URL"])
        SQLModel.metadata.create_all(isolated)

        @contextmanager
        def scope():
            with Session(isolated) as db:
                yield db

        voice_template.session_scope = scope
        count = [0]

        @sa.event.listens_for(isolated, "before_cursor_execute")
        def query_count(*_):
            count[0] += 1

        rng = np.random.default_rng(20261031)
        fixture_hash = hashlib.sha256()
        results = []
        for size in args.sizes:
            with Session(isolated) as db:
                for table in reversed(SQLModel.metadata.sorted_tables):
                    db.execute(table.delete())
                db.add(User(id=1, username="benchmark", role="admin", password_hash="unused"))
                db.add(User(id=2, username="foreign", role="user", password_hash="unused"))
                db.flush()
                db.add(Meeting(id=1, owner_id=1, title="synthetic benchmark"))
                for index in range(size):
                    vector = rng.normal(size=192).astype(np.float32)
                    vector /= np.linalg.norm(vector)
                    encoded = vector.tobytes()
                    fixture_hash.update(encoded)
                    # Explicit and legacy inferred owners, both private scopes.
                    db.add(Voiceprint(id=index + 1, owner_id=None if index % 4 == 0 else (1 if index % 2 == 0 else 2), name=f"fixture-{index}", embedding=encoded))
                    db.flush()
                    for slot in range(5):
                        db.add(VoiceprintTemplate(voiceprint_id=index + 1, slot=slot, embedding=encoded, confirmed=True))
                db.commit()
            voice_template.templates_matrix(meeting_id=1)  # Discard one warm-up.
            timings, queries, digests = [], [], []
            profiler = cProfile.Profile()
            for _ in range(args.runs):
                count[0] = 0
                start = time.perf_counter()
                matrix, rows = voice_template.templates_matrix(meeting_id=1)
                timings.append((time.perf_counter() - start) * 1000)
                queries.append(count[0])
                digests.append(hashlib.sha256(matrix.tobytes() + json.dumps(rows, default=str, sort_keys=True).encode()).hexdigest())
            profiler.runcall(voice_template.templates_matrix, meeting_id=1)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            profiler.dump_stats(str(args.output.with_name(f"{args.output.stem}-{size}.prof")))
            assert len(set(digests)) == 1
            results.append({"people_total": size, "templates_total": size * 5, "returned_templates": len(rows), "runs": args.runs, "milliseconds": {"min": min(timings), "median": statistics.median(timings), "max": max(timings), "raw": timings}, "sql_queries": queries, "result_sha256": digests[0]})
        isolated.dispose()
        record = {"scenario": "scoped cold matrix loads, five templates/person, mixed explicit/legacy owners, fixed seed; no real inference", "python": platform.python_version(), "platform": platform.platform(), "cpu": platform.processor(), "numpy": np.__version__, "sqlalchemy": sa.__version__, "fixture_sha256": fixture_hash.hexdigest(), "warmup_runs_discarded": 1, "results": results}
        args.output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
