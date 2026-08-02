import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { expect, test } from "@playwright/test";

/**
 * What one run of the browser tier owes another, proven by running the tier itself.
 *
 * Two runs at once is the state this host is normally in — concurrent worktrees, each
 * running its own — and fixed ports with one shared fixture path made that collide by
 * construction: a `--strictPort` Vite refusing a port the other run holds, and a
 * fixture server rebuilding the run directory the other run is asserting against. The
 * other half of that bargain is giving the directory back, including when the run
 * fails, which is when a tier that leaks one leaks one per failure.
 */

/**
 * The two journeys that touch what the runs would otherwise share. Starting the servers
 * is where the ports collide, and every server starts whatever the filter selects, so
 * this narrows to what the *fixture directory* needs: "drops a run the server stops
 * serving" takes a run out of the directory its own run is being served, which a shared
 * directory turns into the other run's fixture changing underneath it, and "surfaces a
 * telemetry read it cannot complete" needs its unreachable API to stay unreachable,
 * which is the port the stall server holds bound for exactly that reason.
 */
const JOURNEYS =
  "drops a run the server stops serving|surfaces a telemetry read it cannot complete";

/** What `e2e/global-teardown.ts` says on its way out, naming the directory it built. */
const REMOVED = /dag-ui e2e: removed fixture workspace (\S+)/g;

interface TierRun {
  ok: boolean;
  output: string;
}

/** One real run of `playwright.config.ts`, reported rather than thrown. */
function runTier(...extra: string[]): Promise<TierRun> {
  return new Promise((resolve) => {
    execFile(
      "bunx",
      [
        "playwright",
        "test",
        "--config",
        "playwright.config.ts",
        "--grep",
        JOURNEYS,
        ...extra,
      ],
      (error, stdout, stderr) => {
        resolve({ ok: error === null, output: stdout + stderr });
      },
    );
  });
}

function workspacesOf(run: TierRun): string[] {
  return [...run.output.matchAll(REMOVED)].flatMap(([, directory]) =>
    directory === undefined ? [] : [directory],
  );
}

test("two runs of the browser tier at once stay out of each other's way", async () => {
  const [first, second] = await Promise.all([runTier(), runTier()]);

  for (const run of [first, second]) {
    expect(run.ok, run.output).toBe(true);
  }
  // Each run built, served, and then removed a fixture directory of its own. Both runs
  // getting here at all is the port half: a shared port fails the run outright.
  const directories = [workspacesOf(first), workspacesOf(second)];
  expect(directories.map((found) => found.length)).toEqual([1, 1]);
  expect(directories[0]).not.toEqual(directories[1]);
  for (const found of directories.flat()) {
    expect(existsSync(found)).toBe(false);
  }
});

test("a run that fails still gives its fixture directory back", async () => {
  // One millisecond is less than any journey takes, so every selected test fails and
  // none of them fails for a reason of its own. What has to survive that is the
  // teardown: a tier that only cleans up after a green run leaks a directory per red
  // one, and red is exactly when a run is repeated.
  const failed = await runTier("--timeout", "1");

  expect(failed.ok).toBe(false);
  const [directory, ...extra] = workspacesOf(failed);
  expect(extra).toEqual([]);
  expect(directory).toBeDefined();
  expect(existsSync(String(directory))).toBe(false);
});
