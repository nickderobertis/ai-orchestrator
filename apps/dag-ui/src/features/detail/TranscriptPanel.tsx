import type { DagConversation } from "@ai-orchestrator/dag-model";
import { type Conversation, TurnCard } from "@oneharness/ui";

type Attribution = DagConversation["attribution"];
type AgentRole = Attribution["agentRole"];

export function TranscriptPanel({
  conversations,
}: {
  readonly conversations: readonly DagConversation[];
}) {
  if (conversations.length === 0)
    return (
      <p className="empty-note">No conversations recorded for this node.</p>
    );
  return (
    <div className="transcripts">
      {conversations.map(({ conversation, attribution }) => (
        <section className="transcript" key={conversation.id}>
          <header>
            <div>
              <p className="eyebrow">
                {roleLabel(attribution.agentRole, attribution.transportRole)}
              </p>
              <h4>{attribution.persona ?? conversation.name}</h4>
            </div>
            <span className={`status status-${conversation.state}`}>
              {conversation.state}
            </span>
          </header>
          {conversation.turns.map((turn) => (
            <TurnCard
              key={turn.id}
              turn={turn as Conversation["turns"][number]}
            />
          ))}
        </section>
      ))}
    </div>
  );
}

/** Every semantic role the contract's closed `agentRoleSchema` enum admits. */
const ROLE_LABELS: Readonly<Record<AgentRole, string>> = {
  worker: "Worker",
  judge: "Judge",
  "check-in": "Check-in",
  "pr-author": "PR author",
  orchestrator: "Orchestrator",
};

function roleLabel(
  agentRole: AgentRole,
  transportRole: Attribution["transportRole"],
): string {
  // Nested lint work is grouped under its worker, so its transport is what names it.
  return transportRole === "llmlint" ? "Lint" : ROLE_LABELS[agentRole];
}
