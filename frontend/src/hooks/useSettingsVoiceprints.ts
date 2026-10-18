import { useEffect, useState } from 'react'

import { voiceprintApi } from '../api/client'
import type { Voiceprint } from '../api/types'

export function useSettingsVoiceprints({ open }: { open: boolean }) {
  const [prints, setPrints] = useState<Voiceprint[]>([])
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)

  const load = () => {
    voiceprintApi
      .list()
      .then(setPrints)
      .catch(() => setPrints([]))
  }

  useEffect(() => {
    if (open) load()
  }, [open])

  const remove = async (id: number, name: string) => {
    try {
      await voiceprintApi.remove(id)
      setOk(`已删除「${name}」`)
      setError(null)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败')
    }
  }

  const enrollDone = () => {
    load()
    setError(null)
  }

  const renamed = (message: string) => {
    setOk(message)
    setError(null)
    load()
  }

  return {
    prints,
    error,
    ok,
    load,
    remove,
    enrollDone,
    renamed,
    setError,
  }
}
