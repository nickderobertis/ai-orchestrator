import { expect, type Locator, type Page, test } from "@playwright/test";
import { runs } from "./fixture-facts";
import { DESKTOP, PHONE, VIEWPORTS, type Viewport } from "./viewports";

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

/** How far the tab path is walked before a control counts as unreachable by keyboard. */
const TAB_STOPS = 250;

/**
 * Walk the tab path from the top of the document until `target` has focus.
 *
 * Bounded, and it fails saying so: a control that cannot be reached this way is one a
 * reader without a pointer cannot reach at all, which is worth failing on rather than
 * working around with a direct `focus()`.
 */
async function tabTo(page: Page, target: Locator): Promise<void> {
  await page.locator("body").press("Tab");
  for (let pressed = 1; pressed <= TAB_STOPS; pressed += 1) {
    if (await target.evaluate((element) => element === document.activeElement))
      return;
    await page.keyboard.press("Tab");
  }
  throw new Error(
    `the target was not on the tab path within ${TAB_STOPS} stops`,
  );
}

/**
 * Open the app at `size` and wait until it is holding runs, not merely mounted.
 *
 * The `DAG Observatory` heading is a sibling of the run list rather than anything
 * rendered from it, so it paints on mount and says nothing about the run-list read.
 * Readiness is a loaded run link. This also settles the apparent width dependence: the
 * loop's first viewport races the dev server's initial transform, whichever one it is.
 */
async function open(page: Page, size: Viewport, path: string): Promise<void> {
  await page.setViewportSize({ width: size.width, height: size.height });
  await page.goto(path);
  await expect(navigation(page).locator(".run-link").first()).toBeVisible();
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
  await expect(page.getByText("Graph timeline")).toBeVisible();
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
  test(`draws the whole graph inside its canvas at ${size.name}`, async ({
    page,
  }) => {
    // Both extremes: the `minZoom` floor that has to be low enough for the narrow one
    // must not change what the wide one draws.
    await open(page, size, `/?run=${runs().live}&view=graph`);
    const canvas = page.getByLabel("DAG execution graph");
    await expect(page.locator(".dag-node").first()).toBeVisible();
    const pane = await canvas.boundingBox();
    const cards = page.locator(".dag-node");
    const count = await cards.count();
    expect(count).toBeGreaterThan(1);
    for (let index = 0; index < count; index += 1) {
      const card = await cards.nth(index).boundingBox();
      expect(card?.x).toBeGreaterThanOrEqual((pane?.x ?? 0) - 1);
      expect((card?.x ?? 0) + (card?.width ?? 0)).toBeLessThanOrEqual(
        (pane?.x ?? 0) + (pane?.width ?? 0) + 1,
      );
      // Both axes: a zoom floor that fits the width and not the height leaves the
      // deepest ranks of the graph off the bottom of the canvas instead of the side.
      expect(card?.y).toBeGreaterThanOrEqual((pane?.y ?? 0) - 1);
      expect((card?.y ?? 0) + (card?.height ?? 0)).toBeLessThanOrEqual(
        (pane?.y ?? 0) + (pane?.height ?? 0) + 1,
      );
    }

    // The lowered floor is also how far the reader may now zoom out by hand, so the
    // control is driven to it: the button stops rather than running away, and what it
    // stops at still holds the graph.
    const out = page.locator(".react-flow__controls-zoomout");
    for (let click = 0; click < 30; click += 1) {
      if (await out.isDisabled()) break;
      await out.click();
    }
    await expect(out).toBeDisabled();
    const zoomed = await cards.first().boundingBox();
    expect(zoomed?.width ?? 0).toBeGreaterThan(0);
    expect(zoomed?.x).toBeGreaterThanOrEqual((pane?.x ?? 0) - 1);
    await expectThePageItselfDoesNotScroll(page);
  });
}

test("keeps a long recorded label inside the node transcript at the phone", async ({
  page,
}) => {
  // `foundation` is the node whose labels carry a branch name: one unbreakable word
  // wider than this reading, which is what sizes the transcript's grid column.
  await open(page, PHONE, `/?run=${runs().live}&node=foundation`);
  const region = transcript(page);
  await expect(region.getByRole("article").first()).toBeVisible();
  await expect(
    region.getByRole("article", { name: /branch push/ }),
  ).toBeVisible();
  const bounds = await region.boundingBox();
  const items = region.getByRole("article");
  const count = await items.count();
  expect(count).toBeGreaterThan(1);
  for (let index = 0; index < count; index += 1) {
    const item = await items.nth(index).boundingBox();
    expect((item?.x ?? 0) + (item?.width ?? 0)).toBeLessThanOrEqual(
      (bounds?.x ?? 0) + (bounds?.width ?? 0) + 1,
    );
  }
  expect(
    await region.evaluate(
      (element) => element.scrollWidth - element.clientWidth,
    ),
  ).toBeLessThanOrEqual(1);
  await expectThePageItselfDoesNotScroll(page);
});

