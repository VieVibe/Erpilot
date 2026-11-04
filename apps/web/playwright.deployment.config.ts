import { defineConfig } from '@playwright/test';

// Start a dedicated Compose project first; this suite recreates its API container.
export default defineConfig({
  testDir: './tests/deployment', workers: 1, timeout: 120000,
  expect: { timeout: 15000 }, reporter: 'list',
  outputDir: '../../.playwright-mcp/deployment-tests',
  use: {
    baseURL: process.env.ERPILOT_DEPLOYMENT_URL ?? 'http://127.0.0.1:8768',
    viewport: { width: 1440, height: 1000 },
    actionTimeout: 10000, navigationTimeout: 20000,
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE },
    screenshot: 'only-on-failure',
  },
});
