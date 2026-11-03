/** 声纹库管理：列表、建议合并、改名、停用、设备列表。
 *
 * 列表与建议都走 `useQuery`：`load()` 退化为「失效重取」，
 * 因此 effect 里不再有取数调用（`react-hooks/set-state-in-effect`）。
 * `ok` / `error` 是操作级反馈，仍留在本地 state，不塞进缓存。
 */

import { useCallback, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { voiceprintApi } from '../api/client'
import type { Voiceprint } from '../api/types'
import { useSession } from '../store/session'

type Suggestion = { score: number; source: Voiceprint; target: Voiceprint }

export function useVoiceprintAdmin() {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [draft, setDraft] = useState('')

  const printsQuery = useQuery({
    queryKey: ['voiceprints'],
    queryFn: () => voiceprintApi.list(),
  })
  const suggestionsQuery = useQuery({
    queryKey: ['voiceprint-suggestions'],
    queryFn: () => voiceprintApi.suggestions(),
    // 列表都没取到时先不取建议，省一次请求
    enabled: printsQuery.isSuccess,
  })
  // 设备列表仍以 session store 为准；这条 query 只负责「何时拉」
  useQuery({
    queryKey: ['audio-devices'],
    queryFn: async () => {
      await useSession.getState().refreshDevices()
      return useSession.getState().devices
    },
  })

  const devices = useSession((state) => state.devices)
  const activeDeviceId = useSession((state) => state.activeDeviceId)

  const prints = printsQuery.data ?? []
  const suggestions: Suggestion[] = suggestionsQuery.data ?? []
  const loading = printsQuery.isLoading
  const queryError = printsQuery.isError
    ? printsQuery.error instanceof Error
      ? printsQuery.error.message
      : '加载失败'
    : null

  const load = useCallback(async () => {
    await qc.invalidateQueries({ queryKey: ['voiceprints'] })
    await qc.invalidateQueries({ queryKey: ['voiceprint-suggestions'] })
  }, [qc])

  const startEdit = (print: Voiceprint) => {
    setEditingId(print.id)
    setDraft(print.name)
    setOk(null)
  }

  const submitEdit = async (print: Voiceprint) => {
    const name = draft.trim()
    if (!name || name === print.name) {
      setEditingId(null)
      return
    }
    try {
      // 按 id 改名：后端会按该声纹所属会议回溯更新片段，
      // 并通知正在录制的会话立即使用新名字。
      const result = await voiceprintApi.rename(print.id, name)
      setOk(`已改名为「${result.name}」`)
      setError(null)
      setEditingId(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '改名失败')
    }
  }

  const remove = async (print: Voiceprint) => {
    try {
      await voiceprintApi.remove(print.id)
      setOk(`已删除「${print.name}」`)
      setError(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除失败')
    }
  }

  const merge = async (source: Voiceprint, targetId: number) => {
    try {
      const result = await voiceprintApi.merge(source.id, targetId)
      setOk(
        `已把「${source.name}」合并进「${result.name}」` +
          (result.updated_segments > 0
            ? `，更新了 ${result.updated_segments} 条转写`
            : ''),
      )
      setError(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '合并失败')
    }
  }

  const toggleDisabled = async (print: Voiceprint) => {
    try {
      await voiceprintApi.setDisabled(print.id, !print.disabled)
      setOk(`${print.disabled ? '已启用' : '已停用'}「${print.name}」`)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '状态更新失败')
    }
  }

  const provisional = prints.filter((print) => print.provisional)
  const named = prints.filter((print) => !print.provisional)

  return {
    prints,
    loading,
    error: error ?? queryError,
    ok,
    editingId,
    draft,
    setDraft,
    suggestions,
    devices,
    activeDeviceId,
    provisional,
    named,
    load,
    startEdit,
    submitEdit,
    cancelEdit: () => setEditingId(null),
    remove,
    merge,
    toggleDisabled,
    enrollDone: () => {
      void load()
      setError(null)
    },
    clearError: () => setError(null),
    clearOk: () => setOk(null),
  }
}
