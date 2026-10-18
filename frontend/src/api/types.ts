/** 与后端接口契约对应的类型定义。 */

// ---------------------------------------------------------------------------
// 转写片段
// ---------------------------------------------------------------------------

export interface Segment {
  /** 会话内唯一。partial 与 final 共用同一 id，前端据此覆盖而非追加。 */
  segmentId: string
  text: string
  isFinal: boolean
  startMs: number
  endMs: number
  speaker: string | null
  speakerScore?: number
  speakerSource?: string
  lowConfidence?: boolean
  asrSource?: string
  translation?: string
  seq: number
}

// ---------------------------------------------------------------------------
// WebSocket 消息
// ---------------------------------------------------------------------------

export type ServerMessage =
  | {
      type: 'ready'
      meeting_id: number
      session_id: string
      sample_rate: number
      mode?: 'normal' | 'echo' | 'off'
      metadata_capture_minutes?: number
      speaker_low_confidence_threshold?: number
      audio_chunk_seq?: number
    }
  | { type: 'partial'; segment_id: string; text: string; start_ms: number; end_ms: number }
  | {
      type: 'final'
      segment_id: string
      text: string
      start_ms: number
      end_ms: number
      speaker: string | null
      speaker_key?: string | null
      speaker_score?: number
      speaker_source?: string
      speaker_low_confidence?: boolean
      asr_source?: string
      session_id?: string
      seq: number
    }
  | { type: 'speaker_update'; meeting_id: number; from: string; to: string }
  | { type: 'minutes_update'; meeting_id: number; minutes: Minutes }
  | { type: 'speakers'; data: SpeakerRatio[] }
  | { type: 'closed'; session_id: string }
  | { type: 'pong'; ts: number }
  | { type: 'error'; code: string; message: string }
  /**
   * 服务端独立探测到「持续收到全零 PCM」——即采集端没有有效音频。
   * 与前端本地的 onSilence 互为独立证据，任一方检出即可提示用户。
   */
  | { type: 'signal_warning'; message: string; silent_seconds: number }
  | { type: 'noise_warning'; message: string; snr_db: number }
  | {
      type: 'meeting_meta'
      meeting_id: number
      title: string
      participants: string[]
      agenda: string
      meeting_type: string
    }
  | { type: 'translation'; segment_id: string; text: string }
  | { type: 'audio_ack'; seq: number }

export interface SpeakerRatio {
  id: string
  ratio: number
}

// ---------------------------------------------------------------------------
// 会话状态
// ---------------------------------------------------------------------------

export type SessionStatus = 'idle' | 'connecting' | 'recording' | 'paused' | 'stopping' | 'error'

/** WebSocket 链路状态，用于顶栏指示灯。 */
export type WsState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

// ---------------------------------------------------------------------------
// 会议与纪要
// ---------------------------------------------------------------------------

export interface Meeting {
  id: number
  title: string
  /**
   * recording  进行中
   * finished   客户端显式 stop，正常结束
   * abandoned  连接被关/超时等非正常中断（已收到的内容仍保留）
   * failed     会话处理过程中抛异常
   */
  status: 'scheduled' | 'recording' | 'finished' | 'abandoned' | 'failed'
  duration_ms: number
  segment_count: number
  tags: string
  archived: boolean
  deleted_at: string | null
  description: string
  participants: string
  agenda: string
  meeting_type: string
  scheduled_at: string | null
  metadata_captured: boolean
  retention_days: number
  legal_hold: boolean
  redacted_at: string | null
  created_at: string
  finished_at: string | null
  /** 是否留存了音频；前端据此决定「回放」是否可用 */
  has_audio: boolean
}

export interface MeetingDetail extends Meeting {
  segments: ApiSegment[]
  speaker_low_confidence_threshold?: number
}

export interface MeetingSearchResult {
  items: Meeting[]
  total: number
  matches?: SearchMatch[]
}

export interface SearchMatch {
  meeting_id: number
  segment_id: string
  start_ms: number
  text: string
  score: number
}

export interface ShareLink {
  id: number
  token: string
  meeting_id: number
  expires_at: string
  allow_download: boolean
  has_password: boolean
  url: string
}

export interface ActionItem {
  id: number
  meeting_id: number
  task: string
  owner: string
  due: string
  status: 'open' | 'doing' | 'done'
  source: string
  source_version: number
  evidence: string
  created_at: string
  updated_at: string
}

export interface KnowledgeTopic {
  topic: string
  count: number
  meetings: { id: number; title: string }[]
}

export interface AdminDashboard {
  meetings: number
  archived: number
  trash: number
  duration_minutes: number
  segments: number
  open_actions: number
  llm_calls: number
  llm_prompt_chars: number
  llm_response_chars: number
  llm_avg_ms: number
  llm_prompt_tokens: number
  llm_completion_tokens: number
  llm_cost_usd: number
  latency_p95_median: number | null
  noise_warnings: number
  jobs: Record<string, number>
  maintenance: { draining: boolean; reason: string }
}

