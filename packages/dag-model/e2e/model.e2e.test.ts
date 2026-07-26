import { expect, test } from "bun:test";

// eslint-disable-next-line @nx/enforce-module-boundaries -- This verifies the package export as a consumer uses it.
import {
  conversationSchema,
  launchProvenanceSchema,
  parseRunDetail,
  parseRunList,
  roundSchema,
  runDetailSchema,
  sessionLinkSchema,
  sseEventNameSchema,
} from "@ai-orchestrator/dag-model";

test("a package consumer validates an API response through the public export", () => {
  expect(
    parseRunList({
      api_version: 1,
      telemetry_schema_version: 6,
      observed_at: "2026-07-26T12:00:00Z",
      runs: [],
    }).runs,
  ).toEqual([]);
});

test("a package consumer rejects incompatible list and detail payloads", () => {
  expect(() =>
    parseRunList({
      api_version: 2,
      telemetry_schema_version: 6,
      observed_at: "2026-07-26T12:00:00Z",
      runs: [],
    }),
  ).toThrow();
  expect(
    runDetailSchema.safeParse({
      api_version: 1,
      telemetry_schema_version: 6,
      observed_at: "2026-07-26T12:00:00Z",
      run: {},
      rounds: [{ node_states: { build: "paused" } }],
      conversations: [],
    }).success,
  ).toBe(false);
});

test("a package consumer accepts a complete run detail", () => {
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
  const parsed = parseRunDetail({
    api_version: 1,
    telemetry_schema_version: 6,
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
      telemetry_quality: "complete",
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
    conversations: [],
  });
  expect(parsed.run.run_id).toBe("run-1");
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
  expect(
    roundSchema.parse({
      run_id: "run-1",
      round: 1,
      plan: {
        tasks: [{ id: "build", task: "Build it" }],
        schema_version: 5,
      },
      node_states: { build: "done" },
      node_results: { build: { status: "done" } },
      attestations: [],
      result: null,
      last_seq: 3,
    }).node_states.build,
  ).toBe("done");
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
