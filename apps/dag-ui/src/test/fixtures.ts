/**
 * Read-API payloads shaped exactly as `@ai-orchestrator/dag-model` validates them.
 *
 * These stand in for the network, never for the telemetry client: the unit tests
 * hand them to a real `TelemetryClient` over a doubled `fetch`, so every field here
 * is parsed by the shipped schemas. `fixtures.test.ts` asserts that, and the browser
 * journeys prove the same views against the real server.
 */

export const LIVE_RUN = "dag-ui-live";
export const HISTORY_RUN = "dag-ui-history";

const timing = {
  agent_seconds: 1,
  judge_seconds: 1,
  llmlint_seconds: 0,
  gate_seconds: 2,
  publication_wait_seconds: 0,
  lock_wait_seconds: 0,
  setup_seconds: 1,
  scheduling_seconds: 0,
  wall_seconds: 5,
  agent_model_ms: 1000,
  judge_model_ms: 1000,
  llmlint_model_ms: 0,
  tool_ms: 1000,
  idle_orchestration_ms: 1000,
  unattributed_ms: 0,
  wall_ms: 5000,
  fractions: {
    agent_model: 0.2,
    judge_model: 0.2,
    llmlint_model: 0,
    tool: 0.2,
    idle_orchestration: 0.2,
    lock_wait: 0,
    setup: 0.2,
    scheduling: 0,
  },
};

const party = {
  input_tokens: null,
  output_tokens: null,
  cache_read_tokens: null,
  cache_write_tokens: null,
  cost_usd: null,
};

const timingPresence = {
  agent_model_ms: true,
  judge_model_ms: true,
  llmlint_model_ms: false,
  tool_ms: true,
};

export const runList = {
  api_version: 1,
  telemetry_schema_version: 7,
  observed_at: "2026-07-26T12:00:00Z",
  runs: [
    summary(LIVE_RUN, "running", { running: 1, done: 1, failed: 1 }),
    summary(HISTORY_RUN, "complete", { done: 1 }),
  ],
};

export function runDetail(runId: string = LIVE_RUN) {
  const historical = runId === HISTORY_RUN;
  const tasks = historical
    ? [
        {
          id: "archive",
          task: "Archive the release",
          done_when: "Archive exists",
        },
      ]
    : [
        {
          id: "foundation",
          task: "Prepare shared contracts",
          done_when: "Contract tests pass",
          repo: "local/example",
        },
        {
          id: "dashboard",
          deps: ["foundation"],
          task: "Build the live dashboard",
          done_when: "Users can inspect transcripts",
        },
        {
          id: "publish",
          deps: ["dashboard"],
          task: "Publish the dashboard",
          done_when: "The release is reachable",
        },
        {
          id: "approval",
          kind: "human",
          deps: ["publish"],
          task: "Wait for release approval",
        },
        {
          id: "queued",
          deps: ["approval"],
          task: "Start queued follow-up",
          done_when: "Follow-up starts",
        },
        {
          id: "obsolete",
          task: "Retire obsolete work",
          done_when: "Work is cancelled",
        },
      ];
  const states: Record<string, string> = historical
    ? { archive: "done" }
    : {
        foundation: "done",
        dashboard: "running",
        publish: "failed",
        approval: "waiting",
        obsolete: "cancelled",
      };
  const launchId = historical ? "c1a0".repeat(8) : "c0de".repeat(8);
  const launcher = historical ? "claude-code" : "codex";
  const node = historical ? "archive" : "dashboard";
  return {
    api_version: 1,
    telemetry_schema_version: 7,
    observed_at: "2026-07-26T12:00:00Z",
    run: {
      run_id: runId,
      state: historical ? "complete" : "running",
      phase: historical ? "complete" : "agent",
      last_event: historical ? "round-finished" : "node-started",
      timing,
      nodes: tasks
        .filter(({ id }) => states[id] !== undefined)
        .map(({ id }) => ({
          node: id,
          status: states[id] ?? "cancelled",
          sessions: [],
          turns: id === "dashboard" ? 2 : 1,
          lint: 0,
          timing_quality: "complete",
          linkage_quality: "labelled",
          timing_presence: timingPresence,
          ...(id === "foundation"
            ? { gate_attestation: { command: ["just", "gate"] } }
            : {}),
        })),
      usage: { agent: party, judge: party, llmlint: party, total: party },
      timing_quality: "complete",
      linkage_quality: "labelled",
      timing_presence: timingPresence,
      sources: ["oneharness"],
      node_work_ms: {
        agent_model_ms: 1000,
        judge_model_ms: 1000,
        llmlint_model_ms: 0,
        tool_ms: 1000,
        wall_ms: 5000,
      },
      turns: 4,
      lint: 0,
    },
    rounds: [
      {
        run_id: runId,
        round: 1,
        plan: { tasks },
        node_states: states,
        node_results: historical
          ? { archive: { status: "done", ok: true } }
          : {
              foundation: {
                status: "done",
                ok: true,
                pr: "https://github.com/example/repo/pull/12",
                detail: "Gate completed successfully",
              },
              publish: { status: "failed", ok: false, detail: "Deploy failed" },
            },
        attestations: [],
        result: null,
        last_seq: 7,
      },
    ],
    conversations: [
      conversation(
        "worker-session",
        "worker",
        "agent",
        node,
        launchId,
        launcher,
        "Implementing the dashboard now",
      ),
      conversation(
        "judge-session",
        "judge",
        "judge",
        node,
        launchId,
        launcher,
        "The transcript is accessible",
      ),
      conversation(
        "check-in-session",
        "check-in",
        "agent",
        node,
        launchId,
        launcher,
        "Progress update sent",
      ),
      conversation(
        "pr-author-session",
        "pr-author",
        "agent",
        node,
        launchId,
        launcher,
        "Drafted the pull request",
      ),
      conversation(
        "llmlint-session",
        "worker",
        "llmlint",
        node,
        launchId,
        launcher,
        "Reviewed the changed behavior",
      ),
      conversation(
        "orchestrator-session",
        "orchestrator",
        "agent",
        undefined,
        launchId,
        launcher,
        "Coordinating the execution frontier",
      ),
    ],
  };
}

function summary(
  runId: string,
  state: string,
  nodeCounts: Record<string, number>,
) {
  return {
    run_id: runId,
    state,
    phase: state === "complete" ? "complete" : "agent",
    last_event: state === "complete" ? "round-finished" : "node-started",
    timing_quality: "complete",
    linkage_quality: "labelled",
    timing,
    node_counts: nodeCounts,
  };
}

function conversation(
  id: string,
  agentRole: string,
  transportRole: string,
  nodeId: string | undefined,
  launchId: string,
  launcher: string,
  text: string,
) {
  return {
    conversation: {
      canContinue: false,
      harnesses: ["codex"],
      id,
      name: `${agentRole} conversation`,
      project: "ai-orchestrator",
      startedAt: "2026-07-26T11:00:00Z",
      state: "completed",
      turns: [
        {
          assistant: text,
          failureKind: null,
          harness: "codex",
          id: `${id}-0`,
          model: "gpt-5",
          reasoning: null,
          status: "completed",
          timestamp: "2026-07-26T11:00:00Z",
          tools: [],
          unknown: {},
          usage: {},
          user: `Act as ${agentRole}`,
        },
      ],
    },
    attribution: {
      transportRole,
      agentRole,
      launcher,
      launchId,
      persona: agentRole === "pr-author" ? "pr-author" : "engineer",
      ...(nodeId === undefined ? {} : { nodeId }),
    },
  };
}
