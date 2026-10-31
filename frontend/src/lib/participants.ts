/** 参会人面板的纯函数：属性摘要、状态文案与排序规则。
 *
 * 抽出来是为了能在 node 环境的 vitest 里直接测（前端测试环境没有 DOM）。
 */

import type { MeetingParticipantItem, ParticipantState, PersonAttributeItem } from '../api/types'

export const STATE_LABELS: Record<ParticipantState, string> = {
  pending: '待确认',
  confirmed: '已确认',
  rejected: '已删除',
}

/** 面板里只显示待确认与已确认；已删除的记录保留在库里但不再出现。 */
export function visibleParticipants(
  items: MeetingParticipantItem[] | null | undefined,
  includeLowConfidence = false,
): MeetingParticipantItem[] {
  const list = (items ?? []).filter((item) => item.state !== 'rejected' && (includeLowConfidence || !item.hidden_by_confidence))
  return [...list].sort(
    (a, b) =>
      a.importance - b.importance ||
      Number(b.state === 'pending') - Number(a.state === 'pending') ||
      a.speaker.localeCompare(b.speaker),
  )
}

/** 把属性拼成「总体院 · 主任」这样的一行摘要；没有属性返回空串。 */
export function summarizeAttributes(attributes: PersonAttributeItem[] | null | undefined): string {
  const unit = attributes?.find((item) => item.category === 'org' && item.key === 'unit')?.value
  const title =
    attributes?.find((item) => item.category === 'role' && item.key === 'title')?.value ??
    attributes?.find((item) => item.category === 'org' && item.key === 'department')?.value
  return [unit, title].filter(Boolean).join(' · ')
}

export function pendingCount(items: MeetingParticipantItem[] | null | undefined): number {
  return (items ?? []).filter((item) => item.state === 'pending').length
}

/** 冲突等级 → 中文提示（弹窗与列表共用，保证两边说法一致）。 */
export function conflictLevelText(level: 'none' | 'medium' | 'high'): string {
  if (level === 'high') return '高冲突：更像别人'
  if (level === 'medium') return '建议核对'
  return '一致'
}
