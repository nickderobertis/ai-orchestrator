import type {
  RunTimeline,
  TimelineEvent,
  TimelineSpan,
} from "@ai-orchestrator/dag-model";

/**
 * One node's slice of the served run timeline, as rows a rail can render.
 *
 * The served payload is a flat span list carrying the tree in `parent_id`; this
 * rebuilds that tree for one node, merges each span's child spans with its own
 * events into one recorded order, and collapses long runs of same-kind siblings so
 * a node that dispatched two hundred sessions is a row rather than two hundred.
 */

/** Siblings of one kind collapse into a group once a run reaches this many. */
export const GROUP_THRESHOLD = 8;

interface RowBase {
  readonly id: string;
  /** What the row is: a span kind, a journal event kind, or the grouped span kind. */
  readonly kind: string;
  /**
   * What a dispatch was for — worker, judge, orchestrator, check-in, pr-author, or
   * the lint run under a worker. Served on the span itself, so a row says which
   * session it is without the transcript behind it being fetched. Absent on every
   * other kind of row, and on a dispatch recorded before the roles were served.
   */
  readonly role?: string;
  readonly label: string;
  readonly startedAt: string;
  /** `null` for work the recorded stream never closed, and for an instant. */
  readonly endedAt: string | null;
  readonly status?: string;
  readonly durationMs: number | null;
  readonly children: readonly TimelineRow[];
  /** Operator-facing identity; never the free-text transport/session label. */
  readonly displayLabel: string;
  /** Legend vocabulary, with lifecycle wrappers presented as named phases. */
  readonly displayKind: string;
}

export type TimelineRow =
  | (RowBase & { readonly rowKind: "span"; readonly span: TimelineSpan })
  | (RowBase & { readonly rowKind: "event"; readonly event: TimelineEvent })
  | (RowBase & { readonly rowKind: "group"; readonly count: number });

export interface NodeTimeline {
  /** The node's own span, when the recorded stream opened one. */
  readonly span?: TimelineSpan;
  readonly rows: readonly TimelineRow[];
  /** Every span and event this node recorded, however deeply nested. */
  readonly total: number;
}

const EMPTY: NodeTimeline = { rows: [], total: 0 };

export function nodeTimeline(
  timeline: RunTimeline | undefined,
  nodeId: string,
): NodeTimeline {
  if (timeline === undefined) return EMPTY;
  const scoped = timeline.spans.filter((span) => span.node_id === nodeId);
  if (scoped.length === 0) {
    const orphans = orphanEvents(timeline.spans, new Set(), nodeId);
    return orphans.length === 0
      ? EMPTY
      : { rows: group(orphans), total: orphans.length };
  }
  const ids = new Set(scoped.map(({ id }) => id));
  const children = new Map<string, TimelineSpan[]>();
  const roots: TimelineSpan[] = [];
  for (const span of scoped) {
    const parent =
      span.parent_id !== undefined && ids.has(span.parent_id)
        ? span.parent_id
        : undefined;
    if (parent === undefined) roots.push(span);
    else children.set(parent, [...(children.get(parent) ?? []), span]);
  }
  // The node's own span is the view's subject, not a row inside it: its children and
  // its own events are what the rail lists, so every row is one recorded activity.
  const own = roots.find(({ kind }) => kind === "node");
  const top = [
    ...(own === undefined ? [] : spanRows(own, children)),
    ...roots
      .filter((span) => span !== own)
      .map((span) => spanRow(span, children)),
    ...orphanEvents(timeline.spans, ids, nodeId),
  ].sort(byStart);
  // Pairing makes alternating worker/judge streams consecutive worker groups; run
  // the density cap again so hundreds of full conversations remain bounded.
  const rows = group(pairConversations(labelWorkerRetries(group(top))));
  return { span: own, rows, total: count(rows) };
}

/** Depth-first lookup of one row by the id the query string carries. */
export function findRow(
  rows: readonly TimelineRow[],
  id: string,
): TimelineRow | undefined {
  return pathTo(rows, id).at(-1);
}

/** The rows enclosing `id`, outermost first and ending with the row itself. */
export function pathTo(
  rows: readonly TimelineRow[],
  id: string,
): readonly TimelineRow[] {
  for (const row of rows) {
    if (row.id === id) return [row];
    const nested = pathTo(row.children, id);
    if (nested.length > 0) return [row, ...nested];
  }
  return [];
}

function spanRows(
  span: TimelineSpan,
  children: Map<string, TimelineSpan[]>,
): TimelineRow[] {
  return [
    ...(children.get(span.id) ?? []).map((child) => spanRow(child, children)),
    ...span.events.map(eventRow),
  ].sort(byStart);
}

function spanRow(
  span: TimelineSpan,
  children: Map<string, TimelineSpan[]>,
): TimelineRow {
  const role = dispatchRole(span);
  return {
    rowKind: "span",
    span,
    id: span.id,
    kind: span.kind,
    role,
    label: span.label,
    startedAt: span.started_at,
    endedAt: span.ended_at,
    status: span.status,
    // A rollup stands in for thousands of records and carries their total itself;
    // its own start-to-end interval would describe the contention window instead.
    durationMs:
      span.total_duration_ms ?? elapsed(span.started_at, span.ended_at),
    children: group(spanRows(span, children)),
    displayLabel: spanLabel(span, role),
    displayKind: span.kind === "step" ? "Phase" : roleKind(role, span.kind),
  };
}

/**
 * A dispatch's role as one word. Lint is the case that needs both halves: it is the
 * worker's own verification, told apart from the worker only by its transport role.
 */
