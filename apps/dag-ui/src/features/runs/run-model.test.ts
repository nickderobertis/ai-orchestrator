import { parseRunDetail, parseRunList } from "@ai-orchestrator/dag-model";
import { describe, expect, test } from "vitest";
import { HISTORY_RUN, LIVE_RUN, runDetail, runList } from "../../test/fixtures";
import {
  groupRuns,
  isUnhealthy,
  latestRound,
  nodeReason,
  nodeViews,
} from "./run-model";

const live = parseRunDetail(runDetail(LIVE_RUN));
const summaries = parseRunList(runList).runs;

describe("node views", () => {
  test("classifies kind, reads the served status, and carries each node's record", () => {
    const views = nodeViews(live);
    expect(views.map(({ id }) => id)).toEqual([
      "foundation",
      "dashboard",
      "publish",
      "approval",
      "queued",
      "abandoned",
      "followup",
      "obsolete",
    ]);
    const byId = new Map(views.map((view) => [view.id, view]));
    expect(byId.get("foundation")?.kind).toBe("lifecycle");
    expect(byId.get("approval")?.kind).toBe("human");
    expect(byId.get("dashboard")?.kind).toBe("agent");
    expect(byId.get("dashboard")?.telemetry?.turns).toBe(2);
    expect(byId.get("publish")?.result?.detail).toBe("Deploy failed");

    // Every status is the one the server served, including the three the journal
    // never recorded and a client used to have to invent as "pending".
    expect(
      Object.fromEntries(views.map((view) => [view.id, view.status])),
    ).toEqual({
      foundation: "done",
      dashboard: "running",
      publish: "failed",
      approval: "waiting",
      queued: "blocked",
      abandoned: "skipped",
      followup: "pending",
      obsolete: "cancelled",
    });
  });

  test("carries the served blockers and failure of the nodes that have them", () => {
    const byId = new Map(nodeViews(live).map((view) => [view.id, view]));
    expect(byId.get("queued")?.blockers).toEqual(["approval"]);
    expect(byId.get("abandoned")?.blockers).toEqual(["publish"]);
    expect(byId.get("dashboard")?.blockers).toEqual([]);
    expect(byId.get("publish")?.failure).toEqual({
      class: "agent",
      detail: "Deploy failed",
    });
    expect(byId.get("dashboard")?.failure).toBeUndefined();
  });

  test("states one reason per node that is not making progress, and none otherwise", () => {
    const byId = new Map(nodeViews(live).map((view) => [view.id, view]));
    const reason = (id: string) => {
      const view = byId.get(id);
      if (view === undefined) throw new Error(`fixture has no ${id}`);
      return nodeReason(view);
    };
    expect(reason("queued")).toBe("blocked by approval");
    expect(reason("abandoned")).toBe("blocked by publish");
    expect(reason("publish")).toBe("Deploy failed");
    // Healthy work has nothing to report, and must not be given a line that reads
    // as though it does.
    expect(reason("dashboard")).toBeUndefined();
    expect(reason("foundation")).toBeUndefined();
    expect(reason("followup")).toBeUndefined();

    expect(
      ["queued", "abandoned", "publish", "obsolete"].map((id) =>
        isUnhealthy(byId.get(id)?.status ?? "unknown"),
      ),
    ).toEqual([true, true, true, true]);
    expect(
      ["foundation", "dashboard", "followup", "approval"].map((id) =>
        isUnhealthy(byId.get(id)?.status ?? "unknown"),
      ),
    ).toEqual([false, false, false, false]);
  });

  test("says a node failed with no recorded reason rather than showing nothing", () => {
    const payload = runDetail(LIVE_RUN);
    const round = payload.rounds[0];
    if (round === undefined) throw new Error("fixture has no round");
    const bare = parseRunDetail({
      ...payload,
      run: {
        ...payload.run,
        nodes: payload.run.nodes.filter(({ node }) => node !== "publish"),
      },
      rounds: [
        {
          ...round,
          node_results: {
            ...round.node_results,
            publish: { status: "failed" },
          },
        },
      ],
    });
    const publish = nodeViews(bare).find(({ id }) => id === "publish");
    if (publish === undefined) throw new Error("fixture has no publish node");
    expect(nodeReason(publish)).toBe("failed, with no reason recorded");
  });

  test("renders nothing for a detail with no projected round", () => {
    const empty = parseRunDetail({ ...runDetail(LIVE_RUN), rounds: [] });
    expect(latestRound(empty)).toBeUndefined();
    expect(nodeViews(empty)).toEqual([]);
  });
});

describe("run grouping", () => {
  test("groups runs under the session that launched them", () => {
    // The join is on the list rows themselves, so the navigation is complete
    // before a single run's detail — let alone its transcripts — has been read.
    const groups = groupRuns(summaries);
    expect(groups.map(({ launcher }) => launcher)).toEqual(["Codex", "Claude"]);
    expect(groups[0]?.label).toMatch(/^Codex session · 5e551040…$/);
    expect(groups.map(({ runs }) => runs.map(({ run_id }) => run_id))).toEqual([
      [LIVE_RUN],
      [HISTORY_RUN],
    ]);
  });

  test("gathers every run of one session, whatever launch each one was", () => {
    // The whole point of grouping by session: `just orchestrate` mints a fresh
    // launch id per run, so two runs of one planner session share nothing but the
    // session key — and keying on the launch would file each one alone.
    const [first, second] = summaries;
    if (first === undefined || second === undefined) throw new Error("fixture");
    const launch = first.launch;
    if (launch === undefined) throw new Error("fixture");
    const groups = groupRuns([
      first,
      {
        ...first,
        run_id: "sibling",
        launch: { ...launch, launch_id: "f".repeat(32) },
      },
      { ...second, run_id: "orphan", launch: undefined },
    ]);
    expect(groups.map(({ launcher }) => launcher)).toEqual([
      "Codex",
      "Unattributed",
    ]);
    expect(groups[0]?.runs.map(({ run_id }) => run_id)).toEqual([
      LIVE_RUN,
      "sibling",
    ]);
    // A run with no launch record at all is honestly unattributed, and gets a
    // group of its own rather than being pooled with unrelated runs under one
    // bucket that would claim they share a planner.
    expect(groups[1]?.runs.map(({ run_id }) => run_id)).toEqual(["orphan"]);
    expect(groups[1]?.label).toBe("Unattributed");
  });

  test("names a launch whose session nothing can resolve by the launch itself", () => {
    // Every run launched before the launcher was detected, once its short-lived
    // provenance record has gone: with nothing left to name the session, the server
    // serves the launch id and an unknown launcher.
    const [first] = summaries;
    if (first?.launch === undefined) throw new Error("fixture");
    const [group] = groupRuns([
      {
        ...first,
        launch: { launch_id: first.launch.launch_id, launcher: "unknown" },
      },
    ]);
    expect(group?.label).toBe("Unattributed launch · c0dec0de…");
    expect(group?.launcher).toBe("Unattributed");
  });
});
