import { DAG_NODE_STATES } from "@ai-orchestrator/dag-layout";
import type {
  Failure,
  GraphResultItem,
  NodeDetail,
  NodeStatus,
  NodeTelemetry,
  PlanTask,
  Round,
  RunDetail,
  RunLaunch,
  RunSummary,
} from "@ai-orchestrator/dag-model";

/** How a launching harness is named wherever this app names one. */
export type LauncherName = "Claude" | "Codex" | "Unattributed";

export interface RunGroup {
  readonly id: string;
  readonly label: string;
  readonly launcher: LauncherName;
  readonly runs: readonly RunSummary[];
}

export interface NodeView {
  readonly id: string;
  readonly label: string;
  readonly kind: "agent" | "human" | "lifecycle";
  /** The authoritative `Round.node_status`, rendered without client-side defaults. */
  readonly status: NodeStatus;
  readonly task: PlanTask;
  readonly telemetry?: NodeTelemetry;
  readonly result?: GraphResultItem;
  readonly detail?: NodeDetail;
  /** How this node failed, when it did; served typed rather than parsed out of prose. */
  readonly failure?: Failure;
  /**
   * Everything holding this node up, in the order a reader should be told it: the
   * plan nodes gating it, then the human action refs its settled result named.
   * Empty for a node nothing is holding.
   */
  readonly blockers: readonly string[];
}

export function latestRound(detail: RunDetail): Round | undefined {
  return detail.rounds.at(-1);
}

export function nodeViews(detail: RunDetail): NodeView[] {
  const round = latestRound(detail);
  if (!round) return [];
  const telemetry = new Map(detail.run.nodes.map((node) => [node.node, node]));
  return round.plan.tasks.flatMap((task) => {
    const rawKind = readString(task, "kind");
    const kind =
      rawKind === "human"
        ? "human"
        : hasLifecycleShape(task)
          ? "lifecycle"
          : "agent";
    // `node_results` holds only what a *terminal journal event* carried, so it is
    // empty for every node the scheduler settled without dispatching. A round that
    // finished also recorded a whole-graph result, and for those nodes it is the only
    // record there is — the one that carries what blocked them.
    const result =
      round.node_results[task.id] ?? round.result?.results?.[task.id];
    const status = round.node_status[task.id];
    // The server excludes a run it cannot fold into authoritative node statuses.
    // Stay defensive if an older server violates that invariant: omit the unusable
    // task instead of inventing a state for it or taking down the remaining graph.
    if (status === undefined) return [];
    return [
      {
        id: task.id,
        label: readString(task, "name") ?? task.id,
        status,
        kind,
        task,
        telemetry: telemetry.get(task.id),
        result,
        detail: detail.node_details[task.id],
        failure: telemetry.get(task.id)?.failure,
        blockers: [
          ...(round.node_gated_by[task.id] ?? []),
          ...(result?.blocked_by ?? []),
        ],
      },
    ];
  });
}

/** The harness that launched a run, named the one way this app names it. */
export function launcherName(launch?: RunLaunch): LauncherName {
  switch (launch?.launcher) {
    case "claude-code":
      return "Claude";
    case "codex":
      return "Codex";
    default:
      return "Unattributed";
  }
}

/**
 * How one run's launch is named on screen — in the sidebar heading and beside a
 * run-level transcript alike, so the two never disagree about the same run.
 *
 * There are three honest answers, and none of them is "unknown session". A run
 * whose launching session is named reads as that session. A run that recorded a
 * launch but nothing that can name its session — every run launched before the
 * launcher was detected, once its short-lived provenance record has gone — reads as
 * the launch it does know. A run with no launch record at all is unattributed, which
 * is what an e2e fixture and a bare `run-plan` genuinely are.
 */
export function launchLabel(launch?: RunLaunch): string {
  const name = launcherName(launch);
  if (launch?.session_key !== undefined) {
    return `${name} session · ${shortId(launch.session_key)}`;
  }
  return launch === undefined
    ? "Unattributed"
    : `${name} launch · ${shortId(launch.launch_id)}`;
}

/**
 * The one-line reason a node is not making progress, or `undefined` when it is.
 *
 * Shared by the graph card and the node detail banner so the short line under a card
 * and the headline of the view it opens cannot say different things.
 */
