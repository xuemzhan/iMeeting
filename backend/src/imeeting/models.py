"""数据模型定义（SQLModel）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import CheckConstraint, UniqueConstraint
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
    must_change_password: bool = Field(default=False)
    auth_version: int = Field(default=0)
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
    #: 解绑/改绑时保留的原标注（例如「郭睿」）。只做追溯与展示，
    #: 判定仍以 ``speaker`` / ``speaker_source`` 为准。
    speaker_previous: str = Field(default="", max_length=32)
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
    #: 列的旧名称保留兼容；这里始终保存明文令牌的 SHA-256，不保存可直接使用的链接。
    token: str = Field(unique=True, index=True, max_length=64)
    token_hint: str = Field(default="", max_length=16)
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
    #: 永久关联的人员（REQ-001）：人员是身份事实源，声纹只是「怎么被听出来」。
    #: 过渡期允许为空——旧数据、自动临时声纹在人工绑定前还没有对应人员。
    person_id: Optional[int] = Field(default=None, foreign_key="person.id", index=True)
    #: 停用后不参与后续会议的声纹匹配，但保留历史身份。
    disabled: bool = Field(default=False, index=True)
    #: 0~1，当前由样本时长和人工确认状态推导。
    quality_score: float = Field(default=0.0)
    last_seen_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SpeakerRole(SQLModel, table=True):
    """会议内说话人的重要程度（纪要加权用）。

    重要程度：**1 = 关键（最高）**、2 = 次要、3 = 一般（默认）。
    键优先用 ``Segment.speaker_key``——改名只改 label、键不变，因此重命名
    不会丢重要程度；历史数据没有 key 时退回 ``label:<显示名>``。
    """

    __tablename__ = "speaker_role"
    __table_args__ = (
        UniqueConstraint("meeting_id", "speaker_key", name="uq_speaker_role"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    #: 与 Segment.speaker_key 对应（旧数据用 "label:<显示名>" 兜底）
    speaker_key: str = Field(max_length=96, index=True)
    #: 设置时的显示名，仅用于排障；展示名以片段最新值为准
    label: str = Field(default="", max_length=64)
    importance: int = Field(default=3, index=True)  # 1 关键 | 2 次要 | 3 一般
    #: 参会人处理状态：0 未处理（面板显示）| 1 已确认 | 2 已删除（后两者不再显示）
    state: int = Field(default=0, index=True)
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


# ---------------------------------------------------------------------------
# 人员本体（REQ-001）
# ---------------------------------------------------------------------------


class Person(SQLModel, table=True):
    """跨会议复用的人员实体。

    身份主键：``voiceprint`` 只是「这个人怎么被听出来」的实现细节，姓名、
    单位、职务等都挂在这里。同一 ``owner_id`` 下展示名唯一，避免同名重复建档。
    """

    __tablename__ = "person"
    __table_args__ = (
        UniqueConstraint("owner_id", "display_name", name="uq_person_owner_name"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="user.id", index=True)
    display_name: str = Field(max_length=64, index=True)
    #: 检索用拼音（声母+韵母，去声调），无依赖的简化实现
    pinyin: str = Field(default="", max_length=128, index=True)
    #: active | merged | deleted
    status: str = Field(default="active", max_length=16, index=True)
    #: 合并去向（status=merged 时指向保留的那一位）
    merged_into_id: Optional[int] = Field(default=None, foreign_key="person.id")
    #: 同名不同人：待人工用单位/职务区分
    ambiguous: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PersonAlias(SQLModel, table=True):
    """别名/称呼/旧名：改名不丢历史匹配。"""

    __tablename__ = "person_alias"
    __table_args__ = (
        UniqueConstraint("person_id", "alias", name="uq_person_alias"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="person.id", index=True)
    alias: str = Field(max_length=64, index=True)
    #: oral | manual | import
    source: str = Field(default="oral", max_length=16)
    confidence: float = Field(default=0.0)
    created_at: datetime = Field(default_factory=utcnow)


class PersonAttribute(SQLModel, table=True):
    """人员属性（单位/职务/领域…），带来源与证据。

    幂等锚点是 ``(person_id, category, key)``：重复抽取只更新值、置信度与证据，
    不会堆出多行。
    """

    __tablename__ = "person_attribute"
    __table_args__ = (
        UniqueConstraint(
            "person_id", "category", "key", name="uq_person_attribute"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="person.id", index=True)
    #: 该属性是从哪场会议听来的（人工修改可为空）
    meeting_id: Optional[int] = Field(default=None, foreign_key="meeting.id", index=True)
    #: identity | org | role | domain | contact
    category: str = Field(max_length=16, index=True)
    #: unit / title / department / ...
    key: str = Field(max_length=32, index=True)
    value: str = Field(default="", max_length=128)
    confidence: float = Field(default=0.0)
    #: llm | rule | manual | import
    source: str = Field(default="llm", max_length=16)
    #: JSON 数组：["segment_id", ...]
    evidence: str = Field(default="[]")
    updated_at: datetime = Field(default_factory=utcnow)


class MeetingParticipant(SQLModel, table=True):
    """会议 × 人员关系；同时承担 speaker_key ↔ person 的绑定。

    ``state``：pending 待确认 | confirmed 已确认 | rejected 已删除（不再显示，
    但保留审计与统计）。
    """

    __tablename__ = "meeting_participant"
    __table_args__ = (
        UniqueConstraint("meeting_id", "person_id", name="uq_participant_meeting_person"),
        UniqueConstraint("meeting_id", "speaker_key", name="uq_participant_meeting_key"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    person_id: int = Field(foreign_key="person.id", index=True)
    #: 会议内稳定键；为空表示尚未与声音绑定
    speaker_key: Optional[str] = Field(default=None, max_length=32)
    #: 主持 | 主讲 | 评审 | 记录 | 旁听 | ""
    role: str = Field(default="", max_length=16)
    importance: int = Field(default=3)  # 1 关键 | 2 次要 | 3 一般
    #: pending | confirmed | rejected
    state: str = Field(default="pending", max_length=16, index=True)
    confidence: float = Field(default=0.0)
    evidence: str = Field(default="[]")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# 多场景声纹模板（REQ-002）
# ---------------------------------------------------------------------------


class VoiceprintTemplate(SQLModel, table=True):
    """一条场景化声纹模板。

    ``slot`` 取 0..4，配合 ``UNIQUE(voiceprint_id, slot)`` 在库层硬约束「每人
    ≤5 条」；替换模板就是改 slot，天然不会写出第 6 条。
    """

    __tablename__ = "voiceprint_template"
    __table_args__ = (
        UniqueConstraint("voiceprint_id", "slot", name="uq_voiceprint_template_slot"),
        CheckConstraint("slot <= 4", name="ck_voiceprint_template_slot_range"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    voiceprint_id: int = Field(foreign_key="voiceprint.id", index=True)
    slot: int = Field(default=0, index=True)
    disabled: bool = Field(default=False, index=True)
    #: 192 × float32（L2 归一化后）
    embedding: bytes = Field(default=b"")
    dim: int = Field(default=192)
    #: 场景桶：设备/距离/噪声/发声
    scenario_device: str = Field(default="", max_length=16)
    scenario_distance: str = Field(default="", max_length=16)
    scenario_noise: str = Field(default="", max_length=16)
    scenario_voice: str = Field(default="", max_length=16)
    quality: float = Field(default=0.0)
    sample_ms: int = Field(default=0)
    snr_db: float = Field(default=0.0)
    source_meeting_id: Optional[int] = Field(default=None, index=True)
    source_segment_id: Optional[str] = Field(default=None, max_length=64)
    #: 只有人工确认过的模板跨会议生效
    confirmed: bool = Field(default=False, index=True)
    confirmed_by: Optional[int] = Field(default=None)
    confirmed_at: Optional[datetime] = Field(default=None)
    #: 被替换时指向新模板，保留可回滚
    superseded_by: Optional[int] = Field(default=None, index=True)
    #: 主模板（REQ-002 FR-5）：每人最多一条，自动淘汰时受保护，需先取消才能停用/替换。
    #: 库层用 SQLite 部分唯一索引 ``uq_voiceprint_template_pinned`` 兜底（见迁移）。
    pinned: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class VoiceprintConflict(SQLModel, table=True):
    """确认声纹时的冲突记录（也是阈值校准的负样本来源）。

    ``status``：pending 待用户决定 | accepted 确认为本人 | reassigned 判给别人
    | cancelled 取消 | tolerated 已容忍（同一对不再打扰）。
    """

    __tablename__ = "voiceprint_conflict"

    id: Optional[int] = Field(default=None, primary_key=True)
    voiceprint_id: int = Field(foreign_key="voiceprint.id", index=True)
    meeting_id: Optional[int] = Field(default=None, index=True)
    segment_id: Optional[str] = Field(default=None, max_length=64)
    #: 只保留向量，不留原始音频
    sample_embedding: bytes = Field(default=b"")
    self_score: Optional[float] = Field(default=None)
    other_voiceprint_id: Optional[int] = Field(default=None, index=True)
    other_score: Optional[float] = Field(default=None)
    #: diverge | conflict | both
    reason: str = Field(default="", max_length=16)
    #: pending | accepted | reassigned | cancelled | tolerated
    status: str = Field(default="pending", max_length=16, index=True)
    decided_by: Optional[int] = Field(default=None)
    decided_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)


class VoiceprintTemplateSource(SQLModel, table=True):
    """Unique source contributions make repeated/reordered rebuilds idempotent."""

    __tablename__ = "voiceprint_template_source"
    __table_args__ = (UniqueConstraint("voiceprint_id", "source_key", name="uq_template_source"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    voiceprint_id: int = Field(foreign_key="voiceprint.id", index=True)
    template_id: int = Field(foreign_key="voiceprint_template.id", index=True)
    source_key: str = Field(max_length=200)
    embedding: bytes
    weight: float = Field(default=1.0)
    sample_ms: int = Field(default=0)


class PersonExtractionState(SQLModel, table=True):
    """Persistent quotas/cursor/lease shared by manual and background extraction."""

    __tablename__ = "person_extraction_state"
    meeting_id: int = Field(foreign_key="meeting.id", ondelete="CASCADE", primary_key=True)
    manual_count: int = Field(default=0)
    auto_count: int = Field(default=0)
    last_manual_at: Optional[datetime] = None
    last_auto_at: Optional[datetime] = None
    cursor_id: int = Field(default=0)
    failures: int = Field(default=0)
    lease_token: Optional[str] = Field(default=None, max_length=64)
    lease_until: Optional[datetime] = None


class TemplateSampleEmbedding(SQLModel, table=True):
    """会后模板取样的声纹向量缓存（按片段复用，避免重复解码）。

    一场会议重跑模板重建时为每位说话人取 ≤``spk.template_sample_max`` 段音频，
    每段都要解码 + 跑一次 CAM++，几百段的会议是数分钟级开销。同一片段只要音频
    文件与片段边界没变，嵌入就完全一致，因此这里按 ``(meeting_id, segment_id)``
    缓存向量；``audio_signature`` 由音频文件大小/修改时间与片段
    ``start_ms/end_ms/adaptive_gain`` 派生，任一变化即视为失效。
    """

    __tablename__ = "template_sample_embedding"
    __table_args__ = (
        UniqueConstraint("meeting_id", "segment_id", name="uq_template_sample_segment"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", ondelete="CASCADE", index=True)
    segment_id: str = Field(max_length=64, index=True)
    #: "音频签名|start|end|gain" 的 sha256，用于判定缓存是否仍对应当前音频
    audio_signature: str = Field(default="", max_length=64)
    dim: int = Field(default=192)
    embedding: bytes = Field(default=b"")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TemplateRebuildState(SQLModel, table=True):
    """会后模板重建的细粒度进度：阶段、说话人数与取样命中数。

    进度按说话人写入短事务，进程重启或任务失败后仍可查询到最后到达的阶段，
    便于前端提示和排障，不必等整场聚类结束。
    """

    __tablename__ = "template_rebuild_state"

    meeting_id: int = Field(foreign_key="meeting.id", ondelete="CASCADE", primary_key=True)
    job_id: Optional[int] = Field(default=None, index=True)
    #: idle | scanning | extracting | storing | done | failed | skipped
    stage: str = Field(default="idle", max_length=24, index=True)
    speakers_total: int = Field(default=0)
    speakers_done: int = Field(default=0)
    #: 已处理取样数（= samples_cached + samples_extracted）
    samples_done: int = Field(default=0)
    samples_cached: int = Field(default=0)
    samples_extracted: int = Field(default=0)
    templates_created: int = Field(default=0)
    templates_updated: int = Field(default=0)
    #: 当前处理或最近一次跳过的说明（截断到 200 字）
    note: str = Field(default="", max_length=200)
    last_error: str = Field(default="", max_length=500)
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = Field(default=None)
    updated_at: datetime = Field(default_factory=utcnow)
