"""数据库引擎与会话管理。"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import event, inspect, text
from sqlalchemy.exc import TimeoutError as SATimeoutError
from sqlalchemy.pool import QueuePool, StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from .models import AppConfigRow, User, utcnow
from .security import hash_password, hash_share_token
from .settings import BASE_DIR, SERVER_CONFIG, config_store

logger = logging.getLogger(__name__)

#: 出厂默认管理员。登录页的「预填」功能据此判断账号是否还是初始状态，
#: 所以这里是唯一事实来源，不要在别处再写一遍字面量。
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"


def admin_credentials_from_env() -> tuple[str, str]:
    """读取管理员初始凭据（可用环境变量覆盖）。

    用 ``or`` 而不是 ``os.getenv(name, default)``：后者遇到 ``ADMIN_PASSWORD=``
    这种「键存在但为空串」的情况会返回空串，等于把管理员口令设成空。
    """
    return (
        os.getenv("ADMIN_USERNAME") or DEFAULT_ADMIN_USERNAME,
        os.getenv("ADMIN_PASSWORD") or DEFAULT_ADMIN_PASSWORD,
    )


_db_url = SERVER_CONFIG["database_url"]
SERVER_CONFIG["data_dir"].mkdir(parents=True, exist_ok=True)


def _pool_env(name: str, default: str) -> str:
    """读池参数：环境变量存在但为空串时回退默认值（与 settings._env_or 同约定）。"""
    raw = os.getenv(name)
    return raw if raw else default


class _CountingQueuePool(QueuePool):
    """QueuePool + 取连接超时计数。

    为什么需要：``create_engine`` 未配置池参数时是 ``QueuePool(5 + 10)``，而并发方
    （anyio 线程池里的同步端点、``asyncio.to_thread`` 落库、job worker、清理线程）
    远超 15，满载时会抛 ``TimeoutError``。这个超时**不会**经过 ``handle_error``
    （那时连接还没拿到），只能在 ``connect()`` 里数，否则线上只会看到零星的 500。
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.checkout_timeouts = 0

    def connect(self):  # type: ignore[override]
        try:
            return super().connect()
        except SATimeoutError:
            self.checkout_timeouts += 1
            raise


_connect_args = {"check_same_thread": False} if _db_url.startswith("sqlite") else {}
_engine_kwargs: dict = {"echo": False, "connect_args": _connect_args}
if ":memory:" in _db_url:
    _engine_kwargs["poolclass"] = StaticPool
else:
    # 显式配置而不是留默认：默认 5+10 且 pool_timeout=30s，
    # 满载时表现为"请求卡 30 秒再 500"，比快速失败更难排查。
    _engine_kwargs.update(
        poolclass=_CountingQueuePool,
        pool_size=int(_pool_env("DB_POOL_SIZE", "5")),
        max_overflow=int(_pool_env("DB_POOL_MAX_OVERFLOW", "10")),
        pool_timeout=float(_pool_env("DB_POOL_TIMEOUT", "10")),
    )

engine = create_engine(_db_url, **_engine_kwargs)

#: 语句计数（M0 度量）：op -> 次数。只做观测，失败不影响主链路。
_statement_counts: dict[str, int] = defaultdict(int)