/**
 * A hovered segment's reading, whole, at every width in the matrix.
 *
 * The package paints that reading inside its own `overflow-hidden` plot at a fixed
 * offset below the lane row, so any plot shorter than the reading cut its bottom off —
 * which is every collapsed line and every graph row, at every one of these widths. The
 * cut is why this is held at all five rather than at the two extremes: it was never a
 * narrow-screen problem, and a fix that only kept the phone whole would have left the
 * desktop reading truncated exactly as it was.
 */
for (const size of VIEWPORTS) {
  test(`reads a hovered segment whole at ${size.name}`, async ({ page }) => {
    await open(page, size, `/?run=${runs().live}&node=foundation`);
    const segment = timeline(page).getByRole("button", { name: /branch push/ });
    await segment.hover();

    const reading = page.getByTestId("timeline-popover");
    await expect(reading).toBeInViewport({ ratio: 1 });
    const box = await reading.boundingBox();
    expect(box?.x).toBeGreaterThanOrEqual(0);
    expect(box?.y).toBeGreaterThanOrEqual(0);
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(size.width);
    expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(size.height);
    expect(
      await reading.evaluate(
        (element) => element.scrollHeight - element.clientHeight,
      ),
    ).toBeLessThanOrEqual(1);

    // And it is the description the segment names, not a second account of it: the
    // reading ends where the served record ends.
    await expect(reading).toContainText("Lane: verification");
    await expect(reading).toContainText("Duration:");
    expect(await reading.textContent()).toBe(
      (await page.getByRole("tooltip").textContent())?.trim(),
    );

    await page.getByRole("region", { name: "Node transcript" }).hover();
    await expect(reading).toHaveCount(0);

    // And so does the segment itself going away under a reading nobody dismissed:
    // Escape leaves the node view without the pointer moving, so the plot the reading
    // is anchored to is unmounted while it is open.
    await segment.hover();
    await expect(reading).toBeInViewport({ ratio: 1 });
    await page.keyboard.press("Escape");
    await expect(page.locator(".dag-node").first()).toBeVisible();
    await expect(reading).toHaveCount(0);
    await expectThePageItselfDoesNotScroll(page);
  });
}

test("ends the reading when the pointer leaves the document", async ({
  page,
}) => {
  // The other way a pointer stops being over a segment, and the one no other journey
  // here reaches: they all dismiss by moving the pointer *onto* something that is not
  // a segment, which is `pointerover` doing the closing. A pointer leaving the window
  // arrives nowhere, so it fires no `pointerover` at all — the document's own
  // `pointerleave` is the only thing that hears it, and without it the reading stays
  // painted over an app the pointer is no longer in.
  await open(page, DESKTOP, `/?run=${runs().live}&node=foundation`);
  const segment = timeline(page).getByRole("button", { name: /branch push/ });
  await segment.hover();
  const reading = page.getByTestId("timeline-popover");
  await expect(reading).toContainText("Lane: verification");

  // Out through the top edge, to a point the document does not cover — the pointer
  // leaving the window, not moving to another part of it.
  await page.mouse.move(Math.round(DESKTOP.width / 2), -5);
  await expect(reading).toHaveCount(0);

  // And it takes only the pointed-at reading with it. Someone who tabbed to a segment
  // and then took the pointer off the window keeps that segment's reading: the two are
  // tracked apart precisely so the pointer leaving does not answer for the keyboard.
  await tabTo(page, segment);
  await expect(reading).toContainText("Lane: verification");
  await timeline(page)
    .getByRole("button", { name: /^Publication/ })
    .hover();
  await expect(reading).toContainText("Lane: publication");

  await page.mouse.move(Math.round(DESKTOP.width / 2), -5);
  await expect(segment).toBeFocused();
  await expect(reading).toContainText("Lane: verification");
});

