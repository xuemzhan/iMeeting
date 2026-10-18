/** 后台配置的读取、草稿与保存。
 *
 * 后台配置页与设置抽屉都用它——两处逻辑若各写一份，很容易出现
 * 「一处修了、另一处没修」的漂移（例如校验提示、需重启提示）。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { configApi } from '../api/client'
import type { ConfigSchema } from '../api/types'

export interface ConfigToast {
  kind: 'ok' | 'warn' | 'err'
  text: string
}

interface Options {
  /** 为 false 时不发请求（例如设置抽屉未打开、或当前用户不是管理员）。 */
  enabled?: boolean
}

export function useConfigEditor({ enabled = true }: Options = {}) {
  const [schema, setSchema] = useState<ConfigSchema | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<ConfigToast | null>(null)

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const s = await configApi.schema()
      const initial: Record<string, unknown> = {}
      for (const fields of Object.values(s.groups)) {
        for (const f of fields) initial[f.key] = f.value
      }
      setSchema(s)
      setDraft(initial)
      setErrors({})
      setLoadError(null)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : '加载配置失败')
      setSchema(null)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    void load()
  }, [enabled, load])

  const handleChange = useCallback((key: string, value: unknown) => {
    setDraft((prev) => ({ ...prev, [key]: value }))
    setErrors((prev) => {
      if (!(key in prev)) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
  }, [])

  /** 与已保存值不同的字段。只提交这些，避免把未改动的密钥掩码写回。 */
  const changed = useMemo(() => {
    if (!schema) return {}
    const out: Record<string, unknown> = {}
    for (const fields of Object.values(schema.groups)) {
      for (const f of fields) {
        if (JSON.stringify(f.value) !== JSON.stringify(draft[f.key])) {
          out[f.key] = draft[f.key]
        }
      }
    }
    return out
  }, [schema, draft])

  const hasChanges = Object.keys(changed).length > 0

  const save = useCallback(async () => {
    if (saving || !schema) return
    setSaving(true)
    setToast(null)
    try {
      if (Object.keys(changed).length === 0) {
        setToast({ kind: 'ok', text: '没有需要保存的改动' })
        return
      }
      const res = await configApi.update(changed)
      if (Object.keys(res.errors).length) {
        setErrors(res.errors)
        setToast({ kind: 'err', text: '部分配置校验未通过，请检查标红项' })
      } else if (res.requires_restart.length) {
        setToast({
          kind: 'warn',
          text: `已保存 ${res.updated.length} 项。其中 ${res.requires_restart.length} 项需重启服务后生效。`,
        })
      } else {
        setToast({ kind: 'ok', text: `已保存 ${res.updated.length} 项配置，即时生效` })
      }
      // 保存成功后以服务端返回值为准重新拉取，避免本地草稿与服务端不一致
      await load()
    } catch (err) {
      setToast({ kind: 'err', text: err instanceof Error ? err.message : '保存失败' })
    } finally {
      setSaving(false)
    }
  }, [changed, load, saving, schema])

  const reset = useCallback(() => {
    if (!schema) return
    const initial: Record<string, unknown> = {}
    for (const fields of Object.values(schema.groups)) {
      for (const f of fields) initial[f.key] = f.value
    }
    setDraft(initial)
    setErrors({})
    setToast({ kind: 'ok', text: '已还原为上次保存的值' })
  }, [schema])

  return {
    schema,
    isLoading,
    loadError,
    draft,
    errors,
    saving,
    toast,
    hasChanges,
    handleChange,
    save,
    reset,
    setToast,
    reload: load,
  }
}
