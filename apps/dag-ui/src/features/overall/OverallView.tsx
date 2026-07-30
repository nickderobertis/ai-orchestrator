import type { RunDetail } from "@ai-orchestrator/dag-model";
import {
  Badge,
  Card,
  CardContent,
  ScrollArea,
  Separator,
  TurnCard,
} from "@oneharness/ui";
import { Activity, Clock3, Cpu, Layers3 } from "lucide-react";

export function OverallView({ detail }: { readonly detail: RunDetail }) {
  const orchestrator = detail.conversations.filter(
    ({ attribution }) => attribution.agentRole === "orchestrator",
  );
  return (
    <div className="overall-view">
      <ScrollArea className="h-full">
        <div className="p-[34px]">
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
            <Metric
              icon={<Activity />}
              label="Status"
              value={detail.run.state}
            />
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
            <Metric
              icon={<Cpu />}
              label="Turns"
              value={String(detail.run.turns)}
            />
          </div>
          <Card className="gap-0 py-[15px]">
            <CardContent className="px-[15px]">
              <div className="section-heading">
                <Activity size={16} />
                <h3>Planner session</h3>
              </div>
              {orchestrator.length === 0 ? (
                <p className="m-0 text-[11px] text-muted-foreground">
                  No planner conversation is available.
                </p>
              ) : (
                orchestrator.map(({ conversation, attribution }) => (
                  <article key={conversation.id}>
                    <header className="transcript-header">
                      <div>
                        <p className="eyebrow">Orchestrator</p>
                        <h4>{conversation.name}</h4>
                      </div>
                      <Badge variant="secondary">
                        {attribution.launcher ?? "unknown"} launcher
                      </Badge>
                    </header>
                    <Separator className="my-2.5" />
                    {conversation.turns.map((turn) => (
                      <TurnCard key={turn.id} turn={turn} />
                    ))}
                  </article>
                ))
              )}
            </CardContent>
          </Card>
        </div>
      </ScrollArea>
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
    <Card className="metric flex-row items-center gap-3 p-4">
      {icon}
      <div>
        <p>{label}</p>
        <strong>{value}</strong>
      </div>
    </Card>
  );
}
