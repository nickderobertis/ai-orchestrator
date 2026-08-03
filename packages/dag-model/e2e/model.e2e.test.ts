import { expect, test } from "bun:test";

// eslint-disable-next-line @nx/enforce-module-boundaries -- This verifies the package export as a consumer uses it.
import {
  conversationSchema,
  dagConversationSchema,
  launchProvenanceSchema,
  nodeConversationsSchema,
  nodeTelemetrySchema,
  parseRunDetail,
  parseRunList,
  parseRunTimeline,
  roundSchema,
  runConversationsSchema,
  runDetailSchema,
  runSummarySchema,
  sessionLinkSchema,
  sseEventNameSchema,
} from "@ai-orchestrator/dag-model";

const zeroTiming = {
  agent_seconds: 0,
  judge_seconds: 0,
  llmlint_seconds: 0,
  gate_seconds: 0,
  publication_wait_seconds: 0,
  lock_wait_seconds: 0,
  setup_seconds: 0,
  scheduling_seconds: 0,
  wall_seconds: 0,
  agent_model_ms: 0,
  judge_model_ms: 0,
  llmlint_model_ms: 0,
  tool_ms: 0,
  idle_orchestration_ms: 0,
  unattributed_ms: 0,
  wall_ms: 0,
  fractions: {
    agent_model: 0,
    judge_model: 0,
    llmlint_model: 0,
    tool: 0,
    idle_orchestration: 0,
    lock_wait: 0,
    setup: 0,
    scheduling: 0,
  },
};

test("a package consumer validates an API response through the public export", () => {
  expect(
    parseRunList({
      api_version: 2,
      telemetry_schema_version: 9,
      observed_at: "2026-07-26T12:00:00Z",
      runs: [],
    }).runs,
  ).toEqual([]);
});

test("the checked-in v2 run-detail contract parses and v1 is rejected", async () => {
  const golden = await Bun.file(
    new URL("../../../tests/golden/run-detail-v2.json", import.meta.url),
  ).json();
  const parsed = parseRunDetail(golden);
  expect(parsed.rounds[0]?.node_status.release).toBe("blocked");
  expect(parsed.rounds[0]?.node_gated_by.release).toEqual(["approve"]);
  expect(() => parseRunDetail({ ...golden, api_version: 1 })).toThrow();
});

test("the goal id the read boundary derives is what makes a legacy run parse", async () => {
  // Python derives this fixture's `plan.goal.id`; the run behind it recorded text
  // alone. Without that id the contract rejects the whole detail.
  const golden = await Bun.file(
    new URL("../../../tests/golden/run-detail-v2.json", import.meta.url),
  ).json();
  expect(parseRunDetail(golden).rounds[0]?.plan.goal).toEqual({
    id: "Ship-the-gated-release",
    text: "Ship the gated release",
  });

  const [round, ...rest] = golden.rounds;
  const legacy = {
    ...golden,
    rounds: [
      {
        ...round,
        plan: { ...round.plan, goal: { text: round.plan.goal.text } },
      },
      ...rest,
    ],
  };
  expect(() => parseRunDetail(legacy)).toThrow();
});

test("a package consumer rejects incompatible list and detail payloads", () => {
  expect(() =>
    parseRunList({
      api_version: 3,
      telemetry_schema_version: 9,
      observed_at: "2026-07-26T12:00:00Z",
      runs: [],
    }),
  ).toThrow();
  expect(
    runDetailSchema.safeParse({
      api_version: 2,
      telemetry_schema_version: 9,
      observed_at: "2026-07-26T12:00:00Z",
      run: {},
      rounds: [{ node_states: { build: "paused" } }],
      conversations: [],
    }).success,
  ).toBe(false);
});

const TRANSCRIPT = {
  conversation: {
    canContinue: false,
    harnesses: ["codex"],
    id: "worker-session",
    name: "engineer-build",
    project: "repo",
    startedAt: "2026-07-26T12:00:00Z",
    state: "completed",
    turns: [],
  },
  attribution: {
    runId: "run-1",
    nodeId: "build",
    transportRole: "agent",
    agentRole: "worker",
  },
};

