import { useCallback, useState } from 'react'
import { ChevronDown, ChevronRight, Layers } from 'lucide-react'

import { voiceprintApi } from '../api/client'
import type { VoiceprintTemplateItem, VoiceprintTemplateList } from '../api/types'

const SCENARIO_LABELS: Record<string, string> = {
  local_mic: '本机麦',
  room_array: '会议麦',
  phone: '手机',
  headset: '耳机',
  online: '线上',
  near: '近场',
  mid: '中距',
  far: '远场',
  quiet: '安静',
  reverb: '混响',
  noisy: '嘈杂',
  normal: '正常',
  fast: '语速快',
  low: '低沉',
  engaged: '投入',
}

function scenarioText(item: VoiceprintTemplateItem): string {
  return [item.scenario.device, item.scenario.distance, item.scenario.noise, item.scenario.voice]
    .filter(Boolean)
    .map((value) => SCENARIO_LABELS[value] ?? value)
    .join(' · ')
}

/**
 * 某位人员的声纹模板（REQ-002，每人 ≤5 条）。
 *
 * 默认折叠，点开才请求：声纹页可能有几十个人，一次性拉全部模板没有必要。
 */
export function VoiceprintTemplateSummary({ voiceprintId }: { voiceprintId: number }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<VoiceprintTemplateList | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await voiceprintApi.templates(voiceprintId)
      setData(result)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取模板失败')
    } finally {
      setLoading(false)
    }
  }, [voiceprintId])

  const active = (data?.templates ?? []).filter((item) => item.active)
  const displayed = (data?.templates ?? []).filter((item) => item.superseded_by == null)
  const toggle = async (templateId: number, active: boolean) => {
    try { await voiceprintApi.patchTemplate(voiceprintId, templateId, { disabled: active }); await load() }
    catch (err) { setError(err instanceof Error ? err.message : '修改模板状态失败') }
  }
  const togglePinned = async (templateId: number, pinned: boolean) => {
    try { await voiceprintApi.patchTemplate(voiceprintId, templateId, { pinned }); await load() }
    catch (err) { setError(err instanceof Error ? err.message : '设置主模板失败') }
  }
  const primary = displayed.find((item) => item.active && item.primary)
  const mergeIntoPrimary = async (sourceId: number, targetId: number) => {
    try {
      await voiceprintApi.mergeTemplates(voiceprintId, {
        source_template_id: sourceId,
        target_template_id: targetId,
      })
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : '合并模板失败')
    }
  }

  return (
    <li className="px-3 pb-2" style={{ background: 'transparent' }}>
      <button
        type="button"
        onClick={() => {
          const next = !open
          setOpen(next)
          if (next && data == null) void load()
        }}
        className="flex items-center gap-1 text-[11px]"
        style={{ color: 'var(--text-tertiary)' }}
        aria-expanded={open}
      >
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        <Layers size={11} />
        声纹模板{data ? `（${active.length}/${data.max_templates}）` : ''}
        {data?.person ? ` · ${data.person.display_name}` : ''}
      </button>

      {open && (
        <div className="mt-1 space-y-1">
          {loading && (
            <p className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
              读取中…
            </p>
          )}
          {error && (
            <p className="text-[10px]" style={{ color: 'var(--danger-text)' }}>
              {error}
            </p>
          )}
          {data && active.length === 0 && (
            <p className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
              还没有模板：录入声纹或开会后确认一次即可生成。
            </p>
          )}
          {displayed.map((item) => (
            <div
              key={item.template_id}
              className="flex flex-wrap items-center gap-x-2 rounded px-2 py-1 text-[10px]"
              style={{ background: 'var(--bg-subtle)', color: 'var(--text-secondary)' }}
            >
              <span>样本 {item.slot >= 0 ? item.slot + 1 : '（已停用）'}</span>
              <button type="button" className="underline" onClick={() => void toggle(item.template_id, item.active)}>{item.active ? '停用' : '重新启用'}</button>
              {item.active && (
                <button
                  type="button"
                  className="underline"
                  onClick={() => void togglePinned(item.template_id, !item.pinned)}
                >
                  {item.pinned ? '取消主模板' : '设为主模板'}
                </button>
              )}
              {item.active && primary && primary.template_id !== item.template_id && (
                <button
                  type="button"
                  className="underline"
                  onClick={() => void mergeIntoPrimary(item.template_id, primary.template_id)}
                >
                  合并到主模板
                </button>
              )}
              {item.pinned && (
                <span className="chip" style={{ height: 16 }}>
                  主模板
                </span>
              )}
              {!item.pinned && item.primary && (
                <span className="chip" style={{ height: 16 }}>
                  推荐
                </span>
              )}
              <span>{scenarioText(item) || '场景未知'}</span>
              <span style={{ color: 'var(--text-tertiary)' }}>
                质量 {Math.round(item.quality * 100)}%
              </span>
              {item.sample_ms > 0 && (
                <span style={{ color: 'var(--text-tertiary)' }}>
                  {(item.sample_ms / 1000).toFixed(1)}s
                </span>
              )}
              {!item.confirmed && (
                <span className="chip" style={{ height: 16 }}>
                  待确认
                </span>
              )}
              {item.source_meeting_id != null && (
                <span className="ml-auto" style={{ color: 'var(--text-tertiary)' }}>
                  来自会议 #{item.source_meeting_id}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </li>
  )
}
