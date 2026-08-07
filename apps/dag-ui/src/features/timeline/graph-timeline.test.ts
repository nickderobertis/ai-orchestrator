import { parseRunDetail, parseRunTimeline } from "@ai-orchestrator/dag-model";
import { describe, expect, test } from "vitest";
import {
  HISTORY_RUN,
  LIVE_RUN,
  runDetail,
  runScopeTimeline,
} from "../../test/fixtures";
import { nodeViews } from "../runs/run-model";
import {
  graphTimeline,
  IDLE_ID_PREFIX,
  nodeRowId,
  RUN_ROW_ID,
} from "./graph-timeline";

const live = parseRunTimeline(runScopeTimeline(LIVE_RUN));
const liveNodes = nodeViews(parseRunDetail(runDetail(LIVE_RUN)));
const settled = parseRunTimeline(runScopeTimeline(HISTORY_RUN));
const settledNodes = nodeViews(parseRunDetail(runDetail(HISTORY_RUN)));

/** `2026-07-26T11:00:00Z` plus `seconds`, which is where every fixture stamp sits. */
const at = (seconds: number) =>
  Date.UTC(2026, 6, 26, 11, 0, 0) + seconds * 1000;

const rowFor = (
  graph: ReturnType<typeof graphTimeline>,
  id: string,
): NonNullable<ReturnType<typeof graphTimeline>["rows"][number]> => {
  const found = graph.rows.find((row) => row.id === id);
  if (found === undefined) throw new Error(`no row ${id}`);
  return found;
};

describe("the whole graph on one clock", () => {
  test("runs from launch to the moment an unfinished record was read", () => {
    const graph = graphTimeline(live, liveNodes);
    // The round span opened at the first record and has never closed, so the run is
    // still going and the plot runs to when the server read it — not to the last
    // thing that happened to be recorded, which would shrink as the run went on.
    expect(graph.live).toBe(true);
    expect(graph.range).toEqual([at(0), at(240)]);

    // A finished run stops where it stopped; nothing extends it towards now.
    const done = graphTimeline(settled, settledNodes);
    expect(done.live).toBe(false);
    expect(done.range).toEqual([at(0), at(120)]);
  });

  test("gives the run's own sessions a row, and every plan node one", () => {
    const graph = graphTimeline(live, liveNodes);
    expect(graph.rows[0]?.id).toBe(RUN_ROW_ID);
    expect(graph.rows[0]?.kind).toBe("run");
    expect(graph.rows.slice(1).map(({ nodeId }) => nodeId)).toEqual(
      liveNodes.map(({ id }) => id),
    );

    // The run row is the orchestrator driving the graph and the round's check-in,
    // each in the lane its served roles name — not one undifferentiated "planner".
    expect(rowFor(graph, RUN_ROW_ID).lanes.map(({ id }) => id)).toEqual([
      "orchestrator",
      "check-in",
      "idle",
    ]);
  });

  test("reads a node's summaries as the lanes the node view draws", () => {
    const dashboard = rowFor(
      graphTimeline(live, liveNodes),
      nodeRowId("dashboard"),
    );
    // Including Lint, which shares the worker's semantic role and is told from it by
    // the transport the summary carries — the pair, not either half.
    expect(dashboard.lanes.map(({ id }) => id)).toEqual([
      "worker",
      "judge",
      "lint",
      "check-in",
      "pr-author",
      "lock-waits",
      "idle",
    ]);
    const lint = dashboard.items.find(({ laneId }) => laneId === "lint");
    expect(lint?.label).toBe("dashboard · Lint");
    expect(lint?.start).toBe(at(20));
    expect(lint?.end).toBe(at(50));

    // The aggregate is plotted at the time it cost, never across the window those
    // 1240 waits happened to fall in.
    const waits = dashboard.items.find(({ laneId }) => laneId === "lock-waits");
    expect(waits?.start).toBe(at(15));
    expect(waits?.end).toBe(at(15) + 4200);
    expect(waits?.label).toBe("dashboard · Lock waits · 1240 recorded");
  });

  test("draws the stretches a row recorded nothing in", () => {
    const graph = graphTimeline(live, liveNodes);
    const foundation = rowFor(graph, nodeRowId("foundation"));
    const idle = foundation.items.filter(({ laneId }) => laneId === "idle");
    // Its verification ran from +30s to +95s and its publication from +100s to +180s,
    // so the row is silent before, between, and after — and each stretch is a segment
    // of its own rather than blank space that could equally mean "not recorded".
    expect(idle.map((item) => [item.start, item.end])).toEqual([
      [at(0), at(30)],
      [at(95), at(100)],
      [at(180), at(240)],
    ]);
    expect(idle[1]?.label).toBe("Idle · 5s");
    expect(idle.every(({ id }) => id.startsWith(IDLE_ID_PREFIX))).toBe(true);
    expect(foundation.workedMs).toBe(145_000);
    expect(foundation.idleMs).toBe(95_000);

    // A node the run has not reached recorded nothing at all, which is the whole run
    // spent idle rather than a row with nothing in it.
    const queued = rowFor(graph, nodeRowId("queued"));
    expect(queued.items).toHaveLength(1);
    expect(queued.items[0]?.laneId).toBe("idle");
    expect(queued.workedMs).toBe(0);
    expect(queued.idleMs).toBe(240_000);
  });

  test("every row spans the same interval, which is what one zoom rests on", () => {
    const graph = graphTimeline(live, liveNodes);
    for (const row of graph.rows) {
      const starts = row.items.map(({ start }) => start);
      const ends = row.items.map((item) => item.end ?? item.start);
      expect([Math.min(...starts), Math.max(...ends)]).toEqual([
        ...graph.range,
      ]);
    }
  });

  test("the single line is the graph's work, and the silence of all of it", () => {
    const graph = graphTimeline(live, liveNodes);
    const idle = graph.line.items.filter(({ laneId }) => laneId === "idle");
    // The orchestrator opened at +1s and something has been recorded ever since — the
    // human wait this graph is still sitting in runs to the moment it was read — so
    // the graph as a whole is idle only in the second before it started.
    expect(idle.map((item) => [item.start, item.end])).toEqual([
      [at(0), at(1)],
    ]);
    const waiting = rowFor(graph, nodeRowId("approval")).items.find(
      ({ laneId }) => laneId === "human-wait",
    );
    expect([waiting?.start, waiting?.end]).toEqual([at(75), at(240)]);
    // And the work it draws is bounded rather than one bar per summary in the run.
    expect(graph.line.items.length).toBeLessThan(
      graph.rows.flatMap((row) => row.items).length,
    );
    expect(graph.line.items.every(({ payload }) => payload.kind !== undefined));
  });

  test("carries what a click on a segment should open", () => {
    const graph = graphTimeline(live, liveNodes);
    const session = rowFor(graph, RUN_ROW_ID).items.find(
      ({ laneId }) => laneId === "orchestrator",
    );
    // A run-level session has no node to drill into; it opens its own transcript.
    expect(session?.payload.nodeId).toBeUndefined();
    expect(session?.payload.conversationId).toBe("orchestrator-session");

    // Every segment of a node's row opens that node, silence included.
    const publish = rowFor(graph, nodeRowId("publish"));
    expect(
      publish.items.every(({ payload }) => payload.nodeId === "publish"),
    ).toBe(true);
  });

  test("answers for a run whose timeline has not arrived", () => {
    const graph = graphTimeline(undefined, liveNodes);
    expect(graph.range).toEqual([0, 1]);
    expect(graph.live).toBe(false);
    expect(graph.line.items).toHaveLength(1);
  });
});
