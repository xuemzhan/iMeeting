import { useEffect, useState } from 'react'

import { voiceprintApi } from '../api/client'
import type { Voiceprint } from '../api/types'
import { useSession } from '../store/session'

export function useVoiceprintAdmin() {
  const [prints, setPrints] = useState<Voiceprint[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [suggestions, setSuggestions] = useState<
    { score: number; source: Voiceprint; target: Voiceprint }[]
  >([])

  const devices = useSession((state) => state.devices)
  const activeDeviceId = useSession((state) => state.activeDeviceId)
  const refreshDevices = useSession((state) => state.refreshDevices)

  const load = () => {
    setLoading(true)
    voiceprintApi
      .list()
      .then((data) => {
        setPrints(data)
        setError(null)
        void voiceprintApi
          .suggestions()
          .then(setSuggestions)
          .catch(() => setSuggestions([]))
      })
      .catch((e) => setError(e instanceof Error ? e.message : '加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    void refreshDevices()
  }, [refreshDevices])

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
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '改名失败')
    }
  }

  const remove = async (print: Voiceprint) => {
    try {
      await voiceprintApi.remove(print.id)
      setOk(`已删除「${print.name}」`)
      setError(null)
      load()
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
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '合并失败')
    }
  }

  const toggleDisabled = async (print: Voiceprint) => {
    try {
      await voiceprintApi.setDisabled(print.id, !print.disabled)
      setOk(`${print.disabled ? '已启用' : '已停用'}「${print.name}」`)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '状态更新失败')
    }
  }

  const provisional = prints.filter((print) => print.provisional)
  const named = prints.filter((print) => !print.provisional)

  return {
    prints,
    loading,
    error,
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
      load()
      setError(null)
    },
    clearError: () => setError(null),
    clearOk: () => setOk(null),
  }
}
