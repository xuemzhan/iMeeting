/**
 * 响应式现状量测（细化版，临时诊断脚本）。
 */

import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from '@playwright/test'

const BASE = process.env.BASE_URL || 'http://localhost:5173'
const OUT = process.env.OUT_DIR

const VIEWPORTS = [
  { name: 'phone-390', width: 390, height: 844, dsf: 3, mobile: true },
  { name: 'phone-360', width: 360, height: 780, dsf: 3, mobile: true },
  { name: 'tablet-768', width: 768, height: 1024, dsf: 2, mobile: true },
  { name: 'tablet-1024', width: 1024, height: 768, dsf: 2, mobile: false },
]

function probe() {
  const de = document.documentElement
  const vv = window.visualViewport
  const vw = de.clientWidth

  const rectOf = (el) => {
    const r = el.getBoundingClientRect()
    return { w: Math.round(r.width), h: Math.round(r.height), right: Math.round(r.right) }
  }

  const header = document.querySelector('header')
  const footer = document.querySelector('footer')
  const hero = document.querySelector('main, [class*="overflow-y-auto"]')

  const tapTargets = Array.from(document.querySelectorAll('button, a.btn, a[role="button"]'))
    .map((el) => ({ el, ...rectOf(el) }))
    .filter((x) => x.h > 0)
  const smallTargets = tapTargets.filter((x) => x.h < 40)

  const startCtas = Array.from(document.querySelectorAll('button')).filter((b) =>
    (b.textContent || '').includes('开始会议'),
  )

  const grid = document.querySelector('.grid')
  const gridStyle = grid ? getComputedStyle(grid) : null

  const featureCards = grid ? Array.from(grid.children).map(rectOf) : []

  return {
    layoutWidth: vw,
    innerWidth: window.innerWidth,
    visualWidth: vv ? Math.round(vv.width) : null,
    docScrollWidth: de.scrollWidth,
    horizontalOverflow: de.scrollWidth - vw,
    header: header ? { ...rectOf(header), needed: header.scrollWidth } : null,
    headerOverflow: header ? header.scrollWidth - header.clientWidth : null,
    footerHeight: footer ? rectOf(footer).h : null,
    mainScrollable: hero ? getComputedStyle(hero).overflowY : null,
    tapTargets: tapTargets.length,
    smallTargets: smallTargets.length,
    smallestTargets: smallTargets.slice(0, 5).map((x) => `${x.w}x${x.h}`),
    startCtaCount: startCtas.length,
    heroGridColumns: gridStyle ? gridStyle.gridTemplateColumns : null,
    featureCardWidths: featureCards.map((c) => c.w),
    docHeight: de.scrollHeight,
    viewportHeight: window.innerHeight,
  }
}

mkdirSync(OUT, { recursive: true })
const browser = await chromium.launch({ channel: 'chrome' })
const results = []

for (const vp of VIEWPORTS) {
  const context = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    deviceScaleFactor: vp.dsf,
    isMobile: vp.mobile,
    hasTouch: vp.mobile,
    locale: 'zh-CN',
  })
  const tab = await context.newPage()
  try {
    await tab.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 20000 })
  } catch {
    /* 继续用已渲染内容量测 */
  }
  await tab.waitForTimeout(1200)
  const metrics = await tab.evaluate(probe)
  await tab.screenshot({ path: join(OUT, `${vp.name}-home.png`) })
  results.push({ viewport: `${vp.width}x${vp.height}`, ...metrics })
  await context.close()
}

await browser.close()
console.log(JSON.stringify(results, null, 2))
