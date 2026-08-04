import {
  type NodeDetail,
  parseRunDetail,
  parseRunTimeline,
} from "@ai-orchestrator/dag-model";
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
  PR_URL,
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

/**
 * The transcript entry a reader would open, named by the words it puts on screen.
 *
 * The transcript is where an operator lands and where every recorded item is
 * listed in full; the timeline above it plots the same items and shows whichever
 * of them fit the compact line, so a journey that is about opening one item asks
 * the transcript for it rather than depending on that compaction.
 */
const openTranscript = async (name: RegExp) => {
  // Re-queried through the region on every attempt, never held across one: moving
  // between nodes remounts the whole view, and an element captured before that move
  // is a detached node whose click reaches nothing and reports nothing. `waitFor`
  // resolves to what its last attempt returned, which is the live element.
  const entry = await waitFor(() =>
    within(screen.getByRole("region", { name: "Node transcript" })).getByRole(
      "button",
      { name },
    ),
  );
  await userEvent.click(entry);
  await screen.findByRole("region", { name: "Timeline item detail" });
};

const detail = () =>
  screen.getByRole("region", { name: "Timeline item detail" });

/** Nodes of the live fixture whose status every surface has to agree on. */
const SERVED_STATUSES: readonly { node: string; status: string }[] = [
  { node: "queued", status: "blocked" },
  { node: "abandoned", status: "skipped" },
  { node: "followup", status: "pending" },
  { node: "publish", status: "failed" },
];

