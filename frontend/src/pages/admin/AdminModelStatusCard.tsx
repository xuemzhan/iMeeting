import type { Health } from '../../api/types'

export function AdminModelStatusCard({ health }: { health: Health }) {
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
        {models.map((model) => (
          <div
            key={model.name}
            className="rounded-lg px-3 py-2"
            style={{ background: 'var(--bg-subtle)' }}
          >
            <div className="flex items-center gap-1.5">
              <span
                className="size-1.5 rounded-full"
                style={{
                  background: model.loaded
                    ? 'var(--live)'
                    : model.error
                      ? 'var(--danger)'
                      : 'var(--warn)',
                }}
              />
              <span className="text-[12px] font-medium">
                {model.name === 'streaming' ? '流式模型' : '离线模型'}
              </span>
            </div>
            <p className="mt-0.5 text-[11px] text-tertiary">
              {model.error
                ? model.error.slice(0, 60)
                : model.loaded
                  ? `${model.device} · 加载耗时 ${model.load_seconds.toFixed(1)}s`
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
