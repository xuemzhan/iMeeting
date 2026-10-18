import { useEffect, useRef, useState } from 'react'
import { Mic, Loader2, RotateCcw, Check, ChevronRight } from 'lucide-react'
import { voiceprintApi } from '../api/client'
import { recordWav } from '../audio/recordWav'
import { ENROLL_TEXTS, RECORD_SECONDS } from '../lib/voiceprintTexts'

type Step = 'name' | 'read' | 'recording' | 'uploading'

/**
 * 声纹录入引导。
 *
 * 分三步走而不是「填个名字就开录」：声纹质量高度依赖**说什么**——
 * 只念「你好」这类短句，音素覆盖太少，遇到长句就匹配不上。
 * 所以这里给出固定朗读文本，并让用户先看到文本再开始计时。
 */
export function VoiceprintEnroll({
  deviceId,
  onDone,
}: {
  deviceId?: string
  onDone: () => void
}) {
  const [step, setStep] = useState<Step>('name')
  const [name, setName] = useState('')
  const [textIdx, setTextIdx] = useState(() => Math.floor(Math.random() * ENROLL_TEXTS.length))
  const [remain, setRemain] = useState(RECORD_SECONDS)
  const [level, setLevel] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)
  /**
   * 组件是否已卸载。
   *
   * 必须在 effect 体内先复位为 false 再返回清理函数：React StrictMode（开发模式）
   * 挂载时会「setup → cleanup → setup」跑两遍，只写清理函数的话 cleanup 会立刻把
   * 标志置为 true 且再无机会复位，导致录音正常结束后被误判成「已卸载」而提前
   * return——表现为念完话后卡在录音步、既不上传也不报错。
   */
  const cancelled = useRef(false)

  useEffect(() => {
    cancelled.current = false
    return () => {
      cancelled.current = true
    }
  }, [])

  const text = ENROLL_TEXTS[textIdx]

  const startRecord = async () => {
    setError(null)
    setOk(null)
    setStep('recording')
    setRemain(RECORD_SECONDS)
    setLevel(0)
    try {
      const { blob, ms } = await recordWav(RECORD_SECONDS, {
        deviceId,
        onTick: (r, lv) => {
          setRemain(Math.ceil(r))
          if (lv > 0) setLevel(lv)
        },
      })
      if (cancelled.current) return
      setStep('uploading')
      const created = await voiceprintApi.create(name.trim(), blob)
      setOk(`已录入「${created.name}」（${(ms / 1000).toFixed(1)} 秒音频）`)
      setName('')
      setStep('name')
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : '录入失败，请重试')
      setStep('read')
    }
  }

  return (
    <div
      className="rounded-xl p-3"
      style={{ background: 'var(--bg-subtle)', border: '1px solid var(--border-subtle)' }}
    >
      <Steps current={step} />

      {step === 'name' && (
        <div className="mt-3">
          <label className="mb-1.5 block text-[11px]" style={{ color: 'var(--text-secondary)' }}>
            第 1 步 · 这个人叫什么？
          </label>
          <div className="flex gap-1.5">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && name.trim()) setStep('read')
              }}
              placeholder="例如：张工"
              maxLength={32}
              className="flex-1 rounded-lg px-2.5 py-1.5 text-[12px] outline-none"
              style={{
                background: 'var(--bg-surface)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border-default)',
              }}
            />
            <button
              type="button"
              disabled={!name.trim()}
              onClick={() => setStep('read')}
              className="btn btn-primary"
              style={{ height: 30, padding: '0 12px', fontSize: 12 }}
            >
              下一步
              <ChevronRight size={12} />
            </button>
          </div>
        </div>
      )}

      {step === 'read' && (
        <div className="mt-3">
          <label className="mb-1.5 block text-[11px]" style={{ color: 'var(--text-secondary)' }}>
            第 2 步 · 请用正常语速朗读下面这段话
          </label>
          <div
            className="rounded-lg px-3 py-2.5 text-[13px] leading-relaxed"
            style={{
              background: 'var(--bg-surface)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-default)',
            }}
          >
            {text}
          </div>
          <p className="mt-1.5 text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
            共 {RECORD_SECONDS} 秒。这段话覆盖了普通话的常见音素，念完即可，不必赶时间。
          </p>
          <div className="mt-2 flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => void startRecord()}
              className="btn btn-primary"
              style={{ height: 30, padding: '0 12px', fontSize: 12 }}
            >
              <Mic size={12} />
              开始录音
            </button>
            <button
              type="button"
              onClick={() => setTextIdx((i) => (i + 1) % ENROLL_TEXTS.length)}
              className="btn btn-ghost"
              style={{ height: 30, padding: '0 10px', fontSize: 12 }}
            >
              <RotateCcw size={12} />
              换一段
            </button>
            <button
              type="button"
              onClick={() => setStep('name')}
              className="btn btn-ghost"
              style={{ height: 30, padding: '0 10px', fontSize: 12 }}
            >
              上一步
            </button>
          </div>
        </div>
      )}

      {(step === 'recording' || step === 'uploading') && (
        <div className="mt-3">
          <label className="mb-1.5 block text-[11px]" style={{ color: 'var(--text-secondary)' }}>
            {step === 'recording' ? `第 3 步 · 正在录音（还剩 ${remain} 秒）` : '正在提取声纹…'}
          </label>
          <div
            className="rounded-lg px-3 py-2.5 text-[13px] leading-relaxed"
            style={{
              background: 'var(--bg-surface)',
              color: 'var(--text-primary)',
              border: `1px solid ${step === 'recording' ? 'var(--accent)' : 'var(--border-default)'}`,
            }}
          >
            {text}
          </div>

          {step === 'recording' ? (
            <>
              <div
                className="mt-2 h-1.5 overflow-hidden rounded-full"
                style={{ background: 'var(--border-subtle)' }}
              >
                <div
                  className="h-full transition-[width] duration-100"
                  style={{
                    width: `${Math.min(100, level * 260)}%`,
                    background: 'var(--accent)',
                  }}
                />
              </div>
              <p className="mt-1.5 flex items-center gap-1 text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
                <span className="size-1.5 animate-pulse rounded-full" style={{ background: 'var(--accent)' }} />
                请照着上面这段话念，声音太小会录不进去
              </p>
            </>
          ) : (
            <p className="mt-2 flex items-center gap-1.5 text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
              <Loader2 size={12} className="animate-spin" />
              正在计算声纹特征…
            </p>
          )}
        </div>
      )}

      {error && (
        <div
          className="mt-2 rounded-lg px-2.5 py-1.5 text-[11px]"
          style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}
        >
          {error}
        </div>
      )}
      {ok && (
        <div
          className="mt-2 flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px]"
          style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
        >
          <Check size={12} />
          {ok}
        </div>
      )}
    </div>
  )
}

function Steps({ current }: { current: Step }) {
  const items: { key: Step[]; label: string }[] = [
    { key: ['name'], label: '填姓名' },
    { key: ['read'], label: '朗读' },
    { key: ['recording', 'uploading'], label: '录音' },
  ]
  return (
    <div className="flex items-center gap-1.5">
      {items.map((it, i) => {
        const active = it.key.includes(current)
        const done = items.findIndex((x) => x.key.includes(current)) > i
        return (
          <div key={it.label} className="flex items-center gap-1.5">
            <span
              className="flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px]"
              style={{
                background: active || done ? 'var(--accent-soft)' : 'transparent',
                color: active ? 'var(--accent-text)' : done ? 'var(--text-secondary)' : 'var(--text-tertiary)',
                border: `1px solid ${active ? 'var(--accent)' : 'var(--border-subtle)'}`,
              }}
            >
              {done ? <Check size={9} /> : <span>{i + 1}</span>}
              {it.label}
            </span>
            {i < items.length - 1 && (
              <span style={{ color: 'var(--text-tertiary)' }} className="text-[10px]">
                ·
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}
