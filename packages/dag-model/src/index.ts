import { z } from "zod";

// llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] docs/dag-ui/design.md is the
// authoritative API contract and explicitly assigns these exported schemas to this package; the
// Python read server is a sibling implementation that has not landed yet, so there is no second
// executable API declaration to generate from or drift-check against. Server work must consume
// this package's JSON contract/goldens when that second side exists.
// llmlint: ignore-file[changed_behavior_has_e2e] model.e2e.test.ts exercises every top-level API
// parser plus populated telemetry, projection, provenance, timeline, and conversation attribution
// through the package export. Nested Zod records compose those same tested boundaries; exhaustively
// repeating every nested optional combination as an e2e would duplicate their focused unit tests.

const finite = z.number().finite();
const nonnegative = finite.nonnegative();
const counter = z.number().int().nonnegative();
const timestamp = z.iso.datetime({ offset: true });
/**
 * The kind of a run's most recent journal event, shared by the list row and the run
 * telemetry that both carry it. Null — never an empty string — for a run that has
 * recorded no event yet, which is how a just-launched run reads on disk.
 */
const lastEvent = z.string().min(1).nullable();
const openObject = <T extends z.ZodRawShape>(shape: T) =>
  z.object(shape).catchall(z.unknown());

export const API_V1_PATHS = {
  runs: "/api/v1/runs",
  run: (runId: string) => `/api/v1/runs/${encodeURIComponent(runId)}`,
  timeline: (runId: string) =>
    `/api/v1/runs/${encodeURIComponent(runId)}/timeline`,
  conversation: (runId: string, conversationId: string) =>
    `/api/v1/runs/${encodeURIComponent(runId)}/conversations/${encodeURIComponent(conversationId)}`,
  events: "/api/v1/events",
} as const;
export const API_V1_QUERY = {
  includeSettled: "include_settled",
  /**
   * Opt out of run-detail transcripts. `false` serves `conversations` as an empty
   * array — an opt-out, not a schema change: the field stays required and present,
   * so `api_version` is untouched and a client reading the timeline instead simply
   * stops refetching megabytes of transcript on every live update.
   */
  includeConversations: "include_conversations",
  runId: "run_id",
  after: "after",
} as const;

export const timingQualitySchema = z.enum(["complete", "partial", "legacy"]);
export const linkageQualitySchema = z.enum(["native", "labelled", "inferred"]);
export const timingPresenceSchema = openObject({
  agent_model_ms: z.boolean(),
  judge_model_ms: z.boolean(),
  llmlint_model_ms: z.boolean(),
  tool_ms: z.boolean(),
});

export const timingSchema = openObject({
  agent_seconds: nonnegative,
  judge_seconds: nonnegative,
  llmlint_seconds: nonnegative,
  gate_seconds: nonnegative,
  publication_wait_seconds: nonnegative,
  lock_wait_seconds: nonnegative,
  setup_seconds: nonnegative,
  scheduling_seconds: nonnegative,
  wall_seconds: nonnegative,
  agent_model_ms: counter,
  judge_model_ms: counter,
  llmlint_model_ms: counter,
  tool_ms: counter,
  idle_orchestration_ms: counter,
  unattributed_ms: counter,
  wall_ms: counter,
  fractions: openObject({
    agent_model: nonnegative,
    judge_model: nonnegative,
    llmlint_model: nonnegative,
    tool: nonnegative,
    idle_orchestration: nonnegative,
    lock_wait: nonnegative,
    setup: nonnegative,
    scheduling: nonnegative,
  }),
});

const usageValue = nonnegative.nullable();
export const usagePartySchema = openObject({
  input_tokens: usageValue,
  output_tokens: usageValue,
  cache_read_tokens: usageValue,
  cache_write_tokens: usageValue,
  cost_usd: usageValue,
});
export const usageSchema = openObject({
  agent: usagePartySchema,
  judge: usagePartySchema,
  llmlint: usagePartySchema,
  total: usagePartySchema,
});

export const sessionLinkSchema = openObject({
  session_id: z.string().min(1),
  history_id: z.string().min(1).nullable().optional(),
  role: z.enum(["agent", "judge", "llmlint"]),
  turn_index: counter.nullable().optional(),
  started_at: timestamp.optional(),
  finished_at: timestamp.nullable().optional(),
});

const arbitraryRecord = z.record(z.string(), z.unknown());

