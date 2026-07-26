import { describe, expect, test } from "bun:test";

import { TelemetryClient, TelemetryClientError } from "./index.js";

const emptyList = {
  api_version: 1,
  telemetry_schema_version: 7,
  observed_at: "2026-07-26T12:00:00Z",
  runs: [],
} as const;

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
    expect(list.api_version).toBe(1);
    expect(list.runs).toEqual([]);
    expect(requested).toBe(
      "http://127.0.0.1:8000/api/v1/runs?include_settled=true",
    );
  });

  test("rejects a successful response that violates the model", async () => {
    const client = new TelemetryClient("http://localhost", {
      fetch: async () => Response.json({ ...emptyList, api_version: 2 }),
    });
    await expect(client.listRuns()).rejects.toBeInstanceOf(
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
      data: JSON.stringify({ ...emptyList, telemetry_schema_version: 7 }),
    }),
  );
  expect(events).toHaveLength(1);
  expect(errors).toHaveLength(1);
  subscription.close();
  expect(closed).toBe(true);
});
