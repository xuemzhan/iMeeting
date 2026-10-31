/**
 * 浏览器验收：参会人重要程度 / 面板字号 / 可拖动分栏。
 *
 * 由 pwsh 脚本或人工先启动后端（ASR_MODE=echo，独立 SQLite）与 Vite dev，
 * 并注入环境变量后执行：
 *   BASE_URL=http://127.0.0.1:5175 API_URL=http://127.0.0.1:8021 \
 *   MEETING_ID=1 E2E_USERNAME=e2e E2E_PASSWORD=e2e-pass-123 \
 *   node scripts/verify-features.mjs
 *
 * 退出码 0 = 全部通过；非 0 = 有失败项（同时打印 FAIL 行与截图路径）。
 */

import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const BASE_URL = (process.env.BASE_URL || 'http://127.0.0.1:5175').replace(/\/$/, '')
const API_URL = (process.env.API_URL || 'http://127.0.0.1:8021').replace(/\/$/, '')
const MEETING_ID = process.env.MEETING_ID || '1'
const USERNAME = process.env.E2E_USERNAME || 'e2e'
const PASSWORD = process.env.E2E_PASSWORD || 'e2e-pass-123'

const here = dirname(fileURLToPath(import.meta.url))
const shotsDir = join(here, '..', 'test-results')
mkdirSync(shotsDir, { recursive: true })

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  — ${detail}` : ''}`)
}

function near(actual, expected, tolerance = 3) {
  return Math.abs(Number(actual) - Number(expected)) <= tolerance
}

async function waitFor(label, fn, timeout = 15_000, interval = 150) {
  const deadline = Date.now() + timeout
  let lastError = null
  while (Date.now() < deadline) {
    try {
      const value = await fn()
      if (value) return value
    } catch (error) {
      lastError = error
    }
    await new Promise((resolve) => setTimeout(resolve, interval))
  }
  throw new Error(`${label} 超时（${timeout}ms）${lastError ? `：${lastError.message}` : ''}`)
}

/** 读取本地偏好（持久化有 200ms 防抖，必须轮询而不是立刻断言）。 */
function readStored(page, key) {
  return page.evaluate((name) => {
    try {
      return JSON.parse(localStorage.getItem('imeeting-settings') || '{}')[name]
    } catch {
      return null
    }
  }, key)
}

async function waitStored(page, key, predicate, label, timeout = 6_000) {
  return waitFor(
    label,
    async () => {
      const value = await readStored(page, key)
      return predicate(value) ? value : null
    },
    timeout,
  )
}

const browser = await chromium.launch({
  channel: 'chrome',
  headless: true,
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
})
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  permissions: ['microphone'],
})
const page = await context.newPage()
page.on('pageerror', (error) => console.log('pageerror:', error.message))

