import { describe, expect, test } from "bun:test";

import {
  graphPayloadSchema,
  graphResultItemSchema,
  parseRunList,
  parseRunTimeline,
  planTaskSchema,
  runDetailSchema,
  runSummarySchema,
  runTelemetrySchema,
  sessionLinkSchema,
  timelineReferenceSchema,
  timelineSpanSchema,
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

const usageParty = {
  input_tokens: null,
  output_tokens: null,
  cache_read_tokens: null,
  cache_write_tokens: null,
  cost_usd: null,
};

test("validates and preserves additive run-list fields", () => {
  const parsed = parseRunList({
    api_version: 1,
    telemetry_schema_version: 9,
    observed_at: "2026-07-26T12:00:00Z",
    extension: true,
    runs: [
      {
        run_id: "run-1",
        state: "running",
        phase: "agent",
        last_event: "node-started",
        timing_quality: "complete",
        linkage_quality: "native",
        timing,
        node_counts: { running: 1 },
      },
    ],
  });
  expect(parsed.extension).toBe(true);
});

test("reads the launching session off the list row it is served on", () => {
  const row = {
    run_id: "run-1",
    state: "running",
    phase: "agent",
    last_event: "node-started",
    timing_quality: "complete",
    linkage_quality: "native",
    timing,
    node_counts: { running: 1 },
  };
  // The join is served on the row itself, so grouping runs by their launching
  // session never has to fetch a run's transcripts to recover the same answer.
  const parsed = parseRunList({
    api_version: 1,
    telemetry_schema_version: 9,
    observed_at: "2026-07-26T12:00:00Z",
    runs: [
      { ...row, launch: { launch_id: "c0de".repeat(8), launcher: "codex" } },
    ],
  });
  expect(parsed.runs[0]?.launch?.launcher).toBe("codex");
  // A run that recorded no launch id is served without the join at all.
  expect(runSummarySchema.parse(row).launch).toBeUndefined();
  // The launcher vocabulary is closed: an unrecognized one is a contract failure,
  // not a run silently grouped under a launcher the server never named.
  expect(
    runSummarySchema.safeParse({
      ...row,
      launch: { launch_id: "c0de".repeat(8), launcher: "gemini" },
    }).success,
  ).toBe(false);
});

test("accepts a run that has recorded no last event, and still rejects a blank one", () => {
  const eventless = {
    run_id: "run-2",
    state: "running",
    phase: "running",
    last_event: null,
    timing_quality: "legacy",
    linkage_quality: "inferred",
    timing,
    node_counts: {},
  };
  const parsed = parseRunList({
    api_version: 1,
    telemetry_schema_version: 9,
    observed_at: "2026-07-26T12:00:00Z",
    runs: [eventless],
  });
  expect(parsed.runs[0]?.last_event).toBeNull();
  // The whole point of the null is that it is the only representation of absence;
  // the degenerate empty string it replaced must stay invalid.
  expect(
    runSummarySchema.safeParse({ ...eventless, last_event: "" }).success,
  ).toBe(false);

  const telemetryResult = runTelemetrySchema.safeParse({
    run_id: "run-2",
    state: "running",
    phase: "running",
    last_event: null,
    timing,
    nodes: [],
    usage: {
      agent: usageParty,
      judge: usageParty,
      llmlint: usageParty,
      total: usageParty,
    },
    timing_quality: "legacy",
    linkage_quality: "inferred",
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
  });
  expect(telemetryResult.success).toBe(true);
});

describe("boundary failures", () => {
  test("rejects incompatible API versions and negative counters", () => {
    expect(() =>
      parseRunList({
        api_version: 2,
        telemetry_schema_version: 9,
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
      telemetry_schema_version: 9,
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

describe("run timeline", () => {
  const span = {
    id: "node-1-api",
    kind: "node",
    label: "api",
    started_at: "2026-07-26T12:00:00Z",
    ended_at: null,
    events: [
      {
        id: "event-4",
        kind: "pr-created",
        at: "2026-07-26T12:00:01Z",
        node_id: "api",
        round: 1,
        status: "OPEN",
        reference: { kind: "pr", value: "https://x/pull/7" },
      },
    ],
  };

  test("accepts an open span, a rollup, and reference-only heavy content", () => {
    const timeline = parseRunTimeline({
      api_version: 1,
      observed_at: "2026-07-26T12:00:00Z",
      run_id: "demo",
      spans: [
        span,
        {
          id: "rollup-lock-wait-9",
          kind: "rollup",
          label: "lock-wait",
          started_at: "2026-07-26T12:00:02Z",
          ended_at: "2026-07-26T12:00:09Z",
          parent_id: "node-1-api",
          node_id: "api",
          count: 1722,
          total_duration_ms: 430500,
          events: [],
        },
        {
          id: "dispatch-lint-1",
          kind: "dispatch",
          label: "llmlint-diff",
          started_at: "2026-07-26T12:00:03Z",
          ended_at: null,
          parent_id: "dispatch-worker-1",
          events: [],
          reference: { kind: "conversation", value: "lint-1" },
        },
      ],
    });
    // A live run is representable: the node has started and has not finished.
    expect(timeline.spans[0]?.ended_at).toBeNull();
    expect(timeline.spans[0]?.events[0]?.reference?.value).toBe(
      "https://x/pull/7",
    );
    expect(timeline.spans[1]?.count).toBe(1722);
    // Nesting travels as a parent link, so a lint run is not a sibling dispatch.
    expect(timeline.spans[2]?.parent_id).toBe("dispatch-worker-1");
  });

  test("rejects an unsupported span kind, reference kind, or negative rollup", () => {
    expect(() =>
      timelineSpanSchema.parse({ ...span, kind: "guess" }),
    ).toThrow();
    expect(() =>
      timelineReferenceSchema.parse({ kind: "transcript", value: "x" }),
    ).toThrow();
    expect(() =>
      timelineSpanSchema.parse({ ...span, kind: "rollup", count: -1 }),
    ).toThrow();
    // ended_at is nullable, never absent, and never a non-timestamp string.
    expect(() =>
      timelineSpanSchema.parse({ ...span, ended_at: "recently" }),
    ).toThrow();
    expect(() =>
      parseRunTimeline({
        api_version: 2,
        observed_at: "2026-07-26T12:00:00Z",
        run_id: "demo",
        spans: [],
      }),
    ).toThrow();
  });
});
