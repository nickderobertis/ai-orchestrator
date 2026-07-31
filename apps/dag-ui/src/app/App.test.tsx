import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  busyTimeline,
  HISTORY_RUN,
  LIVE_RUN,
  LONG_SESSION,
  longConversation,
  ROUND_CHECK_IN_SESSION,
  runDetail,
  runList,
  runTimeline,
} from "../test/fixtures";
import {
  defaultResponder,
  isConversation,
  isRunDetail,
  isRunList,
  isTimeline,
  telemetryHarness,
} from "../test/telemetry-harness";
import { App } from "./App";
import { AppErrorBoundary } from "./AppErrorBoundary";

/** The rail row a reader would click, named by the words it puts on screen. */
const railRow = (name: RegExp) =>
  within(screen.getByRole("region", { name: "Node timeline" })).getByRole(
    "button",
    { name },
  );

const detail = () =>
  screen.getByRole("region", { name: "Timeline item detail" });

describe("DAG application", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
  });

  afterEach(cleanup);

  test("opens a node in its own view, reads its timeline, and returns", async () => {
    const { client } = telemetryHarness();
    render(<App client={client} />);
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
    expect(screen.getByText("publish").closest(".dag-node")).toHaveClass(
      "state-failed",
    );

    fireEvent.click(screen.getByRole("button", { name: "dashboard: running" }));
    // The node takes over the working area: the graph is gone, and a breadcrumb
    // stands where it was.
    expect(
      await screen.findByRole("region", { name: "Timeline for dashboard" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "DAG nodes" })).toBeNull();
    expect(
      screen.getByRole("navigation", { name: "Breadcrumb" }),
    ).toHaveTextContent("dashboard");

    // Every row states what happened, when, how it ended, and how long it took.
    const worker = railRow(/engineer-dashboard/);
    expect(worker).toHaveTextContent("dispatch");
    expect(worker).toHaveTextContent("11:00:12");
    expect(worker).toHaveTextContent("completed");
    expect(worker).toHaveTextContent("48.0s");
    // An aggregate says how many records it stands in for.
    expect(railRow(/lock-wait/)).toHaveTextContent("×1240");

    await userEvent.click(worker);
    await waitFor(() =>
      expect(window.location.search).toContain("event=dispatch-worker-session"),
    );
    expect(
      await within(detail()).findByText("Implementing the dashboard now"),
    ).toBeInTheDocument();
    expect(within(detail()).getByText("Worker · engineer")).toBeInTheDocument();

    // Escape is the keyboard way back to the graph.
    await userEvent.keyboard("{Escape}");
    expect(await screen.findByText("queued")).toBeInTheDocument();
    expect(window.location.search).not.toContain("node=");
  });

  test("walks back to the graph from the breadcrumb button", async () => {
    window.history.replaceState(
      null,
      "",
      `/?run=${LIVE_RUN}&node=dashboard&event=dispatch-judge-session`,
    );
    const { client } = telemetryHarness();
    render(<App client={client} />);
    // A bookmarked moment is restored, expanded, from the address alone.
    await screen.findByRole("region", { name: "Timeline for dashboard" });
    expect(
      await within(detail()).findByText("The transcript is accessible"),
    ).toBeInTheDocument();

    const back = screen.getByRole("button", { name: /Graph/ });
    back.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText("obsolete")).toBeInTheDocument();
  });

  test("expands a session to the one turn a bookmark names", async () => {
    window.history.replaceState(
      null,
      "",
      `/?run=${LIVE_RUN}&node=dashboard&event=worker-session-0`,
    );
    const { client } = telemetryHarness();
    render(<App client={client} />);
    // The rail reveals the row the address names, however deep it sits.
    expect(
      await screen.findByRole("button", { name: /conversation-turn/ }),
    ).toHaveAttribute("aria-current", "true");
    expect(
      await within(detail()).findByText("Implementing the dashboard now"),
    ).toBeInTheDocument();
  });

  test("shows a verification, a publication, and an aggregate as themselves", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=foundation`);
    const { client } = telemetryHarness();
    render(<App client={client} />);

    await userEvent.click(
      await screen.findByRole("button", { name: /just gate/ }),
    );
    expect(await within(detail()).findByText("Gate attestation")).toBeVisible();
    expect(within(detail()).getByText("comparison_base")).toBeInTheDocument();
    expect(
      within(detail()).getByText("round-01/foundation/gate.log"),
    ).toBeInTheDocument();

    await userEvent.click(railRow(/local\/example/));
    expect(
      await within(detail()).findByRole("link", {
        name: /github\.com\/example\/repo\/pull\/12/,
      }),
    ).toBeInTheDocument();
    // A PR event states the checks that were observed on it, not just its url.
    expect(within(detail()).getByText("Observed checks")).toBeInTheDocument();
    expect(within(detail()).getByText("unit")).toBeInTheDocument();

    // An item with no dedicated rendering still shows every field the timeline
    // recorded for it rather than an empty pane.
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=dashboard`);
    window.dispatchEvent(new PopStateEvent("popstate"));
    await userEvent.click(
      await screen.findByRole("button", { name: /lock-wait/ }),
    );
    expect(
      await within(detail()).findByText("1240 records"),
    ).toBeInTheDocument();
    expect(within(detail()).getByText("Reference")).toBeInTheDocument();
  });

  test("keeps a node whose recorded work is hundreds of sessions scannable", async () => {
    const { client } = telemetryHarness((url) =>
      isTimeline(url)
        ? Response.json(busyTimeline(200))
        : defaultResponder(url),
    );
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=dashboard`);
    render(<App client={client} />);

    const rail = await screen.findByRole("region", { name: "Node timeline" });
    // Two hundred conversations, and a rail a reader can take in at a glance.
    expect(
      within(rail).getByRole("button", { name: /204 × dispatch/ }),
    ).toBeInTheDocument();
    expect(within(rail).getAllByRole("button").length).toBeLessThan(12);

    // Opening the group hands out a page at a time rather than every row at once.
    await userEvent.click(
      within(rail).getByRole("button", { name: /204 × dispatch/ }),
    );
    const paged = within(rail).getAllByRole("button");
    expect(paged.length).toBeLessThan(60);
    expect(
      within(rail).getByRole("button", { name: /Show 25 more of 204/ }),
    ).toBeInTheDocument();
  });

  test("names what a failed node's attempts did not record", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=publish`);
    const { client } = telemetryHarness();
    render(<App client={client} />);

    // A gate that never reached an attestation, and a publication with no PR and
    // no observed checks: each absence is stated rather than left as a blank block
    // that reads like "all clear".
    await userEvent.click(
      await screen.findByRole("button", { name: /branch push/ }),
    );
    expect(
      await within(detail()).findByText(
        "This verification recorded no gate attestation.",
      ),
    ).toBeInTheDocument();
    expect(within(detail()).getByText("No log was recorded.")).toBeVisible();

    await userEvent.click(railRow(/publication/));
    expect(
      await within(detail()).findByText("No pull request was recorded."),
    ).toBeInTheDocument();
    expect(
      within(detail()).getByText("No checks were observed on this node."),
    ).toBeInTheDocument();
  });

  test("says so when an opened turn is no longer in its transcript", async () => {
    window.history.replaceState(
      null,
      "",
      `/?run=${LIVE_RUN}&node=dashboard&event=worker-session-0`,
    );
    // The timeline was folded from a history store that has since been rewritten,
    // so the turn it names is not in the transcript the server serves back.
    const { client } = telemetryHarness((url) =>
      isConversation(url)
        ? Response.json(longConversation())
        : defaultResponder(url),
    );
    render(<App client={client} />);
    expect(
      await screen.findByText(
        "This turn is no longer part of the recorded transcript.",
      ),
    ).toBeInTheDocument();
  });

  test("reports the overall view's sessions as unread rather than absent", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&view=overall`);
    let release: (response: Response) => void = () => {};
    const held = telemetryHarness((url) => {
      if (isTimeline(url))
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      return defaultResponder(url);
    });
    const view = render(<App client={held.client} />);
    // "No run-level conversation" would be a claim about a record nothing has read.
    expect(
      await screen.findByText("Loading the run's sessions…"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("No run-level conversation is available."),
    ).toBeNull();
    release(Response.json(runTimeline(LIVE_RUN)));
    expect(
      await screen.findByText("Coordinating the execution frontier"),
    ).toBeInTheDocument();
    view.unmount();

    const failing = telemetryHarness((url) =>
      isTimeline(url)
        ? Response.json(
            { error: { code: "unreadable", message: "Journal is corrupt" } },
            { status: 500 },
          )
        : defaultResponder(url),
    );
    render(<App client={failing.client} />);
    expect(
      await screen.findByText(/The run's sessions could not be read/),
    ).toBeInTheDocument();
  });

  test("hands a long session to the reader a page at a time", async () => {
    window.history.replaceState(
      null,
      "",
      `/?run=${HISTORY_RUN}&node=archive&event=dispatch-${LONG_SESSION}`,
    );
    const { client } = telemetryHarness();
    render(<App client={client} />);
    await screen.findByRole("region", { name: "Timeline for archive" });
    // Thirty recorded turns: the reader is shown a page and told what is left,
    // rather than handed the whole session on selection.
    expect(await within(detail()).findByText("Archive step 0")).toBeVisible();
    expect(within(detail()).getByText("Archive step 24")).toBeInTheDocument();
    expect(within(detail()).queryByText("Archive step 25")).toBeNull();

    await userEvent.click(
      within(detail()).getByRole("button", { name: /Show more of 30 turns/ }),
    );
    expect(
      await within(detail()).findByText("Archive step 29"),
    ).toBeInTheDocument();
  });

  test("reports a transcript the server cannot serve", async () => {
    window.history.replaceState(
      null,
      "",
      `/?run=${HISTORY_RUN}&node=archive&event=dispatch-${LONG_SESSION}`,
    );
    const { client } = telemetryHarness((url) =>
      isConversation(url)
        ? Response.json(
            { error: { code: "unreadable", message: "History store is gone" } },
            { status: 503 },
          )
        : defaultResponder(url),
    );
    render(<App client={client} />);
    // The rail still reads; only the body of the session is missing, and that is
    // what has to be said rather than an empty card.
    expect(
      await screen.findByText("Transcript unavailable"),
    ).toBeInTheDocument();
    expect(screen.getByText("History store is gone")).toBeInTheDocument();
  });

  test("reports a planner transcript the server cannot serve", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&view=overall`);
    const { client } = telemetryHarness((url) =>
      isConversation(url)
        ? Response.json(
            { error: { code: "unreadable", message: "History store is gone" } },
            { status: 503 },
          )
        : defaultResponder(url),
    );
    render(<App client={client} />);
    expect(
      await screen.findByText(/This transcript could not be read/),
    ).toBeInTheDocument();
  });

  test("reports a node timeline still on its way, then invites a selection", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=dashboard`);
    let release: (response: Response) => void = () => {};
    const { client } = telemetryHarness((url) => {
      if (isTimeline(url))
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      return defaultResponder(url);
    });
    render(<App client={client} />);
    // The node view opens before its record has arrived. "No recorded timeline"
    // would be a claim about a journal nothing has read yet.
    expect(
      await screen.findByText("Loading the recorded timeline…"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("This node has no recorded timeline yet."),
    ).toBeNull();

    // It arrives with no item named in the address, so the detail region says how
    // to read one rather than standing empty beside a full rail.
    release(Response.json(runTimeline(LIVE_RUN)));
    const region = await screen.findByRole("region", {
      name: "Timeline item detail",
    });
    expect(
      within(region).getByText(
        "Select an item in the timeline to read what it recorded.",
      ),
    ).toBeInTheDocument();
    expect(railRow(/engineer-dashboard/)).toBeInTheDocument();
  });

  test("says so when a node has no recorded timeline, and when the read fails", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=queued`);
    const { client } = telemetryHarness();
    const view = render(<App client={client} />);
    expect(
      await screen.findByText("This node has no recorded timeline yet."),
    ).toBeInTheDocument();
    view.unmount();

    const failing = telemetryHarness((url) =>
      isTimeline(url)
        ? Response.json(
            { error: { code: "unreadable", message: "Journal is corrupt" } },
            { status: 500 },
          )
        : defaultResponder(url),
    );
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=dashboard`);
    render(<App client={failing.client} />);
    expect(await screen.findByText("Timeline unavailable")).toBeInTheDocument();
    expect(screen.getByText("Journal is corrupt")).toBeInTheDocument();
  });

  test("keeps the node's task, criteria, dependencies and gate reachable", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=dashboard`);
    const { client } = telemetryHarness();
    render(<App client={client} />);

    await userEvent.click(await screen.findByRole("button", { name: "Task" }));
    expect(
      await screen.findByText("Build the live dashboard"),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Completion criteria" }),
    );
    expect(
      await screen.findByText("Users can inspect transcripts"),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Dependencies, PR and gate" }),
    );
    expect(await screen.findByText("foundation")).toBeInTheDocument();
  });

  test("reads only the selected run, and a transcript only when one is opened", async () => {
    const { client, fetch } = telemetryHarness();
    render(<App client={client} />);
    await screen.findByText("dashboard");

    const paths = (): string[] =>
      fetch.mock.calls.map((call: unknown[]) =>
        new URL(String(call[0]), window.location.origin).toString(),
      );
    await waitFor(() =>
      expect(paths().some((url: string) => isTimeline(new URL(url)))).toBe(
        true,
      ),
    );
    const details = paths().filter((url: string) => isRunDetail(new URL(url)));
    // One detail, for the run being looked at — not one for every listed run —
    // and it asks the server to leave the transcripts out of it.
    expect(details).toHaveLength(1);
    expect(details[0]).toContain(LIVE_RUN);
    expect(details[0]).toContain("include_conversations=false");
    expect(paths().some((url: string) => isConversation(new URL(url)))).toBe(
      false,
    );

    fireEvent.click(screen.getByRole("button", { name: "dashboard: running" }));
    await userEvent.click(
      await screen.findByRole("button", { name: /engineer-dashboard/ }),
    );
    await waitFor(() =>
      expect(
        paths().filter((url: string) => isConversation(new URL(url))),
      ).toHaveLength(1),
    );
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

  test("shows the run-level sessions in the overall view", async () => {
    const { client } = telemetryHarness();
    render(<App client={client} />);
    await screen.findByText("dashboard");
    // The view switcher is a real tab set now, and a tab set selects on the pointer
    // press rather than on the synthetic click that follows it — so this drives the
    // whole pointer sequence a person produces instead of dispatching one event.
    await userEvent.click(screen.getByRole("tab", { name: "Overall" }));
    await waitFor(() =>
      expect(window.location.search).toContain("view=overall"),
    );
    expect(await screen.findByText("Run-level sessions")).toBeInTheDocument();
    expect(
      await screen.findByText("Coordinating the execution frontier"),
    ).toBeInTheDocument();
  });

  test("opens a run-level session other than the one shown on arrival", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&view=overall`);
    const { client, fetch } = telemetryHarness();
    render(<App client={client} />);
    // The run recorded two sessions at no node: the planner's, and the round's own
    // check-in. Only the first is open on arrival.
    expect(
      await screen.findByText("Coordinating the execution frontier"),
    ).toBeInTheDocument();
    const transcripts = (): string[] =>
      fetch.mock.calls
        .map(
          (call: unknown[]) => new URL(String(call[0]), window.location.origin),
        )
        .filter(isConversation)
        .map((url: URL) =>
          decodeURIComponent(url.pathname.split("/").at(-1) ?? ""),
        );
    expect(transcripts()).toEqual(["orchestrator-session"]);
    expect(screen.queryByText("Round 1 progress reported")).toBeNull();

    // Opening the check-in discloses it and reads its own transcript, then — the
    // whole point of listing them separately rather than stacking every session.
    await userEvent.click(
      screen.getByRole("button", { name: /check-in-round-1/ }),
    );
    expect(
      await screen.findByText("Round 1 progress reported"),
    ).toBeInTheDocument();
    expect(transcripts()).toEqual([
      "orchestrator-session",
      ROUND_CHECK_IN_SESSION,
    ]);
  });

  test("says so when a run recorded no run-level conversation", async () => {
    window.history.replaceState(null, "", `/?run=${HISTORY_RUN}&view=overall`);
    const { client } = telemetryHarness();
    render(<App client={client} />);
    expect(
      await screen.findByText("No run-level conversation is available."),
    ).toBeInTheDocument();
  });

  test("refreshes on demand and restores a bookmarked node selection", async () => {
    window.history.replaceState(null, "", `/?run=${HISTORY_RUN}&node=archive`);
    const { client, fetch } = telemetryHarness();
    render(<App client={client} />);
    expect(
      await screen.findByRole("region", { name: "Timeline for archive" }),
    ).toBeInTheDocument();

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

  test("keeps loading while a listed run's detail is still on its way", async () => {
    let release: (response: Response) => void = () => {};
    const { client } = telemetryHarness((url) => {
      // Only the selected run's detail is held back; everything else resolves.
      if (isRunDetail(url) && url.pathname.endsWith(LIVE_RUN))
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      return defaultResponder(url);
    });
    render(<App client={client} />);
    // Runs exist, so "no runs found" would be a lie; the view waits instead.
    expect(
      await screen.findByText("Loading execution history…"),
    ).toBeInTheDocument();
    expect(screen.queryByText("No DAG runs found")).toBeNull();

    release(Response.json(runDetail(LIVE_RUN)));
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
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
      { ...runList, telemetry_schema_version: 9 },
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
        // The first read is the one that put the run on screen; a re-read raised
        // by a live update is the failure this proves.
        if (details > 1)
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
      if (!isRunDetail(url)) return defaultResponder(url);
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
      return defaultResponder(url);
    });
    render(<App client={client} />);
    await screen.findByText("dashboard");

    removed = true;
    sources[0]?.emit("run.removed", { run_id: LIVE_RUN }, "3");
    expect(await screen.findByText("archive")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: RegExp(LIVE_RUN) })).toBeNull();
  });
});

test("serves the timeline of whichever run is selected", async () => {
  window.history.replaceState(null, "", `/?run=${HISTORY_RUN}&node=archive`);
  const { client } = telemetryHarness();
  render(<App client={client} />);
  // The archive run's own recorded work, not the live run's.
  expect(
    await screen.findByRole("button", { name: /engineer-archive/ }),
  ).toBeInTheDocument();
  expect(runTimeline(HISTORY_RUN).spans).toHaveLength(3);
  cleanup();
});
