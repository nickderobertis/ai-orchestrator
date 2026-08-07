import type { RunTimeline, TimelineSpan } from "@ai-orchestrator/dag-model";
import type {
  TimelineItem,
  TimelineLane,
  TimelineMarker,
} from "@oneharness/ui";
import { formatDuration } from "../../lib/time";
import type { NodeView } from "../runs/run-model";
import {
  compactTimelineItems,
  compactTimelineMarkers,
  laneLabel,
  NODE_LANES,
  spanLane,
} from "./timeline-model";

/**
 * The whole run as one clock: what it has spent its life on, node by node, from launch
 * to finish — or to the moment the record was read, while it is still running.
 *
 * The node view answers "what did this node do"; this answers the question above it,
 * and the two are read in the same vocabulary so a reader moving between them is not
 * learning a second one. Its rows are the graph's own: the run-level sessions that
 * drive it, then one per plan node. Every row is plotted on one shared range, so the
 * columns of a row mean the same instants as the columns of the row above it.
 *
 * The gaps are the point. A run's wall time is mostly not work — a node waits on a
 * dependency, a graph waits on a person, a driver waits on a provider — and a plot
 * that leaves those stretches blank cannot be told from one whose record is missing.
 * They are recorded here as segments of their own.
 */

/** The lane the run's own unrecorded stretches are plotted in. */
export const IDLE_LANE_ID = "idle";

/**
 * The id every idle segment starts with.
 *
 * The upstream plot derives each segment's tooltip id from the item id, and that is
 * what a stylesheet has to reach an idle segment through: it carries no other mark of
 * its own. Anything selecting on this prefix is selecting on this constant.
 */
export const IDLE_ID_PREFIX = "idle-";

/**
 * Row ids are namespaced, because a plan may name a node anything at all — including
 * whatever this row would otherwise have been called.
 */
export const RUN_ROW_ID = "row:run";
export const nodeRowId = (nodeId: string): string => `row:node:${nodeId}`;

const IDLE_LANE: TimelineLane = { id: IDLE_LANE_ID, label: "Idle" };

/** Every category a graph row is read in: the node vocabulary, plus its own silence. */
export const GRAPH_LANES: readonly TimelineLane[] = [...NODE_LANES, IDLE_LANE];

/**
 * One plotted stretch of a row: recorded work, or recorded silence.
 *
 * `nodeId` is what a click acts on — every segment of a node's row opens that node,
 * whether the node was working then or not — and `conversationId` is what a run-level
 * session opens instead, since there is no node to drill into.
 */
export interface GraphSegment {
  readonly id: string;
  readonly kind: "work" | "idle";
  readonly label: string;
  /** The row this segment was projected from; absent on the whole-graph line's idle. */
  readonly rowId?: string;
  readonly nodeId?: string;
  readonly conversationId?: string;
  readonly startedAt: string;
  /** `null` for work the record never closed. */
  readonly endedAt: string | null;
  readonly durationMs: number;
}

export interface GraphRow {
  readonly id: string;
  readonly kind: "run" | "node";
  readonly label: string;
  readonly nodeId?: string;
  /** The lanes this row recorded anything in, in the canonical order. */
  readonly lanes: readonly TimelineLane[];
  readonly items: readonly TimelineItem<GraphSegment>[];
  /**
   * The same row as one line: its silence, and as much of its work as can be told
   * apart on a single row. Collapsed, every category shares one row and the plot
   * gives each segment a minimum width a finger can hit, so two activities a moment
   * apart cover one another — and the one underneath cannot be read or clicked.
   */
  readonly line: readonly TimelineItem<GraphSegment>[];
  readonly markers: readonly TimelineMarker<GraphSegment>[];
  /** How long this row recorded work, and how long it recorded none. */
  readonly workedMs: number;
  readonly idleMs: number;
}

export interface GraphTimeline {
  /** Launch to finish, or to the moment the served record was read while it runs. */
  readonly range: readonly [number, number];
  /** True while the record holds work it never closed. */
  readonly live: boolean;
  readonly rows: readonly GraphRow[];
  /** The single line: the graph's dominant activity, its silence, and its markers. */
  readonly line: {
    readonly items: readonly TimelineItem<GraphSegment>[];
    readonly markers: readonly TimelineMarker<GraphSegment>[];
    readonly lanes: readonly TimelineLane[];
  };
}

