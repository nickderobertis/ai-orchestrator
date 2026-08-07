import type {
  AgentRole,
  RunTimeline,
  TimelineEvent,
  TimelineSpan,
  TimelineSpanKind,
} from "@ai-orchestrator/dag-model";
import type {
  TimelineItem,
  TimelineLane,
  TimelineMarker,
} from "@oneharness/ui";

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

/**
 * One onejudge dispatch: the agent session and the judge and lint sessions that
 * supervised it, which the operator reads as a single labelled unit.
 */
export interface DispatchGroup {
  /** The agent session's own span id, which every member of the group shares. */
  readonly id: string;
  /** Operator-facing name of the group, in the order the node dispatched them. */
  readonly label: string;
}

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
  readonly role?: DispatchRole;
  readonly label: string;
  readonly startedAt: string;
  /** `null` for work the recorded stream never closed, and for an instant. */
  readonly endedAt: string | null;
  readonly status?: string;
  readonly durationMs: number | null;
  readonly children: readonly TimelineRow[];
  /** Operator-facing identity; never the free-text transport/session label. */
  readonly displayLabel: string;
  /** Lane vocabulary; a container and an aggregate are named for what they hold. */
  readonly displayKind: string;
  /** The oneharness session this row is, when it is one; absent otherwise. */
  readonly sessionName?: string;
  /** The onejudge dispatch this session belongs to; absent on every other row. */
  readonly dispatch?: DispatchGroup;
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

/**
 * The categories a node's recorded work is read in, one lane each.
 *
 * Every one of them is a word the server already serves — the `agent_role` and
 * `transport_role` of a dispatch, and the span kinds around them — so a reader is
 * never shown a category the journal has no record of.
 */
const LANE_LABELS = {
  worker: "Worker",
  judge: "Judge",
  lint: "Lint",
  orchestrator: "Orchestrator",
  "check-in": "Check-in",
  "pr-author": "PR author",
  verification: "Verification",
  publication: "Publication",
  "lock-waits": "Lock waits",
  "human-wait": "Human wait",
} as const;

export type LaneId = keyof typeof LANE_LABELS;

export const NODE_LANES: readonly TimelineLane[] = Object.entries(
  LANE_LABELS,
).map(([id, label]) => ({ id, label }));

/**
 * Which lane each served span kind belongs in, or `null` for the kinds that hold
 * work rather than being work.
 *
 * Keyed by the contract's own closed span vocabulary, so a kind added to
 * `orchestrator/timeline.py` fails to compile here until it has been given a lane
 * rather than silently landing in whichever one a string comparison reached first.
 */
const LANE_BY_SPAN_KIND: Readonly<Record<TimelineSpanKind, LaneId | null>> = {
  round: null,
  node: null,
  // A lifecycle step brackets the sessions inside it; the transcript names it, and
  // giving it a lane of its own would plot the container over its own contents.
  step: null,
  // Refined by the dispatch's own served roles; `worker` is what an unroled one is.
  dispatch: "worker",
  verification: "verification",
  publication: "publication",
  "pr-drafting": "pr-author",
  // Resolving a conflict is work on the merge, so it reads beside the publication
  // it is unblocking rather than as a category an operator has to learn.
  "conflict-resolution": "publication",
  "human-wait": "human-wait",
  rollup: "lock-waits",
};

/**
 * What one dispatch is read as: the semantic role the server records on it, or the
 * lint transport, which is the worker's own verification told apart from the worker
 * by nothing else.
 */
export type DispatchRole = AgentRole | typeof LLMLINT_TRANSPORT;

export const LLMLINT_TRANSPORT = "llmlint";

/**
 * The lane each of those roles is plotted in.
 *
 * Keyed by the contract's own closed `agentRoleSchema`, so a role added there fails
 * to compile until it has been given a lane — rather than falling through to the
 * dispatch default and being plotted and named "Worker" without anything saying so.
 */
const LANE_BY_ROLE: Readonly<Record<DispatchRole, LaneId>> = {
  worker: "worker",
  judge: "judge",
  llmlint: "lint",
  orchestrator: "orchestrator",
  "check-in": "check-in",
  "pr-author": "pr-author",
};

/**
 * Which of those roles onejudge dispatches in its own right, and which run over an
 * agent's work. Keyed by the same enum for the same reason: a newly served role that
 * matched neither would be left an ungrouped sibling with nothing reporting it.
 */
const OPENS_DISPATCH: Readonly<Record<DispatchRole, boolean>> = {
  worker: true,
  orchestrator: true,
  "check-in": true,
  "pr-author": true,
  judge: false,
  llmlint: false,
};

