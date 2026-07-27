import { parseRunDetail, parseRunList } from "@ai-orchestrator/dag-model";
import { describe, expect, test } from "vitest";
import { HISTORY_RUN, LIVE_RUN, runDetail, runList } from "../../test/fixtures";
import { groupRuns, latestRound, nodeViews } from "./run-model";

const live = parseRunDetail(runDetail(LIVE_RUN));
const historical = parseRunDetail(runDetail(HISTORY_RUN));
const summaries = parseRunList(runList).runs;

describe("node views", () => {
  test("classifies kind, projects state, and attaches each node's transcripts", () => {
    const views = nodeViews(live);
    expect(views.map(({ id }) => id)).toEqual([
      "foundation",
      "dashboard",
      "publish",
      "approval",
      "queued",
      "obsolete",
    ]);
    const byId = new Map(views.map((view) => [view.id, view]));
    expect(byId.get("foundation")?.kind).toBe("lifecycle");
    expect(byId.get("approval")?.kind).toBe("human");
    expect(byId.get("dashboard")?.kind).toBe("agent");
    expect(byId.get("dashboard")?.state).toBe("running");
    expect(byId.get("approval")?.state).toBe("waiting");
    // A node the round never started carries no projected state at all.
    expect(byId.get("queued")?.state).toBe("pending");
    expect(byId.get("dashboard")?.telemetry?.turns).toBe(2);
    expect(byId.get("publish")?.result?.detail).toBe("Deploy failed");
  });

  test("groups the run's flat transcript list by the node each session names", () => {
    const dashboard = nodeViews(live).find(({ id }) => id === "dashboard");
    expect(
      dashboard?.conversations.map(({ attribution }) => attribution.agentRole),
    ).toEqual(["worker", "judge", "check-in", "pr-author", "worker"]);
    // The orchestrator session names no node, so it belongs to no node view.
    expect(
      nodeViews(live).flatMap(({ conversations }) => conversations),
    ).not.toContainEqual(
      expect.objectContaining({
        attribution: expect.objectContaining({ agentRole: "orchestrator" }),
      }),
    );
  });

  test("renders nothing for a detail with no projected round", () => {
    const empty = parseRunDetail({ ...runDetail(LIVE_RUN), rounds: [] });
    expect(latestRound(empty)).toBeUndefined();
    expect(nodeViews(empty)).toEqual([]);
  });
});

describe("run grouping", () => {
  test("groups runs under the session that launched them", () => {
    const groups = groupRuns(
      summaries,
      new Map([
        [LIVE_RUN, live],
        [HISTORY_RUN, historical],
      ]),
    );
    expect(groups.map(({ launcher }) => launcher)).toEqual(["Codex", "Claude"]);
    expect(groups[0]?.label).toMatch(/^Codex session · c0dec0de…$/);
    expect(groups.map(({ runs }) => runs.map(({ run_id }) => run_id))).toEqual([
      [LIVE_RUN],
      [HISTORY_RUN],
    ]);
  });

  test("keeps a run without transcripts visible under an unknown launcher", () => {
    const groups = groupRuns(summaries, new Map());
    expect(groups.map(({ launcher }) => launcher)).toEqual([
      "Unknown",
      "Unknown",
    ]);
    expect(groups.map(({ runs }) => runs.length)).toEqual([1, 1]);
  });
});
