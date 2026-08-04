import { parseRunTimeline } from "@ai-orchestrator/dag-model";
import { describe, expect, test } from "vitest";
import { busyTimeline, LIVE_RUN, runTimeline } from "../../test/fixtures";
import {
  compactTimelineItems,
  compactTimelineMarkers,
  findRow,
  GROUP_THRESHOLD,
  nodeTimeline,
  nodeTimelineV2,
  pathTo,
} from "./timeline-model";

const timeline = parseRunTimeline(runTimeline(LIVE_RUN));

describe("one node's slice of the run timeline", () => {
  test("lists the node's own recorded work, in order, with its durations", () => {
    const dashboard = nodeTimeline(timeline, "dashboard");
    expect(dashboard.span?.id).toBe("node-1-dashboard");
    // The node's own span is the subject of the view, so what the rail lists is
    // what happened inside it — its dispatches, its rollup, its own events.
    expect(dashboard.rows.map(({ id }) => id)).toEqual([
      "dispatch-worker-session",
      "rollup-lock-wait-11",
      "event-9",
      "dispatch-check-in-session",
      "dispatch-pr-author-session",
    ]);
    const worker = dashboard.rows[0];
    expect(worker?.kind).toBe("dispatch");
    expect(worker?.status).toBe("completed");
    // 11:00:12 to 11:01:00 is the interval the stream recorded for it.
    expect(worker?.durationMs).toBe(48_000);
    expect(worker?.displayLabel).toBe("Worker (engineer-dashboard)");
    // Every session says which role it played and which session it was — three
    // concurrent sessions of one dispatch are told apart by nothing else.
    expect(worker?.children.map(({ displayLabel }) => displayLabel)).toEqual([
      "conversation-turn",
      "Lint (llmlint-dashboard)",
      "Judge (you-are-a-strict-careful-evaluator)",
    ]);
    expect(dashboard.rows.map(({ displayLabel }) => displayLabel)).toContain(
      "Check-in (check-in-dashboard)",
    );
    expect(dashboard.rows.map(({ displayLabel }) => displayLabel)).toContain(
      "PR author (pr-author-dashboard)",
    );
    // An aggregate stands in for thousands of records and carries their total
    // itself; it is named for what was aggregated, never for the word `rollup`.
    expect(dashboard.rows[1]?.durationMs).toBe(4200);
    expect(dashboard.rows[1]).toMatchObject({
      displayKind: "Lock waits",
      displayLabel: "Lock waits: 1240 recorded",
    });
  });

  test("gathers one dispatch's agent, lint, and judge sessions under it", () => {
    const dashboard = nodeTimeline(timeline, "dashboard");
    const worker = findRow(dashboard.rows, "dispatch-worker-session");
    // The agent session names the dispatch, and every session recorded inside it
    // carries the same name — which is what lets the transcript nest them.
    expect(worker?.dispatch).toEqual({
      id: "dispatch-worker-session",
      label: "Dispatch 1",
    });
    expect(
      worker?.children
        .filter(({ rowKind }) => rowKind === "span")
        .map(({ dispatch }) => dispatch?.label),
    ).toEqual(["Dispatch 1", "Dispatch 1"]);
    // A separately dispatched role is its own dispatch, not a member of the first.
    expect(
      findRow(dashboard.rows, "dispatch-check-in-session")?.dispatch,
    ).toEqual({ id: "dispatch-check-in-session", label: "Dispatch 2" });
    // The aggregate is no session at all, so it belongs to no dispatch.
    expect(
      findRow(dashboard.rows, "rollup-lock-wait-11")?.dispatch,
    ).toBeUndefined();
  });

  test("reports work the stream never closed as still running", () => {
    const approval = nodeTimeline(timeline, "approval");
    expect(
      approval.rows.map(({ kind, endedAt, durationMs }) => [
        kind,
        endedAt,
        durationMs,
      ]),
    ).toEqual([["human-wait", null, null]]);
  });

  test("keeps a node's publication events under the publication they belong to", () => {
    const foundation = nodeTimeline(timeline, "foundation");
    const publication = findRow(foundation.rows, "publication-6");
    expect(publication?.status).toBe("finished");
    expect(publication?.children.map(({ kind }) => kind)).toEqual([
      "pr-created",
      "pr-checks-observed",
    ]);
    expect(pathTo(foundation.rows, "event-7").map(({ id }) => id)).toEqual([
      "publication-6",
      "event-7",
    ]);
  });

  test("says nothing at all about a node the run never recorded", () => {
    expect(nodeTimeline(timeline, "queued")).toEqual({ rows: [], total: 0 });
    expect(nodeTimeline(undefined, "dashboard").rows).toEqual([]);
  });

  test("collapses hundreds of sessions into rows a reader can scan", () => {
    const busy = nodeTimeline(parseRunTimeline(busyTimeline(200)), "dashboard");
    // Two hundred conversations arrive as one row, not two hundred; the run they
    // stand for — every consecutive dispatch, including the four the fixture's own
    // node recorded — is still reachable inside it, in the order it happened.
    const group = busy.rows.find(({ rowKind }) => rowKind === "group");
    expect(group?.label).toBe("203 × dispatch");
    expect(group?.children).toHaveLength(203);
    expect(busy.rows.length).toBeLessThan(GROUP_THRESHOLD);
    expect(busy.total).toBeGreaterThan(200);
  });

  test("names lifecycle steps and distinguishes a retried worker dispatch", () => {
    const fixture = parseRunTimeline(runTimeline(LIVE_RUN));
    const node = fixture.spans.find(({ id }) => id === "node-1-dashboard");
    const worker = fixture.spans.find(
      ({ id }) => id === "dispatch-worker-session",
    );
    if (node === undefined || worker === undefined)
      throw new Error("fixture lost dashboard work");
    node.events.push({
      id: "retry-1",
      kind: "retry-requested",
      at: "2026-07-26T11:02:35.000Z",
      node_id: "dashboard",
      round: 1,
    });
    fixture.spans.push(
      {
        ...worker,
        id: "dispatch-worker-retry",
        label: "engineer-dashboard-retry",
        started_at: "2026-07-26T11:02:40.000Z",
        ended_at: "2026-07-26T11:03:00.000Z",
      },
      {
        id: "step-build",
        kind: "step",
        label: "Build and verify",
        parent_id: "node-1-dashboard",
        node_id: "dashboard",
        step_id: "build",
        round: 1,
        started_at: "2026-07-26T11:03:01.000Z",
        ended_at: "2026-07-26T11:03:20.000Z",
        status: "done",
        events: [],
      },
    );

    const projected = nodeTimeline(fixture, "dashboard");
    expect(
      findRow(projected.rows, "dispatch-worker-session")?.dispatch,
    ).toMatchObject({ label: "Dispatch 1" });
    expect(findRow(projected.rows, "dispatch-worker-retry")?.displayLabel).toBe(
      "Worker (engineer-dashboard-retry) · retry 1",
    );
    // A retry is a second dispatch of the same node, never a second session of the
    // first one, so it opens a group of its own.
    expect(findRow(projected.rows, "dispatch-worker-retry")?.dispatch?.id).toBe(
      "dispatch-worker-retry",
    );
    expect(findRow(projected.rows, "step-build")).toMatchObject({
      displayKind: "Lifecycle",
      displayLabel: "Lifecycle: Build and verify",
    });
    expect(projected.rows.some(({ id }) => id === "node-1-dashboard")).toBe(
      false,
    );
  });

  test("projects intervals into deterministic lanes and journals into markers", () => {
    const projected = nodeTimelineV2(timeline, "dashboard");
    expect(projected.lanes.map(({ label }) => label)).toEqual([
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
    expect(
      projected.items.find(({ id }) => id === "dispatch-judge-session"),
    ).toMatchObject({ laneId: "judge" });
    expect(
      projected.items.find(({ id }) => id === "rollup-lock-wait-11"),
    ).toMatchObject({ laneId: "lock-waits", duration: 4200 });
    expect(projected.markers.some(({ id }) => id === "event-9")).toBe(true);
    expect(projected.items.some(({ id }) => id === "event-9")).toBe(false);
  });

  test("keeps one deterministic hit target for coincident compact items", () => {
    const projected = nodeTimelineV2(timeline, "dashboard");
    const worker = projected.items.find(({ laneId }) => laneId === "worker");
    const judge = projected.items.find(({ laneId }) => laneId === "judge");
    const publication = projected.items.find(({ laneId }) => laneId === "lint");
    if (
      worker === undefined ||
      judge === undefined ||
      publication === undefined
    )
      throw new Error("fixture lost the dashboard's dispatches");
    // Three moments, two of them coincident and neither an end of the window: the
    // pair collapses to the category that dominates it, and the window's own ends
    // are untouched, so what the compact line spans is what the node spans.
    const first = { ...publication, start: 0, end: 10 };
    const compacted = compactTimelineItems([
      first,
      { ...judge, start: 1_000, end: 1_000 },
      { ...worker, start: 1_000, end: 1_000 },
      { ...publication, id: "last", start: 100_000, end: 100_010 },
    ]);
    expect(compacted.map(({ id }) => id)).toEqual([
      first.id,
      worker.id,
      "last",
    ]);
  });

  test("spans the same window compact as expanded", () => {
    const projected = nodeTimelineV2(timeline, "dashboard");
    const worker = projected.items.find(({ laneId }) => laneId === "worker");
    const waits = projected.items.find(({ laneId }) => laneId === "lock-waits");
    if (worker === undefined || waits === undefined)
      throw new Error("fixture lost the dashboard's work");
    const bounds = (items: readonly { start: number; end?: number | null }[]) =>
      [
        Math.min(...items.map(({ start }) => start)),
        Math.max(...items.map((item) => item.end ?? item.start)),
      ] as const;
    // The shape the server serves: a short aggregate at the very start of the node,
    // and a session opening inside the same visual cluster that dominates it — over
    // a window wide enough that the two really do compete for one hit target.
    const served = [
      { ...waits, start: 0, end: 10_000 },
      { ...worker, start: 5_000, end: 55_000 },
      { ...worker, id: "last", start: 225_000, end: 255_000 },
    ];
    // The axis and the time cursor are read straight off the plotted items, so a
    // compaction that dropped either end would move the same moment on collapse.
    expect(bounds(compactTimelineItems(served))).toEqual(bounds(served));
    expect(compactTimelineItems(served)).toHaveLength(3);
  });

  test("keeps a selected marker clickable when journal icons coincide", () => {
    const projected = nodeTimelineV2(timeline, "dashboard");
    const markers = projected.markers.slice(0, 2);
    const second = markers[1];
    if (second === undefined) throw new Error("fixture lost journal markers");
    const coincident = markers.map((marker) => ({ ...marker, at: second.at }));
    expect(compactTimelineMarkers(coincident, projected.items)).toHaveLength(1);
    expect(
      compactTimelineMarkers(coincident, projected.items, second.id)[0]?.id,
    ).toBe(second.id);
  });
});
