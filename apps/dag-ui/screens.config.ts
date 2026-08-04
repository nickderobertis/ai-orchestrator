import { defineConfig } from "@playwright/test";
import browserTier from "./playwright.config";

/**
 * The screenshot gallery: the same application, the same fixture servers, captured
 * rather than asserted.
 *
 * It reuses the browser tier's own configuration — its per-run ports and fixture
 * directory included — so the gallery is a picture of what the journeys drive and
 * never of a second, differently built application. Only the specs differ, which is
 * why this is a separate config: a gallery spec living under `e2e/` would run inside
 * `just check` and spend its time producing images nothing asserts on.
 */
export default defineConfig({
  ...browserTier,
  testDir: "./e2e-screens",
  globalTeardown: undefined,
});
