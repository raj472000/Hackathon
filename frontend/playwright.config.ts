import { defineConfig, devices } from '@playwright/test'

// End-to-end tests run against the demo stack (mock MCP servers, synthetic data).
// `npm run e2e` starts it with ../scripts/dev.sh unless it is already running.
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173', trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1366, height: 850 } } },
    { name: 'mobile', use: { ...devices['Pixel 7'] }, grep: /@mobile/ },
  ],
  // E2E_BASE_URL points the suite at an already-running demo stack (no server is started then).
  webServer: process.env.E2E_BASE_URL ? undefined : { command: 'DATA_MODE=demo ../scripts/dev.sh', url: 'http://127.0.0.1:5173', reuseExistingServer: true, timeout: 180_000 },
})