@event.listens_for(engine, "before_cursor_execute")
def _count_db_statement(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
    """按语句首词归类计数，供 ``/metrics`` 暴露（量化 C-1/C-2/C-4/S-7 的依据）。"""
    head = statement.lstrip()
    op = head.split(None, 1)[0].upper() if head else "UNKNOWN"
    _statement_counts[op] += 1


def db_statement_counts() -> dict[str, int]:
    """当前进程的 SQL 语句计数快照。"""
    return dict(_statement_counts)


def reset_db_statement_counts() -> None:
    """清空语句计数；供测试与压测分段使用。"""
    _statement_counts.clear()


def pool_metrics() -> tuple[dict[str, float], dict[str, int]]:
    """返回 (池水位 gauge, 取连接超时计数器)。"""
    pool = engine.pool
    gauges: dict[str, float] = {}
    counters: dict[str, int] = {}
    try:
        gauges["meeting_db_pool_checked_out"] = float(pool.checkedout())  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        gauges["meeting_db_pool_size"] = float(pool.size())  # type: ignore[attr-defined]
    except Exception:
        pass
    max_overflow = getattr(pool, "_max_overflow", None)
    if max_overflow is not None:
        # 不用 pool.overflow()：池未满时它返回负值，作为 gauge 会误导告警规则。
        gauges["meeting_db_pool_max_overflow"] = float(max_overflow)
    timeouts = getattr(pool, "checkout_timeouts", None)
    if timeouts is not None:
        counters["meeting_db_pool_timeouts_total"] = int(timeouts)
    return gauges, counters


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """生产 SQLite 连接参数。

    WAL 让读写不互相阻塞；busy_timeout 避免短时写锁直接返回
    ``database is locked``；外键约束保证删除会议时不会留下孤儿数据。
    """
    if not _db_url.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        if ":memory:" not in _db_url:
            cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


def init_db() -> None:
    """Create schema, apply migrations, load config and ensure admin."""
    SQLModel.metadata.create_all(engine)
    _migrate_schema()
    with engine.begin() as conn:
        try:
            from .audit import backfill_audit_hashes

            backfill_audit_hashes(conn)
        except Exception:
            logger.exception("审计哈希链回填失败")
    _run_versioned_migrations()
    logger.info("数据库已就绪: %s", _db_url)

    _load_config_overrides()
    _ensure_admin()


def _run_versioned_migrations() -> None:
    """在幂等兼容迁移之后登记/执行 Alembic 版本。"""
    if os.getenv("MEETING_SKIP_ALEMBIC") == "1":
        return
    try:
        try:
            from alembic import command
            from alembic.config import Config
        except ImportError:
            logger.warning(
                "未安装 Alembic，当前使用幂等兼容迁移；生产环境请安装 alembic"
            )
            return

        cfg = Config(str(BASE_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
        cfg.set_main_option("sqlalchemy.url", _db_url)
        command.upgrade(cfg, "head")
    except Exception:
        logger.exception("Alembic 版本迁移失败")
        raise

_BACKFILL_KEY = "legacy_backfill_2026_11_04"
_ONCE_TABLE = "compat_migration"


def _once_done(conn, key: str) -> bool:
    """判断某个一次性回填是否已执行过。

    表不存在时不创建（避免每次启动都产生 DDL），只在真正要记录时才建。
    """
    exists = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": _ONCE_TABLE},
    ).first()
    if exists is None:
        return False
    return conn.execute(
        text(f"SELECT 1 FROM {_ONCE_TABLE} WHERE key = :key"), {"key": key}
    ).first() is not None


def _mark_once(conn, key: str) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_ONCE_TABLE} "
            "(key VARCHAR(64) PRIMARY KEY, applied_at DATETIME)"
        )
    )
    conn.execute(
        text(f"INSERT OR IGNORE INTO {_ONCE_TABLE} (key, applied_at) VALUES (:key, :at)"),
        {"key": key, "at": utcnow()},
    )