try {
  // ---- 登录（API）并把令牌塞进 localStorage，跳过改密引导 ----
  const loginRes = await context.request.post(`${API_URL}/api/v1/auth/login`, {
    data: { username: USERNAME, password: PASSWORD },
  })
  if (!loginRes.ok()) {
    throw new Error(`登录失败 HTTP ${loginRes.status()}: ${await loginRes.text()}`)
  }
  const { access_token: token } = await loginRes.json()
  await context.addInitScript((value) => {
    localStorage.setItem('imeeting-token', value)
  }, token)
  const auth = { Authorization: `Bearer ${token}` }
  const speakersUrl = `${API_URL}/api/v1/meetings/${MEETING_ID}/speakers`

  // 幂等：先把所有说话人复位成「一般」，重复执行本脚本也能得到同样的起点
  for (const item of await (await context.request.get(speakersUrl, { headers: auth })).json()) {
    await context.request.put(`${speakersUrl}/importance`, {
      headers: auth,
      data: { speaker: item.speaker, importance: 3 },
    })
  }

  // ---- 详情页：参会人重要程度 ----
  await page.goto(`${BASE_URL}/history/${MEETING_ID}`, { waitUntil: 'domcontentloaded' })
  await page.getByRole('heading', { name: '完整转写' }).waitFor({ timeout: 20_000 })

  const speakers = await (
    await context.request.get(speakersUrl, { headers: auth })
  ).json()
  check(
    '详情接口默认三人且重要程度为一般(3)',
    speakers.length === 3 && speakers.every((item) => item.importance === 3),
    JSON.stringify(speakers.map((item) => [item.speaker, item.importance])),
  )

  const zhangsanGroup = page.getByRole('radiogroup', { name: '张三的重要程度' })
  await zhangsanGroup.waitFor({ timeout: 10_000 })
  await zhangsanGroup.getByRole('radio', { name: '关键', exact: true }).click()
  await waitFor(
    '张三切到关键',
    async () =>
      (await zhangsanGroup.getByRole('radio', { name: '关键', exact: true }).getAttribute('aria-checked')) === 'true',
  )
  const afterKey = await (
    await context.request.get(`${API_URL}/api/v1/meetings/${MEETING_ID}/speakers`, {
      headers: auth,
    })
  ).json()
  const zhangsan = afterKey.find((item) => item.speaker === '张三')
  check('设置关键后接口返回 importance=1', zhangsan?.importance === 1, JSON.stringify(zhangsan))

  const transcriptPanel = page
    .locator('section')
    .filter({ has: page.getByRole('heading', { name: '完整转写' }) })
  const keyChip = transcriptPanel.getByText('关键', { exact: true }).first()
  await keyChip.waitFor({ timeout: 10_000 })
  check('转写面板显示「关键」标记', await keyChip.isVisible())

  // 次要：李四
  const lisiGroup = page.getByRole('radiogroup', { name: '李四的重要程度' })
  await lisiGroup.getByRole('radio', { name: '次要', exact: true }).click()
  await waitFor(
    '李四切到次要',
    async () =>
      (await lisiGroup.getByRole('radio', { name: '次要', exact: true }).getAttribute('aria-checked')) === 'true',
  )
  const secondaryChip = transcriptPanel.getByText('次要', { exact: true }).first()
  await secondaryChip.waitFor({ timeout: 10_000 })
  check('转写面板显示「次要」标记', await secondaryChip.isVisible())

  // 刷新后仍然保留（服务端持久化）
  await page.reload({ waitUntil: 'domcontentloaded' })
  const zhangsanReloaded = page.getByRole('radiogroup', { name: '张三的重要程度' })
  await zhangsanReloaded.waitFor({ timeout: 20_000 })
  await waitFor(
    '刷新后张三仍是关键',
    async () =>
      (await zhangsanReloaded.getByRole('radio', { name: '关键', exact: true }).getAttribute('aria-checked')) === 'true',
  )
  check('刷新后重要程度保留', true)

  // 侧栏标题/操作行不能被挤换行（320px 窄栏）
  const detailSidebar = page
    .locator('aside')
    .filter({ has: page.getByRole('heading', { name: '会议纪要' }) })
    .first()
  const titleHeight = await detailSidebar
    .locator('h2')
    .first()
    .evaluate((el) => el.getBoundingClientRect().height)
  check('详情侧栏标题不换行', titleHeight <= 22, `h2 height=${titleHeight.toFixed(1)}px`)
  check(
    '纪要操作按钮独立成行',
    await detailSidebar.getByRole('toolbar', { name: '纪要操作' }).isVisible(),
  )

  // ---- 字号：纪要面板 ----
  const minutesAside = page
    .locator('aside')
    .filter({ has: page.getByRole('heading', { name: '会议纪要' }) })
    .first()
  const minutesBefore = await minutesAside.evaluate((el) => el.style.getPropertyValue('--reader-scale'))
  await page.getByRole('button', { name: '会议纪要字号调大' }).click()
  await waitFor(
    '纪要字号变为 1.05',
    async () =>
      (await minutesAside.evaluate((el) => el.style.getPropertyValue('--reader-scale'))) === '1.05',
  )
  const storedMinutes = await waitStored(
    page,
    'minutesScale',
    (value) => value === 1.05,
    '纪要字号写入本地偏好',
  )
  check(
    '纪要字号 +1 档并持久化',
    storedMinutes === 1.05,
    `before=${minutesBefore || '1'} stored=${storedMinutes}`,
  )

  // ---- 字号：转写面板（实测正文计算字号变大）----
  const bodyBefore = await transcriptPanel
    .locator('.reader-body-sm')
    .first()
    .evaluate((el) => Number.parseFloat(getComputedStyle(el).fontSize))
  await page.getByRole('button', { name: '完整转写字号调大' }).click()
  await waitFor(
    '转写字号变为 1.05',
    async () =>
      (await transcriptPanel.evaluate((el) => el.style.getPropertyValue('--reader-scale'))) === '1.05',
  )
  const bodyAfter = await transcriptPanel
    .locator('.reader-body-sm')
    .first()
    .evaluate((el) => Number.parseFloat(getComputedStyle(el).fontSize))
  check(
    '转写正文字号随缩放变大',
    bodyAfter > bodyBefore,
    `${bodyBefore.toFixed(2)}px -> ${bodyAfter.toFixed(2)}px`,
  )
  // 等持久化（防抖 200ms）；下面会用整页跳转/重新加载验证它真的落盘了
  await waitStored(
    page,
    'transcriptScale',
    (value) => value === 1.05,
    '转写字号写入本地偏好',
  )
  await page.screenshot({ path: join(shotsDir, 'verify-detail.png'), fullPage: true })

  // ---- 实时页：可拖动分栏 ----
  await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: /开始会议/ }).first().click()
  const separator = page.getByRole('separator')
  await separator.waitFor({ state: 'visible', timeout: 30_000 })
  const liveAside = page
    .locator('aside')
    .filter({ has: page.getByRole('heading', { name: '会议纪要' }) })
    .first()
  await liveAside.waitFor({ state: 'visible', timeout: 30_000 })
  const widthBefore = (await liveAside.boundingBox()).width

  const sepBox = await separator.boundingBox()
  await page.mouse.move(sepBox.x + sepBox.width / 2, sepBox.y + sepBox.height / 2)
  await page.mouse.down()
  await page.mouse.move(sepBox.x + sepBox.width / 2 - 120, sepBox.y + sepBox.height / 2, { steps: 10 })
  await page.mouse.up()
  const widthAfter = (await liveAside.boundingBox()).width
  check(
    '拖动分隔条加宽纪要面板',
    near(widthAfter, widthBefore + 120),
    `${widthBefore}px -> ${widthAfter}px`,
  )
  const storedPane = await waitStored(
    page,
    'minutesPaneWidth',
    (value) => near(value, widthAfter),
    '分栏宽度写入本地偏好',
  )
  check('分栏宽度写入本地偏好', near(storedPane, widthAfter), `stored=${storedPane}`)

  await separator.dblclick()
  const widthReset = (await liveAside.boundingBox()).width
  check('双击分隔条复位为默认宽度', near(widthReset, 320), `${widthReset}px`)

  // 缩到最窄也不能把标题挤换行（自适应下限）
  const resetBox = await separator.boundingBox()
  await page.mouse.move(resetBox.x + resetBox.width / 2, resetBox.y + resetBox.height / 2)
  await page.mouse.down()
  await page.mouse.move(resetBox.x + resetBox.width / 2 + 260, resetBox.y + resetBox.height / 2, { steps: 8 })
  await page.mouse.up()
  const widthNarrow = (await liveAside.boundingBox()).width
  const narrowTitleHeight = await liveAside
    .locator('h2')
    .first()
    .evaluate((el) => el.getBoundingClientRect().height)
  check(
    '最窄宽度被夹住且标题不换行',
    widthNarrow >= 259 && narrowTitleHeight <= 22,
    `width=${widthNarrow}px h2=${narrowTitleHeight.toFixed(1)}px`,
  )
  await separator.dblclick()

  // 实时页字号按钮存在且生效（先看 DOM 变量，再等本地持久化）
  const liveTranscript = page
    .locator('section')
    .filter({ has: page.getByRole('heading', { name: '实时转写' }) })
  const liveScaleBefore = await liveTranscript.evaluate((el) =>
    Number(el.style.getPropertyValue('--reader-scale') || 1),
  )
  await page.getByRole('button', { name: '实时转写字号调大' }).click()
  const liveScaleAfter = await waitFor(
    '实时转写字号 +1 档',
    async () => {
      const value = await liveTranscript.evaluate((el) =>
        Number(el.style.getPropertyValue('--reader-scale') || 1),
      )
      return value > liveScaleBefore ? value : null
    },
    5_000,
  )
  await waitStored(
    page,
    'transcriptScale',
    (value) => near(value, liveScaleAfter, 0.001),
    '实时转写字号写入本地偏好',
  )
  check(
    '实时转写字号可在界面调整',
    near(liveScaleAfter, 1.1),
    `${liveScaleBefore} -> ${liveScaleAfter}`,
  )
  await page.screenshot({ path: join(shotsDir, 'verify-live.png') })

  // 收尾：结束会议
  await page.getByRole('button', { name: '结束会议' }).click().catch(() => {})
} catch (error) {
  check('执行异常', false, error.message)
} finally {
  await browser.close()
}

const failed = results.filter((item) => !item.ok)
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
if (failed.length) {
  console.log('失败项：' + failed.map((item) => item.name).join('；'))
  process.exitCode = 1
}
