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
    expect(worker?.displayLabel).toBe(
      "Worker (engineer-dashboard) · conversation 1",
    );
    expect(worker?.children.map(({ displayLabel }) => displayLabel)).toEqual([
      "conversation-turn",
      "Judge · conversation 1",
      "Lint · conversation 1",
    ]);
    expect(dashboard.rows.map(({ displayLabel }) => displayLabel)).toContain(
      "Check-in",
    );
    expect(dashboard.rows.map(({ displayLabel }) => displayLabel)).toContain(
      "PR author",
    );
    // A rollup stands in for thousands of records and carries their total itself.
    expect(dashboard.rows[1]?.durationMs).toBe(4200);
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
    expect(group?.label).toBe("204 × dispatch");
    expect(group?.children).toHaveLength(204);
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
      findRow(projected.rows, "dispatch-worker-session")?.displayLabel,
    ).toContain("conversation 1");
    expect(findRow(projected.rows, "dispatch-worker-retry")?.displayLabel).toBe(
      "Worker (engineer-dashboard-retry) · retry 1 · conversation 2",
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
    if (worker === undefined || judge === undefined)
      throw new Error("fixture lost worker or judge");
    const coincident = compactTimelineItems([
      { ...judge, end: judge.start },
      { ...worker, start: judge.start, end: judge.start },
    ]);
    expect(coincident).toHaveLength(1);
    expect(coincident[0]?.laneId).toBe("worker");
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
