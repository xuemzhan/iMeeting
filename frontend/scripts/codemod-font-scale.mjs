/**
 * 字号语义化 codemod（方案 IM-PLN-001 §4.2 / P6）。
 *
 * 背景：项目里有 400+ 处写死的 `text-[10px]` / `text-[11px]` / `text-[12px]`。
 * 手机（<=767）上这些字号偏小，但直接在窄屏规则里改 `.text-\[11px\]` 之类的类名是
 * 不现实的，而逐个手改 400 处又会把桌面排版一起改掉。
 *
 * 做法：把「独立的」三档硬编码字号换成语义类，语义类在 CSS 里保持桌面像素不变，
 * 只在 compact 抬一档：
 *
 *   text-[10px]  ->  .text-micro   桌面 10px / compact 11px
 *   text-[11px]  ->  .text-meta    桌面 11px / compact 12px
 *   text-[12px]  ->  .text-ui      桌面 12px / compact 13px
 *
 * 护栏（命中即跳过，不做转换）：
 *   1. 同一个字符串里出现 `:text-[`（如 `md:text-[12px]`）—— 响应式覆盖会与语义类打架；
 *   2. 同一个字符串里出现第二个硬编码字号 —— 说明写法本身有覆盖关系；
 *   3. 字符串里含 `${`（模板插值）—— 结构不确定，交给人看。
 *
 * 用法（在 frontend/ 下）：
 *   node scripts/codemod-font-scale.mjs            # 打印将要修改的内容
 *   node scripts/codemod-font-scale.mjs --write    # 真正写入
 *   node scripts/codemod-font-scale.mjs --write --only src/pages   # 只处理某个目录（分批）
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { readdirSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'

const args = process.argv.slice(2)
const WRITE = args.includes('--write')
const ONLY = (() => {
  const i = args.indexOf('--only')
  return i >= 0 && args[i + 1] ? args[i + 1] : null
})()

const ROOT = resolve(process.cwd())
const SRC = ONLY ? resolve(ROOT, ONLY) : resolve(ROOT, 'src')

const MAP = [
  { from: 'text-[10px]', to: 'text-micro', px: 10 },
  { from: 'text-[11px]', to: 'text-meta', px: 11 },
  { from: 'text-[12px]', to: 'text-ui', px: 12 },
]

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    const info = statSync(path)
    if (info.isDirectory()) walk(path, out)
    else if (/\.(tsx|ts)$/.test(name)) out.push(path)
  }
  return out
}

/** 逐行处理：本仓库的 className 都是单行字面量，行级处理比整文件 AST 更稳。 */
function processLine(line, stats) {
  // 只处理不含插值的引号字符串（"..." / '...' / `...` 无 ${}）
  return line.replace(/(["'])((?:\\.|(?!\1)[^\\])*?)\1/g, (whole, quote, body) => {
    if (!body.includes('text-[')) return whole
    if (body.includes('${')) {
      stats.skipped.template += 1
      return whole
    }
    if (/:text-\[/.test(body)) {
      stats.skipped.variant += 1
      return whole
    }
    const bare = [...body.matchAll(/(^|\s)text-\[(\d+)px\](\s|$)/g)]
    if (bare.length === 0) return whole
    if (bare.length > 1) {
      stats.skipped.multiple += 1
      return whole
    }
    const match = MAP.find((item) => item.from === `text-[${bare[0][2]}px]`)
    if (!match) return whole

    stats.converted[match.to] = (stats.converted[match.to] ?? 0) + 1
    // 保留首尾空白，避免把相邻类粘在一起
    return `${quote}${body.replace(match.from, match.to)}${quote}`
  })
}

function makeStats() {
  return { converted: {}, skipped: { variant: 0, multiple: 0, template: 0 } }
}

function collect(dir) {
  const stats = makeStats()
  const changes = []
  for (const file of walk(dir)) {
    const original = readFileSync(file, 'utf8')
    const lines = original.split('\n')
    let touched = false
    const next = lines.map((line) => {
      if (!line.includes('text-[')) return line
      const replaced = processLine(line, stats)
      if (replaced !== line) touched = true
      return replaced
    })
    if (touched) changes.push({ file, before: original, after: next.join('\n') })
  }
  return { stats, changes }
}

const { stats, changes } = collect(SRC)
const rel = changes.map((c) => relative(ROOT, c.file)).sort()

const total = Object.values(stats.converted).reduce((sum, n) => sum + n, 0)
console.log(`扫描目录: ${relative(ROOT, SRC) || '.'}`)
console.log(`涉及文件: ${changes.length}`)
console.log(
  `替换: 共 ${total} 处 ` +
    `(micro ${stats.converted['text-micro'] ?? 0} / ` +
    `meta ${stats.converted['text-meta'] ?? 0} / ` +
    `ui ${stats.converted['text-ui'] ?? 0})`,
)
console.log(
  `跳过: 带变体 ${stats.skipped.variant} / 多个字号 ${stats.skipped.multiple} / ` +
    `模板插值 ${stats.skipped.template}`,
)
if (rel.length) console.log(`文件: \n  - ${rel.join('\n  - ')}`)

if (WRITE && changes.length) {
  for (const c of changes) writeFileSync(c.file, c.after, 'utf8')
  console.log('\n已写入。')
} else if (!WRITE) {
  console.log('\n（未写入；加 --write 生效）')
}
