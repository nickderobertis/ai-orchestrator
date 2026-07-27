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
 * The runs the fixture wrote, read from the file it publishes beside them. The
 * Python module that records them is their one source; naming them again here would
 * be a second one that drifts the moment the fixture changes.
 */
const runIdsSchema = z.object({
  live: z.string().min(1),
  history: z.string().min(1),
  sibling: z.string().min(1),
  unattributed: z.string().min(1),
  eventless: z.string().min(1),
});
let cachedRunIds: z.infer<typeof runIdsSchema> | undefined;
const runs = (): z.infer<typeof runIdsSchema> =>
  (cachedRunIds ??= runIdsSchema.parse(
    JSON.parse(readFileSync(join(FIXTURE_WORKSPACE, "run-ids.json"), "utf8")),
  ));

/** Open the app and wait for it to have mounted; each journey then asserts its own state. */
async function openObservatory(page: Page, path = "/"): Promise<void> {
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

test("tracks every node state, node detail, and role transcript of a live run", async ({
  page,
}) => {
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
  await expect(page.locator(".dag-node.state-pending")).toContainText("queued");
  await expect(page.locator(".dag-node.state-cancelled")).toContainText(
    "obsolete",
  );

  await page.locator(".dag-node.state-running").click();
  await expect(page.getByText("Build the live dashboard")).toBeVisible();
  await expect(page.getByText("Users can inspect transcripts")).toBeVisible();
  for (const role of ["Worker", "Judge", "Check-in", "PR author", "Lint"]) {
    await expect(page.getByText(role, { exact: true })).toBeVisible();
  }
  await expect(page.getByText("Implementing the dashboard now")).toBeVisible();
  await expect(page.getByText("Drafted the pull request")).toBeVisible();

  await page.getByRole("button", { name: /Close/ }).click();
  await expect(page.getByText("Build the live dashboard")).toHaveCount(0);

  await page.locator(".dag-node.state-done").click();
  const section = (name: string) =>
    page
      .locator(".detail-section")
      .filter({ has: page.getByRole("heading", { name }) });
  await expect(
    section("Pull request").getByRole("link", {
      name: /github\.com\/example\/repo\/pull\/12/,
    }),
  ).toBeVisible();
  await expect(section("Logs")).toContainText("Gate completed successfully");
  // The gate result is the attestation the verification recorded for this node.
  await expect(page.locator(".facts")).toContainText("comparison_base");
  await expect(
    page.getByText("No conversations recorded for this node."),
  ).toBeVisible();

  // The failed node published nothing, and the panel says so rather than leaving an
  // empty block that reads as "all clear".
  await page.locator(".dag-node.state-failed").click();
  await expect(section("Pull request")).toContainText("Not recorded");
  await expect(section("Logs")).toContainText("Deploy failed");

  // A human action names work for a person, so the contract forbids it a completion
  // bar; the panel has to say that rather than render an empty criteria block.
  await page.locator(".dag-node.state-waiting").click();
  await expect(page.getByText("Wait for release approval")).toBeVisible();
  await expect(
    page.getByText("No completion criteria recorded."),
  ).toBeVisible();
});

test("opens a node from the keyboard-accessible node list", async ({
  page,
}) => {
  await openObservatory(page);
  // The canvas is a pointer surface, so the list beside it is the keyboard path to
  // every node; it has to reach the same detail panel a click does.
  const node = page
    .getByRole("list", { name: "DAG nodes" })
    .getByRole("button", { name: "dashboard: running" });
  expect(await tabTo(page, node)).toBe(true);
  await page.keyboard.press("Enter");
  await expect(page.getByText("Build the live dashboard")).toBeVisible();

  await page.getByRole("button", { name: /Close/ }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Build the live dashboard")).toHaveCount(0);
});

test("zooms and reframes the graph through its canvas controls", async ({
  page,
}) => {
  await openObservatory(page);
  const viewport = page.locator(".react-flow__viewport");
  const transform = async (): Promise<string> =>
    viewport.evaluate((element) => getComputedStyle(element).transform);

  await expect(page.locator(".react-flow__minimap")).toBeVisible();
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

  await page.getByRole("button", { name: RegExp(runs().history) }).click();
  await expect(page.locator(".dag-node.state-done")).toContainText("archive");
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
  await expect(metric("Wall time")).toContainText(/\d+\.\ds/);
  await expect(metric("Turns")).toContainText(/\d+/);
  await expect(page.getByText("Planner session")).toBeVisible();
  await expect(
    page.getByText("Coordinating the execution frontier"),
  ).toBeVisible();

  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.getByText("Planner session")).toBeVisible();

  await page.getByRole("tab", { name: "Graph" }).click();
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "dashboard",
  );
  await expect(page.getByText("Planner session")).toHaveCount(0);

  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  await expect(page.getByText("Build the live dashboard")).toBeVisible();
});