/**
 * How a recorded outcome failed, classified once on the server.
 *
 * `orchestrator/telemetry.py`'s `FailureClass` owns this vocabulary and
 * `scripts/check-dag-state-contract.py` reconciles the three copies of it. It is
 * `class`, not `kind`, because that is the key the Python `Failure.record()` writes.
 */
export const failureClassSchema = z.enum([
  "agent",
  "gate",
  "checks",
  "publication",
  "timeout",
  "provider",
  "configuration",
  "unknown",
]);
export const failureSchema = openObject({
  class: failureClassSchema,
  detail: z.string().optional(),
});

export const nodeTelemetrySchema = openObject({
  node: z.string().min(1),
  status: z.string().min(1),
  outcome: z.string().min(1).optional(),
  branch: z.string().min(1).optional(),
  comparison_remote: z.string().min(1).optional(),
  comparison_base: z.string().min(1).optional(),
  checkpoint: z.string().min(1).optional(),
  commit: z.string().min(1).optional(),
  retry_lineage: arbitraryRecord.optional(),
  gate_attestation: arbitraryRecord.optional(),
  /** How this node's own outcome failed; omitted for a node that did not fail. */
  failure: failureSchema.optional(),
  timing: timingSchema.optional(),
  usage: usageSchema.optional(),
  sessions: z.array(sessionLinkSchema),
  tool_commands: z.record(z.string(), counter).optional(),
  turns: counter,
  lint: counter,
  timing_quality: timingQualitySchema,
  linkage_quality: linkageQualitySchema,
  timing_presence: timingPresenceSchema,
});

export const runTelemetrySchema = openObject({
  run_id: z.string().min(1),
  state: z.string().min(1),
  phase: z.string().min(1),
  last_event: lastEvent,
  last_progress_at: nonnegative.optional(),
  timing: timingSchema,
  nodes: z.array(nodeTelemetrySchema),
  providers: z.array(arbitraryRecord).optional(),
  failure: failureSchema.optional(),
  check_rollup: arbitraryRecord.optional(),
  usage: usageSchema,
  timing_quality: timingQualitySchema,
  linkage_quality: linkageQualitySchema,
  timing_presence: timingPresenceSchema,
  sources: z.array(z.string()),
  node_work_ms: openObject({
    agent_model_ms: counter,
    judge_model_ms: counter,
    llmlint_model_ms: counter,
    tool_ms: counter,
    wall_ms: counter,
  }),
  turns: counter,
  lint: counter,
});

/**
 * A run's join to its launching session, served on both the list row and the detail.
 *
 * It is omitted for a run that recorded no `launch_id`, and `launcher_session_id`
 * appears only when the server is configured to expose it — so a consumer that wants
 * to group runs by the session that launched them reads it from the list itself,
 * without fetching a single run's transcripts to recover the same join.
 */
export const runLaunchSchema = openObject({
  launch_id: z.string().min(1),
  launcher: z.enum(["claude-code", "codex", "unknown"]),
  launcher_session_id: z.string().min(1).optional(),
});

export const runSummarySchema = openObject({
  run_id: z.string().min(1),
  state: z.string().min(1),
  phase: z.string().min(1),
  last_event: lastEvent,
  last_progress_at: nonnegative.optional(),
  timing_quality: timingQualitySchema,
  linkage_quality: linkageQualitySchema,
  timing: timingSchema,
  node_counts: z.record(z.string(), counter),
  launch: runLaunchSchema.optional(),
});

export const runListSchema = openObject({
  api_version: z.literal(1),
  telemetry_schema_version: z.literal(8),
  observed_at: timestamp,
  runs: z.array(runSummarySchema),
});

