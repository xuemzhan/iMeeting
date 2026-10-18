import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  Mic,
  Sparkles,
  Users,
  FileText,
  Clock,
  ArrowRight,
  CalendarDays,
  Upload,
  Loader2,
} from 'lucide-react'
import { BrandMark } from './BrandMark'
import { useSession } from '../store/session'
import { meetingApi, getToken } from '../api/client'
import { formatDuration } from '../store/session'
import type { Meeting } from '../api/types'

/** 会议未开始时展示的引导面板。
 *
 * 设计意图：
 * - 让新用户一眼明白平台能做什么（自动断句、说话人区分、标点补全）
 * - 老用户看到最近会议，可以快速继续或回看
 * - 整体视觉密度接近生产工具而非空壳
 */
export function IdleHero() {
  const start = useSession((s) => s.start)
  const [recent, setRecent] = useState<Meeting[]>([])
  const [scheduled, setScheduled] = useState<Meeting[]>([])
  const [importing, setImporting] = useState(false)
  const [importMessage, setImportMessage] = useState<string | null>(null)

  useEffect(() => {
    // 会议接口需要鉴权；未登录（本地单机主界面按设计可用）时不发请求，
    // 否则每次进首页都会在服务端日志里刷一串无意义的 401。
    if (!getToken()) return
    Promise.all([meetingApi.list(8, 0), meetingApi.scheduled()])
      .then(([rows, upcoming]) => {
        setRecent(rows.filter((meeting) => meeting.status !== 'scheduled').slice(0, 3))
        setScheduled(upcoming.slice(0, 5))
      })
      .catch(() => {
        setRecent([])
        setScheduled([])
      })
  }, [])

  const importIcs = async (file: File | undefined) => {
    if (!file) return
    setImporting(true)
    setImportMessage(null)
    try {
      const result = await meetingApi.importIcs(file)
      setScheduled((items) => [...result.meetings, ...items].slice(0, 5))
      setImportMessage(
        result.imported > 0
          ? `已导入 ${result.imported} 场会议${result.skipped ? `，跳过 ${result.skipped} 场重复/无效日程` : ''}`
          : '没有可导入的新日程',
      )
    } catch (e) {
      setImportMessage(e instanceof Error ? e.message : '导入日历失败')
    } finally {
      setImporting(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center overflow-y-auto px-6 py-8">
      {/* 主体 */}
      <div className="w-full max-w-[640px]">
        {/* 标题区 */}
        <div className="mb-6 text-center">
          <div className="mb-3 flex justify-center">
            <BrandMark size={48} />
          </div>
          <h1
            className="text-[28px] font-semibold leading-tight tracking-tight"
            style={{ color: 'var(--text-primary)' }}
          >
            iMeeting
          </h1>
          {/* 副标题：小字号、灰色，与主标题形成层级 */}
          <p className="mt-1 text-[13px]" style={{ color: 'var(--text-tertiary)' }}>
            您的硅基伙伴
          </p>
          <p className="mt-3 text-[12px]" style={{ color: 'var(--text-secondary)' }}>
            自动断句 · 说话人区分 · 标点补全 · 结构化纪要
          </p>
        </div>

        {/* 特性卡片 */}
        <div className="mb-6 grid grid-cols-3 gap-3">
          <FeatureCard
            icon={<Mic size={16} />}
            title="实时转写"
            desc="流式识别，字幕随说话实时出现"
          />
          <FeatureCard
            icon={<Users size={16} />}
            title="说话人区分"
            desc="自动区分不同说话人并标注"
          />
          <FeatureCard
            icon={<Sparkles size={16} />}
            title="智能纪要"
            desc="会后自动提取议题、结论与待办"
          />
        </div>

        {/* 开始按钮 */}
        <button
          type="button"
          onClick={() => void start()}
          className="group flex w-full items-center justify-center gap-3 rounded-xl py-4 transition-all active:scale-[0.98] hover:shadow-md"
          style={{
            background: 'var(--accent)',
            color: '#fff',
            boxShadow: 'var(--shadow-panel)',
          }}
        >
          <span className="flex size-8 items-center justify-center rounded-full bg-white/15">
            <Mic size={18} />
          </span>
          <span className="text-[15px] font-medium">开始会议</span>
          <span className="text-[12px] opacity-70">点击即可，无需配置</span>
        </button>

        {scheduled.length > 0 && (
          <div className="mt-6">
            <div className="mb-3 flex items-center gap-2">
              <CalendarDays size={13} style={{ color: 'var(--text-tertiary)' }} />
              <h2 className="text-[12px] font-medium" style={{ color: 'var(--text-tertiary)' }}>
                已预约会议
              </h2>
              <label className="ml-auto cursor-pointer text-[11px] text-tertiary hover:text-secondary">
                <input
                  type="file"
                  accept=".ics,text/calendar"
                  className="hidden"
                  onChange={(e) => {
                    void importIcs(e.target.files?.[0])
                    e.currentTarget.value = ''
                  }}
                />
                <span className="inline-flex items-center gap-1">
                  {importing ? (
                    <Loader2 size={11} className="animate-spin" />
                  ) : (
                    <Upload size={11} />
                  )}
                  导入日历
                </span>
              </label>
            </div>
            <div className="space-y-2">
              {scheduled.map((meeting) => (
                <div
                  key={meeting.id}
                  className="panel flex items-center gap-3 px-3 py-2.5"
                >
                  <span
                    className="flex size-7 shrink-0 items-center justify-center rounded-lg"
                    style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
                  >
                    <CalendarDays size={13} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[12px] font-medium">{meeting.title}</p>
                    <p className="mt-0.5 text-[11px] text-tertiary">
                      {meeting.scheduled_at
                        ? new Date(meeting.scheduled_at).toLocaleString('zh-CN')
                        : '未设置时间'}
                      {meeting.participants && ` · ${meeting.participants}`}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void start(meeting.title, meeting.id)}
                    className="btn btn-primary shrink-0 text-[11px]"
                    style={{ height: 28 }}
                  >
                    开始
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {scheduled.length === 0 && getToken() && (
          <div className="mt-5 flex items-center justify-center">
            <label className="cursor-pointer text-[11px] text-tertiary hover:text-secondary">
              <input
                type="file"
                accept=".ics,text/calendar"
                className="hidden"
                onChange={(e) => {
                  void importIcs(e.target.files?.[0])
                  e.currentTarget.value = ''
                }}
              />
              <span className="inline-flex items-center gap-1">
                {importing ? (
                  <Loader2 size={11} className="animate-spin" />
                ) : (
                  <Upload size={11} />
                )}
                从日历导入预约会议
              </span>
            </label>
          </div>
        )}

        {importMessage && (
          <p className="mt-2 text-center text-[11px] text-tertiary">{importMessage}</p>
        )}

        {/* 最近会议 */}
        {recent.length > 0 && (
          <div className="mt-8">
            <div className="mb-3 flex items-center gap-2">
              <Clock size={13} style={{ color: 'var(--text-tertiary)' }} />
              <h2 className="text-[12px] font-medium" style={{ color: 'var(--text-tertiary)' }}>
                最近会议
              </h2>
            </div>
            <div className="space-y-2">
              {recent.map((m) => (
                <RecentMeetingRow key={m.id} meeting={m} />
              ))}
            </div>
          </div>
        )}

        {/* 提示 */}
        <div className="mt-6 flex items-center justify-center gap-4 text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
          <span className="flex items-center gap-1">
            <span className="kbd">Space</span>
            暂停/继续
          </span>
          <span className="flex items-center gap-1">
            <span className="kbd">F</span>
            切句
          </span>
          <span className="flex items-center gap-1">
            <span className="kbd">Esc</span>
            结束
          </span>
        </div>
      </div>
    </div>
  )
}

function FeatureCard({ icon, title, desc }: { icon: React.ReactNode; title: string; desc: string }) {
  return (
    <div
      className="panel flex flex-col gap-1.5 px-3 py-3"
      style={{ borderRadius: 'var(--radius-card)' }}
    >
      <span
        className="flex size-7 items-center justify-center rounded-lg"
        style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
      >
        {icon}
      </span>
      <p className="text-[12px] font-medium" style={{ color: 'var(--text-primary)' }}>
        {title}
      </p>
      <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
        {desc}
      </p>
    </div>
  )
}

function RecentMeetingRow({ meeting }: { meeting: Meeting }) {
  return (
    <Link
      to={`/history/${meeting.id}`}
      className="panel flex cursor-pointer items-center gap-3 px-3 py-2.5 no-underline transition-colors hover:bg-[var(--bg-hover)]"
    >
      <span
        className="flex size-7 shrink-0 items-center justify-center rounded-lg"
        style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
      >
        <FileText size={13} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[12px] font-medium" style={{ color: 'var(--text-primary)' }}>
          {meeting.title}
        </p>
        <p className="mt-0.5 flex items-center gap-1.5 text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
          <span>{new Date(meeting.created_at).toLocaleString('zh-CN')}</span>
          {meeting.duration_ms > 0 && (
            <>
              <span aria-hidden>·</span>
              <span>{formatDuration(meeting.duration_ms)}</span>
            </>
          )}
          <span aria-hidden>·</span>
          <span>{meeting.segment_count} 段</span>
        </p>
      </div>
      <ArrowRight size={13} style={{ color: 'var(--text-tertiary)' }} />
    </Link>
  )
}
