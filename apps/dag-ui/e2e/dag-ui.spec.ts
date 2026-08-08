import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, type Locator, type Page, test } from "@playwright/test";
import { z } from "zod";
import {
  FIXTURE_WORKSPACE,
  OFFLINE_UI_URL,
  STALLED_UI_URL,
} from "../playwright.config";
import { PHONE } from "./viewports";

/**
 * The DAG Observatory driven end to end against a real `orchestrator/server.py`
 * serving a real recorded run directory (see `e2e/fixtures/serve_fixture.py`, started
 * by `playwright.config.ts`). Nothing between the browser and the read model is
 * doubled: the app's own telemetry client makes the HTTP and SSE requests, and the
 * server projects them from journal files the executor's own writers produced. Live
 * updates are provoked by changing that run directory, never by faking an event. The
 * pagination recovery journey takes the browser offline; its online retry still
 * reaches this real server and renders its next recorded page.
 */

/**
 * What the fixture wrote — its runs, and the pull request one of them published —
 * read from the file it publishes beside them. The Python module that records them is
 * their one source; naming them again here would be a second one that drifts the
 * moment the fixture changes.
 */
const fixtureSchema = z.object({
  runs: z.object({
    live: z.string().min(1),
    history: z.string().min(1),
    outcomes: z.string().min(1),
    legacy: z.string().min(1),
    sibling: z.string().min(1),
    unattributed: z.string().min(1),
    eventless: z.string().min(1),
    busy: z.string().min(1),
  }),
  foundation_pr: z.string().min(1),
});
let cachedFixture: z.infer<typeof fixtureSchema> | undefined;
const fixture = (): z.infer<typeof fixtureSchema> =>
  (cachedFixture ??= fixtureSchema.parse(
    JSON.parse(
      readFileSync(join(FIXTURE_WORKSPACE, "fixture-facts.json"), "utf8"),
    ),
  ));
/** Every run the fixture wrote, and nothing else: journeys iterate this. */
const runs = (): z.infer<typeof fixtureSchema>["runs"] => fixture().runs;

/**
 * Open the app and wait for it to have mounted; each journey then asserts its own state.
 *
 * The default names the graph because an address that names no view lands on the
 * overall reading of the run — which is a journey of its own below, and what every
 * graph journey here would otherwise have to walk out of first.
 */
async function openObservatory(
  page: Page,
  path = "/?view=graph",
): Promise<void> {
  await page.goto(path);
  await expect(page.getByText("DAG Observatory")).toBeVisible();
}

/** The navigation group holding `runId`, whichever launching session it belongs to. */
function sessionGroup(page: Page, runId: string): Locator {
  return page
    .locator("section")
    .filter({ has: page.getByRole("button", { name: RegExp(runId) }) });
}

/**
 * Whether repeated Tab presses ever land on `target`, i.e. it is in the tab order.
 *
 * The element is resolved once and answers for itself, because `Locator.evaluate`
 * re-queries and waits for a match: a target that leaves the document mid-walk would
 * otherwise park the loop until the test budget expired, reporting that expiry rather
 * than the disappearance behind it.
 */
async function tabTo(
  page: Page,
  target: Locator,
  presses = 40,
): Promise<boolean> {
  const element = await target.elementHandle();
  if (element === null)
    throw new Error("nothing to tab to: the target is not in the document");
  try {
    for (let index = 0; index < presses; index += 1) {
      await page.keyboard.press("Tab");
      const walked = await element.evaluate((node) => ({
        focused: node === document.activeElement,
        attached: node.isConnected,
      }));
      if (walked.focused) return true;
      if (!walked.attached)
        throw new Error(
          `the target left the document after ${index + 1} Tab presses, before focus reached it`,
        );
    }
    return false;
  } finally {
    await element.dispose();
  }
}

async function backgroundColor(locator: Locator): Promise<string> {
  return locator.evaluate(
    (element) => getComputedStyle(element).backgroundColor,
  );
}

/** The brightest channel of a serialized colour — how a dark surface is told from a light one. */
function brightestChannel(color: string): number {
  return Math.max(...(color.match(/\d+/g) ?? ["255"]).slice(0, 3).map(Number));
}

/**
 * Painting a throwaway element is what makes a token comparable to a surface: reading
 * the custom property back gives its declaration text, which is never the `rgb(…)` the
 * browser reports for a `background-color`, so the two could not be compared directly.
 */
async function tokenColor(page: Page, token: string): Promise<string> {
  return page.evaluate((name) => {
    const probe = document.createElement("div");
    probe.style.backgroundColor = `var(${name})`;
    document.body.append(probe);
    const computed = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return computed;
  }, token);
}

/**
 * Change what the server is serving — record progress, or take a run away — through
 * the fixture module that wrote the run directory in the first place.
 */
function changeServedRuns(args: string[]): void {
  execFileSync(
    "uv",
    [
      "run",
      "python",
      "e2e/fixtures/serve_fixture.py",
      "--workspace",
      FIXTURE_WORKSPACE,
      ...args,
    ],
    { stdio: "inherit" },
  );
}

/** The node view's pinned plot, once a node has been opened. */
const timeline = (page: Page): Locator =>
  page.getByRole("region", { name: "Node timeline" });

/** The node view's detail region: whichever timeline item is open, expanded. */
const itemDetail = (page: Page): Locator =>
  page.getByRole("region", { name: "Timeline item detail" });

/** The overall view's single line: the whole graph collapsed to one clock. */
const graphLine = (page: Page): Locator =>
  page.getByRole("region", { name: "Graph timeline" });

/** One row of that graph, once the line has been opened into rows. */
const graphRow = (page: Page, name: string): Locator =>
  page.getByRole("region", { name: `${name} timeline` });

/** The whole row, plot and heading: the heading is where its name and totals are. */
const graphRowCard = (page: Page, name: string): Locator =>
  page.locator(".graph-row").filter({ has: graphRow(page, name) });

/** Open the line into one row per node beside the run's own driving sessions. */
async function expandGraphRows(page: Page): Promise<void> {
  await graphLine(page)
    .getByRole("button", { name: "Expand timeline" })
    .click();
  await expect(
    page.getByRole("region", { name: "Run-level timeline" }),
  ).toBeVisible();
}

/** What one plot's clock currently reads: its two axis ticks, in order. */
async function axisTicks(plot: Locator): Promise<string> {
  return plot.getByTestId("timeline-axis").innerText();
}

test("tracks every node state and kind of a live run", async ({ page }) => {
  await openObservatory(page);

  await expect(
    page.locator(".dag-node.state-done").filter({ hasText: "foundation" }),
  ).toContainText("foundation");
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "dashboard",
  );
  await expect(
    page.locator(".dag-node.state-failed").filter({ hasText: "publish" }),
  ).toContainText("publish");
  await expect(page.locator(".dag-node.state-waiting")).toContainText(
    "approval",
  );
  await expect(page.locator(".dag-node.state-pending")).toContainText(
    "followup",
  );
  await expect(page.locator(".dag-node.state-cancelled")).toContainText(
    "obsolete",
  );
  // The two statuses the scheduler derives and journals nothing about. The served
  // graph re-derives them, so they reach the canvas as themselves rather than as the
  // "pending" a client used to invent for every node the journal never mentioned.
  await expect(page.locator(".dag-node.state-blocked")).toContainText("queued");
  await expect(page.locator(".dag-node.state-skipped")).toContainText(
    "abandoned",
  );

  // Each card names the kind of work it stands for, so an operator can tell the two
  // apart without opening either: agent work runs itself, a human action does not.
  await expect(page.locator(".dag-node.state-running")).toContainText("agent");
  await expect(page.locator(".dag-node.state-waiting")).toContainText("human");

  // And a card that is not moving says why in one line, so a graph of red and amber
  // is a diagnosis rather than an invitation to open every node in it.
  await expect(page.locator(".dag-node.state-blocked")).toContainText(
    "blocked by approval",
  );
  await expect(page.locator(".dag-node.state-skipped")).toContainText(
    "blocked by publish",
  );
  await expect(
    page.locator(".dag-node.state-failed").filter({ hasText: "publish" }),
  ).toContainText("Deploy failed");
  await expect(page.locator(".dag-node.state-cancelled")).toContainText(
    "cancelled cooperatively",
  );
  // Work that is fine gets no such line at all.
  await expect(page.locator(".dag-node.state-done .node-reason")).toHaveCount(
    0,
  );
});

test("leads a node that is not moving with the reason it is not", async ({
  page,
}) => {
  await openObservatory(page, `/?run=${runs().live}&node=publish`);
  const banner = page.getByRole("alert");
  await expect(banner).toContainText("This node failed: agent");
  await expect(banner).toContainText("Deploy failed");
  await expect(banner).toContainText("publication exited non-zero");
  await expect(banner).toContainText("2");
  // It is the first thing in the view: above the disclosures, not inside one.
  const bannerBox = await banner.boundingBox();
  const taskBox = await page.getByRole("tab", { name: "Task" }).boundingBox();
  expect(bannerBox?.y ?? 0).toBeLessThan(taskBox?.y ?? 0);

  // A held node states what holds it, by the plan node the server named.
  await openObservatory(page, `/?run=${runs().live}&node=queued`);
  await expect(page.getByRole("alert")).toContainText("This node is blocked");
  await expect(page.getByRole("alert")).toContainText("approval");

  // The same for the node its failed prerequisite made unreachable.
  await openObservatory(page, `/?run=${runs().live}&node=abandoned`);
  await expect(page.getByRole("alert")).toContainText("This node is skipped");
  await expect(page.getByRole("alert")).toContainText("publish");

  // Abandoned work is lost work: it reads with the failures rather than with the
  // held nodes, and the scheduler's own words for it are what the banner shows.
  await openObservatory(page, `/?run=${runs().live}&node=obsolete`);
  await expect(page.getByRole("alert")).toContainText(
    "This node was cancelled",
  );
  await expect(page.getByRole("alert")).toContainText(
    "cancelled cooperatively",
  );
});

