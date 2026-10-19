/** REST 客户端：统一处理鉴权注入、错误、JSON 解析。 */

import type {
  ActionItem,
  AdminDashboard,
  ApiSegment,
  AuthBootstrap,
  ConfigSchema,
  ConfigUpdateResponse,
  Health,
  Job,
  KnowledgeTopic,
  Meeting,
  MeetingDetail,
  MeetingSearchResult,
  MindMapResponse,
  Minutes,
  MinutesTemplate,
  ShareLink,
  GlossaryTerm,
  TranscriptReview,
  TokenResponse,
  User,
  Voiceprint,
} from './types'

const TOKEN_KEY = 'imeeting-token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

/** 令牌被服务端拒绝（过期/失效）时触发，供 UI 层同步登出状态。
 *
 * 不能在 client 里直接 import auth store（会形成循环依赖），
 * 所以用事件解耦：store 订阅、这里只负责广播。
 */
export const AUTH_EXPIRED_EVENT = 'auth-expired'

/** 这些端点的 401 是「凭证不对」的业务结果，不代表已登录的会话失效，
 *  不应触发全局登出（例如登录时密码错→401，此时不能清掉旧的登录态）。
 *
 *  `/bootstrap` 是公开接口（登录页预填），即使浏览器里残留了过期令牌、
 *  它也不该把用户踢成「已过期」——否则每次打开登录页都会先闪一条报错。
 */
const AUTH_EXEMPT_PATHS = [
  '/api/v1/auth/login',
  '/api/v1/auth/register',
  '/api/v1/auth/bootstrap',
]

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)

  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  if (init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }

  const res = await fetch(path, { cache: 'no-store', ...init, headers })

  if (res.status === 204) return undefined as T

  const text = await res.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }

  if (!res.ok) {
    let detail = `请求失败 (${res.status})`
    if (data && typeof data === 'object' && 'detail' in data) {
      const raw = (data as { detail: unknown }).detail
      if (typeof raw === 'string') detail = raw
      else if (Array.isArray(raw) && raw.length) {
        const first = raw[0] as { msg?: string }
        detail = first?.msg ?? detail
      }
    }

    // 401：仅在「本来带着令牌、且不是登录/注册本身」时才认定为会话失效。
    // 否则会把登录失败误判成掉线，也会在匿名访问私有接口时污染登录态。
    if (res.status === 401 && token && !AUTH_EXEMPT_PATHS.some((p) => path.startsWith(p))) {
      setToken(null)
      window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT))
    }

    throw new ApiError(res.status, detail)
  }

  return data as T
}

