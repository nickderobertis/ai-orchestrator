import { API_V1_PATHS, API_V1_QUERY } from "@ai-orchestrator/dag-model";
import { TelemetryClient } from "@ai-orchestrator/telemetry-client";
import { vi } from "vitest";
import {
  HISTORY_RUN,
  LIVE_RUN,
  runDetail,
  runList,
  runTimeline,
} from "./fixtures";

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

/** True for the run-list path the packages publish, whatever it is. */
export const isRunList = (url: URL): boolean =>
  url.pathname === API_V1_PATHS.runs;

/** True for a single run's detail path, whatever run it names. */
export const isRunDetail = (url: URL): boolean =>
  url.pathname.startsWith(`${API_V1_PATHS.runs}/`) &&
  !isTimeline(url) &&
  !isConversation(url);

/** True for a run's timeline path, whatever run it names. */
export const isTimeline = (url: URL): boolean =>
  url.pathname.endsWith("/timeline");

/** True for one transcript's path, whatever run and conversation it names. */
export const isConversation = (url: URL): boolean =>
  url.pathname.includes("/conversations/");

/** The run one `/api/v1/runs/...` path names. */
export const requestedRunId = (url: URL): string =>
  url.pathname.split("/")[4] === HISTORY_RUN ? HISTORY_RUN : LIVE_RUN;

/**
 * The read API a browser would see: list, detail, timeline, one transcript, and the
 * SSE stream. Detail honours `include_conversations` exactly as the server does — it
 * serves the field empty rather than omitting it — so a client that opts out here is
 * opting out of the same payload it would opt out of in production.
 */
export function defaultResponder(url: URL): Response {
  if (isRunList(url)) return Response.json(runList);
  const runId = requestedRunId(url);
  if (isTimeline(url)) return Response.json(runTimeline(runId));
  if (isConversation(url)) {
    const wanted = decodeURIComponent(url.pathname.split("/").at(-1) ?? "");
    const found = runDetail(runId).conversations.find(
      ({ conversation }) => conversation.id === wanted,
    );
    return found
      ? Response.json(found)
      : Response.json(
          {
            error: { code: "conversation_not_found", message: "no transcript" },
          },
          { status: 404 },
        );
  }
  const detail = runDetail(runId);
  return Response.json(
    url.searchParams.get(API_V1_QUERY.includeConversations) === "false"
      ? { ...detail, conversations: [] }
      : detail,
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
