import type { DagNodeState } from "@ai-orchestrator/dag-layout";
import type {
  GraphResultItem,
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
  readonly state: DagNodeState;
  readonly task: PlanTask;
  readonly telemetry?: NodeTelemetry;
  readonly result?: GraphResultItem;
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
    return {
      id: task.id,
      label: readString(task, "name") ?? task.id,
      // A node the round never started has no projected state; it is still pending.
      state: round.node_states[task.id] ?? "pending",
      kind,
      task,
      telemetry: telemetry.get(task.id),
      result: round.node_results[task.id],
    };
  });
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
