import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import {
  ArrowLeft,
  BookMarked,
  LayoutDashboard,
  Loader2,
  RotateCcw,
  Save,
  ScrollText,
  Settings2,
} from 'lucide-react'

import { systemApi } from '../../api/client'
import { GlossaryPanel } from '../../components/GlossaryPanel'
import { TopBar } from '../../components/TopBar'
import { useConfigEditor } from '../../hooks/useConfigEditor'
import { AdminAuditLog } from './AdminAuditLog'
import { AdminDashboard } from './AdminDashboard'
import { AdminModelStatusCard } from './AdminModelStatusCard'
import { AdminToast } from './AdminToast'
import { ConfigFieldInput } from './ConfigFieldInput'

export default function Admin() {
  const [tab, setTab] = useState<'config' | 'dashboard' | 'glossary' | 'audit'>('config')
  const {
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
  } = useConfigEditor()

  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: () => systemApi.health(),
    refetchInterval: 5000,
  })

  const error = loadError

  return (
    <div className="flex h-full flex-col">
      <TopBar />

      <main className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-[860px]">
          <Link
            to="/"
            className="mb-4 inline-flex items-center gap-1 text-[12px] no-underline text-tertiary hover:text-secondary"
          >
            <ArrowLeft size={13} />
            返回会议
          </Link>

          <div className="mb-4 flex items-center justify-between">
            <div>
              <h1 className="text-[15px] font-medium">
                {tab === 'config'
                  ? '后台配置'
                  : tab === 'dashboard'
                    ? '运营数据看板'
                    : tab === 'glossary'
                      ? '企业词表'
                    : '审计日志'}
              </h1>
              <p className="mt-0.5 text-[12px] text-tertiary">
                {tab === 'config'
                  ? '修改后立即对所有新会话生效，无需重启（标注的除外）'
                  : tab === 'dashboard'
                    ? '会议规模、AI 成本、延迟与噪声告警的集中视图'
                    : tab === 'glossary'
                      ? '统一型号、项目名、缩写和发言人常用写法'
                    : '关键配置、删除、分享与人工修订操作全程留痕'}
              </p>
            </div>

            <div className="flex items-center gap-2" hidden={tab !== 'config'}>
              <button
                type="button"
                onClick={reset}
                className="btn"
                disabled={!hasChanges || saving}
              >
                <RotateCcw size={13} />
                还原
              </button>
              <button
                type="button"
                onClick={() => void save()}
                className="btn btn-primary"
                disabled={!hasChanges || saving}
              >
                {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                保存
              </button>
            </div>
          </div>

          <div
            className="mb-3 flex gap-1 rounded-lg p-1"
            style={{ background: 'var(--bg-subtle)' }}
          >
            {[
              { key: 'config' as const, label: '配置', icon: Settings2 },
              { key: 'dashboard' as const, label: '数据看板', icon: LayoutDashboard },
              { key: 'glossary' as const, label: '企业词表', icon: BookMarked },
              { key: 'audit' as const, label: '审计日志', icon: ScrollText },
            ].map((item) => {
              const Icon = item.icon
              return (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => setTab(item.key)}
                  className="flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] transition-colors"
                  style={{
                    background: tab === item.key ? 'var(--bg-surface)' : 'transparent',
                    color:
                      tab === item.key ? 'var(--text-primary)' : 'var(--text-tertiary)',
                    boxShadow: tab === item.key ? 'var(--shadow-panel)' : 'none',
                  }}
                >
                  <Icon size={13} /> {item.label}
                </button>
              )
            })}
          </div>

          {tab === 'dashboard' && <AdminDashboard />}
          {tab === 'glossary' && <GlossaryPanel />}
          {tab === 'audit' && <AdminAuditLog />}

          {/* 模型状态 */}
          {tab === 'config' && health && <AdminModelStatusCard health={health} />}

          {/* 提示 */}
          {toast && <AdminToast toast={toast} onClose={() => setToast(null)} />}

          {/* 配置分组。仅在首次加载（还没有 schema）时占满屏，保存后的刷新不再闪 */}
          {tab === 'config' && isLoading && !schema && (
            <div className="flex justify-center py-16">
              <Loader2 size={18} className="animate-spin" style={{ color: 'var(--accent)' }} />
            </div>
          )}

          {tab === 'config' && error && (
            <div
              className="rounded-lg px-4 py-3 text-[12px]"
              style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}
            >
              加载配置失败：{error}
            </div>
          )}

          {tab === 'config' &&
            schema &&
            Object.entries(schema.groups).map(([group, fields]) => (
              <section key={group} className="panel mb-3 overflow-hidden">
                <div
                  className="px-4 py-3"
                  style={{ borderBottom: '1px solid var(--border-subtle)' }}
                >
                  <h2 className="text-[13px] font-medium">{group}</h2>
                </div>
                <div className="divide-y" style={{ borderColor: 'var(--border-subtle)' }}>
                  {fields.map((field) => (
                    <ConfigFieldInput
                      key={field.key}
                      field={field}
                      value={draft[field.key]}
                      error={errors[field.key]}
                      onChange={(v) => handleChange(field.key, v)}
                    />
                  ))}
                </div>
              </section>
            ))}
        </div>
      </main>
    </div>
  )
}
