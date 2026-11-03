import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'

import { ApiError, shareApi } from '../api/client'
import { groupParagraphs } from '../store/paragraphs'

function messageOf(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

/**
 * 分享页：口令开门 + 读取内容。
 *
 * `shareApi.open()` 其实是两种动作，拆开才不会有 effect 里的命令式调用：
 * - 首屏「用空口令试一次」是查询（受口令保护时失败属于预期路径）；
 * - 用户提交口令是变更（`useMutation`）。
 * `opening` / `authError` 全部由这两者派生。
 */
export function useSharedMeeting(token: string) {
  const [password, setPassword] = useState('')
  const [visibleParagraphCount, setVisibleParagraphCount] = useState(200)
  const [downloading, setDownloading] = useState(false)

  const auto = useQuery({
    queryKey: ['share-open', token],
    queryFn: () => shareApi.open(token, ''),
    enabled: Boolean(token),
    retry: false,
    // 换来的 access_token 不留在缓存里
    gcTime: 0,
  })
  const submit = useMutation({
    mutationFn: (candidate: string) => shareApi.open(token, candidate),
  })

  const accessToken = submit.data?.access_token ?? auto.data?.access_token ?? ''
  const opening = auto.isFetching || submit.isPending
  const authError = submit.isError
    ? messageOf(submit.error, '无法验证分享访问权限')
    : auto.isError
      ? messageOf(auto.error, '无法验证分享访问权限')
      : null

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
    try {
      await submit.mutateAsync(candidate)
    } catch {
      // 失败原因由 authError 派生展示，这里不再重复落一份状态
    }
  }

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