/** A complete `RunDetail` whose transcripts are supplied in the shape under test. */
function completeDetail(conversations: unknown[]) {
  const usageParty = {
    input_tokens: null,
    output_tokens: null,
    cache_read_tokens: null,
    cache_write_tokens: null,
    cost_usd: null,
  };
  const timing = {
    agent_seconds: 0,
    judge_seconds: 0,
    llmlint_seconds: 0,
    gate_seconds: 0,
    publication_wait_seconds: 0,
    lock_wait_seconds: 0,
    setup_seconds: 0,
    scheduling_seconds: 0,
    wall_seconds: 0,
    agent_model_ms: 0,
    judge_model_ms: 0,
    llmlint_model_ms: 0,
    tool_ms: 0,
    idle_orchestration_ms: 0,
    unattributed_ms: 0,
    wall_ms: 0,
    fractions: {
      agent_model: 0,
      judge_model: 0,
      llmlint_model: 0,
      tool: 0,
      idle_orchestration: 0,
      lock_wait: 0,
      setup: 0,
      scheduling: 0,
    },
  };
  return {
    api_version: 2,
    telemetry_schema_version: 9,
    observed_at: "2026-07-26T12:00:00Z",
    run: {
      run_id: "run-1",
      state: "running",
      phase: "agent",
      last_event: "node-started",
      timing,
      nodes: [],
      usage: {
        agent: usageParty,
        judge: usageParty,
        llmlint: usageParty,
        total: usageParty,
      },
      timing_quality: "complete",
      linkage_quality: "native",
      timing_presence: {
        agent_model_ms: false,
        judge_model_ms: false,
        llmlint_model_ms: false,
        tool_ms: false,
      },
      sources: [],
      node_work_ms: {
        agent_model_ms: 0,
        judge_model_ms: 0,
        llmlint_model_ms: 0,
        tool_ms: 0,
        wall_ms: 0,
      },
      turns: 0,
      lint: 0,
    },
    rounds: [],
    conversations,
  };
}

test("a package consumer accepts a complete run detail", () => {
  // The read API serves one flat list of transcripts, each carrying its own node
  // locator, exactly as `docs/dag-ui/design.md` fixes `RunDetail.conversations`.
  const parsed = parseRunDetail(completeDetail([TRANSCRIPT]));
  expect(parsed.run.run_id).toBe("run-1");
  expect(parsed.conversations[0]?.attribution.nodeId).toBe("build");
});

test("a package consumer accepts a run detail grouped by node", () => {
  // A payload written against the grouped shape stays valid and reads as the same
  // list, so one consumer handles both without knowing which it was handed.
  const parsed = parseRunDetail(
    completeDetail([{ node: "build", conversations: [TRANSCRIPT] }]),
  );
  expect(parsed.conversations).toEqual([TRANSCRIPT]);
});

test("a package consumer reads both recorded conversation shapes as one list", () => {
  // What the read API serves: one flat list, each entry carrying its own locator.
  expect(runConversationsSchema.parse([TRANSCRIPT])).toEqual([TRANSCRIPT]);
  // What a payload grouped under `nodeConversationsSchema` carries: still valid, and
  // flattened to the same list so one consumer handles both.
  expect(
    runConversationsSchema.parse([
      { node: "build", conversations: [TRANSCRIPT] },
      { conversations: [] },
    ]),
  ).toEqual([TRANSCRIPT]);
  // The grouped entry keeps validating on its own, for a consumer holding just one.
  expect(
    nodeConversationsSchema.parse({
      node: "build",
      conversations: [TRANSCRIPT],
    }).node,
  ).toBe("build");
  expect(() => runConversationsSchema.parse([{ node: "build" }])).toThrow();
  expect(() => nodeConversationsSchema.parse({ node: "build" })).toThrow();
});

test("a package consumer validates provenance, SSE names, and counters", () => {
  expect(
    launchProvenanceSchema.parse({
      schema_version: 1,
      launch_id: "launch",
      launcher: "codex",
      launcher_session_id: "session",
      started_at: "2026-07-26T12:00:00Z",
      repository_identity: "local/repo",
    }).launcher,
  ).toBe("codex");
  expect(sseEventNameSchema.parse("run.changed")).toBe("run.changed");
  expect(sseEventNameSchema.parse("activity.changed")).toBe("activity.changed");
  expect(() => sseEventNameSchema.parse("run.created")).toThrow();
  expect(() =>
    sessionLinkSchema.parse({
      session_id: "session",
      role: "agent",
      turn_index: -1,
    }),
  ).toThrow();
});

