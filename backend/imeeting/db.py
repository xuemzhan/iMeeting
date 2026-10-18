"""数据库引擎与会话管理。"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import event, inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from .models import AppConfigRow, GlossaryTerm, MinutesTemplate, User, utcnow
from .security import hash_password
from .settings import SERVER_CONFIG, config_store

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

_connect_args = {"check_same_thread": False} if _db_url.startswith("sqlite") else {}
_engine_kwargs: dict = {"echo": False, "connect_args": _connect_args}
if ":memory:" in _db_url:
    _engine_kwargs["poolclass"] = StaticPool

engine = create_engine(_db_url, **_engine_kwargs)


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
    """建表、加载配置覆盖、确保管理员存在。"""
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
    _ensure_minutes_templates()
    _ensure_glossary_terms()
    try:
        from .meeting_cleanup import purge_expired_meetings, purge_retention_expired

        purge_expired_meetings()
        purge_retention_expired()
    except Exception:
        logger.exception("回收站过期清理失败")


def _run_versioned_migrations() -> None:
    """在幂等兼容迁移之后登记/执行 Alembic 版本。"""
    if os.getenv("MEETING_SKIP_ALEMBIC") == "1":
        return
    try:
        from pathlib import Path

        try:
            from alembic import command
            from alembic.config import Config
        except ImportError:
            logger.warning(
                "未安装 Alembic，当前使用幂等兼容迁移；生产环境请安装 alembic"
            )
            return

        root = Path(__file__).resolve().parents[1]
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "migrations"))
        cfg.set_main_option("sqlalchemy.url", _db_url)
        command.upgrade(cfg, "head")
    except Exception:
        logger.exception("Alembic 版本迁移失败")
        raise


BUILTIN_MINUTES_TEMPLATES = [
    (
        "标准会议纪要",
        "通用会议：议题、结论、待办",
        "你是一位专业的会议记录员。请根据以下会议转写内容，生成结构化的会议纪要。\n\n"
        "要求：\n1. 提取讨论的议题，每个议题给出简要说明\n"
        "2. 提炼每个议题的结论或共识\n"
        "3. 列出明确的待办事项，注明责任人与截止时间（如原文提及）\n"
        "4. 保持客观，不要臆造原文没有的信息\n\n"
        "输出 JSON，结构如下：\n"
        '{"summary":"整体摘要","topics":[{"title":"议题","detail":"说明","conclusion":"结论"}],'
        '"todos":[{"task":"待办","owner":"责任人","due":"截止时间"}]}\n\n'
        "会议转写内容：\n---\n{transcript}\n---",
    ),
    (
        "项目周会",
        "进展、风险、下周计划与责任人",
        "你是项目经理。请基于以下转写生成项目周会纪要。\n"
        "重点关注：本周进展、阻塞与风险、下周计划、责任人、截止时间。\n"
        "不要把未提及的内容补全。\n\n"
        "输出 JSON：\n"
        '{"summary":"本周整体进展","topics":[{"title":"进展/风险/计划","detail":"说明","conclusion":"结论"}],'
        '"todos":[{"task":"待办","owner":"责任人","due":"截止时间"}]}\n\n'
        "会议转写：\n---\n{transcript}\n---",
    ),
    (
        "决策评审",
        "背景、方案对比、决策与行动项",
        "你是技术评审主持人。请基于以下转写生成决策评审纪要。\n"
        "重点：问题背景、候选方案与权衡、最终决策、遗留风险、行动项。\n"
        "若会议未形成决策，请明确写“未决策”，不要臆造。\n\n"
        "输出 JSON：\n"
        '{"summary":"决策摘要","topics":[{"title":"议题","detail":"方案与权衡","conclusion":"决策/未决策"}],'
        '"todos":[{"task":"行动项","owner":"责任人","due":"截止时间"}]}\n\n'
        "会议转写：\n---\n{transcript}\n---",
    ),
    (
        "客户访谈",
        "痛点、需求、异议与后续动作",
        "你是客户成功经理。请基于以下转写生成客户访谈纪要。\n"
        "重点：客户痛点、明确需求、预算/时间约束、异议与顾虑、后续跟进动作。\n\n"
        "输出 JSON：\n"
        '{"summary":"访谈摘要","topics":[{"title":"痛点/需求/异议","detail":"客户原话要点","conclusion":"判断"}],'
        '"todos":[{"task":"跟进动作","owner":"责任人","due":"截止时间"}]}\n\n'
        "访谈转写：\n---\n{transcript}\n---",
    ),
]


BUILTIN_GLOSSARY_TERMS = [
    ("拓扑结构", "拓部结构,拓扑", "网络安全"),
    ("震网病毒", "阵亡病毒,Stuxnet", "网络安全"),
    ("零日漏洞", "灵力漏洞,0day,0-day", "网络安全"),
    ("载荷", "载合", "网络安全"),
    ("情报载荷", "情报载合", "网络安全"),
    ("跳板", "条板", "网络安全"),
    ("木马", "优步码,木马程序", "网络安全"),
    ("APT", "A P T,高级持续性威胁", "网络安全"),
    ("PDR", "防护检测响应", "网络安全"),
    ("DAPV", "动态自适应保护", "网络安全"),
    ("网络空间安全", "网络空安全,网控安全", "网络安全"),
    ("攻击", "空击,公击", "网络安全"),
    ("供应链", "供应莲,供应链件", "网络安全"),
    ("态势感知", "态势赶知", "网络安全"),
    ("内网渗透", "内网参透", "网络安全"),
    ("横向移动", "横向移种", "网络安全"),
    # #231 真实会议暴露的高频领域词与误识别写法。只收录明确的错误别名，
    # 不把“多态/拟态/靶场”这类跨领域通用词做全局替换。
    ("多模态", "播模态,膜态,多摸态", "人工智能"),
    ("数字孪生", "数字本身,数字双生", "网络安全"),
    ("动态防御", "随机跳变,随机跳变防御,动态防御技术", "网络安全"),
    ("拟态防御", "拟态防御技术", "网络安全"),
    ("量子密码", "量子DNA密码,量子密钥", "网络安全"),
    ("行为认证", "行为风格认证,鼠标移动认证,行为识别认证", "网络安全"),
    ("增强现实", "增强现实技术", "人工智能"),
    ("虚拟现实", "虚拟现实技术", "人工智能"),
    ("超级智能", "ASI,超级人工智能,超级值", "人工智能"),
    ("数字靶场", "网络靶场,数字靶场", "网络安全"),
    ("防护对象", "团论对象", "网络安全"),
    ("基础理论", "基术理论", "网络安全"),
    ("安全投入", "杀菌投入", "网络安全"),
    ("组织机构", "主织事库", "网络安全"),
    ("法案", "会打牌呀法", "网络安全"),
    ("智能汽车", "智能汽", "人工智能"),
    ("关键信息基础设施", "关基", "网络安全"),
    ("零信任", "零信任架构", "网络安全"),
    ("数据治理", "数据治理体系", "人工智能"),
    ("情报共享", "情报共享机制", "网络安全"),
    ("诱导隔离", "诱导隔离技术", "网络安全"),
    ("网络空间地图", "网络地图", "网络安全"),
    ("新型网络体系", "五新的网网", "网络安全"),
    ("风险", "重险", "通用"),
    ("成本", "成务", "通用"),
    # 数据安全 / 个人信息保护会议常见规范词。只收录明确的同音/近音别名；
    # “数据确认”本身是正常词，不在全局替换，交给知识上下文结合语义判断。
    ("数据确权", "数据确认机制,数据确认与数据资产机制", "数据安全"),
    ("个人信息保护", "", "数据安全"),
    ("个人信息保护法", "个保法", "数据安全"),
    ("数据安全法", "数安法", "数据安全"),
    ("网络安全法", "网安法", "数据安全"),
    ("敏感个人信息", "敏感信息,敏感个人数据", "数据安全"),
    ("告知同意", "知情同意", "数据安全"),
    ("数据出境", "数据跨境,数据出境安全评估", "数据安全"),
    ("个人信息过度收集", "过度收集个人信息,个人信息过度采集", "数据安全"),
    ("第三方披露", "第三方共享", "数据安全"),
    ("数据泄露", "数据外泄", "数据安全"),
    ("数据资产", "", "数据安全"),
    ("滴滴", "滴滴出行,滴滴打车", "数据安全"),
    ("哈梅内伊", "韩没内衣", "国际政治"),
    ("美以联合行动", "美国与以色列联合行动,美以联合对伊朗行动", "国际政治"),
    ("Claude 3 Opus", "clall三opx,Claude3Opus", "人工智能"),
    ("GPT-6.5", "GP六点五,GPT6.5,GP六", "人工智能"),
    ("NIST", "", "网络安全"),
    ("护网行动", "护网", "网络安全"),
    ("供应链攻击", "供应链弱化", "网络安全"),
    ("认知战", "认知作战,舆论战", "网络安全"),
    ("网络攻击工业化", "AI驱动工业化,攻击工业化", "网络安全"),
    ("智能体", "AI Agent,智能代理", "人工智能"),
    ("攻击方", "正房行", "网络安全"),
    ("攻击性", "竞攻性", "网络安全"),
    ("算力设备", "科理设备", "人工智能"),
    ("访问权限", "访款权限", "网络安全"),
    ("KYC", "KYZ", "数据安全"),
    ("斩首行动", "展斗行动", "国际政治"),
]


def _ensure_minutes_templates() -> None:
    """首次启动写入内置纪要模板（幂等）。"""
    with Session(engine) as db:
        existing = {row.name for row in db.exec(select(MinutesTemplate)).all()}
        for name, description, content in BUILTIN_MINUTES_TEMPLATES:
            if name in existing:
                continue
            db.add(
                MinutesTemplate(
                    name=name,
                    description=description,
                    content=content,
                    is_builtin=True,
                )
            )
        db.commit()


def _ensure_glossary_terms() -> None:
    """首次启动写入内置网络安全词表（幂等）。"""
    with Session(engine) as db:
        existing = {
            row.term: row for row in db.exec(select(GlossaryTerm)).all()
        }
        for term, aliases, category in BUILTIN_GLOSSARY_TERMS:
            row = existing.get(term)
            if row is not None:
                # 内置词条允许随版本补充别名；用户自建词条不覆盖。
                if row.owner_id is not None:
                    continue
                current = [
                    item.strip()
                    for item in (row.aliases or "").replace("，", ",").split(",")
                    if item.strip()
                ]
                for alias in aliases.split(","):
                    alias = alias.strip()
                    if alias and alias not in current:
                        current.append(alias)
                merged = ",".join(current)
                if merged != (row.aliases or ""):
                    row.aliases = merged[:1000]
                    db.add(row)
                if not row.category:
                    row.category = category
                continue
            db.add(
                GlossaryTerm(
                    term=term,
                    aliases=aliases,
                    category=category,
                )
            )
        db.commit()
    try:
        from .glossary import invalidate_cache

        invalidate_cache()
    except Exception:
        logger.debug("词表缓存刷新失败", exc_info=True)


def _migrate_schema() -> None:
    """补齐 SQLModel ``create_all`` 不会处理的增量列，并修复历史数据。

    SQLModel 的 ``create_all`` 只建新表，不会给已存在的表加列。这里做三件事：

    1. 给 ``segment`` 补 ``session_id`` / ``speaker_key``；
    2. 给 ``voiceprint`` 补 ``meeting_id`` / ``speaker_key``；
    3. 回填历史多会话会议的时间轴偏移，让重连后的片段按真实先后排序。

    全部操作幂等，可在每次启动时安全执行。
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    def columns(table: str) -> set[str]:
        return {c["name"] for c in inspector.get_columns(table)}

    with engine.begin() as conn:
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
            conn.execute(
                text(
                    "UPDATE segment SET original_text = text "
                    "WHERE (original_text IS NULL OR original_text = '')"
                )
            )
            # 旧数据：segment_id 形如 s-<session_id>-<seq>-<rand>
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
            # 历史临时声纹按“首次发现会议”归入会议作用域，避免跨会议串味；
            # 人工命名过的（provisional=0）保持全局身份。
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
            # 已经有稳定的 spk_* 键，两者应归一。
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

    _backfill_session_offsets()


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
        )
        session.add(admin)
        session.commit()
        logger.warning(
            "已创建默认管理员 %s / %s —— 请立即修改密码",
            username,
            password,
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
