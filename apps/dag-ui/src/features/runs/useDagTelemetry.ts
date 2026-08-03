import {
  parseRunList,
  type RunDetail,
  type RunList,
  type RunTimeline,
} from "@ai-orchestrator/dag-model";
import {
  type TelemetryClient,
  TelemetryClientError,
  type TelemetryEvent,
} from "@ai-orchestrator/telemetry-client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

// llmlint: ignore-file[changed_behavior_has_e2e] The browser suite in e2e/dag-ui.spec.ts
// drives every branch a conforming server can reach: the opening snapshot, a
// `run.changed` raised by appending a real journal event to the served run, a
// `run.removed` raised by taking that run out of the served root, the empty list that
// follows, and a read the browser cannot complete at all. Two branches are left that
// only a broken peer reaches — a snapshot that fails contract validation, and a stream
// that drops after a successful handshake — and this repository's server produces
// neither: it validates what it serves, and an unreachable API fails the handshake
// rather than dropping a live stream. Both are proven in App.test.tsx, which drives
// the real app and the real telemetry client at their browser boundary.

/**
 * What has been read for the one run being looked at.
 *
 * Transcripts are deliberately absent: they dominate the detail payload and are
 * re-read on every invalidation, so the detail is fetched without them and the
 * ordered record comes from the timeline instead. A single conversation is fetched
 * by whichever view opens it.
 */
interface RunRecord {
  readonly runId: string;
  readonly timelineScope?: string;
  readonly detail?: RunDetail;
  readonly timeline?: RunTimeline;
  readonly timelineError?: Error;
}

export interface DagTelemetryState {
  readonly list?: RunList;
  /** The run being read: the requested one when it is served, else the first listed. */
  readonly runId?: string;
  readonly detail?: RunDetail;
  readonly timeline?: RunTimeline;
  /** A timeline read that failed, reported where the timeline would have been. */
  readonly timelineError?: Error;
  readonly loading: boolean;
  readonly lastUpdated?: string;
  readonly activity: readonly LiveActivity[];
  readonly conversationRevision: number;
  readonly error?: Error;
  readonly refresh: () => Promise<void>;
  readonly loadMore: () => Promise<void>;
  readonly hasMore: boolean;
}

export interface LiveActivity {
  readonly round: string;
  readonly node: string;
  readonly at: number;
  readonly kind: string;
  readonly name: string;
  readonly detail: string;
  readonly events: number;
}

