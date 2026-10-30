import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.PROFITOPS_WEB_PORT ?? 5180),
    strictPort: true,
    proxy: { '/api': process.env.PROFITOPS_API_TARGET ?? 'http://127.0.0.1:8010', '/health': process.env.PROFITOPS_API_TARGET ?? 'http://127.0.0.1:8010' },
  },
  test: {
    environment: 'jsdom', setupFiles: './src/test/setup.ts',
    include: ['src/**/*.test.{ts,tsx}'], css: true,
  },
})
