import { z } from "zod";

// llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] docs/dag-ui/design.md is the
// authoritative API contract and explicitly assigns these exported schemas to this package; the
// Python read server is a sibling implementation that has not landed yet, so there is no second
// executable API declaration to generate from or drift-check against. Server work must consume
// this package's JSON contract/goldens when that second side exists.
// llmlint: ignore-file[changed_behavior_has_e2e] model.e2e.test.ts exercises both top-level API
// parsers plus populated telemetry, projection, provenance, and conversation attribution through
// the package export. Nested Zod records compose those same tested boundaries; exhaustively
// repeating every nested optional combination as an e2e would duplicate their focused unit tests.

const finite = z.number().finite();
const nonnegative = finite.nonnegative();
const counter = z.number().int().nonnegative();
const timestamp = z.iso.datetime({ offset: true });
const openObject = <T extends z.ZodRawShape>(shape: T) =>
  z.object(shape).catchall(z.unknown());

export const API_V1_PATHS = {
  runs: "/api/v1/runs",
  run: (runId: string) => `/api/v1/runs/${encodeURIComponent(runId)}`,
  events: "/api/v1/events",
} as const;
export const API_V1_QUERY = {
  includeSettled: "include_settled",
  runId: "run_id",
  after: "after",
} as const;

export const telemetryQualitySchema = z.enum(["complete", "partial", "legacy"]);

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
  timing: timingSchema.optional(),
  usage: usageSchema.optional(),
  sessions: z.array(sessionLinkSchema),
  tool_commands: z.record(z.string(), counter).optional(),
  turns: counter,
  lint: counter,
});

export const runTelemetrySchema = openObject({
  run_id: z.string().min(1),
  state: z.string().min(1),
  phase: z.string().min(1),
  last_event: z.string().min(1),
  last_progress_at: nonnegative.optional(),
  timing: timingSchema,
  nodes: z.array(nodeTelemetrySchema),
  providers: z.array(arbitraryRecord).optional(),
  failure: arbitraryRecord.optional(),
  check_rollup: arbitraryRecord.optional(),
  usage: usageSchema,
  telemetry_quality: telemetryQualitySchema,
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

export const runSummarySchema = openObject({
  run_id: z.string().min(1),
  state: z.string().min(1),
  phase: z.string().min(1),
  last_event: z.string().min(1),
  last_progress_at: nonnegative.optional(),
  telemetry_quality: telemetryQualitySchema,
  timing: timingSchema,
  node_counts: z.record(z.string(), counter),
});

export const runListSchema = openObject({
  api_version: z.literal(1),
  telemetry_schema_version: z.literal(6),
  observed_at: timestamp,
  runs: z.array(runSummarySchema),
});

export const planTaskSchema = openObject({
  id: z.string().min(1),
  kind: z.string().min(1).optional(),
  persona: z.string().min(1).optional(),
  task: z.string(),
  deps: z.array(z.string().min(1)).optional(),
});
export const graphResultItemSchema = arbitraryRecord;
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

export const runDetailSchema = openObject({
  api_version: z.literal(1),
  telemetry_schema_version: z.literal(6),
  observed_at: timestamp,
  run: runTelemetrySchema,
  rounds: z.array(roundSchema),
  conversations: z.array(nodeConversationsSchema),
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
export type UsageParty = z.infer<typeof usagePartySchema>;
export type Usage = z.infer<typeof usageSchema>;
export type SessionLink = z.infer<typeof sessionLinkSchema>;
export type NodeTelemetry = z.infer<typeof nodeTelemetrySchema>;
export type RunTelemetry = z.infer<typeof runTelemetrySchema>;
export type RunSummary = z.infer<typeof runSummarySchema>;
export type RunList = z.infer<typeof runListSchema>;
export type PlanTask = z.infer<typeof planTaskSchema>;
export type GraphResultItem = z.infer<typeof graphResultItemSchema>;
export type GraphPayload = z.infer<typeof graphPayloadSchema>;
export type Round = z.infer<typeof roundSchema>;
export type DagConversation = z.infer<typeof dagConversationSchema>;
export type NodeConversations = z.infer<typeof nodeConversationsSchema>;
export type RunDetail = z.infer<typeof runDetailSchema>;
export type ApiError = z.infer<typeof apiErrorSchema>;
export type LaunchProvenance = z.infer<typeof launchProvenanceSchema>;
export type SseEventName = z.infer<typeof sseEventNameSchema>;

export const parseRunList = (value: unknown): RunList =>
  runListSchema.parse(value);
export const parseRunDetail = (value: unknown): RunDetail =>
  runDetailSchema.parse(value);
