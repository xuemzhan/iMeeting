"""Measure queue monitoring cost in an isolated database; never opens app data."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=5000)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.jobs < 1 or args.runs < 3:
        parser.error("--jobs must be positive and --runs must be at least 3")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    with tempfile.TemporaryDirectory(prefix="imeeting-metrics-bench-") as folder:
        os.environ.update(
            DATABASE_URL=f"sqlite:///{Path(folder).as_posix()}/bench.db",
            DATA_DIR=folder,
            APP_ENV="development",
            SECRET_KEY="isolated-benchmark-secret-0123456789",
            BCRYPT_ROUNDS="4",
            MEETING_SKIP_ALEMBIC="1",
        )
        from imeeting.application.jobs import job_metrics, job_stats
        from imeeting.db import engine, init_db
        from imeeting.models import Job

        init_db()
        with engine.begin() as connection:
            connection.execute(
                Job.__table__.insert(),
                [dict(kind="export_single", status="succeeded", payload="x" * 4096)
                 for _ in range(args.jobs)],
            )
        job_stats()  # warm the connection and statement caches
        job_metrics()
        results = []
        for _ in range(args.runs):
            tracemalloc.start()
            started = time.perf_counter()
            assert job_stats()["succeeded"] == args.jobs
            assert job_metrics()["oldest_running_seconds"] == 0
            elapsed = time.perf_counter() - started
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            results.append({"seconds": round(elapsed, 6), "peak_bytes": peak})
        print(json.dumps({"python": platform.python_version(), "cpu": platform.processor(),
                          "jobs": args.jobs, "payload_bytes": 4096, "runs": results}, indent=2))
        engine.dispose()


if __name__ == "__main__":
    main()
