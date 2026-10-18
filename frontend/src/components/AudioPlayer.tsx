import { useEffect, useRef, useState, useCallback } from 'react'
import { Play, Pause, Volume2, VolumeX } from 'lucide-react'
import { getToken, meetingApi } from '../api/client'
import { formatDuration } from '../store/session'

interface Props {
  meetingId: number
}

/** 音频回放组件。
 *
 * 从 GET /api/v1/meetings/{id}/audio 加载 WAV，
 * 支持 play/pause/seek，并通过自定义事件暴露 seek 方法供外部调用。
 */
export function AudioPlayer({ meetingId }: Props) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [currentMs, setCurrentMs] = useState(0)
  const [durationMs, setDurationMs] = useState(0)
  const [muted, setMuted] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 构造带鉴权的 URL：fetch 拿 blob 再赋给 audio
  const audioUrlRef = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function load() {
      setLoading(true)
      setError(null)
      try {
        const token = getToken()
        const res = await fetch(meetingApi.audioUrl(meetingId), {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        })
        if (!res.ok) {
          throw new Error(`加载失败 (${res.status})`)
        }
        const blob = await res.blob()
        if (cancelled) return
        const url = URL.createObjectURL(blob)
        audioUrlRef.current = url
        if (audioRef.current) {
          audioRef.current.src = url
        }
        setLoading(false)
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : '音频加载失败')
        setLoading(false)
      }
    }

    void load()

    return () => {
      cancelled = true
      if (audioUrlRef.current) {
        URL.revokeObjectURL(audioUrlRef.current)
        audioUrlRef.current = null
      }
    }
  }, [meetingId])

  // 音频事件
  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return

    const onTimeUpdate = () => {
      setCurrentMs(Math.floor(audio.currentTime * 1000))
    }
    const onLoadedMetadata = () => {
      setDurationMs(Math.floor(audio.duration * 1000))
    }
    const onPlay = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    const onEnded = () => setPlaying(false)

    audio.addEventListener('timeupdate', onTimeUpdate)
    audio.addEventListener('loadedmetadata', onLoadedMetadata)
    audio.addEventListener('play', onPlay)
    audio.addEventListener('pause', onPause)
    audio.addEventListener('ended', onEnded)

    return () => {
      audio.removeEventListener('timeupdate', onTimeUpdate)
      audio.removeEventListener('loadedmetadata', onLoadedMetadata)
      audio.removeEventListener('play', onPlay)
      audio.removeEventListener('pause', onPause)
      audio.removeEventListener('ended', onEnded)
    }
  }, [])

  // seek：供外部通过 ref 调用
  const seek = useCallback((ms: number) => {
    const audio = audioRef.current
    if (!audio || !audio.src) return
    audio.currentTime = ms / 1000
    setCurrentMs(ms)
  }, [])

  // 暴露 seek 给父组件
  useEffect(() => {
    // 通过自定义事件让父组件调用 seek
    const handler = (e: Event) => {
      const ms = (e as CustomEvent<number>).detail
      seek(ms)
    }
    window.addEventListener('audio-player-seek', handler)
    return () => window.removeEventListener('audio-player-seek', handler)
  }, [seek])

  const togglePlay = () => {
    const audio = audioRef.current
    if (!audio || !audio.src) return
    if (playing) {
      audio.pause()
    } else {
      void audio.play()
    }
  }

  const toggleMute = () => {
    const audio = audioRef.current
    if (!audio) return
    audio.muted = !audio.muted
    setMuted(audio.muted)
  }

  const handleScrub = (e: React.ChangeEvent<HTMLInputElement>) => {
    const ms = Number(e.target.value)
    seek(ms)
  }

  if (loading) {
    return (
      <div
        className="flex items-center gap-2 rounded-lg px-3 py-2 text-[12px]"
        style={{ background: 'var(--bg-subtle)', color: 'var(--text-tertiary)' }}
      >
        <span className="size-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
        正在加载音频…
      </div>
    )
  }

  if (error) {
    return (
      <div
        className="flex items-center gap-2 rounded-lg px-3 py-2 text-[12px]"
        style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}
      >
        {error}
      </div>
    )
  }

  const progress = durationMs > 0 ? (currentMs / durationMs) * 100 : 0

  return (
    <div
      className="flex items-center gap-3 rounded-lg px-3 py-2"
      style={{ background: 'var(--bg-subtle)' }}
    >
      <audio ref={audioRef} preload="metadata" />

      {/* 播放/暂停 */}
      <button
        type="button"
        onClick={togglePlay}
        className="flex size-8 shrink-0 items-center justify-center rounded-full transition-colors hover:bg-black/5"
        style={{ background: 'var(--accent)', color: '#fff' }}
        title={playing ? '暂停' : '播放'}
      >
        {playing ? <Pause size={14} fill="currentColor" /> : <Play size={14} fill="currentColor" />}
      </button>

      {/* 时间 */}
      <span className="font-mono text-[11px] tabular-nums text-tertiary">
        {formatDuration(currentMs)}
      </span>

      {/* 进度条 */}
      <input
        type="range"
        min={0}
        max={durationMs}
        value={currentMs}
        onChange={handleScrub}
        className="h-1 flex-1 cursor-pointer appearance-none rounded-full"
        style={{
          background: `linear-gradient(to right, var(--accent) ${progress}%, var(--border-default) ${progress}%)`,
        }}
        title="拖动定位"
      />

      {/* 总时长 */}
      <span className="font-mono text-[11px] tabular-nums text-tertiary">
        {formatDuration(durationMs)}
      </span>

      {/* 静音 */}
      <button
        type="button"
        onClick={toggleMute}
        className="btn btn-ghost shrink-0"
        style={{ width: 28, padding: 0, height: 28 }}
        title={muted ? '取消静音' : '静音'}
      >
        {muted ? <VolumeX size={13} /> : <Volume2 size={13} />}
      </button>
    </div>
  )
}
