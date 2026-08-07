import { z } from "zod";

// llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] docs/dag-ui/design.md is the
// authoritative API contract and explicitly assigns these exported schemas to this package; the
// Python read server emits the same contract from TypedDicts that cannot consume Zod directly;
// scripts/check-dag-state-contract.py therefore reconciles their fields and closed vocabularies.
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

// Const assertions preserve route/query literals for consumers; widening these
// shared constants to `string` would discard the closed transport vocabulary.
export const API_V2_PATHS = {
  runs: "/api/v2/runs",
  run: (runId: string) => `/api/v2/runs/${encodeURIComponent(runId)}`,
  timeline: (runId: string) =>
    `/api/v2/runs/${encodeURIComponent(runId)}/timeline`,
  conversation: (runId: string, conversationId: string) =>
    `/api/v2/runs/${encodeURIComponent(runId)}/conversations/${encodeURIComponent(conversationId)}`,
  artifact: (runId: string, artifactId: string) =>
    `/api/v2/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}`,
  events: "/api/v2/events",
} as const;
export const API_V2_QUERY = {
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
  cursor: "cursor",
  limit: "limit",
  nodeId: "node_id",
  scope: "scope",
} as const;
export const API_V2_TIMELINE_SCOPES = {
  run: "run",
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

/**
 * The two role vocabularies, declared once here because three payloads carry them:
 * a conversation's attribution, a node's session links, and a timeline dispatch
 * span. `transportRole` is the party oneharness recorded; `agentRole` is what the
 * dispatch was for.
 */
export const agentRoleSchema = z.enum([
  "orchestrator",
  "worker",
  "judge",
  "check-in",
  "pr-author",
]);
export const transportRoleSchema = z.enum(["agent", "judge", "llmlint"]);

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

/**
 * One session that did a node's work: the transport party in `role`, and the
 * semantic role beside it in `agent_role` so a client can label and group sessions
 * without fetching a transcript for each one.
 */
export const sessionLinkSchema = openObject({
  session_id: z.string().min(1),
  history_id: z.string().min(1).nullable().optional(),
  role: transportRoleSchema,
  agent_role: agentRoleSchema.optional(),
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
/**
 * Which side of onejudge's two-party conversation the provider refused.
 *
 * `orchestrator/provider_failure.py`'s `ConversationSide` owns this vocabulary and
 * `scripts/check-dag-state-contract.py` reconciles the three copies of it. A
 * planner reading "quota" needs it first: the two sides prefer different
 * identities, so a fix aimed at the wrong one changes nothing.
 */
export const conversationSideSchema = z.enum(["agent", "judge", "llmlint"]);

/**
 * Why the provider refused, closed so a client can switch on it exhaustively.
 *
 * `orchestrator/provider_failure.py`'s `ProviderFailureCause` owns this vocabulary and
 * `scripts/check-dag-state-contract.py` reconciles the three copies of it.
 * `quota_at_launch` fell through to the next identity in the chain;
 * `quota_mid_conversation` could not, because the conversation was already bound
 * to the identity that refused it.
 */
export const providerFailureCauseSchema = z.enum([
  "quota_at_launch",
  "quota_mid_conversation",
  "stale_session_resume",
  "rate_limit",
  "harness_exit",
]);

/**
 * A provider refusal, as served on a failure record.
 *
 * `orchestrator/provider_failure.py`'s `ProviderFailure` owns this shape and
 * `scripts/check-dag-state-contract.py` reconciles all three copies of it. Every
 * field is optional: only a failure that reached a provider carries any of them,
 * and the evidence a harness gives varies.
 */
export const providerFailureSchema = openObject({
  side: conversationSideSchema.optional(),
  harness: z.string().optional(),
  variant: z.string().optional(),
  identity: z.string().optional(),
  cause: providerFailureCauseSchema.optional(),
  raw_tail: z.string().optional(),
  reset_time: z.string().optional(),
  missing_session_id: z.string().optional(),
  wait_seconds: z.number().nonnegative().optional(),
  failure_kind: z.string().optional(),
  structured_error: arbitraryRecord.optional(),
  judge_unrecorded: z.boolean().optional(),
});

/** The classification, plus whatever a provider refusal recorded beside it. */
export const failureSchema = providerFailureSchema.extend({
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
 * A read-only provider capacity snapshot, served beside a run list or detail.
 *
 * Every configured identity is present whether or not its probe answered — one that
 * did not carries `availability.state = "unknown"` rather than being dropped, so a
 * client can never mistake an unprobed identity for one that is not configured. The
 * per-identity shape is upstream oneharness's and is deliberately not restated here.
 */
export const providerHealthSchema = openObject({
  schema_version: z.union([z.string(), z.number()]).optional(),
  observed_at: z.string().optional(),
  identities: z.array(arbitraryRecord),
});

/**
 * A run's attribution to its launching session, on both the list row and the detail.
 *
 * It is omitted for a run that recorded no `launch_id`. `session_key` is the opaque,
 * stable, irreversible name of the launching session and is served by default, so a
 * consumer groups runs by the planner that launched them from the list itself —
 * without fetching a single run's transcripts, and without the raw
 * `launcher_session_id`, which appears only when the server is configured to expose
 * it. A run that named no session (a plain shell, or a record predating the key) has
 * no `session_key`.
 */
export const runLaunchSchema = openObject({
  launch_id: z.string().min(1),
  launcher: z.enum(["claude-code", "codex", "unknown"]),
  session_key: z.string().min(1).optional(),
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
  api_version: z.literal(2),
  telemetry_schema_version: z.literal(10),
  observed_at: timestamp,
  runs: z.array(runSummarySchema),
  next_cursor: z.string().min(1).optional(),
  provider_health: providerHealthSchema.optional(),
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
/**
 * Where a preserved workstream is picked back up, as the plan records it for the
 * round that continues it. Never a boolean: the field this schema types has always
 * carried `orchestrator.lifecycle.Resume`'s own metadata, and typing it `boolean`
 * is what made every replanned run fail whole-detail validation in the browser.
 * `scripts/check-dag-state-contract.py` reconciles it with
 * `orchestrator.lifecycle.RESUME_FIELDS` and `docs/dag-ui/design.md`.
 *
 * Only the four fields that locate the work are required. A journal written before
 * a later field existed omits it — `completed_steps` and `pr` are both absent from
 * recorded rounds — so requiring them here would sever exactly the history this
 * contract exists to read.
 */
export const planTaskResumeSchema = openObject({
  branch: z.string().min(1),
  base_branch: z.string().min(1),
  pr_base: z.string().min(1),
  checkpoint: z.string().min(1),
  completed_steps: z.array(z.string()).optional(),
  pr: z.string().nullable().optional(),
  mode: z.enum(["pause", "retry"]).optional(),
  source_round: counter.positive().optional(),
  attempts: counter.optional(),
});
/**
 * One anchor a stacked plan node bases on. A *mapping*, never a bare branch name:
 * `orchestrator.lifecycle.STACK_BASE_FIELDS` is its one source and refuses anything
 * else, so a stacked run would have failed whole-detail validation the same way a
 * replanned one did.
 */
export const stackBaseSchema = openObject({
  branch: z.string().min(1),
  repo: z.string().min(1).optional(),
  identity: z.string().min(1).optional(),
  base_branch: z.string().min(1).optional(),
  pr: z.string().min(1).optional(),
  pr_base: z.string().min(1).optional(),
});
/**
 * One top-level plan node. `task` is optional because one legal node shape has never
 * had it: a lifecycle node that delegates to `steps` carries its prose on each step
 * instead, and so carries no `persona` either. The refinement below holds every other
 * shape to non-empty prose, so an agent or human node that lost its task is still a
 * contract violation rather than a silently blank node view.
 */
export const planTaskSchema = planStepSchema
  .extend({
    task: z.string().min(1).optional(),
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
    stack_bases: z.array(stackBaseSchema).optional(),
    resume: planTaskResumeSchema.optional(),
  })
  .superRefine((task, context) => {
    if (task.steps === undefined && task.task === undefined) {
      context.addIssue({
        code: "custom",
        path: ["task"],
        message: "a plan task without `steps` must carry its own `task` prose",
      });
    }
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
  commit: z.string().optional(),
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
  "parked",
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
  "parked",
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
    goal: openObject({
      id: z.string().min(1),
      text: z.string().min(1),
    }).optional(),
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
}).superRefine((round, context) => {
  const taskIds = new Set(round.plan.tasks.map((task) => task.id));
  const statusIds = new Set(Object.keys(round.node_status));
  if (
    taskIds.size !== round.plan.tasks.length ||
    taskIds.size !== statusIds.size ||
    [...taskIds].some((taskId) => !statusIds.has(taskId))
  ) {
    context.addIssue({
      code: "custom",
      path: ["node_status"],
      message: "must contain exactly one entry for every plan task",
    });
  }
  const invalidGate = Object.entries(round.node_gated_by).find(
    ([nodeId, blockers]) =>
      !taskIds.has(nodeId) || blockers.some((blocker) => !taskIds.has(blocker)),
  );
  if (invalidGate) {
    context.addIssue({
      code: "custom",
      path: ["node_gated_by"],
      message: "must name only nodes in this round's plan",
    });
  }
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
/**
 * `timestamp` is when the record was *written*, which is when the turn finished.
 * The five optional timing fields are the turn's own measurements, present exactly
 * when the history record carried them: a harness that measures no wall interval
 * records `startedAt`/`finishedAt` as null and only a `durationMs`.
 */
export const conversationTurnSchema = openObject({
  assistant: z.string().nullable(),
  durationMs: usageValue.optional(),
  failureKind: z.string().nullable(),
  finishedAt: timestamp.nullable().optional(),
  harness: z.string(),
  id: z.string(),
  model: z.string().nullable(),
  modelMs: usageValue.optional(),
  reasoning: z.string().nullable(),
  startedAt: timestamp.nullable().optional(),
  status: z.string(),
  timestamp,
  toolMs: usageValue.optional(),
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
export const dagConversationSchema = openObject({
  conversation: conversationSchema,
  attribution: openObject({
    runId: z.string().optional(),
    round: counter.optional(),
    nodeId: z.string().optional(),
    stepId: z.string().optional(),
    launchId: z.string().optional(),
    launcher: z.enum(["claude-code", "codex", "unknown"]).optional(),
    transportRole: transportRoleSchema,
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

const verificationCheckSchema = openObject({
  name: z.string(),
  state: z.string(),
  required: z.boolean(),
  url: z.string().url().optional(),
});
const verificationRecordSchema = openObject({
  ok: z.boolean(),
  output_tail: z.string(),
  artifact_id: z.string().min(1).optional(),
});
export const nodeDetailSchema = openObject({
  verification: openObject({
    pre_push_hook: z.boolean().optional(),
    required_checks: z.array(z.string()).optional(),
    required_checks_status: z.string().optional(),
    expected_gate: z.array(z.string()).optional(),
    checks: z.array(verificationCheckSchema).optional(),
    records: z.array(verificationRecordSchema),
  }),
  publication: openObject({
    pr_url: z.string().url().optional(),
    branch: z.string().optional(),
    branch_url: z.string().url().optional(),
    merged: z.boolean(),
    base_branch: z.string().optional(),
    commit: z.string().optional(),
    commit_url: z.string().url().optional(),
  }).optional(),
});
export const runDetailSchema = openObject({
  api_version: z.literal(2),
  telemetry_schema_version: z.literal(10),
  observed_at: timestamp,
  run: runTelemetrySchema,
  rounds: z.array(roundSchema),
  conversations: runConversationsSchema,
  node_details: z.record(z.string(), nodeDetailSchema).optional().default({}),
  launch: runLaunchSchema.optional(),
  provider_health: providerHealthSchema.optional(),
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
 * Which part of its loop the launched orchestrator is in. Derived by the server from
 * what the run itself recorded — never asserted by the agent, which cannot report that
 * it has stopped talking.
 */
export const supervisoryPhaseSchema = z.enum([
  "starting",
  "driving-round",
  "executing-run-plan",
  "reviewing-results",
  "surfacing",
  "finished",
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
 * One discrete wait a `rollup` span absorbed, so a client can draw the stalls a run
 * actually took instead of one bar across the whole contention window.
 */
export const timelineIntervalSchema = openObject({
  started_at: timestamp,
  ended_at: timestamp,
});
/**
 * One interval of recorded work. `ended_at` is null for work the recorded stream
 * never closed — an in-flight run, not an error — and `parent_id` links spans into
 * the tree the recorded nesting implies. `count`, `total_duration_ms` and
 * `intervals` appear only on a `rollup` span, which stands in for thousands of
 * high-frequency records, and the role pair and `dispatch_id` — the key that groups
 * the several oneharness sessions of one onejudge dispatch — only on a `dispatch` one.
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
  intervals: z.array(timelineIntervalSchema).optional(),
  agent_role: agentRoleSchema.optional(),
  transport_role: transportRoleSchema.optional(),
  dispatch_id: z.string().min(1).optional(),
  reference: timelineReferenceSchema.optional(),
  detail: openObject({
    ok: z.boolean().optional(),
    output_tail: z.string().optional(),
    artifact_id: z.string().optional(),
  }).optional(),
  phase: supervisoryPhaseSchema.optional(),
});
export const artifactContentSchema = openObject({
  id: z.string().min(1),
  kind: timelineReferenceKindSchema,
  content: z.string(),
  truncated: z.boolean(),
});
export const runTimelineSchema = openObject({
  api_version: z.literal(2),
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
  "activity.changed",
  "run.removed",
]);
export const sseEventDataSchema = arbitraryRecord;

export interface LiveActivity {
  round: string;
  node: string;
  at: number;
  kind: string;
  name: string;
  detail: string;
  events: number;
}
export const liveActivitySchema: z.ZodType<LiveActivity> = z.object({
  round: z.string().min(1),
  node: z.string().min(1),
  at: z.number().finite(),
  kind: z.string(),
  name: z.string(),
  detail: z.string(),
  events: z.number().int().nonnegative(),
});
export const liveActivityListSchema = z.array(liveActivitySchema);

export type Timing = z.infer<typeof timingSchema>;
export type FailureClass = z.infer<typeof failureClassSchema>;
export type Failure = z.infer<typeof failureSchema>;
export type NodeState = z.infer<typeof nodeStateSchema>;
export type NodeStatus = z.infer<typeof nodeStatusSchema>;
/**
 * The two closed role vocabularies a dispatch is served with. Exported so a consumer
 * can key a table on them rather than restating their members as strings — which is
 * what makes a role added here fail to compile there instead of falling through.
 */
export type AgentRole = z.infer<typeof agentRoleSchema>;
export type TransportRole = z.infer<typeof transportRoleSchema>;
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
export type NodeDetail = z.infer<typeof nodeDetailSchema>;
export type ArtifactContent = z.infer<typeof artifactContentSchema>;
export type TimelineReferenceKind = z.infer<typeof timelineReferenceKindSchema>;
export type TimelineSpanKind = z.infer<typeof timelineSpanKindSchema>;
export type SupervisoryPhase = z.infer<typeof supervisoryPhaseSchema>;
export type TimelineReference = z.infer<typeof timelineReferenceSchema>;
export type TimelineEvent = z.infer<typeof timelineEventSchema>;
export type TimelineInterval = z.infer<typeof timelineIntervalSchema>;
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