test("renders the outcomes only a settled round records", async ({ page }) => {
  // A finished round records statuses a live one cannot journal. Each has to reach
  // the canvas as itself and read as the kind of outcome it is.
  await openObservatory(page, `/?run=${runs().outcomes}&view=graph`);
  await expect(page.locator(".dag-node.state-not-completed")).toContainText(
    "backfill",
  );
  await expect(page.locator(".dag-node.state-unknown")).toContainText("verify");

  // Unfinished work is lost work, not held work; a status the vocabulary does not
  // hold has no outcome to claim and must not borrow one.
  await expect(page.locator(".dag-node.state-not-completed")).toHaveCSS(
    "background-color",
    await tokenColor(page, "--destructive-surface"),
  );
  await expect(page.locator(".dag-node.state-unknown")).toHaveCSS(
    "background-color",
    await tokenColor(page, "--card"),
  );

  await page.locator(".dag-node.state-not-completed").click();
  await expect(page.getByRole("alert")).toContainText("did not complete");
  await expect(page.getByRole("alert")).toContainText("step 'load' timed out");

  // And a node that failed with nothing recorded about why says exactly that,
  // rather than leaving a banner with an empty body under a heading.
  await openObservatory(page, `/?run=${runs().outcomes}&node=migrate`);
  await expect(page.getByRole("alert")).toContainText(
    "No reason was recorded for this outcome.",
  );

  // And a failure whose only recorded explanation is its outcome word still puts
  // that word on the card, rather than saying nothing the run did not already know.
  await openObservatory(page, `/?run=${runs().outcomes}&view=graph`);
  await expect(
    page.locator(".dag-node.state-failed").filter({ hasText: "rollback" }),
  ).toContainText("gate-failed");
  // The banner reads the same chain, so the card and the view it opens cannot
  // explain one failure two ways.
  await openObservatory(page, `/?run=${runs().outcomes}&node=rollback`);
  await expect(page.getByRole("alert")).toContainText("This node failed: gate");
  await expect(page.getByRole("alert")).toContainText("gate-failed");

  // A blocked node names the human action refs its own result recorded, not only
  // the plan nodes the server derived — the two are different locators.
  await openObservatory(page, `/?run=${runs().outcomes}&node=stalled`);
  await expect(page.getByRole("alert")).toContainText("migrate/sign-off");

  // And one recorded blocked with nothing recorded about what blocks it — a legacy
  // result, or one whose gate has since settled — says exactly that.
  await openObservatory(page, `/?run=${runs().outcomes}&node=orphaned`);
  await expect(page.getByRole("alert")).toContainText(
    "Nothing recorded; the run has not written what holds it.",
  );

  // The lifecycle's prose and the dispatch's error are separate fields that are
  // sometimes the same sentence; the banner states it once, under one heading.
  await openObservatory(page, `/?run=${runs().outcomes}&node=retry`);
  const once = page.getByRole("alert");
  await expect(once).toContainText("gate rejected the push");
  await expect(once).not.toContainText("Error");
});

test("counts a run the strict fold cannot read at all", async ({ page }) => {
  await openObservatory(page);
  // The served run recorded a result with no authoritative journal behind it, which
  // is what a run predating the journal looks like. The per-node derivation cannot run, so
  // the row is counted from the tolerant telemetry index instead — whose statuses are
  // an open string, and whose words the navigation still has to show rather than drop.
  await expect(
    page.getByRole("button", { name: RegExp(runs().legacy) }),
  ).toContainText("1 improvised");
});

test("counts a run's own nodes on the row that opens it", async ({ page }) => {
  await openObservatory(page);
  // The row and the graph it opens are counted from one derivation on the server, so
  // a run whose row says only "running" can no longer hide a node already blocked.
  const liveRow = page.getByRole("button", { name: RegExp(runs().live) });
  await expect(liveRow).toContainText("1 blocked");
  await expect(liveRow).toContainText("1 skipped");
  await expect(liveRow).toContainText("1 pending");
});

