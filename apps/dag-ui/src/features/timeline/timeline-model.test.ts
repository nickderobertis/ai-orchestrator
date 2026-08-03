import { parseRunTimeline } from "@ai-orchestrator/dag-model";
import { describe, expect, test } from "vitest";
import { busyTimeline, LIVE_RUN, runTimeline } from "../../test/fixtures";
import {
  findRow,
  GROUP_THRESHOLD,
  nodeTimeline,
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
      "dispatch-judge-session",
      "dispatch-check-in-session",
      "dispatch-pr-author-session",
      "dispatch-llmlint-session",
    ]);
    const worker = dashboard.rows[0];
    expect(worker?.kind).toBe("dispatch");
    expect(worker?.status).toBe("completed");
    // 11:00:12 to 11:01:00 is the interval the stream recorded for it.
    expect(worker?.durationMs).toBe(48_000);
    expect(worker?.children.map(({ kind }) => kind)).toEqual([
      "conversation-turn",
    ]);
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
});
