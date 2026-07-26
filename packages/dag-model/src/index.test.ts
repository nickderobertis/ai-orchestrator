import { describe, expect, test } from "bun:test";

import {
  graphPayloadSchema,
  graphResultItemSchema,
  parseRunList,
  planTaskSchema,
  runDetailSchema,
  sessionLinkSchema,
} from "./index.js";

const timing = {
  agent_seconds: 1,
  judge_seconds: 0,
  llmlint_seconds: 0,
  gate_seconds: 0,
  publication_wait_seconds: 0,
  lock_wait_seconds: 0,
  setup_seconds: 0,
  scheduling_seconds: 0,
  wall_seconds: 1,
  agent_model_ms: 1000,
  judge_model_ms: 0,
  llmlint_model_ms: 0,
  tool_ms: 0,
  idle_orchestration_ms: 0,
  unattributed_ms: 0,
  wall_ms: 1000,
  fractions: {
    agent_model: 1,
    judge_model: 0,
    llmlint_model: 0,
    tool: 0,
    idle_orchestration: 0,
    lock_wait: 0,
    setup: 0,
    scheduling: 0,
  },
};

test("validates and preserves additive run-list fields", () => {
  const parsed = parseRunList({
    api_version: 1,
    telemetry_schema_version: 6,
    observed_at: "2026-07-26T12:00:00Z",
    extension: true,
    runs: [
      {
        run_id: "run-1",
        state: "running",
        phase: "agent",
        last_event: "node-started",
        telemetry_quality: "complete",
        timing,
        node_counts: { running: 1 },
      },
    ],
  });
  expect(parsed.extension).toBe(true);
});

describe("boundary failures", () => {
  test("rejects incompatible API versions and negative counters", () => {
    expect(() =>
      parseRunList({
        api_version: 2,
        telemetry_schema_version: 6,
        observed_at: "2026-07-26T12:00:00Z",
        runs: [],
      }),
    ).toThrow();
    expect(() =>
      sessionLinkSchema.parse({
        session_id: "session",
        role: "agent",
        turn_index: -1,
      }),
    ).toThrow();
  });

  test("rejects a detail with an unsupported projected state", () => {
    const result = runDetailSchema.safeParse({
      api_version: 1,
      telemetry_schema_version: 6,
      observed_at: "2026-07-26T12:00:00Z",
      run: {},
      rounds: [{ node_states: { build: "paused" } }],
      conversations: [],
    });
    expect(result.success).toBe(false);
  });

  test("rejects malformed nested plan and result payloads", () => {
    expect(() =>
      planTaskSchema.parse({
        id: "release",
        task: "Release",
        steps: [{ id: "approve", kind: "human", task: 42 }],
      }),
    ).toThrow();
    expect(() =>
      graphResultItemSchema.parse({
        status: "done",
        steps: [{ id: "build", kind: "agent", persona: null }],
      }),
    ).toThrow();
    expect(() =>
      graphPayloadSchema.parse({
        ok: true,
        results: { build: { deferred_cleanup: "not-a-list" } },
      }),
    ).toThrow();
  });
});