export function nodeReason(node: NodeView): string | undefined {
  if (node.blockers.length > 0 && REASON_IS_A_BLOCKER.has(node.status)) {
    return `blocked by ${node.blockers.join(", ")}`;
  }
  if (!OWN_WORK_LOST.has(node.status)) return undefined;
  return recordedReason(node) ?? `${node.status}, with no reason recorded`;
}

/**
 * What the run itself recorded about a lost outcome, or `undefined` when it recorded
 * nothing — the classified failure detail first, then the lifecycle's own prose, the
 * dispatch's error, and last the outcome word, which is a classification rather than
 * a sentence and so is the least it can say.
 *
 * One chain, read by the card's line and the node view's banner alike: two orders
 * would let a card and the view it opens explain the same failure differently.
 */
export function recordedReason(node: NodeView): string | undefined {
  const recorded =
    node.failure?.detail ||
    node.result?.detail ||
    node.result?.error ||
    node.telemetry?.outcome ||
    node.result?.outcome;
  return recorded?.trim() || undefined;
}

/**
 * Statuses whose reason is named in `blockers` rather than in the node's own record.
 *
 * They are three different conditions: `blocked` is held behind a dependency,
 * `skipped` is terminal because a prerequisite did not complete, and `waiting` is
 * journaled on the node itself. All this set decides is where the sentence comes
 * from — a waiting node's own result names the human action it waits for, exactly as
 * a blocked node's names what holds it, so one line reads all three.
 */
const REASON_IS_A_BLOCKER: ReadonlySet<NodeStatus> = new Set<NodeStatus>([
  "blocked",
  "skipped",
  "waiting",
]);
/** Statuses that mean this node's own work ran, or was cut short, without finishing. */
const OWN_WORK_LOST: ReadonlySet<NodeStatus> = new Set<NodeStatus>([
  "failed",
  "not-completed",
  "cancelled",
]);

/**
 * A run row's own nodes, counted by status: `2 done · 1 blocked`.
 *
 * `RunSummary.node_counts` is counted on the server over the same derivation the
 * graph renders, so this line and the cards it opens cannot describe different
 * graphs — which is the disagreement an operator saw between the two. Ordered by the
 * contract's own vocabulary so the words hold their places between polls; a count the
 * vocabulary does not know is still shown, after them, rather than dropped.
 */
export function nodeCountSummary(
  counts: Readonly<Record<string, number>>,
): string {
  const known: readonly string[] = DAG_NODE_STATES;
  const positive = (name: string) => (counts[name] ?? 0) > 0;
  return [
    ...known.filter(positive),
    ...Object.keys(counts)
      .filter((name) => !known.includes(name))
      .sort()
      .filter(positive),
  ]
    .map((name) => `${counts[name]} ${name}`)
    .join(" · ");
}

/** Whether a node's status is one an operator has to act on, banner and all. */
export function isUnhealthy(status: NodeStatus): boolean {
  return (
    OWN_WORK_LOST.has(status) || status === "blocked" || status === "skipped"
  );
}

/**
 * Gather the listed runs under the session that launched each one.
 *
 * The join is served on the list row itself, so this needs nothing but the list: a
 * run whose transcripts have been swept, and a run whose detail has not been read
 * because it is not the one selected, both still group under their own launcher.
 *
 * Grouping is by *session*, not by launch: one planner session launches many runs
 * and mints a fresh `launch_id` for each, so keying on the launch id put every run
 * in a group of its own and told an operator nothing. A run whose session cannot be
 * named still gets a group to itself rather than being pooled with unrelated runs
 * under one bucket that would falsely claim they share a planner.
 */
export function groupRuns(runs: readonly RunSummary[]): RunGroup[] {
  const groups = new Map<string, RunGroup>();
  for (const run of runs) {
    const key = run.launch?.session_key;
    const id =
      key !== undefined
        ? `session:${run.launch?.launcher}:${key}`
        : `run:${run.run_id}`;
    const existing = groups.get(id);
    groups.set(id, {
      id,
      launcher: launcherName(run.launch),
      label: launchLabel(run.launch),
      runs: [...(existing?.runs ?? []), run],
    });
  }
  return [...groups.values()];
}

export function readString(
  value: Record<string, unknown>,
  key: string,
): string | undefined {
  const result = value[key];
  return typeof result === "string" && result.length > 0 ? result : undefined;
}

function hasLifecycleShape(task: PlanTask): boolean {
  return "repo" in task || "steps" in task;
}

function shortId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}