def _migrate_schema() -> None:
    """补齐 SQLModel ``create_all`` 不会处理的增量列，并修复历史数据。

    SQLModel 的 ``create_all`` 只建新表，不会给已存在的表加列。这里做三件事：

    1. 给 ``segment`` 补 ``session_id`` / ``speaker_key``；
    2. 给 ``voiceprint`` 补 ``meeting_id`` / ``speaker_key``；
    3. 回填历史多会话会议的时间轴偏移，让重连后的片段按真实先后排序。

    全部操作幂等，可在每次启动时安全执行。

    **只对 SQLite 生效**：这里的 DDL 用到了 SQLite 专有语法（``CREATE TRIGGER
    ... RAISE(ABORT)``、``PRAGMA`` 相关假设、``DATETIME`` 亲和类型）。别的方言
    一律跳过，交给 Alembic 管理——否则按 ``DATABASE_URL=postgresql+psycopg://``
    切库时会在启动迁移阶段直接语法报错。
    """
    if engine.dialect.name != "sqlite":
        logger.info(
            "数据库方言为 %s：跳过 SQLite 专用兼容迁移，表结构以 Alembic 为准",
            engine.dialect.name,
        )
        return
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    def columns(table: str) -> set[str]:
        return {c["name"] for c in inspector.get_columns(table)}

    with engine.begin() as conn:
        # IM-PLN-004 C-4：重型历史回填只在库还没跑过这些 SQL 时执行一次。
        # 加列的 DDL 与安全相关迁移（分享令牌摘要）仍然每次都跑——它们幂等且便宜，
        # 而且必须能被"列被删掉"这类场景重新触发。
        run_backfill = not _once_done(conn, _BACKFILL_KEY)
        if not run_backfill:
            logger.debug("历史回填已执行过（%s），跳过全表扫描", _BACKFILL_KEY)
        if "voiceprint_template" in tables:
            if "disabled" not in columns("voiceprint_template"):
                conn.execute(text("ALTER TABLE voiceprint_template ADD COLUMN disabled BOOLEAN NOT NULL DEFAULT 0"))
            if "pinned" not in columns("voiceprint_template"):
                conn.execute(
                    text(
                        "ALTER TABLE voiceprint_template ADD COLUMN "
                        "pinned BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_voiceprint_template_pinned "
                        "ON voiceprint_template(pinned)"
                    )
                )
                if engine.dialect.name == "sqlite":
                    # 每人最多一条主模板；部分唯一索引只在 SQLite 建。
                    conn.execute(
                        text(
                            "CREATE UNIQUE INDEX IF NOT EXISTS "
                            "uq_voiceprint_template_pinned "
                            "ON voiceprint_template(voiceprint_id) WHERE pinned = 1"
                        )
                    )
            over = conn.execute(
                text("SELECT count(*) FROM voiceprint_template WHERE slot > 4")
            ).scalar()
            if over:
                # 宁可拒绝启动也不要带着越界数据跑：槽位上限是模板匹配的前提。
                # 给出可执行的自救路径，避免运维只看到一句「整理后重新启动」。
                raise RuntimeError(
                    f"发现 {over} 条旧声纹模板的 slot > 4（超出五槽范围），拒绝启动。"
                    "处理步骤：1) 用 deploy/backup_sqlite.sh 备份 data/meeting.db；"
                    "2) 在 backend/ 执行 python scripts/repair_template_slots.py "
                    "--db data/meeting.db（默认 dry-run；确认后加 --apply，按"
                    "主模板→已确认→质量重新分配 0..4 槽并退役多余模板）；"
                    "3) 复查 SELECT voiceprint_id, COUNT(*) FROM voiceprint_template "
                    "GROUP BY voiceprint_id HAVING COUNT(*) > 5; 4) 重新启动。"
                )
            for action, suffix in (("INSERT", "insert"), ("UPDATE OF slot", "update")):
                conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS template_slot_{suffix} BEFORE {action} ON voiceprint_template WHEN NEW.slot > 4 BEGIN SELECT RAISE(ABORT, 'template slot must be <= 4'); END"))
        if "user" in tables:
            user_cols = columns("user")
            if "auth_version" not in user_cols:
                conn.execute(text("ALTER TABLE user ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0"))
            if "must_change_password" not in user_cols:
                conn.execute(
                    text(
                        "ALTER TABLE user ADD COLUMN "
                        "must_change_password BOOLEAN DEFAULT 0"
                    )
                )
                # Existing administrators created by older versions must rotate
                # their bootstrap password before receiving business access.
                conn.execute(
                    text(
                        "UPDATE user SET must_change_password = 1 "
                        "WHERE role = 'admin'"
                    )
                )

        if "meeting" in tables:
            cols = columns("meeting")
            if "tags" not in cols:
                conn.execute(
                    text("ALTER TABLE meeting ADD COLUMN tags VARCHAR(500) DEFAULT ''")
                )
            if "archived" not in cols:
                conn.execute(
                    text("ALTER TABLE meeting ADD COLUMN archived BOOLEAN DEFAULT 0")
                )
            if "deleted_at" not in cols:
                conn.execute(
                    text("ALTER TABLE meeting ADD COLUMN deleted_at DATETIME")
                )
            if "description" not in cols:
                conn.execute(
                    text("ALTER TABLE meeting ADD COLUMN description VARCHAR(2000) DEFAULT ''")
                )
            if "participants" not in cols:
                conn.execute(
                    text("ALTER TABLE meeting ADD COLUMN participants VARCHAR(1000) DEFAULT ''")
                )
            if "agenda" not in cols:
                conn.execute(
                    text("ALTER TABLE meeting ADD COLUMN agenda VARCHAR(2000) DEFAULT ''")
                )
            if "meeting_type" not in cols:
                conn.execute(
                    text("ALTER TABLE meeting ADD COLUMN meeting_type VARCHAR(64) DEFAULT ''")
                )
            if "scheduled_at" not in cols:
                conn.execute(
                    text("ALTER TABLE meeting ADD COLUMN scheduled_at DATETIME")
                )
            if "metadata_captured" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE meeting ADD COLUMN metadata_captured "
                        "BOOLEAN DEFAULT 0"
                    )
                )
            if "retention_days" not in cols:
                conn.execute(
                    text("ALTER TABLE meeting ADD COLUMN retention_days INTEGER DEFAULT 0")
                )
            if "legal_hold" not in cols:
                conn.execute(
                    text("ALTER TABLE meeting ADD COLUMN legal_hold BOOLEAN DEFAULT 0")
                )
            if "redacted_at" not in cols:
                conn.execute(
                    text("ALTER TABLE meeting ADD COLUMN redacted_at DATETIME")
                )
            if "audio_chunk_seq" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE meeting ADD COLUMN audio_chunk_seq INTEGER DEFAULT 0"
                    )
                )
            if "adaptive_profile" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE meeting ADD COLUMN adaptive_profile "
                        "TEXT DEFAULT '{}'"
                    )
                )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_meeting_archived "
                    "ON meeting (archived)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_meeting_deleted_at "
                    "ON meeting (deleted_at)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_meeting_legal_hold "
                    "ON meeting (legal_hold)"
                )
            )

        if "segment" in tables:
            cols = columns("segment")
            if "session_id" not in cols:
                conn.execute(text("ALTER TABLE segment ADD COLUMN session_id VARCHAR(32)"))
            if "speaker_key" not in cols:
                conn.execute(text("ALTER TABLE segment ADD COLUMN speaker_key VARCHAR(32)"))
            if "original_text" not in cols:
                conn.execute(
                    text("ALTER TABLE segment ADD COLUMN original_text TEXT DEFAULT ''")
                )
            if "edited_at" not in cols:
                conn.execute(
                    text("ALTER TABLE segment ADD COLUMN edited_at DATETIME")
                )
            for name, ddl in (
                ("speaker_score", "FLOAT DEFAULT 0"),
                ("speaker_source", "VARCHAR(32) DEFAULT ''"),
                ("speaker_previous", "VARCHAR(32) DEFAULT ''"),
                ("asr_source", "VARCHAR(32) DEFAULT ''"),
                ("asr_error", "VARCHAR(1000)"),
                ("finalize_reason", "VARCHAR(32) DEFAULT ''"),
                ("speech_ms", "INTEGER DEFAULT 0"),
                ("audio_rms", "FLOAT DEFAULT 0"),
                ("noise_floor", "FLOAT DEFAULT 0"),
                ("snr_db", "FLOAT DEFAULT 0"),
                ("adaptive_gain", "FLOAT DEFAULT 1"),
                ("profile_version", "INTEGER DEFAULT 0"),
            ):
                if name not in cols:
                    conn.execute(
                        text(f"ALTER TABLE segment ADD COLUMN {name} {ddl}")
                    )
            if run_backfill:
                conn.execute(
                    text(
                        "UPDATE segment SET original_text = text "
                        "WHERE (original_text IS NULL OR original_text = '')"
                    )
                )
            # 旧数据：segment_id 形如 s-<session_id>-<seq>-<rand>
            if run_backfill:
                rows = conn.execute(
                    text(
                        "SELECT id, segment_id FROM segment "
                        "WHERE session_id IS NULL AND segment_id LIKE 's-%'"
                    )
                ).fetchall()
                for row in rows:
                    parts = str(row[1]).split("-")
                    if len(parts) >= 2:
                        conn.execute(
                            text("UPDATE segment SET session_id = :sid WHERE id = :id"),
                            {"sid": parts[1], "id": row[0]},
                        )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_segment_session_id "
                    "ON segment (session_id)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_segment_speaker_key "
                    "ON segment (speaker_key)"
                )
            )
            # 增量落库与收尾批量落库可能并发，历史上已产生过重复片段。
            # 先去重（同一 segment_id 保留最早一条），再加唯一约束兜底。
            # 去重是全表扫描 + 排序：只在第一次启动跑；新增重复由唯一索引直接拦住。
            if run_backfill:
                conn.execute(
                    text(
                        "DELETE FROM segment WHERE id NOT IN ("
                        "SELECT MIN(id) FROM segment GROUP BY meeting_id, segment_id)"
                    )
                )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_segment_meeting_sid "
                    "ON segment (meeting_id, segment_id)"
                )
            )
            # 逐会议重算段数：O(会议数 × 段数)，同样只在第一次启动跑。
            if run_backfill:
                conn.execute(
                    text(
                        "UPDATE meeting SET segment_count = "
                        "(SELECT COUNT(*) FROM segment WHERE segment.meeting_id = meeting.id)"
                    )
                )
            # FTS5：trigram tokenizer 支持中文子串检索；失败时搜索自动回退 LIKE
            if engine.dialect.name == "sqlite":
                try:
                    conn.execute(
                        text(
                            "CREATE VIRTUAL TABLE IF NOT EXISTS segment_fts "
                            "USING fts5(text, meeting_id UNINDEXED, "
                            "segment_id UNINDEXED, tokenize='trigram')"
                        )
                    )
                    conn.execute(
                        text(
                            "CREATE TRIGGER IF NOT EXISTS segment_fts_ai "
                            "AFTER INSERT ON segment BEGIN "
                            "INSERT INTO segment_fts(rowid,text,meeting_id,segment_id) "
                            "VALUES (new.id,new.text,new.meeting_id,new.segment_id); END"
                        )
                    )
                    conn.execute(
                        text(
                            "CREATE TRIGGER IF NOT EXISTS segment_fts_ad "
                            "AFTER DELETE ON segment BEGIN "
                            "DELETE FROM segment_fts WHERE rowid = old.id; END"
                        )
                    )
                    conn.execute(
                        text(
                            "CREATE TRIGGER IF NOT EXISTS segment_fts_au "
                            "AFTER UPDATE OF text ON segment BEGIN "
                            "UPDATE segment_fts SET text = new.text "
                            "WHERE rowid = new.id; END"
                        )
                    )
                    conn.execute(
                        text(
                            "INSERT INTO segment_fts(rowid,text,meeting_id,segment_id) "
                            "SELECT id,text,meeting_id,segment_id FROM segment "
                            "WHERE id NOT IN (SELECT rowid FROM segment_fts)"
                        )
                    )
                except Exception:
                    logger.exception("FTS5 初始化失败，全文搜索将回退 LIKE")

        if "voiceprint" in tables:
            cols = columns("voiceprint")
            if "meeting_id" not in cols:
                conn.execute(text("ALTER TABLE voiceprint ADD COLUMN meeting_id INTEGER"))
            if "speaker_key" not in cols:
                conn.execute(
                    text("ALTER TABLE voiceprint ADD COLUMN speaker_key VARCHAR(32)")
                )
            if "disabled" not in cols:
                conn.execute(
                    text("ALTER TABLE voiceprint ADD COLUMN disabled BOOLEAN DEFAULT 0")
                )
            if "quality_score" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE voiceprint ADD COLUMN quality_score "
                        "FLOAT DEFAULT 0"
                    )
                )
            if "sample_count" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE voiceprint ADD COLUMN sample_count "
                        "INTEGER DEFAULT 0"
                    )
                )
            if "last_seen_at" not in cols:
                conn.execute(
                    text("ALTER TABLE voiceprint ADD COLUMN last_seen_at DATETIME")
                )
            if "person_id" not in cols:
                # 兼容未启用以 Alembic 的部署：补列 + 索引，服务层维护一致性。
                conn.execute(
                    text("ALTER TABLE voiceprint ADD COLUMN person_id INTEGER")
                )
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_voiceprint_person_id "
                        "ON voiceprint(person_id)"
                    )
                )
            # 历史临时声纹按“首次发现会议”归入会议作用域，避免跨会议串味；
            # 人工命名过的（provisional=0）保持全局身份。
            if run_backfill:
                conn.execute(
                    text(
                        "UPDATE voiceprint SET meeting_id = source_meeting_id "
                        "WHERE meeting_id IS NULL AND provisional = 1 "
                        "AND source_meeting_id IS NOT NULL"
                    )
                )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_voiceprint_meeting_id "
                    "ON voiceprint (meeting_id)"
                )
            )
            # 修复历史身份分裂：某些片段用了 vp_<id>，而同一会议声纹行
            # 已经有稳定的 spk_* 键，两者应归一。逐行 UPDATE 是 N+1 写，只跑一次。
            if run_backfill:
                for vid, speaker_key in conn.execute(
                    text(
                        "SELECT id, speaker_key FROM voiceprint "
                        "WHERE speaker_key IS NOT NULL"
                    )
                ).fetchall():
                    conn.execute(
                        text(
                            "UPDATE segment SET speaker_key = :sk "
                            "WHERE speaker_key = :vp"
                        ),
                        {"sk": speaker_key, "vp": f"vp_{vid}"},
                    )

        if "minutes" in tables:
            cols = columns("minutes")
            if "version" not in cols:
                conn.execute(
                    text("ALTER TABLE minutes ADD COLUMN version INTEGER DEFAULT 1")
                )
            if "is_draft" not in cols:
                conn.execute(
                    text("ALTER TABLE minutes ADD COLUMN is_draft BOOLEAN DEFAULT 0")
                )
            if "transcript_end_ms" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE minutes ADD COLUMN transcript_end_ms INTEGER DEFAULT 0"
                    )
                )
            if "edited" not in cols:
                conn.execute(
                    text("ALTER TABLE minutes ADD COLUMN edited BOOLEAN DEFAULT 0")
                )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_minutes_version "
                    "ON minutes (meeting_id, version)"
                )
            )

        if "action_item" in tables:
            cols = columns("action_item")
            if "evidence" not in cols:
                conn.execute(
                    text("ALTER TABLE action_item ADD COLUMN evidence TEXT DEFAULT '[]'")
                )

        if "llm_usage" in tables:
            cols = columns("llm_usage")
            if "prompt_tokens" not in cols:
                conn.execute(
                    text("ALTER TABLE llm_usage ADD COLUMN prompt_tokens INTEGER DEFAULT 0")
                )
            if "completion_tokens" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE llm_usage ADD COLUMN completion_tokens "
                        "INTEGER DEFAULT 0"
                    )
                )
            if "cost_usd" not in cols:
                conn.execute(
                    text("ALTER TABLE llm_usage ADD COLUMN cost_usd FLOAT DEFAULT 0")
                )

        if "share_link" in tables:
            cols = columns("share_link")
            if "access_count" not in cols:
                conn.execute(
                    text("ALTER TABLE share_link ADD COLUMN access_count INTEGER DEFAULT 0")
                )
            if "last_accessed_at" not in cols:
                conn.execute(
                    text("ALTER TABLE share_link ADD COLUMN last_accessed_at DATETIME")
                )
            if "token_hint" not in cols:
                conn.execute(
                    text("ALTER TABLE share_link ADD COLUMN token_hint VARCHAR(16) DEFAULT ''")
                )
            # Older releases stored bearer tokens verbatim. Hash them in place so
            # existing links keep working while a database dump alone is useless.
            legacy_rows = conn.execute(
                text(
                    "SELECT id, token FROM share_link "
                    "WHERE token_hint IS NULL OR token_hint = ''"
                )
            ).fetchall()
            for row in legacy_rows:
                plaintext = str(row[1])
                conn.execute(
                    text(
                        "UPDATE share_link SET token = :digest, token_hint = :hint "
                        "WHERE id = :id"
                    ),
                    {
                        "digest": hash_share_token(plaintext),
                        "hint": plaintext[:8],
                        "id": row[0],
                    },
                )

        if "job" in tables:
            cols = columns("job")
            if "owner_id" not in cols:
                conn.execute(
                    text("ALTER TABLE job ADD COLUMN owner_id INTEGER")
                )

        if "audit_log" in tables:
            cols = columns("audit_log")
            if "prev_hash" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE audit_log ADD COLUMN prev_hash VARCHAR(64) DEFAULT ''"
                    )
                )
            if "record_hash" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE audit_log ADD COLUMN record_hash VARCHAR(64) DEFAULT ''"
                    )
                )

    if run_backfill:
        # 注意：_backfill_session_offsets 自己开事务，必须在外层写事务提交之后执行，
        # 否则 SQLite 上会出现 database is locked（外层已持 RESERVED 锁）。
        _backfill_session_offsets()
        with engine.begin() as conn:
            _mark_once(conn, _BACKFILL_KEY)


