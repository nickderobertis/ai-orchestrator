import { expect, type Locator, type Page, test } from "@playwright/test";
import { runs } from "./fixture-facts";
import { DESKTOP, PHONE, type Viewport } from "./viewports";

/**
 * Getting around the DAG Observatory: what scrolls, what stays put, and what an
 * address or a back button brings back — at the widest screen in the matrix and at
 * the narrowest.
 *
 * These journeys are about the shell rather than about any one record, and every one
 * of them found something. The app is a fixed-height two-column shell whose regions
 * scroll inside themselves, and that arrangement fails silently: a region that
 * overflows its container does not report anything, it just puts content where no
 * scroll can reach it — off the bottom of the view for a node with a problem banner,
 * off the side of the screen at phone width. Nothing announced either, because the
 * document itself is clipped and cannot scroll to show you.
 *
 * It shares the fixture server with `dag-ui.spec.ts`, whose last journeys deliberately
 * remove the served runs one at a time. Playwright runs files in name order, and this
 * file sorts before that one — which is the reason for the name.
 */

/** The left navigation's own scroll container. */
const navigation = (page: Page): Locator =>
  page.getByRole("navigation", { name: "DAG runs" });
const navigationViewport = (page: Page): Locator =>
  navigation(page).locator("[data-radix-scroll-area-viewport]");

/** The node view's pinned plot, the reading below it, and whatever is opened over it. */
const timeline = (page: Page): Locator =>
  page.getByRole("region", { name: "Node timeline" });
const transcript = (page: Page): Locator =>
  page.getByRole("region", { name: "Node transcript" });
const itemDetail = (page: Page): Locator =>
  page.getByRole("region", { name: "Timeline item detail" });

/** How far down a scroll container has been moved. */
const scrollTop = (locator: Locator): Promise<number> =>
  locator.evaluate((element) => element.scrollTop);

/**
 * The document itself must not scroll.
 *
 * The shell is exactly one viewport tall and every region inside it scrolls on its
 * own, so a document taller than the window means some region has put its content
 * outside the only box that could have scrolled to it — which is how three empty
 * screens ended up under a fifty-run navigation. One pixel of tolerance, because the
 * graph canvas rounds its own height up by one and that is nothing anybody has to
 * reach.
 */
async function expectThePageItselfDoesNotScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollHeight -
      document.documentElement.clientHeight,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

async function open(page: Page, size: Viewport, path: string): Promise<void> {
  await page.setViewportSize({ width: size.width, height: size.height });
  await page.goto(path);
  await expect(page.getByText("DAG Observatory")).toBeVisible();
}

for (const size of [DESKTOP, PHONE]) {
  test(`scrolls the run list and pages the next runs in at ${size.name}`, async ({
    page,
  }) => {
    await open(page, size, `/?run=${runs().live}&view=graph`);
    const links = navigation(page).locator(".run-link");
    await expect(links).toHaveCount(50);
    // The run this reader is on is legible rather than ellipsized away: a list no run
    // can be identified from is not a navigation, at any width. At 160px the id shares
    // its row with a state pill and a chevron, which left `dag-ui-live` reading `d..`.
    const identifier = navigation(page)
      .getByRole("button", { name: RegExp(runs().live) })
      .locator(".run-link-main span:last-child");
    await expect(identifier).toHaveText(runs().live);
    expect(
      await identifier.evaluate(
        (element) => element.scrollWidth - element.clientWidth,
      ),
    ).toBeLessThanOrEqual(1);

    // Reaching the end of the list is what asks the server for the next page, so the
    // list has to be able to reach its end in the first place.
    await navigationViewport(page).hover();
    await page.mouse.wheel(0, 10_000);
    await expect
      .poll(() => scrollTop(navigationViewport(page)))
      .toBeGreaterThan(0);
    await expect(links).toHaveCount(52);
    // The rows that just arrived are reachable by the same scroll that asked for them.
    await page.mouse.wheel(0, 10_000);
    await expect(links.last()).toBeInViewport();

    // And none of that moved the page: the list scrolled inside its own container.
    await expectThePageItselfDoesNotScroll(page);
  });
}

test("scrolls the working area without moving the run list", async ({
  page,
}) => {
  // A viewport short enough that the run's own reading overflows it, which is the
  // state the two regions have to scroll independently in.
  await open(page, DESKTOP, `/?run=${runs().live}&view=overall`);
  await page.setViewportSize({ width: 1024, height: 700 });
  await expect(page.getByText("Run timeline")).toBeVisible();
  const workspace = page
    .locator(".overall-view [data-radix-scroll-area-viewport]")
    .first();
  await expect
    .poll(() =>
      workspace.evaluate(
        (element) => element.scrollHeight - element.clientHeight,
      ),
    )
    .toBeGreaterThan(0);

  const navigationBefore = await scrollTop(navigationViewport(page));
  await workspace.hover();
  await page.mouse.wheel(0, 600);
  await expect.poll(() => scrollTop(workspace)).toBeGreaterThan(0);
  // The run list did not come along for the ride.
  expect(await scrollTop(navigationViewport(page))).toBe(navigationBefore);

  const workspaceAt = await scrollTop(workspace);
  await navigationViewport(page).hover();
  await page.mouse.wheel(0, 600);
  await expect
    .poll(() => scrollTop(navigationViewport(page)))
    .toBeGreaterThan(0);
  // And the reading stayed where the reader left it.
  expect(await scrollTop(workspace)).toBe(workspaceAt);
  await expectThePageItselfDoesNotScroll(page);
});

