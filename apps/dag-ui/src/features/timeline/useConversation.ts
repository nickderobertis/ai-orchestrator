import type { DagConversation } from "@ai-orchestrator/dag-model";
import type { TelemetryClient } from "@ai-orchestrator/telemetry-client";
import { useEffect, useState } from "react";

export interface ConversationState {
  readonly conversation?: DagConversation;
  readonly loading: boolean;
  readonly error?: Error;
}

/**
 * One transcript, fetched only while something is showing it.
 *
 * A run's transcripts are the bulk of its recorded state, so nothing downloads them
 * wholesale: the timeline names each session by id, and this reads the one the
 * operator opened. Selecting another moment of the same session changes nothing here,
 * so stepping through a conversation's turns costs one read, not one per turn.
 */
export function useConversation(
  client: TelemetryClient,
  runId?: string,
  conversationId?: string,
): ConversationState {
  const [state, setState] = useState<ConversationState>({ loading: false });

  useEffect(() => {
    if (runId === undefined || conversationId === undefined) {
      setState({ loading: false });
      return;
    }
    let active = true;
    setState({ loading: true });
    void client
      .getConversation(runId, conversationId)
      .then((conversation) => {
        if (active) setState({ conversation, loading: false });
      })
      .catch((caught: unknown) => {
        if (active)
          setState({
            loading: false,
            error: caught instanceof Error ? caught : new Error(String(caught)),
          });
      });
    return () => {
      active = false;
    };
  }, [client, runId, conversationId]);

  return state;
}
