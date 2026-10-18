import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import {
  ArrowLeft,
  Save,
  RotateCcw,
  AlertTriangle,
  Loader2,
  CheckCircle2,
  Settings2,
  LayoutDashboard,
  ScrollText,
  Timer,
  Database,
  Bot,
  ListChecks,
  BookMarked,
  Download,
  ShieldCheck,
  Power,
} from 'lucide-react'
import { adminApi, systemApi } from '../../api/client'
import { ConfigFieldInput } from './ConfigFieldInput'
import { TopBar } from '../../components/TopBar'
import { useConfigEditor } from '../../hooks/useConfigEditor'
import { GlossaryPanel } from '../../components/GlossaryPanel'

export default function Admin() {
  const [tab, setTab] = useState<'config' | 'dashboard' | 'glossary' | 'audit'>('config')
  const {
    schema,
    isLoading,
    loadError,
    draft,
    errors,
    saving,
    toast,
    hasChanges,
    handleChange,
    save,
    reset,
    setToast,
  } = useConfigEditor()

  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: () => systemApi.health(),
    refetchInterval: 5000,
  })

  const error = loadError

  return (
    <div className="flex h-full flex-col">
      <TopBar />

      <main className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-[860px]">
          <Link
            to="/"
            className="mb-4 inline-flex items-center gap-1 text-[12px] no-underline text-tertiary hover:text-secondary"
          >
            <ArrowLeft size={13} />
            返回会议
          </Link>

          <div className="mb-4 flex items-center justify-between">
            <div>
              <h1 className="text-[15px] font-medium">
                {tab === 'config'
                  ? '后台配置'
                  : tab === 'dashboard'
                    ? '运营数据看板'
                    : tab === 'glossary'
                      ? '企业词表'
                    : '审计日志'}
              </h1>
              <p className="mt-0.5 text-[12px] text-tertiary">
                {tab === 'config'
                  ? '修改后立即对所有新会话生效，无需重启（标注的除外）'
                  : tab === 'dashboard'
                    ? '会议规模、AI 成本、延迟与噪声告警的集中视图'
                    : tab === 'glossary'
                      ? '统一型号、项目名、缩写和发言人常用写法'
                    : '关键配置、删除、分享与人工修订操作全程留痕'}
              </p>
            </div>

            <div className="flex items-center gap-2" hidden={tab !== 'config'}>
              <button
                type="button"
                onClick={reset}
                className="btn"
                disabled={!hasChanges || saving}
              >
                <RotateCcw size={13} />
                还原
              </button>
              <button
                type="button"
                onClick={() => void save()}
                className="btn btn-primary"
                disabled={!hasChanges || saving}
              >
                {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                保存
              </button>
            </div>
          </div>

          <div
            className="mb-3 flex gap-1 rounded-lg p-1"
            style={{ background: 'var(--bg-subtle)' }}
          >
            {[
              { key: 'config' as const, label: '配置', icon: Settings2 },
              { key: 'dashboard' as const, label: '数据看板', icon: LayoutDashboard },
              { key: 'glossary' as const, label: '企业词表', icon: BookMarked },
              { key: 'audit' as const, label: '审计日志', icon: ScrollText },
            ].map((item) => {
              const Icon = item.icon
              return (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => setTab(item.key)}
                  className="flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] transition-colors"
                  style={{
                    background: tab === item.key ? 'var(--bg-surface)' : 'transparent',
                    color:
                      tab === item.key ? 'var(--text-primary)' : 'var(--text-tertiary)',
                    boxShadow: tab === item.key ? 'var(--shadow-panel)' : 'none',
                  }}
                >
                  <Icon size={13} /> {item.label}
                </button>
              )
            })}
          </div>

          {tab === 'dashboard' && <Dashboard />}
          {tab === 'glossary' && <GlossaryPanel />}
          {tab === 'audit' && <AuditLog />}

          {/* 模型状态 */}
          {tab === 'config' && health && <ModelStatusCard health={health} />}

          {/* 提示 */}
          {toast && <Toast toast={toast} onClose={() => setToast(null)} />}

          {/* 配置分组。仅在首次加载（还没有 schema）时占满屏，保存后的刷新不再闪 */}
          {tab === 'config' && isLoading && !schema && (
            <div className="flex justify-center py-16">
              <Loader2 size={18} className="animate-spin" style={{ color: 'var(--accent)' }} />
            </div>
          )}

          {tab === 'config' && error && (
            <div
              className="rounded-lg px-4 py-3 text-[12px]"
              style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}
            >
              加载配置失败：{error}
            </div>
          )}

          {tab === 'config' &&
            schema &&
            Object.entries(schema.groups).map(([group, fields]) => (
              <section key={group} className="panel mb-3 overflow-hidden">
                <div
                  className="px-4 py-3"
                  style={{ borderBottom: '1px solid var(--border-subtle)' }}
                >
                  <h2 className="text-[13px] font-medium">{group}</h2>
                </div>
                <div className="divide-y" style={{ borderColor: 'var(--border-subtle)' }}>
                  {fields.map((field) => (
                    <ConfigFieldInput
                      key={field.key}
                      field={field}
                      value={draft[field.key]}
                      error={errors[field.key]}
                      onChange={(v) => handleChange(field.key, v)}
                    />
                  ))}
                </div>
              </section>
            ))}
        </div>
      </main>
    </div>
  )
}