test("reads a segment of the graph timeline, and follows it as the view scrolls", async ({
  page,
}) => {
  // The layer is mounted once for every plot on every view, so the reading is proved
  // on the overall view's own rows as well as on a node's: those plots sit inside a
  // scroll area rather than a pinned region, which is the other clipping stack it was
  // moved out of, and the one where the segment moves under the reader.
  await open(page, DESKTOP, `/?run=${runs().live}&view=overall`);
  await page
    .getByRole("region", { name: "Graph timeline" })
    .getByRole("button", { name: "Expand timeline" })
    .click();
  const row = page.getByRole("region", { name: "Run-level timeline" });
  const segment = row.getByRole("button", {
    name: /^Run-level · Orchestrator/,
  });
  await segment.hover();

  const reading = page.getByTestId("timeline-popover");
  await expect(reading).toBeInViewport({ ratio: 1 });
  await expect(reading).toContainText("Lane: orchestrator");

  // Held by focus for the scroll, because the pointer is about to leave the segment:
  // a reader scrolls by putting the pointer over the reading and turning the wheel,
  // which is exactly the case where conflating pointed-at with focused takes the
  // reading away from someone who never asked for it to go. It follows the segment
  // rather than being left behind pointing at nothing.
  await tabTo(page, segment);
  const before = await reading.boundingBox();
  const anchoredAt = await segment.boundingBox();
  await page.locator(".overall-hero").hover();
  await page.mouse.wheel(0, 120);
  await expect
    .poll(async () => (await reading.boundingBox())?.y)
    .not.toBe(before?.y);
  const moved = await reading.boundingBox();
  const movedAnchor = await segment.boundingBox();
  expect(Math.round((moved?.y ?? 0) - (before?.y ?? 0))).toBe(
    Math.round((movedAnchor?.y ?? 0) - (anchoredAt?.y ?? 0)),
  );
  await expect(reading).toBeInViewport({ ratio: 1 });

  // A window that narrows under an open reading re-places it rather than leaving it
  // hanging off the new edge: the clamp is computed from the viewport, so it has to be
  // recomputed when the viewport is what moved.
  await page.setViewportSize({ width: 900, height: 700 });
  await expect
    .poll(async () => (await reading.boundingBox())?.x)
    .not.toBe(moved?.x);
  const resized = await reading.boundingBox();
  expect((resized?.x ?? 0) + (resized?.width ?? 0)).toBeLessThanOrEqual(900);
  await expect(reading).toBeInViewport({ ratio: 1 });
  await expectThePageItselfDoesNotScroll(page);
});

test("reads a segment the keyboard reached", async ({ page }) => {
  // Tabbed to from the top of the document, with no pointer involved at all — which
  // proves both halves at once: that a plot's segments are on the tab path in the
  // first place, and that arriving at one by keyboard states what it is.
  await open(page, DESKTOP, `/?run=${runs().live}&node=foundation`);
  const segment = timeline(page).getByRole("button", { name: /branch push/ });
  await tabTo(page, segment);

  const reading = page.getByTestId("timeline-popover");
  await expect(reading).toBeInViewport({ ratio: 1 });
  await expect(reading).toContainText("Lane: verification");
  const first = await reading.textContent();

  expect(
    await segment.evaluate(
      (element) =>
        document.getElementById(element.getAttribute("aria-describedby") ?? "")
          ?.textContent,
    ),
  ).toBe(first);
  await expect(reading).toHaveAttribute("aria-hidden", "true");

  // The described element is still in the accessibility tree — it is the one the
  // assertion above just read through `aria-describedby` — and off the painted one.
  // Both halves matter and neither is implied by the other: a stylesheet selector that
  // stopped matching the package's copy would put two readings on screen at once, and
  // every other assertion in this file would still pass; one that hid it from
  // assistive technology instead would take the description away entirely.
  const described = await page.getByRole("tooltip").evaluate((element) => {
    const box = element.getBoundingClientRect();
    return {
      clipped: getComputedStyle(element).clipPath,
      height: box.height,
      width: box.width,
    };
  });
  expect(described.clipped).not.toBe("none");
  // Its box floors at the padding the package puts on it, so the reading to compare
  // against is the one it would paint: 20rem of it, which is what the portal beside it
  // is. Anything near that width is the package's copy painted a second time.
  expect(described.width).toBeLessThan(
    ((await reading.boundingBox())?.width ?? 0) / 4,
  );

  // Pointed at and focused at once, on two different segments. The one under the
  // pointer wins, because that is the one the reader is asking about — and the focused
  // one is not lost with it: taking the pointer away again goes back to that reading
  // rather than to none, which is what tracking the two apart buys.
  const publication = timeline(page).getByRole("button", {
    name: /^Publication/,
  });
  await publication.hover();
  await expect(segment).toBeFocused();
  await expect(reading).toContainText("Lane: publication");
  await transcript(page).hover();
  await expect(reading).toHaveText(first ?? "");

  // Tabbing on takes the reading with them: the next stop is the next segment of the
  // same plot, and the reading is that one's, whole, with no pointer near either.
  await page.keyboard.press("Tab");
  await expect(segment).not.toBeFocused();
  await expect(reading).not.toHaveText(first ?? "");
  await expect(reading).toBeInViewport({ ratio: 1 });

  // Tabbing out of the plot ends it: focus reaching something that is not a segment is
  // what closes the reading, with no pointer involved in that either.
  for (let stop = 0; stop < TAB_STOPS && (await reading.count()) > 0; stop += 1)
    await page.keyboard.press("Tab");
  await expect(reading).toHaveCount(0);
});

