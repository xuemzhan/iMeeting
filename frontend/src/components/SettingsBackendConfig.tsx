import { Link } from 'react-router-dom'
import { Loader2, RotateCcw, Save, ShieldCheck } from 'lucide-react'

import { useConfigEditor } from '../hooks/useConfigEditor'
import { ConfigGroupFields } from './ConfigGroupFields'
import {
  CollapsibleSection,
  InlineMsg,
  SettingsSection,
} from './SettingsPanelParts'

/**
 * 设置抽屉里直接可改的后端配置分组。
 *
 * 这里只放「模型 / FunASR 服务」这一条主线的分组——它们与转写质量、
 * 延迟直接相关，用户最常调；「会话与安全」偏运维，仍走「后台配置」页。
 * 分组名必须与后端 `CONFIG_SCHEMA` 里的 `group` 完全一致。
 */
const INLINE_GROUPS: { name: string; title: string; defaultOpen: boolean }[] = [
  { name: '模型与推理', title: '模型与推理', defaultOpen: false },
  { name: '识别与断句', title: '识别与断句', defaultOpen: false },
  { name: '说话人识别', title: '说话人识别', defaultOpen: false },
  { name: 'LLM 纪要', title: 'LLM 纪要', defaultOpen: true },
]

export function SettingsBackendConfig({
  isAdminUser,
  cfg,
  onClose,
}: {
  isAdminUser: boolean
  cfg: ReturnType<typeof useConfigEditor>
  onClose: () => void
}) {
  if (!isAdminUser) {
    return (
      <SettingsSection title="后端配置">
        <p className="mb-2 text-[11px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          模型与推理、识别与断句、说话人识别、LLM 纪要等配置项都在这里，
          但<strong>需要管理员身份</strong>才能查看和修改。当前未登录，所以暂时看不到。
        </p>
        <p className="mb-2 text-[11px] leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
          未登录也可以正常开会转写；只有「改后端配置」这一步需要管理员。
        </p>
        <Link
          to="/login"
          onClick={onClose}
          className="mt-1 inline-flex items-center gap-1.5 text-[12px] no-underline"
          style={{ color: 'var(--accent-text)' }}
        >
          <ShieldCheck size={13} />
          用管理员账号登录
        </Link>
      </SettingsSection>
    )
  }

  return (
    <>
      {cfg.isLoading && !cfg.schema && (
        <div className="mb-5 flex justify-center py-4">
          <Loader2 size={15} className="animate-spin" style={{ color: 'var(--accent)' }} />
        </div>
      )}

      {cfg.loadError && <InlineMsg kind="err">加载配置失败：{cfg.loadError}</InlineMsg>}
      {cfg.toast && <InlineMsg kind={cfg.toast.kind}>{cfg.toast.text}</InlineMsg>}

      {cfg.schema && (
        <p
          className="mb-3 text-[11px] leading-relaxed"
          style={{ color: 'var(--text-tertiary)' }}
        >
          以下为后端 FunASR 与 LLM 配置。标「需重启」的项保存后要重启服务才生效；
          其余即时生效（模型类参数在下次加载模型时生效）。
        </p>
      )}

      {INLINE_GROUPS.map(({ name, title, defaultOpen }) => {
        const fields = cfg.schema?.groups[name]
        if (!fields?.length) return null
        return (
          <CollapsibleSection
            key={name}
            title={title}
            count={fields.length}
            defaultOpen={defaultOpen}
          >
            <ConfigGroupFields
              fields={fields}
              draft={cfg.draft}
              errors={cfg.errors}
              onChange={cfg.handleChange}
            />
          </CollapsibleSection>
        )
      })}

      {cfg.schema && (
        <div className="mb-5 flex items-center gap-2">
          <button
            type="button"
            className="btn btn-primary flex-1"
            disabled={!cfg.hasChanges || cfg.saving}
            onClick={() => void cfg.save()}
          >
            {cfg.saving ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Save size={13} />
            )}
            保存配置
          </button>
          <button
            type="button"
            className="btn"
            disabled={!cfg.hasChanges || cfg.saving}
            onClick={cfg.reset}
          >
            <RotateCcw size={13} />
            还原
          </button>
        </div>
      )}
    </>
  )
}