/**
 * An interior gap narrower than this share of the run is not drawn.
 *
 * Below it a segment is a hairline nobody can read or click, and a row of them reads
 * as noise rather than as time. The gaps at the two ends are never dropped, whatever
 * their width: they are what makes every row span the same interval, which is what
 * keeps one zoom shared across all of them.
 */
const MIN_INTERIOR_IDLE = 0.005;

export function graphTimeline(
  timeline: RunTimeline | undefined,
  nodes: readonly NodeView[],
): GraphTimeline {
  const spans = timeline?.spans ?? [];
  const range = runRange(timeline);
  const runLevel = spans.filter((span) => span.node_id === undefined);
  const rows: GraphRow[] = [
    row({
      id: RUN_ROW_ID,
      kind: "run",
      label: "Run-level",
      range,
      spans: runLevel,
    }),
    ...nodes.map((node) =>
      row({
        id: nodeRowId(node.id),
        kind: "node",
        label: node.label,
        nodeId: node.id,
        range,
        spans: spans.filter((span) => span.node_id === node.id),
      }),
    ),
  ];
  const work = rows.flatMap((entry) =>
    entry.items.filter((item) => item.payload.kind === "work"),
  );
  return {
    range,
    live: spans.some((span) => span.ended_at === null),
    rows,
    line: {
      // The graph is idle only where every row is, so the line's own gaps are the
      // complement of all the work there was rather than any one row's — and, like
      // every row's, they are laid down first so the work sits over them.
      items: [
        ...idleItems(range, work, undefined),
        ...compactTimelineItems(work),
      ],
      markers: compactTimelineMarkers(
        rows.flatMap((entry) => [...entry.markers]),
        work,
      ),
      lanes: GRAPH_LANES,
    },
  };
}

/**
 * Launch to finish, or to when the record was read.
 *
 * "Now" is the server's own `observed_at` rather than the browser's clock: it is the
 * instant this payload describes, so the plot says how long the run had been going
 * when it was last read instead of drifting a pixel per animation frame.
 */
function runRange(timeline?: RunTimeline): readonly [number, number] {
  const spans = timeline?.spans ?? [];
  const starts = spans.map((span) => Date.parse(span.started_at));
  if (starts.length === 0) return [0, 1];
  const start = Math.min(...starts);
  const recorded = Math.max(
    ...spans.map((span) =>
      span.ended_at === null
        ? Date.parse(span.started_at)
        : Date.parse(span.ended_at),
    ),
  );
  const observed = Date.parse(timeline?.observed_at ?? "");
  const open = spans.some((span) => span.ended_at === null);
  const end =
    open && Number.isFinite(observed) ? Math.max(recorded, observed) : recorded;
  return [start, end > start ? end : start + 1];
}

function row({
  id,
  kind,
  label,
  nodeId,
  range,
  spans,
}: {
  readonly id: string;
  readonly kind: GraphRow["kind"];
  readonly label: string;
  readonly nodeId?: string;
  readonly range: readonly [number, number];
  readonly spans: readonly TimelineSpan[];
}): GraphRow {
  const work = spans.flatMap((span): TimelineItem<GraphSegment>[] => {
    const lane = spanLane(span);
    if (lane === null) return [];
    const start = Date.parse(span.started_at);
    const end = plottedEnd(span, lane, start, range);
    const conversationId =
      span.reference?.kind === "conversation"
        ? span.reference.value
        : undefined;
    return [
      {
        id: span.id,
        label: `${label} · ${laneLabel(lane)}${summarized(span)}`,
        laneId: lane,
        start,
        end,
        duration: end - start,
        status: span.status,
        payload: {
          id: span.id,
          kind: "work",
          label: `${label} · ${laneLabel(lane)}`,
          rowId: id,
          ...(nodeId === undefined ? {} : { nodeId }),
          ...(conversationId === undefined ? {} : { conversationId }),
          startedAt: span.started_at,
          endedAt: span.ended_at,
          durationMs: end - start,
        },
      },
    ];
  });
  const idle = idleItems(range, work, { rowId: id, nodeId });
  const workedMs = union(work).reduce(
    (total, [from, to]) => total + (to - from),
    0,
  );
  return {
    id,
    kind,
    label,
    ...(nodeId === undefined ? {} : { nodeId }),
    lanes: GRAPH_LANES.filter(({ id: lane }) =>
      [...work, ...idle].some((item) => item.laneId === lane),
    ),
    // Silence first, work over it. The plot draws in the order it is given and gives
    // every segment a minimum width a finger can hit, so a moment's work is wider on
    // screen than in time — and the silence that begins where it ended would cover it.
    items: [...idle, ...work],
    line: [...idle, ...compactTimelineItems(work)],
    markers: spans.flatMap((span) =>
      span.events.map(
        (event): TimelineMarker<GraphSegment> => ({
          id: event.id,
          label: `${label} · ${event.step_id ?? event.kind}`,
          at: Date.parse(event.at),
          status: event.status,
          payload: {
            id: event.id,
            kind: "work",
            label: event.kind,
            rowId: id,
            ...(nodeId === undefined ? {} : { nodeId }),
            startedAt: event.at,
            endedAt: null,
            durationMs: 0,
          },
        }),
      ),
    ),
    workedMs,
    idleMs: idle.reduce((total, item) => total + item.payload.durationMs, 0),
  };
}

