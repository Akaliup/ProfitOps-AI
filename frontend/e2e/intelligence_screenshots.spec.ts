import { test, expect } from '@playwright/test'
import path from 'node:path'
import fs from 'node:fs'

test('Capture multi-store intelligence screenshots', async ({ page }) => {
  test.setTimeout(60000)
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))

  // 1. Open app
  await page.goto('/')
  await expect(page.getByText('销售在增长，利润却没有跟上。')).toBeVisible()

  // 2. Go to data import page
  await page.getByRole('button', { name: '数据导入' }).click()
  await expect(page.getByRole('heading', { name: '数据导入', level: 1 })).toBeVisible()

  const ordersCsvPath = path.resolve('..', 'docs', 'examples', 'intelligence', 'orders.csv')
  const ordersContent = fs.readFileSync(ordersCsvPath, 'utf8')
  const adsContent = fs.readFileSync(path.resolve('..', 'docs', 'examples', 'intelligence', 'ads.csv'), 'utf8')
  const invContent = fs.readFileSync(path.resolve('..', 'docs', 'examples', 'intelligence', 'inventory.csv'), 'utf8')

  // Set file via input
  const fileInput = page.locator('input[type="file"]')
  await fileInput.setInputFiles({
    name: 'orders.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from(ordersContent),
  })

  // Click 校验文件
  await page.getByRole('button', { name: '校验文件' }).click()
  await expect(page.getByText('校验通过')).toBeVisible()

  // Take screenshot 1: import-preview.png
  await page.screenshot({ path: '../docs/screenshots/import-preview.png' })

  // Commit the 3 sample files via backend API as approver
  const approverHeaders = { Authorization: 'Bearer demo-approver', 'Content-Type': 'text/csv' }
  const ordersResponse = await page.request.post('/api/data/orders/commit', { headers: approverHeaders, data: ordersContent })
  expect(ordersResponse.ok(), await ordersResponse.text()).toBeTruthy()
  const adsResponse = await page.request.post('/api/data/ads/commit', { headers: approverHeaders, data: adsContent })
  expect(adsResponse.ok(), await adsResponse.text()).toBeTruthy()
  const inventoryResponse = await page.request.post('/api/data/inventory/commit', { headers: approverHeaders, data: invContent })
  expect(inventoryResponse.ok(), await inventoryResponse.text()).toBeTruthy()

  // 3. Navigate to weekly intelligence page
  await page.getByRole('button', { name: '经营周报与库存' }).click()
  await expect(page.getByText('多店铺经营周报', { exact: true })).toBeVisible()

  // Set end date 2020-01-12
  const dateInput = page.getByLabel('周期结束日')
  await dateInput.fill('2020-01-12')

  // Click "生成并保存周报"
  await page.getByRole('button', { name: '生成并保存周报' }).click()

  // Wait for report: check for 店铺表现 and 量价与结构拆解
  await expect(page.getByText('店铺表现')).toBeVisible({ timeout: 15000 })
  await expect(page.getByText('量价与结构拆解')).toBeVisible({ timeout: 15000 })

  // Take screenshot 2: weekly-decomposition.png
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: '../docs/screenshots/weekly-decomposition.png' })

  // Take screenshot 3: inventory-replenishment.png
  const invSection = page.getByText('库存风险与补货建议', { exact: true })
  await invSection.scrollIntoViewIfNeeded()
  await page.screenshot({ path: '../docs/screenshots/inventory-replenishment.png' })

  // Take screenshot 4: pricing-suggestions.png
  const pricingSection = page.getByText('调价建议', { exact: true })
  await pricingSection.scrollIntoViewIfNeeded()
  await page.screenshot({ path: '../docs/screenshots/pricing-suggestions.png' })

  // Real offline fallback: no intercepted responses or invented citations.
  await page.getByText('追问这份周报', { exact: true }).scrollIntoViewIfNeeded()
  await page.getByLabel('周报问题').fill('本周利润变化的主要贡献是什么？')
  await page.getByRole('button', { name: '发送追问' }).click()
  await expect(page.getByText('确定性降级 · 未配置模型')).toBeVisible()
  await page.screenshot({ path: '../docs/screenshots/weekly-chat-fallback.png' })

  await page.getByText('查看计算证据与导入批次', { exact: true }).click()
  const evidence = page.locator('details [data-evidence-id]').first()
  await expect(evidence).toBeVisible()
  await evidence.scrollIntoViewIfNeeded()
  await page.screenshot({ path: '../docs/screenshots/weekly-evidence.png' })
  expect(errors).toEqual([])
})
