import { DAG_NODE_STATES } from "@ai-orchestrator/dag-layout";
import type {
  Failure,
  GraphResultItem,
  NodeStatus,
  NodeTelemetry,
  PlanTask,
  Round,
  RunDetail,
  RunSummary,
} from "@ai-orchestrator/dag-model";

export interface RunGroup {
  readonly id: string;
  readonly label: string;
  readonly launcher: "Claude" | "Codex" | "Unknown";
  readonly runs: readonly RunSummary[];
}

export interface NodeView {
  readonly id: string;
  readonly label: string;
  readonly kind: "agent" | "human" | "lifecycle";
  /**
   * The served `Round.node_status`, unmodified.
   *
   * Every surface — the graph card, the accessible list, the node detail badge —
   * reads this one field. Nothing here derives, defaults, or renames it: the server
   * decides a node's status once, and a client that filled in a gap of its own is
   * how the sidebar came to call a node blocked while its detail said running.
   */
  readonly status: NodeStatus;
  readonly task: PlanTask;
  readonly telemetry?: NodeTelemetry;
  readonly result?: GraphResultItem;
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
  return round.plan.tasks.map((task) => {
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
    return {
      id: task.id,
      label: readString(task, "name") ?? task.id,
      // The server populates one entry per plan task, so this normally cannot miss.
      // The fallback is the contract's own word for a status it cannot represent —
      // `node_status` is a record, so a payload that dropped a key would parse — and
      // never a state invented here: reporting "unknown" is what stops a surface
      // quietly disagreeing with the one beside it, which is the defect this replaced.
      status: round.node_status[task.id] ?? "unknown",
      kind,
      task,
      telemetry: telemetry.get(task.id),
      result,
      failure: telemetry.get(task.id)?.failure,
      blockers: [
        ...(round.node_gated_by[task.id] ?? []),
        ...(result?.blocked_by ?? []),
      ],
    };
  });
}

/**
 * The one-line reason a node is not making progress, or `undefined` when it is.
 *
 * Shared by the graph card and the node detail banner so the short line under a card
 * and the headline of the view it opens cannot say different things.
 */
export function nodeReason(node: NodeView): string | undefined {
  if (node.blockers.length > 0 && DECIDED_BY_DEPENDENCIES.has(node.status)) {
    return `blocked by ${node.blockers.join(", ")}`;
  }
  if (!OWN_WORK_LOST.has(node.status)) return undefined;
  const recorded =
    node.failure?.detail ||
    node.result?.detail ||
    node.result?.error ||
    node.telemetry?.outcome ||
    node.result?.outcome;
  return recorded?.trim() || `${node.status}, with no reason recorded`;
}

/**
 * Statuses a node holds because of *other* nodes, never because of its own run.
 *
 * These three are not one condition: `waiting` and `blocked` move when a person acts,
 * while `skipped` is terminal — its prerequisite did not complete, so it will never
 * run. What they share, and all this set decides, is that the reason lives somewhere
 * else and is named in `blockers`, so one sentence reads all three.
 */
const DECIDED_BY_DEPENDENCIES: ReadonlySet<NodeStatus> = new Set<NodeStatus>([
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
 */
export function groupRuns(runs: readonly RunSummary[]): RunGroup[] {
  const groups = new Map<string, RunGroup>();
  for (const run of runs) {
    // A run that recorded no launch id gets a group of its own rather than sharing
    // one unknown bucket with every other unattributed run.
    const id = run.launch?.launch_id ?? `unknown:${run.run_id}`;
    const launcher =
      run.launch?.launcher === "claude-code"
        ? "Claude"
        : run.launch?.launcher === "codex"
          ? "Codex"
          : "Unknown";
    const existing = groups.get(id);
    groups.set(id, {
      id,
      launcher,
      label: `${launcher} session · ${shortId(id)}`,
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
