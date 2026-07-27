import type { DagConversation } from "@ai-orchestrator/dag-model";
import { type Conversation, TurnCard } from "@oneharness/ui";

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

function roleLabel(agentRole: string, transportRole: string): string {
  if (transportRole === "llmlint") return "Lint";
  const labels: Record<string, string> = {
    worker: "Worker",
    judge: "Judge",
    "check-in": "Check-in",
    "pr-author": "PR author",
    orchestrator: "Orchestrator",
  };
  return labels[agentRole] ?? agentRole;
}