async function requestBlob(path: string, init: RequestInit = {}): Promise<Blob> {
  const headers = new Headers(init.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  const res = await fetch(path, { ...init, headers })
  if (!res.ok) {
    let detail = `请求失败 (${res.status})`
    try {
      const data = await res.json()
      if (typeof data?.detail === 'string') detail = data.detail
    } catch {
      // ignore
    }
    throw new ApiError(res.status, detail)
  }
  return res.blob()
}

// ---------------------------------------------------------------------------
// 认证
// ---------------------------------------------------------------------------

export const authApi = {
  login: (username: string, password: string) =>
    request<TokenResponse>('/api/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),

  register: (username: string, password: string, displayName = '') =>
    request<TokenResponse>('/api/v1/auth/register', {
      method: 'POST',
      body: JSON.stringify({ username, password, display_name: displayName }),
    }),

  me: () => request<User>('/api/v1/auth/me'),

  changePassword: (currentPassword: string, newPassword: string) =>
    request<User>('/api/v1/auth/password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    }),

  /** 登录页预填：拿出厂默认管理员账号（口令仅在仍是默认值时返回）。 */
  bootstrap: () => request<AuthBootstrap>('/api/v1/auth/bootstrap'),
}

// ---------------------------------------------------------------------------
// 系统
// ---------------------------------------------------------------------------

export const systemApi = {
  health: () => request<Health>('/api/v1/health'),
}

// ---------------------------------------------------------------------------
// 会议
// ---------------------------------------------------------------------------

export const meetingApi = {
  list: (limit = 50, offset = 0) =>
    request<Meeting[]>(`/api/v1/meetings?limit=${limit}&offset=${offset}`),

  get: (id: number) => request<MeetingDetail>(`/api/v1/meetings/${id}`),

  search: (params: {
    q?: string
    status?: string
    dateFrom?: string
    archived?: string
    tag?: string
    limit?: number
    offset?: number
  }) => {
    const sp = new URLSearchParams()
    if (params.q) sp.set('q', params.q)
    if (params.status) sp.set('status', params.status)
    if (params.dateFrom) sp.set('date_from', params.dateFrom)
    if (params.archived) sp.set('archived', params.archived)
    if (params.tag) sp.set('tag', params.tag)
    sp.set('limit', String(params.limit ?? 100))
    sp.set('offset', String(params.offset ?? 0))
    return request<MeetingSearchResult>(`/api/v1/meetings/search?${sp.toString()}`)
  },

  rename: (id: number, title: string) =>
    request<Meeting>(`/api/v1/meetings/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    }),

  remove: (id: number) => request<void>(`/api/v1/meetings/${id}`, { method: 'DELETE' }),

  bulkRemove: (ids: number[]) =>
    request<{ deleted: number; deleted_ids: number[]; skipped: number[] }>(
      '/api/v1/meetings/bulk-delete',
      { method: 'POST', body: JSON.stringify({ ids }) },
    ),

  trash: (limit = 50, offset = 0) =>
    request<MeetingSearchResult>(
      `/api/v1/meetings/trash?limit=${limit}&offset=${offset}`,
    ),

  bulkRestore: (ids: number[]) =>
    request<{ updated: number; ids: number[]; skipped: number[] }>(
      '/api/v1/meetings/bulk-restore',
      { method: 'POST', body: JSON.stringify({ ids }) },
    ),

  bulkPurge: (ids: number[]) =>
    request<{ deleted: number; deleted_ids: number[]; skipped: number[] }>(
      '/api/v1/meetings/bulk-purge',
      { method: 'POST', body: JSON.stringify({ ids }) },
    ),

  bulkRename: (
    ids: number[],
    options: { find?: string; replace?: string; prefix?: string; suffix?: string },
  ) =>
    request<{ updated: number; ids: number[]; skipped: number[] }>(
      '/api/v1/meetings/bulk-rename',
      { method: 'POST', body: JSON.stringify({ ids, ...options }) },
    ),

  bulkUpdate: (
    ids: number[],
    options: { archived?: boolean; tagsAdd?: string[]; tagsRemove?: string[] },
  ) =>
    request<{ updated: number; ids: number[]; skipped: number[] }>(
      '/api/v1/meetings/bulk-update',
      {
        method: 'POST',
        body: JSON.stringify({
          ids,
          archived: options.archived,
          tags_add: options.tagsAdd ?? [],
          tags_remove: options.tagsRemove ?? [],
        }),
      },
    ),

  bulkExport: (ids: number[], format: string) =>
    requestBlob('/api/v1/meetings/bulk-export', {
      method: 'POST',
      body: JSON.stringify({ ids, format }),
    }),

  generateMinutes: (id: number, force = false, templateId: number | null = null) =>
    request<Minutes>(`/api/v1/meetings/${id}/minutes`, {
      method: 'POST',
      body: JSON.stringify({ force, template_id: templateId }),
    }),

  getMinutes: (id: number) => request<Minutes | null>(`/api/v1/meetings/${id}/minutes`),

  mindmap: (id: number) => request<MindMapResponse>(`/api/v1/meetings/${id}/mindmap`),

  updateMetadata: (
    id: number,
    data: Partial<{
      title: string
      description: string
      participants: string
      agenda: string
      meeting_type: string
      scheduled_at: string
    }>,
  ) =>
    request<Meeting>(`/api/v1/meetings/${id}/metadata`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  updateRetention: (
    id: number,
    data: { retention_days?: number; legal_hold?: boolean },
  ) =>
    request<{ retention_days: number; legal_hold: boolean }>(
      `/api/v1/meetings/${id}/retention`,
      { method: 'PATCH', body: JSON.stringify(data) },
    ),

  redact: (id: number, dryRun = false) =>
    request<{ dry_run: boolean; replacements: number }>(
      `/api/v1/meetings/${id}/redact`,
      { method: 'POST', body: JSON.stringify({ dry_run: dryRun }) },
    ),

  editSegment: (id: number, segmentId: string, data: { text?: string; speaker?: string }) =>
    request<ApiSegment>(
      `/api/v1/meetings/${id}/segments/${encodeURIComponent(segmentId)}`,
      { method: 'PATCH', body: JSON.stringify(data) },
    ),

  minutesVersions: (id: number) =>
    request<Minutes[]>(`/api/v1/meetings/${id}/minutes/versions`),

  editMinutes: (
    id: number,
    version: number,
    data: { summary?: string; topics?: unknown[]; todos?: unknown[] },
  ) =>
    request<Minutes>(`/api/v1/meetings/${id}/minutes/${version}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  createShare: (
    id: number,
    options: { expires_hours?: number; password?: string; allow_download?: boolean },
  ) =>
    request<ShareLink>(`/api/v1/meetings/${id}/shares`, {
      method: 'POST',
      body: JSON.stringify(options),
    }),

  listShares: (id: number) => request<ShareLink[]>(`/api/v1/meetings/${id}/shares`),

  revokeShare: (id: number, token: string) =>
    request<void>(`/api/v1/meetings/${id}/shares/${token}`, { method: 'DELETE' }),

  actions: (id: number) => request<ActionItem[]>(`/api/v1/meetings/${id}/actions`),

  allActions: (status?: string) =>
    request<ActionItem[]>(`/api/v1/actions${status ? `?status=${status}` : ''}`),

  updateAction: (
    actionId: number,
    data: Partial<{ task: string; owner: string; due: string; status: string }>,
  ) =>
    request<ActionItem>(`/api/v1/actions/${actionId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  importIcs: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<{ imported: number; skipped: number; meetings: Meeting[] }>(
      '/api/v1/meetings/import-ics',
      { method: 'POST', body: fd },
    )
  },

  scheduled: () => request<Meeting[]>('/api/v1/meetings/scheduled'),

  exportBlob: (id: number, format: string) =>
    requestBlob(`/api/v1/meetings/${id}/export?format=${format}`),

  exportUrl: (id: number, format: string) => `/api/v1/meetings/${id}/export?format=${format}`,

  /** 音频回放 URL。需要鉴权，用 fetch + blob 方式加载。 */
  audioUrl: (id: number) => `/api/v1/meetings/${id}/audio`,

  createExportJob: (id: number, format: string) =>
    request<Job>(`/api/v1/meetings/${id}/export-jobs`, {
      method: 'POST',
      body: JSON.stringify({ format }),
    }),

  createBulkExportJob: (ids: number[], format: string) =>
    request<Job>('/api/v1/meetings/bulk-export-jobs', {
      method: 'POST',
      body: JSON.stringify({ ids, format }),
    }),

  exportJob: (jobId: number) => request<Job>(`/api/v1/export-jobs/${jobId}`),

  downloadExportJob: (jobId: number) =>
    requestBlob(`/api/v1/export-jobs/${jobId}/download`),

  reviews: (id: number, status = 'pending') =>
    request<TranscriptReview[]>(
      `/api/v1/meetings/${id}/reviews?status=${encodeURIComponent(status)}`,
    ),

  reviewAudio: (id: number) =>
    request<{ job_id: number; status: string }>(
      `/api/v1/meetings/${id}/review-audio`,
      { method: 'POST' },
    ),

  acceptReview: (id: number) =>
    request<TranscriptReview>(`/api/v1/reviews/${id}/accept`, { method: 'POST' }),

  rejectReview: (id: number) =>
    request<TranscriptReview>(`/api/v1/reviews/${id}/reject`, { method: 'POST' }),

  members: (id: number) =>
    request<
      { user_id: number; username: string; display_name: string; role: string }[]
    >(`/api/v1/meetings/${id}/members`),

  addMember: (id: number, username: string, role: string) =>
    request<{ user_id: number; username: string; display_name: string; role: string }>(
      `/api/v1/meetings/${id}/members`,
      { method: 'POST', body: JSON.stringify({ username, role }) },
    ),

  removeMember: (id: number, userId: number) =>
    request<void>(`/api/v1/meetings/${id}/members/${userId}`, {
      method: 'DELETE',
    }),
}

export const adminApi = {
  dashboard: () => request<AdminDashboard>('/api/v1/admin/dashboard'),
  audit: (limit = 100, offset = 0, action = '') => {
    const sp = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    })
    if (action) sp.set('action', action)
    return request<{
      total: number
      items: {
        id: number
        username: string
        action: string
        target: string | null
        created_at: string
      }[]
    }>(`/api/v1/admin/audit?${sp.toString()}`)
  },
  jobs: (status = '') =>
    request<Job[]>(`/api/v1/admin/jobs${status ? `?status=${status}` : ''}`),
  retryJob: (id: number) =>
    request<Job>(`/api/v1/admin/jobs/${id}/retry`, { method: 'POST' }),
  maintenance: () =>
    request<{ draining: boolean; reason: string; active_sessions: number }>(
      '/api/v1/admin/maintenance',
    ),
  drain: () =>
    request<{ draining: boolean; active_sessions: number }>(
      '/api/v1/admin/maintenance/drain',
      { method: 'POST', body: JSON.stringify({ reason: 'release' }) },
    ),
  resume: () =>
    request<{ draining: boolean }>('/api/v1/admin/maintenance/resume', {
      method: 'POST',
    }),
  verifyAudit: () =>
    request<{ valid: boolean; records: number; broken_at: number | null }>(
      '/api/v1/admin/audit/verify',
    ),
  exportAudit: (action = '') =>
    requestBlob(
      `/api/v1/admin/audit/export${action ? `?action=${encodeURIComponent(action)}` : ''}`,
    ),
  reindexSearch: () =>
    request<{ job_id: number; status: string }>('/api/v1/admin/search/reindex', {
      method: 'POST',
      body: JSON.stringify({}),
    }),
}

export const knowledgeApi = {
  search: (q: string, tag = '', limit = 50) => {
    const sp = new URLSearchParams({ q, limit: String(limit) })
    if (tag) sp.set('tag', tag)
    return request<MeetingSearchResult>(`/api/v1/knowledge/search?${sp.toString()}`)
  },
  topics: (limit = 20) =>
    request<KnowledgeTopic[]>(`/api/v1/knowledge/topics?limit=${limit}`),
}

export const glossaryApi = {
  list: () => request<GlossaryTerm[]>('/api/v1/glossary'),
  create: (term: string, aliases: string, category = '') =>
    request<GlossaryTerm>('/api/v1/glossary', {
      method: 'POST',
      body: JSON.stringify({ term, aliases, category }),
    }),
  update: (
    id: number,
    data: Partial<{ term: string; aliases: string; category: string; enabled: boolean }>,
  ) =>
    request<GlossaryTerm>(`/api/v1/glossary/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  remove: (id: number) =>
    request<void>(`/api/v1/glossary/${id}`, { method: 'DELETE' }),
}

export const shareApi = {
  open: (token: string, password = '') =>
    request<{ access_token: string; expires_in: number }>(
      `/api/v1/share/${encodeURIComponent(token)}/open`,
      {
        method: 'POST',
        body: JSON.stringify({ password }),
      },
    ),
  get: (token: string, accessToken: string) =>
    request<{
      meeting: Meeting
      segments: ApiSegment[]
      minutes: Minutes | null
      allow_download: boolean
    }>(
      `/api/v1/share/${encodeURIComponent(token)}`,
      { headers: { Authorization: `Bearer ${accessToken}` } },
    ),
  audio: (token: string, accessToken: string) =>
    requestBlob(
      `/api/v1/share/${encodeURIComponent(token)}/audio`,
      { headers: { Authorization: `Bearer ${accessToken}` } },
    ),
}

// ---------------------------------------------------------------------------
// 纪要模板
// ---------------------------------------------------------------------------

export const minutesTemplateApi = {
  list: () => request<MinutesTemplate[]>('/api/v1/minutes-templates'),

  create: (name: string, description: string, content: string) =>
    request<MinutesTemplate>('/api/v1/minutes-templates', {
      method: 'POST',
      body: JSON.stringify({ name, description, content }),
    }),

  importFile: (file: File, name = '') => {
    const fd = new FormData()
    fd.append('file', file)
    if (name) fd.append('name', name)
    return request<MinutesTemplate>('/api/v1/minutes-templates/import', {
      method: 'POST',
      body: fd,
    })
  },

  remove: (id: number) =>
    request<void>(`/api/v1/minutes-templates/${id}`, { method: 'DELETE' }),
}

// ---------------------------------------------------------------------------
// 声纹
// ---------------------------------------------------------------------------

export const voiceprintApi = {
  list: () => request<Voiceprint[]>('/api/v1/voiceprints'),

  /** 录入声纹：姓名 + 一段 wav。同名会覆盖。 */
  create: (name: string, wav: Blob) => {
    const fd = new FormData()
    fd.append('name', name)
    fd.append('file', wav, 'voiceprint.wav')
    // request() 对 FormData 不会强制 Content-Type，交给浏览器带 boundary
    return request<Voiceprint>('/api/v1/voiceprints', { method: 'POST', body: fd })
  },

  /**
   * 按「说话人标签」改名，并回溯更新已有转写片段。
   *
   * 会议进行中点某条语句的说话人标签时用这个：既改声纹库（以后认得出），
   * 也把已落库的旧标签一起改掉（回看历史时不会还是「说话人 1」）。
   */
  relabel: (meetingId: number, fromLabel: string, toName: string) =>
    request<{ renamed_voiceprint: boolean; updated_segments: number; name: string }>(
      '/api/v1/voiceprints/relabel',
      {
        method: 'POST',
        body: JSON.stringify({
          meeting_id: meetingId,
          from_label: fromLabel,
          to_name: toName,
        }),
      },
    ),

  /** 把 source 合并进 target：片段改挂到 target 名下，source 删除。 */
  merge: (sourceId: number, targetId: number) =>
    request<{ renamed_voiceprint: boolean; updated_segments: number; name: string }>(
      `/api/v1/voiceprints/${sourceId}/merge/${targetId}`,
      { method: 'POST' },
    ),

  /** 按 id 改名。用于设置页里对「待命名」条目改名（改名即转正）。 */
  rename: (id: number, name: string) =>
    request<Voiceprint>(`/api/v1/voiceprints/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    }),

  remove: (id: number) => request<void>(`/api/v1/voiceprints/${id}`, { method: 'DELETE' }),

  setDisabled: (id: number, disabled: boolean) =>
    request<Voiceprint>(`/api/v1/voiceprints/${id}/state`, {
      method: 'PATCH',
      body: JSON.stringify({ disabled }),
    }),

  suggestions: (meetingId?: number, threshold = 0.62) => {
    const sp = new URLSearchParams({ threshold: String(threshold) })
    if (meetingId != null) sp.set('meeting_id', String(meetingId))
    return request<
      {
        score: number
        source: Voiceprint
        target: Voiceprint
      }[]
    >(`/api/v1/voiceprints/merge-suggestions?${sp.toString()}`)
  },
}

// ---------------------------------------------------------------------------
// 配置
// ---------------------------------------------------------------------------

export const configApi = {
  read: () => request<Record<string, unknown>>('/api/v1/config'),

  schema: () => request<ConfigSchema>('/api/v1/config/schema'),

  update: (values: Record<string, unknown>) =>
    request<ConfigUpdateResponse>('/api/v1/config', {
      method: 'PUT',
      body: JSON.stringify({ values }),
    }),
}
