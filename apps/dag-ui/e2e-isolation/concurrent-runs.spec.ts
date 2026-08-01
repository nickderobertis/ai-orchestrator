import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { promisify } from "node:util";
import { expect, test } from "@playwright/test";

/**
 * Two runs of the browser tier at once, which is the state this host is normally in:
 * concurrent worktrees, each running its own. Fixed ports and one shared fixture path
 * made that collide by construction — a `--strictPort` Vite refusing a port the other
 * run holds, and a fixture server rebuilding the run directory the other run is
 * asserting against — so the collision is the journey, and it takes two real runs.
 */

const run = promisify(execFile);

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

function workspacesOf(output: string): string[] {
  return [...output.matchAll(REMOVED)].flatMap(([, directory]) =>
    directory === undefined ? [] : [directory],
  );
}

test("two runs of the browser tier at once stay out of each other's way", async () => {
  const [first, second] = await Promise.all([
    run("bunx", [
      "playwright",
      "test",
      "--config",
      "playwright.config.ts",
      "--grep",
      JOURNEYS,
    ]),
    run("bunx", [
      "playwright",
      "test",
      "--config",
      "playwright.config.ts",
      "--grep",
      JOURNEYS,
    ]),
  ]);

  // Each run built, served, and then removed a fixture directory of its own. Both runs
  // getting here at all is the port half: a shared port fails the run outright.
  const directories = [workspacesOf(first.stdout), workspacesOf(second.stdout)];
  expect(directories.map((found) => found.length)).toEqual([1, 1]);
  expect(directories[0]).not.toEqual(directories[1]);
  for (const found of directories.flat()) {
    expect(existsSync(found)).toBe(false);
  }
});
