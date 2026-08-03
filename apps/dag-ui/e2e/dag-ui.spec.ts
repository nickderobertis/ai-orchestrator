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

/**
 * The DAG Observatory driven end to end against a real `orchestrator/server.py`
 * serving a real recorded run directory (see `e2e/fixtures/serve_fixture.py`, started
 * by `playwright.config.ts`). Nothing between the browser and the read model is
 * doubled: the app's own telemetry client makes the HTTP and SSE requests, and the
 * server projects them from journal files the executor's own writers produced. Live
 * updates are provoked by changing that run directory, never by faking an event.
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

/** Whether repeated Tab presses ever land on `target`, i.e. it is in the tab order. */
async function tabTo(
  page: Page,
  target: Locator,
  presses = 40,
): Promise<boolean> {
  for (let index = 0; index < presses; index += 1) {
    await page.keyboard.press("Tab");
    if (
      await target.evaluate((element) => element === document.activeElement)
    ) {
      return true;
    }
  }
  return false;
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

/** The node view's master rail, once a node has been opened. */
const rail = (page: Page): Locator =>
  page.getByRole("region", { name: "Node timeline" });

/** The node view's detail region: whichever timeline item is open, expanded. */
const itemDetail = (page: Page): Locator =>
  page.getByRole("region", { name: "Timeline item detail" });

test("tracks every node state and kind of a live run", async ({ page }) => {
  await openObservatory(page);

  await expect(page.locator(".dag-node.state-done")).toContainText(
    "foundation",
  );
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "dashboard",
  );
  await expect(page.locator(".dag-node.state-failed")).toContainText("publish");
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
  await expect(page.locator(".dag-node.state-failed")).toContainText(
    "Deploy failed",
  );
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
  const taskBox = await page
    .getByRole("button", { name: "Task" })
    .boundingBox();
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
  // is what every `repo-plan` run looks like. The per-node derivation cannot run, so
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

  await expect(itemDetail(page)).toContainText(
    "Select an item in the timeline to read what it recorded.",
  );

  // Every row of the rail states what was recorded, when, how it ended, and how
  // long it took — which is the whole reason the transcript dump was unreadable.
  const worker = rail(page).getByRole("button", { name: /engineer-dashboard/ });
  await expect(worker).toContainText("dispatch");
  await expect(worker).toContainText("completed");
  await expect(worker).toContainText(/\d\d:\d\d:\d\d/);
  await expect(rail(page).getByRole("button")).not.toHaveCount(0);

  await worker.click();
  await expect
    .poll(() => new URL(page.url()).searchParams.get("event"))
    .toBe("dispatch-worker-session");
  await expect(itemDetail(page)).toContainText(
    "Implementing the dashboard now",
  );
  await expect(itemDetail(page)).toContainText("Worker");
  await expect(
    itemDetail(page).getByRole("article", { name: /^Turn / }),
  ).toBeVisible();
  // The detail region is where the reading happens, so it holds the majority of
  // the width rather than a fixed narrow column.
  const railWidth = (await rail(page).boundingBox())?.width ?? 0;
  const detailWidth = (await itemDetail(page).boundingBox())?.width ?? 0;
  expect(detailWidth).toBeGreaterThan(railWidth);

  // A span contains its events, and opening it discloses them: one turn here.
  const turn = rail(page).getByRole("button", { name: /conversation-turn/ });
  await expect(turn.first()).toBeVisible();
  await turn.first().click();
  await expect(itemDetail(page)).toContainText(
    "Implementing the dashboard now",
  );

  // Escape is the keyboard way back to the graph.
  await page.keyboard.press("Escape");
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "dashboard",
  );
});

test("restores a bookmarked moment inside a session from the address alone", async ({
  page,
}) => {
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  await rail(page)
    .getByRole("button", { name: /engineer-dashboard/ })
    .click();
  const turn = rail(page)
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
  await expect(
    rail(page)
      .getByRole("button", { name: /conversation-turn/ })
      .first(),
  ).toHaveAttribute("aria-current", "true");
  await expect(itemDetail(page)).toContainText(
    "Implementing the dashboard now",
  );
});

