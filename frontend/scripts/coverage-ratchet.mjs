#!/usr/bin/env node
/**
 * 覆盖率棘轮（ratchet）。
 *
 * 现状：全量行覆盖约 12%，直接设阈值会让 CI 长期红灯，于是这条债一直挂着。
 * 棘轮换个方向：**只保证不倒退**。基线数字存在 `coverage-baseline.json`，
 * 低于基线（留一点抖动余量）才失败；覆盖率上涨不要求同步改基线，
 * 想抬高门槛时手动跑 `node scripts/coverage-ratchet.mjs --update`。
 *
 * 用法：
 *   npm run test:coverage && node scripts/coverage-ratchet.mjs
 *   node scripts/coverage-ratchet.mjs --update   # 把当前数字写成新基线
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const summaryPath = resolve(here, '../coverage/coverage-summary.json')
const baselinePath = resolve(here, '../coverage-baseline.json')

/** v8 覆盖率在不同机器上有零点几个百分点的抖动，留一点余量避免偶发红灯。 */
const TOLERANCE = 0.2

const METRICS = ['lines', 'statements', 'functions', 'branches']

function readJson(path, hint) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'))
  } catch (error) {
    console.error(`读不到 ${path}：${error.code === 'ENOENT' ? hint : error.message}`)
    process.exit(2)
  }
}

const summary = readJson(summaryPath, '请先跑 `npm run test:coverage`')
const total = summary.total
if (!total) {
  console.error('coverage-summary.json 缺少 total 字段，报告格式可能变了')
  process.exit(2)
}

const current = Object.fromEntries(METRICS.map((key) => [key, Number(total[key]?.pct ?? 0)]))
const update = process.argv.includes('--update')

if (update) {
  const payload = { ...current, note: '覆盖率基线：低于此值 CI 失败；抬高请重跑 --update' }
  writeFileSync(baselinePath, `${JSON.stringify(payload, null, 2)}\n`)
  console.log(`已写入基线 ${baselinePath}`)
  for (const key of METRICS) console.log(`  ${key.padEnd(10)} ${current[key].toFixed(2)}%`)
  process.exit(0)
}

const baseline = readJson(baselinePath, '还没有基线：先跑 `node scripts/coverage-ratchet.mjs --update`')

let failed = false
console.log('覆盖率棘轮（基线 -> 当前，下降超过 ' + TOLERANCE + ' 个百分点即失败）')
for (const key of METRICS) {
  const floor = Number(baseline[key] ?? 0)
  const now = current[key]
  const delta = now - floor
  const bad = delta < -TOLERANCE
  if (bad) failed = true
  const arrow = delta === 0 ? '=' : delta > 0 ? '+' : ''
  console.log(
    `  ${bad ? '✗' : '✓'} ${key.padEnd(10)} ${floor.toFixed(2)}% -> ${now.toFixed(2)}%  (${arrow}${delta.toFixed(2)})`,
  )
}

if (failed) {
  console.error('\n覆盖率出现倒退。要么补测试，要么在评审里说明并用 --update 明确下调基线。')
  process.exit(1)
}
const improved = METRICS.some((key) => current[key] - Number(baseline[key] ?? 0) > TOLERANCE)
if (improved) {
  console.log('\n覆盖率高于基线；可以考虑跑 --update 把门槛抬上去。')
}