/**
 * What one dispatch role is called wherever the operator meets it: the lane legend,
 * the transcript's eyebrow, and the header of the conversation it opens.
 *
 * The lane vocabulary above is the one source of those words. A second table of them
 * beside the conversation panel would agree with this one only for as long as nobody
 * renamed a role in one place — and every test would stay green while the plot and
 * the transcript it is read against called the same session two different things.
 */
export function dispatchRoleLabel(role: DispatchRole): string {
  return LANE_LABELS[LANE_BY_ROLE[role]];
}

/** What each aggregated journal kind is called; `rollup` is never a word here. */
const ROLLUP_LABELS: Readonly<Record<string, string>> = {
  "lock-wait": "Lock waits",
};

export interface NodeTimelineV2 {
  readonly items: readonly TimelineItem<TimelineRow>[];
  readonly markers: readonly TimelineMarker<TimelineRow>[];
  readonly lanes: readonly TimelineLane[];
  /** Every row this node recorded, flattened into the order they are read in. */
  readonly rows: readonly TimelineRow[];
}

/**
 * The compact lane answers which activity dominated a moment. Coincident point
 * records cannot all own the same hit target, so retain one deterministically;
 * expanding restores every category in its own lane.
 *
 * Whichever items it drops, the interval the survivors span is the interval the whole
 * set spans: the plotted range is read straight off the items, so dropping the
 * earliest one would move the axis and the time cursor on collapse, and the same
 * moment would sit at two different places depending on which view was open.
 */
export function compactTimelineItems<Payload>(
  items: readonly TimelineItem<Payload>[],
): readonly TimelineItem<Payload>[] {
  const ordered = [...items].sort((left, right) => left.start - right.start);
  const first = ordered.at(0)?.start ?? 0;
  const last = Math.max(
    first + 1,
    ...ordered.map((item) => item.end ?? item.start),
  );
  const boundaries = new Set(
    [
      ordered.at(0),
      ordered.reduce<TimelineItem<Payload> | undefined>(
        (latest, item) =>
          latest === undefined ||
          (item.end ?? item.start) > (latest.end ?? latest.start)
            ? item
            : latest,
        undefined,
      ),
    ]
      .filter((item) => item !== undefined)
      .map(({ id }) => id),
  );
  // A compact point is 20 CSS pixels wide. Treat the nearest 2% of the plotted
  // range as one visual moment so sibling buttons never cover one another at the
  // viewport sizes the application supports.
  const pointCluster = (last - first) * 0.02;
  const result: TimelineItem<Payload>[] = [];
  for (const item of ordered) {
    const itemVisualEnd = Math.max(
      item.end ?? item.start,
      item.start + pointCluster,
    );
    const collision = result.findLast((candidate) => {
      const candidateVisualEnd = Math.max(
        candidate.end ?? candidate.start,
        candidate.start + pointCluster,
      );
      return (
        item.start <= candidateVisualEnd && candidate.start <= itemVisualEnd
      );
    });
    // An item that meets one of the two boundaries neither replaces it nor is
    // dropped by it: both are kept, because losing either end moves the axis and
    // losing the other would silently hide work that really did happen there.
    if (
      collision === undefined ||
      boundaries.has(item.id) ||
      boundaries.has(collision.id)
    ) {
      result.push(item);
    } else if (compactPriority(item) < compactPriority(collision)) {
      result[result.indexOf(collision)] = item;
    }
  }
  return result;
}

function compactPriority(item: TimelineItem<unknown>): number {
  const lane = item.laneId ?? "";
  const order = NODE_LANES.findIndex(({ id }) => id === lane);
  return order < 0 ? NODE_LANES.length : order;
}

/** Keep one clickable journal icon per visual moment, always retaining a deep link. */
export function compactTimelineMarkers<Payload>(
  markers: readonly TimelineMarker<Payload>[],
  items: readonly TimelineItem<Payload>[],
  selectedId?: string,
): readonly TimelineMarker<Payload>[] {
  const times = [
    ...markers.map(({ at }) => at),
    ...items.flatMap((item) => [item.start, item.end ?? item.start]),
  ];
  const first = Math.min(...times);
  const cluster = (Math.max(...times) - first) * 0.02;
  const result: TimelineMarker<Payload>[] = [];
  for (const marker of [...markers].sort((left, right) => left.at - right.at)) {
    const collision = result.findLast(
      (candidate) => marker.at - candidate.at <= cluster,
    );
    if (collision === undefined) result.push(marker);
    else if (marker.id === selectedId)
      result[result.indexOf(collision)] = marker;
  }
  return result;
}

