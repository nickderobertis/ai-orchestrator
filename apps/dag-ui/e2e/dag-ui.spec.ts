import { execFileSync } from "node:child_process";
import { rmSync } from "node:fs";
import { join } from "node:path";
import { expect, type Page, test } from "@playwright/test";
import { FIXTURE_WORKSPACE, OFFLINE_UI_URL } from "../playwright.config";

/**
 * The DAG Observatory driven end to end against a real `orchestrator/server.py`
 * serving a real recorded run directory (see `e2e/fixtures/serve_fixture.py`, started
 * by `playwright.config.ts`). Nothing between the browser and the read model is
 * doubled: the app's own telemetry client makes the HTTP and SSE requests, and the
 * server projects them from journal files the executor's own writers produced. Live
 * updates are provoked by changing that run directory, never by faking an event.
 */

const LIVE_RUN = "dag-ui-live";
const HISTORY_RUN = "dag-ui-history";

/** Wait for the first projected graph so a journey never races the initial read. */
async function openObservatory(page: Page, path = "/"): Promise<void> {
  await page.goto(path);
  await expect(page.getByText("DAG Observatory")).toBeVisible();
}

/** Change the run directory the server is serving, through the executor's writers. */
function advanceFixture(args: string[]): void {
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
  await expect(
    page.getByRole("link", { name: /github\.com\/example\/repo\/pull\/12/ }),
  ).toBeVisible();
  await expect(page.getByText("Gate completed successfully")).toBeVisible();
  await expect(
    page.getByText("No conversations recorded for this node."),
  ).toBeVisible();
});

test("navigates historical DAGs grouped by their launching session", async ({
  page,
}) => {
  await openObservatory(page);
  await expect(page.getByText(/Codex session/)).toBeVisible();
  await expect(page.getByText(/Claude session/)).toBeVisible();

  await page.getByRole("button", { name: RegExp(HISTORY_RUN) }).click();
  await expect(page.locator(".dag-node.state-done")).toContainText("archive");
  await page.getByRole("button", { name: RegExp(LIVE_RUN) }).click();
  await expect(page.locator(".dag-node.state-running")).toContainText(
    "dashboard",
  );
  await page.goBack();
  await expect(page.locator(".dag-node.state-done")).toContainText("archive");
});

test("restores a bookmarked view and refreshes through the read API", async ({
  page,
}) => {
  await openObservatory(page, `/?run=${LIVE_RUN}&view=overall`);
  await expect(page.getByText("Planner session")).toBeVisible();
  await expect(
    page.getByText("Coordinating the execution frontier"),
  ).toBeVisible();

  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.getByText("Planner session")).toBeVisible();

  await openObservatory(page, `/?run=${LIVE_RUN}&node=dashboard`);
  await expect(page.getByText("Build the live dashboard")).toBeVisible();
});

test("reports a live update from the server's own event stream", async ({
  page,
}) => {
  await openObservatory(page);
  // The server opens every connection with a snapshot, so the header flips to
  // "Updates received" only once the browser's EventSource really connected.
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
    .toContain(`run=${LIVE_RUN}`);
});

test("keeps navigation usable at a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 700 });
  await openObservatory(page);
  const navigation = page.getByRole("navigation", { name: "DAG runs" });
  await expect(navigation).toBeVisible();
  expect((await navigation.boundingBox())?.width).toBe(220);
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
  advanceFixture(["--settle-dashboard"]);

  await expect(
    page.locator(".dag-node.state-done", { hasText: "dashboard" }),
  ).toBeVisible();
  await expect(page.locator(".dag-node.state-running")).toHaveCount(0);
  await expect(page.getByText("Updates received")).toBeVisible();
});

test("drops a run the server stops serving", async ({ page }) => {
  await openObservatory(page);
  await expect(
    page.getByRole("button", { name: RegExp(HISTORY_RUN) }),
  ).toBeVisible();

  rmSync(join(FIXTURE_WORKSPACE, "runs", HISTORY_RUN), {
    recursive: true,
    force: true,
  });

  await expect(
    page.getByRole("button", { name: RegExp(HISTORY_RUN) }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: RegExp(LIVE_RUN) }),
  ).toBeVisible();
});

test("falls back to the empty state once no run is left", async ({ page }) => {
  await openObservatory(page);

  rmSync(join(FIXTURE_WORKSPACE, "runs", LIVE_RUN), {
    recursive: true,
    force: true,
  });

  await expect(page.getByText("No DAG runs found")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});
