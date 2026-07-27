import { expect, type Page, test } from "@playwright/test";

/**
 * The DAG Observatory driven end to end against a real `orchestrator/server.py`
 * serving a real recorded run directory (see `e2e/fixtures/serve_fixture.py`, started
 * by `playwright.config.ts`). Nothing between the browser and the read model is
 * doubled: the app's own telemetry client makes the HTTP and SSE requests, and the
 * server projects them from journal files the executor's own writers produced.
 *
 * llmlint: ignore-file[e2e_not_mocked] Every journey but the last drives the real
 * loopback read API. The last one rewrites that server's own response in flight
 * because a cyclic graph is the one condition the executor's writers reject outright,
 * so it cannot be recorded into the fixture the server serves.
 */

const LIVE_RUN = "dag-ui-live";
const HISTORY_RUN = "dag-ui-history";

/** Wait for the first projected graph so a journey never races the initial read. */
async function openObservatory(page: Page, path = "/"): Promise<void> {
  await page.goto(path);
  await expect(page.getByText("DAG Observatory")).toBeVisible();
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

test("catches an unrenderable graph and offers reload recovery", async ({
  page,
}) => {
  // The one place a route is intercepted: a cyclic graph cannot be recorded through
  // the executor's writers at all, so the failure has to be injected at the network.
  await page.route(`**/api/v1/runs/${LIVE_RUN}`, async (route) => {
    const response = await route.fetch();
    const detail = (await response.json()) as {
      rounds: { plan: { tasks: { id: string; deps?: string[] }[] } }[];
    };
    for (const task of detail.rounds[0]?.plan.tasks ?? []) {
      if (task.id === "foundation") task.deps = ["dashboard"];
    }
    await route.fulfill({ json: detail });
  });
  await page.goto("/");
  await expect(
    page.getByText("The DAG view could not be displayed."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Reload" }).click();
  await expect(
    page.getByText("The DAG view could not be displayed."),
  ).toBeVisible();
});
