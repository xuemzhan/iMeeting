"""Consistent SQLite backup and restore helpers."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


class DrainError(RuntimeError):
    """排空过程中服务端的回应不符合预期。"""


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


def _drain_with(
    client: httpx.Client,
    base_url: str,
    username: str,
    password: str,
    timeout_seconds: int,
    poll_seconds: float,
) -> int:
    base = base_url.rstrip("/")
    login = client.post(
        f"{base}/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    login.raise_for_status()
    token = login.json().get("access_token")
    if not token:
        raise DrainError("登录响应里没有 access_token")
    headers = {"Authorization": f"Bearer {token}"}

    started = client.post(
        f"{base}/api/v1/admin/maintenance/drain",
        headers=headers,
        json={"reason": "graceful release"},
    )
    started.raise_for_status()

    deadline = time.monotonic() + timeout_seconds
    while True:
        state = client.get(f"{base}/api/v1/admin/maintenance", headers=headers)
        state.raise_for_status()
        active = int(state.json().get("active_sessions", 0) or 0)
        if active == 0 or time.monotonic() >= deadline:
            return active
        time.sleep(poll_seconds)


def drain_server(
    base_url: str,
    username: str,
    password: str,
    *,
    timeout_seconds: int = 300,
    poll_seconds: float = 3.0,
    client: httpx.Client | None = None,
) -> int:
    """请求服务进入排空并等到活跃会话归零，返回最后看到的活跃会话数（IM-PLN-004 §6.1）。

    为什么放在 Python 里：``scripts/graceful_restart.ps1`` 与 Linux 侧脚本各写一遍，
    「登录 → 触发 drain → 轮询 → 超时判定」这套流程必然漂移；正确性只该有一份实现，
    两边都是薄包装。返回非 0 表示还有人在开会，调用方必须中止发布。
    """
    if client is not None:
        return _drain_with(
            client, base_url, username, password, timeout_seconds, poll_seconds
        )
    with httpx.Client(timeout=30.0) as session:
        return _drain_with(
            session, base_url, username, password, timeout_seconds, poll_seconds
        )


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
    drain = subparsers.add_parser("drain")
    drain.add_argument("--base-url", default="http://127.0.0.1:8010")
    drain.add_argument("--username", default="admin")
    drain.add_argument("--password", required=True)
    drain.add_argument("--timeout-seconds", type=int, default=300)

    args = parser.parse_args()
    try:
        if args.command == "backup":
            print(backup_sqlite(args.source, args.target))
        elif args.command == "verify":
            verify_sqlite(args.path)
            print(f"verified={args.path}")
        elif args.command == "drain":
            active = drain_server(
                args.base_url,
                args.username,
                args.password,
                timeout_seconds=args.timeout_seconds,
            )
            if active:
                print(f"error: 仍有 {active} 个活跃会话，不要重启", file=sys.stderr)
                return 3
            print("drained=ok")
        else:
            safety = restore_sqlite(args.current, args.backup, args.backup_dir)
            if safety is not None:
                print(f"safety={safety}")
            print(f"restored={args.current}")
    except (OSError, SqliteBackupError, DrainError, httpx.HTTPError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