test("opens a node from the keyboard-accessible node list and walks back", async ({
  page,
}) => {
  await openObservatory(page);
  // The canvas is a pointer surface, so the list beside it is the keyboard path to
  // every node; it has to reach the same node view a click does.
  const node = page
    .getByRole("list", { name: "DAG nodes" })
    .getByRole("button", { name: "dashboard: running" });
  expect(await tabTo(page, node)).toBe(true);
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

  // The verification carries the gate attestation the node recorded, and points at
  // the preserved log rather than inlining it.
  await rail(page)
    .getByRole("button", { name: /branch push/ })
    .click();
  await expect(itemDetail(page)).toContainText("Gate attestation");
  await expect(itemDetail(page)).toContainText("comparison_base");
  await expect(itemDetail(page)).toContainText("round-01/foundation/gate.log");

  // The publication carries the PR and the checks that were observed on it.
  await rail(page)
    .getByRole("button", { name: /local\/example/ })
    .click();
  await expect(
    itemDetail(page).getByRole("link", {
      name: /github\.com\/example\/repo\/pull\/12/,
    }),
  ).toBeVisible();
  await expect(itemDetail(page)).toContainText("Observed checks");
  await expect(itemDetail(page)).toContainText("unit");

  // Its own recorded events sit inside it, in the order they happened.
  const checks = rail(page).getByRole("button", {
    name: /pr-checks-observed/,
  });
  await expect(checks).toContainText("passing");
  await checks.click();
  await expect(itemDetail(page)).toContainText("Observed checks");
});

test("keeps a node's task, criteria, dependencies and gate reachable", async ({
  page,
}) => {
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  // A compact summary, not a wall of blocks: each part is one disclosure away.
  await page.getByRole("button", { name: "Task" }).click();
  await expect(page.getByText("Build the live dashboard")).toBeVisible();
  await page.getByRole("button", { name: "Completion criteria" }).click();
  await expect(page.getByText("Users can inspect transcripts")).toBeVisible();

  await page.getByRole("button", { name: "Dependencies, PR and gate" }).click();
  await expect(page.locator(".facts")).toContainText("Not recorded");
  await expect(page.locator(".facts").getByRole("link")).toHaveCount(0);

  await openObservatory(page, `/?run=${runs().live}&node=foundation`);
  await page.getByRole("button", { name: "Dependencies, PR and gate" }).click();
  const pr = page.locator(".facts").getByRole("link");
  await expect(pr).toHaveAttribute("href", fixture().foundation_pr);
  await expect(pr).toHaveAttribute("target", "_blank");
  await expect(pr).toHaveAttribute("rel", "noreferrer");

  // A human action names work for a person, so the contract forbids it a completion
  // bar; the summary has to say that rather than render an empty criteria block.
  await openObservatory(page, `/?run=${runs().live}&node=approval`);
  await page.getByRole("button", { name: "Completion criteria" }).click();
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
  const rows = rail(page).getByRole("button");
  await expect(rows.first()).toBeVisible();
  const grouped = rail(page).getByRole("button", { name: /× dispatch/ });
  await expect(grouped).toBeVisible();
  expect(await rows.count()).toBeLessThan(12);

  // Opening the group hands out a page of it, not every row at once.
  await grouped.click();
  await expect(
    rail(page).getByRole("button", { name: /Show 25 more of \d\d\d/ }),
  ).toBeVisible();
  expect(await rows.count()).toBeLessThan(60);

  // And one session's own turns are paged the same way inside the detail region.
  await rail(page)
    .getByRole("button", { name: /engineer-sweep-7\b/ })
    .click();
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
  await page.getByRole("button", { name: "Dependencies, PR and gate" }).click();
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
  await expect(page.getByText("Run-level sessions")).toBeVisible();
  await expect(
    page.getByText("Coordinating the execution frontier"),
  ).toBeVisible();

  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.getByText("Run-level sessions")).toBeVisible();

  await page.getByRole("tab", { name: "Graph" }).click();
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "dashboard",
  );
  await expect(page.getByText("Run-level sessions")).toHaveCount(0);

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
  await expect(page.getByText("Run-level sessions")).toBeVisible();
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
  await expect(
    page.getByText("No run-level conversation is available."),
  ).toBeVisible();
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
  await expect(page.getByText("Run-level sessions")).toBeVisible();
  await expect(page.locator(".dag-node")).toHaveCount(0);
});

