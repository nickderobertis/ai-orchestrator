import { defineConfig } from "@playwright/test";

/**
 * The browser journeys drive the shipped UI against a real `orchestrator/server.py`
 * process, so both halves are started here: the read API over a throwaway recorded
 * run fixture, and the Vite server proxying to it. Both use ports of their own so a
 * run never attaches to — or is confused by — an operator's `just dag-ui` session.
 */
const API_PORT = 8788;
const UI_PORT = 4174;
/**
 * A second UI origin whose proxy points at a port nothing listens on. It is how the
 * unreachable-API journey reaches the real failure — a real browser making real
 * requests that really fail — without mocking anything.
 */
const OFFLINE_UI_PORT = 4175;
const OFFLINE_API_PORT = 8789;
export const OFFLINE_UI_URL = `http://127.0.0.1:${OFFLINE_UI_PORT}`;
/**
 * Where the fixture server writes the run directory it serves. It is rebuilt on every
 * start and named here rather than hidden in a temporary directory so a journey can
 * change what the server is serving the way an executor would.
 */
export const FIXTURE_WORKSPACE = "e2e/.fixture";

export default defineConfig({
  testDir: "./e2e",
  // One server serves one run directory, and the live-update journeys change what it
  // is serving, so the journeys share that state and must not run against each other.
  workers: 1,
  fullyParallel: false,
  use: { baseURL: `http://127.0.0.1:${UI_PORT}` },
  webServer: [
    {
      command: `uv run python e2e/fixtures/serve_fixture.py --workspace ${FIXTURE_WORKSPACE} --port ${API_PORT}`,
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
    {
      command: `bunx vite --config vite.config.ts --port ${OFFLINE_UI_PORT} --strictPort`,
      url: OFFLINE_UI_URL,
      env: { DAG_UI_API_URL: `http://127.0.0.1:${OFFLINE_API_PORT}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