test("puts the reading above a segment with no room below it", async ({
  page,
}) => {
  // The other placement. A reading is about 90px tall, so "no room below" is made
  // rather than hoped for: the segment is held by focus and the window is then shrunk
  // to end just under it, which is the same state as a plot sitting at the foot of a
  // screen and is reached without the pointer moving off the segment.
  await open(page, DESKTOP, `/?run=${runs().live}&view=overall`);
  await page
    .getByRole("region", { name: "Graph timeline" })
    .getByRole("button", { name: "Expand timeline" })
    .click();
  const segment = page
    .getByRole("region", { name: "Run-level timeline" })
    .getByRole("button", { name: /^Run-level · Orchestrator/ });
  await tabTo(page, segment);
  const reading = page.getByTestId("timeline-popover");
  await expect(reading).toBeInViewport({ ratio: 1 });

  const anchor = await segment.boundingBox();
  const shortened = Math.round((anchor?.y ?? 0) + (anchor?.height ?? 0) + 30);
  await page.setViewportSize({ width: 1024, height: shortened });
  await expect(reading).toBeInViewport({ ratio: 1 });

  const box = await reading.boundingBox();
  const moved = await segment.boundingBox();
  expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(
    (moved?.y ?? 0) + 1,
  );
  expect(box?.y).toBeGreaterThanOrEqual(0);
  expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(shortened);
});

test("reads a segment of an opened conversation's own timeline", async ({
  page,
}) => {
  // The third scope, and the one furthest inside the clipping stack: the conversation
  // plot is drawn by the same component, inside a panel that clips, inside a scroll
  // area that clips. One layer at the app root is what makes all three the same
  // reading.
  await open(page, DESKTOP, `/?run=${runs().live}&node=dashboard`);
  await transcript(page)
    .getByRole("button", { name: /^Open Judge/ })
    .click();
  const conversation = itemDetail(page).getByRole("region", {
    name: "Conversation timeline",
  });
  await expect(conversation).toBeVisible();
  await conversation.locator("[data-timeline-shape]").first().hover();

  const reading = page.getByTestId("timeline-popover");
  await expect(reading).toBeInViewport({ ratio: 1 });
  await expect(reading).toContainText("Status:");
  expect(
    await reading.evaluate(
      (element) => element.scrollHeight - element.clientHeight,
    ),
  ).toBeLessThanOrEqual(1);
});

test("gives a lone recorded term the whole fact row", async ({ page }) => {
  // The list shows its own border colour through the gaps between cells, so an empty
  // cell is a filled panel with nothing written on it rather than blank space.
  await open(page, DESKTOP, `/?run=${runs().live}&node=foundation`);
  await timeline(page)
    .getByRole("button", { name: /branch push/ })
    .click();
  await expect(itemDetail(page)).toContainText("Verification record");
  const facts = itemDetail(page).locator(".facts");
  const only = facts.locator("div");
  await expect(only).toHaveCount(1);
  const list = await facts.boundingBox();
  const term = await only.boundingBox();
  // Within the list's own 1px border on each side.
  expect(term?.width ?? 0).toBeGreaterThanOrEqual((list?.width ?? 0) - 3);
});

test("opens a turn's tool call at the phone", async ({ page }) => {
  // The tool row is itself the disclosure, so a row pushed off the edge of a panel
  // that clips sideways is a call nobody can open.
  await open(page, PHONE, `/?run=${runs().live}&node=dashboard`);
  await transcript(page)
    .getByRole("button", { name: /^Open Judge/ })
    .click();
  const detail = itemDetail(page);
  await expect(
    detail.getByRole("article", { name: /^Turn / }).first(),
  ).toBeVisible();

  const disclosure = detail
    .getByRole("button", { name: "command_execution tool details" })
    .first();
  await expect(disclosure).toBeInViewport({ ratio: 1 });
  await disclosure.click();
  await expect(detail.getByText("just gate").first()).toBeVisible();
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
