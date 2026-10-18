import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { ApiError, shareApi } from '../api/client'
import { groupParagraphs } from '../store/paragraphs'

export function useSharedMeeting(token: string) {
  const [password, setPassword] = useState('')
  const [accessToken, setAccessToken] = useState('')
  const [opening, setOpening] = useState(true)
  const [authError, setAuthError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [visibleParagraphCount, setVisibleParagraphCount] = useState(200)

  const shared = useQuery({
    queryKey: ['shared-meeting', token, accessToken],
    queryFn: () => shareApi.get(token, accessToken),
    enabled: Boolean(token && accessToken),
    retry: false,
  })
  const paragraphs = useMemo(
    () => groupParagraphs(shared.data?.segments ?? []),
    [shared.data?.segments],
  )
  const visibleParagraphs = paragraphs.slice(0, visibleParagraphCount)

  const open = async (candidate = password) => {
    setOpening(true)
    setAuthError(null)
    try {
      const result = await shareApi.open(token, candidate)
      setAccessToken(result.access_token)
    } catch (e) {
      setAccessToken('')
      setAuthError(e instanceof Error ? e.message : '无法验证分享访问权限')
    } finally {
      setOpening(false)
    }
  }

  useEffect(() => {
    if (!token) return
    void open('')
    // token 变化时重新验证；口令由用户显式提交。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  const downloadAudio = async () => {
    if (!shared.data) return
    setDownloading(true)
    try {
      const blob = await shareApi.audio(token, accessToken)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${shared.data.meeting.title || 'meeting'}.wav`
      anchor.click()
      URL.revokeObjectURL(url)
    } finally {
      setDownloading(false)
    }
  }

  const locked =
    !accessToken &&
    (authError !== null ||
      (shared.error instanceof ApiError && shared.error.status === 401))
  const expired =
    shared.error instanceof ApiError && shared.error.status === 410

  return {
    password,
    setPassword,
    opening,
    authError,
    downloading,
    visibleParagraphCount,
    shared,
    paragraphs,
    visibleParagraphs,
    locked,
    expired,
    open,
    downloadAudio,
    loadMore: () =>
      setVisibleParagraphCount((value) => value + 200),
  }
}