function ModelStatusCard({ health }: { health: ReturnType<typeof systemApi.health> extends Promise<infer T> ? T : never }) {
  const models = Object.values(health.models)
  const statusText: Record<string, string> = {
    ok: '就绪',
    degraded: '异常',
    loading: '加载中',
    disabled: '未启用',
  }
  const statusStyle =
    health.status === 'ok'
      ? { background: 'var(--live-soft)', color: 'var(--live-text)' }
      : health.status === 'degraded'
        ? { background: 'var(--danger-soft)', color: 'var(--danger-text)' }
        : health.status === 'disabled'
          ? { background: 'var(--bg-subtle)', color: 'var(--text-3)' }
          : { background: 'var(--warn-soft)', color: 'var(--warn-text)' }

  return (
    <div className="panel mb-3 px-4 py-3">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-[13px] font-medium">模型状态</h2>
        <span className="chip" style={statusStyle}>
          {statusText[health.status] ?? health.status}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2">
        {models.map((m) => (
          <div
            key={m.name}
            className="rounded-lg px-3 py-2"
            style={{ background: 'var(--bg-subtle)' }}
          >
            <div className="flex items-center gap-1.5">
              <span
                className="size-1.5 rounded-full"
                style={{
                  background: m.loaded ? 'var(--live)' : m.error ? 'var(--danger)' : 'var(--warn)',
                }}
              />
              <span className="text-[12px] font-medium">
                {m.name === 'streaming' ? '流式模型' : '离线模型'}
              </span>
            </div>
            <p className="mt-0.5 text-[11px] text-tertiary">
              {m.error
                ? m.error.slice(0, 60)
                : m.loaded
                  ? `${m.device} · 加载耗时 ${m.load_seconds.toFixed(1)}s`
                  : '未加载'}
            </p>
          </div>
        ))}
      </div>

      <p className="mt-2 text-[11px] text-tertiary">
        活跃会话 {health.active_sessions} 个 · 版本 {health.version}
      </p>
    </div>
  )
}

