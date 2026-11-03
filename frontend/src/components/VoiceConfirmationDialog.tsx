import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { voiceprintApi } from '../api/client'
import { VOICE_CONFIRMATION_EVENT, type VoiceChoice, type VoiceConfirmationEvent } from '../lib/voiceConfirmation'

/** Every transcript rename/confirmation shares one explicit, cancellable decision. */
export function VoiceConfirmationDialog() {
  const [queue, setQueue] = useState<VoiceConfirmationEvent[]>([])
  const pending = useRef<VoiceConfirmationEvent[]>([])
  const dialog = useRef<HTMLDivElement>(null)
  const current = queue[0]
  // VoiceConfirmation 没有 id：用「会议 + 声纹 + 片段」作为身份戳
  const currentId = current
    ? `${current.confirmation.meeting_id}:${current.confirmation.voiceprint_id}:${current.confirmation.segment_id ?? ''}`
    : ''

  // 声纹清单与管理页共享同一份缓存
  const printsQuery = useQuery({
    queryKey: ['voiceprints'],
    queryFn: () => voiceprintApi.list(),
    enabled: Boolean(current),
  })
  const prints = printsQuery.data ?? []

  // 「随当前确认项重置」的状态一律带身份戳：换一条确认项自然回到默认值。
  // 不用 key 重挂载——本组件在 App 层无 props，重挂载会让 cleanup 把排队中的
  // 请求全部 resolve 成 cancel。
  const [pick, setPick] = useState<{ id: string; target: string } | null>(null)
  const [slotPick, setSlotPick] = useState<{ id: string; slot: string } | null>(null)
  const [targetSlotPick, setTargetSlotPick] = useState<{ id: string; slot: string } | null>(null)

  const defaultTarget = String(
    current?.confirmation.assessment.other?.voiceprint_id ?? '',
  )
  const target = pick && pick.id === currentId ? pick.target : defaultTarget
  const setTarget = (value: string) => setPick({ id: currentId, target: value })
  const slot = slotPick && slotPick.id === currentId ? slotPick.slot : ''
  const setSlot = (value: string) => setSlotPick({ id: currentId, slot: value })
  const targetSlotKey = `${currentId}:${target}`
  const targetSlot = targetSlotPick?.id === targetSlotKey ? targetSlotPick.slot : ''
  const setTargetSlot = (value: string) =>
    setTargetSlotPick({ id: targetSlotKey, slot: value })

  // 目标声纹的模板：换 target 自动换 query key，不再手工清空三个 state
  const templatesQuery = useQuery({
    queryKey: ['voiceprint-templates', target],
    queryFn: () => voiceprintApi.templates(Number(target)),
    enabled: Boolean(current && target),
  })
  const targetTemplates = (templatesQuery.data?.templates ?? []).filter(
    (sample) => sample.slot >= 0 && sample.superseded_by == null,
  )
  const targetReady = templatesQuery.isSuccess

  useEffect(() => {
    const receive = (event: Event) => {
      event.preventDefault()
      const request = (event as CustomEvent<VoiceConfirmationEvent>).detail
      pending.current.push(request)
      setQueue([...pending.current])
    }
    window.addEventListener(VOICE_CONFIRMATION_EVENT, receive)
    return () => {
      window.removeEventListener(VOICE_CONFIRMATION_EVENT, receive)
      pending.current.forEach((request) => request.resolve({ decision: 'cancel' }))
      pending.current = []
    }
  }, [])

  // 打开一条新的确认时把焦点移进对话框（纯 DOM 副作用，不碰 state）
  useEffect(() => {
    if (current) dialog.current?.focus()
  }, [current])

  const finish = (choice: VoiceChoice) => {
    const request = pending.current.shift()
    request?.resolve(choice)
    setQueue([...pending.current])
  }
  if (!current) return null
  const { confirmation: item } = current
  const assessment = item.assessment
  const score = (value: number | null) => value == null ? '无历史样本' : `${Math.round(value * 100)}%`
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4">
      <div ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="voice-confirmation-title"
        className="panel w-full max-w-md space-y-3 p-5 outline-none max-md:max-h-[92dvh] max-md:overflow-y-auto max-md:p-4"
        onKeyDown={(event) => {
          if (event.key === 'Escape') { event.preventDefault(); finish({ decision: 'cancel' }) }
          if (event.key === 'Tab') {
            const controls = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), select, a[href]')
            if (!controls?.length) return
            const first = controls[0], last = controls[controls.length - 1]
            if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last.focus() }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
          }
        }}>
        <h2 id="voice-confirmation-title" className="font-medium">请核对说话人：{item.name}</h2>
        <p className="text-sm text-secondary">
          与本人历史声纹：{score(assessment.self_score)}。
          {assessment.other && <>与 {assessment.other.name}：{score(assessment.other.score)}。</>}
        </p>
        <p className="text-sm text-secondary">确认后才会更新声纹与转写；取消会保留原来的说话人；只想改本场显示名而不动声纹库时，用「只改本场显示名」。</p>
        {item.segment_id && <a className="text-sm underline" target="_blank" rel="noreferrer"
          href={`/history/${item.meeting_id}?t=${(item.start_ms ?? 0) / 1000}#${encodeURIComponent(item.segment_id)}`}>查看来源片段{item.start_ms != null ? `（${Math.floor(item.start_ms / 1000)}秒）` : ''}</a>}
        {item.requires_slot && <label className="block text-sm">已保存五条声纹，请选择替换项
          <select aria-label="替换模板" className="mt-1 block w-full rounded border p-2" value={slot} onChange={(e) => setSlot(e.target.value)}>
            <option value="">选择要替换的样本…</option>
            {item.templates.map((sample) => <option key={sample.slot} value={sample.slot}>样本 {sample.slot + 1} · {Object.values(sample.scenario ?? {}).filter(Boolean).join(' / ') || '场景未知'} · 质量 {Math.round(sample.quality * 100)}%</option>)}
          </select>
        </label>}
        <label className="block text-sm">如果实际是另一位参会人
          <select aria-label="正确的说话人" className="mt-1 block w-full rounded border p-2" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">选择说话人…</option>
            {prints.filter((print) => print.id !== item.voiceprint_id && !print.provisional).map((print) => <option key={print.id} value={print.id}>{print.name}</option>)}
          </select>
        </label>
        {target && targetTemplates.length >= 5 && <label className="block text-sm">目标说话人的声纹已满，请选择替换项
          <select aria-label="替换目标样本" className="mt-1 block w-full rounded border p-2" value={targetSlot} onChange={(e) => setTargetSlot(e.target.value)}>
            <option value="">选择要替换的样本…</option>
            {targetTemplates.map((sample) => <option key={sample.slot} value={sample.slot}>样本 {sample.slot + 1} · 质量 {Math.round(sample.quality * 100)}%</option>)}
          </select>
        </label>}
        {target && !targetReady && <p className="text-sm text-secondary">正在读取目标声纹；读取成功后可转交。如持续无响应，请取消后重试。</p>}
        <div className="flex flex-wrap justify-end gap-2">
          <button className="btn btn-ghost" onClick={() => finish({ decision: 'cancel' })}>取消</button>
          <button className="btn btn-ghost" disabled={!target || !targetReady || (targetTemplates.length >= 5 && targetSlot === '')} onClick={() => finish({ decision: 'reassign', target_voiceprint_id: Number(target), ...(targetSlot !== '' ? { slot: Number(targetSlot) } : {}) })}>改为选中的人</button>
          <button className="btn btn-primary" disabled={item.requires_slot && slot === ''} onClick={() => finish({ decision: 'confirm_self', ...(slot !== '' ? { slot: Number(slot) } : {}) })}>确认为本人</button>
          <button className="btn btn-ghost" onClick={() => finish({ decision: 'label_only' })}>只改本场显示名</button>
        </div>
      </div>
    </div>
  )
}
