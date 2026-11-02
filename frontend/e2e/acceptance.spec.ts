import { test, expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = path.resolve(frontendRoot, '..')
const python = path.resolve(repositoryRoot, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python')
const databaseUrl = process.env.PROFITOPS_E2E_DATABASE_URL ?? 'sqlite:///../data/e2e.db'
const databasePath = path.resolve(frontendRoot, databaseUrl.replace(/^sqlite:\/\/\//, ''))
const fixtureFile = path.resolve(frontendRoot, 'test-results', 'e2e-fixtures.json')

type Fixtures = { failed_analysis_id: string; tenant_b_analysis_id: string; tenant_b_email: string; tenant_b_password: string }
let fixtures: Fixtures

test.beforeAll(() => {
  execFileSync(python, [
    path.resolve(repositoryRoot, 'scripts/e2e_fixtures.py'),
    '--database', databasePath,
    '--out', fixtureFile,
  ], { stdio: 'pipe' })
  fixtures = JSON.parse(fs.readFileSync(fixtureFile, 'utf8')) as Fixtures
})

test('JWT 登录、401、角色权限与跨租户隔离（真实 API）', async ({ request }) => {
  const login = await request.post('/api/auth/login', {
    data: { email: 'analyst@demo.local', password: 'demo-analyst' },
  })
  expect(login.status()).toBe(200)
  const session = await login.json()
  expect(session.user).toMatchObject({ role: 'analyst', tenant_id: 'demo' })
  expect(session.access_token.split('.')).toHaveLength(3)

  const me = await request.get('/api/auth/me', {
    headers: { Authorization: `Bearer ${session.access_token}` },
  })
  expect(me.status()).toBe(200)
  expect((await me.json()).id).toBe(session.user.id)

  const anonymous = await request.get('/api/analyses')
  expect(anonymous.status()).toBe(401)
  expect((await anonymous.json()).error.code).toBe('unauthorized')

  const tampered = await request.get('/api/analyses', {
    headers: { Authorization: `Bearer ${session.access_token.slice(0, -3)}abc` },
  })
  expect(tampered.status()).toBe(401)

  const wrongPassword = await request.post('/api/auth/login', {
    data: { email: 'analyst@demo.local', password: 'wrong-password' },
  })
  expect(wrongPassword.status()).toBe(401)
  expect((await wrongPassword.json()).error.code).toBe('invalid_credentials')

  // Analyst cannot create knowledge documents; only the approver role may.
  const denied = await request.post('/api/knowledge/documents', {
    headers: { Authorization: `Bearer ${session.access_token}` },
    data: {
      title: 'E2E 越权文档', source: 'e2e://acceptance/denied', version: 'v1',
      content: '经营分析师不应创建知识文档，本请求必须被服务端拒绝。',
    },
  })
  expect(denied.status()).toBe(403)

  const tenantLogin = await request.post('/api/auth/login', {
    data: { email: fixtures.tenant_b_email, password: fixtures.tenant_b_password },
  })
  expect(tenantLogin.status()).toBe(200)
  const tenantSession = await tenantLogin.json()
  expect(tenantSession.user.tenant_id).toBe('tenant-b')

  const crossTenantRead = await request.get(`/api/analyses/${fixtures.tenant_b_analysis_id}`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
  })
  expect(crossTenantRead.status()).toBe(404)
  const ownRead = await request.get(`/api/analyses/${fixtures.tenant_b_analysis_id}`, {
    headers: { Authorization: `Bearer ${tenantSession.access_token}` },
  })
  expect(ownRead.status()).toBe(200)
  expect((await ownRead.json()).status).toBe('failed')

  const demoList = await (await request.get('/api/analyses?limit=100', {
    headers: { Authorization: `Bearer ${session.access_token}` },
  })).json()
  expect(demoList.items.some((row: { id: string }) => row.id === fixtures.tenant_b_analysis_id)).toBe(false)
  const tenantList = await (await request.get('/api/analyses?limit=100', {
    headers: { Authorization: `Bearer ${tenantSession.access_token}` },
  })).json()
  expect(tenantList.items.map((row: { id: string }) => row.id)).toContain(fixtures.tenant_b_analysis_id)
  const tenantRecommendations = await (await request.get('/api/recommendations', {
    headers: { Authorization: `Bearer ${tenantSession.access_token}` },
  })).json()
  expect(tenantRecommendations.items).toEqual([])
})

test('Agent 运维：角色限制、真实聚合与 Trace 跳转', async ({ page, request }) => {
  const analystToken = (await (await request.post('/api/auth/login', {
    data: { email: 'analyst@demo.local', password: 'demo-analyst' },
  })).json()).access_token
  const denied = await request.get('/api/ops/summary', {
    headers: { Authorization: `Bearer ${analystToken}` },
  })
  expect(denied.status()).toBe(403)

  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/')
  await expect(page.getByText('销售在增长，利润却没有跟上。')).toBeVisible()
  await page.getByLabel('演示身份').selectOption('demo-approver')
  await page.getByRole('button', { name: /Agent 运维/ }).click()
  await expect(page.getByRole('heading', { name: '任务队列' })).toBeVisible()
  await expect(page.getByText('最近失败任务')).toBeVisible()
  // Two Playwright workers seed a failed analysis each; match this worker's
  // full request id instead of the shared eight-character display prefix.
  const failureRow = page.locator('.ops-failures tbody tr').filter({ hasText: fixtures.failed_analysis_id })
  await expect(failureRow).toBeVisible()
  await failureRow.getByRole('button', { name: '查看 Trace' }).click()
  await expect(page.getByText(/llm_tool_execution_failed：工具执行失败：模拟账本不可用/)).toBeVisible()
  expect(errors).toEqual([])
})

test('失败状态展示与手动重试（真实 Worker）', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/')
  await expect(page.getByText('销售在增长，利润却没有跟上。')).toBeVisible()
  await page.getByRole('button', { name: /Agent 工作台/ }).click()
  // Another spec may have produced a newer analysis in the shared database;
  // select the seeded failed one explicitly instead of relying on ordering.
  await page.getByLabel('历史分析').selectOption(fixtures.failed_analysis_id)
  await expect(page.getByText('llm_tool_execution_failed：工具执行失败：模拟账本不可用')).toBeVisible()
  await expect(page.getByText('尝试 3/3')).toBeVisible()
  await expect(page.getByRole('button', { name: '重新排队执行' })).toBeVisible()

  await page.getByRole('button', { name: '重新排队执行' }).click()
  await expect(page.getByText('失败任务已重新排队，工作流会从新的一次尝试开始。')).toBeVisible()
  await expect(page.getByRole('button', { name: '查看经营建议' })).toBeVisible({ timeout: 30000 })

  const response = await page.request.get(`/api/analyses/${fixtures.failed_analysis_id}`, {
    headers: { Authorization: 'Bearer demo-analyst' },
  })
  const analysis = await response.json()
  expect(analysis.status).toBe('waiting_approval')
  expect(analysis.recommendation_id).toBeTruthy()
  expect(errors).toEqual([])
})

test('知识库页面：新增、检索与停用（真实 API）', async ({ page }) => {
  const marker = Date.now().toString(36)
  const title = `E2E 广告预算审批规则 ${marker}`
  await page.goto('/')
  await expect(page.getByText('销售在增长，利润却没有跟上。')).toBeVisible()
  await page.getByLabel('演示身份').selectOption('demo-approver')
  await page.getByRole('button', { name: /运营知识库/ }).click()

  await page.getByLabel('知识标题').fill(title)
  await page.getByLabel('知识来源').fill(`e2e://acceptance/${marker}`)
  await page.getByLabel('知识版本').fill('v-e2e')
  await page.getByLabel('知识正文').fill(`# 适用范围\n广告预算调整必须由审批负责人批准。${marker}`)
  await page.getByRole('button', { name: '添加知识文档' }).click()
  await expect(page.getByText(/知识文档已创建/)).toBeVisible()

  const document = page.locator('.knowledge-doc-list article').filter({ hasText: marker })
  await expect(document).toBeVisible()

  await page.getByLabel('知识库检索').fill('广告预算审批')
  await page.getByRole('button', { name: '检索知识' }).click()
  await expect(page.getByText(/检索到 \d+ 个知识片段/)).toBeVisible()

  await document.getByRole('button', { name: '停用' }).click()
  await expect(page.getByText(/知识文档已停用/)).toBeVisible()
  await expect(page.locator('.knowledge-doc-list article').filter({ hasText: marker })).toHaveCount(0)
})
