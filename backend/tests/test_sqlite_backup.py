"""SQLite backup and restore safety tests."""

import sqlite3

import pytest

from imeeting.sqlite_backup import (
    SqliteBackupError,
    backup_sqlite,
    restore_sqlite,
    verify_sqlite,
)


def _seed(path, values: list[str]) -> None:
    db = sqlite3.connect(path)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS note (value TEXT)")
        db.executemany("INSERT INTO note(value) VALUES (?)", [(v,) for v in values])
        db.commit()
    finally:
        db.close()


def _values(path) -> list[str]:
    db = sqlite3.connect(path)
    try:
        return [row[0] for row in db.execute("SELECT value FROM note ORDER BY rowid")]
    finally:
        db.close()


def test_backup_is_consistent_and_verified(tmp_path) -> None:
    current = tmp_path / "meeting.db"
    snapshot = tmp_path / "backups" / "meeting.db"
    _seed(current, ["a", "b"])

    backup_sqlite(current, snapshot)

    verify_sqlite(snapshot)
    assert _values(snapshot) == ["a", "b"]


def test_backup_refuses_to_overwrite_existing_target(tmp_path) -> None:
    current = tmp_path / "meeting.db"
    snapshot = tmp_path / "meeting.db.bak"
    _seed(current, ["a"])
    snapshot.write_text("existing")

    with pytest.raises(SqliteBackupError):
        backup_sqlite(current, snapshot)
    assert snapshot.read_text() == "existing"


def test_restore_replaces_database_and_keeps_safety_copy(tmp_path) -> None:
    current = tmp_path / "data" / "meeting.db"
    current.parent.mkdir()
    snapshot = tmp_path / "backups" / "known-good.db"
    _seed(current, ["old"])
    backup_sqlite(current, snapshot)
    _seed(current, ["changed"])

    safety = restore_sqlite(current, snapshot, tmp_path / "backups")

    assert _values(current) == ["old"]
    assert safety is not None and safety.exists()
    assert _values(safety) == ["old", "changed"]
    assert not (current.parent / f"{current.name}-wal").exists()
    assert not (current.parent / f"{current.name}-shm").exists()


def test_invalid_backup_does_not_touch_current_database(tmp_path) -> None:
    current = tmp_path / "meeting.db"
    invalid = tmp_path / "invalid.db"
    _seed(current, ["keep"])
    invalid.write_text("not sqlite")

    with pytest.raises(SqliteBackupError):
        restore_sqlite(current, invalid, tmp_path / "backups")

    assert _values(current) == ["keep"]


def test_restore_recovers_when_current_database_is_corrupt(tmp_path) -> None:
    current = tmp_path / "meeting.db"
    snapshot = tmp_path / "known-good.db"
    _seed(snapshot, ["known-good"])
    current.write_bytes(b"not a sqlite database")

    safety = restore_sqlite(current, snapshot, tmp_path / "backups")

    assert _values(current) == ["known-good"]
    assert safety is not None
    assert safety.name.endswith(".corrupt.db")
    assert safety.read_bytes() == b"not a sqlite database"
