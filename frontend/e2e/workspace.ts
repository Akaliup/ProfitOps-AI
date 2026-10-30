import { expect, type Page } from '@playwright/test'

export class Workspace {
  constructor(readonly page: Page) {}
  async open() {
    await this.page.goto('/')
    await expect(this.page.getByText('销售在增长，利润却没有跟上。')).toBeVisible()
  }
  async analyse(): Promise<string> {
    const accepted = this.page.waitForResponse(response => response.url().endsWith('/api/analyses') && response.request().method() === 'POST')
    await this.page.getByRole('button', { name: '启动 Profit Agent' }).click()
    const result = await (await accepted).json()
    await expect(this.page.getByRole('button', { name: '查看经营建议' })).toBeVisible()
    await this.page.getByRole('button', { name: '查看经营建议' }).click()
    const analysisResponse = await this.page.request.get(`/api/analyses/${result.id}`, { headers: { Authorization: 'Bearer demo-analyst' } })
    const analysis = await analysisResponse.json()
    return analysis.recommendation_id
  }
  async switchApprover() {
    await this.page.getByLabel('演示身份').selectOption('demo-approver')
    await expect(this.page.getByRole('button', { name: '批准建议' })).toBeVisible()
  }
}
