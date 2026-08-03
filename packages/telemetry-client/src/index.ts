import {
  API_V2_PATHS,
  API_V2_QUERY,
  API_V2_TIMELINE_SCOPES,
  type ArtifactContent,
  apiErrorSchema,
  artifactContentSchema,
  type DagConversation,
  dagConversationSchema,
  type RunDetail,
  type RunList,
  type RunTimeline,
  runDetailSchema,
  runListSchema,
  runTimelineSchema,
  type SseEventName,
  sseEventDataSchema,
  sseEventNameSchema,
} from "@ai-orchestrator/dag-model";

// llmlint: ignore-file[changed_behavior_has_e2e] client.e2e.test.ts crosses a real loopback HTTP
// boundary through the package export. Bun has no native browser EventSource implementation, so
// SSE is exercised at its public EventSource interface with real MessageEvents in index.test.ts;
// the injected factory is the browser boundary, not an internal client layer.

export class TelemetryClientError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly code?: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "TelemetryClientError";
  }
}

export interface TelemetryEvent {
  readonly id: string;
  readonly event: SseEventName;
  readonly data: RunList | Record<string, unknown>;
}

export interface TelemetrySubscription {
  close(): void;
}

export interface SubscribeOptions {
  readonly runId?: string;
  readonly after?: string;
  readonly onEvent: (event: TelemetryEvent) => void;
  readonly onError?: (error: unknown) => void;
}

type EventSourceFactory = (url: string) => EventSource;
type Fetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export interface RunDetailOptions {
  /** Omit to accept the server default (`true`). */
  readonly includeConversations?: boolean;
}

export interface TelemetryClientOptions {
  readonly fetch?: Fetch;
  readonly eventSource?: EventSourceFactory;
}

// Owned by orchestrator.server.RUNS_PAGE_LIMIT and held in sync by the DAG contract
// check. Sending it explicitly keeps continuation pages the same size.
const RUNS_PAGE_LIMIT = 50;

export class TelemetryClient {
  readonly #baseUrl: URL;
  readonly #fetch: Fetch;
  readonly #eventSource?: EventSourceFactory;

  constructor(baseUrl: string | URL, options: TelemetryClientOptions = {}) {
    this.#baseUrl = new URL(baseUrl);
    this.#fetch = options.fetch ?? globalThis.fetch;
    this.#eventSource = options.eventSource;
  }

  async listRuns(
    includeSettled = false,
    cursor?: string,
    limit = RUNS_PAGE_LIMIT,
  ): Promise<RunList> {
    const url = this.#url(API_V2_PATHS.runs);
    url.searchParams.set(API_V2_QUERY.includeSettled, String(includeSettled));
    url.searchParams.set(API_V2_QUERY.limit, String(limit));
    if (cursor !== undefined) url.searchParams.set(API_V2_QUERY.cursor, cursor);
    return this.#request(url, runListSchema.parse);
  }

  /**
   * One run's detail.
   *
   * `includeConversations: false` asks the server for no transcripts, which it
   * serves as an empty `conversations` array. Prefer it alongside {@link getTimeline}
   * for a live view: transcripts dominate the payload and are refetched on every
   * invalidation, while a single conversation stays reachable via
   * {@link getConversation}.
   */
  async getRun(
    runId: string,
    options: RunDetailOptions = {},
  ): Promise<RunDetail> {
    requireOpaqueId(runId, "run ID");
    const url = this.#url(API_V2_PATHS.run(runId));
    if (options.includeConversations !== undefined) {
      url.searchParams.set(
        API_V2_QUERY.includeConversations,
        String(options.includeConversations),
      );
    }
    return this.#request(url, runDetailSchema.parse);
  }

  /** One node's timeline, or the run-level timeline when nodeId is omitted. */
  async getTimeline(runId: string, nodeId?: string): Promise<RunTimeline> {
    requireOpaqueId(runId, "run ID");
    const url = this.#url(API_V2_PATHS.timeline(runId));
    if (nodeId === undefined)
      url.searchParams.set(API_V2_QUERY.scope, API_V2_TIMELINE_SCOPES.run);
    else url.searchParams.set(API_V2_QUERY.nodeId, nodeId);
    return this.#request(url, runTimelineSchema.parse);
  }

