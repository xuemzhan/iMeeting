#!/usr/bin/env node
/**
 * pcm-worklet 同步（IM-PLN-004 §5.7）。
 *
 * `src/audio/pcm-worklet.js` 是唯一来源，`public/pcm-worklet.js` 是发给浏览器的副本
 * （AudioWorklet 需要固定 URL，不能走打包器的模块图）。
 *
 * 用法：
 *   node scripts/sync-worklet.mjs          # 覆盖写入 public/
 *   node scripts/sync-worklet.mjs --check  # 只检查，不一致时退出码 1（可用于 CI）
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = fileURLToPath(new URL('.', import.meta.url))
const root = resolve(here, '..')
const source = resolve(root, 'src/audio/pcm-worklet.js')
const target = resolve(root, 'public/pcm-worklet.js')

const data = readFileSync(source)

if (process.argv.includes('--check')) {
  let current
  try {
    current = readFileSync(target)
  } catch {
    console.error('[worklet] public/pcm-worklet.js 缺失，请运行 npm run worklet:sync')
    process.exit(1)
  }
  if (!current.equals(data)) {
    console.error(
      '[worklet] public/ 与 src/ 不一致：请运行 npm run worklet:sync 后重新提交',
    )
    process.exit(1)
  }
  console.log('[worklet] 一致')
} else {
  writeFileSync(target, data)
  console.log(`[worklet] 已同步 -> ${target}`)
}
