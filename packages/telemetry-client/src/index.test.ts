import { describe, expect, test } from "bun:test";

import { TelemetryClient, TelemetryClientError } from "./index.js";

const emptyList = {
  api_version: 2,
  telemetry_schema_version: 10,
  observed_at: "2026-07-26T12:00:00Z",
  runs: [],
} as const;

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
const zeroParty = {
  input_tokens: null,
  output_tokens: null,
  cache_read_tokens: null,
  cache_write_tokens: null,
  cost_usd: null,
};
/** The smallest `RunTelemetry` the published contract accepts. */
const runTelemetry = {
  run_id: "run-1",
  state: "running",
  phase: "dispatch",
  last_event: "node-started",
  timing: zeroTiming,
  nodes: [],
  usage: {
    agent: zeroParty,
    judge: zeroParty,
    llmlint: zeroParty,
    total: zeroParty,
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
};

describe("TelemetryClient fetch boundary", () => {
  test("returns validated list data and sends the settled filter", async () => {
    let requested = "";
    const client = new TelemetryClient("http://127.0.0.1:8000/", {
      fetch: async (input) => {
        requested = String(input);
        return Response.json(emptyList);
      },
    });
    const list = await client.listRuns(true);
    expect(list.api_version).toBe(2);
    expect(list.runs).toEqual([]);
    expect(requested).toBe(
      "http://127.0.0.1:8000/api/v2/runs?include_settled=true&limit=50",
    );
  });

  test("rejects a successful response that violates the model", async () => {
    const client = new TelemetryClient("http://localhost", {
      fetch: async () => Response.json({ ...emptyList, api_version: 3 }),
    });
    await expect(client.listRuns()).rejects.toBeInstanceOf(
      TelemetryClientError,
    );
  });

  test("asks for a run without transcripts and reads the run timeline", async () => {
    const requested: string[] = [];
    const client = new TelemetryClient("http://127.0.0.1:8000/", {
      fetch: async (input) => {
        const url = String(input);
        requested.push(url);
        if (url.includes("/timeline?")) {
          return Response.json({
            api_version: 2,
            observed_at: "2026-07-26T12:00:00Z",
            run_id: "run-1",
            spans: [
              {
                id: "node-1-build",
                kind: "node",
                label: "build",
                started_at: "2026-07-26T12:00:00Z",
                ended_at: null,
                events: [],
              },
            ],
          });
        }
        return Response.json({
          api_version: 2,
          telemetry_schema_version: 10,
          observed_at: "2026-07-26T12:00:00Z",
          run: runTelemetry,
          rounds: [],
          conversations: [],
        });
      },
    });

    const lean = await client.getRun("run-1", { includeConversations: false });
    expect(lean.conversations).toEqual([]);
    // The opt-out travels as the documented query parameter, not a header or a path.
    expect(requested[0]).toBe(
      "http://127.0.0.1:8000/api/v2/runs/run-1?include_conversations=false",
    );

    const timeline = await client.getTimeline("run-1", "build");
    // An in-flight node stays representable all the way to the consumer.
    expect(timeline.spans[0]?.ended_at).toBeNull();
    expect(requested[1]).toBe(
      "http://127.0.0.1:8000/api/v2/runs/run-1/timeline?node_id=build",
    );

    // Omitting the option leaves the request exactly as it was before.
    await client.getRun("run-1");
    expect(requested[2]).toBe("http://127.0.0.1:8000/api/v2/runs/run-1");
  });

  test("rejects a timeline response that violates the model", async () => {
    const client = new TelemetryClient("http://localhost", {
      fetch: async () =>
        Response.json({
          api_version: 2,
          observed_at: "2026-07-26T12:00:00Z",
          run_id: "run-1",
          spans: [{ id: "x", kind: "guess", label: "x", events: [] }],
        }),
    });
    await expect(client.getTimeline("run-1")).rejects.toBeInstanceOf(
      TelemetryClientError,
    );
    await expect(client.getTimeline("bad/id")).rejects.toBeInstanceOf(
      TelemetryClientError,
    );
  });

  test("surfaces the typed server error", async () => {
    const client = new TelemetryClient("http://localhost", {
      fetch: async () =>
        Response.json(
          { error: { code: "not_found", message: "Run is missing" } },
          { status: 404 },
        ),
    });
    try {
      await client.getRun("run-1");
      throw new Error("expected request to fail");
    } catch (error) {
      expect(error).toMatchObject({
        status: 404,
        code: "not_found",
        message: "Run is missing",
      });
    }
  });
});

test("validates SSE snapshots before notifying subscribers", () => {
  const listeners = new Map<string, EventListener>();
  let closed = false;
  const source = {
    addEventListener: (
      name: string,
      listener: EventListenerOrEventListenerObject,
    ) => listeners.set(name, listener as EventListener),
    close: () => {
      closed = true;
    },
    onerror: null,
  } as unknown as EventSource;
  const events: unknown[] = [];
  const errors: unknown[] = [];
  const client = new TelemetryClient("http://localhost", {
    eventSource: () => source,
  });
  const subscription = client.subscribe({
    onEvent: (event) => events.push(event),
    onError: (error) => errors.push(error),
  });
  listeners.get("snapshot")?.(
    new MessageEvent("snapshot", {
      data: JSON.stringify(emptyList),
      lastEventId: "12",
    }),
  );
  listeners.get("snapshot")?.(
    new MessageEvent("snapshot", {
      data: JSON.stringify({ ...emptyList, telemetry_schema_version: 11 }),
    }),
  );
  expect(events).toHaveLength(1);
  expect(errors).toHaveLength(1);
  subscription.close();
  expect(closed).toBe(true);
});
