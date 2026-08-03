export const conversationLabelLimits = {
  maxCount: 20,
  maxLength: 64,
} as const;

export interface ConversationUsage {
  cacheReadTokens?: number | null | undefined;
  cacheWriteTokens?: number | null | undefined;
  costUsd?: number | null | undefined;
  inputTokens?: number | null | undefined;
  outputTokens?: number | null | undefined;
}

export interface ConversationToolEvent {
  durationMs?: number | null | undefined;
  finishedAt?: string | null | undefined;
  index: number;
  input?: unknown | undefined;
  kind: string;
  name?: string | null | undefined;
  output?: string | null | undefined;
  startedAt?: string | null | undefined;
  status?: string | null | undefined;
  timingSource?: string | null | undefined;
  toolCallId?: string | null | undefined;
}

export interface ConversationTurn {
  assistant: string | null;
  durationMs?: number | null | undefined;
  failureKind: string | null;
  finishedAt?: string | null | undefined;
  harness: string;
  id: string;
  model: string | null;
  modelMs?: number | null | undefined;
  reasoning: string | null;
  status: string;
  startedAt?: string | null | undefined;
  timestamp: string;
  timeToFirstTokenMs?: number | null | undefined;
  toolMs?: number | null | undefined;
  tools: ConversationToolEvent[];
  unknown: Record<string, unknown>;
  usage: ConversationUsage;
  user: string;
}

export interface Conversation {
  canContinue: boolean;
  harnesses: string[];
  historyLabels?: Record<string, string> | undefined;
  id: string;
  name: string;
  project: string;
  startedAt: string;
  state: string;
  turns: ConversationTurn[];
}

export interface ConversationSummary {
  harnesses: string[];
  historyLabels?: Record<string, string> | undefined;
  id: string;
  labels?: string[] | undefined;
  name: string;
  project: string;
  startedAt: string;
  turnCount: number;
}
