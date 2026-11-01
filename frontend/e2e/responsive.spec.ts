import { expect, test, type Page } from '@playwright/test'

/**
 * 响应式布局验收（对应 docs/plans/2026-11-01-responsive-ui-adaptation.md 第 6 节 A1/A2/A3/A5/A6）。
 *
 * 为什么在用例里改视口，而不是给每个设备配一个 Playwright project：
 * 项目里现有的 e2e（auth / identity-confirmation / participant-controls）默认桌面档，
 * 加 project 会让它们在每个设备上各跑一遍（时间 ×N，且多数断言与宽度无关）。
 * 这里只用 `setViewportSize` 切宽度 —— 我们所有窄屏规则都是宽度断点，
 * 不依赖 `isMobile` / `hasTouch`（真机触控另由 IM-PLN-001 A10 人工清单覆盖）。
 */

const VIEWPORTS = [
  { name: 'phone-360', width: 360, height: 780, compact: true },
  { name: 'phone-390', width: 390, height: 844, compact: true },
  { name: 'tablet-768', width: 768, height: 1024, compact: false },
  { name: 'tablet-1023', width: 1023, height: 768, compact: false },
  { name: 'laptop-1024', width: 1024, height: 768, compact: false },
  { name: 'desktop-1440', width: 1440, height: 900, compact: false },
] as const

/** 手指操作档（compact + medium）：可点控件不得小于 40px */
const TOUCH_MAX_WIDTH = 1023

/** 轮换后的管理员口令（与 auth.spec 保持一致） */
const NEXT_PASSWORD = 'playwright-password-123'

async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
}

async function headerOverflow(page: Page): Promise<number | null> {
  return page.evaluate(() => {
    const header = document.querySelector('header')
    return header ? header.scrollWidth - header.clientWidth : null
  })
}

/** 只统计真正显示在屏上的按钮（display:none 的也会被 querySelectorAll 命中） */
async function visibleTargetHeights(page: Page): Promise<number[]> {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>('button, a.btn, a[role="button"]'))
      .map((el) => el.getBoundingClientRect())
      .filter((rect) => rect.height > 0)
      .map((rect) => Math.round(rect.height)),
  )
}

/**
 * 首页「开始会议」按钮。`getByRole` 只匹配无障碍树里的元素，
 * 因此被 `max-md:hidden` / `md:hidden` 收起来的那一个不会被算进来。
 */
function startCta(page: Page) {
  return page.getByRole('button', { name: /开始会议/ })
}

test.describe('首页各档布局', () => {
  for (const vp of VIEWPORTS) {
    test(`${vp.name}：无横向溢出、只有一个开始按钮、触控够大`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height })
      await page.goto('/')
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible()

      expect(await horizontalOverflow(page), '横向溢出').toBe(0)
      expect(await headerOverflow(page), '顶栏溢出').toBe(0)

      // A5：同屏只能有一个「开始会议」（隐藏的那一个不在无障碍树里）
      const cta = startCta(page)
      await expect(cta).toHaveCount(1)
      if (vp.compact) {
        // A3：compact 下主 CTA >= 56 高
        const box = await cta.first().boundingBox()
        expect(box?.height ?? 0).toBeGreaterThanOrEqual(56)
      }

      // A3：手指操作档不允许 <40px 的可点控件
      if (vp.width <= TOUCH_MAX_WIDTH) {
        const heights = await visibleTargetHeights(page)
        expect(heights.filter((h) => h < 40), '不到 40px 的可点控件').toEqual([])
      }
    })
  }

  test('390px 顶栏不出现常驻导航，入口收进「更多」', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/')

    await expect(page.getByRole('link', { name: '历史会议' })).toBeHidden()
    await expect(page.getByRole('button', { name: '更多入口' })).toBeVisible()
    await page.getByRole('button', { name: '更多入口' }).click()
    await expect(page.getByRole('link', { name: '历史会议' })).toBeVisible()
  })
})

test.describe('登录页各档布局', () => {
  for (const vp of VIEWPORTS) {
    test(`${vp.name}：无溢出、输入 16px、主按钮够高`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height })
      await page.goto('/login')
      await expect(page.locator('#username')).toBeVisible()

      expect(await horizontalOverflow(page)).toBe(0)

      if (vp.width <= TOUCH_MAX_WIDTH) {
        const fontSize = await page
          .locator('#username')
          .evaluate((el) => parseFloat(getComputedStyle(el).fontSize))
        // iOS / iPadOS 对 <16px 的输入框会在聚焦时放大整页
        expect(fontSize).toBeGreaterThanOrEqual(16)

        const submit = page.getByRole('button', { name: /登录|注册并登录/ }).first()
        const box = await submit.boundingBox()
        expect(box?.height ?? 0).toBeGreaterThanOrEqual(40)
      }
    })
  }
})