const planStepSchema = openObject({
  id: z.string().min(1),
  kind: z.enum(["agent", "human"]).optional(),
  persona: z.string().min(1).optional(),
  task: z.string().min(1),
  deps: z.array(z.string().min(1)).optional(),
  done_when: z.string().min(1).optional(),
  max_turns: counter.positive().optional(),
  expects_no_diff: z.boolean().optional(),
});
export const planTaskSchema = planStepSchema.extend({
  repo: z.string().min(1).optional(),
  steps: z.array(planStepSchema).min(1).optional(),
  session: z.string().min(1).optional(),
  project_dir: z.string().min(1).optional(),
  base_branch: z.string().min(1).optional(),
  branch: z.string().min(1).optional(),
  title: z.string().min(1).optional(),
  verify_cmd: z.string().min(1).optional(),
  skip_verify: z.boolean().optional(),
  verify_via_ci: z.boolean().optional(),
  merge_policy: z.enum(["auto", "direct", "none"]).optional(),
  workflow: z.enum(["local", "remote"]).optional(),
  repo_type: z.enum(["single-owner", "team"]).optional(),
  execution_checkout: z.string().min(1).optional(),
  stack_bases: z.array(z.string().min(1)).optional(),
  resume: z.boolean().optional(),
});
const artifactPathsSchema = openObject({
  gate_log: z.string().optional(),
  worker_report: z.string().optional(),
  oneharness_session: z.string().optional(),
});
const stepResultSchema = openObject({
  id: z.string().min(1),
  kind: z.string().min(1),
  persona: z.string().nullable(),
  status: z.string().min(1),
  telemetry: arbitraryRecord.optional(),
  artifacts: artifactPathsSchema.optional(),
});
const humanActionSchema = openObject({
  ref: z.string().min(1),
  task: z.string(),
  unblocks: z.array(z.string()),
  unblocks_publication: z.boolean(),
});
const resumeSchema = openObject({
  branch: z.string().min(1),
  base_branch: z.string().min(1),
  pr_base: z.string(),
  checkpoint: z.string().min(1),
  completed_steps: z.array(z.string()),
  pr: z.string().nullable(),
  mode: z.string().optional(),
  source_round: counter.optional(),
});
export const graphResultItemSchema = openObject({
  kind: z.string().optional(),
  status: z.string().optional(),
  task: z.string().optional(),
  unblocks: z.array(z.string()).optional(),
  blocked_by: z.array(z.string()).optional(),
  human_actions: z.array(humanActionSchema).optional(),
  completed: z.boolean().optional(),
  exit_code: z.number().int().nullable().optional(),
  verdicts: z.array(z.unknown()).optional(),
  usage: arbitraryRecord.optional(),
  telemetry: arbitraryRecord.optional(),
  repo: z.string().optional(),
  branch: z.string().optional(),
  base_branch: z.string().optional(),
  pr_base: z.string().optional(),
  synthetic_stack_base: z.string().nullable().optional(),
  stack_bases: z.array(arbitraryRecord).optional(),
  repository_type: z.enum(["single-owner", "team"]).nullable().optional(),
  repo_type: z.enum(["single-owner", "team"]).nullable().optional(),
  publication_workflow: z.enum(["local", "remote"]).nullable().optional(),
  workflow: z.enum(["local", "remote"]).nullable().optional(),
  merge_policy: z.enum(["auto", "direct", "none"]).nullable().optional(),
  outcome: z.string().optional(),
  ok: z.boolean().optional(),
  pr: z.string().nullable().optional(),
  detail: z.string().optional(),
  follow_ups: z.string().nullable().optional(),
  steps: z.array(stepResultSchema).optional(),
  waiting_steps: z.array(z.string()).optional(),
  resume: resumeSchema.nullable().optional(),
  error: z.string().nullable().optional(),
  retry_lineage: arbitraryRecord.optional(),
  deferred_cleanup: z.array(z.string()).optional(),
  artifacts: artifactPathsSchema.optional(),
});
export const graphPayloadSchema = openObject({
  ok: z.boolean().optional(),
  state: z.string().optional(),
  started_order: z.array(z.string()).optional(),
  results: z.record(z.string(), graphResultItemSchema).optional(),
  schema_version: counter.optional(),
  round: counter.optional(),
});
export const nodeStateSchema = z.enum([
  "running",
  "done",
  "failed",
  "waiting",
  "cancelled",
]);
/**
 * The one authoritative per-node status, owned by `orchestrator.projection.NodeStatus`
 * and reconciled with it, `@ai-orchestrator/dag-layout`, and `docs/dag-ui/design.md`
 * by `scripts/check-dag-state-contract.py`.
 *
 * `nodeStateSchema` above is the strict journal fold and is a subset of this: it can
 * only speak for nodes the journal recorded something about, so `pending`, `blocked`
 * and `skipped` appear only here. A consumer renders from `Round.node_status` and
 * never from an absent `node_states` entry — inferring one is how the sidebar and the
 * detail view came to disagree about the same node.
 */
