import { expect, test } from '@playwright/test'

test('first administrator login rotates the bootstrap password', async ({ page }) => {
  await page.goto('/login')

  await page.locator('#username').fill('admin')
  await page.locator('#password').fill('admin123')
  await page.getByRole('button', { name: '登录', exact: true }).click()

  await expect(page).toHaveURL(/\/change-password$/)
  await expect(page.getByRole('heading', { name: '修改密码' })).toBeVisible()
  await page.locator('#current-password').fill('admin123')
  await page.locator('#new-password').fill('playwright-password-123')
  await page.locator('#confirm-password').fill('playwright-password-123')
  await page.getByRole('button', { name: '修改密码', exact: true }).click()

  await expect(page).toHaveURL(/\/admin$/)
  await expect(page.getByRole('heading', { name: '后台配置' })).toBeVisible()

  const health = await page.request.get('http://127.0.0.1:8020/api/v1/live')
  expect(health.ok()).toBeTruthy()
})