/**
 * A6：断点行为（双栏 / 分隔条 / 手机三标签）。
 *
 * 这几条断言需要真的进到「会中」状态，而会中要能开始会议 —— 依赖后端是否强制登录
 * （CI 的 e2e 席位是 REQUIRE_AUTH=1，本地 dev 席位通常不需要）。
 *
 * 用例自己找路：按「测试账号阶梯」用 API 登录拿 token；若账号仍在首次改密状态，
 * 就顺手把密码改成 NEXT_PASSWORD 再登录（改密会让旧 token 失效，所以必须重登）。
 * 两条路都不通才显式 skip 并写清原因 —— 绝不假绿。需要临时关掉时设 RESPONSIVE_MEETING=0。
 */
async function loginForMeeting(
  page: Page,
): Promise<{ token: string } | { pendingPassword: true } | null> {
  const credentials = [
    { username: 'e2e', password: 'e2e-pass-123' },
    { username: 'admin', password: NEXT_PASSWORD },
    { username: 'admin', password: 'admin123' },
  ]
  let pending = false
  for (const data of credentials) {
    try {
      const response = await page.request.post('/api/v1/auth/login', { data })
      if (!response.ok()) continue
      const body = (await response.json()) as { access_token?: string; token?: string }
      let token = body.access_token ?? body.token
      if (!token) continue

      const me = await page.request.get('/api/v1/auth/me', {
        headers: { authorization: `Bearer ${token}` },
      })
      if (me.ok()) {
        const profile = (await me.json()) as { must_change_password?: boolean }
        if (profile.must_change_password) {
          pending = true
          const rotated = await page.request.post('/api/v1/auth/password', {
            data: { current_password: data.password, new_password: NEXT_PASSWORD },
            headers: { authorization: `Bearer ${token}` },
          })
          if (!rotated.ok()) continue
          const relogin = await page.request.post('/api/v1/auth/login', {
            data: { username: data.username, password: NEXT_PASSWORD },
          })
          if (!relogin.ok()) continue
          const again = (await relogin.json()) as { access_token?: string; token?: string }
          token = again.access_token ?? again.token
          if (!token) continue
        }
      }
      await page.addInitScript((value) => window.localStorage.setItem('imeeting-token', value), token)
      return { token }
    } catch {
      // 登录接口不存在（未启用鉴权）时继续尝试下一个
    }
  }
  return pending ? { pendingPassword: true } : null
}

test.describe('会中断点', () => {
  test.skip(process.env.RESPONSIVE_MEETING === '0', '显式关闭（RESPONSIVE_MEETING=0）')

  test('1023 双栏无分隔条，1024 出现分隔条，390 只显示当前标签', async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 })
    const auth = await loginForMeeting(page)
    await page.goto('/')

    if (auth && 'pendingPassword' in auth) {
      test.skip(true, '测试账号仍处于首次改密状态，无法进入会中')
      return
    }

    const cta = startCta(page)
    const canStart = await cta
      .first()
      .waitFor({ state: 'visible', timeout: 5_000 })
      .then(() => true)
      .catch(() => false)
    if (!canStart) {
      test.skip(true, auth ? '已登录但首页没有「开始会议」入口' : '既没有可用测试账号，也不能匿名起会')
      return
    }
    await cta.first().click()

    // 注意用 waitFor 而不是 isVisible：后者不等待，会立刻返回 false
    const recording = await page
      .getByText('识别中')
      .first()
      .waitFor({ state: 'visible', timeout: 20_000 })
      .then(() => true)
      .catch(() => false)
    if (!recording) {
      test.skip(true, '后端拒绝建会/建连，无法进入会中状态')
      return
    }

    // 1024（wide）：两侧并排 + 分隔条
    await expect(page.getByRole('separator')).toBeVisible()
    await expect(page.getByRole('heading', { name: '实时转写' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '会议纪要' })).toBeVisible()

    // 1023（medium）：双栏，但分隔条不挂载
    await page.setViewportSize({ width: 1023, height: 768 })
    await expect(page.getByRole('heading', { name: '会议纪要' })).toBeVisible()
    await expect(page.getByRole('separator')).toHaveCount(0)

    // 390（compact）：底部三标签，只显示当前那一块
    await page.setViewportSize({ width: 390, height: 844 })
    const nav = page.getByRole('navigation', { name: '面板切换' })
    await expect(nav).toBeVisible()
    await expect(nav.getByRole('button')).toHaveCount(3)
    await expect(page.getByRole('heading', { name: '会议纪要' })).toBeHidden()

    await nav.getByRole('button', { name: /纪要/ }).click()
    await expect(page.getByRole('heading', { name: '会议纪要' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '实时转写' })).toBeHidden()
  })
})