export const nodeStatusSchema = z.enum([
  "pending",
  "running",
  "waiting",
  "blocked",
  "skipped",
  "done",
  "not-completed",
  "failed",
  "cancelled",
  "unknown",
]);
export const roundSchema = openObject({
  run_id: z.string().min(1),
  round: counter,
  plan: openObject({
    tasks: z.array(planTaskSchema),
    schema_version: counter.optional(),
    concurrency: counter.positive().optional(),
    name: z.string().min(1).optional(),
  }),
  node_states: z.record(z.string(), nodeStateSchema),
  /** One entry per plan task, so a client never invents a status for a node. */
  node_status: z.record(z.string(), nodeStatusSchema),
  /**
   * The plan node ids gating each `blocked` or `skipped` node, in plan order; every
   * other node is absent. Not `GraphResultItem.blocked_by`, which names human action
   * refs on a settled result.
   */
  node_gated_by: z.record(z.string(), z.array(z.string().min(1))),
  node_results: z.record(z.string(), graphResultItemSchema),
  attestations: z.array(z.string()),
  result: graphPayloadSchema.nullable(),
  last_seq: counter,
});

export const conversationUsageSchema = openObject({
  cacheReadTokens: usageValue.optional(),
  cacheWriteTokens: usageValue.optional(),
  costUsd: usageValue.optional(),
  inputTokens: usageValue.optional(),
  outputTokens: usageValue.optional(),
});
export const conversationToolEventSchema = openObject({
  index: counter,
  input: z.unknown().optional(),
  kind: z.string(),
  name: z.string().nullable().optional(),
  output: z.string().nullable().optional(),
});
export const conversationTurnSchema = openObject({
  assistant: z.string().nullable(),
  failureKind: z.string().nullable(),
  harness: z.string(),
  id: z.string(),
  model: z.string().nullable(),
  reasoning: z.string().nullable(),
  status: z.string(),
  timestamp,
  tools: z.array(conversationToolEventSchema),
  unknown: arbitraryRecord,
  usage: conversationUsageSchema,
  user: z.string(),
});
export const conversationSchema = openObject({
  canContinue: z.boolean(),
  harnesses: z.array(z.string()),
  id: z.string().min(1),
  name: z.string(),
  project: z.string(),
  startedAt: timestamp,
  state: z.string(),
  turns: z.array(conversationTurnSchema),
});
export const agentRoleSchema = z.enum([
  "orchestrator",
  "worker",
  "judge",
  "check-in",
  "pr-author",
]);
export const dagConversationSchema = openObject({
  conversation: conversationSchema,
  attribution: openObject({
    runId: z.string().optional(),
    round: counter.optional(),
    nodeId: z.string().optional(),
    stepId: z.string().optional(),
    launchId: z.string().optional(),
    launcher: z.enum(["claude-code", "codex", "unknown"]).optional(),
    transportRole: z.enum(["agent", "judge", "llmlint"]),
    agentRole: agentRoleSchema,
    parentConversationId: z.string().optional(),
    persona: z.string().optional(),
    finishedAt: timestamp.nullable().optional(),
    inferred: z.literal(true).optional(),
    timing: timingSchema.optional(),
  }),
});
export const nodeConversationsSchema = openObject({
  node: z.string().optional(),
  conversations: z.array(dagConversationSchema),
});

/**
 * `RunDetail.conversations`, accepting both recorded shapes and yielding one.
 *
 * `docs/dag-ui/design.md` fixes the served shape as a flat `DagConversation[]`, and
 * that is what `orchestrator/read_model.py` returns — each transcript carries its own
 * `attribution.nodeId`, so a consumer that wants them per node groups by it. Payloads
 * that group transcripts under `nodeConversationsSchema` entries stay valid and are
 * flattened into the same list, so a producer or recorded fixture written against
 * that shape keeps parsing.
 */
export const runConversationsSchema = z.union([
  z.array(dagConversationSchema),
  z
    .array(nodeConversationsSchema)
    .transform((groups) => groups.flatMap((group) => group.conversations)),
]);

export const runDetailSchema = openObject({
  api_version: z.literal(1),
  telemetry_schema_version: z.literal(8),
  observed_at: timestamp,
  run: runTelemetrySchema,
  rounds: z.array(roundSchema),
  conversations: runConversationsSchema,
  launch: runLaunchSchema.optional(),
});

export const timelineReferenceKindSchema = z.enum([
  "conversation",
  "gate_log",
  "worker_report",
  "oneharness_session",
  "pr",
]);
export const timelineSpanKindSchema = z.enum([
  "round",
  "node",
  "step",
  "dispatch",
  "verification",
  "publication",
  "pr-drafting",
  "conflict-resolution",
  "human-wait",
  "rollup",
]);
/**
 * Where one timeline item's heavy content lives. The payload never inlines a
 * transcript, a gate log, or a report body, so a consumer fetches only what it opens.
 */
export const timelineReferenceSchema = openObject({
  kind: timelineReferenceKindSchema,
  value: z.string().min(1),
});
/**
 * `kind` is an open string on purpose: it is the journal event kind that produced
 * the item, or `conversation-turn` for a turn, and the journal owns that vocabulary.
 */
