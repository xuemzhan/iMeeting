"""Consistent SQLite backup and restore helpers."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


class SqliteBackupError(RuntimeError):
    pass


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, timeout=30)


def verify_sqlite(path: Path) -> None:
    if not path.is_file():
        raise SqliteBackupError(f"SQLite 文件不存在：{path}")
    connection = _connect(path)
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise SqliteBackupError(f"SQLite 文件无法打开：{path}") from exc
    finally:
        connection.close()
    if not result or result[0] != "ok":
        raise SqliteBackupError(f"SQLite 完整性校验失败：{result}")


def backup_sqlite(source: Path, target: Path) -> Path:
    """Create a transactionally consistent snapshot using SQLite's backup API."""
    verify_sqlite(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise SqliteBackupError(f"备份目标已存在：{target}")

    source_db = _connect(source)
    target_db = _connect(target)
    try:
        source_db.backup(target_db)
    finally:
        target_db.close()
        source_db.close()
    verify_sqlite(target)
    target.chmod(0o600)
    return target


def _checkpoint_sqlite(path: Path) -> None:
    """Fold a WAL into the main file before replacing it.

    The caller must have stopped the application. A busy checkpoint means
    another process still has the database open, so restoring would be unsafe.
    """
    connection = _connect(path)
    try:
        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.DatabaseError as exc:
        raise SqliteBackupError(f"SQLite WAL 检查点失败：{path}") from exc
    finally:
        connection.close()
    if result and result[0]:
        raise SqliteBackupError(f"数据库仍在使用，无法安全恢复：{path}")


def _preserve_unverified(current: Path, backup_dir: Path, stamp: str) -> Path:
    """Keep byte-for-byte copies when the current database cannot be verified."""
    safety = backup_dir / f"pre-restore-{stamp}.corrupt.db"
    if safety.exists():
        raise SqliteBackupError(f"安全副本已存在：{safety}")
    shutil.copy2(current, safety)
    safety.chmod(0o600)
    for suffix in ("-wal", "-shm"):
        sidecar = current.with_name(f"{current.name}{suffix}")
        if sidecar.is_file():
            copy = safety.with_name(f"{safety.name}{suffix}")
            shutil.copy2(sidecar, copy)
            copy.chmod(0o600)
    return safety


def _move_sidecars_aside(current: Path, stamp: str) -> list[tuple[Path, Path]]:
    moved: list[tuple[Path, Path]] = []
    try:
        for suffix in ("-wal", "-shm"):
            source = current.with_name(f"{current.name}{suffix}")
            if not source.exists():
                continue
            target = current.parent / f".meeting-restore-{stamp}{suffix}"
            source.replace(target)
            moved.append((source, target))
    except Exception:
        for original, hidden in reversed(moved):
            hidden.replace(original)
        raise
    return moved


def restore_sqlite(
    current: Path,
    backup: Path,
    backup_dir: Path,
) -> Path | None:
    """Atomically replace ``current`` with ``backup``.

    A safety snapshot of the current database is written first. The replacement
    is prepared and verified in a temporary file before ``os.replace`` makes it
    visible. Callers must stop the application before invoking this function.
    """
    verify_sqlite(backup)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    safety: Path | None = None
    if current.is_file():
        safety = backup_dir / f"pre-restore-{stamp}.db"
        try:
            backup_sqlite(current, safety)
        except SqliteBackupError:
            safety = _preserve_unverified(current, backup_dir, stamp)
        else:
            _checkpoint_sqlite(current)

    prepared = current.parent / f".meeting-restore-{stamp}.db"
    source_db = _connect(backup)
    prepared_db = _connect(prepared)
    try:
        source_db.backup(prepared_db)
    finally:
        prepared_db.close()
        source_db.close()

    moved_sidecars: list[tuple[Path, Path]] = []
    try:
        verify_sqlite(prepared)
        prepared.chmod(0o600)
        moved_sidecars = _move_sidecars_aside(current, stamp)
        prepared.replace(current)
    except Exception:
        for original, hidden in moved_sidecars:
            hidden.replace(original)
        prepared.unlink(missing_ok=True)
        raise
    for _, hidden in moved_sidecars:
        hidden.unlink(missing_ok=True)
    return safety


def _main() -> int:
    parser = argparse.ArgumentParser(description="iMeeting SQLite backup/restore")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup")
    backup.add_argument("--source", required=True, type=Path)
    backup.add_argument("--target", required=True, type=Path)

    restore = subparsers.add_parser("restore")
    restore.add_argument("--current", required=True, type=Path)
    restore.add_argument("--backup", required=True, type=Path)
    restore.add_argument("--backup-dir", required=True, type=Path)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--path", required=True, type=Path)

    args = parser.parse_args()
    try:
        if args.command == "backup":
            print(backup_sqlite(args.source, args.target))
        elif args.command == "verify":
            verify_sqlite(args.path)
            print(f"verified={args.path}")
        else:
            safety = restore_sqlite(args.current, args.backup, args.backup_dir)
            if safety is not None:
                print(f"safety={safety}")
            print(f"restored={args.current}")
    except (OSError, SqliteBackupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