test("a package consumer validates rounds and conversations", () => {
  const round = roundSchema.parse({
    run_id: "run-1",
    round: 1,
    plan: {
      tasks: [
        { id: "build", task: "Build it" },
        { id: "ship", task: "Ship it", deps: ["build"] },
        { id: "announce", task: "Announce it", deps: ["ship"] },
      ],
      schema_version: 5,
    },
    node_states: { build: "done", ship: "waiting" },
    // Served for every plan task, including the one the journal never recorded.
    node_status: { build: "done", ship: "waiting", announce: "blocked" },
    node_gated_by: { announce: ["ship"] },
    node_results: { build: { status: "done" } },
    attestations: [],
    result: null,
    last_seq: 3,
  });
  expect(round.node_states.build).toBe("done");
  expect(round.node_status.announce).toBe("blocked");
  expect(round.node_gated_by.announce).toEqual(["ship"]);
  const conversation = {
    canContinue: false,
    harnesses: ["codex"],
    id: "conversation-1",
    name: "Worker",
    project: "repo",
    startedAt: "2026-07-26T12:00:00Z",
    state: "completed",
    turns: [],
  };
  expect(conversationSchema.parse(conversation).id).toBe("conversation-1");
  expect(() =>
    conversationSchema.parse({ ...conversation, startedAt: "yesterday" }),
  ).toThrow();
});

test("a package consumer validates populated telemetry and attribution", () => {
  expect(
    runSummarySchema.parse({
      run_id: "run-1",
      state: "running",
      phase: "agent",
      last_event: "node-started",
      timing_quality: "complete",
      linkage_quality: "native",
      timing: zeroTiming,
      node_counts: { running: 1 },
    }).node_counts.running,
  ).toBe(1);
  expect(
    nodeTelemetrySchema.parse({
      node: "build",
      status: "running",
      sessions: [{ session_id: "worker", role: "agent" }],
      turns: 1,
      lint: 0,
      timing_quality: "complete",
      linkage_quality: "native",
      timing_presence: {
        agent_model_ms: true,
        judge_model_ms: false,
        llmlint_model_ms: false,
        tool_ms: true,
      },
    }).sessions[0]?.session_id,
  ).toBe("worker");
  expect(
    dagConversationSchema.parse({
      conversation: {
        canContinue: false,
        harnesses: ["codex"],
        id: "conversation-1",
        name: "Worker",
        project: "repo",
        startedAt: "2026-07-26T12:00:00Z",
        state: "completed",
        turns: [],
      },
      attribution: {
        runId: "run-1",
        nodeId: "build",
        launcher: "codex",
        transportRole: "agent",
        agentRole: "worker",
      },
    }).attribution.nodeId,
  ).toBe("build");
});

test("a package consumer parses a served run timeline through the export", () => {
  const timeline = parseRunTimeline({
    api_version: 2,
    observed_at: "2026-07-26T12:00:00Z",
    run_id: "run-1",
    spans: [
      {
        id: "round-1",
        kind: "round",
        label: "round 1",
        started_at: "2026-07-26T12:00:00Z",
        ended_at: null,
        events: [],
      },
      {
        id: "dispatch-worker-1",
        kind: "dispatch",
        label: "engineer-build",
        started_at: "2026-07-26T12:00:01Z",
        ended_at: "2026-07-26T12:04:00Z",
        parent_id: "round-1",
        node_id: "build",
        round: 1,
        status: "completed",
        reference: { kind: "conversation", value: "worker-1" },
        events: [
          {
            id: "worker-1-0",
            kind: "conversation-turn",
            at: "2026-07-26T12:00:01Z",
            status: "completed",
            reference: { kind: "conversation", value: "worker-1" },
          },
        ],
      },
    ],
  });
  expect(timeline.spans[0]?.ended_at).toBeNull();
  expect(timeline.spans[1]?.events[0]?.reference?.kind).toBe("conversation");
});
