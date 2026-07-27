import { TelemetryClient } from "@ai-orchestrator/telemetry-client";
import { vi } from "vitest";
import { HISTORY_RUN, LIVE_RUN, runDetail, runList } from "./fixtures";

/**
 * The browser `EventSource` the telemetry client opens, implemented over a real
 * `EventTarget` so the client's own listeners and `MessageEvent`s run unchanged.
 * jsdom ships no `EventSource`, and this is the browser boundary the client
 * deliberately takes as an injectable factory — not a layer of the app under test.
 */
export class FakeEventSource extends EventTarget {
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    super();
  }

  close(): void {
    this.closed = true;
  }

  /** Deliver one server-sent frame exactly as the browser would. */
  emit(event: string, data: unknown, lastEventId = "1"): void {
    this.dispatchEvent(
      new MessageEvent(event, { data: JSON.stringify(data), lastEventId }),
    );
  }

  /** Drop the stream the way a browser reports a lost connection. */
  fail(): void {
    this.onerror?.(new Event("error"));
  }
}

export interface TelemetryHarness {
  readonly client: TelemetryClient;
  readonly sources: FakeEventSource[];
  readonly fetch: ReturnType<typeof vi.fn>;
}

type Responder = (url: URL) => Response | Promise<Response>;

/** The read API a browser would see: list, detail, and the SSE stream. */
export function defaultResponder(url: URL): Response {
  if (url.pathname === "/api/v1/runs") return Response.json(runList);
  const runId = url.pathname.split("/").at(-1);
  return Response.json(
    runDetail(runId === HISTORY_RUN ? HISTORY_RUN : LIVE_RUN),
  );
}

/** A real `TelemetryClient` wired to a doubled network and event stream. */
export function telemetryHarness(
  responder: Responder = defaultResponder,
): TelemetryHarness {
  const sources: FakeEventSource[] = [];
  const fetchDouble = vi.fn(async (input: URL | RequestInfo) =>
    responder(new URL(String(input), window.location.origin)),
  );
  const client = new TelemetryClient(window.location.origin, {
    fetch: fetchDouble as unknown as typeof fetch,
    eventSource: (url) => {
      const source = new FakeEventSource(url);
      sources.push(source);
      return source as unknown as EventSource;
    },
  });
  return { client, sources, fetch: fetchDouble };
}
