"""Pydantic 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=4, max_length=128)
    display_name: str = Field(default="", max_length=64)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: str
    is_active: bool
    must_change_password: bool = False
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class AuthBootstrapOut(BaseModel):
    """登录页的预填信息。

    ``default_password`` **只在管理员账号仍在使用出厂默认口令时**才回传。
    一旦改过密码就返回 None——既避免把真实口令泄露给未登录者，
    也避免用户照着预填值反复登录失败（那比不预填更让人困惑）。
    """

    default_username: str
    default_password: str | None = None
    using_default_password: bool = False


# ---------------------------------------------------------------------------
# 会议
# ---------------------------------------------------------------------------


class MeetingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    status: str
    duration_ms: int
    segment_count: int
    tags: str = ""
    archived: bool = False
    deleted_at: datetime | None = None
    description: str = ""
    participants: str = ""
    agenda: str = ""
    meeting_type: str = ""
    scheduled_at: datetime | None = None
    metadata_captured: bool = False
    retention_days: int = 0
    legal_hold: bool = False
    redacted_at: datetime | None = None
    created_at: datetime
    finished_at: datetime | None = None
    # 是否留存了音频；前端据此决定「回放」按钮是否可用。
    # 只暴露布尔值，不暴露服务端文件路径（避免泄露目录结构）。
    has_audio: bool = False

    @model_validator(mode="before")
    @classmethod
    def _derive_has_audio(cls, data: Any) -> Any:
        """从 ORM 对象的 audio_path 推导 has_audio。

        ORM 对象上没有 has_audio 字段，这里在转换前补出来；
        已是 dict 的入参（例如内部构造）则原样放行。
        """
        if isinstance(data, dict):
            return data
        path = getattr(data, "audio_path", None)
        try:
            # from_attributes 模式下需要转成 dict 才能加字段
            return {
                "id": data.id,
                "title": data.title,
                "status": data.status,
                "duration_ms": data.duration_ms,
                "segment_count": data.segment_count,
                "tags": getattr(data, "tags", "") or "",
                "archived": bool(getattr(data, "archived", False)),
                "deleted_at": getattr(data, "deleted_at", None),
                "description": getattr(data, "description", "") or "",
                "participants": getattr(data, "participants", "") or "",
                "agenda": getattr(data, "agenda", "") or "",
                "meeting_type": getattr(data, "meeting_type", "") or "",
                "scheduled_at": getattr(data, "scheduled_at", None),
                "metadata_captured": bool(
                    getattr(data, "metadata_captured", False)
                ),
                "retention_days": int(getattr(data, "retention_days", 0) or 0),
                "legal_hold": bool(getattr(data, "legal_hold", False)),
                "redacted_at": getattr(data, "redacted_at", None),
                "created_at": data.created_at,
                "finished_at": data.finished_at,
                "has_audio": bool(path),
            }
        except AttributeError:
            return data


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    segment_id: str
    text: str
    original_text: str = ""
    is_final: bool
    start_ms: int
    end_ms: int
    speaker: str | None = None
    speaker_score: float = 0.0
    speaker_source: str = ""
    #: 由后端统一按“全局阈值 / 短片段阈值”计算，前端不再各自硬编码。
    speaker_low_confidence: bool = False
    asr_source: str = ""
    asr_error: str | None = None
    finalize_reason: str = ""
    speech_ms: int = 0
    audio_rms: float = 0.0
    noise_floor: float = 0.0
    snr_db: float = 0.0
    adaptive_gain: float = 1.0
    profile_version: int = 0
    edited_at: datetime | None = None
    seq: int

    @model_validator(mode="after")
    def _mark_low_confidence(self):
        from .speaker_policy import is_low_confidence

        self.speaker_low_confidence = is_low_confidence(
            self.speaker_score,
            self.speech_ms,
            self.text,
            source=self.speaker_source,
        )
        return self


class MeetingDetail(MeetingOut):
    segments: list[SegmentOut] = []
    speaker_low_confidence_threshold: float = 0.62


class MeetingUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SearchMatchOut(BaseModel):
    meeting_id: int
    segment_id: str
    start_ms: int
    text: str
    score: float = 0.0


class MeetingSearchResult(BaseModel):
    items: list[MeetingOut]
    total: int
    matches: list[SearchMatchOut] = []


class MeetingBulkDeleteRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


class MeetingBulkDeleteResult(BaseModel):
    deleted: int
    deleted_ids: list[int]
    skipped: list[int]


class MeetingBulkResult(BaseModel):
    updated: int
    ids: list[int]
    skipped: list[int]


class MeetingBulkRenameRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    find: str = ""
    replace: str = ""
    prefix: str = ""
    suffix: str = ""


class MeetingBulkUpdateRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    archived: bool | None = None
    tags_add: list[str] = []
    tags_remove: list[str] = []


class MeetingBulkExportRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=50)
    format: str = "json"


class TranscriptItem(BaseModel):
    """供 LLM 使用的紧凑转写条目。"""

    start_ms: int
    end_ms: int
    speaker: str | None = None
    text: str


# ---------------------------------------------------------------------------
# 纪要
# ---------------------------------------------------------------------------


class TopicItem(BaseModel):
    title: str = ""
    detail: str = ""
    conclusion: str = ""
    evidence: list[dict] = []


class TodoItem(BaseModel):
    task: str = ""
    owner: str = ""
    due: str = ""
    evidence: list[dict] = []


class MinutesOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    meeting_id: int
    summary: str
    topics: list[TopicItem] = []
    todos: list[TodoItem] = []
    raw_markdown: str = ""
    model_used: str = ""
    status: str
    error: str | None = None
    version: int = 1
    is_draft: bool = False
    edited: bool = False
    created_at: datetime


class MinutesGenerateRequest(BaseModel):
    force: bool = Field(default=False, description="已有纪要时是否强制重新生成")
    template_id: int | None = Field(default=None, description="纪要模板 id")


class MinutesTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str = ""
    content: str = ""
    is_builtin: bool = False
    created_at: datetime


class MinutesTemplateCreate(BaseModel):
    name: str
    description: str = ""
    content: str


class MeetingMetadataUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    participants: str | None = Field(default=None, max_length=1000)
    agenda: str | None = Field(default=None, max_length=2000)
    meeting_type: str | None = Field(default=None, max_length=64)
    scheduled_at: datetime | None = None

    @field_validator("scheduled_at")
    @classmethod
    def _ensure_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(
                tzinfo=datetime.now().astimezone().tzinfo
            )
        return value


class SegmentUpdate(BaseModel):
    text: str | None = Field(default=None, max_length=20000)
    speaker: str | None = Field(default=None, max_length=32)


class MinutesEditRequest(BaseModel):
    summary: str | None = None
    topics: list[dict] | None = None
    todos: list[dict] | None = None


class ShareCreateRequest(BaseModel):
    expires_hours: int = Field(default=24, ge=1, le=24 * 365)
    password: str = ""
    allow_download: bool = False


class ShareOpenRequest(BaseModel):
    password: str = Field(default="", max_length=256)


class ShareOpenOut(BaseModel):
    access_token: str
    expires_in: int = 7200


class ShareOut(BaseModel):
    id: int
    #: 仅在创建时返回一次；列表接口不会再暴露可直接访问会议的明文令牌。
    token: str = ""
    token_hint: str = ""
    meeting_id: int
    expires_at: datetime
    allow_download: bool
    has_password: bool = False
    url: str = ""


class ActionItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    meeting_id: int
    task: str
    owner: str
    due: str
    status: str
    source: str
    source_version: int
    evidence: str = "[]"
    created_at: datetime
    updated_at: datetime


class ActionItemUpdate(BaseModel):
    task: str | None = None
    owner: str | None = None
    due: str | None = None
    status: str | None = None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    meeting_id: int | None = None
    owner_id: int | None = None
    kind: str
    status: str
    priority: int
    attempts: int
    max_attempts: int
    run_after: datetime
    last_error: str | None = None
    result: str = "{}"
    result_path: str | None = None
    download_name: str | None = None
    created_at: datetime
    updated_at: datetime


class GlossaryTermCreate(BaseModel):
    term: str = Field(min_length=1, max_length=128)
    aliases: str = Field(default="", max_length=1000)
    category: str = Field(default="", max_length=64)


class GlossaryTermUpdate(BaseModel):
    term: str | None = Field(default=None, min_length=1, max_length=128)
    aliases: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None


class GlossaryTermOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    term: str
    aliases: str
    category: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class TranscriptReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    meeting_id: int
    segment_id: str
    original_text: str
    suggested_text: str
    start_ms: int
    end_ms: int
    similarity: float
    status: str
    reason: str
    created_at: datetime
    reviewed_at: datetime | None = None


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


class ConfigFieldOut(BaseModel):
    key: str
    label: str
    group: str
    type: str
    value: Any = None
    help: str = ""
    options: list[str] | None = None
    secret: bool = False
    restart_required: bool = False
    min_value: float | None = None
    max_value: float | None = None


class ConfigSchemaOut(BaseModel):
    groups: dict[str, list[ConfigFieldOut]]


class ConfigUpdateRequest(BaseModel):
    values: dict[str, Any]


class ConfigUpdateResponse(BaseModel):
    updated: list[str] = []
    requires_restart: list[str] = []
    errors: dict[str, str] = {}


# ---------------------------------------------------------------------------
# 模型状态
# ---------------------------------------------------------------------------


class ModelStatusOut(BaseModel):
    name: str
    loaded: bool
    loading: bool
    error: str | None = None
    load_seconds: float = 0.0
    device: str = "cpu"


class HealthOut(BaseModel):
    status: str
    version: str
    models: dict[str, ModelStatusOut]
    active_sessions: int
