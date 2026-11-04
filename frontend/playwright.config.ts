import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const dataDir = mkdtempSync(join(tmpdir(), 'invoice-desk-e2e-'));
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:18940',
    browserName: 'chromium',
    trace: 'retain-on-failure',
    ...(executablePath ? { launchOptions: { executablePath } } : {}),
  },
  webServer: {
    command: '../.venv/bin/python -m invoice_flow.server --port 18940',
    url: 'http://127.0.0.1:18940/api/v1/healthz',
    reuseExistingServer: false,
    timeout: 30_000,
    env: {
      APP_ENV: 'local', DATA_DIR: dataDir, STORAGE_PERSISTENCE: 'local', MODEL_MODE: 'mock',
      OPENROUTER_API_KEY: '', MODEL_BUDGET_USD: '', ALLOW_LIVE_IN_SESSIONS: '0',
      DEMO_ACCESS_CODE: '', SESSION_SECRET: 'local-browser-test-session-secret', RUN_LIVE_TESTS: '0',
    },
  },
});
