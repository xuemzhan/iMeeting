import { useState } from 'react'
import { Loader2, ShieldCheck, X } from 'lucide-react'
import { meetingApi } from '../api/client'
import type { Meeting } from '../api/types'

export function CompliancePanel({
  meeting,
  onSaved,
  onClose,
}: {
  meeting: Meeting
  onSaved: () => void
  onClose: () => void
}) {
  const [retention, setRetention] = useState(meeting.retention_days)
  const [legalHold, setLegalHold] = useState(meeting.legal_hold)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const save = async () => {
    setBusy(true)
    setMessage(null)
    try {
      await meetingApi.updateRetention(meeting.id, {
        retention_days: retention,
        legal_hold: legalHold,
      })
      setMessage('保留策略已保存')
      onSaved()
    } catch (e) {
      setMessage(e instanceof Error ? e.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  const redact = async (dryRun: boolean) => {
    setBusy(true)
    setMessage(null)
    try {
      const result = await meetingApi.redact(meeting.id, dryRun)
      setMessage(
        dryRun
          ? `预览发现 ${result.replacements} 处敏感信息`
          : `已脱敏 ${result.replacements} 处敏感信息`,
      )
      if (!dryRun) onSaved()
    } catch (e) {
      setMessage(e instanceof Error ? e.message : '脱敏失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="panel w-full max-w-[520px] p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-[14px] font-medium">
            <ShieldCheck size={15} /> 数据合规
          </h2>
          <button type="button" onClick={onClose} className="p-1 text-tertiary" aria-label="关闭">
            <X size={15} />
          </button>
        </div>
        <div className="space-y-3">
          <label className="block text-[11px] text-tertiary">
            会议保留天数（0 使用全局策略）
            <input
              type="number"
              min={0}
              max={3650}
              value={retention}
              onChange={(e) => setRetention(Number(e.target.value))}
              className="mt-1 w-full rounded px-2 py-1.5 text-[12px] outline-none"
              style={{
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-subtle)',
              }}
            />
          </label>
          <label className="flex items-center gap-2 text-[12px] text-secondary">
            <input
              type="checkbox"
              checked={legalHold}
              onChange={(e) => setLegalHold(e.target.checked)}
              style={{ accentColor: 'var(--accent)' }}
            />
            法律保留：禁止自动清理这场会议
          </label>
          <div className="rounded-lg p-2.5 text-[11px] text-tertiary" style={{ background: 'var(--bg-subtle)' }}>
            脱敏会处理转写、纪要和待办中的邮箱、身份证、手机号、银行卡和 IP。
            音频文件不会自动抹除；如需处理音频，请结合保留策略删除原文件。
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => void redact(true)} disabled={busy} className="btn text-[12px]">
              预览脱敏
            </button>
            <button type="button" onClick={() => void redact(false)} disabled={busy} className="btn text-[12px]">
              执行脱敏
            </button>
            {meeting.redacted_at && (
              <span className="self-center text-[10px] text-tertiary">
                上次脱敏 {new Date(meeting.redacted_at).toLocaleString('zh-CN')}
              </span>
            )}
          </div>
          {message && <p className="text-[11px] text-secondary">{message}</p>}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="btn text-[12px]">关闭</button>
          <button type="button" onClick={() => void save()} disabled={busy} className="btn btn-primary text-[12px]">
            {busy && <Loader2 size={12} className="animate-spin" />} 保存策略
          </button>
        </div>
      </div>
    </div>
  )
}