/** Project the served vocabulary into Timeline v2: intervals use lanes; journals use markers. */
export function nodeTimelineV2(
  timeline: RunTimeline | undefined,
  nodeId: string,
): NodeTimelineV2 {
  const projected = nodeTimeline(timeline, nodeId).rows;
  const rows = flattenRows(projected);
  const plottedRows = flattenRows(projected, false);
  const items = plottedRows.flatMap((row): TimelineItem<TimelineRow>[] => {
    const lane = laneId(row);
    if (lane === null) return [];
    const start = Date.parse(row.startedAt);
    const recordedEnd = row.endedAt === null ? null : Date.parse(row.endedAt);
    // An aggregate stands in for thousands of separate waits and carries their total
    // itself, so it is plotted at the length it actually waited. Its recorded
    // start-to-end interval is the window those waits fell in, and plotting that
    // would draw a bar across the whole node for a wait of a few seconds.
    const end =
      row.rowKind === "span" && row.span.total_duration_ms !== undefined
        ? start + row.span.total_duration_ms
        : recordedEnd;
    return [
      {
        id: row.id,
        label: row.displayLabel,
        laneId: lane,
        payload: row,
        start,
        end,
        duration: end === null ? null : end - start,
        status: row.status,
      },
    ];
  });
  const markers = plottedRows.flatMap((row): TimelineMarker<TimelineRow>[] =>
    row.rowKind === "event"
      ? [
          {
            id: row.id,
            label: row.displayLabel,
            at: Date.parse(row.startedAt),
            payload: row,
            status: row.status,
          },
        ]
      : [],
  );
  return { items, markers, lanes: NODE_LANES, rows };
}

function flattenRows(
  rows: readonly TimelineRow[],
  includeGroupChildren = true,
): TimelineRow[] {
  const seen = new Set<string>();
  const result: TimelineRow[] = [];
  const visit = (nested: readonly TimelineRow[]) => {
    for (const row of nested) {
      if (!seen.has(row.id)) {
        seen.add(row.id);
        result.push(row);
      }
      if (includeGroupChildren || row.rowKind !== "group") visit(row.children);
    }
  };
  visit(rows);
  return result;
}

/** The lane a row is plotted in, or `null` for a journal record and a container. */
function laneId(row: TimelineRow): LaneId | null {
  // A journal record is a moment, not an interval: it is a marker over every lane.
  if (row.rowKind === "event") return null;
  const role = roleLane(row.role);
  if (role !== null) return role;
  // Widened for the lookup, not narrowed for it: a group row carries the kind of the
  // spans it stands for as a plain string, and a kind the table has no entry for is
  // an answer here rather than an assertion that it must have one.
  const table: Readonly<Record<string, LaneId | null>> = LANE_BY_SPAN_KIND;
  return table[row.rowKind === "span" ? row.span.kind : row.kind] ?? null;
}

/** A dispatch's own lane, from the roles the server records on it. */
function roleLane(role: DispatchRole | undefined): LaneId | null {
  return role === undefined ? null : (LANE_BY_ROLE[role] ?? null);
}

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
  // Grouping makes alternating worker/judge streams consecutive worker groups; run
  // the density cap again so hundreds of full conversations remain bounded.
  const rows = group(groupDispatches(labelWorkerRetries(group(top))));
  return { span: own, rows, total: count(rows) };
}

/**
 * One span on its own as a row, for a reader that reached it outside a node.
 *
 * The graph-level view opens run-level sessions — the orchestrator's own, and the
 * per-round check-ins — and they are read in the same panel a node's sessions are.
 * Projecting them through the same function is what keeps the two readings identical
 * rather than merely similar.
 */
export function spanAsRow(span: TimelineSpan): TimelineRow {
  return spanRow(span, new Map());
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
    displayKind: displayKind(span, role),
    ...(span.kind === "dispatch" && span.label
      ? { sessionName: span.label }
      : {}),
  };
}

/**
 * A dispatch's role as one word. Lint is the case that needs both halves: it is the
 * worker's own verification, told apart from the worker only by its transport role.
 */
function dispatchRole(span: TimelineSpan): DispatchRole | undefined {
  return span.kind === "dispatch" ? servedRole(span) : undefined;
}

/**
 * The dispatch role a span was *served* with, whatever kind of span it is.
 *
 * A `scope=run` rollup of dispatches carries the same pair the dispatches it stands
 * for carry, because that pair is the category it summarizes — so the graph-level
 * view reads a lane out of one exactly as the node view reads it out of the other.
 */
function servedRole(span: TimelineSpan): DispatchRole | undefined {
  if (span.agent_role === undefined) return undefined;
  return span.transport_role === LLMLINT_TRANSPORT
    ? LLMLINT_TRANSPORT
    : span.agent_role;
}

