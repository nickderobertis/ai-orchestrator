export interface ConversationUsage {
  cacheReadTokens?: number | null | undefined;
  cacheWriteTokens?: number | null | undefined;
  costUsd?: number | null | undefined;
  inputTokens?: number | null | undefined;
  outputTokens?: number | null | undefined;
}

export interface ConversationToolEvent {
  index: number;
  input?: unknown | undefined;
  kind: string;
  name?: string | null | undefined;
  output?: string | null | undefined;
}

export interface ConversationTurn {
  assistant: string | null;
  failureKind: string | null;
  harness: string;
  id: string;
  model: string | null;
  reasoning: string | null;
  status: string;
  timestamp: string;
  tools: ConversationToolEvent[];
  unknown: Record<string, unknown>;
  usage: ConversationUsage;
  user: string;
}

export interface Conversation {
  canContinue: boolean;
  harnesses: string[];
  id: string;
  name: string;
  project: string;
  startedAt: string;
  state: string;
  turns: ConversationTurn[];
}

export interface ConversationSummary {
  harnesses: string[];
  id: string;
  labels?: string[] | undefined;
  name: string;
  project: string;
  startedAt: string;
  turnCount: number;
}
export const conversationLabelMaxLength = 64;
export const conversationLabelsMaxCount = 20;
