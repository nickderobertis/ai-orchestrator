import { mkdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, type Page, test } from "@playwright/test";
import { z } from "zod";
import { FIXTURE_WORKSPACE } from "../playwright.config";

/**
 * The node view, photographed at every width the application supports.
 *
 * This asserts almost nothing on purpose: its product is the images, which a
 * reviewer reads for clipping, overlap and reflow that no selector describes. It
 * drives the same fixture servers the browser tier drives, so what it photographs is
 * the application under real served data rather than a storybook of it.
 */

/** The widths the layout is designed against, from a wide desktop down to a laptop. */
const VIEWPORTS = [
  { name: "1-desktop-wide", width: 1920, height: 1080 },
  { name: "2-desktop", width: 1600, height: 1000 },
  { name: "3-laptop", width: 1400, height: 900 },
  { name: "4-laptop-narrow", width: 1100, height: 800 },
  // Below the layout's 900px breakpoint, where the navigation and rail give up width.
  { name: "5-compact", width: 800, height: 700 },
] as const;

const GALLERY = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  ".screens",
  "node-view",
);

const liveRun = (): string =>
  z
    .object({ runs: z.object({ live: z.string().min(1) }) })
    .parse(
      JSON.parse(
        readFileSync(join(FIXTURE_WORKSPACE, "fixture-facts.json"), "utf8"),
      ),
    ).runs.live;

test.beforeAll(() => {
  rmSync(GALLERY, { force: true, recursive: true });
  mkdirSync(GALLERY, { recursive: true });
});

async function openNode(page: Page): Promise<void> {
  await page.goto(`/?run=${liveRun()}&node=dashboard`);
  await expect(
    page.getByRole("region", { name: "Node transcript" }),
  ).toBeVisible();
}

for (const viewport of VIEWPORTS) {
  test(`node view at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    await openNode(page);
    await page.screenshot({
      path: join(GALLERY, `${viewport.name}-collapsed.png`),
    });

    await page
      .getByRole("region", { name: "Node timeline" })
      .getByRole("button", { name: "Expand timeline" })
      .click();
    await page.screenshot({
      path: join(GALLERY, `${viewport.name}-expanded.png`),
    });

    await page
      .getByRole("region", { name: "Node transcript" })
      .getByRole("button", { name: /^Open Judge/ })
      .click();
    const panel = page.getByRole("region", { name: "Timeline item detail" });
    await expect(panel).toBeVisible();
    // Photographed with the conversation loaded, not with its skeleton on screen.
    await expect(panel.getByRole("article", { name: /^Turn / })).toBeVisible();
    await page.screenshot({
      path: join(GALLERY, `${viewport.name}-conversation-panel.png`),
    });
  });
}