/**
 * The lane one served span is plotted in, from its roles and the kind vocabulary.
 *
 * A rollup is named for what it summarized rather than for being a rollup, so its
 * `label` is read as that kind — which is what keeps a summarized verification in the
 * verification lane instead of in the aggregate one every rollup would otherwise share.
 */
export function spanLane(span: TimelineSpan): LaneId | null {
  const role = servedRole(span);
  if (role !== undefined) return LANE_BY_ROLE[role] ?? null;
  // Widened for the lookup, not narrowed for it: a rollup's label is a plain string,
  // and a word the table has no entry for is an answer rather than an assertion.
  const table: Readonly<Record<string, LaneId | null>> = LANE_BY_SPAN_KIND;
  return table[span.kind === "rollup" ? span.label : span.kind] ?? null;
}

/** What one lane is called wherever an operator meets it. */
export function laneLabel(lane: LaneId): string {
  return LANE_LABELS[lane];
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

/**
 * What one span is called in the lane legend and the transcript's eyebrow.
 *
 * Every answer is a category an operator reads about; the served identifiers
 * `rollup` and `pr-drafting` never reach the screen as themselves.
 */
function displayKind(
  span: TimelineSpan,
  role: DispatchRole | undefined,
): string {
  if (span.kind === "step") return "Lifecycle";
  if (span.kind === "rollup") return ROLLUP_LABELS[span.label] ?? span.label;
  const lane = roleLane(role) ?? LANE_BY_SPAN_KIND[span.kind];
  return lane === null || lane === undefined ? span.kind : LANE_LABELS[lane];
}

/**
 * How the operator names one recorded activity: its category, then the session or
 * artifact it was. A judge session says Judge and says which session it was, which is
 * the pair a reader needs to tell three concurrent sessions apart.
 */
function spanLabel(span: TimelineSpan, role: DispatchRole | undefined): string {
  if (span.kind === "step")
    return span.label ? `Lifecycle: ${span.label}` : "Lifecycle step";
  const kind = displayKind(span, role);
  if (span.kind === "rollup") return `${kind}: ${span.count ?? 0} recorded`;
  if (!span.label || span.label === kind) return kind;
  // A session is named for the session it is; everything else is named for the
  // artifact or branch it acted on, which reads as a subtitle rather than an alias.
  return span.kind === "dispatch"
    ? `${kind} (${span.label})`
    : `${kind}: ${span.label}`;
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

/**
 * Gather each onejudge dispatch's sessions under the agent session that opened it.
 *
 * One dispatch is an agent conversation plus the judge that supervised it and the
 * lint run it made of its own work — three oneharness sessions an operator has to be
 * able to read as one unit, and which three sibling rows of equal weight hid.
 *
 * The identity comes from what the server records about each session. A lint session
 * is already served nested inside the dispatch it ran under, so it arrives as a child.
 * A judge session is served as a sibling, and the dispatch it belongs to is the agent
 * session it is supervising — the most recent one opened in the same scope, which is
 * exactly the pairing onejudge produces. Schema 10 serves that identity outright as
 * `dispatch_id`; this recovers the same grouping from what schema 9 records.
 */
function groupDispatches(rows: readonly TimelineRow[]): TimelineRow[] {
  const grouped: TimelineRow[] = [];
  let agentIndex = -1;
  let ordinal = 0;
  const joined = (row: TimelineRow, dispatch: DispatchGroup): TimelineRow => ({
    ...row,
    dispatch,
    children: row.children.map((child) => joined(child, dispatch)),
  });
  for (const row of rows) {
    if (opensDispatch(row.role)) {
      ordinal += 1;
      agentIndex = grouped.length;
      grouped.push(joined(row, { id: row.id, label: `Dispatch ${ordinal}` }));
      continue;
    }
    const agent = agentIndex < 0 ? undefined : grouped[agentIndex];
    if (supervises(row.role) && agent?.dispatch !== undefined) {
      grouped[agentIndex] = {
        ...agent,
        children: [...agent.children, joined(row, agent.dispatch)],
      };
      continue;
    }
    grouped.push(row);
  }
  return grouped;
}

/** The roles onejudge dispatches in their own right, each opening a group. */
function opensDispatch(role: DispatchRole | undefined): boolean {
  return role !== undefined && OPENS_DISPATCH[role] === true;
}

/** The roles that run over an agent's work rather than being dispatched alone. */
function supervises(role: DispatchRole | undefined): boolean {
  return role !== undefined && OPENS_DISPATCH[role] === false;
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