def _backfill_session_offsets() -> None:
    """把历史多会话会议的片段时间轴改成全局连续。

    旧实现每段会话的 ``start_ms`` 都从 0 开始，而接口按 ``seq`` 排序，
    导致会议 203 出现 107 处时间倒序。这里按会话落库先后排序，给后面的
    会话累加偏移；已经是连续时间轴的会议不会重复修改（幂等）。
    """
    with engine.begin() as conn:
        meetings = conn.execute(
            text(
                "SELECT meeting_id FROM segment WHERE session_id IS NOT NULL "
                "GROUP BY meeting_id HAVING COUNT(DISTINCT session_id) > 1 "
                # 已经有 meeting_session 记录的是新版本会话，时间轴在写入时
                # 就已按全局偏移生成，不能再平移（否则重连交叠会被二次移位）。
                "AND meeting_id NOT IN (SELECT meeting_id FROM meeting_session)"
            )
        ).fetchall()
        for (meeting_id,) in meetings:
            sessions = conn.execute(
                text(
                    "SELECT session_id, MIN(start_ms) AS s, MAX(end_ms) AS e, "
                    "MIN(created_at) AS first_at FROM segment "
                    "WHERE meeting_id = :mid AND session_id IS NOT NULL "
                    "GROUP BY session_id ORDER BY first_at, session_id"
                ),
                {"mid": meeting_id},
            ).fetchall()
            prev_end = None
            for session_id, span_start, span_end, _first_at in sessions:
                if prev_end is not None and span_start < prev_end:
                    shift = prev_end - span_start
                    conn.execute(
                        text(
                            "UPDATE segment SET start_ms = start_ms + :shift, "
                            "end_ms = end_ms + :shift "
                            "WHERE meeting_id = :mid AND session_id = :sid"
                        ),
                        {"shift": shift, "mid": meeting_id, "sid": session_id},
                    )
                    span_start += shift
                    span_end += shift
                prev_end = span_end
            conn.execute(
                text(
                    "UPDATE meeting SET duration_ms = :duration "
                    "WHERE id = :mid AND duration_ms < :duration"
                ),
                {"duration": prev_end or 0, "mid": meeting_id},
            )