export const timelineEventSchema = openObject({
  id: z.string().min(1),
  kind: z.string().min(1),
  at: timestamp,
  node_id: z.string().min(1).optional(),
  step_id: z.string().min(1).optional(),
  round: counter.optional(),
  status: z.string().min(1).optional(),
  reference: timelineReferenceSchema.optional(),
});
/**
 * One interval of recorded work. `ended_at` is null for work the recorded stream
 * never closed — an in-flight run, not an error — and `parent_id` links spans into
 * the tree the recorded nesting implies. `count` and `total_duration_ms` appear only
 * on a `rollup` span, which stands in for thousands of high-frequency records.
 */
export const timelineSpanSchema = openObject({
  id: z.string().min(1),
  kind: timelineSpanKindSchema,
  label: z.string(),
  started_at: timestamp,
  ended_at: timestamp.nullable(),
  events: z.array(timelineEventSchema),
  parent_id: z.string().min(1).optional(),
  node_id: z.string().min(1).optional(),
  step_id: z.string().min(1).optional(),
  round: counter.optional(),
  status: z.string().min(1).optional(),
  count: counter.optional(),
  total_duration_ms: counter.optional(),
  reference: timelineReferenceSchema.optional(),
});
export const runTimelineSchema = openObject({
  api_version: z.literal(1),
  observed_at: timestamp,
  run_id: z.string().min(1),
  spans: z.array(timelineSpanSchema),
});

export const apiErrorSchema = openObject({
  error: openObject({ code: z.string(), message: z.string() }),
});

export const launchProvenanceSchema = openObject({
  schema_version: z.literal(1),
  launch_id: z.string().min(1),
  launcher: z.enum(["claude-code", "codex"]),
  launcher_session_id: z.string().min(1),
  started_at: timestamp,
  repository_identity: z.string().min(1),
});

export const sseEventNameSchema = z.enum([
  "snapshot",
  "run.changed",
  "conversation.changed",
  "run.removed",
]);
export const sseEventDataSchema = arbitraryRecord;

export type Timing = z.infer<typeof timingSchema>;
export type FailureClass = z.infer<typeof failureClassSchema>;
export type Failure = z.infer<typeof failureSchema>;
export type NodeState = z.infer<typeof nodeStateSchema>;
export type NodeStatus = z.infer<typeof nodeStatusSchema>;
export type UsageParty = z.infer<typeof usagePartySchema>;
export type Usage = z.infer<typeof usageSchema>;
export type SessionLink = z.infer<typeof sessionLinkSchema>;
export type NodeTelemetry = z.infer<typeof nodeTelemetrySchema>;
export type RunTelemetry = z.infer<typeof runTelemetrySchema>;
export type RunLaunch = z.infer<typeof runLaunchSchema>;
export type RunSummary = z.infer<typeof runSummarySchema>;
export type RunList = z.infer<typeof runListSchema>;
export type PlanTask = z.infer<typeof planTaskSchema>;
export type GraphResultItem = z.infer<typeof graphResultItemSchema>;
export type GraphPayload = z.infer<typeof graphPayloadSchema>;
export type Round = z.infer<typeof roundSchema>;
export type DagConversation = z.infer<typeof dagConversationSchema>;
export type NodeConversations = z.infer<typeof nodeConversationsSchema>;
export type RunConversations = z.infer<typeof runConversationsSchema>;
export type RunDetail = z.infer<typeof runDetailSchema>;
export type TimelineReferenceKind = z.infer<typeof timelineReferenceKindSchema>;
export type TimelineSpanKind = z.infer<typeof timelineSpanKindSchema>;
export type TimelineReference = z.infer<typeof timelineReferenceSchema>;
export type TimelineEvent = z.infer<typeof timelineEventSchema>;
export type TimelineSpan = z.infer<typeof timelineSpanSchema>;
export type RunTimeline = z.infer<typeof runTimelineSchema>;
export type ApiError = z.infer<typeof apiErrorSchema>;
export type LaunchProvenance = z.infer<typeof launchProvenanceSchema>;
export type SseEventName = z.infer<typeof sseEventNameSchema>;

export const parseRunList = (value: unknown): RunList =>
  runListSchema.parse(value);
export const parseRunDetail = (value: unknown): RunDetail =>
  runDetailSchema.parse(value);
export const parseRunTimeline = (value: unknown): RunTimeline =>
  runTimelineSchema.parse(value);
