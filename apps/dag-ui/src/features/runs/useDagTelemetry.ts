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
  readonly hasUpdates: boolean;
  readonly error?: Error;
  readonly refresh: () => Promise<void>;
}

export function useDagTelemetry(
  client: TelemetryClient,
  requestedRunId?: string,
): DagTelemetryState {
  const [list, setList] = useState<RunList>();
  const [record, setRecord] = useState<RunRecord>();
  const [loading, setLoading] = useState(true);
  const [hasUpdates, setHasUpdates] = useState(false);
  const [error, setError] = useState<Error>();
  //: Bumped whenever the selected run's reads must be taken again.
  const [revision, setRevision] = useState(0);

  const runId =
    list === undefined
      ? undefined
      : list.runs.some(({ run_id }) => run_id === requestedRunId)
        ? requestedRunId
        : list.runs.at(0)?.run_id;
  // Read by the event stream, which must not be torn down and reopened every time
  // the operator selects a different run.
  const selected = useRef(runId);
  useEffect(() => {
    selected.current = runId;
  }, [runId]);

  const loadList = useCallback(async () => {
    setList(await client.listRuns(true));
  }, [client]);

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
      current?.runId === runId ? current : { runId: runId },
    );
    const amend = (change: (current: RunRecord) => RunRecord) => {
      if (!active) return;
      setRecord((current) =>
        current?.runId === runId ? change(current) : current,
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
    void client
      .getTimeline(runId)
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
  }, [client, runId, revision]);

  useEffect(() => {
    void refresh();
    const subscription = client.subscribe({
      onEvent: (event) => {
        setHasUpdates(true);
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
      hasUpdates,
      error,
      refresh,
    }),
    [list, runId, current, loading, hasUpdates, error, refresh],
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
