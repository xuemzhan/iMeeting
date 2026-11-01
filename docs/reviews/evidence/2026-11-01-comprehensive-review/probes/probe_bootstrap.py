"""Probe: does GET /api/v1/auth/bootstrap leak the admin password in production?"""

import os
import sys
import tempfile

tmp = tempfile.mkdtemp(prefix="imeeting-bootstrap-")
os.environ.pop("LLM_API_KEY", None)
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{tmp}/prod.db",
        "DATA_DIR": tmp,
        "SECRET_KEY": "0123456789abcdef0123456789abcdef",
        "MEETING_SKIP_ALEMBIC": "1",
        "BCRYPT_ROUNDS": "4",
        "ASR_MODE": "echo",
        "SKIP_MODEL_WARMUP": "1",
        "REQUIRE_AUTH": "1",
        "APP_ENV": "production",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "Sup3r-Secret-Prod-Pass",
    }
)
sys.path.insert(0, r"E:\Workspace\imeeting\backend\src")

from fastapi.testclient import TestClient  # noqa: E402

from imeeting.main import app  # noqa: E402

with TestClient(app) as client:
    resp = client.get("/api/v1/auth/bootstrap")
    print("status:", resp.status_code)
    body = resp.json()
    print("using_default_password:", body.get("using_default_password"))
    print("returned password:", body.get("default_password"))

    login = client.post(
        "/api/v1/auth/login",
        json={"username": body["default_username"], "password": body.get("default_password") or ""},
    )
    print("login with that password:", login.status_code)
