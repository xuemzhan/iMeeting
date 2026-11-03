/** 后台配置的读取、草稿与保存。
 *
 * 后台配置页与设置抽屉都用它——两处逻辑若各写一份，很容易出现
 * 「一处修了、另一处没修」的漂移（例如校验提示、需重启提示）。
 *
 * 取数走 `useQuery`、草稿只存「用户改过的键」：
 * - 服务端值是基线，`values` = 基线 + 覆盖层；
 * - 因此不需要在 effect 里把 schema 抄进草稿（`react-hooks/set-state-in-effect`），
 *   也不会出现「保存后草稿和服务端不一致」。
 */

import { useCallback, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

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

/** 把分组结构拍平成 key -> 服务端当前值。 */
function flattenSchemaValues(
  schema: ConfigSchema | null,
): Record<string, unknown> {
  if (!schema) return {}
  const out: Record<string, unknown> = {}
  for (const fields of Object.values(schema.groups)) {
    for (const field of fields) out[field.key] = field.value
  }
  return out
}

export function useConfigEditor({ enabled = true }: Options = {}) {
  const qc = useQueryClient()
  const schemaQuery = useQuery({
    queryKey: ['config-schema'],
    queryFn: () => configApi.schema(),
    enabled,
  })
  const schema = schemaQuery.data ?? null
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<ConfigToast | null>(null)

  const baseValues = useMemo(() => flattenSchemaValues(schema), [schema])
  // 用展开而不是 `??`：`false` / `0` / `''` 都是合法配置值，也都是假值，
  // 用空值合并会让「把开关关掉」看起来没生效。
  const values = useMemo(
    () => ({ ...baseValues, ...draft }),
    [baseValues, draft],
  )

  const handleChange = useCallback((key: string, value: unknown) => {
    setDraft((previous) => ({ ...previous, [key]: value }))
    setErrors((previous) => {
      if (!(key in previous)) return previous
      const next = { ...previous }
      delete next[key]
      return next
    })
  }, [])

  /** 与已保存值不同的字段。只提交这些，避免把未改动的密钥掩码写回。 */
  const changed = useMemo(() => {
    const out: Record<string, unknown> = {}
    for (const key of Object.keys(draft)) {
      if (JSON.stringify(draft[key]) !== JSON.stringify(baseValues[key])) {
        out[key] = draft[key]
      }
    }
    return out
  }, [draft, baseValues])

  const hasChanges = Object.keys(changed).length > 0
  const isLoading = schemaQuery.isLoading
  const loadError = schemaQuery.isError
    ? schemaQuery.error instanceof Error
      ? schemaQuery.error.message
      : '加载配置失败'
    : null

  const reload = useCallback(async () => {
    await qc.invalidateQueries({ queryKey: ['config-schema'] })
  }, [qc])

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
      // 保存成功后草稿清空、以服务端返回值为准重新拉取，避免本地与服务端不一致
      setDraft({})
      await reload()
    } catch (err) {
      setToast({ kind: 'err', text: err instanceof Error ? err.message : '保存失败' })
    } finally {
      setSaving(false)
    }
  }, [changed, reload, saving, schema])

  const reset = useCallback(() => {
    setDraft({})
    setErrors({})
    setToast({ kind: 'ok', text: '已还原为上次保存的值' })
  }, [])

  return {
    schema,
    isLoading,
    loadError,
    /** 基线 + 覆盖层后的完整取值，渲染控件用它 */
    values,
    errors,
    saving,
    toast,
    hasChanges,
    handleChange,
    save,
    reset,
    setToast,
    reload,
  }
}
