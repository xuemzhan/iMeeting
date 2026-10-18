import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink, Mic, Pencil, RefreshCw, Trash2 } from 'lucide-react'

import type { Voiceprint } from '../api/types'
import { voiceprintApi } from '../api/client'
import type { useSettingsVoiceprints } from '../hooks/useSettingsVoiceprints'
import { VoiceprintEnroll } from './VoiceprintEnroll'
import { SettingsSection } from './SettingsPanelParts'

type Controller = ReturnType<typeof useSettingsVoiceprints>

export function SettingsVoiceprintSection({
  deviceId,
  voiceprints,
  onClose,
}: {
  deviceId: string | undefined
  voiceprints: Controller
  onClose: () => void
}) {
  return (
    <SettingsSection title="声纹">
      <p className="mb-2 text-[11px] leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
        录入后，会议转写会直接显示说话人姓名，而不是「说话人 1」。
      </p>

      <VoiceprintEnroll deviceId={deviceId} onDone={voiceprints.enrollDone} />

      {voiceprints.error && (
        <div
          className="mt-2 rounded-lg px-2.5 py-1.5 text-[11px]"
          style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}
        >
          {voiceprints.error}
        </div>
      )}
      {voiceprints.ok && !voiceprints.error && (
        <div
          className="mt-2 rounded-lg px-2.5 py-1.5 text-[11px]"
          style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
        >
          {voiceprints.ok}
        </div>
      )}

      <div className="mt-4 mb-1.5 flex items-center justify-between">
        <span className="text-[11px]" style={{ color: 'var(--text-secondary)' }}>
          已录入 {voiceprints.prints.length} 个
        </span>
        <span className="flex items-center gap-1">
          <button
            type="button"
            onClick={voiceprints.load}
            className="btn btn-ghost"
            style={{ height: 22, padding: '0 8px', fontSize: 11 }}
          >
            <RefreshCw size={11} />
            刷新
          </button>
          <Link
            to="/voiceprints"
            onClick={onClose}
            className="btn btn-ghost no-underline"
            style={{ height: 22, padding: '0 8px', fontSize: 11 }}
          >
            管理页
            <ExternalLink size={10} />
          </Link>
        </span>
      </div>

      {voiceprints.prints.length === 0 ? (
        <p className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
          还没有任何声纹
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {voiceprints.prints.map((print) => (
            <PrintRow
              key={print.id}
              print={print}
              onRenamed={voiceprints.renamed}
              onError={voiceprints.setError}
              onDelete={() => void voiceprints.remove(print.id, print.name)}
            />
          ))}
        </ul>
      )}

      {voiceprints.prints.some((print) => print.provisional) && (
        <p className="mt-2 text-[11px] leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
          标「待命名」的是开会时自动发现的说话人。改成真实姓名后，
          以后所有会议都会直接认出这个人。
        </p>
      )}
    </SettingsSection>
  )
}

/**
 * 一条声纹。支持就地改名——「待命名」的自动发现项改名后即转正，
 * 之后所有会议都会用这个名字。
 */
function PrintRow({
  print,
  onRenamed,
  onError,
  onDelete,
}: {
  print: Voiceprint
  onRenamed: (message: string) => void
  onError: (message: string) => void
  onDelete: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(print.name)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    const name = draft.trim()
    if (!name || name === print.name) {
      setEditing(false)
      setDraft(print.name)
      return
    }
    setBusy(true)
    try {
      const updated = await voiceprintApi.rename(print.id, name)
      onRenamed(`已改名为「${updated.name}」`)
      setEditing(false)
    } catch (err) {
      onError(err instanceof Error ? err.message : '改名失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <li
      className="flex items-center justify-between gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px]"
      style={{ background: 'var(--bg-subtle)' }}
    >
      {editing ? (
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void submit()
            if (e.key === 'Escape') {
              setEditing(false)
              setDraft(print.name)
            }
          }}
          onBlur={() => void submit()}
          maxLength={32}
          disabled={busy}
          className="min-w-0 flex-1 rounded px-1.5 py-0.5 text-[12px] outline-none"
          style={{
            background: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            border: '1px solid var(--accent)',
          }}
        />
      ) : (
        <span className="flex min-w-0 items-center gap-1.5" style={{ color: 'var(--text-primary)' }}>
          <Mic
            size={12}
            style={{
              color: print.provisional ? 'var(--text-tertiary)' : 'var(--accent)',
            }}
          />
          <span className="truncate">{print.name}</span>
          {print.provisional ? (
            <span
              className="shrink-0 rounded-full px-1.5 py-0.5 text-[10px]"
              style={{ background: 'var(--warn-soft)', color: 'var(--warn-text)' }}
            >
              待命名
            </span>
          ) : (
            <span className="shrink-0" style={{ color: 'var(--text-tertiary)' }}>
              {(print.sample_ms / 1000).toFixed(1)}s
            </span>
          )}
        </span>
      )}

      <span className="flex shrink-0 items-center">
        {!editing && (
          <button
            type="button"
            onClick={() => {
              setDraft(print.name)
              setEditing(true)
            }}
            className="btn btn-ghost"
            style={{ height: 24, width: 24, padding: 0 }}
            title="改名"
            aria-label={`改名 ${print.name}`}
          >
            <Pencil size={12} />
          </button>
        )}
        <button
          type="button"
          onClick={onDelete}
          className="btn btn-ghost"
          style={{ height: 24, width: 24, padding: 0 }}
          title="删除"
          aria-label={`删除 ${print.name}`}
        >
          <Trash2 size={12} />
        </button>
      </span>
    </li>
  )
}
