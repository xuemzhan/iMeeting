/**
 * pcm-worklet 单一来源校验（IM-PLN-004 §5.7）。
 *
 * 背景：音频重采样在 AudioWorklet 里完成，脚本必须由 `public/` 以固定 URL 提供
 * （见 `src/audio/recorder.ts` 的 WORKLET_URL）。历史上同一份代码在
 * `src/audio/pcm-worklet.js` 与 `public/pcm-worklet.js` 各存一份、靠人工同步——
 * 改了一处忘了另一处就会出现"开发环境正常、构建产物还是旧逻辑"的诡异问题。
 *
 * 这里用一条测试把它钉住：两份必须逐字节相同。同步动作由
 * `npm run worklet:sync` 完成（src 是唯一来源）。
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..', '..')
const source = resolve(root, 'src/audio/pcm-worklet.js')
const published = resolve(root, 'public/pcm-worklet.js')

describe('pcm-worklet 唯一来源', () => {
  it('public/ 与 src/ 两份必须逐字节一致', () => {
    const a = readFileSync(source)
    const b = readFileSync(published)
    expect(b.equals(a)).toBe(true)
  })

  it('worklet 里不能出现需要打包器处理的 import（它按原样发到浏览器）', () => {
    const code = readFileSync(source, 'utf8')
    expect(/^\s*import\s/m.test(code)).toBe(false)
    expect(code).toContain('registerProcessor')
  })
})