test("gathers every run of one launching session under it", async ({
  page,
}) => {
  await openObservatory(page);
  // Two of the served runs record the same launch id, as one planner session driving
  // two graphs does. They belong to one group, not one group each.
  const codex = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: /Codex session/ }) });
  await expect(
    page.getByRole("heading", { name: /Codex session/ }),
  ).toHaveCount(1);
  await expect(codex.getByRole("button")).toHaveCount(2);
  await expect(codex).toContainText(runs().live);
  await expect(codex).toContainText(runs().sibling);

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

test("says so when a run recorded no planner conversation", async ({
  page,
}) => {
  // The settled run's history holds a worker session and no orchestrator one, so
  // its overall view has no planner transcript to show.
  await openObservatory(page, `/?run=${runs().history}&view=overall`);
  await expect(page.getByText("Planner session")).toBeVisible();
  await expect(
    page.getByText("No planner conversation is available."),
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
});

test("reflows navigation, detail, and metrics at a narrow viewport", async ({
  page,
}) => {
  const width = async (locator: Locator): Promise<number | undefined> =>
    (await locator.boundingBox())?.width;
  const navigation = page.getByRole("navigation", { name: "DAG runs" });
  const panel = page.locator(".detail-panel");
  const metrics = page.locator(".metric");

  await page.setViewportSize({ width: 1400, height: 900 });
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  await expect(navigation).toBeVisible();
  expect(await width(navigation)).toBe(280);
  expect(await width(panel)).toBe(440);
  // Four metrics across one row while there is room for them.
  await page.getByRole("tab", { name: "Overall" }).click();
  await expect(metrics).toHaveCount(4);
  const wideRows = await metrics.evaluateAll((tiles) =>
    tiles.map((tile) => tile.getBoundingClientRect().top),
  );
  expect(new Set(wideRows).size).toBe(1);

  await page.setViewportSize({ width: 800, height: 700 });
  await openObservatory(page, `/?run=${runs().live}&node=dashboard`);
  // Everything stays on screen: the navigation and the detail panel each give up
  // width, and the metrics wrap onto a second row instead of being squeezed.
  await expect(navigation).toBeVisible();
  expect(await width(navigation)).toBe(220);
  await expect(panel).toBeVisible();
  expect(await width(panel)).toBe(360);
  await page.getByRole("tab", { name: "Overall" }).click();
  await expect(metrics).toHaveCount(4);
  const narrowRows = await metrics.evaluateAll((tiles) =>
    tiles.map((tile) => tile.getBoundingClientRect().top),
  );
  expect(new Set(narrowRows).size).toBe(2);
});

test("shows the loading view while its first read is still in flight", async ({
  page,
}) => {
  // A UI origin proxying to a listener that accepts and never answers: the app's
  // own request really is outstanding, which is the only honest way to hold the
  // loading view still long enough to look at.
  await page.goto(STALLED_UI_URL);
  await expect(page.getByText("Loading execution history…")).toBeVisible();
  await expect(page.getByText("No DAG runs found")).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("surfaces a telemetry read it cannot complete", async ({ page }) => {
  // A UI origin whose proxy target is not listening: the browser's own fetch and
  // EventSource both fail for real, and the operator must be told rather than shown
  // an empty graph that looks like "no runs yet".
  await page.goto(OFFLINE_UI_URL);
  await expect(page.getByRole("alert")).toContainText("Live telemetry issue");
  await expect(page.getByText("Awaiting updates")).toBeVisible();
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
  for (const runId of [runs().live, runs().unattributed, runs().eventless]) {
    changeServedRuns(["--remove-run", runId]);
    await expect(page.getByText("No DAG runs found")).toHaveCount(0);
  }
  changeServedRuns(["--remove-run", runs().sibling]);

  await expect(page.getByText("No DAG runs found")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});