def _load_config_overrides() -> None:
    with Session(engine) as session:
        rows = session.exec(select(AppConfigRow)).all()
        config_store.load_overrides({row.key: row.value for row in rows})


def _ensure_admin() -> None:
    """首次启动时创建默认管理员。

    默认口令仅用于本地初始化,首次登录后应立即修改。
    """
    with Session(engine) as session:
        existing = session.exec(select(User).where(User.role == "admin")).first()
        if existing is not None:
            return
        username, password = admin_credentials_from_env()
        admin = User(
            username=username,
            display_name="管理员",
            password_hash=hash_password(password),
            role="admin",
            must_change_password=True,
        )
        session.add(admin)
        session.commit()
        logger.warning(
            "已创建管理员 %s；首次登录必须修改初始化密码",
            username,
        )


def get_session() -> Generator[Session, None, None]:
    """FastAPI 依赖注入用。"""
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """脚本与非请求上下文用。"""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def persist_config(values: dict, *, user_id: int | None = None) -> None:
    """把配置写入数据库并热更新内存缓存。"""
    with session_scope() as session:
        for key, value in values.items():
            import json

            row = session.get(AppConfigRow, key)
            payload = json.dumps(value, ensure_ascii=False)
            if row is None:
                row = AppConfigRow(key=key, value=payload, updated_by=user_id, updated_at=utcnow())
            else:
                row.value = payload
                row.updated_by = user_id
                row.updated_at = utcnow()
            session.add(row)

    config_store.set_overrides(values)
    logger.info("配置已更新: %s", ", ".join(values))
