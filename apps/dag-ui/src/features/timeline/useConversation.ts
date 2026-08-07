import type { DagConversation } from "@ai-orchestrator/dag-model";
import type { TelemetryClient } from "@ai-orchestrator/telemetry-client";
import { useEffect, useMemo, useState } from "react";

export interface ConversationState {
  readonly conversation?: DagConversation;
  /**
   * True only while the *first* read of this transcript is in flight.
   *
   * A revalidation of a transcript already on screen is deliberately not loading:
   * an operator reading a live run must not have the page they are reading replaced
   * by a skeleton every time the run records something.
   */
  readonly loading: boolean;
  readonly error?: Error;
}

/** What has been read, and which transcript it is the reading of. */
interface Fetched {
  readonly key: string;
  readonly conversation?: DagConversation;
  readonly error?: Error;
}

/**
 * One transcript, fetched only while something is showing it, and re-read only when
 * the run has recorded something new in it.
 *
 * A run's transcripts are the bulk of its recorded state, so nothing downloads them
 * wholesale: the timeline names each session by id, and this reads the one the
 * operator opened. Selecting another moment of the same session changes nothing here,
 * so stepping through a conversation's turns costs one read, not one per turn.
 *
 * `record` is what the served timeline says this session has recorded — its state, its
 * end, and its turns. It is the whole revalidation rule: a live session's record moves
 * as turns land and this re-reads it, while a session that has stopped recording keeps
 * one record forever and is never read again, however busy the rest of the run is.
 * Reads are stale-while-revalidate — the transcript already served stays until the
 * next one arrives, and a revalidation that fails leaves it in place rather than
 * replacing a readable page with an error.
 */
export function useConversation(
  client: TelemetryClient,
  runId?: string,
  conversationId?: string,
  record?: string,
): ConversationState {
  const [fetched, setFetched] = useState<Fetched>();
  // Serialized rather than joined on a separator: both halves are server-chosen
  // identifiers, and no separator is guaranteed to be absent from either.
  const key =
    runId === undefined || conversationId === undefined
      ? undefined
      : JSON.stringify([runId, conversationId]);

  useEffect(() => {
    if (
      key === undefined ||
      runId === undefined ||
      conversationId === undefined
    )
      return;
    // Read so each change of the served record asks for the open transcript again;
    // the value itself is immaterial.
    void record;
    let active = true;
    void client
      .getConversation(runId, conversationId)
      .then((conversation) => {
        if (active) setFetched({ key, conversation });
      })
      .catch((caught: unknown) => {
        if (!active) return;
        const error =
          caught instanceof Error ? caught : new Error(String(caught));
        setFetched((current) =>
          current?.key === key && current.conversation !== undefined
            ? current
            : { key, error },
        );
      });
    return () => {
      active = false;
    };
  }, [client, runId, conversationId, key, record]);

  // A transcript read for a session that is no longer open is not this one's, so the
  // panel reports a first load rather than rendering one session's turns under
  // another's name.
  const current = fetched?.key === key ? fetched : undefined;
  return useMemo(
    () => ({
      conversation: current?.conversation,
      error: current?.error,
      loading: key !== undefined && current === undefined,
    }),
    [current, key],
  );
}
