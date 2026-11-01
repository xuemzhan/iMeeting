"""Probe: does a config secret survive a restart as usable plaintext?

Writes an encrypted llm.api_key exactly like PUT /api/v1/config does, then
simulates a process restart by rebuilding the config store from database rows.
"""

import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp(prefix="imeeting-probe-")
os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/probe.db"
os.environ["SECRET_KEY"] = "0123456789abcdef0123456789abcdef"
os.environ["MEETING_SKIP_ALEMBIC"] = "1"
os.environ["DATA_DIR"] = tmp
os.environ["BCRYPT_ROUNDS"] = "4"
os.environ.pop("LLM_API_KEY", None)

sys.path.insert(0, r"E:\Workspace\imeeting\backend\src")

from sqlmodel import Session, select  # noqa: E402

from imeeting.db import engine, init_db, persist_config  # noqa: E402
from imeeting.models import AppConfigRow  # noqa: E402
from imeeting.security import encrypt_value  # noqa: E402
from imeeting.settings import ConfigStore  # noqa: E402

# backend/.env is loaded by settings at import time and would win over the DB
# layer through the documented env > db > default precedence; drop it so the
# probe measures the database round-trip only.
os.environ.pop("LLM_API_KEY", None)
print("env override still present:", "LLM_API_KEY" in os.environ)

init_db()

PLAIN = "sk-live-key-should-survive-restart"
persist_config({"llm.api_key": encrypt_value(PLAIN)})

with Session(engine) as session:
    rows = {row.key: row.value for row in session.exec(select(AppConfigRow)).all()}

print("stored row value (ciphertext expected):", rows["llm.api_key"][:40], "...")

# Simulate restart: brand-new store, loaded only from the database.
store = ConfigStore()
store.load_overrides(rows)
effective = store.get("llm.api_key")
print("effective after restart :", repr(effective)[:60])
print("is plaintext            :", effective == PLAIN)
print("is still ciphertext     :", effective == json.loads(rows["llm.api_key"]))
