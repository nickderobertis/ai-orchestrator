import { tmpdir } from "node:os";
import { join } from "node:path";
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
 * A third UI origin whose proxy points at a listener that accepts and never answers,
 * so the app's first read stays in flight and its loading view stays on screen long
 * enough for a real browser to observe it.
 */
const STALLED_UI_PORT = 4176;
const STALLED_API_PORT = 8790;
export const STALLED_UI_URL = `http://127.0.0.1:${STALLED_UI_PORT}`;
/**
 * Where the fixture server writes the run directory it serves, rebuilt on every start.
 * A journey needs to name it to change what the server is serving, so it is a fixed
 * path rather than a random one — and it sits outside the checkout, so no tool has to
 * be told to ignore it.
 */
export const FIXTURE_WORKSPACE = join(tmpdir(), "dag-ui-e2e-fixture");

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
    {
      command: `uv run python e2e/fixtures/serve_fixture.py --stall --port ${STALLED_API_PORT}`,
      // Readiness is the accepted connection: this listener answers nothing, by design.
      port: STALLED_API_PORT,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `bunx vite --config vite.config.ts --port ${STALLED_UI_PORT} --strictPort`,
      url: STALLED_UI_URL,
      env: { DAG_UI_API_URL: `http://127.0.0.1:${STALLED_API_PORT}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
