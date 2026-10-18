import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, Loader2, ScrollText, ShieldCheck } from 'lucide-react'

import { adminApi } from '../../api/client'

export function AdminAuditLog() {
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
            const anchor = document.createElement('a')
            anchor.href = url
            anchor.download = `audit-${Date.now()}.csv`
            anchor.click()
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
                  <tr
                    key={row.id}
                    className="border-t"
                    style={{ borderColor: 'var(--border-subtle)' }}
                  >
                    <td className="whitespace-nowrap px-3 py-2 text-tertiary">
                      {new Date(row.created_at).toLocaleString('zh-CN')}
                    </td>
                    <td className="px-3 py-2">{row.username}</td>
                    <td className="px-3 py-2">{actionLabel[row.action] ?? row.action}</td>
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