test("opens a node's timeline, reads one recorded moment, and returns", async ({
  page,
}) => {
  await openObservatory(page);
  await page.locator(".dag-node.state-running").click();

  // The node takes the working area: the graph is gone, and a breadcrumb stands
  // where it was.
  await expect(
    page.getByRole("region", { name: "Timeline for dashboard" }),
  ).toBeVisible();
  await expect(page.locator(".dag-node")).toHaveCount(0);
  await expect(
    page.getByRole("navigation", { name: "Breadcrumb" }),
  ).toContainText("dashboard");
  await expect(page.locator(".node-view-facts")).toContainText("running");

  await expect(
    page.getByRole("region", { name: "Node transcript" }),
  ).toBeVisible();

  // The upstream plot distinguishes duration bars from instant icons and moves the
  // old row metadata into a compact hover tooltip.
  const worker = timeline(page).getByRole("button", {
    name: /engineer-dashboard/,
  });
  await worker.hover();
  await expect(page.getByRole("tooltip")).toContainText("Duration:");
  await expect(page.getByRole("tooltip")).toContainText("Status: completed");
  await expect(timeline(page).getByRole("button")).not.toHaveCount(0);
  // And it says which session it was, served on the row rather than read out of a
  // transcript: every dispatch on this node read "dispatch" and nothing else, so
  // the worker, the judge that supervised it, and the lint run under it were three
  // rows a reader could not tell apart. Neither name contains its own role.
  await expect(worker).toHaveAccessibleName(/Worker \(engineer-dashboard\)/);
  await expect(
    timeline(page).getByRole("button", { name: "Expand timeline" }),
  ).toBeVisible();
  await timeline(page).getByRole("button", { name: "Expand timeline" }).click();
  await expect(
    timeline(page).getByRole("button", { name: /^Judge/ }),
  ).toBeVisible();
  await expect(
    timeline(page).getByRole("button", { name: /^Lint/ }),
  ).toBeVisible();
  await expect(
    timeline(page).getByRole("button", { name: /^Check-in/ }),
  ).toBeVisible();
  await expect(
    timeline(page).getByRole("button", { name: /^PR author/ }),
  ).toBeVisible();
  // The waits a node spent on a lock are plotted at the length they actually took —
  // ten seconds of a four-minute node — and neither stretched across the window the
  // journal happened to write them in nor collapsed to the instant that window is.
  const lockWait = timeline(page).getByRole("button", { name: /^Lock waits/ });
  await expect(lockWait).toHaveAttribute("data-timeline-shape", "span");
  const lockWidth = (await lockWait.boundingBox())?.width ?? 0;
  const plot = timeline(page).getByLabel(/Timeline plot/);
  const plotWidth = (await plot.boundingBox())?.width ?? 1;
  expect(lockWidth / plotWidth).toBeLessThan(0.1);
  expect(lockWidth / plotWidth).toBeGreaterThan(0.02);
  // The categories the reader was promised, and no served identifier among them.
  await expect(page.getByRole("list", { name: "Timeline legend" })).toHaveText(
    [
      "Worker",
      "Judge",
      "Lint",
      "Orchestrator",
      "Check-in",
      "PR author",
      "Verification",
      "Publication",
      "Lock waits",
      "Human wait",
    ].join(""),
  );
  // Not the rail alone: the words are banned from the whole reading, and a lifecycle
  // step is named in the transcript rather than plotted over its own contents. Read
  // without word boundaries on purpose — this matches the concatenated text content
  // of the whole view, where a banned word runs straight into the next label.
  await expect(
    page.getByRole("region", { name: "Timeline for dashboard" }),
  ).not.toContainText(/phase|rollup/i);
  await timeline(page)
    .getByRole("button", { name: "Collapse timeline" })
    .click();
  await expect(worker).toHaveAttribute("data-timeline-shape", "span");
  await expect(
    page.getByRole("list", { name: "Timeline legend" }),
  ).toBeVisible();
  await plot.hover();
  await page.mouse.wheel(0, -120);
  await expect(
    page.getByRole("button", { name: "Reset timeline zoom" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Reset timeline zoom" }).click();
  const plotBox = await plot.boundingBox();
  if (plotBox === null)
    throw new Error("timeline plot has no brushable bounds");
  await page.mouse.move(plotBox.x + plotBox.width * 0.2, plotBox.y + 10);
  await page.mouse.down();
  await page.mouse.move(plotBox.x + plotBox.width * 0.7, plotBox.y + 10);
  await page.mouse.up();
  await expect(
    page.getByRole("button", { name: "Reset timeline zoom" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Reset timeline zoom" }).click();

  await worker.click();
  await expect
    .poll(() => new URL(page.url()).searchParams.get("event"))
    .toBe("dispatch-worker-session");
  await expect(itemDetail(page)).toContainText(
    "Implementing the dashboard now",
  );
  // The dispatch it belongs to and the role it played in it, on the transcript's
  // own header rather than left to be inferred from the session name.
  await expect(itemDetail(page)).toContainText(
    "Dispatch 1 · Worker · engineer",
  );
  await expect(
    itemDetail(page)
      .getByRole("article", { name: /^Turn / })
      .first(),
  ).toBeVisible();
  // One disclosure per recorded turn: this worker ran three.
  await expect(
    itemDetail(page).getByRole("button", {
      name: "command_execution tool details",
    }),
  ).toHaveCount(3);
  await itemDetail(page)
    .getByRole("button", { name: "command_execution tool details" })
    .first()
    .click();
  const toolOutput = itemDetail(page)
    .getByLabel("command_execution tool output")
    .first();
  await expect(toolOutput).toContainText('"exit_code": 0');
  await expect(toolOutput.locator(".hljs-number")).toHaveText("0");
  // The detail region is where the reading happens, so it holds the majority of
  // the width rather than a fixed narrow column.
  const detailWidth = (await itemDetail(page).boundingBox())?.width ?? 0;
  const workingWidth =
    (await page.locator(".workspace").boundingBox())?.width ?? 0;
  expect(detailWidth / workingWidth).toBeCloseTo(2 / 3, 1);

  // The conversation's own timeline is pinned above its turns and drives them: it
  // stays where it is while they scroll, and picking a moment on it moves the
  // reading to that turn.
  const conversationTimeline = itemDetail(page).getByRole("region", {
    name: "Conversation timeline",
  });
  await expect(conversationTimeline).toBeVisible();
  const reading = itemDetail(page).locator("[data-radix-scroll-area-viewport]");
  const readingTop = () =>
    reading.evaluate((element) => Math.round(element.scrollTop));
  expect(await readingTop()).toBe(0);
  // Expanded first, because a collapsed line stacks turns and the tool calls inside
  // them on one row — which is what collapsing is for, and what makes any single one
  // of them unreachable by pointer.
  await conversationTimeline
    .getByRole("button", { name: "Expand timeline" })
    .click();
  await conversationTimeline
    .getByRole("button", { name: /claude-code turn/ })
    .last()
    .click();
  await expect.poll(readingTop).toBeGreaterThan(0);
  // Pinned, not carried off: the turns scroll under it and it is still on screen.
  await expect(conversationTimeline).toBeInViewport();
  await reading.hover();
  await page.mouse.wheel(0, -20_000);
  await expect.poll(readingTop).toBe(0);

  // A span contains its events, and opening it discloses them: one turn here.
  const turn = timeline(page).getByRole("button", {
    name: /conversation-turn/,
  });
  await expect(turn.first()).toBeVisible();
  await turn.first().click();
  await expect(itemDetail(page)).toContainText(
    "Implementing the dashboard now",
  );

  // Escape closes detail first, then returns to the graph.
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "dashboard",
  );
});

test("restores a bookmarked moment inside a session from the address alone", async ({
  page,
}) => {
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  await timeline(page)
    .getByRole("button", { name: /engineer-dashboard/ })
    .click();
  const turn = timeline(page)
    .getByRole("button", { name: /conversation-turn/ })
    .first();
  await turn.click();
  const bookmarked = new URL(page.url());
  expect(bookmarked.searchParams.get("event")).not.toBe(
    "dispatch-worker-session",
  );

  // Loading the graph in between is what makes the next load cold: nothing the
  // clicks left behind can be what reopens the moment, only the address.
  await openObservatory(page, "/?view=graph");
  await openObservatory(page, `${bookmarked.pathname}${bookmarked.search}`);
  const bookmarkedId = bookmarked.searchParams.get("event");
  if (bookmarkedId === null) throw new Error("bookmark lost its event id");
  // The address alone reopens the moment: its marker is the pressed one among the
  // node's journal icons, and its item is the focused one in the transcript.
  await expect(
    timeline(page).locator('[data-selected="true"]'),
  ).toHaveAccessibleName(/conversation-turn, marker/);
  await expect(
    page
      .getByRole("region", { name: "Node transcript" })
      .locator('[data-selected="true"]'),
  ).toHaveCount(1);
  await expect(itemDetail(page)).toContainText(
    "Implementing the dashboard now",
  );
});

test("keeps timeline, transcript, and nested judge conversation in time sync", async ({
  page,
}) => {
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  const transcript = page.getByRole("region", { name: "Node transcript" });
  const axis = timeline(page).getByTestId("timeline-axis");
  await expect(axis.locator("span")).toHaveCount(2);
  for (const tick of await axis.locator("span").allTextContents()) {
    expect(tick).toMatch(/\d{2}:\d{2}:\d{2}.*[+−]\d/u);
  }

  // Scrolled the way a reader scrolls it — a wheel over the transcript — because the
  // cursor tracks the real scroll path, not a value written into `scrollTop`.
  const timelineTop = (await timeline(page).boundingBox())?.y;
  const cursor = timeline(page).getByTestId("timeline-cursor");
  const before = await cursor.getAttribute("style");
  await transcript.hover();
  await page.mouse.wheel(0, 400);
  await expect.poll(() => cursor.getAttribute("style")).not.toBe(before);
  // And the timeline stays exactly where it was while the reading moves under it.
  expect((await timeline(page).boundingBox())?.y).toBe(timelineTop);

  await timeline(page).getByRole("button", { name: "Expand timeline" }).click();
  const judge = timeline(page).getByRole("button", { name: /^Judge/ });
  const lint = timeline(page).getByRole("button", { name: /^Lint/ });
  await expect(judge).toHaveAttribute("data-timeline-shape", "span");
  await expect(lint).toHaveAttribute("data-timeline-shape", "span");
  // Read as a share of the plot, not as pixels: a supervising session projected
  // against a window nothing else occupied still clears the minimum bar width the
  // design system paints, which is exactly the sliver this has to rule out. Each of
  // these ran half a minute of a four-minute node.
  const plotWidth = (
    await timeline(page)
      .getByLabel(/Timeline plot/)
      .boundingBox()
  )?.width;
  if (plotWidth === undefined) throw new Error("timeline plot has no bounds");
  for (const supervising of [judge, lint]) {
    expect((await supervising.boundingBox())?.width ?? 0).toBeGreaterThan(
      plotWidth * 0.05,
    );
  }
  await judge.click();
  await expect
    .poll(() => new URL(page.url()).searchParams.get("event"))
    .toBe("dispatch-judge-session");

  const judgeItem = transcript
    .getByRole("article")
    .filter({ hasText: "you-are-a-strict-careful-evaluator" });
  await expect(judgeItem).toHaveAttribute("data-selected", "true");
  await expect(judgeItem).toHaveAttribute("data-dispatch-group", "Dispatch 1");
  await expect(judgeItem).toContainText("Judge");
  const workerItem = transcript
    .getByRole("article")
    .filter({ hasText: "engineer-dashboard" });
  await expect(workerItem).toHaveAttribute("data-dispatch-group", "Dispatch 1");

  await expect(itemDetail(page)).toContainText("Judge");
  await expect(itemDetail(page)).toContainText(
    "you-are-a-strict-careful-evaluator",
  );
  await expect(
    itemDetail(page).locator(".conversation-timeline-sticky"),
  ).toHaveCSS("position", "sticky");
  await expect(
    itemDetail(page).getByRole("article", { name: /^Turn / }),
  ).toContainText("Judge");
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("Item detail panel")).toHaveCount(0);
});

test("scrolls the transcript to the journal record a marker names", async ({
  page,
}) => {
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  const transcript = page.getByRole("region", { name: "Node transcript" });
  // A journal record occupies no lane: it is a marker over all of them, which is why
  // clicking one is the only way to reach an instant from the plot.
  const markers = timeline(page).getByRole("button", { name: /, marker$/ });
  await expect(markers.first()).toBeVisible();
  const marked = await markers.last().getAttribute("aria-label");
  if (marked === null) throw new Error("a marker carries no accessible name");

  const before = await transcript.evaluate((element) => element.scrollTop);
  await markers.last().click();
  const focused = transcript.locator('[data-selected="true"]');
  await expect(focused).toHaveCount(1);
  await expect(focused).toHaveAccessibleName(marked.replace(", marker", ""));
  // Focusing is a move, not just a highlight: the reading position follows.
  await expect
    .poll(() => transcript.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(before);
  // And the moment it moved to is in the address, so the reading is bookmarkable.
  await expect
    .poll(() => new URL(page.url()).searchParams.get("event"))
    .not.toBeNull();

  // The sessions of one dispatch are nested under its own name, not listed beside
  // it: the agent session and the lint run it made of its own work read as one unit.
  const dispatch = transcript.getByRole("region", { name: "Dispatch 1" });
  await expect(
    dispatch.getByRole("article", { name: /^Worker \(engineer-dashboard\)/ }),
  ).toBeVisible();
  await expect(
    dispatch.getByRole("article", { name: /^Lint \(llmlint-dashboard\)/ }),
  ).toBeVisible();
  await expect(
    dispatch.getByRole("article", { name: /^Judge \(/ }),
  ).toBeVisible();
  // A separately dispatched role is its own group rather than a member of the first.
  await expect(
    transcript
      .getByRole("region", { name: "Dispatch 2" })
      .getByRole("article", { name: /^Check-in \(/ }),
  ).toBeVisible();
});

test("opens a node from the keyboard-accessible node list and walks back", async ({
  page,
}) => {
  // Eighty-odd Tab presses, each a browser round trip: latency-bound rather than slow.
  test.slow();
  await openObservatory(page);
  // The run navigation precedes the workspace in the tab order, so the walk means
  // nothing until it has arrived: setting off first reached the node list only because
  // the fifty run links were still missing from it.
  await expect(
    page.getByRole("navigation", { name: "DAG runs" }).locator(".run-link"),
  ).toHaveCount(50);
  // The canvas is a pointer surface, so the list beside it is the keyboard path to
  // every node; it has to reach the same node view a click does.
  const node = page
    .getByRole("list", { name: "DAG nodes" })
    .getByRole("button", { name: "dashboard: running" });
  // Enough to cross that navigation and the workspace behind it: the node answers at
  // the 82nd tab stop.
  expect(await tabTo(page, node, 120)).toBe(true);
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("region", { name: "Timeline for dashboard" }),
  ).toBeVisible();

  // The way back is in the tab order too, not only under the Escape key.
  const back = page
    .getByRole("navigation", { name: "Breadcrumb" })
    .getByRole("button", { name: /Graph/ });
  expect(await tabTo(page, back)).toBe(true);
  await page.keyboard.press("Enter");
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "dashboard",
  );
});

test("shows a verification and a publication as the records they are", async ({
  page,
}) => {
  await openObservatory(page, `/?run=${runs().live}&node=foundation`);

  // The verification carries this push's own verdict and bounded output, then loads
  // the preserved log through its opaque artifact id without exposing a host path.
  await timeline(page)
    .getByRole("button", { name: /branch push/ })
    .click();
  await expect(
    timeline(page).getByRole("button", { name: /branch push/ }),
  ).toHaveAttribute("data-timeline-shape", "span");
  await expect(itemDetail(page)).toContainText("Verification record");
  await expect(itemDetail(page)).toContainText("pre-push verification passed");
  await expect(itemDetail(page)).toContainText("full verification output");
  await expect(itemDetail(page)).not.toContainText(
    "oldest verification output",
  );
  await itemDetail(page).getByRole("button", { name: "Expand log" }).click();
  await expect(itemDetail(page)).toContainText("oldest verification output");
  await expect(
    itemDetail(page).getByRole("button", { name: "Collapse log" }),
  ).toBeVisible();
  await expect(itemDetail(page)).not.toContainText(
    "round-01/foundation/gate.log",
  );
  await page.getByRole("button", { name: "Close detail" }).click();

  // The publication carries the PR and the checks that were observed on it.
  await timeline(page)
    .getByRole("button", { name: /local\/example/ })
    .click();
  await expect(
    itemDetail(page).getByRole("link", {
      name: /github\.com\/example\/repo\/pull\/12/,
    }),
  ).toBeVisible();
  await expect(itemDetail(page)).toContainText("Observed checks");
  await expect(itemDetail(page)).toContainText("unit");
  await expect(
    itemDetail(page).getByRole("link", { name: "SUCCESS" }),
  ).toHaveAttribute("href", "https://github.com/example/repo/actions/runs/12");
  await expect(
    itemDetail(page).getByRole("link", { name: /Merged commit/ }),
  ).toHaveAttribute(
    "href",
    `https://github.com/example/repo/commit/${"4".repeat(40)}`,
  );
  await page.getByRole("button", { name: "Close detail" }).click();

  // Its own recorded events sit inside it, in the order they happened.
  const checks = page
    .getByRole("article")
    .filter({ hasText: /pr-checks-observed/ })
    .getByRole("button");
  await checks.click();
  await expect(itemDetail(page)).toContainText("Observed checks");
  await expect(itemDetail(page)).toContainText("passing");
});

test("states when a verification artifact is unavailable", async ({ page }) => {
  await openObservatory(page, `/?run=${runs().live}&node=missing-artifact`);
  const checksTab = page.getByRole("tab", { name: "Checks" });
  await checksTab.focus();
  await page.keyboard.press("Enter");
  await expect(
    page.locator(".facts").filter({ hasText: "Verification coverage" }),
  ).toContainText("Hook: not recorded");
  await page.getByRole("tab", { name: "Timeline" }).click();
  const rejected = page.waitForResponse(
    (response) =>
      response.url().includes("/artifacts/") && response.status() === 404,
  );
  const failedVerification = timeline(page).getByRole("button", {
    name: /missing verification log/,
  });
  await failedVerification.hover();
  await expect(page.getByRole("tooltip")).toContainText(
    "Failure: log was removed",
  );
  await failedVerification.click();
  await rejected;
  await expect(itemDetail(page)).toContainText("No readable log was recorded.");
});

for (const scenario of [
  {
    name: "local direct merge",
    node: "local-direct",
    expected: [`https://github.com/example/repo/commit/${"a".repeat(40)}`],
    absent: [
      fixture().foundation_pr,
      "https://github.com/example/repo/tree/feature/local",
    ],
  },
  {
    name: "remote PR unmerged",
    node: "remote-open",
    expected: [
      "https://github.com/example/repo/pull/13",
      "https://github.com/example/repo/tree/feature/remote-open",
    ],
    absent: [`https://github.com/example/repo/commit/${"b".repeat(40)}`],
  },
  {
    name: "remote PR merged",
    node: "remote-merged",
    expected: [
      "https://github.com/example/repo/pull/14",
      `https://github.com/example/repo/commit/${"c".repeat(40)}`,
    ],
    absent: ["https://github.com/example/repo/tree/feature/remote-merged"],
  },
] satisfies readonly {
  name: string;
  node: string;
  expected: readonly string[];
  absent: readonly string[];
}[]) {
  test(`renders the ${scenario.name} publication from the API`, async ({
    page,
  }) => {
    await openObservatory(page, `/?run=${runs().live}&node=${scenario.node}`);
    await timeline(page)
      .getByRole("button", { name: /example\/repo/ })
      .click();
    for (const href of scenario.expected) {
      await expect(itemDetail(page).locator(`a[href="${href}"]`)).toBeVisible();
    }
    for (const href of scenario.absent) {
      await expect(itemDetail(page).locator(`a[href="${href}"]`)).toHaveCount(
        0,
      );
    }
  });
}

test("keeps a node's task, criteria, dependencies and verification reachable", async ({
  page,
}) => {
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  await expect(page.getByRole("tab", { name: "Timeline" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await page.getByRole("tab", { name: "Overall" }).click();
  await expandGraphRows(page);
  await graphRowCard(page, "dashboard")
    .getByRole("button", { name: "dashboard", exact: true })
    .click();
  await expect(page).toHaveURL(/node=dashboard/);
  await page.getByRole("tab", { name: "Task" }).click();
  await expect(page.getByText("Build the live dashboard")).toBeVisible();
  await page.getByRole("tab", { name: "Completion criteria" }).click();
  await expect(page.getByText("Users can inspect transcripts")).toBeVisible();

  await page.getByRole("tab", { name: "Dependencies" }).click();
  await expect(page.locator(".facts")).toContainText("foundation");
  await page.getByRole("tab", { name: "PR" }).click();
  await expect(page.locator(".facts")).toContainText("Not recorded");
  await expect(page.locator(".facts").getByRole("link")).toHaveCount(0);

  await openObservatory(page, `/?run=${runs().live}&node=foundation`);
  await page.getByRole("tab", { name: "PR" }).click();
  const pr = page.locator(".facts").getByRole("link", { name: "Pull request" });
  await expect(pr).toHaveAttribute("href", fixture().foundation_pr);
  await expect(pr).toHaveAttribute("target", "_blank");
  await expect(pr).toHaveAttribute("rel", "noreferrer");

  // A human action names work for a person, so the contract forbids it a completion
  // bar; the summary has to say that rather than render an empty criteria block.
  await openObservatory(page, `/?run=${runs().live}&node=approval`);
  await page.getByRole("tab", { name: "Completion criteria" }).click();
  await expect(
    page.getByText("No completion criteria recorded."),
  ).toBeVisible();
});

test("keeps a node of hundreds of recorded sessions scannable", async ({
  page,
}) => {
  // The served run really did record hundreds of sessions on this node, which is
  // the shape that made the old detail panel unreadable.
  await openObservatory(page, `/?run=${runs().busy}&node=sweep`);
  const rows = timeline(page).getByRole("button");
  await expect(rows.first()).toBeVisible();
  const grouped = timeline(page).getByRole("button", {
    name: /grouped worker activities/,
  });
  await expect(grouped).toBeVisible();
  expect(
    await timeline(page).locator("[data-timeline-shape]").count(),
  ).toBeLessThan(12);
  await timeline(page).getByRole("button", { name: "Expand timeline" }).click();
  await expect(
    timeline(page).getByRole("button", { name: "Collapse timeline" }),
  ).toBeVisible();
  await timeline(page)
    .getByRole("button", { name: "Collapse timeline" })
    .click();

  // And one long session's own turns are handed out a page at a time in the panel,
  // so opening it does not render thirty of them at once either.
  await page
    .getByRole("article")
    .filter({ hasText: /engineer-sweep-7\b/ })
    .getByRole("button")
    .click();
  await expect(itemDetail(page)).toContainText("Swept batch 7 (0)");
  const groupedBookmark = new URL(page.url());
  await openObservatory(page, "/?view=graph");
  await openObservatory(
    page,
    `${groupedBookmark.pathname}${groupedBookmark.search}`,
  );
  await expect(itemDetail(page)).toContainText("Swept batch 7 (0)");
  await expect(itemDetail(page)).not.toContainText("Swept batch 7 (29)");
  await itemDetail(page)
    .getByRole("button", { name: /Show more of 30 turns/ })
    .click();
  await expect(itemDetail(page)).toContainText("Swept batch 7 (29)");
});

test("reports a node whose recorded work the run has not written yet", async ({
  page,
}) => {
  // `followup` never started, so the run recorded no span or event for it at all.
  // That is a real state of a live graph, and it has to be said rather than shown
  // as an empty pane that reads like a broken view.
  await openObservatory(page, `/?run=${runs().live}&node=followup`);
  await expect(
    page.getByText("This node has no recorded timeline yet."),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("zooms and reframes the graph through its canvas controls", async ({
  page,
}) => {
  await openObservatory(page);
  const viewport = page.locator(".react-flow__viewport");
  const transform = async (): Promise<string> =>
    viewport.evaluate((element) => getComputedStyle(element).transform);

  // These graphs are a handful of nodes that fit the canvas, so there is no minimap
  // over them: the zoom controls are the whole of the canvas chrome.
  await expect(page.locator(".react-flow__minimap")).toHaveCount(0);
  const framed = await transform();
  await page.getByRole("button", { name: "zoom in" }).click();
  await expect.poll(transform).not.toBe(framed);
  // Fit view returns the whole graph to frame, which is how an operator recovers
  // from a zoom that lost the nodes.
  await page.getByRole("button", { name: "fit view" }).click();
  await expect.poll(transform).toBe(framed);
});

test("renders a graph whose node depends on another run", async ({ page }) => {
  await openObservatory(page);
  // The served plan gives `dashboard` a `run:<run_id>#<node_id>` prerequisite. It
  // names a node this graph does not hold, so it cannot be an edge — and it must not
  // take the whole view down with it either.
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "dashboard",
  );
  await expect(
    page.getByText("The DAG view could not be displayed."),
  ).toHaveCount(0);

  // The prerequisite itself stays visible where the node's dependencies are listed.
  await page.locator(".dag-node.state-running").click();
  await page.getByRole("tab", { name: "Dependencies" }).click();
  await expect(page.locator(".facts")).toContainText(
    `run:${runs().history}#archive`,
  );
});

test("navigates historical DAGs grouped by their launching session", async ({
  page,
}) => {
  await openObservatory(page);
  await expect(page.getByText(/Codex session/)).toBeVisible();
  await expect(page.getByText(/Claude session/)).toBeVisible();

  // Every row states the run's own state and whether it is still moving, so the list
  // is readable without opening a run.
  const liveRow = page.getByRole("button", { name: RegExp(runs().live) });
  await expect(liveRow).toContainText("running");
  await expect(
    page.getByRole("button", { name: RegExp(runs().history) }),
  ).toContainText("complete");

  // The live marker is a bare dot, so it carries a name of its own and repeats it on
  // hover rather than leaving colour to say the only thing that distinguishes it.
  const liveMarker = liveRow.getByRole("img", { name: "Live" });
  await expect(liveMarker).toBeVisible();
  await liveMarker.hover();
  await expect(page.getByRole("tooltip")).toContainText("Live");

  await page.getByRole("button", { name: RegExp(runs().history) }).click();
  await expect(page.locator(".dag-node.state-done")).toContainText("archive");
  // The graph is what this reader is in, so the address keeps saying so as they move
  // between runs — the same way it keeps saying `overall` for a reader in that.
  await expect
    .poll(() => new URL(page.url()).searchParams.get("view"))
    .toBe("graph");
  await page.getByRole("button", { name: RegExp(runs().live) }).click();
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "dashboard",
  );
  await page.goBack();
  await expect(page.locator(".dag-node.state-done")).toContainText("archive");
});

test("loads another run-list page when navigation reaches the end", async ({
  page,
}) => {
  await page.goto("/?view=graph");
  const navigation = page.getByRole("navigation", { name: "DAG runs" });
  await expect(navigation.locator(".run-link")).toHaveCount(50);
  await navigation.locator("[data-radix-scroll-area-viewport]").hover();
  await page.mouse.wheel(0, 10_000);
  await expect(navigation.locator(".run-link")).toHaveCount(52);
});

/**
 * A window tall enough to hold the whole first page of runs: those fifty rows measure
 * 4350px, so this leaves room for ten more before the list would scroll again.
 */
const RUN_LIST_FITS_HEIGHT = 5200;

test("loads another run-list page from the keyboard", async ({ page }) => {
  // Fifty-odd Tab presses, each a browser round trip: the journey is latency-bound
  // rather than slow, so it needs the budget rather than the speed.
  test.slow();
  // The keyboard path and the scroll path are otherwise the same path: walking the run
  // list scrolls it, the navigation pages itself from that scroll, and `hasMore` going
  // false unmounts the control being tabbed towards. A list that fits cannot scroll.
  await page.setViewportSize({ width: 1280, height: RUN_LIST_FITS_HEIGHT });
  await page.goto("/?view=graph");
  const navigation = page.getByRole("navigation", { name: "DAG runs" });
  const loadMore = page.getByRole("button", { name: "Load more runs" });
  await expect(navigation.locator(".run-link")).toHaveCount(50);
  // Asserted rather than assumed: a first page that outgrew the window would scroll.
  expect(
    await navigation
      .locator("[data-radix-scroll-area-viewport]")
      .evaluate((element) => element.scrollHeight - element.clientHeight),
  ).toBe(0);

  expect(await tabTo(page, loadMore, 70)).toBe(true);
  // Still on the first page once the walk has arrived, so the only thing that can
  // account for the next one is the keypress below.
  await expect(navigation.locator(".run-link")).toHaveCount(50);
  await page.keyboard.press("Enter");
  await expect(navigation.locator(".run-link")).toHaveCount(52);
});

test("recovers when loading another run-list page fails", async ({
  context,
  page,
}) => {
  await page.goto("/?view=graph");
  const navigation = page.getByRole("navigation", { name: "DAG runs" });
  const viewport = navigation.locator("[data-radix-scroll-area-viewport]");
  await expect(navigation.locator(".run-link")).toHaveCount(50);

  await context.setOffline(true);
  await viewport.hover();
  await page.mouse.wheel(0, 10_000);
  await expect(page.getByRole("alert")).toContainText(
    "Telemetry request failed",
  );

  await context.setOffline(false);
  await page.mouse.wheel(0, -200);
  await page.mouse.wheel(0, 10_000);
  await expect(navigation.locator(".run-link")).toHaveCount(52);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("restores a bookmarked view and refreshes through the read API", async ({
  page,
}) => {
  await openObservatory(page, `/?run=${runs().live}&view=overall`);
  const metric = (label: string) =>
    page.locator(".metric").filter({ hasText: label });
  await expect(metric("Status")).toContainText("running");
  await expect(metric("Nodes")).toContainText(/[1-9]\d*/);
  // A duration in the units it is read in, never the raw second count the contract
  // serves: `58000.0s` is arithmetic homework, `16h 6m 40s` is an answer.
  await expect(metric("Wall time").locator("strong")).toHaveText(
    /^(\d{1,3}ms|[1-5]?\ds|\d+m [1-5]?\ds|\d+h [1-5]?\dm [1-5]?\ds)$/,
  );
  await expect(metric("Turns")).toContainText(/\d+/);
  await expect(graphLine(page)).toBeVisible();
  await expect(page.getByText("Observe the live DAG safely")).toBeVisible();
  await expect(page.getByText(/^Codex session · /).first()).toBeVisible();

  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(graphLine(page)).toBeVisible();

  await page.getByRole("tab", { name: "Graph" }).click();
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "dashboard",
  );
  await expect(graphLine(page)).toHaveCount(0);

  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  await expect(
    page.getByRole("region", { name: "Timeline for dashboard" }),
  ).toBeVisible();
});

test("lands on the run as a whole, with every deep link still opening", async ({
  page,
}) => {
  // An address that names no view is an operator arriving at the observatory, and
  // what they came to read is the run — not the shape of its graph.
  await page.goto("/");
  await expect(page.getByText("DAG Observatory")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Overall" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(graphLine(page)).toBeVisible();
  await expect(page.locator(".dag-node")).toHaveCount(0);

  // Picking a second run is an operator comparing the two, so the reading they are
  // comparing them in survives the move — only the run under it changes.
  await page.getByRole("button", { name: RegExp(runs().history) }).click();
  await expect
    .poll(() => new URL(page.url()).searchParams.get("run"))
    .toBe(runs().history);
  await expect(page.getByRole("tab", { name: "Overall" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(graphLine(page)).toBeVisible();
  await expect(page.locator(".dag-node")).toHaveCount(0);

  // Every address that does name where it is going still opens there.
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  await expect(
    page.getByRole("region", { name: "Timeline for dashboard" }),
  ).toBeVisible();

  // The node cannot survive a move to a run that never recorded it, so leaving one
  // this way lands on the run as a whole — the reading a bare address gets — rather
  // than on the graph the node bookmark was being read through.
  await page.getByRole("button", { name: RegExp(runs().history) }).click();
  await expect(page.getByRole("tab", { name: "Overall" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(
    page.getByRole("region", { name: "Timeline for dashboard" }),
  ).toHaveCount(0);

  await openObservatory(page);
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "dashboard",
  );

  // An address naming a view this app does not have is an address naming none: a
  // stale bookmark lands where a bare one does rather than on an empty pane.
  await openObservatory(page, `/?run=${runs().live}&view=timeline`);
  await expect(page.getByRole("tab", { name: "Overall" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(graphLine(page)).toBeVisible();
  await expect(page.locator(".dag-node")).toHaveCount(0);
});

test("restores node tabs and moves between them from the keyboard", async ({
  page,
}) => {
  await openObservatory(
    page,
    `/?run=${runs().live}&node=dashboard&tab=criteria`,
  );
  const criteria = page.getByRole("tab", { name: "Completion criteria" });
  await expect(criteria).toHaveAttribute("aria-selected", "true");
  await criteria.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Dependencies" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page).toHaveURL(/tab=dependencies/);

  await page.getByRole("tab", { name: "Timeline" }).click();
  await timeline(page)
    .getByRole("button", { name: /engineer-dashboard/ })
    .click();
  await expect(page).toHaveURL(/event=/);
  await page.getByRole("tab", { name: "Task" }).click();
  await expect(page).not.toHaveURL(/event=/);

  await page.getByRole("button", { name: /Graph/ }).click();
  await expect(page).not.toHaveURL(/tab=/);
  const foundation = page.getByRole("button", { name: "foundation: done" });
  await foundation.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("tab", { name: "Timeline" })).toHaveAttribute(
    "aria-selected",
    "true",
  );

  await page.getByRole("tab", { name: "Checks" }).click();
  await expect(page.locator(".facts")).toContainText("unit: SUCCESS");
  await expect(
    page.locator(".facts").getByRole("link", { name: "unit: SUCCESS" }),
  ).toHaveAttribute("href", "https://github.com/example/repo/actions/runs/12");
  await page.getByRole("tab", { name: "Task" }).click();
  await expect(page.locator(".facts")).toContainText(
    "Gate completed successfully",
  );

  await page.getByRole("tab", { name: "Task" }).click();
  await page.getByRole("tab", { name: "Overall" }).click();
  await expect(page).not.toHaveURL(/tab=/);

  await openObservatory(page, `/?run=${runs().live}&node=dashboard&tab=bogus`);
  await expect(page.getByRole("tab", { name: "Timeline" })).toHaveAttribute(
    "aria-selected",
    "true",
  );

  await page.getByRole("tab", { name: "Task" }).click();
  await page.getByRole("button", { name: RegExp(runs().history) }).click();
  await expect(page).not.toHaveURL(/tab=/);
});

test("reads every recorded moment as words rather than as its stamp", async ({
  page,
}) => {
  // `approval` recorded work the run never closed, so its one item is shown as the
  // typed record it is — which is where two raw ISO strings used to reach the reader.
  await openObservatory(page, `/?run=${runs().live}&node=approval`);
  await timeline(page)
    .getByRole("button", { name: /approval/ })
    .click();
  await expect(itemDetail(page)).toContainText("Recorded at");
  await expect(itemDetail(page)).toContainText("Still running");
  // The detail reading stays human-formatted; the exact stamp belongs to the
  // timeline's accessible tooltip, where it can be copied when needed.
  await expect(itemDetail(page)).not.toContainText(/\d{4}-\d\d-\d\dT/);
  await expect(itemDetail(page)).not.toContainText(/\d+\.\d+s/);
  // The whole instant stays reachable: it is the reading's own tooltip, and the
  // recorded stamp is on the element the browser can read it off.
  // What a fact list is asked is how recent the record is, so it is read as an age —
  // the run wrote this journal moments ago, and that is what it says.
  const recorded = itemDetail(page).locator(".facts time").first();
  await expect(recorded).toHaveText(/^\d+ (second|minute|hour|day)s? ago$/);
  await expect(recorded).toHaveAttribute("datetime", /^\d{4}-\d\d-\d\dT/);
  await expect(recorded).toHaveAttribute("title", /\d{4}/);

  // Across a populated node, metadata stays in the visualization's hover detail.
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  const dashboardWorker = timeline(page).getByRole("button", {
    name: /engineer-dashboard/,
  });
  await dashboardWorker.hover();
  // Three recorded turns, each with its own start and finish: the served span is as
  // long as the session actually ran, not the instant a start stamp alone gives it.
  await expect(page.getByRole("tooltip")).toContainText("Duration: 50.0 s");
});

test("gathers every run of one launching session under it", async ({
  page,
}) => {
  await openObservatory(page);
  // Three of the served runs record the same launch id, as one planner session
  // driving several graphs does. They belong to one group, not one group each.
  const codex = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: /Codex session/ }) });
  await expect(
    page.getByRole("heading", { name: /Codex session/ }),
  ).toHaveCount(1);
  await expect(codex.getByRole("button")).toHaveCount(3);
  await expect(codex).toContainText(runs().live);
  await expect(codex).toContainText(runs().sibling);
  await expect(codex).toContainText(runs().busy);

  // Both are reachable from that one group.
  await codex.getByRole("button", { name: RegExp(runs().sibling) }).click();
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "sibling",
  );
});

test("groups a run with no recorded launch as unattributed", async ({
  page,
}) => {
  await openObservatory(page);
  // Wait for the attributed groups first: until a run's detail arrives it has no
  // transcript to attribute, so every group reads as unattributed for that moment.
  await expect(page.getByText(/Codex session/)).toBeVisible();
  // The claude launch has no protected provenance record at all — the state every
  // launch reaches once that short-lived record expires — and its run is still
  // named by the session that launched it, from what the run directory recorded.
  await expect(page.getByText(/Claude session/)).toBeVisible();
  // The server serves this run with no launch join and no transcripts at all; it
  // still has to be reachable rather than dropped from the navigation. Every
  // unattributed run gets its own group, so name this run's group rather than the
  // only one — and it reads as honestly unattributed, not as an unknown session.
  await expect(
    sessionGroup(page, runs().unattributed).getByRole("heading", {
      name: /Unattributed/,
    }),
  ).toBeVisible();
  // A run recorded before attribution reached the run directory, whose protected
  // record is gone too: nothing can name its session, so it is named by the launch
  // it did record rather than pooled with the runs that recorded nothing at all.
  await expect(
    sessionGroup(page, runs().eventless).getByRole("heading", {
      name: /Unattributed launch · 1e6a1e6a…/,
    }),
  ).toBeVisible();

  await page.getByRole("button", { name: RegExp(runs().unattributed) }).click();
  await expect(page.locator(".dag-node.state-running")).toContainText("orphan");
  await expect(page.getByText("Continue unattributed work")).toHaveCount(0);
});

test("lists a run that has recorded no event beside the runs that have", async ({
  page,
}) => {
  await openObservatory(page);
  // The served root mixes both shapes: four runs with journalled events and one that
  // has journalled none. The client parses the run list as a whole, so a run whose
  // `last_event` it rejected would take every other run down with it and leave the
  // operator looking at "No DAG runs found" — the state this fixture would have
  // reproduced before `last_event` became nullable.
  const navigation = page.getByRole("navigation", { name: "DAG runs" });
  for (const runId of Object.values(runs())) {
    await expect(
      navigation.getByRole("button", { name: RegExp(runId) }),
    ).toBeVisible();
  }
  await expect(page.getByText("No DAG runs found")).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);

  // Its overall view names the absence instead of trailing off after "last event".
  await openObservatory(page, `/?run=${runs().eventless}&view=overall`);
  const hero = page.locator(".overall-hero");
  await expect(hero).toContainText("no events recorded yet");
  await expect(hero).not.toContainText("null");
  await expect(hero).not.toContainText("last event");
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("opens a run-level session other than the one shown on arrival", async ({
  page,
}) => {
  // Which transcripts the browser really asked the server for — counted by session
  // rather than by request, since a development build mounts every effect twice.
  const transcripts = new Set<string>();
  page.on("request", (request) => {
    const { pathname } = new URL(request.url());
    if (pathname.includes("/conversations/"))
      transcripts.add(decodeURIComponent(pathname.split("/").at(-1) ?? ""));
  });

  // The served run records two sessions at no node: the orchestrator's own, and the
  // round's check-in beside it. The graph view plots them and reads neither, because
  // it is a reading of the record rather than a download of it.
  await openObservatory(page, `/?run=${runs().live}&view=overall`);
  await expandGraphRows(page);
  const runLevel = page.getByRole("region", { name: "Run-level timeline" });
  await expect(runLevel).toBeVisible();
  await expect.poll(() => transcripts.size).toBe(0);

  // Each opens in the two-thirds panel beside the plot, with the turns labelled by
  // the role the segment that opened them was named with.
  await runLevel
    .getByRole("button", { name: /^Run-level · Orchestrator/ })
    .click();
  await expect(itemDetail(page)).toContainText(
    "Coordinating the execution frontier",
  );
  await expect(
    itemDetail(page)
      .getByRole("article", { name: /^Turn / })
      .first(),
  ).toBeVisible();
  await expect.poll(() => transcripts.size).toBe(1);

  // The panel takes two thirds of the working area and lies over the plot it was
  // opened from, so reading the next session means closing this one — which Escape
  // does here exactly as it does in the node view.
  await page.keyboard.press("Escape");
  await expect(itemDetail(page)).toHaveCount(0);
  await runLevel.getByRole("button", { name: /^Run-level · Check-in/ }).click();
  await expect(itemDetail(page)).toContainText("Round 1 progress reported");
  await expect(itemDetail(page)).toContainText("Check-in");
  await expect.poll(() => transcripts.size).toBe(2);
});

test("draws a run that recorded no run-level session as a silent row", async ({
  page,
}) => {
  // The settled run's history holds a worker session and no orchestrator one, so its
  // run-level row recorded nothing at all — which is a row of idle rather than a row
  // that has been left out, and it is drawn beside the node that did work.
  await openObservatory(page, `/?run=${runs().history}&view=overall`);
  await expandGraphRows(page);
  const runLevel = page.getByRole("region", { name: "Run-level timeline" });
  await expect(runLevel.getByRole("button", { name: /^Idle · / })).toHaveCount(
    1,
  );
  await expect(
    page.getByRole("region", { name: "archive timeline" }),
  ).toBeVisible();
});

test("reads the whole run as one clock, node by node, from one line", async ({
  page,
}) => {
  await openObservatory(page, `/?run=${runs().live}&view=overall`);

  // Collapsed, the run is a single line covering everything it has recorded. Its
  // clock runs from the launch to the moment the record was read, which for a run
  // still going is now — and no row of any kind is drawn yet.
  await expect(graphLine(page)).toBeVisible();
  await expect(page.getByRole("region", { name: /\stimeline$/ })).toHaveCount(
    1,
  );
  await expect(
    graphLine(page).getByRole("button", { name: /^Idle · / }),
  ).not.toHaveCount(0);

  // Opened once: one row per plan node, and the run's own driving sessions beside
  // them rather than folded into a node that never dispatched them.
  await expandGraphRows(page);
  for (const node of ["foundation", "dashboard", "publish", "queued"]) {
    await expect(graphRow(page, node)).toBeVisible();
  }
  const dashboard = graphRow(page, "dashboard");
  // Collapsed, a row is one line whatever it holds.
  await expect(dashboard.getByTestId("timeline-lane")).toHaveCount(1);

  // Opened again: that node's own category lanes, the same vocabulary its node view
  // draws — Lint included, which shares the worker's semantic role and is told from
  // it only by the transport the served summary carries.
  await dashboard.getByRole("button", { name: "Expand timeline" }).click();
  await expect(
    dashboard
      .getByTestId("timeline-lane")
      .and(page.locator('[data-lane-id="lint"]')),
  ).toHaveCount(1);
  await expect(
    dashboard
      .getByTestId("timeline-lane")
      .and(page.locator('[data-lane-id="worker"]')),
  ).toHaveCount(1);
  // And the other rows are untouched: opening one node is not opening the graph.
  await expect(
    graphRow(page, "publish").getByTestId("timeline-lane"),
  ).toHaveCount(1);
});

test("keeps every graph row on one scale when any of them is zoomed", async ({
  page,
}) => {
  await openObservatory(page, `/?run=${runs().live}&view=overall`);
  await expandGraphRows(page);
  const dashboard = graphRow(page, "dashboard");
  const runLevel = page.getByRole("region", { name: "Run-level timeline" });

  // Every plot starts on the same clock, which is what makes a column of one row
  // mean the same instant as the column above it.
  const before = await axisTicks(graphLine(page));
  expect(await axisTicks(dashboard)).toBe(before);
  expect(await axisTicks(runLevel)).toBe(before);

  // Zooming *one* row reframes all of them, because there is one range and every
  // plot is drawn against it.
  await dashboard.getByLabel(/^Timeline plot/).hover();
  await page.mouse.wheel(0, -300);
  await expect.poll(() => axisTicks(dashboard)).not.toBe(before);
  const zoomed = await axisTicks(dashboard);
  expect(await axisTicks(graphLine(page))).toBe(zoomed);
  expect(await axisTicks(runLevel)).toBe(zoomed);

  // And resetting from any of them puts every one of them back.
  await runLevel.getByRole("button", { name: "Reset timeline zoom" }).click();
  await expect.poll(() => axisTicks(graphLine(page))).toBe(before);
  expect(await axisTicks(dashboard)).toBe(before);
});

test("draws the stretches the run recorded nothing in", async ({ page }) => {
  await openObservatory(page, `/?run=${runs().live}&view=overall`);
  await expandGraphRows(page);
  const runLevel = page.getByRole("region", { name: "Run-level timeline" });

  // The run-level row records the driver, then a gap, then the round's check-in.
  // That gap is drawn as a segment of its own: blank space is the one reading to
  // avoid here, because it cannot be told from a record that is missing.
  const idle = runLevel.getByRole("button", { name: /^Idle · / }).first();
  await expect(idle).toBeVisible();
  // Distinct to look at, not merely to a screen reader: work is a solid bar and
  // silence is hatched, so a glance at the row already separates the two.
  const hatching = await idle.evaluate(
    (element) => getComputedStyle(element).backgroundImage,
  );
  expect(hatching).toContain("repeating-linear-gradient");
  const working = await runLevel
    .getByRole("button", { name: /^Run-level · Orchestrator/ })
    .evaluate((element) => getComputedStyle(element).backgroundImage);
  expect(working).not.toContain("repeating-linear-gradient");

  // And how long it was silent is on the segment itself rather than left to be
  // measured off the axis.
  await idle.hover();
  await expect(page.getByRole("tooltip")).toContainText("Lane: idle");

  // A node the run never reached is that same reading for its whole life.
  const queued = graphRow(page, "queued");
  await expect(queued.getByRole("button", { name: /^Idle · / })).toHaveCount(1);
  await expect(queued.getByRole("button", { name: /^queued · / })).toHaveCount(
    0,
  );

  // A gap too narrow to see is not drawn at all. This run's journal is written in
  // one pass, so `foundation` verified and published milliseconds apart inside a
  // window of minutes — a hairline nobody could read or click. Only the two gaps at
  // the ends survive, and they always do: they are what makes every row span the
  // same interval, which is what one shared zoom rests on.
  await expect(
    graphRow(page, "foundation").getByRole("button", { name: /^Idle · / }),
  ).toHaveCount(2);

  // How much of its life each row spent working, and how much it did not, is on the
  // row rather than left to be measured off the plot.
  await expect(
    graphRowCard(page, "queued").locator(".graph-row-facts"),
  ).toHaveText(/^0ms recorded · \d+m \d+s idle$/);
});

test("frames a different run from scratch when the reader moves to it", async ({
  page,
}) => {
  await openObservatory(page, `/?run=${runs().live}&view=overall`);
  await expandGraphRows(page);
  const dashboard = graphRow(page, "dashboard");
  await dashboard.getByRole("button", { name: "Expand timeline" }).click();
  await dashboard.getByLabel(/^Timeline plot/).hover();
  await page.mouse.wheel(0, -300);
  await expect(
    graphLine(page).getByRole("button", { name: "Reset timeline zoom" }),
  ).toBeEnabled();

  // A different run is a different clock, so none of that framing follows it there:
  // one run's zoom over another run's record would be a plot of nothing.
  await page.getByRole("button", { name: RegExp(runs().history) }).click();
  await expect(graphLine(page)).toBeVisible();
  await expect(page.getByRole("region", { name: /\stimeline$/ })).toHaveCount(
    1,
  );
  await expect(
    graphLine(page).getByRole("button", { name: "Reset timeline zoom" }),
  ).toBeDisabled();
});

test("says a run has recorded no timeline rather than drawing an empty one", async ({
  page,
}) => {
  // The served `dag-ui-eventless` run has its round prepared and has journalled
  // nothing at all — what every run looks like for its first moments. There is no
  // clock to plot, and saying so is not the same answer as an empty plot.
  await openObservatory(page, `/?run=${runs().eventless}&view=overall`);
  await expect(
    page.getByText("This run has recorded no timeline yet."),
  ).toBeVisible();
  await expect(graphLine(page)).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("drills from a graph row into that node's own view", async ({ page }) => {
  await openObservatory(page, `/?run=${runs().live}&view=overall`);
  await expandGraphRows(page);
  // The row's own name is one way in, from the level where the rows first appear.
  await graphRowCard(page, "foundation")
    .getByRole("button", { name: "foundation", exact: true })
    .click();
  await expect(page).toHaveURL(/node=foundation/);
  await expect(
    page.getByRole("region", { name: "Timeline for foundation" }),
  ).toBeVisible();

  // And so is any segment of it, at the level where each category has its own lane.
  await page.getByRole("tab", { name: "Overall" }).click();
  await expandGraphRows(page);
  const foundation = graphRow(page, "foundation");
  await foundation.getByRole("button", { name: "Expand timeline" }).click();
  await foundation
    .getByRole("button", { name: /^foundation · Verification/ })
    .click();
  await expect(page).toHaveURL(/node=foundation/);

  // Silence counts as one of its segments: the row *is* the node, working or not.
  await page.getByRole("tab", { name: "Overall" }).click();
  await expandGraphRows(page);
  await graphRow(page, "dashboard")
    .getByRole("button", { name: /^Idle · / })
    .first()
    .click();
  await expect(page).toHaveURL(/node=dashboard/);
  await expect(
    page.getByRole("region", { name: "Timeline for dashboard" }),
  ).toBeVisible();
});

test("connects to the server's event stream on load", async ({ page }) => {
  await openObservatory(page);
  // The server opens every connection with a snapshot, so the header gains a
  // last-updated reading only once the browser's EventSource really connected. The
  // journeys below then change the served run and assert what the stream carries.
  await expect(page.getByText(/Last updated/)).toBeVisible();
});

test("recovers the selection when a bookmarked run is not being served", async ({
  page,
}) => {
  await openObservatory(page, "/?run=absent-run&node=dashboard");
  // The server serves no such run, so the view falls back to a real one and
  // rewrites the address rather than stranding the operator on an empty graph.
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "dashboard",
  );
  await expect
    .poll(() => new URL(page.url()).search)
    .toContain(`run=${runs().live}`);

  // The same fallback from the overall reading keeps the operator in it: only the
  // run under the view is rewritten, so a stale bookmark never also moves them.
  await openObservatory(page, "/?run=absent-run");
  await expect(graphLine(page)).toBeVisible();
  await expect(page.getByRole("tab", { name: "Overall" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect
    .poll(() => new URL(page.url()).search)
    .toContain(`run=${runs().live}`);
});

test("reflows navigation, detail, and metrics at a narrow viewport", async ({
  page,
}) => {
  const width = async (locator: Locator): Promise<number | undefined> =>
    (await locator.boundingBox())?.width;
  const navigation = page.getByRole("navigation", { name: "DAG runs" });
  const metrics = page.locator(".metric");
  /**
   * Which way, if either, the whole reading spills out of the viewport it was given.
   *
   * A view that overflows the document does not merely look wrong: the widest row
   * sized the working area and clipped every other one against the right edge, and
   * a document with anywhere to scroll to gets scrolled by the first
   * `scrollIntoView` the transcript makes — taking the navigation and the pinned
   * timeline off screen with it.
   */
  const viewportOverflow = async () =>
    page.evaluate(() => {
      const root = document.documentElement;
      return {
        overflowsX: root.scrollWidth > root.clientWidth,
        overflowsY: root.scrollHeight > root.clientHeight,
      };
    });

  await page.setViewportSize({ width: 1400, height: 900 });
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  expect(await viewportOverflow()).toEqual({
    overflowsX: false,
    overflowsY: false,
  });
  await timeline(page)
    .getByRole("button", { name: /engineer-dashboard/ })
    .click();
  await expect(navigation).toBeVisible();
  expect(await width(navigation)).toBe(280);
  // The plot keeps the full working width for its bars, and whatever is opened over
  // it takes two thirds of that width — enough to read a turn in.
  expect(await width(timeline(page))).toBeGreaterThan(380);
  expect((await width(itemDetail(page))) ?? 0).toBeCloseTo(
    (1400 - 280) * (2 / 3),
    -1,
  );
  await page.getByRole("button", { name: "Close detail" }).click();
  // Four metrics across one row while there is room for them.
  await page.getByRole("tab", { name: "Overall" }).click();
  await expect(metrics).toHaveCount(4);
  const wideRows = await metrics.evaluateAll((tiles) =>
    tiles.map((tile) => tile.getBoundingClientRect().top),
  );
  expect(new Set(wideRows).size).toBe(1);

  await page.setViewportSize({ width: 800, height: 700 });
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  // Below the layout's breakpoint the six named readings wrap onto a second row
  // rather than widening the view that holds them — and rather than overflowing a
  // centred row, which spilled the first and last of them past both edges of a
  // scroller that could only ever reach one of the two.
  expect(await viewportOverflow()).toEqual({
    overflowsX: false,
    overflowsY: false,
  });
  const tabStrip = page.getByRole("tablist", { name: "Node details" });
  const stripBox = await tabStrip.boundingBox();
  if (stripBox === null) throw new Error("the node tab strip has no bounds");
  const labels = await tabStrip.getByRole("tab").all();
  expect(labels).toHaveLength(6);
  for (const label of labels) {
    // Both axes: the row they spill past horizontally is only reachable one way, and
    // the row they spill past vertically is drawn under the reading below and reaches
    // nobody at all.
    const box = await label.boundingBox();
    if (box === null) throw new Error("a node tab has no bounds");
    expect(box.x).toBeGreaterThanOrEqual(stripBox.x);
    expect(box.x + box.width).toBeLessThanOrEqual(stripBox.x + stripBox.width);
    expect(box.y).toBeGreaterThanOrEqual(stripBox.y);
    expect(box.y + box.height).toBeLessThanOrEqual(
      stripBox.y + stripBox.height,
    );
    await expect(label).toBeInViewport();
  }
  await timeline(page)
    .getByRole("button", { name: /engineer-dashboard/ })
    .click();
  // Everything stays on screen: the navigation and the rail each give up width,
  // and the metrics wrap onto a second row instead of being squeezed.
  await expect(navigation).toBeVisible();
  expect(await width(navigation)).toBe(220);
  expect(await width(timeline(page))).toBeGreaterThan(190);
  expect((await width(itemDetail(page))) ?? 0).toBeCloseTo(
    (800 - 220) * (2 / 3),
    -1,
  );
  await page.getByRole("button", { name: "Close detail" }).click();
  // The panel opened and closed over the view without ever giving the document
  // somewhere to scroll to.
  expect(await viewportOverflow()).toEqual({
    overflowsX: false,
    overflowsY: false,
  });
  await page.getByRole("tab", { name: "Overall" }).click();
  await expect(metrics).toHaveCount(4);
  const narrowRows = await metrics.evaluateAll((tiles) =>
    tiles.map((tile) => tile.getBoundingClientRect().top),
  );
  expect(new Set(narrowRows).size).toBe(2);
});

test("keeps the timeline's clock readable when its lanes outgrow the view", async ({
  page,
}) => {
  /** How far the axis falls outside the region that holds it, in pixels. */
  const clipped = async (): Promise<number> => {
    const region = await timeline(page).boundingBox();
    const axis = await timeline(page)
      .getByTestId("timeline-axis")
      .boundingBox();
    if (region === null || axis === null)
      throw new Error("the timeline has no bounds to read");
    return Math.max(
      0,
      region.y - axis.y,
      axis.y + axis.height - (region.y + region.height),
    );
  };
  /** Whether the plot really is taller than the room it was given. */
  const overflowing = async (): Promise<boolean> =>
    timeline(page).evaluate(
      (element) => element.scrollHeight > element.clientHeight,
    );

  // The laptop the layout is designed against, the compact size below its breakpoint,
  // and the phone the matrix ends at: ten lanes and a reading do not both fit the
  // last two at any share of them, so the viewports state different things about the
  // expanded plot. The collapsed one they all state the same thing about.
  for (const viewport of [
    { width: 1400, height: 900, expandedFits: true },
    { width: 800, height: 700, expandedFits: false },
    { width: PHONE.width, height: PHONE.height, expandedFits: false },
  ]) {
    await page.setViewportSize(viewport);
    await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
    await expect(timeline(page).getByTestId("timeline-axis")).toBeVisible();
    // The view an operator lands on is never cut: the collapsed plot is one line, and
    // the region holds it and its clock whole at every width — including the one
    // whose ten lane names wrap the legend onto a second row.
    expect(await overflowing()).toBe(false);
    expect(await clipped()).toBe(0);

    await timeline(page)
      .getByRole("button", { name: "Expand timeline" })
      .click();
    expect(await overflowing()).toBe(!viewport.expandedFits);
    if (!viewport.expandedFits) {
      // Where the lanes cannot fit, the region scrolls rather than dropping what it
      // could not draw, and the clock is at the end of that scroll — whole, not the
      // half-drawn line of digits the bottom edge used to leave. The wheel over the
      // region is how an operator reaches that end, so it is what carries the journey
      // there; a `scrollTop` written from script would prove the layout without ever
      // proving the region really scrolls under one.
      await timeline(page).hover();
      await page.mouse.wheel(0, 10_000);
      await expect
        .poll(() =>
          timeline(page).evaluate(
            (element) =>
              element.scrollHeight - element.clientHeight - element.scrollTop,
          ),
        )
        .toBeLessThanOrEqual(1);
    }
    expect(await clipped()).toBe(0);
    // Still the axis it was, not a stub of one: both ticks, each naming the wall
    // clock and the elapsed time the reader tracks lanes against.
    const ticks = timeline(page).getByTestId("timeline-axis").locator("span");
    await expect(ticks).toHaveCount(2);
    for (const tick of await ticks.allTextContents()) {
      expect(tick).toMatch(/\d{2}:\d{2}:\d{2}.*[+−]\d/u);
    }
    await expect(ticks.first()).toBeInViewport();
  }
});

test("paints the design system's components in the application's dark palette", async ({
  page,
}) => {
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);

  // `dark` on the document element is the switch @oneharness/ui's stylesheet selects
  // its dark tokens with. Without it every component the package ships renders its
  // light default inside this dark application shell.
  await expect(page.locator("html")).toHaveClass(/\bdark\b/);

  // The node view's own cards are the package's Card, so their surface proves two
  // things at once: that the utilities its components are written in are generated
  // for this app at all, and that they resolve to the dark token rather than white.
  await timeline(page)
    .getByRole("button", { name: /engineer-dashboard/ })
    .click();
  const card = await backgroundColor(
    itemDetail(page).locator('[data-slot="card"]').first(),
  );
  // An opaque `rgb(…)`: a token this build never defined would leave the utility
  // invalid and the surface transparent, which is the shape this must not accept.
  expect(card).toMatch(/^rgb\(\d+, \d+, \d+\)$/);
  expect(card).toBe(await tokenColor(page, "--card"));
  expect(brightestChannel(card)).toBeLessThan(80);

  // The transcripts the package renders sit on that same surface, which is the
  // defect an operator saw on every node they opened.
  const turn = page.getByRole("article", { name: /^Turn / }).first();
  await expect(turn).toBeVisible();
  expect(
    await backgroundColor(
      turn.locator("xpath=ancestor::*[@data-slot='card'][1]"),
    ),
  ).toBe(card);

  // And the application's own chrome is painted from the same token set rather than
  // a hand-picked palette beside it.
  const panel = await backgroundColor(timeline(page));
  expect(panel).toMatch(/^rgb\(\d+, \d+, \d+\)$/);
  expect(panel).toBe(await tokenColor(page, "--background"));

  // The graph canvas scopes its own variables, so it needs its own switch; without
  // it the zoom controls stay white inside the dark workspace. They are reached by
  // leaving the node view, which is the only place the canvas renders.
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");
  expect(
    brightestChannel(
      await backgroundColor(
        page.locator(".react-flow__controls-button").first(),
      ),
    ),
  ).toBeLessThan(80);
});

test("tells each outcome apart by the palette's semantic tones", async ({
  page,
}) => {
  await openObservatory(page);
  // The node view states its node's state in words beside the graph's colour, which
  // is the only reading of it available to anyone who cannot rely on that colour.
  // Each reading is checked against its word too, so a selector that drifted onto
  // one of the view's other badges would fail rather than pass quietly.
  const stateBadge = page.locator('.node-view-facts > [data-slot="badge"]');

  /**
   * Open one node's view, retrying the *click* and not only the reading of what it
   * produced: closing a view mounts a fresh canvas, and a click delivered into that
   * remount selects nothing, which no assertion under it can wait out. Only this
   * journey opens eight views in sequence, so only it is exposed. See [The three ways
   * a wall-clock assertion is
   * fixed](../../../docs/repo-lifecycle.md#the-three-ways-a-wall-clock-assertion-is-fixed).
   */
  const openNode = async (card: Locator, state: string): Promise<void> => {
    await expect(async () => {
      // Whatever a previous attempt opened has to close again before the canvas is
      // back on screen to be clicked at all.
      if ((await page.locator(".node-view").count()) > 0) {
        await page.keyboard.press("Escape");
        await expect(page.locator(".node-view")).toHaveCount(0);
      }
      await card.click();
      await expect(stateBadge).toHaveText(state);
    }).toPass({ timeout: 45_000 });
  };

  // Reading a state costs an operator nothing only while the outcomes look different:
  // settled work green, work that was lost red, work still moving blue. The design
  // system's own status vocabulary stops at four states and includes none of these
  // words, so without the app's mapping every one of them paints the same neutral
  // pill. `toHaveCSS` rather than one reading of the computed style: the badge
  // transitions its colour, so an immediate read catches it partway between two.
  for (const { state, token } of [
    { state: "done", token: "--success" },
    { state: "cancelled", token: "--destructive" },
    { state: "failed", token: "--destructive" },
    { state: "running", token: "--info" },
  ]) {
    await openNode(page.locator(`.dag-node.state-${state}`).first(), state);
    await expect(stateBadge).toHaveCSS("color", await tokenColor(page, token));
    await page.keyboard.press("Escape");
  }

  // Held work is neither settled nor lost: it needs something outside it to move, and
  // painting it neutral would say there is nothing to report about a node that is
  // going nowhere. `waiting` keeps its neutral badge beside its amber card — a human
  // action is the graph's own normal shape, and the card is where that is said.
  const held = await tokenColor(page, "--warning");
  for (const state of ["blocked", "skipped"]) {
    await openNode(page.locator(`.dag-node.state-${state}`), state);
    await expect(stateBadge).toHaveCSS("color", held);
    await page.keyboard.press("Escape");
  }

  // Work that has not started has no outcome to report, so it must not borrow one of
  // those meanings — which is also what stops the assertions above from passing on a
  // mapping that simply paints everything.
  const neutral = await tokenColor(page, "--foreground");
  for (const state of ["waiting", "pending"]) {
    await openNode(page.locator(`.dag-node.state-${state}`), state);
    await expect(stateBadge).toHaveCSS("color", neutral);
    await page.keyboard.press("Escape");
  }

  // The run list is the other surface that states an outcome, and `complete` is a
  // state the package's own badge does not know at all.
  const runBadge = (runId: string): Locator =>
    page
      .getByRole("button", { name: RegExp(runId) })
      .locator('[data-slot="badge"]');
  await expect(runBadge(runs().history)).toHaveCSS(
    "color",
    await tokenColor(page, "--success"),
  );
  await expect(runBadge(runs().live)).toHaveCSS(
    "color",
    await tokenColor(page, "--info"),
  );
  // A run's state is an open string in the read contract, and the sibling run's
  // executor stopped without recording a result — a real state with no outcome in it.
  // The list has to say the word and stop there rather than colour it in.
  await expect(runBadge(runs().sibling)).toHaveText("stopped");
  await expect(runBadge(runs().sibling)).toHaveCSS("color", neutral);

  // And the canvas says the same things on its own surfaces, out of the same tokens
  // rather than the hex values it used to carry. `waiting` is blocked work, the one
  // meaning the cards state and the badges deliberately do not.
  for (const { state, token } of [
    { state: "done", token: "--success-surface" },
    { state: "failed", token: "--destructive-surface" },
    { state: "running", token: "--info-surface" },
    { state: "waiting", token: "--warning-surface" },
    { state: "blocked", token: "--warning-surface" },
    { state: "skipped", token: "--warning-surface" },
  ]) {
    await expect(page.locator(`.dag-node.state-${state}`).first()).toHaveCSS(
      "background-color",
      await tokenColor(page, token),
    );
  }
});

test("shows the loading view while its first read is still in flight", async ({
  page,
}) => {
  // A UI origin proxying to a listener that accepts and never answers: the app's
  // own request really is outstanding, which is the only honest way to hold the
  // loading view still long enough to look at.
  await page.goto(STALLED_UI_URL);
  await expect(page.getByText("Loading execution history…")).toBeVisible();
  // Placeholder bars stand where the run will be, so the wait reads as work in
  // progress rather than as a screen that has finished and found nothing.
  await expect(page.locator('[data-slot="skeleton"]').first()).toBeVisible();
  await expect(page.getByText("No DAG runs found")).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("surfaces a telemetry read it cannot complete", async ({ page }) => {
  // A UI origin whose proxy target is not listening: the browser's own fetch and
  // EventSource both fail for real, and the operator must be told rather than shown
  // an empty graph that looks like "no runs yet".
  await page.goto(OFFLINE_UI_URL);
  const banner = page.getByRole("alert");
  await expect(banner).toContainText("Live telemetry issue");
  // The banner names the failure as well as announcing one: an operator who cannot
  // see what broke cannot tell a wedged server from a mistyped API address.
  await expect(
    banner.locator('[data-slot="alert-description"]'),
  ).not.toBeEmpty();
  await expect(page.getByText("Waiting for first update")).toBeVisible();

  // The one control that can retry the read stays reachable while the read is
  // failing, and reporting the failure again is the honest outcome of pressing it.
  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(banner).toContainText("Live telemetry issue");
});

// The remaining journeys change what the server is serving, so they run last and in
// order: each one leaves the fixture advanced for the ones after it.

test("streams real progress the server observes on disk", async ({ page }) => {
  await openObservatory(page);
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "dashboard",
  );

  // Record progress the way the executor does: one appended authoritative event.
  // The server's own poll notices it and invalidates the run over SSE.
  changeServedRuns(["--settle-dashboard"]);

  await expect(
    page.locator(".dag-node.state-done", { hasText: "dashboard" }),
  ).toBeVisible();
  await expect(page.locator(".dag-node.state-running")).toHaveCount(0);
  await expect(page.getByText(/Last updated/)).toBeVisible();
});

test("shows mid-turn activity from a live dispatch", async ({ page }) => {
  await openObservatory(page, `/?run=${runs().live}&view=graph`);
  await page
    .getByRole("button", { name: /dashboard: (running|done)/ })
    .press("Enter");
  await page
    .getByRole("region", { name: "Node transcript" })
    .getByRole("button", { name: /^Open Worker \(engineer-dashboard\)/ })
    .click();
  await expect(
    page.getByText("Implementing the dashboard now").first(),
  ).toBeVisible();
  changeServedRuns(["--stream-dashboard"]);
  await expect(
    page.getByText("dashboard: Read orchestrator/server.py"),
  ).toBeVisible();
  await expect(
    page.getByText("Streaming the dashboard response now"),
  ).toBeVisible();

  // A newly opened run-scoped stream receives activity that was already live,
  // rather than waiting for the next publication to change it.
  await page.reload();
  await expect(
    page.getByText("dashboard: Read orchestrator/server.py"),
  ).toBeVisible();

  // Ending the publication emits an empty activity set and clears the live summary.
  changeServedRuns(["--clear-dashboard-stream"]);
  await expect(
    page.getByText("dashboard: Read orchestrator/server.py"),
  ).toHaveCount(0);
});

async function detailScroll(
  page: Page,
): Promise<{ top: number; bottom: number }> {
  return itemDetail(page)
    .locator('[data-slot="scroll-area-viewport"]')
    .evaluate((element) => ({
      top: element.scrollTop,
      bottom: element.scrollHeight - element.scrollTop - element.clientHeight,
    }));
}

async function wheelDetail(page: Page, delta: number): Promise<void> {
  const panel = await itemDetail(page).boundingBox();
  if (panel === null) throw new Error("the detail panel is not on screen");
  await page.mouse.move(panel.x + panel.width / 2, panel.y + panel.height / 2);
  await page.mouse.wheel(0, delta);
}

/** A wheel this far in either direction reaches the end of any transcript here. */
const WHEEL_TO_THE_END = 100_000;

test("follows a growing transcript only while the reader is at its end", async ({
  page,
}) => {
  test.slow();
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  await page
    .getByRole("region", { name: "Node transcript" })
    .getByRole("button", { name: /^Open Worker \(engineer-dashboard\)/ })
    .click();
  await expect(itemDetail(page)).toContainText(
    "Implementing the dashboard now",
  );

  // Long enough that the panel really scrolls, and short enough that the reader is
  // still handed every turn rather than a page of them.
  changeServedRuns(["--grow-worker-session", "20"]);
  await expect(itemDetail(page)).toContainText("Dashboard turn 19 arrived");
  await wheelDetail(page, WHEEL_TO_THE_END);
  await expect
    .poll(async () => (await detailScroll(page)).bottom)
    .toBeLessThan(40);
  // The panel really does overflow, so being at its end is a position a reader chose
  // rather than the only one there is.
  expect((await detailScroll(page)).top).toBeGreaterThan(0);

  // Read at the end, the panel follows what the run writes next.
  changeServedRuns(["--grow-worker-session", "21"]);
  await expect(itemDetail(page)).toContainText("Dashboard turn 20 arrived");
  await expect
    .poll(async () => (await detailScroll(page)).bottom)
    .toBeLessThan(40);

  // Read anywhere else, it does not: the reader keeps the position they chose while
  // the transcript keeps growing underneath them.
  await wheelDetail(page, -WHEEL_TO_THE_END);
  await expect.poll(async () => (await detailScroll(page)).top).toBe(0);
  changeServedRuns(["--grow-worker-session", "22"]);
  await expect(itemDetail(page)).toContainText("Dashboard turn 21 arrived");
  expect((await detailScroll(page)).top).toBe(0);
  // And the turn it was opened on was never taken away and put back.
  await expect(itemDetail(page)).toContainText(
    "Implementing the dashboard now",
  );

  // Opening this long a session lands at its beginning: following a transcript that
  // is still being written is not the same as skipping to the last thing it said.
  await page.reload();
  await expect(itemDetail(page)).toContainText("Dashboard turn 21 arrived");
  expect((await detailScroll(page)).top).toBe(0);
});

test("drops a run the server stops serving", async ({ page }) => {
  await openObservatory(page);
  await expect(
    page.getByRole("button", { name: RegExp(runs().history) }),
  ).toBeVisible();

  changeServedRuns(["--remove-run", runs().history]);

  await expect(
    page.getByRole("button", { name: RegExp(runs().history) }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: RegExp(runs().live) }),
  ).toBeVisible();
});

test("falls back to the empty state once no run is left", async ({ page }) => {
  await openObservatory(page);
  changeServedRuns(["--remove-page-runs"]);

  // Every remaining run except one — the journey before this removed the historical
  // one. The empty state means the server serves none, so it must not appear while
  // any run is still there to show, whatever shape that run is.
  for (const runId of [
    runs().live,
    runs().outcomes,
    runs().legacy,
    runs().unattributed,
    runs().eventless,
    runs().busy,
  ]) {
    changeServedRuns(["--remove-run", runId]);
    await expect(page.getByText("No DAG runs found")).toHaveCount(0);
  }
  changeServedRuns(["--remove-run", runs().sibling]);

  await expect(page.getByText("No DAG runs found")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});
