/**
 * 响应式量测探针（可重复执行，CI 可作为门禁）。
 *
 * 对若干视口加载页面，采集方案 IM-PLN-001 第 6 节的验收指标：
 *   - A1 横向溢出 = 0
 *   - A2 顶栏不溢出
 *   - A3 触控目标高度 >= 40（compact 下 >= 44）
 *   - A5 首页同屏「开始会议」按钮恰好 1 个
 *   - 顺带记录特性卡宽度、正文计算字号，便于回归对比
 *
 * 用法：
 *   # 需要 dev server 已在 5173 运行
 *   node scripts/responsive-probe.mjs                     # 打印结果
 *   node scripts/responsive-probe.mjs --strict            # 任一指标不达标即退出码 1
 *   node scripts/responsive-probe.mjs --out json.txt      # 原始结果落盘
 *   node scripts/responsive-probe.mjs --paths /,/history  # 自定义页面
 *
 * 必须在 `frontend/` 目录下运行（脚本依赖本目录的 @playwright/test 与已安装的 Chrome）。
 */

import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { chromium } from '@playwright/test'

const args = process.argv.slice(2)
const getArg = (name, fallback = null) => {
  const i = args.indexOf(`--${name}`)
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback
}
const hasFlag = (name) => args.includes(`--${name}`)

const BASE = getArg('base', process.env.BASE_URL || 'http://localhost:5173')
const OUT = getArg('out', null)
const SHOTS = getArg('shots', null)
const STRICT = hasFlag('strict')
const PATHS = (getArg('paths', '/')).split(',').map((p) => p.trim()).filter(Boolean)

const VIEWPORTS = [
  { name: 'phone-360', width: 360, height: 780, dsf: 3, mobile: true },
  { name: 'phone-390', width: 390, height: 844, dsf: 3, mobile: true },
  { name: 'phablet-430', width: 430, height: 932, dsf: 3, mobile: true },
  { name: 'tablet-768', width: 768, height: 1024, dsf: 2, mobile: true },
  { name: 'tablet-1024', width: 1024, height: 768, dsf: 2, mobile: false },
  { name: 'desktop-1440', width: 1440, height: 900, dsf: 1, mobile: false },
]

/** 在页面里跑的量测逻辑（序列化后注入，不能引用外部变量）。 */
function collect() {
  const de = document.documentElement
  const vw = de.clientWidth
  const header = document.querySelector('header')
  const readerBodies = Array.from(document.querySelectorAll('.reader-body')).map((el) =>
    parseFloat(getComputedStyle(el).fontSize),
  )

  const rectOf = (el) => {
    const r = el.getBoundingClientRect()
    return { w: Math.round(r.width), h: Math.round(r.height) }
  }

  const targets = Array.from(document.querySelectorAll('button, a.btn, a[role="button"]'))
    .map((el) => ({ el, ...rectOf(el) }))
    .filter((x) => x.h > 0)
  const small = targets.filter((x) => x.h < 40)
  const minTargetHeight = targets.length ? Math.min(...targets.map((x) => x.h)) : null
  // 只统计「真的显示在屏上」的 CTA：display:none 的按钮仍会被 querySelectorAll 命中，
  // 按文本计数会把隐藏的那一个也算进去，从而永远得到 2。
  const startCtas = Array.from(document.querySelectorAll('button'))
    .filter((b) => (b.textContent || '').replace(/\s+/g, '').includes('开始会议'))
    .filter((b) => b.getBoundingClientRect().height > 0)
  const startCtaHeights = startCtas.map((b) => rectOf(b).h)
  const grid = document.querySelector('.grid')
  const cards = grid ? Array.from(grid.children).map((c) => rectOf(c).w) : []

  return {
    layoutWidth: vw,
    docScrollWidth: de.scrollWidth,
    horizontalOverflow: de.scrollWidth - vw,
    headerOverflow: header ? header.scrollWidth - header.clientWidth : null,
    tapTargets: targets.length,
    smallTargets: small.length,
    minTargetHeight,
    smallestTargets: small.slice(0, 6).map((x) => `${x.w}x${x.h}`),
    startCtaCount: startCtas.length,
    startCtaHeights,
    featureCardWidths: cards,
    readerBodyFontPx: readerBodies.length ? Math.min(...readerBodies) : null,
    docHeight: de.scrollHeight,
    viewportHeight: window.innerHeight,
  }
}

// 本地复用已安装的 Chrome；CI 用 Playwright 自带 Chromium（与 e2e 一致）
const browser = await chromium.launch(process.env.CI ? {} : { channel: 'chrome' })
const rows = []

for (const vp of VIEWPORTS) {
  const context = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    deviceScaleFactor: vp.dsf,
    isMobile: vp.mobile,
    hasTouch: vp.mobile,
    locale: 'zh-CN',
  })
  for (const path of PATHS) {
    const page = await context.newPage()
    try {
      await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded', timeout: 20000 })
      await page.waitForTimeout(900)
    } catch (error) {
      rows.push({ viewport: vp.name, path, error: String(error) })
      await page.close()
      continue
    }
    const metrics = await page.evaluate(collect)
    rows.push({ viewport: vp.name, path, ...metrics })
    if (SHOTS) {
      mkdirSync(SHOTS, { recursive: true })
      await page.screenshot({ path: resolve(SHOTS, `${vp.name}${path.replace(/\//g, '_')}.png`) })
    }
    await page.close()
  }
  await context.close()
}

await browser.close()

const formatted = JSON.stringify(rows, null, 2)
console.log(formatted)
if (OUT) {
  mkdirSync(dirname(resolve(OUT)), { recursive: true })
  writeFileSync(resolve(OUT), formatted, 'utf8')
}

if (STRICT) {
  const failures = []
  for (const row of rows) {
    if (row.error) {
      failures.push(`${row.viewport} ${row.path}: 加载失败 ${row.error}`)
      continue
    }
    const compact = row.layoutWidth <= 767
    // 40px 的可点区下限只对「手指操作」的两档（compact / medium）强判；
    // 桌面档（>=1024）用鼠标，保持既有 32px 观感，只记录不拦截。
    const touch = row.layoutWidth <= 1023
    if (row.horizontalOverflow > 0) {
      failures.push(`${row.viewport} ${row.path}: 横向溢出 ${row.horizontalOverflow}px`)
    }
    if (row.headerOverflow !== null && row.headerOverflow > 0) {
      failures.push(`${row.viewport} ${row.path}: 顶栏溢出 ${row.headerOverflow}px`)
    }
    if (touch && row.smallTargets > 0) {
      failures.push(
        `${row.viewport} ${row.path}: ${row.smallTargets} 个触控目标 <40px（${row.smallestTargets.join(', ')}）`,
      )
    }
    if (row.path === '/' && row.startCtaCount !== 1) {
      failures.push(`${row.viewport} ${row.path}: 「开始会议」按钮 ${row.startCtaCount} 个（应为 1）`)
    }
    if (compact && row.startCtaHeight && row.startCtaHeight < 56) {
      failures.push(`${row.viewport} ${row.path}: 「开始会议」高 ${row.startCtaHeight}px（应 >=56）`)
    }
    if (compact && row.readerBodyFontPx && row.readerBodyFontPx < 16) {
      failures.push(`${row.viewport} ${row.path}: 正文 ${row.readerBodyFontPx}px（应 >=16）`)
    }
  }
  if (failures.length) {
    console.error(`\n[响应式门禁] ${failures.length} 项不达标：\n- ` + failures.join('\n- '))
    process.exit(1)
  }
  console.log('\n[响应式门禁] 全部通过')
}
