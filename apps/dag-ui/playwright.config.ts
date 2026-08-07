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
 * The choice is made once per *run* rather than once per process: Playwright loads this
 * file again in every worker it forks, and a worker that chose ports of its own would
 * drive servers nobody started. Workers are forked with the runner's environment, so
 * the runner records what it chose there and every later process — worker, teardown —
 * reads that back instead of choosing again.
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
 * The Python program that asks the kernel which ports are free; running it is what
 * produces the six numbers.
 *
 * It binds all six at once so they come back distinct, then releases them together —
 * nothing here reserves anything, because a port has to be free for a server to take
 * it. So the answer is "what was free at that instant", and a run choosing at the same
 * instant could in principle be handed the same number; what that produces is a
 * `--strictPort` Vite or the stall server refusing to start, never a run quietly
 * attached to another run's server. Choosing by arithmetic from a base would not even
 * give that: only the kernel knows what is free.
 */
const FREE_PORTS_SCRIPT = `
import json, socket
held = [socket.socket() for _ in range(6)]
for sock in held:
    sock.bind(("127.0.0.1", 0))
print(json.dumps([sock.getsockname()[1] for sock in held]))
for sock in held:
    sock.close()
`;

function chooseSession(): Session {
  const [api, ui, offlineApi, offlineUi, stalledApi, stalledUi] = z
    .tuple([port, port, port, port, port, port])
    .parse(
      JSON.parse(
        execFileSync("python3", ["-c", FREE_PORTS_SCRIPT], {
          encoding: "utf8",
        }),
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
  const chosen = chooseSession();
  process.env.DAG_UI_E2E_SESSION = JSON.stringify(chosen);
  return chosen;
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
  // The gallery lives beside the journeys because it drives the same surfaces against
  // the same stack, but it asserts nothing and writes images; `screenshots.config.ts`
  // selects it, and `just dag-ui-screens` is when it runs.
  testIgnore: "**/*.screens.spec.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  /**
   * Budgeted for this host rather than inherited. Every wait here crosses the browser,
   * the dev server, uvicorn and a disk read, and this host runs live agent dispatches
   * beside its own tests, which roughly doubles a journey. Playwright's 5 s / 30 s
   * defaults assume a dedicated runner; the sibling configs driving the same servers
   * already budget for that (`isolation.config.ts`, `screenshots.config.ts`).
   *
   * Neither value can make a failing assertion pass — an element that never arrives
   * still fails, 15 s later.
   */
  expect: { timeout: 15_000 },
  timeout: 120_000,
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