test("reads every recorded moment as words rather than as its stamp", async ({
  page,
}) => {
  // `approval` recorded work the run never closed, so its one item is shown as the
  // typed record it is — which is where two raw ISO strings used to reach the reader.
  await openObservatory(page, `/?run=${runs().live}&node=approval`);
  await rail(page)
    .getByRole("button", { name: /human-wait/ })
    .click();
  await expect(itemDetail(page)).toContainText("Recorded at");
  await expect(itemDetail(page)).toContainText("Still running");
  // Neither an ISO stamp nor a raw second count anywhere the reader is looking.
  await expect(rail(page)).not.toContainText(/\d{4}-\d\d-\d\dT/);
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

  // And across a node whose rail is a column of them, every reading the browser
  // rendered is one of the shapes the formatters produce — not the one row this
  // journey happened to open. A tier that slipped through as a bare number or an
  // ISO string would be a row that matches none of them.
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  await rail(page).getByRole("button").first().waitFor();
  const times = await rail(page).locator(".rail-time").allInnerTexts();
  expect(times.length).toBeGreaterThan(0);
  for (const reading of times) {
    expect(reading).toMatch(
      /^(\d\d:\d\d:\d\d|[A-Z][a-z]{2} \d+(?: \d{4})?, \d\d:\d\d:\d\d)$/,
    );
  }
  // Each tier is bounded by the one above it, so a reading that carried a whole
  // duration in the unit below — the `58000.0s` an operator was doing arithmetic on —
  // matches none of these.
  const durations = await rail(page).locator(".rail-duration").allInnerTexts();
  expect(durations.length).toBeGreaterThan(0);
  for (const reading of durations) {
    expect(reading).toMatch(
      /^(—|running|\d{1,3}ms|[1-5]?\ds|\d+m [1-5]?\ds|\d+h [1-5]?\dm [1-5]?\ds)$/,
    );
  }
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

test("groups a run with no recorded launch under an unknown session", async ({
  page,
}) => {
  await openObservatory(page);
  // Wait for the attributed groups first: until a run's detail arrives it has no
  // transcript to attribute, so every group reads as unknown for that moment.
  await expect(page.getByText(/Codex session/)).toBeVisible();
  await expect(page.getByText(/Claude session/)).toBeVisible();
  // The server serves this run with no launch join and no transcripts at all; it
  // still has to be reachable rather than dropped from the navigation. Every
  // unattributed run gets its own unknown group, so name this run's group rather
  // than the only one.
  await expect(
    sessionGroup(page, runs().unattributed).getByRole("heading", {
      name: /Unknown session/,
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
  // round's check-in beside it. Only the first is open on arrival.
  await openObservatory(page, `/?run=${runs().live}&view=overall`);
  await expect(
    page.getByText("Coordinating the execution frontier"),
  ).toBeVisible();
  await expect(page.getByText("Round 1 progress reported")).toHaveCount(0);
  await expect.poll(() => transcripts.size).toBe(1);

  // Opening the check-in discloses it and reads its own transcript only then.
  await page.getByRole("button", { name: /check-in-.*round-1/ }).click();
  await expect(page.getByText("Round 1 progress reported")).toBeVisible();
  await expect.poll(() => transcripts.size).toBe(2);
});

test("says so when a run recorded no run-level conversation", async ({
  page,
}) => {
  // The settled run's history holds a worker session and no orchestrator one, so
  // its overall view has no planner transcript to show.
  await openObservatory(page, `/?run=${runs().history}&view=overall`);
  await expect(page.getByText("Run-level sessions")).toBeVisible();
  await expect(
    page.getByText("No run-level conversation is available."),
  ).toBeVisible();
});

test("connects to the server's event stream on load", async ({ page }) => {
  await openObservatory(page);
  // The server opens every connection with a snapshot, so the header flips to
  // "Updates received" only once the browser's EventSource really connected. The
  // journeys below then change the served run and assert what the stream carries.
  await expect(page.getByText("Updates received")).toBeVisible();
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
  await expect(page.getByText("Run-level sessions")).toBeVisible();
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

  await page.setViewportSize({ width: 1400, height: 900 });
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  await expect(navigation).toBeVisible();
  expect(await width(navigation)).toBe(280);
  // The rail is a fixed reading column; the detail region takes what is left, and
  // has to keep the majority of it — that is the whole point of the new view.
  expect(await width(rail(page))).toBe(320);
  expect(await width(itemDetail(page))).toBeGreaterThan(
    (await width(rail(page))) ?? 0,
  );
  // Four metrics across one row while there is room for them.
  await page.getByRole("tab", { name: "Overall" }).click();
  await expect(metrics).toHaveCount(4);
  const wideRows = await metrics.evaluateAll((tiles) =>
    tiles.map((tile) => tile.getBoundingClientRect().top),
  );
  expect(new Set(wideRows).size).toBe(1);

  await page.setViewportSize({ width: 800, height: 700 });
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  // Everything stays on screen: the navigation and the rail each give up width,
  // and the metrics wrap onto a second row instead of being squeezed.
  await expect(navigation).toBeVisible();
  expect(await width(navigation)).toBe(220);
  expect(await width(rail(page))).toBe(200);
  expect(await width(itemDetail(page))).toBeGreaterThan(
    (await width(rail(page))) ?? 0,
  );
  await page.getByRole("tab", { name: "Overall" }).click();
  await expect(metrics).toHaveCount(4);
  const narrowRows = await metrics.evaluateAll((tiles) =>
    tiles.map((tile) => tile.getBoundingClientRect().top),
  );
  expect(new Set(narrowRows).size).toBe(2);
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
  await rail(page)
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
  const panel = await backgroundColor(rail(page));
  expect(panel).toMatch(/^rgb\(\d+, \d+, \d+\)$/);
  expect(panel).toBe(await tokenColor(page, "--sidebar"));

  // The graph canvas scopes its own variables, so it needs its own switch; without
  // it the zoom controls stay white inside the dark workspace. They are reached by
  // leaving the node view, which is the only place the canvas renders.
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
    await page.locator(`.dag-node.state-${state}`).click();
    await expect(stateBadge).toHaveText(state);
    await expect(stateBadge).toHaveCSS("color", await tokenColor(page, token));
    await page.keyboard.press("Escape");
  }

  // Held work is neither settled nor lost: it needs something outside it to move, and
  // painting it neutral would say there is nothing to report about a node that is
  // going nowhere. `waiting` keeps its neutral badge beside its amber card — a human
  // action is the graph's own normal shape, and the card is where that is said.
  const held = await tokenColor(page, "--warning");
  for (const state of ["blocked", "skipped"]) {
    await page.locator(`.dag-node.state-${state}`).click();
    await expect(stateBadge).toHaveText(state);
    await expect(stateBadge).toHaveCSS("color", held);
    await page.keyboard.press("Escape");
  }

  // Work that has not started has no outcome to report, so it must not borrow one of
  // those meanings — which is also what stops the assertions above from passing on a
  // mapping that simply paints everything.
  const neutral = await tokenColor(page, "--foreground");
  for (const state of ["waiting", "pending"]) {
    await page.locator(`.dag-node.state-${state}`).click();
    await expect(stateBadge).toHaveText(state);
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
    await expect(page.locator(`.dag-node.state-${state}`)).toHaveCSS(
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
  await expect(page.getByText("Awaiting updates")).toBeVisible();

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
  await expect(page.getByText("Updates received")).toBeVisible();
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
