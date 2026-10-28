// UI-Tests (E2E) gegen Orchestrator + Vite mit Beispieldaten (e2e/server.py, e2e/seed.py).
// Lokal: `npm run e2e` (startet beide Server selbst), schneller `npm run e2e:shards` (mehrere
// Shards gleichzeitig, e2e/shards.mjs). CI-Job `e2e` mit zwei Shards.
import { defineConfig } from "@playwright/test";

// Je Shard eigener Orchestrator + Vite: eigene Ports und eigene SQLite-Datei (E2E_DB, liest
// e2e/server.py) - Shards teilen keine Daten. Ohne Angaben wie bisher 8000/5173.
const apiPort = Number(process.env.E2E_API_PORT || 8000);
const port = Number(process.env.E2E_PORT || 5173);

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  expect: { timeout: 7_000 },
  retries: process.env.CI ? 1 : 0,
  workers: 1, // gemeinsame Beispieldaten (je Shard)
  // Shards teilen je Test (nicht je Datei) auf - ausgeglichen; jeder Test laeuft daher fuer
  // sich allein auf den Beispieldaten, Folgen von Tests nur in `test.describe.serial`
  fullyParallel: true,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://localhost:${port}`,
    viewport: { width: 1440, height: 950 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: `uv run --package orchestrator python web-ui/frontend/e2e/server.py ${apiPort}`,
      cwd: "../..",
      url: `http://127.0.0.1:${apiPort}/health`,
      reuseExistingServer: false,
      timeout: 180_000,
    },
    {
      command: `npx vite --port ${port} --strictPort`,
      url: `http://localhost:${port}`,
      env: { ORCHESTRATOR_URL: `http://127.0.0.1:${apiPort}` }, // Proxy /api (vite.config.ts)
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
