import { defineConfig, devices } from '@playwright/test'
import path from 'node:path'

const python = path.resolve(process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python')
process.env.PLAYWRIGHT_BROWSERS_PATH ??= path.resolve('../data/browsers')
// Acceptance runs point this at a disposable database; the default stays the
// existing E2E database and never the runtime database in data/profitops.db.
const databaseUrl = process.env.PROFITOPS_E2E_DATABASE_URL ?? 'sqlite:///../data/e2e.db'

export default defineConfig({
  // Tests in one spec share a seeded database and mutate it (retry, approve),
  // so they must not race inside a file; separate files still run in parallel.
  testDir: './e2e', fullyParallel: false, workers: 2,
  timeout: 45000, expect: { timeout: 15000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL: 'http://127.0.0.1:5180', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1080 } } }],
  webServer: [
    {
      command: `"${python}" -m uvicorn app.main:app --app-dir ../backend --host 127.0.0.1 --port 8010`,
      // Never reuse an already-running backend: it may be bound to the runtime
      // database instead of the disposable E2E database.
      url: 'http://127.0.0.1:8010/health', reuseExistingServer: false,
      timeout: 30000,
      env: { PROFITOPS_DATABASE_URL: databaseUrl, LLM_API_KEY: '', LLM_PROVIDER: 'disabled' },
    },
    { command: 'npm run dev', url: 'http://127.0.0.1:5180', reuseExistingServer: !process.env.CI, timeout: 30000 },
  ],
})
