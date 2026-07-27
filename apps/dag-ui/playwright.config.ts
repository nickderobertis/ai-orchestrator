import { defineConfig } from "@playwright/test";

/**
 * The browser journeys drive the shipped UI against a real `orchestrator/server.py`
 * process, so both halves are started here: the read API over a throwaway recorded
 * run fixture, and the Vite server proxying to it. Both use ports of their own so a
 * run never attaches to — or is confused by — an operator's `just dag-ui` session.
 */
const API_PORT = 8788;
const UI_PORT = 4174;

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: `http://127.0.0.1:${UI_PORT}` },
  webServer: [
    {
      command: `uv run python e2e/fixtures/serve_fixture.py --port ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `bunx vite --config vite.config.ts --port ${UI_PORT} --strictPort`,
      url: `http://127.0.0.1:${UI_PORT}`,
      env: { DAG_UI_API_URL: `http://127.0.0.1:${API_PORT}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