/**
 * Where a plotted segment ends.
 *
 * Work the record never closed runs to the end of the plotted range, which is what an
 * in-flight dispatch looks like rather than an instant. The one exception is the
 * aggregate lane: those segments stand in for thousands of separate waits and carry
 * their total themselves, so the window they fell in would draw a bar across the whole
 * node for a wait of a few seconds.
 */
function plottedEnd(
  span: TimelineSpan,
  lane: string,
  start: number,
  range: readonly [number, number],
): number {
  if (lane === "lock-waits" && span.total_duration_ms !== undefined)
    return start + span.total_duration_ms;
  return span.ended_at === null ? range[1] : Date.parse(span.ended_at);
}

/** ` · 4 recorded` for a summary standing in for several activities, else nothing. */
function summarized(span: TimelineSpan): string {
  return span.count !== undefined && span.count > 1
    ? ` · ${span.count} recorded`
    : "";
}

/** The stretches of `range` that `items` cover, merged and in order. */
function union(
  items: readonly TimelineItem<GraphSegment>[],
): readonly (readonly [number, number])[] {
  const ordered = items
    .map((item): readonly [number, number] => [
      item.start,
      Math.max(item.end ?? item.start, item.start),
    ])
    .filter(([from, to]) => Number.isFinite(from) && Number.isFinite(to))
    .sort((left, right) => left[0] - right[0]);
  const merged: [number, number][] = [];
  for (const [from, to] of ordered) {
    const open = merged.at(-1);
    if (open !== undefined && from <= open[1]) open[1] = Math.max(open[1], to);
    else merged.push([from, to]);
  }
  return merged;
}

/** Everything in `range` that `items` do not cover, as segments a reader can see. */
function idleItems(
  range: readonly [number, number],
  items: readonly TimelineItem<GraphSegment>[],
  owner: { readonly rowId: string; readonly nodeId?: string } | undefined,
): readonly TimelineItem<GraphSegment>[] {
  const covered = union(items);
  const gaps: (readonly [number, number])[] = [];
  let cursor = range[0];
  for (const [from, to] of covered) {
    if (from > cursor) gaps.push([cursor, from]);
    cursor = Math.max(cursor, to);
  }
  if (cursor < range[1]) gaps.push([cursor, range[1]]);
  const span = Math.max(1, range[1] - range[0]);
  return gaps
    .filter(
      ([from, to], index) =>
        index === 0 ||
        index === gaps.length - 1 ||
        (to - from) / span >= MIN_INTERIOR_IDLE,
    )
    .map(([from, to], index) => {
      const id = `${IDLE_ID_PREFIX}${owner?.rowId ?? "graph"}-${index}`;
      const segment: GraphSegment = {
        id,
        kind: "idle",
        label: `Idle · ${formatDuration(to - from)}`,
        ...(owner === undefined ? {} : { rowId: owner.rowId }),
        ...(owner?.nodeId === undefined ? {} : { nodeId: owner.nodeId }),
        startedAt: new Date(from).toISOString(),
        endedAt: new Date(to).toISOString(),
        durationMs: to - from,
      };
      return {
        id,
        label: segment.label,
        laneId: IDLE_LANE_ID,
        start: from,
        end: to,
        duration: to - from,
        status: "idle",
        payload: segment,
      };
    });
}
