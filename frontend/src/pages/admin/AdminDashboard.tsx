import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Bot,
  Database,
  LayoutDashboard,
  ListChecks,
  Loader2,
  Power,
  Timer,
} from 'lucide-react'

import { adminApi } from '../../api/client'

export function AdminDashboard() {
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