describe("DAG application", () => {
  // The graph is one reading of a run and no longer the one an empty address lands
  // on, so the journeys that are about it say so — exactly as an operator's own
  // bookmark of the graph does. The landing view has a journey of its own below.
  beforeEach(() => {
    window.history.replaceState(null, "", "/?view=graph");
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

    // The upstream visualization identifies each activity and keeps timing details
    // in its hover/focus tooltip rather than printing metadata beside every row.
    const worker = railRow(/engineer-dashboard/);
    fireEvent.mouseEnter(worker);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Duration: 48.0 s");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Status: completed");
    // Every category is a lane of the legend, and the timeline opens on one compact
    // line; expanding is what gives each of them a row of its own.
    const legend = screen.getByRole("list", { name: "Timeline legend" });
    expect(
      within(legend)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
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
    ]);
    await userEvent.click(railRow(/^Expand timeline$/));
    expect(railRow(/^Judge/)).toBeInTheDocument();
    expect(railRow(/^Lint/)).toBeInTheDocument();
    expect(railRow(/^Check-in/)).toBeInTheDocument();
    expect(railRow(/^PR author/)).toBeInTheDocument();
    expect(railRow(/^Lock waits/)).toBeInTheDocument();
    await userEvent.click(railRow(/^Collapse timeline$/));

    await userEvent.click(railRow(/engineer-dashboard/));
    await waitFor(() =>
      expect(window.location.search).toContain("event=dispatch-worker-session"),
    );
    expect(
      await within(detail()).findByText("Implementing the dashboard now"),
    ).toBeInTheDocument();
    // The conversation says which dispatch it belongs to, which role it played in
    // it, and which persona it ran — a judge transcript is unreadable without them.
    expect(
      within(detail()).getByText("Dispatch 1 · Worker · engineer"),
    ).toBeInTheDocument();
    expect(
      within(detail()).getAllByRole("button", { name: "Bash tool details" }),
    ).toHaveLength(1);
    await userEvent.click(
      within(detail()).getByRole("button", { name: "Bash tool details" }),
    );
    expect(
      within(detail()).getByLabelText("Bash tool output"),
    ).toHaveTextContent('"matches": 1');
    expect(
      within(detail())
        .getByLabelText("Bash tool output")
        .querySelector(".hljs-number"),
    ).toHaveTextContent("1");

    // Escape closes on-demand detail first, preserving reading context; a second
    // Escape returns to the graph.
    await userEvent.keyboard("{Escape}");
    await userEvent.keyboard("{Escape}");
    expect(await screen.findByText("queued")).toBeInTheDocument();
    expect(window.location.search).not.toContain("node=");
  });

  test("states one status per node on every surface that shows one", async () => {
    const { client } = telemetryHarness();
    render(<App client={client} />);
    expect(await screen.findByText("dashboard")).toBeInTheDocument();

    const nodeList = screen.getByRole("list", { name: "DAG nodes" });
    for (const { node, status } of SERVED_STATUSES) {
      // The card the pointer reads.
      expect(screen.getByText(node).closest(".dag-node")).toHaveClass(
        `state-${status}`,
      );
      // The list the keyboard reads.
      expect(
        within(nodeList).getByRole("button", {
          name: new RegExp(`^${node}: ${status}\\b`),
        }),
      ).toBeInTheDocument();
    }

    // The run row above them, counted on the server over that same derivation.
    expect(
      screen.getByRole("button", { name: new RegExp(LIVE_RUN) }),
    ).toHaveTextContent("1 pending · 1 running · 1 waiting · 1 blocked");

    // And the node view each card opens.
    for (const { node, status } of SERVED_STATUSES) {
      // Re-queried per node: leaving the node view unmounts and remounts the list.
      fireEvent.click(
        within(screen.getByRole("list", { name: "DAG nodes" })).getByRole(
          "button",
          { name: new RegExp(`^${node}: ${status}\\b`) },
        ),
      );
      const view = await screen.findByRole("region", {
        name: `Timeline for ${node}`,
      });
      expect(view.querySelector(".node-view-facts")).toHaveTextContent(status);
      fireEvent.click(screen.getByRole("button", { name: /Graph/ }));
    }
  });

  test("still counts a run whose statuses the server could not fold", async () => {
    // When a run's authoritative journal will not fold, the server counts its nodes
    // from the tolerant telemetry index instead, whose statuses are an open string.
    // The row has to show those words too — a run going wrong is exactly the one an
    // operator is looking at — after the vocabulary it does know, not instead of it.
    const degraded = {
      ...runList,
      runs: runList.runs.map((run) =>
        run.run_id === LIVE_RUN
          ? { ...run, node_counts: { improvised: 2, running: 1, absent: 0 } }
          : run,
      ),
    };
    const { client } = telemetryHarness((url) =>
      isRunList(url) ? Response.json(degraded) : defaultResponder(url),
    );
    render(<App client={client} />);

    const row = await screen.findByRole("button", {
      name: new RegExp(LIVE_RUN),
    });
    expect(row).toHaveTextContent("1 running · 2 improvised");
    // A status counted zero times is not a status this run has.
    expect(row).not.toHaveTextContent("absent");
  });

  test("leads a failed node's view with why it failed", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=publish`);
    const { client } = telemetryHarness();
    render(<App client={client} />);

    // The reason is the first thing in the view and announces itself, rather than
    // sitting behind an accordion entry called "Outcome" beside four other facts.
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("This node failed: agent");
    expect(banner).toHaveTextContent("Deploy failed");
    expect(banner).toHaveTextContent("publication exited non-zero");
    expect(banner).toHaveTextContent("2");
    expect(
      banner.compareDocumentPosition(screen.getByRole("tab", { name: "Task" })),
    ).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  test("leads a blocked node's view with what is holding it", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=queued`);
    const { client } = telemetryHarness();
    render(<App client={client} />);

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("This node is blocked");
    expect(banner).toHaveTextContent("Blocked by");
    expect(banner).toHaveTextContent("approval");
  });

  test("says nothing extra about a node that is making progress", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=followup`);
    const { client } = telemetryHarness();
    render(<App client={client} />);
    // Work that has not started has no problem to report, and a banner over it
    // would read as one.
    expect(
      await screen.findByRole("region", { name: "Timeline for followup" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
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
    // The address opens the matching long-form item in the detail panel.
    expect(await screen.findByLabelText("Item detail panel")).toBeVisible();
    expect(
      await within(detail()).findByText("Implementing the dashboard now"),
    ).toBeInTheDocument();
  });

  test("shows a verification, a publication, and an aggregate as themselves", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=foundation`);
    const { client } = telemetryHarness();
    render(<App client={client} />);

    await openTranscript(/just gate/);
    expect(
      await within(detail()).findByText("Verification record"),
    ).toBeVisible();
    expect(within(detail()).getByText("Full log")).toBeInTheDocument();
    expect(within(detail()).queryByText(/round-01\/foundation/)).toBeNull();

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
    await openTranscript(/^Open Lock waits/);
    expect(
      await within(detail()).findByText("1240 records"),
    ).toBeInTheDocument();
    expect(within(detail()).getByText("Reference")).toBeInTheDocument();
    // This is the one rendering that shows a record the run closed, so both of its
    // stamps are read as ages — and the moment itself stays on the element rather
    // than reaching the reader as the ISO string the journal wrote.
    const ages = within(detail()).getAllByText(/ ago$/);
    expect(ages).toHaveLength(2);
    expect(ages[0]).toHaveAttribute("datetime", "2026-07-26T11:00:15.000Z");
    expect(ages[1]).toHaveAttribute("datetime", "2026-07-26T11:02:35.000Z");
  });

  test("keeps a node whose recorded work is hundreds of sessions scannable", async () => {
    const dense = parseRunTimeline(busyTimeline(200));
    dense.spans.push({
      id: "step-dense",
      kind: "step",
      label: "Supervised conversations",
      parent_id: "node-1-dashboard",
      node_id: "dashboard",
      step_id: "supervision",
      round: 1,
      started_at: "2026-07-26T11:01:01.000Z",
      ended_at: "2026-07-26T11:10:00.000Z",
      status: "done",
      events: [],
    });
    for (const span of dense.spans) {
      if (span.kind === "dispatch") span.parent_id = "step-dense";
    }
    const { client } = telemetryHarness((url) =>
      isTimeline(url) ? Response.json(dense) : defaultResponder(url),
    );
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=dashboard`);
    render(<App client={client} />);

    const rail = await screen.findByRole("region", { name: "Node timeline" });
    // The lifecycle step brackets the sessions rather than being one of them, so it
    // is the transcript that names it, not a lane plotted over its own contents.
    const transcript = screen.getByRole("region", { name: "Node transcript" });
    expect(
      within(transcript).getByRole("button", {
        name: /Lifecycle: Supervised conversations/,
      }),
    ).toBeInTheDocument();
    expect(
      within(rail).getByRole("button", { name: "Expand timeline" }),
    ).toBeInTheDocument();
    await userEvent.click(
      within(rail).getByRole("button", { name: "Expand timeline" }),
    );
    expect(
      within(rail).getByRole("button", { name: "Collapse timeline" }),
    ).toBeInTheDocument();
  });

  test("labels a worker dispatched after a retry request as the retry", async () => {
    const retried = parseRunTimeline(runTimeline(LIVE_RUN));
    const node = retried.spans.find(({ id }) => id === "node-1-dashboard");
    const worker = retried.spans.find(
      ({ id }) => id === "dispatch-worker-session",
    );
    if (node === undefined || worker === undefined)
      throw new Error("fixture lost dashboard work");
    node.events.push({
      id: "retry-requested-1",
      kind: "retry-requested",
      at: "2026-07-26T11:02:35.000Z",
      node_id: "dashboard",
      round: 1,
    });
    retried.spans.push({
      ...worker,
      id: "dispatch-worker-retry",
      label: "engineer-dashboard",
      started_at: "2026-07-26T11:02:40.000Z",
      ended_at: "2026-07-26T11:03:00.000Z",
    });
    const { client } = telemetryHarness((url) =>
      isTimeline(url) ? Response.json(retried) : defaultResponder(url),
    );
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=dashboard`);
    render(<App client={client} />);

    const transcript = await screen.findByRole("region", {
      name: "Node transcript",
    });
    const retry = within(transcript).getByRole("article", {
      name: /Worker \(engineer-dashboard\) · retry 1/,
    });
    // The retry is a second dispatch of the node, so it is grouped as its own.
    expect(retry).toHaveAttribute("data-dispatch-group", "Dispatch 4");
    expect(
      within(transcript).getByRole("article", {
        name: "Worker (engineer-dashboard)",
      }),
    ).toHaveAttribute("data-dispatch-group", "Dispatch 1");
  });

  test("names what a failed node's attempts did not record", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=publish`);
    const { client } = telemetryHarness();
    render(<App client={client} />);

    // A gate that never reached an attestation, and a publication with no PR and
    // no observed checks: each absence is stated rather than left as a blank block
    // that reads like "all clear".
    await openTranscript(/branch push/);
    expect(
      await within(detail()).findByText("No readable log was recorded."),
    ).toBeInTheDocument();

    await userEvent.click(railRow(/^Publication/));
    expect(
      await within(detail()).findByText("No publication was recorded."),
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
    expect(
      await screen.findByRole("region", { name: "Node transcript" }),
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

    await userEvent.click(await screen.findByRole("tab", { name: "Task" }));
    expect(
      await screen.findByText("Build the live dashboard"),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("tab", { name: "Completion criteria" }),
    );
    expect(
      await screen.findByText("Users can inspect transcripts"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Dependencies" }));
    expect(await screen.findByText("foundation")).toBeInTheDocument();
  });

  test("reads a steps-shaped node's task from the steps that carry it", async () => {
    // A lifecycle node that delegates to `steps` has no `task` prose of its own, so
    // reading only the node's own field left the Task tab blank for exactly the nodes
    // whose description is longest.
    window.history.replaceState(
      null,
      "",
      `/?run=${HISTORY_RUN}&node=corpus&tab=task`,
    );
    const { client } = telemetryHarness();
    render(<App client={client} />);

    expect(await screen.findByRole("tab", { name: "Task" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const prose = await screen.findByText(/Sweep the recorded corpus/);
    expect(prose).toHaveTextContent("sweep");
    expect(prose).toHaveTextContent("Confirm the sweep");
  });

  test("deep-links node tabs and moves across them with the keyboard", async () => {
    window.history.replaceState(
      null,
      "",
      `/?run=${LIVE_RUN}&node=dashboard&tab=criteria`,
    );
    const { client } = telemetryHarness();
    render(<App client={client} />);
    const criteria = await screen.findByRole("tab", {
      name: "Completion criteria",
    });
    expect(criteria).toHaveAttribute("aria-selected", "true");
    criteria.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Dependencies" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(window.location.search).toContain("tab=dependencies");
  });

  test("hands the node's recorded pull request over as a link", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=foundation`);
    const { client } = telemetryHarness();
    const view = render(<App client={client} />);
    await userEvent.click(await screen.findByRole("tab", { name: "PR" }));
    const [link] = await screen.findAllByRole("link", { name: /Pull request/ });
    expect(link).toHaveAttribute("href", PR_URL);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
    view.unmount();

    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=dashboard`);
    render(<App client={client} />);
    await userEvent.click(await screen.findByRole("tab", { name: "PR" }));
    expect(
      screen.getByText("Publication").nextElementSibling,
    ).toHaveTextContent("Not recorded");
    expect(screen.queryByRole("link", { name: RegExp(PR_URL) })).toBeNull();
  });

  test.each<{
    name: string;
    publication: NonNullable<NodeDetail["publication"]>;
    expectedLinks: readonly string[];
    absentLinks: readonly string[];
  }>([
    {
      name: "local direct-merge",
      publication: {
        branch: "feature/local",
        base_branch: "main",
        merged: true,
        commit: "abc12345",
        commit_url: "https://github.com/example/repo/commit/abc12345",
      },
      expectedLinks: ["Commit abc12345"],
      absentLinks: ["Pull request"],
    },
    {
      name: "remote PR unmerged",
      publication: {
        pr_url: PR_URL,
        branch: "feature/remote",
        branch_url: "https://github.com/example/repo/tree/feature/remote",
        base_branch: "main",
        merged: false,
      },
      expectedLinks: ["Pull request", "feature/remote"],
      absentLinks: ["Commit"],
    },
    {
      name: "remote PR merged",
      publication: {
        pr_url: PR_URL,
        branch: "feature/remote",
        branch_url: "https://github.com/example/repo/tree/feature/remote",
        base_branch: "main",
        merged: true,
        commit: "def67890",
        commit_url: "https://github.com/example/repo/commit/def67890",
      },
      expectedLinks: ["Pull request", "Commit def67890"],
      absentLinks: ["feature/remote"],
    },
  ])(
    "renders the $name publication fixture",
    async ({ publication, expectedLinks, absentLinks }) => {
      window.history.replaceState(
        null,
        "",
        `/?run=${LIVE_RUN}&node=foundation`,
      );
      const served = parseRunDetail(runDetail());
      const foundation = served.node_details.foundation;
      if (foundation === undefined)
        throw new Error("fixture has no foundation detail");
      foundation.publication = publication;
      const { client } = telemetryHarness((url) =>
        isRunDetail(url) ? Response.json(served) : defaultResponder(url),
      );
      render(<App client={client} />);
      await userEvent.click(await screen.findByRole("tab", { name: "PR" }));
      for (const name of expectedLinks) {
        expect(
          screen.getAllByRole("link", { name: new RegExp(name) }).length,
        ).toBeGreaterThan(0);
      }
      for (const name of absentLinks) {
        expect(
          screen.queryByRole("link", { name: new RegExp(name) }),
        ).toBeNull();
      }
    },
  );

  test("reads a recorded moment as words rather than as the stamp it was written as", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&node=dashboard`);
    const { client } = telemetryHarness();
    render(<App client={client} />);
    // The lock-wait rollup has no dedicated rendering, so it is the item whose every
    // recorded field reaches the reader — including the two stamps the detail used to
    // print straight out of the journal.
    await openTranscript(/^Open Lock waits/);
    expect(await within(detail()).findByText("Recorded at")).toBeVisible();
    expect(within(detail()).getByText("Duration")).toBeVisible();
    // Not one ISO stamp and not one raw second count anywhere in the pane.
    expect(detail().textContent).not.toMatch(/\d{4}-\d\d-\d\dT/);
    expect(detail().textContent).not.toMatch(/\d+\.\d+s/);
  });

  test("reads only the selected run, and a transcript only when one is opened", async () => {
    const { client, fetch } = telemetryHarness();
    render(<App client={client} />);
    await screen.findByText("dashboard");

    const paths = (): string[] =>
      fetch.mock.calls.map((call: unknown[]) =>
        new URL(String(call[0]), window.location.origin).toString(),
      );
    expect(paths().some((url: string) => isTimeline(new URL(url)))).toBe(false);
    const details = paths().filter((url: string) => isRunDetail(new URL(url)));
    // One detail, for the run being looked at — not one for every listed run —
    // and it asks the server to leave the transcripts out of it.
    expect(details).toHaveLength(1);
    expect(details[0]).toContain(LIVE_RUN);
    expect(details[0]).toContain("include_conversations=false");
    expect(paths().some((url: string) => isConversation(new URL(url)))).toBe(
      false,
    );

    await userEvent.click(screen.getByRole("tab", { name: "Overall" }));
    await waitFor(() =>
      expect(
        paths().some(
          (value: string) =>
            isTimeline(new URL(value)) &&
            new URL(value).searchParams.get("scope") === "run",
        ),
      ).toBe(true),
    );
    const runLevelConversationReads = paths().filter((url: string) =>
      isConversation(new URL(url)),
    ).length;
    expect(
      paths().filter(
        (value: string) =>
          isTimeline(new URL(value)) &&
          new URL(value).searchParams.has("node_id"),
      ),
    ).toHaveLength(0);
    await userEvent.click(screen.getByRole("tab", { name: "Graph" }));

    fireEvent.click(screen.getByRole("button", { name: "dashboard: running" }));
    await waitFor(() =>
      expect(
        paths().some(
          (value: string) =>
            isTimeline(new URL(value)) &&
            new URL(value).searchParams.get("node_id") === "dashboard",
        ),
      ).toBe(true),
    );
    await openTranscript(/engineer-dashboard/);
    await waitFor(() =>
      expect(
        paths().filter((url: string) => isConversation(new URL(url))),
      ).toHaveLength(runLevelConversationReads + 1),
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

  test("shows live activity and refetches an open transcript on run-scoped invalidation", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&view=overall`);
    const { client, sources, fetch } = telemetryHarness();
    render(<App client={client} />);
    await screen.findByText("Coordinating the execution frontier");
    await waitFor(() => expect(sources).toHaveLength(2));
    const before = fetch.mock.calls.filter((call: unknown[]) =>
      isConversation(new URL(String(call[0]), window.location.origin)),
    ).length;

    sources[1]?.emit(
      "activity.changed",
      {
        run_id: LIVE_RUN,
        activity: [
          {
            round: "1",
            node: "dashboard",
            at: Date.now() / 1000,
            kind: "tool",
            name: "Read",
            detail: "server.py",
            events: 12,
          },
        ],
      },
      "9",
    );

    expect(await screen.findByText("dashboard: Read server.py")).toBeVisible();
    await waitFor(() =>
      expect(
        fetch.mock.calls.filter((call: unknown[]) =>
          isConversation(new URL(String(call[0]), window.location.origin)),
        ).length,
      ).toBeGreaterThan(before),
    );
  });

  test("loads the next run-list page when the sidebar reaches its end", async () => {
    const { client, fetch } = telemetryHarness((url) => {
      if (isRunList(url)) {
        return Response.json(
          url.searchParams.has("cursor")
            ? { ...runList, runs: [runList.runs[1]] }
            : { ...runList, runs: [runList.runs[0]], next_cursor: "page-2" },
        );
      }
      return defaultResponder(url);
    });
    render(<App client={client} />);
    expect(
      await screen.findByRole("button", { name: RegExp(LIVE_RUN) }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: RegExp(HISTORY_RUN) }),
    ).toBeNull();

    await userEvent.click(
      screen.getByRole("button", { name: "Load more runs" }),
    );

    expect(
      await screen.findByRole("button", { name: RegExp(HISTORY_RUN) }),
    ).toBeInTheDocument();
    expect(
      fetch.mock.calls.some((call: unknown[]) =>
        String(call[0]).includes("cursor=page-2"),
      ),
    ).toBe(true);
  });

  test("reports a failed next page and can load it on a later scroll", async () => {
    let continuationAttempts = 0;
    const { client } = telemetryHarness((url) => {
      if (!isRunList(url)) return defaultResponder(url);
      if (!url.searchParams.has("cursor"))
        return Response.json({
          ...runList,
          runs: [runList.runs[0]],
          next_cursor: "page-2",
        });
      continuationAttempts += 1;
      if (continuationAttempts === 1) throw new Error("next page unavailable");
      return Response.json({ ...runList, runs: [runList.runs[1]] });
    });
    render(<App client={client} />);
    await screen.findByRole("button", { name: RegExp(LIVE_RUN) });

    const viewport = document.querySelector<HTMLElement>(
      "[data-radix-scroll-area-viewport]",
    );
    if (viewport === null) throw new Error("scroll viewport was not rendered");
    Object.defineProperties(viewport, {
      clientHeight: { configurable: true, value: 100 },
      scrollHeight: { configurable: true, value: 200 },
      scrollTop: { configurable: true, value: 100 },
    });
    fireEvent.scroll(viewport);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Telemetry request failed",
    );

    fireEvent.scroll(viewport);
    expect(
      await screen.findByRole("button", { name: RegExp(HISTORY_RUN) }),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  test("opens on the run as a whole when the address names no view", async () => {
    window.history.replaceState(null, "", "/");
    const { client } = telemetryHarness();
    render(<App client={client} />);
    // The overall reading of the run is what an operator arrives for; the graph is
    // one tab away, and every deep link into it still opens where it points.
    expect(await screen.findByText("Run timeline")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Overall" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByRole("list", { name: "DAG nodes" })).toBeNull();
    // The wall time it reports is a duration, not a second count to do sums on.
    expect(screen.getByText("Wall time").closest(".metric")).toHaveTextContent(
      "5s",
    );

    await userEvent.click(screen.getByRole("tab", { name: "Graph" }));
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
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
    expect(await screen.findByText("Run timeline")).toBeInTheDocument();
    expect(
      await screen.findByText("Coordinating the execution frontier"),
    ).toBeInTheDocument();
    // Each run-level row says which kind of dispatch it was, from the role served on
    // its own span — the orchestrator's own session and the round's check-in read as
    // themselves rather than as two identically labelled sessions.
    expect(screen.getByText("Run-level · orchestrator")).toBeInTheDocument();
    expect(screen.getByText("Run-level · check-in")).toBeInTheDocument();
    // And the run's launch is named with the same phrase the navigation heads its
    // group with, rather than with the raw launcher enum.
    expect(screen.getAllByText(/^Codex session · /)).not.toHaveLength(0);
  });

  test("states when a node summary has no recorded activity", async () => {
    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&view=overall`);
    const scoped = runTimeline(LIVE_RUN);
    const { client } = telemetryHarness((url) =>
      isTimeline(url)
        ? Response.json({
            ...scoped,
            spans: scoped.spans.filter((span) => !("node_id" in span)),
          })
        : defaultResponder(url),
    );
    render(<App client={client} />);
    const summary = await screen.findByText("dashboard", {
      selector: ".overall-node-summary .session-name",
    });
    const trigger = summary.closest("button");
    if (trigger === null) throw new Error("node summary has no trigger");
    await userEvent.click(trigger);
    expect(
      await screen.findByText("No activity summary recorded."),
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
    expect(await screen.findByText("archive")).toBeInTheDocument();
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

    window.history.replaceState(null, "", `/?run=${LIVE_RUN}&view=graph`);
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
      { ...runList, telemetry_schema_version: 10 },
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
    within(
      await screen.findByRole("region", { name: "Node timeline" }),
    ).getByRole("button", { name: /engineer-archive/ }),
  ).toBeInTheDocument();
  expect(runTimeline(HISTORY_RUN).spans).toHaveLength(3);
  cleanup();
});