function Dashboard() {
  const [opsMessage, setOpsMessage] = useState<string | null>(null)
  const { data, isLoading, error } = useQuery({
    queryKey: ['admin-dashboard'],
    queryFn: () => adminApi.dashboard(),
    refetchInterval: 15_000,
  })

  if (isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 size={18} className="animate-spin" />
      </div>
    )
  }
  if (error || !data) {
    return (
      <div
        className="rounded-lg px-4 py-3 text-[12px]"
        style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}
      >
        看板加载失败：{error instanceof Error ? error.message : '未知错误'}
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Metric
          icon={<LayoutDashboard size={14} />}
          label="有效会议"
          value={data.meetings}
          hint={`归档 ${data.archived} · 回收站 ${data.trash}`}
        />
        <Metric
          icon={<Timer size={14} />}
          label="累计时长"
          value={`${data.duration_minutes} 分钟`}
          hint={`${data.segments.toLocaleString()} 个转写片段`}
        />
        <Metric
          icon={<Bot size={14} />}
          label="AI 调用"
          value={data.llm_calls}
          hint={`平均 ${data.llm_avg_ms.toFixed(0)} ms`}
        />
        <Metric
          icon={<ListChecks size={14} />}
          label="未完成待办"
          value={data.open_actions}
          hint="终稿与人工修订自动同步"
        />
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <section className="panel overflow-hidden">
          <div className="panel-header">
            <Bot size={14} /> <h2>AI 成本与用量</h2>
          </div>
          <div className="space-y-2 p-4">
            <StatLine label="Prompt 字符" value={data.llm_prompt_chars.toLocaleString()} />
            <StatLine label="Response 字符" value={data.llm_response_chars.toLocaleString()} />
            <StatLine label="平均调用耗时" value={`${data.llm_avg_ms.toFixed(0)} ms`} />
            <StatLine
              label="Token（输入/输出）"
              value={`${data.llm_prompt_tokens.toLocaleString()} / ${data.llm_completion_tokens.toLocaleString()}`}
            />
            <StatLine label="累计成本" value={`$${data.llm_cost_usd.toFixed(4)}`} />
            <p className="pt-1 text-[10px] text-tertiary">
              数值来自每次模型调用后的本地计量，不会把会议内容上传到额外分析服务。
            </p>
          </div>
        </section>
        <section className="panel overflow-hidden">
          <div className="panel-header">
            <Database size={14} /> <h2>转写质量与稳定性</h2>
          </div>
          <div className="space-y-2 p-4">
            <StatLine
              label="定稿 P95 中位数"
              value={
                data.latency_p95_median == null
                  ? '暂无样本'
                  : `${data.latency_p95_median.toFixed(0)} ms`
              }
            />
            <StatLine label="噪声告警" value={`${data.noise_warnings} 次`} />
            <StatLine label="转写片段" value={data.segments.toLocaleString()} />
            <StatLine
              label="后台任务"
              value={`等待 ${data.jobs.pending ?? 0} · 运行 ${data.jobs.running ?? 0} · 失败 ${data.jobs.failed ?? 0}`}
            />
            <p className="pt-1 text-[10px] text-tertiary">
              定稿延迟取最近会议指标的中位数；噪声告警可用来判断会议室环境是否需要改善。
            </p>
          </div>
        </section>
      </div>

      <section className="panel overflow-hidden">
        <div className="panel-header">
          <Power size={14} /> <h2>发布与排空</h2>
          <span className="ml-auto text-[10px] text-tertiary">
            {data.maintenance.draining ? '当前处于维护排空状态' : '当前可接收新会议'}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2 p-4">
          <button
            type="button"
            className="btn text-[12px]"
            onClick={async () => {
              try {
                await adminApi.reindexSearch()
                setOpsMessage('语义索引重建任务已提交')
              } catch (e) {
                setOpsMessage(e instanceof Error ? e.message : '任务提交失败')
              }
            }}
          >
            重建语义索引
          </button>
          <button
            type="button"
            className="btn text-[12px]"
            onClick={async () => {
              const result = await adminApi.drain()
              setOpsMessage(
                `已进入排空状态；当前仍有 ${result.active_sessions} 个会话，结束后即可发布。`,
              )
            }}
          >
            排空并准备发布
          </button>
          <button
            type="button"
            className="btn btn-primary text-[12px]"
            onClick={async () => {
              await adminApi.resume()
              setOpsMessage('已恢复正常接流')
            }}
          >
            恢复正常接流
          </button>
          {opsMessage && <span className="text-[11px] text-tertiary">{opsMessage}</span>}
        </div>
      </section>
    </div>
  )
}

function Metric({
  icon,
  label,
  value,
  hint,
}: {
  icon: React.ReactNode
  label: string
  value: string | number
  hint: string
}) {
  return (
    <div className="panel px-3 py-3">
      <div className="flex items-center gap-1.5 text-[11px] text-tertiary">
        {icon} {label}
      </div>
      <p className="mt-1 text-[20px] font-semibold tabular-nums">{value}</p>
      <p className="mt-0.5 truncate text-[10px] text-tertiary">{hint}</p>
    </div>
  )
}

function StatLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2 text-[12px]">
      <span className="text-secondary">{label}</span>
      <span className="font-mono tabular-nums" style={{ color: 'var(--text-primary)' }}>
        {value}
      </span>
    </div>
  )
}

