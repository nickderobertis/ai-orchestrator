import { defineConfig } from "@playwright/test";

/**
 * The one check that runs the browser tier rather than being run by it.
 *
 * `playwright.config.ts` allocates that tier's ports and fixture directory per run, and
 * every ordinary run exercises the allocation; only two overlapping runs exercise what
 * it is for. So this config starts no servers and opens no browser — its spec launches
 * two real runs of the other config at once and asserts they stayed out of each other's
 * way. It is deliberately a separate config: a spec living under the tier's own
 * `testDir` would inherit the environment recording that run's allocation, and the runs
 * it launched would reuse it instead of allocating their own.
 */
export default defineConfig({
  testDir: "./e2e-isolation",
  workers: 1,
  // Two whole runs of the browser tier, each starting five servers of its own.
  timeout: 900_000,
});