function dispatchRole(span: TimelineSpan): string | undefined {
  if (span.kind !== "dispatch") return undefined;
  return span.transport_role === "llmlint" ? "llmlint" : span.agent_role;
}

function eventRow(event: TimelineEvent): TimelineRow {
  return {
    rowKind: "event",
    event,
    id: event.id,
    kind: event.kind,
    label: event.step_id ?? "",
    startedAt: event.at,
    endedAt: null,
    status: event.status,
    durationMs: null,
    children: [],
    displayLabel:
      event.kind === "retry-requested"
        ? "Retry requested"
        : (event.step_id ?? event.kind),
    displayKind: "Event",
  };
}

/**
 * Collapse each run of consecutive same-kind span siblings into one row once it is
 * long enough to stop being scannable. Order is preserved: a group stands exactly
 * where the spans it holds stood.
 */
function group(rows: readonly TimelineRow[]): TimelineRow[] {
  const grouped: TimelineRow[] = [];
  for (let index = 0; index < rows.length; index += 1) {
    const first = rows[index];
    if (first === undefined) continue;
    let end = index;
    while (end + 1 < rows.length && sameKindSpan(first, rows[end + 1]))
      end += 1;
    const run = rows.slice(index, end + 1);
    const last = run.at(-1);
    if (run.length < GROUP_THRESHOLD || last === undefined) {
      grouped.push(...run);
    } else {
      grouped.push({
        rowKind: "group",
        count: run.length,
        id: `group-${first.id}`,
        kind: first.kind,
        label: `${run.length} × ${first.kind}`,
        startedAt: first.startedAt,
        endedAt: last.endedAt,
        status: undefined,
        durationMs: elapsed(first.startedAt, last.endedAt),
        children: run,
        displayLabel: `${run.length} grouped ${first.displayKind.toLowerCase()} activities`,
        displayKind: first.displayKind,
      });
    }
    index = end;
  }
  return grouped;
}

function roleKind(role: string | undefined, fallback: string): string {
  switch (role) {
    case "worker":
      return "Worker";
    case "judge":
      return "Judge";
    case "llmlint":
      return "Lint";
    case "orchestrator":
      return "Orchestrator";
    case "check-in":
      return "Check-in";
    case "pr-author":
      return "PR author";
    default:
      return fallback;
  }
}

function spanLabel(span: TimelineSpan, role: string | undefined): string {
  if (span.kind === "step")
    return span.label ? `Phase: ${span.label}` : "Lifecycle phase";
  switch (role) {
    case "worker":
      return `Worker (${span.label || "worker"})`;
    case "judge":
      return "Judge";
    case "llmlint":
      return "Lint";
    case "orchestrator":
      return "Orchestrator";
    case "check-in":
      return "Check-in";
    case "pr-author":
      return "PR author";
    default:
      return span.label || span.kind;
  }
}

/** Label each worker attempt from retry-requested records without renaming sessions. */
function labelWorkerRetries(rows: readonly TimelineRow[]): TimelineRow[] {
  let retry = 0;
  return rows.map((row) => {
    if (row.rowKind === "event" && row.event.kind === "retry-requested")
      retry += 1;
    const children = labelWorkerRetries(row.children);
    if (row.role !== "worker" || retry === 0) return { ...row, children };
    return {
      ...row,
      children,
      displayLabel: `${row.displayLabel} · retry ${retry}`,
    };
  });
}

function pairConversations(rows: readonly TimelineRow[]): TimelineRow[] {
  const paired: TimelineRow[] = [];
  let workerIndex = -1;
  let conversation = 0;
  for (const row of rows) {
    if (row.role === "worker") {
      conversation += 1;
      workerIndex = paired.length;
      paired.push({
        ...row,
        displayLabel: `${row.displayLabel} · conversation ${conversation}`,
      });
      continue;
    }
    if ((row.role === "judge" || row.role === "llmlint") && workerIndex >= 0) {
      const worker = paired[workerIndex];
      if (worker !== undefined) {
        paired[workerIndex] = {
          ...worker,
          children: [
            ...worker.children,
            {
              ...row,
              displayLabel: `${row.displayLabel} · conversation ${conversation}`,
            },
          ],
        };
        continue;
      }
    }
    paired.push(row);
  }
  return paired;
}

function sameKindSpan(
  first: TimelineRow,
  next: TimelineRow | undefined,
): boolean {
  return (
    next !== undefined &&
    first.rowKind === "span" &&
    next.rowKind === "span" &&
    first.kind === next.kind
  );
}

/**
 * Events this node recorded that landed on a span belonging to another scope — a
 * record made before the node's own span opened hangs off the round instead, and
 * would otherwise be invisible from the node it names.
 */
function orphanEvents(
  spans: readonly TimelineSpan[],
  scoped: ReadonlySet<string>,
  nodeId: string,
): TimelineRow[] {
  return spans
    .filter(({ id }) => !scoped.has(id))
    .flatMap(({ events }) => events.filter((event) => event.node_id === nodeId))
    .map(eventRow);
}

function count(rows: readonly TimelineRow[]): number {
  return rows.reduce(
    (total, row) =>
      total + (row.rowKind === "group" ? 0 : 1) + count(row.children),
    0,
  );
}

function byStart(first: TimelineRow, next: TimelineRow): number {
  return (
    first.startedAt.localeCompare(next.startedAt) ||
    first.id.localeCompare(next.id)
  );
}

function elapsed(startedAt: string, endedAt: string | null): number | null {
  if (endedAt === null) return null;
  const start = Date.parse(startedAt);
  const end = Date.parse(endedAt);
  return Number.isNaN(start) || Number.isNaN(end) ? null : end - start;
}