function AuditLog() {
  const [page, setPage] = useState(0)
  const [action, setAction] = useState('')
  const [applied, setApplied] = useState('')
  const [verifyMessage, setVerifyMessage] = useState<string | null>(null)
  const pageSize = 50
  const { data, isLoading, error } = useQuery({
    queryKey: ['admin-audit', page, applied],
    queryFn: () => adminApi.audit(pageSize, page * pageSize, applied),
    refetchInterval: 20_000,
  })
  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize))

  const actionLabel: Record<string, string> = {
    meeting_metadata_update: '更新会议信息',
    segment_edit: '修订转写',
    minutes_edit: '修订纪要',
    share_create: '创建分享',
    meeting_trash: '移入回收站',
    meeting_bulk_trash: '批量移入回收站',
    meeting_restore: '恢复会议',
    meeting_bulk_restore: '批量恢复',
    meeting_purge: '彻底删除',
    meeting_bulk_purge: '批量彻底删除',
    meeting_bulk_rename: '批量重命名',
    meeting_bulk_update: '批量归档/标签',
  }

  return (
    <section className="panel overflow-hidden">
      <div className="panel-header">
        <ScrollText size={14} /> <h2>操作记录</h2>
        <input
          value={action}
          onChange={(e) => setAction(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              setPage(0)
              setApplied(action.trim())
            }
          }}
          placeholder="按动作筛选，如 share_create"
          className="ml-auto w-[220px] rounded px-2 py-1 text-[11px] outline-none"
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-subtle)',
          }}
        />
        <button
          type="button"
          onClick={() => {
            setPage(0)
            setApplied(action.trim())
          }}
          className="btn btn-ghost text-[11px]"
        >
          筛选
        </button>
        <button
          type="button"
          className="btn btn-ghost text-[11px]"
          onClick={async () => {
            const result = await adminApi.verifyAudit()
            setVerifyMessage(
              result.valid
                ? `哈希链校验通过，共 ${result.records} 条`
                : `审计链在第 ${result.broken_at} 条断裂`,
            )
          }}
        >
          <ShieldCheck size={12} /> 校验完整性
        </button>
        <button
          type="button"
          className="btn btn-ghost text-[11px]"
          onClick={async () => {
            const blob = await adminApi.exportAudit(applied)
            const url = URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url
            a.download = `audit-${Date.now()}.csv`
            a.click()
            URL.revokeObjectURL(url)
          }}
        >
          <Download size={12} /> 导出
        </button>
      </div>
      {verifyMessage && <p className="px-3 py-2 text-[11px] text-tertiary">{verifyMessage}</p>}
      {isLoading && (
        <div className="flex justify-center py-16">
          <Loader2 size={17} className="animate-spin" />
        </div>
      )}
      {error && (
        <p className="p-4 text-[12px]" style={{ color: 'var(--danger-text)' }}>
          {error instanceof Error ? error.message : '审计日志加载失败'}
        </p>
      )}
      {!isLoading && !error && (data?.items.length ?? 0) === 0 && (
        <p className="py-16 text-center text-[12px] text-tertiary">没有匹配的操作记录</p>
      )}
      {!isLoading && (data?.items.length ?? 0) > 0 && (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[11px]">
              <thead style={{ background: 'var(--bg-subtle)', color: 'var(--text-tertiary)' }}>
                <tr>
                  <th className="px-3 py-2 font-medium">时间</th>
                  <th className="px-3 py-2 font-medium">用户</th>
                  <th className="px-3 py-2 font-medium">动作</th>
                  <th className="px-3 py-2 font-medium">对象</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((row) => (
                  <tr key={row.id} className="border-t" style={{ borderColor: 'var(--border-subtle)' }}>
                    <td className="whitespace-nowrap px-3 py-2 text-tertiary">
                      {new Date(row.created_at).toLocaleString('zh-CN')}
                    </td>
                    <td className="px-3 py-2">{row.username}</td>
                    <td className="px-3 py-2">
                      {actionLabel[row.action] ?? row.action}
                    </td>
                    <td className="max-w-[260px] truncate px-3 py-2 font-mono text-[10px] text-tertiary">
                      {row.target ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-end gap-2 px-3 py-2 text-[11px] text-tertiary">
            <span>共 {data?.total ?? 0} 条</span>
            <button
              type="button"
              className="btn btn-ghost text-[11px]"
              disabled={page === 0}
              onClick={() => setPage((value) => Math.max(0, value - 1))}
            >
              上一页
            </button>
            <span>
              第 {page + 1} / {totalPages} 页
            </span>
            <button
              type="button"
              className="btn btn-ghost text-[11px]"
              disabled={page + 1 >= totalPages}
              onClick={() => setPage((value) => value + 1)}
            >
              下一页
            </button>
          </div>
        </>
      )}
    </section>
  )
}

function Toast({
  toast,
  onClose,
}: {
  toast: { kind: 'ok' | 'warn' | 'err'; text: string }
  onClose: () => void
}) {
  const palette = {
    ok: { bg: 'var(--live-soft)', fg: 'var(--live-text)' },
    warn: { bg: 'var(--warn-soft)', fg: 'var(--warn-text)' },
    err: { bg: 'var(--danger-soft)', fg: 'var(--danger-text)' },
  }[toast.kind]

  const Icon = toast.kind === 'ok' ? CheckCircle2 : AlertTriangle

  return (
    <button
      type="button"
      onClick={onClose}
      className="mb-3 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[12px]"
      style={{ background: palette.bg, color: palette.fg }}
    >
      <Icon size={14} className="shrink-0" />
      <span className="flex-1">{toast.text}</span>
      <span className="text-[11px] opacity-70">点击关闭</span>
    </button>
  )
}
