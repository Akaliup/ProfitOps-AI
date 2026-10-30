import { defineConfig, devices } from '@playwright/test'
import path from 'node:path'

const python = path.resolve(process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python')
process.env.PLAYWRIGHT_BROWSERS_PATH ??= path.resolve('../data/browsers')

export default defineConfig({
  testDir: './e2e', fullyParallel: true, workers: 2,
  timeout: 45000, expect: { timeout: 15000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL: 'http://127.0.0.1:5180', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1080 } } }],
  webServer: [
    {
      command: `"${python}" -m uvicorn app.main:app --app-dir ../backend --host 127.0.0.1 --port 8010`,
      url: 'http://127.0.0.1:8010/health', reuseExistingServer: !process.env.CI,
      timeout: 30000,
      env: { PROFITOPS_DATABASE_URL: 'sqlite:///../data/e2e.db' },
    },
    { command: 'npm run dev', url: 'http://127.0.0.1:5180', reuseExistingServer: !process.env.CI, timeout: 30000 },
  ],
})
