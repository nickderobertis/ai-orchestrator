import {
  parseRunList,
  type RunDetail,
  type RunList,
} from "@ai-orchestrator/dag-model";
import {
  type TelemetryClient,
  TelemetryClientError,
  type TelemetryEvent,
} from "@ai-orchestrator/telemetry-client";
import { useCallback, useEffect, useMemo, useState } from "react";

export interface DagTelemetryState {
  readonly list?: RunList;
  readonly details: ReadonlyMap<string, RunDetail>;
  readonly loading: boolean;
  readonly hasUpdates: boolean;
  readonly error?: Error;
  readonly refresh: () => Promise<void>;
}

export function useDagTelemetry(client: TelemetryClient): DagTelemetryState {
  const [list, setList] = useState<RunList>();
  const [details, setDetails] = useState<ReadonlyMap<string, RunDetail>>(
    new Map(),
  );
  const [loading, setLoading] = useState(true);
  const [hasUpdates, setHasUpdates] = useState(false);
  const [error, setError] = useState<Error>();

  const loadDetail = useCallback(
    async (runId: string) => {
      const detail = await client.getRun(runId);
      setDetails((current) => {
        const next = new Map(current);
        next.set(runId, detail);
        return next;
      });
    },
    [client],
  );

  const loadDetailFromEvent = useCallback(
    (runId: string) => {
      void loadDetail(runId)
        .catch(ignoreRemovedRun)
        .catch((caught: unknown) => setError(asError(caught)));
    },
    [loadDetail],
  );

  const refresh = useCallback(async () => {
    try {
      const next = await client.listRuns(true);
      setList(next);
      await Promise.all(
        next.runs.map(({ run_id }) =>
          loadDetail(run_id).catch(ignoreRemovedRun),
        ),
      );
      setError(undefined);
    } catch (caught) {
      setError(asError(caught));
    } finally {
      setLoading(false);
    }
  }, [client, loadDetail]);

  useEffect(() => {
    void refresh();
    const subscription = client.subscribe({
      onEvent: (event) => {
        setHasUpdates(true);
        // Every connection — including one the browser reopened after a drop —
        // opens with a snapshot, so an arriving event means the stream recovered.
        setError(undefined);
        if (event.event === "snapshot") {
          const snapshot = parseRunList(event.data);
          setList(snapshot);
          for (const { run_id } of snapshot.runs) {
            loadDetailFromEvent(run_id);
          }
          return;
        }
        const runId = invalidatedRunId(event);
        if (runId === undefined) return;
        if (event.event === "run.removed") {
          setDetails((current) => {
            const next = new Map(current);
            next.delete(runId);
            return next;
          });
          void refresh();
          return;
        }
        loadDetailFromEvent(runId);
        void client
          .listRuns(true)
          .then(setList)
          .catch((caught: unknown) => setError(asError(caught)));
      },
      onError: (caught) => {
        setError(asError(caught));
      },
    });
    return () => subscription.close();
  }, [client, loadDetailFromEvent, refresh]);

  return useMemo(
    () => ({ list, details, loading, hasUpdates, error, refresh }),
    [list, details, loading, hasUpdates, error, refresh],
  );
}

/**
 * Swallow the one detail failure that is not a failure: a run removed between the
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
