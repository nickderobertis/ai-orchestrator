import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig } from "@playwright/test";
import { z } from "zod";

/**
 * Everything one run of this tier must not share with another: its ports and the
 * fixture directory its servers build.
 *
 * Concurrent worktrees are the normal state on this host, so two runs of this tier
 * overlapping is normal too — and fixed ports plus one shared fixture path made them
 * collide by construction. The second run's `--strictPort` Vite refuses to start on a
 * port the first is holding, and its fixture server rebuilds, from scratch, the very
 * run directory the first run is asserting against.
 *
 * The allocation happens once per *run* rather than once per process: Playwright
 * loads this file again in every worker it forks, and a worker that allocated ports
 * of its own would drive servers nobody started. Workers are forked with the runner's
 * environment, so the runner records what it allocated there and every later process —
 * worker, teardown — reads that back instead of allocating again.
 */
const port = z.number().int().min(1).max(65535);
const sessionSchema = z.object({
  api: port,
  ui: port,
  offlineApi: port,
  offlineUi: port,
  stalledApi: port,
  stalledUi: port,
  workspace: z.string().min(1),
});
type Session = z.infer<typeof sessionSchema>;

/**
 * Ask the kernel for ports nothing else holds, all bound at once so they are distinct,
 * and released together. Choosing them by arithmetic from a base would only move the
 * collision; only the kernel knows which ports are free.
 */
const ALLOCATE_PORTS = `
import json, socket
held = [socket.socket() for _ in range(6)]
for sock in held:
    sock.bind(("127.0.0.1", 0))
print(json.dumps([sock.getsockname()[1] for sock in held]))
for sock in held:
    sock.close()
`;

function allocate(): Session {
  const [api, ui, offlineApi, offlineUi, stalledApi, stalledUi] = z
    .tuple([port, port, port, port, port, port])
    .parse(
      JSON.parse(
        execFileSync("python3", ["-c", ALLOCATE_PORTS], { encoding: "utf8" }),
      ),
    );
  return {
    api,
    ui,
    offlineApi,
    offlineUi,
    stalledApi,
    stalledUi,
    workspace: mkdtempSync(join(tmpdir(), "dag-ui-e2e-fixture-")),
  };
}

function currentSession(): Session {
  const recorded = process.env.DAG_UI_E2E_SESSION;
  if (recorded !== undefined) {
    return sessionSchema.parse(JSON.parse(recorded));
  }
  const allocated = allocate();
  process.env.DAG_UI_E2E_SESSION = JSON.stringify(allocated);
  return allocated;
}

const session = currentSession();

/**
 * A second UI origin whose proxy points at a port that refuses every connection. It is
 * how the unreachable-API journey reaches the real failure — a real browser making real
 * requests that really fail — without mocking anything. The stall server below holds
 * that port bound but unlistened, which is what makes it refuse: merely leaving a port
 * free would let a concurrent run's API server take it, and this journey would quietly
 * be driving a reachable API.
 */
export const OFFLINE_UI_URL = `http://127.0.0.1:${session.offlineUi}`;
/**
 * A third UI origin whose proxy points at a listener that accepts and never answers,
 * so the app's first read stays in flight and its loading view stays on screen long
 * enough for a real browser to observe it.
 */
export const STALLED_UI_URL = `http://127.0.0.1:${session.stalledUi}`;
/**
 * Where the fixture server writes the run directory it serves, rebuilt on every start.
 * A journey needs to name it to change what the server is serving; it is this run's
 * own directory, and it sits outside the checkout, so no tool has to be told to ignore
 * it. `e2e/global-teardown.ts` removes it when the run ends.
 */
export const FIXTURE_WORKSPACE = session.workspace;

export default defineConfig({
  testDir: "./e2e",
  globalTeardown: "./e2e/global-teardown.ts",
  // One server serves one run directory, and the live-update journeys change what it
  // is serving, so the journeys share that state and must not run against each other.
  workers: 1,
  fullyParallel: false,
  use: { baseURL: `http://127.0.0.1:${session.ui}` },
  webServer: [
    {
      command: `uv run python e2e/fixtures/serve_fixture.py --workspace ${FIXTURE_WORKSPACE} --port ${session.api}`,
      url: `http://127.0.0.1:${session.api}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `bunx vite --config vite.config.ts --port ${session.ui} --strictPort`,
      url: `http://127.0.0.1:${session.ui}`,
      env: { DAG_UI_API_URL: `http://127.0.0.1:${session.api}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `uv run python e2e/fixtures/serve_fixture.py --stall --port ${session.stalledApi} --refuse-port ${session.offlineApi}`,
      // Readiness is the accepted connection: this listener answers nothing, by design.
      port: session.stalledApi,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `bunx vite --config vite.config.ts --port ${session.stalledUi} --strictPort`,
      url: STALLED_UI_URL,
      env: { DAG_UI_API_URL: `http://127.0.0.1:${session.stalledApi}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `bunx vite --config vite.config.ts --port ${session.offlineUi} --strictPort`,
      url: OFFLINE_UI_URL,
      env: { DAG_UI_API_URL: `http://127.0.0.1:${session.offlineApi}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