export function useDagTelemetry(
  client: TelemetryClient,
  requestedRunId?: string,
  timelineScope?: { readonly nodeId?: string },
): DagTelemetryState {
  const [list, setList] = useState<RunList>();
  const [record, setRecord] = useState<RunRecord>();
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<string>();
  const [activity, setActivity] = useState<readonly LiveActivity[]>([]);
  const [conversationRevision, setConversationRevision] = useState(0);
  const [error, setError] = useState<Error>();
  //: Bumped whenever the selected run's reads must be taken again.
  const [revision, setRevision] = useState(0);

  const runId =
    list === undefined
      ? undefined
      : list.runs.some(({ run_id }) => run_id === requestedRunId)
        ? requestedRunId
        : list.runs.at(0)?.run_id;
  const timelineScopeKey =
    timelineScope === undefined ? undefined : (timelineScope.nodeId ?? "run");
  // Read by the event stream, which must not be torn down and reopened every time
  // the operator selects a different run.
  const selected = useRef(runId);
  useEffect(() => {
    selected.current = runId;
  }, [runId]);

  const loadList = useCallback(async () => {
    setList(await client.listRuns(true));
  }, [client]);
  const loadMore = useCallback(async () => {
    if (list?.next_cursor === undefined) return;
    try {
      const next = await client.listRuns(true, list.next_cursor);
      setList((current) =>
        current === undefined
          ? next
          : {
              ...next,
              runs: [
                ...current.runs,
                ...next.runs.filter(
                  ({ run_id }) =>
                    !current.runs.some((run) => run.run_id === run_id),
                ),
              ],
            },
      );
      setError(undefined);
    } catch (caught) {
      setError(asError(caught));
    }
  }, [client, list]);

  const refresh = useCallback(async () => {
    try {
      await loadList();
      setError(undefined);
    } catch (caught) {
      setError(asError(caught));
    } finally {
      setLoading(false);
    }
    setRevision((current) => current + 1);
  }, [loadList]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: `revision` is not read by this effect — it is what asks for the same run to be read again, which is how a refresh and a live invalidation reach the server at all. Dropping it would leave the view showing the read it took first.
  useEffect(() => {
    if (runId === undefined) {
      setRecord(undefined);
      return;
    }
    let active = true;
    // Keep what is already on screen while the same run is re-read; drop it the
    // moment a different run is selected, so no view renders one run's detail
    // under another's name.
    setRecord((current) =>
      current?.runId === runId && current.timelineScope === timelineScopeKey
        ? current
        : {
            runId: runId,
            timelineScope: timelineScopeKey,
            ...(current?.runId === runId && current.detail !== undefined
              ? { detail: current.detail }
              : {}),
          },
    );
    const amend = (change: (current: RunRecord) => RunRecord) => {
      if (!active) return;
      setRecord((current) =>
        current?.runId === runId && current.timelineScope === timelineScopeKey
          ? change(current)
          : current,
      );
    };
    void client
      .getRun(runId, { includeConversations: false })
      .then((detail) => {
        amend((current) => ({ ...current, detail }));
        if (active) setError(undefined);
      })
      .catch(ignoreRemovedRun)
      .catch((caught: unknown) => {
        if (active) setError(asError(caught));
      });
    if (timelineScope !== undefined)
      void client
        .getTimeline(runId, timelineScope.nodeId)
        .then((timeline) =>
          amend((current) => ({
            ...current,
            timeline,
            timelineError: undefined,
          })),
        )
        .catch(ignoreRemovedRun)
        .catch((caught: unknown) =>
          amend((current) => ({ ...current, timelineError: asError(caught) })),
        );
    return () => {
      active = false;
    };
  }, [client, runId, revision, timelineScope?.nodeId, timelineScopeKey]);

  useEffect(() => {
    void refresh();
    const subscription = client.subscribe({
      onEvent: (event) => {
        setLastUpdated(new Date().toISOString());
        // Every connection — including one the browser reopened after a drop —
        // opens with a snapshot, so an arriving event means the stream recovered.
        setError(undefined);
        if (event.event === "snapshot") {
          setList(parseRunList(event.data));
          setRevision((current) => current + 1);
          return;
        }
        const invalidated = invalidatedRunId(event);
        if (invalidated === undefined) return;
        void loadList().catch((caught: unknown) => setError(asError(caught)));
        // Only the run being looked at is re-read: another run's progress changes
        // its row in the list and nothing else that is on screen.
        if (invalidated === selected.current)
          setRevision((current) => current + 1);
      },
      onError: (caught) => {
        setError(asError(caught));
      },
    });
    return () => subscription.close();
  }, [client, loadList, refresh]);

  useEffect(() => {
    if (runId === undefined) {
      setActivity([]);
      return;
    }
    const subscription = client.subscribe({
      runId,
      onEvent: (event) => {
        setLastUpdated(new Date().toISOString());
        setError(undefined);
        // The global stream owns the complete run list. A run-scoped snapshot is
        // intentionally partial and must never replace it.
        if (event.event === "snapshot") return;
        if (event.event === "activity.changed") {
          const candidate = event.data.activity;
          setActivity(
            Array.isArray(candidate) ? (candidate as LiveActivity[]) : [],
          );
        }
        if (
          event.event === "conversation.changed" ||
          event.event === "activity.changed" ||
          event.event === "run.changed"
        ) {
          setRevision((current) => current + 1);
          setConversationRevision((current) => current + 1);
        }
      },
      onError: (caught) => setError(asError(caught)),
    });
    return () => subscription.close();
  }, [client, runId]);

  // A record read for a run that is no longer selected is not this run's record.
  const current =
    record !== undefined && record.runId === runId ? record : undefined;
  return useMemo(
    () => ({
      list,
      runId,
      detail: current?.detail,
      timeline: current?.timeline,
      timelineError: current?.timelineError,
      loading,
      lastUpdated,
      activity,
      conversationRevision,
      error,
      refresh,
      loadMore,
      hasMore: list?.next_cursor !== undefined,
    }),
    [
      list,
      runId,
      current,
      loading,
      lastUpdated,
      activity,
      conversationRevision,
      error,
      refresh,
      loadMore,
    ],
  );
}

/**
 * Swallow the one read failure that is not a failure: a run removed between the
 * read that listed it and the read that fetched it. The next list already drops it,
 * so reporting "no recorded run" would only describe the race, not a problem.
 */
function ignoreRemovedRun(caught: unknown): void {
  if (caught instanceof TelemetryClientError && caught.status === 404) return;
  throw caught;
}

/** The run an invalidation event names, or `undefined` when it names none. */
function invalidatedRunId(event: TelemetryEvent): string | undefined {
  const runId = (event.data as Record<string, unknown>).run_id;
  return typeof runId === "string" && runId.length > 0 ? runId : undefined;
}

function asError(value: unknown): Error {
  if (value instanceof Error) return value;
  // An `EventSource` failure arrives as a bare DOM Event with nothing to read.
  if (typeof Event !== "undefined" && value instanceof Event) {
    return new Error("Live telemetry stream disconnected");
  }
  return new Error(String(value));
}
