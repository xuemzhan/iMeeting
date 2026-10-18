"""数据模型定义（SQLModel）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    """用户账号。"""

    __tablename__ = "user"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True, max_length=64)
    display_name: str = Field(default="", max_length=64)
    password_hash: str = Field(max_length=255)
    role: str = Field(default="user", max_length=16)  # admin | user
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)


class Meeting(SQLModel, table=True):
    """一次会议会话。"""

    __tablename__ = "meeting"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(default="未命名会议", max_length=200)
    owner_id: int = Field(foreign_key="user.id", index=True)
    status: str = Field(default="recording", max_length=16)
    # recording（进行中）| finished（正常结束）| abandoned（异常中断/半途丢弃）
    # | failed（转写过程出错）
    duration_ms: int = Field(default=0)
    audio_path: Optional[str] = Field(default=None, max_length=500)
    segment_count: int = Field(default=0)
    #: 逗号分隔的标签（企业归档/检索用）
    tags: str = Field(default="", max_length=500)
    #: 归档后默认不出现在历史列表，可通过筛选查看
    archived: bool = Field(default=False, index=True)
    #: 软删除时间；30 天后由启动清理任务物理删除
    deleted_at: Optional[datetime] = Field(default=None, index=True)
    #: 会前/会中补充的元信息（口语录入或 .ics 导入）
    description: str = Field(default="", max_length=2000)
    participants: str = Field(default="", max_length=1000)
    agenda: str = Field(default="", max_length=2000)
    meeting_type: str = Field(default="", max_length=64)
    scheduled_at: Optional[datetime] = Field(default=None, index=True)
    #: 是否已经通过口语/手动完成元信息采集（自动提取不再覆盖）
    metadata_captured: bool = Field(default=False)
    #: 合规保留：0 表示沿用全局策略；>0 表示会议结束后保留天数。
    retention_days: int = Field(default=0)
    #: 法律保留开启时，自动清理任务不得删除该会议。
    legal_hold: bool = Field(default=False, index=True)
    redacted_at: Optional[datetime] = Field(default=None)
    #: 客户端音频块序号，用于断线补传去重。
    audio_chunk_seq: int = Field(default=0)
    #: 当前会议的自适应音频/VAD/切分配置快照（JSON）
    adaptive_profile: str = Field(default="{}")
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = Field(default=None)


class Segment(SQLModel, table=True):
    """转写片段。写入最频繁的表。

    ``segment_id`` 是会话内唯一标识，partial 与 final 共用同一个值，
    前端据此覆盖而非追加。数据库唯一约束防止重复落库。
    """

    __tablename__ = "segment"

    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    segment_id: str = Field(max_length=64, index=True)
    #: 产生该片段的 WS 会话。重连后同一会议会有多个会话，
    #: 排序、音频归属和问题排查都依赖它。
    session_id: Optional[str] = Field(default=None, max_length=32, index=True)
    text: str = Field(default="")
    #: 首次落库时的原始文本，供“原文/当前”对比
    original_text: str = Field(default="")
    is_final: bool = Field(default=False, index=True)
    start_ms: int = Field(default=0)
    end_ms: int = Field(default=0)
    speaker: Optional[str] = Field(default=None, max_length=32)
    #: 会议内稳定的说话人键。改名只改 label，键保持不变，
    #: 因而历史片段、后续定稿和重连会话都能对上同一个人。
    speaker_key: Optional[str] = Field(default=None, max_length=32, index=True)
    speaker_score: float = Field(default=0.0)
    speaker_source: str = Field(default="", max_length=32)
    #: 逐段诊断：offline / streaming_fallback / echo
    asr_source: str = Field(default="", max_length=32)
    asr_error: Optional[str] = Field(default=None, max_length=1000)
    finalize_reason: str = Field(default="", max_length=32)
    speech_ms: int = Field(default=0)
    audio_rms: float = Field(default=0.0)
    noise_floor: float = Field(default=0.0)
    snr_db: float = Field(default=0.0)
    adaptive_gain: float = Field(default=1.0)
    profile_version: int = Field(default=0)
    edited_at: Optional[datetime] = Field(default=None)
    seq: int = Field(default=0, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class MeetingSession(SQLModel, table=True):
    """一次 WebSocket 连接（会议的一段）。

    浏览器刷新、网络抖动都会让同一场会议产生多个会话。旧实现把每次会话
    当成全新的 `StreamSession`：序号从 0 重来、声纹聚类清零、音频写同一
    个文件互相覆盖。这张表把每段会话的墙钟时间、时长、时间轴偏移、音频
    和状态持久化下来，重连才能接着上一段继续。
    """

    __tablename__ = "meeting_session"

    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    session_id: str = Field(max_length=32, unique=True, index=True)
    #: 本段开始的墙钟时间
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: Optional[datetime] = Field(default=None)
    #: 本段在会议全局时间轴上的起点（前面所有会话时长之和）
    start_offset_ms: int = Field(default=0)
    duration_ms: int = Field(default=0)
    segment_count: int = Field(default=0)
    audio_path: Optional[str] = Field(default=None, max_length=500)
    status: str = Field(default="recording", max_length=16)


class Minutes(SQLModel, table=True):
    """LLM 生成的会议纪要。"""

    __tablename__ = "minutes"

    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    summary: str = Field(default="")
    topics: str = Field(default="[]")  # JSON 字符串
    todos: str = Field(default="[]")  # JSON 字符串
    raw_markdown: str = Field(default="")
    model_used: str = Field(default="", max_length=128)
    status: str = Field(default="ready", max_length=16)  # ready | failed
    error: Optional[str] = Field(default=None)
    #: 版本号：会议进行中每次自动草稿 +1，终稿再 +1；历史版本保留可对比
    version: int = Field(default=1, index=True)
    #: 是否为会中自动生成的阶段草稿
    is_draft: bool = Field(default=False, index=True)
    #: 本版覆盖到的转写时间轴终点（毫秒）；滚动摘要据此只取新增内容
    transcript_end_ms: int = Field(default=0)
    #: 人工编辑产生的版本
    edited: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)


class ShareLink(SQLModel, table=True):
    """会议只读分享链接（可设置过期时间与访问口令）。"""

    __tablename__ = "share_link"

    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(unique=True, index=True, max_length=64)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    password_hash: Optional[str] = Field(default=None, max_length=255)
    expires_at: datetime = Field(index=True)
    allow_download: bool = Field(default=False)
    access_count: int = Field(default=0)
    last_accessed_at: Optional[datetime] = Field(default=None)
    created_by: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)


class ActionItem(SQLModel, table=True):
    """会议待办闭环（不用外部 IM，应用内跟踪状态与截止时间）。"""

    __tablename__ = "action_item"

    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    task: str = Field(default="", max_length=500)
    owner: str = Field(default="", max_length=64)
    due: str = Field(default="", max_length=64)
    status: str = Field(default="open", max_length=16, index=True)  # open|doing|done
    source: str = Field(default="auto", max_length=16)  # auto|manual
    source_version: int = Field(default=0)
    #: 证据链片段（JSON 数组，含 segment_id / start_ms / text）。
    evidence: str = Field(default="[]")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class LLMUsage(SQLModel, table=True):
    """LLM 调用计量（成本看板用）。"""

    __tablename__ = "llm_usage"

    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: Optional[int] = Field(default=None, index=True)
    purpose: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=128)
    prompt_chars: int = Field(default=0)
    response_chars: int = Field(default=0)
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    cost_usd: float = Field(default=0.0)
    elapsed_ms: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow, index=True)


class MinutesTemplate(SQLModel, table=True):
    """会议纪要模板：可导入、可复用，生成纪要时作为提示词/格式约束。"""

    __tablename__ = "minutes_template"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=64)
    description: str = Field(default="", max_length=200)
    content: str = Field(default="")
    is_builtin: bool = Field(default=False, index=True)
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Voiceprint(SQLModel, table=True):
    """已录入的声纹。

    存 CAM++ 提取的 192 维说话人向量（float32 小端字节），识别时与实时
    片段的向量做余弦相似度比对，命中则直接给出说话人姓名。

    为什么必须自己存：FunASR 的 `spk` 字段是**单次 generate() 内部的聚类
    下标**，每次调用都从 0 重新编号。而本项目是「一句话调用一次 generate」，
    于是每一句都被独立聚成第 0 类——所有说话人永远都是 0。要跨句区分
    说话人，只能在会话层面自己聚类/比对。
    """

    __tablename__ = "voiceprint"

    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    name: str = Field(max_length=64, index=True)
    # 192 维 float32 向量序列化后的字节
    embedding: bytes = Field(default=b"")
    # 录入时使用的音频时长（毫秒），仅用于展示
    sample_ms: int = Field(default=0)
    sample_count: int = Field(default=0)
    # 是否为「自动发现」的临时声纹：开会时聚出来但还没人工命名的人。
    # 这类条目同样参与识别匹配，好处是同一个陌生人在不同会议里能拿到
    # 一致的标签；界面会标注「待命名」，允许改名（改名即转正）或删除。
    provisional: bool = Field(default=False, index=True)
    # 首次发现该说话人的会议，便于用户回想「这是谁」
    source_meeting_id: Optional[int] = Field(default=None, index=True)
    #: 会议作用域：自动发现的临时声纹只在本场会议内参与匹配，
    #: 避免不同会议的「说话人 1」互相串味。人工命名后 provisional=False，
    #: 该行转为全局身份，跨会议生效。
    meeting_id: Optional[int] = Field(default=None, index=True)
    #: 与本场 segment.speaker_key 对应的稳定键
    speaker_key: Optional[str] = Field(default=None, max_length=32, index=True)
    #: 停用后不参与后续会议的声纹匹配，但保留历史身份。
    disabled: bool = Field(default=False, index=True)
    #: 0~1，当前由样本时长和人工确认状态推导。
    quality_score: float = Field(default=0.0)
    last_seen_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Job(SQLModel, table=True):
    """持久化后台任务。

    自动终稿、会后合并、导出、音频复核等任务必须跨进程重启保留，
    不能只依赖 ``asyncio.create_task``。
    """

    __tablename__ = "job"

    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: Optional[int] = Field(default=None, index=True)
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    kind: str = Field(max_length=64, index=True)
    payload: str = Field(default="{}")
    status: str = Field(default="pending", index=True)
    priority: int = Field(default=100, index=True)
    attempts: int = Field(default=0)
    max_attempts: int = Field(default=3)
    run_after: datetime = Field(default_factory=utcnow, index=True)
    locked_at: Optional[datetime] = Field(default=None)
    dedupe_key: Optional[str] = Field(default=None, max_length=160, index=True)
    last_error: Optional[str] = Field(default=None)
    result: str = Field(default="{}")
    result_path: Optional[str] = Field(default=None, max_length=500)
    download_name: Optional[str] = Field(default=None, max_length=200)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class GlossaryTerm(SQLModel, table=True):
    """企业词表：规范词、别名与 ASR 热词。"""

    __tablename__ = "glossary_term"

    id: Optional[int] = Field(default=None, primary_key=True)
    term: str = Field(max_length=128, index=True)
    aliases: str = Field(default="", max_length=1000)
    category: str = Field(default="", max_length=64)
    enabled: bool = Field(default=True, index=True)
    owner_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TranscriptReview(SQLModel, table=True):
    """会后全音频复核产生的修订建议，默认不直接覆盖原转写。"""

    __tablename__ = "transcript_review"

    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    segment_id: str = Field(max_length=64, index=True)
    original_text: str = Field(default="")
    suggested_text: str = Field(default="")
    start_ms: int = Field(default=0)
    end_ms: int = Field(default=0)
    similarity: float = Field(default=0.0)
    status: str = Field(default="pending", index=True)
    reason: str = Field(default="", max_length=500)
    created_at: datetime = Field(default_factory=utcnow)
    reviewed_at: Optional[datetime] = Field(default=None)


class MeetingMember(SQLModel, table=True):
    """会议级协作者权限；管理员与会议所有者始终拥有完整权限。"""

    __tablename__ = "meeting_member"
    __table_args__ = (
        UniqueConstraint("meeting_id", "user_id", name="uq_meeting_member"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    role: str = Field(default="viewer", max_length=16)  # viewer | editor
    created_by: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)


class SegmentEmbedding(SQLModel, table=True):
    """可选语义检索向量；本地部署未配置 Embedding 时不写入。"""

    __tablename__ = "segment_embedding"

    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    segment_id: str = Field(max_length=64, index=True)
    model: str = Field(default="", max_length=128)
    dim: int = Field(default=0)
    vector: bytes = Field(default=b"")
    created_at: datetime = Field(default_factory=utcnow)


class AppConfigRow(SQLModel, table=True):
    """配置项持久化。key 为主键，value 存 JSON 字符串。"""

    __tablename__ = "app_config"

    key: str = Field(primary_key=True, max_length=128)
    value: str = Field(default="")
    updated_by: Optional[int] = Field(default=None)
    updated_at: datetime = Field(default_factory=utcnow)


class AuditLog(SQLModel, table=True):
    """敏感操作审计日志。"""

    __tablename__ = "audit_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, index=True)
    username: str = Field(default="", max_length=64)
    action: str = Field(max_length=64, index=True)
    target: Optional[str] = Field(default=None, max_length=200)
    payload: Optional[str] = Field(default=None)
    ip: Optional[str] = Field(default=None, max_length=64)
    #: 哈希链，防止普通数据库操作静默篡改审计记录。
    prev_hash: str = Field(default="", max_length=64)
    record_hash: str = Field(default="", max_length=64, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
