import type { DagNodeState } from "@ai-orchestrator/dag-layout";
import type {
  GraphResultItem,
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