  async getConversation(
    runId: string,
    conversationId: string,
  ): Promise<DagConversation> {
    requireOpaqueId(runId, "run ID");
    requireOpaqueId(conversationId, "conversation ID");
    return this.#request(
      this.#url(API_V2_PATHS.conversation(runId, conversationId)),
      dagConversationSchema.parse,
    );
  }

  async getArtifact(
    runId: string,
    artifactId: string,
  ): Promise<ArtifactContent> {
    requireOpaqueId(runId, "run ID");
    requireOpaqueId(artifactId, "artifact ID");
    return this.#request(
      this.#url(API_V2_PATHS.artifact(runId, artifactId)),
      artifactContentSchema.parse,
    );
  }

  subscribe(options: SubscribeOptions): TelemetrySubscription {
    if (options.runId !== undefined) requireOpaqueId(options.runId, "run ID");
    if (options.after !== undefined) requireOpaqueId(options.after, "cursor");
    const url = this.#url(API_V2_PATHS.events);
    if (options.runId !== undefined)
      url.searchParams.set(API_V2_QUERY.runId, options.runId);
    if (options.after !== undefined)
      url.searchParams.set(API_V2_QUERY.after, options.after);
    const create =
      this.#eventSource ??
      ((sourceUrl: string) => {
        if (typeof EventSource === "undefined") {
          throw new TelemetryClientError(
            "EventSource is unavailable; provide an eventSource factory",
          );
        }
        return new EventSource(sourceUrl);
      });
    const source = create(url.toString());
    for (const eventName of sseEventNameSchema.options) {
      source.addEventListener(eventName, (rawEvent) => {
        try {
          // DOM's EventListener callback erases the MessageEvent subtype even though
          // EventSource listeners for named server events always receive one.
          const event = rawEvent as MessageEvent<string>;
          const decoded: unknown = JSON.parse(event.data);
          const data =
            eventName === "snapshot"
              ? runListSchema.parse(decoded)
              : sseEventDataSchema.parse(decoded);
          options.onEvent({
            id: event.lastEventId,
            event: eventName,
            data,
          });
        } catch (error) {
          options.onError?.(error);
        }
      });
    }
    source.onerror = (error) => options.onError?.(error);
    return { close: () => source.close() };
  }

  #url(path: string): URL {
    return new URL(path, this.#baseUrl);
  }

  async #request<T>(url: URL, parse: (value: unknown) => T): Promise<T> {
    let response: Response;
    try {
      response = await this.#fetch(url);
    } catch (error) {
      throw new TelemetryClientError(
        "Telemetry request failed",
        undefined,
        undefined,
        {
          cause: error,
        },
      );
    }
    const value: unknown = await response.json().catch((error: unknown) => {
      throw new TelemetryClientError(
        "Telemetry server returned invalid JSON",
        response.status,
        undefined,
        { cause: error },
      );
    });
    if (!response.ok) {
      const parsed = apiErrorSchema.safeParse(value);
      throw new TelemetryClientError(
        parsed.success
          ? parsed.data.error.message
          : `Telemetry request failed with status ${response.status}`,
        response.status,
        parsed.success ? parsed.data.error.code : undefined,
      );
    }
    try {
      return parse(value);
    } catch (error) {
      throw new TelemetryClientError(
        "Telemetry response failed contract validation",
        response.status,
        undefined,
        { cause: error },
      );
    }
  }
}

function requireOpaqueId(value: string, label: string): void {
  const hasControlCharacter = [...value].some(
    (character) => character.charCodeAt(0) < 32,
  );
  if (value.length === 0 || hasControlCharacter || /[/?#]/u.test(value)) {
    throw new TelemetryClientError(`Invalid ${label}`);
  }
}
