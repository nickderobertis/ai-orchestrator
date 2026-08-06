import { defineConfig } from "@playwright/test";
import e2eConfig from "./playwright.config";

/**
 * The screenshot tier: the same stack the browser e2e boots, driven to capture rather
 * than to assert.
 *
 * It reuses `playwright.config.ts`'s configuration object wholesale rather than
 * describing servers of its own. That is the point — everything two concurrent runs
 * must not share is chosen there, per run, by asking the kernel for free ports and
 * making a fixture directory of its own, and a gallery config that restated any of it
 * would reintroduce exactly the fixed ports and shared fixture path that choice
 * replaced. Importing the module runs that choice, so `just dag-ui-screens` twice at
 * once is two independent stacks.
 *
 * Only the file selection is inverted: the e2e tier ignores `*.screens.spec.ts` and
 * this config runs nothing else. The spread carries that `testIgnore` along, so it is
 * cleared here — without which this tier would select the gallery and then ignore it.
 */
export default defineConfig({
  ...e2eConfig,
  testMatch: "**/*.screens.spec.ts",
  testIgnore: [],
  // Five viewports across six surfaces, each waiting for real reads to settle.
  timeout: 120_000,
});
