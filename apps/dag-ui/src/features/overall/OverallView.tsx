import type { RunDetail } from "@ai-orchestrator/dag-model";
import { type Conversation, TurnCard } from "@oneharness/ui";
import { Activity, Clock3, Cpu, Layers3 } from "lucide-react";

export function OverallView({ detail }: { readonly detail: RunDetail }) {
  const orchestrator = detail.conversations.filter(
    ({ attribution }) => attribution.agentRole === "orchestrator",
  );
  return (
    <div className="overall-view">
      <section className="overall-hero">
        <p className="eyebrow">Whole DAG</p>
        <h2>{detail.run.run_id}</h2>
        <p>
          {detail.run.phase} ·{" "}
          {detail.run.last_event
            ? `last event ${detail.run.last_event}`
            : "no events recorded yet"}
        </p>
      </section>
      <div className="metric-grid">
        <Metric icon={<Activity />} label="Status" value={detail.run.state} />
        <Metric
          icon={<Layers3 />}
          label="Nodes"
          value={String(detail.run.nodes.length)}
        />
        <Metric
          icon={<Clock3 />}
          label="Wall time"
          value={`${detail.run.timing.wall_seconds.toFixed(1)}s`}
        />
        <Metric icon={<Cpu />} label="Turns" value={String(detail.run.turns)} />
      </div>
      <section className="planner-session">
        <div className="section-heading">
          <Activity size={16} />
          <h3>Planner session</h3>
        </div>
        {orchestrator.length === 0 ? (
          <p className="empty-note">No planner conversation is available.</p>
        ) : (
          orchestrator.map(({ conversation, attribution }) => (
            <article className="transcript" key={conversation.id}>
              <header>
                <div>
                  <p className="eyebrow">Orchestrator</p>
                  <h4>{conversation.name}</h4>
                </div>
                <span>{attribution.launcher ?? "unknown"} launcher</span>
              </header>
              {conversation.turns.map((turn) => (
                <TurnCard
                  key={turn.id}
                  turn={turn as Conversation["turns"][number]}
                />
              ))}
            </article>
          ))
        )}
      </section>
    </div>
  );
}

function Metric({
  icon,
  label,
  value,
}: {
  readonly icon: React.ReactNode;
  readonly label: string;
  readonly value: string;
}) {
  return (
    <div className="metric">
      {icon}
      <div>
        <p>{label}</p>
        <strong>{value}</strong>
      </div>
    </div>
  );
}
