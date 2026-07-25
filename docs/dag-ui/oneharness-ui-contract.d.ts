// @oneharness/ui 0.1.0 public transcript contract, pinned by config/oneharness-ui.commit.
export interface ConversationUsage { cacheReadTokens?: number | null; cacheWriteTokens?: number | null; costUsd?: number | null; inputTokens?: number | null; outputTokens?: number | null; }
export interface ConversationToolEvent { index: number; input?: unknown; kind: string; name?: string | null; output?: string | null; }
export interface ConversationTurn { assistant: string | null; failureKind: string | null; harness: string; id: string; model: string | null; reasoning: string | null; status: string; timestamp: string; tools: ConversationToolEvent[]; unknown: Record<string, unknown>; usage: ConversationUsage; user: string; }
export interface Conversation { canContinue: boolean; harnesses: string[]; id: string; name: string; project: string; startedAt: string; state: string; turns: ConversationTurn[]; }
