import type { DagNodeState } from "@ai-orchestrator/dag-layout";
import type {
  DagConversation,
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
  readonly conversations: readonly DagConversation[];
}

export function latestRound(detail: RunDetail): Round | undefined {
  return detail.rounds.at(-1);
}

export function nodeViews(detail: RunDetail): NodeView[] {
  const round = latestRound(detail);
  if (!round) return [];
  const telemetry = new Map(detail.run.nodes.map((node) => [node.node, node]));
  const conversations = groupConversationsByNode(detail.conversations);
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
      conversations: conversations.get(task.id) ?? [],
    };
  });
}

/** Bucket the run's flat transcript list by the node each session was labelled with. */
export function groupConversationsByNode(
  conversations: readonly DagConversation[],
): Map<string, DagConversation[]> {
  const grouped = new Map<string, DagConversation[]>();
  for (const item of conversations) {
    const nodeId = item.attribution.nodeId;
    if (nodeId === undefined) continue;
    const existing = grouped.get(nodeId);
    if (existing) existing.push(item);
    else grouped.set(nodeId, [item]);
  }
  return grouped;
}

export function groupRuns(
  runs: readonly RunSummary[],
  details: ReadonlyMap<string, RunDetail>,
): RunGroup[] {
  const groups = new Map<string, RunGroup>();
  for (const run of runs) {
    const attribution = details
      .get(run.run_id)
      ?.conversations.find(
        ({ attribution }) => attribution.launchId,
      )?.attribution;
    const id = attribution?.launchId ?? `unknown:${run.run_id}`;
    const launcher =
      attribution?.launcher === "claude-code"
        ? "Claude"
        : attribution?.launcher === "codex"
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

export function readUnknown(
  value: Record<string, unknown> | undefined,
  ...keys: string[]
): unknown {
  if (!value) return undefined;
  for (const key of keys) {
    if (value[key] !== undefined) return value[key];
  }
  return undefined;
}

function hasLifecycleShape(task: PlanTask): boolean {
  return "repo" in task || "steps" in task;
}

function shortId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}
