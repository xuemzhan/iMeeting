"""End-to-end smoke probe against a throwaway database (no models loaded)."""

import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp(prefix="imeeting-smoke-")
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{tmp}/smoke.db",
        "SECRET_KEY": "0123456789abcdef0123456789abcdef",
        "DATA_DIR": tmp,
        "MEETING_SKIP_ALEMBIC": "1",
        "BCRYPT_ROUNDS": "4",
        "ASR_MODE": "echo",
        "SKIP_MODEL_WARMUP": "1",
        "REQUIRE_AUTH": "1",
        "APP_ENV": "development",
    }
)
sys.path.insert(0, r"E:\Workspace\imeeting\backend\src")

from fastapi.testclient import TestClient  # noqa: E402

from imeeting.main import app  # noqa: E402

results = []


def check(name, fn):
    try:
        results.append((name, fn()))
    except Exception as exc:  # noqa: BLE001
        results.append((name, f"EXC {type(exc).__name__}: {exc}"))


with TestClient(app) as client:
    check("GET /api/v1/live", lambda: client.get("/api/v1/live").status_code)
    check("GET /api/v1/health", lambda: client.get("/api/v1/health").json())

    def metrics():
        r = client.get("/metrics")
        return r.status_code, ("meeting_" in r.text)

    check("GET /metrics (no token)", metrics)

    def anon_meetings():
        return client.get("/api/v1/meetings").status_code

    check("GET /api/v1/meetings (no token)", anon_meetings)

    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    results.append(("POST /auth/login", (login.status_code, login.json().get("user", {}).get("must_change_password"))))
    token = login.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    check("GET /api/v1/meetings (pending password)", lambda: client.get("/api/v1/meetings", headers=h).status_code)
    check("PUT /api/v1/config (pending password)", lambda: client.put("/api/v1/config", json={"values": {}}, headers=h).status_code)

    pwd = client.post(
        "/api/v1/auth/password",
        json={"current_password": "admin123", "new_password": "new-pass-1234"},
        headers=h,
    )
    results.append(("POST /auth/password", pwd.status_code))
    h = {"Authorization": f"Bearer {pwd.json()['access_token']}"}

    def create_meeting():
        return client.post("/api/v1/meetings", json={"title": "smoke"}, headers=h).status_code

    check("POST /api/v1/meetings", create_meeting)
    check("GET /api/v1/meetings (ok)", lambda: len(client.get("/api/v1/meetings", headers=h).json()))

    def bad_bulk_export():
        return client.post("/api/v1/meetings/bulk-export-jobs", json={"ids": ["x"], "format": "md"}, headers=h).status_code

    check("POST bulk-export-jobs with non-numeric id", bad_bulk_export)

    def unauth_config():
        return client.get("/api/v1/config/schema").status_code

    check("GET /api/v1/config/schema (no token)", unauth_config)

for name, value in results:
    print(f"{name}: {value}")