test("keeps a node the run reported a problem on readable", async ({
  page,
}) => {
  // A node with a banner has one child more than a healthy one. The view used to be a
  // fixed list of rows over a `display: contents` tab set, so that extra child moved
  // everything below it down a row: the tab strip took the flexible row and stretched,
  // and the recorded timeline landed past the bottom of a shell that never scrolls.
  await open(page, DESKTOP, `/?run=${runs().live}&node=missing-artifact`);
  await expect(page.getByRole("alert")).toContainText("This node failed");

  const panel = page.locator(".node-timeline-panel");
  await expect(panel).toBeInViewport({ ratio: 1 });
  await expect(timeline(page)).toBeInViewport({ ratio: 1 });
  await expect(transcript(page)).toBeInViewport({ ratio: 1 });
  // The panel begins where the tab strip ends rather than a stretched row below it.
  const tabs = await page.getByRole("tab", { name: "Timeline" }).boundingBox();
  const opened = await panel.boundingBox();
  expect((opened?.y ?? 0) - (tabs?.y ?? 0)).toBeLessThan(60);
  await expectThePageItselfDoesNotScroll(page);

  // What it records is reachable, which is the whole point of the region being there.
  await timeline(page)
    .getByRole("button", { name: /missing verification log/ })
    .click();
  await expect(itemDetail(page)).toContainText("Verification record");
});

test("walks graph to node to timeline item and back, restoring each selection", async ({
  page,
}) => {
  await open(page, DESKTOP, `/?run=${runs().live}&view=graph`);
  await page.locator(".dag-node.state-running").click();
  await expect(
    page.getByRole("region", { name: "Timeline for dashboard" }),
  ).toBeVisible();
  await timeline(page)
    .getByRole("button", { name: /engineer-dashboard/ })
    .click();
  await expect(itemDetail(page)).toContainText(
    "Implementing the dashboard now",
  );
  await expect(page).toHaveURL(/event=dispatch-worker-session/);

  // Back once leaves the item and keeps the node: the reader stepped out of one
  // moment of this node's execution, not out of the node. The panel that carried it
  // is gone, and the reading it was opened from is what is left on screen.
  await page.goBack();
  await expect(page).not.toHaveURL(/event=/);
  await expect(
    page.getByRole("region", { name: "Timeline for dashboard" }),
  ).toBeVisible();
  await expect(itemDetail(page)).toHaveCount(0);
  await expect(transcript(page)).toBeVisible();

  // Back again leaves the node for the graph it was opened from.
  await page.goBack();
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "dashboard",
  );
  await expect(page).toHaveURL(/view=graph/);
  await expect(page).not.toHaveURL(/node=/);

  // And forward retraces it, moment included.
  await page.goForward();
  await expect(
    page.getByRole("region", { name: "Timeline for dashboard" }),
  ).toBeVisible();
  await page.goForward();
  await expect(itemDetail(page)).toContainText(
    "Implementing the dashboard now",
  );
});

test("restores a deep-linked moment at a narrow viewport", async ({ page }) => {
  // The address names a run, a node and one recorded moment of it. At this width the
  // shell's two columns leave a working area 230px wide, and two thirds of that is
  // narrower than any turn can be read in — so the panel the address was pointing at
  // takes the screen here rather than a share of it, and the reading it covers is one
  // Escape away.
  await open(
    page,
    PHONE,
    `/?run=${runs().live}&node=dashboard&event=dispatch-worker-session`,
  );
  await expect(
    page.getByRole("region", { name: "Timeline for dashboard" }),
  ).toBeVisible();
  await expect(itemDetail(page)).toContainText(
    "Implementing the dashboard now",
  );
  await expect(itemDetail(page)).toBeInViewport({ ratio: 1 });
  expect(
    (await page.getByLabel("Item detail panel").boundingBox())?.width,
  ).toBe(PHONE.width);
  await page.keyboard.press("Escape");
  await expect(itemDetail(page)).toHaveCount(0);
  await expect(timeline(page)).toBeInViewport({ ratio: 1 });
  await expect(transcript(page)).toBeInViewport({ ratio: 1 });
  // Every tab of the node is reachable too: the strip scrolls rather than setting a
  // width the region around it cannot afford.
  await expect(page.getByRole("tab", { name: "Timeline" })).toBeInViewport();
  await page.getByRole("tab", { name: "Checks" }).click();
  await expect(page.locator(".facts")).toContainText("Verification coverage");
  await expectThePageItselfDoesNotScroll(page);
});

for (const size of [DESKTOP, PHONE]) {
  test(`leaves the node view under Escape at ${size.name}`, async ({
    page,
  }) => {
    await open(page, size, `/?run=${runs().live}&node=dashboard`);
    await timeline(page)
      .getByRole("button", { name: /engineer-dashboard/ })
      .click();
    await expect(itemDetail(page)).toContainText(
      "Implementing the dashboard now",
    );

    // Escape closes what is open over the reading before it leaves the reading: one
    // press puts the panel away, the next returns to the graph the node was opened
    // from.
    await page.keyboard.press("Escape");
    await expect(itemDetail(page)).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(page.locator(".dag-node.state-running")).toContainText(
      "dashboard",
    );
    await expect(
      page.getByRole("region", { name: "Timeline for dashboard" }),
    ).toHaveCount(0);
    await expectThePageItselfDoesNotScroll(page);
  });
}
