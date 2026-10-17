import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';

const root = resolve('../..');
const python = process.env.ERPILOT_DEMO_PYTHON ?? resolve(root,
  process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');

export default defineConfig({
  testDir: './tests/demo', workers: 1, timeout: 30000,
  expect: { timeout: 10000 }, reporter: 'list',
  outputDir: '../../.playwright-mcp/demo-tests',
  use: {
    baseURL: 'http://127.0.0.1:8766', viewport: { width: 1440, height: 1000 },
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE },
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: `"${python}" scripts/demo.py --serve --port 8766`, cwd: root,
    url: 'http://127.0.0.1:8766/healthz', timeout: 30000, reuseExistingServer: false,
  },
});
