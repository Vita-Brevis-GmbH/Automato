// UI-Tests (E2E) gegen Orchestrator + Vite mit Beispieldaten (e2e/server.py, e2e/seed.py).
// Lokal: `npm run e2e` (startet beide Server selbst). CI-Job `e2e`.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  expect: { timeout: 7_000 },
  retries: process.env.CI ? 1 : 0,
  workers: 1, // gemeinsame Beispieldaten
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://localhost:5173",
    viewport: { width: 1440, height: 950 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: "uv run --package orchestrator python web-ui/frontend/e2e/server.py 8000",
      cwd: "../..",
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: false,
      timeout: 180_000,
    },
    {
      command: "npx vite --port 5173 --strictPort",
      url: "http://localhost:5173",
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