export interface ApiSegment {
  id: number
  segment_id: string
  text: string
  original_text?: string
  is_final: boolean
  start_ms: number
  end_ms: number
  speaker: string | null
  speaker_score?: number
  speaker_source?: string
  speaker_low_confidence?: boolean
  asr_source?: string
  asr_error?: string | null
  finalize_reason?: string
  speech_ms?: number
  audio_rms?: number
  noise_floor?: number
  snr_db?: number
  adaptive_gain?: number
  profile_version?: number
  edited_at?: string | null
  seq: number
}

export interface TopicItem {
  title: string
  detail: string
  conclusion: string
  evidence?: Evidence[]
}

export interface TodoItem {
  task: string
  owner: string
  due: string
  evidence?: Evidence[]
}

export interface Evidence {
  segment_id: string
  start_ms: number
  end_ms: number
  text: string
}

export interface Minutes {
  id: number
  meeting_id: number
  summary: string
  topics: TopicItem[]
  todos: TodoItem[]
  raw_markdown: string
  model_used: string
  status: 'ready' | 'failed'
  error: string | null
  version: number
  is_draft: boolean
  edited?: boolean
  created_at: string
}

export interface MinutesTemplate {
  id: number
  name: string
  description: string
  content: string
  is_builtin: boolean
  created_at: string
}

export interface MindMapNode {
  id: string
  label: string
  meta?: string
  evidence?: Evidence[]
  children?: MindMapNode[]
}

export interface MindMapResponse {
  meeting_id: number
  title: string
  version: number
  is_draft: boolean
  root: MindMapNode
}

// ---------------------------------------------------------------------------
// 认证
// ---------------------------------------------------------------------------

export interface User {
  id: number
  username: string
  display_name: string
  role: 'admin' | 'user'
  is_active: boolean
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  user: User
}

/** 登录页预填信息。
 *
 * `default_password` 仅在管理员**仍在使用出厂默认口令**时才有值；
 * 改过密码后为 `null`，此时不应把密码框预填成已失效的旧口令。
 */
export interface AuthBootstrap {
  default_username: string
  default_password: string | null
  using_default_password: boolean
}

// ---------------------------------------------------------------------------
// 配置
// ---------------------------------------------------------------------------

export type ConfigFieldType =
  | 'string'
  | 'int'
  | 'float'
  | 'bool'
  | 'select'
  | 'text'
  | 'password'
  | 'list'

export interface ConfigField {
  key: string
  label: string
  group: string
  type: ConfigFieldType
  value: unknown
  help: string
  options: string[] | null
  secret: boolean
  restart_required: boolean
  min_value: number | null
  max_value: number | null
}

export interface ConfigSchema {
  groups: Record<string, ConfigField[]>
}

export interface ConfigUpdateResponse {
  updated: string[]
  requires_restart: string[]
  errors: Record<string, string>
}

// ---------------------------------------------------------------------------
// 模型状态
// ---------------------------------------------------------------------------

export interface ModelStatus {
  name: string
  loaded: boolean
  loading: boolean
  error: string | null
  load_seconds: number
  device: string
}

export interface Health {
  /**
   * ok       服务可正常转写
   * loading  模型仍在加载（不是故障）
   * degraded 模型加载失败
   * disabled 配置为 off/echo 模式，本就不需要模型
   */
  status: 'ok' | 'loading' | 'degraded' | 'disabled'
  version: string
  models: Record<string, ModelStatus>
  active_sessions: number
}

/** 一条声纹。 */
export interface Voiceprint {
  id: number
  name: string
  /** 录入时使用的音频时长（毫秒）；自动发现的为 0 */
  sample_ms: number
  created_at: string
  /** 开会时自动发现、尚未人工命名的人（界面标注「待命名」） */
  provisional: boolean
  /** 首次发现该说话人的会议 id */
  source_meeting_id: number | null
  sample_count: number
  quality_score: number
  disabled: boolean
  last_seen_at: string | null
}

export interface Job {
  id: number
  meeting_id: number | null
  owner_id: number | null
  kind: string
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  priority: number
  attempts: number
  max_attempts: number
  run_after: string
  last_error: string | null
  result: string
  result_path: string | null
  download_name: string | null
  created_at: string
  updated_at: string
}

export interface GlossaryTerm {
  id: number
  term: string
  aliases: string
  category: string
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface TranscriptReview {
  id: number
  meeting_id: number
  segment_id: string
  original_text: string
  suggested_text: string
  start_ms: number
  end_ms: number
  similarity: number
  status: string
  reason: string
  created_at: string
  reviewed_at: string | null
}
