import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { HISTORY_RUN, LIVE_RUN, runDetail, runList } from "../test/fixtures";
import {
  defaultResponder,
  isRunDetail,
  isRunList,
  telemetryHarness,
} from "../test/telemetry-harness";
import { App } from "./App";
import { AppErrorBoundary } from "./AppErrorBoundary";

describe("DAG application", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
  });

  afterEach(cleanup);

  test("renders live status and drills into every attributed transcript role", async () => {
    const { client } = telemetryHarness();
    render(<App client={client} />);
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
    expect(screen.getByText("publish").closest(".dag-node")).toHaveClass(
      "state-failed",
    );
    expect(screen.getByText("queued").closest(".dag-node")).toHaveClass(
      "state-pending",
    );

    fireEvent.click(screen.getByRole("button", { name: "dashboard: running" }));
    expect(
      await screen.findByText("Build the live dashboard"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Users can inspect transcripts"),
    ).toBeInTheDocument();
    expect(screen.getByText("Worker")).toBeInTheDocument();
    expect(screen.getByText("Judge")).toBeInTheDocument();
    expect(screen.getByText("Check-in")).toBeInTheDocument();
    expect(screen.getByText("PR author")).toBeInTheDocument();
    expect(screen.getByText("Lint")).toBeInTheDocument();
    expect(
      screen.getByText("Implementing the dashboard now"),
    ).toBeInTheDocument();
  });

  test("groups historical runs by launcher and reloads on SSE invalidation", async () => {
    const { client, sources, fetch } = telemetryHarness();
    render(<App client={client} />);
    expect(await screen.findByText(/Codex session/)).toBeInTheDocument();
    expect(screen.getByText(/Claude session/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: RegExp(HISTORY_RUN) }));
    await waitFor(() =>
      expect(window.location.search).toContain(`run=${HISTORY_RUN}`),
    );
    expect(await screen.findByText("archive")).toBeInTheDocument();

    const before = fetch.mock.calls.length;
    sources[0]?.emit("run.changed", { run_id: HISTORY_RUN, round: 1 }, "8");
    await waitFor(() =>
      expect(fetch.mock.calls.length).toBeGreaterThan(before),
    );
  });

  test("shows the orchestrator in the overall view", async () => {
    const { client } = telemetryHarness();
    render(<App client={client} />);
    await screen.findByText("dashboard");
    fireEvent.click(screen.getByRole("tab", { name: "Overall" }));
    await waitFor(() =>
      expect(window.location.search).toContain("view=overall"),
    );
    expect(await screen.findByText("Planner session")).toBeInTheDocument();
    expect(
      screen.getByText("Coordinating the execution frontier"),
    ).toBeInTheDocument();
  });

  test("refreshes on demand and restores a bookmarked node selection", async () => {
    window.history.replaceState(null, "", `/?run=${HISTORY_RUN}&node=archive`);
    const { client, fetch } = telemetryHarness();
    render(<App client={client} />);
    expect(await screen.findByText("Archive the release")).toBeInTheDocument();

    const before = fetch.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() =>
      expect(fetch.mock.calls.length).toBeGreaterThan(before),
    );

    window.history.replaceState(null, "", `/?run=${LIVE_RUN}`);
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
  });

  test("shows the loading state, then an empty history", async () => {
    let release: (response: Response) => void = () => {};
    const { client } = telemetryHarness((url) => {
      if (isRunList(url))
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      return defaultResponder(url);
    });
    render(<App client={client} />);
    expect(screen.getByText("Loading execution history…")).toBeInTheDocument();

    release(Response.json({ ...runList, runs: [] }));
    expect(await screen.findByText("No DAG runs found")).toBeInTheDocument();
  });

  test("surfaces a read failure and clears it when the stream reconnects", async () => {
    let offline = true;
    const { client, sources } = telemetryHarness((url) => {
      if (isRunList(url) && offline)
        return Response.json(
          { error: { code: "offline", message: "Telemetry offline" } },
          { status: 503 },
        );
      return defaultResponder(url);
    });
    render(<App client={client} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Telemetry offline",
    );

    // A reconnecting browser re-opens the stream with a fresh snapshot, which is
    // the app's evidence that live telemetry recovered.
    offline = false;
    sources[0]?.emit("snapshot", runList, "1");
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
  });

  test("surfaces a snapshot that fails contract validation", async () => {
    const { client, sources } = telemetryHarness();
    render(<App client={client} />);
    await screen.findByText("dashboard");

    // A peer that ships a schema the app does not accept must be reported, not
    // silently rendered from whatever survived.
    sources[0]?.emit(
      "snapshot",
      { ...runList, telemetry_schema_version: 8 },
      "5",
    );
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("dashboard")).toBeInTheDocument();
  });

  test("surfaces a dropped event stream", async () => {
    const { client, sources } = telemetryHarness();
    render(<App client={client} />);
    await screen.findByText("dashboard");

    sources[0]?.fail();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Live telemetry stream disconnected",
    );
  });

  test("surfaces a detail failure triggered by a live update", async () => {
    let details = 0;
    const { client, sources } = telemetryHarness((url) => {
      if (isRunDetail(url)) {
        details += 1;
        if (details > runList.runs.length)
          return Response.json(
            { error: { code: "offline", message: "Detail unavailable" } },
            { status: 503 },
          );
      }
      return defaultResponder(url);
    });
    render(<App client={client} />);
    await screen.findByText("dashboard");

    sources[0]?.emit("run.changed", { run_id: LIVE_RUN, round: 1 }, "2");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Detail unavailable",
    );
  });

  test("stays quiet when an invalidated run has already been removed", async () => {
    let removed = false;
    const { client, sources } = telemetryHarness((url) => {
      if (isRunList(url))
        return Response.json(
          removed ? { ...runList, runs: runList.runs.slice(1) } : runList,
        );
      if (removed && url.pathname.endsWith(LIVE_RUN))
        return Response.json(
          { error: { code: "run_not_found", message: "no recorded run" } },
          { status: 404 },
        );
      return defaultResponder(url);
    });
    render(<App client={client} />);
    await screen.findByText("dashboard");

    // The invalidation names a run the sweep has already taken away; the view
    // follows the list instead of reporting a telemetry failure.
    removed = true;
    sources[0]?.emit("run.changed", { run_id: LIVE_RUN, round: 1 }, "4");
    expect(await screen.findByText("archive")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("hands an unrenderable graph to the error boundary", async () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
    const { client } = telemetryHarness((url) => {
      if (isRunList(url)) return Response.json(runList);
      const detail = runDetail(LIVE_RUN);
      // A dependency cycle: the layout rejects it, and no partial graph may be
      // shown in its place.
      const tasks: { deps?: string[] }[] = detail.rounds[0]?.plan.tasks ?? [];
      if (tasks[0]) tasks[0].deps = ["dashboard"];
      return Response.json(detail);
    });
    render(
      <AppErrorBoundary>
        <App client={client} />
      </AppErrorBoundary>,
    );
    expect(
      await screen.findByText("The DAG view could not be displayed."),
    ).toBeInTheDocument();
    expect(screen.getByText("DAG contains a cycle")).toBeInTheDocument();
    consoleError.mockRestore();
  });

  test("drops a removed run and falls back to the remaining one", async () => {
    let removed = false;
    const { client, sources } = telemetryHarness((url) => {
      if (isRunList(url))
        return Response.json(
          removed ? { ...runList, runs: runList.runs.slice(1) } : runList,
        );
      if (url.pathname.endsWith(HISTORY_RUN))
        return Response.json(runDetail(HISTORY_RUN));
      return Response.json(runDetail(LIVE_RUN));
    });
    render(<App client={client} />);
    await screen.findByText("dashboard");

    removed = true;
    sources[0]?.emit("run.removed", { run_id: LIVE_RUN }, "3");
    expect(await screen.findByText("archive")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: RegExp(LIVE_RUN) })).toBeNull();
  });
});
