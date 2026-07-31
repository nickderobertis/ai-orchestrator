import { parseRunDetail, parseRunList } from "@ai-orchestrator/dag-model";
import { describe, expect, test } from "vitest";
import { HISTORY_RUN, LIVE_RUN, runDetail, runList } from "../../test/fixtures";
import { groupRuns, latestRound, nodeViews } from "./run-model";

const live = parseRunDetail(runDetail(LIVE_RUN));
const summaries = parseRunList(runList).runs;

describe("node views", () => {
  test("classifies kind, projects state, and carries each node's own record", () => {
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
    expect(groups[0]?.label).toMatch(/^Codex session · c0dec0de…$/);
    expect(groups.map(({ runs }) => runs.map(({ run_id }) => run_id))).toEqual([
      [LIVE_RUN],
      [HISTORY_RUN],
    ]);
  });

  test("gathers every run of one launch, and keeps an unattributed run apart", () => {
    const [first, second] = summaries;
    if (first === undefined || second === undefined) throw new Error("fixture");
    const groups = groupRuns([
      first,
      { ...first, run_id: "sibling" },
      { ...second, run_id: "orphan", launch: undefined },
    ]);
    expect(groups.map(({ launcher }) => launcher)).toEqual([
      "Codex",
      "Unknown",
    ]);
    expect(groups[0]?.runs.map(({ run_id }) => run_id)).toEqual([
      LIVE_RUN,
      "sibling",
    ]);
    // An unattributed run gets a group of its own rather than being hidden or
    // pooled with every other run that recorded no launch.
    expect(groups[1]?.runs.map(({ run_id }) => run_id)).toEqual(["orphan"]);
  });
});
