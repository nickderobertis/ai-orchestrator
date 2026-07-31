import type { RunDetail, RunTimeline } from "@ai-orchestrator/dag-model";
import type { TelemetryClient } from "@ai-orchestrator/telemetry-client";
import {
  Badge,
  Card,
  CardContent,
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
  ScrollArea,
  Separator,
  Skeleton,
  TurnCard,
} from "@oneharness/ui";
import { Activity, ChevronRight, Clock3, Cpu, Layers3 } from "lucide-react";
import { useState } from "react";
import { useConversation } from "../timeline/useConversation";

export function OverallView({
  client,
  detail,
  timeline,
}: {
  readonly client: TelemetryClient;
  readonly detail: RunDetail;
  readonly timeline?: RunTimeline;
}) {
  // A session the graph placed at no node is run-level work: the planner driving the
  // whole graph, and the per-round check-ins beside it.
  const sessions = (timeline?.spans ?? []).filter(
    (span) => span.kind === "dispatch" && span.node_id === undefined,
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
              {sessions.length === 0 ? (
                <p className="m-0 text-[11px] text-muted-foreground">
                  No planner conversation is available.
                </p>
              ) : (
                sessions.map((span, index) => (
                  <PlannerSession
                    client={client}
                    conversationId={
                      span.reference?.kind === "conversation"
                        ? span.reference.value
                        : undefined
                    }
                    initiallyOpen={index === 0}
                    key={span.id}
                    label={span.label}
                    launcher={detail.launch?.launcher ?? "unknown"}
                    runId={detail.run.run_id}
                  />
                ))
              )}
            </CardContent>
          </Card>
        </div>
      </ScrollArea>
    </div>
  );
}

/**
 * One run-level transcript, read only while it is open.
 *
 * The run detail no longer carries transcripts at all, so this fetches the one
 * session it is showing — the first is open on arrival because that is the planner
 * conversation an operator came to the overall view to read.
 */
function PlannerSession({
  client,
  runId,
  conversationId,
  label,
  launcher,
  initiallyOpen,
}: {
  readonly client: TelemetryClient;
  readonly runId: string;
  readonly conversationId?: string;
  readonly label: string;
  readonly launcher: string;
  readonly initiallyOpen: boolean;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const transcript = useConversation(
    client,
    open ? runId : undefined,
    open ? conversationId : undefined,
  );
  return (
    <Collapsible onOpenChange={setOpen} open={open}>
      <article>
        <header className="transcript-header">
          <CollapsibleTrigger asChild>
            <button className="planner-toggle" type="button">
              <ChevronRight
                className={open ? "rotate-90" : ""}
                size={14}
                aria-hidden="true"
              />
              <span>
                <span className="eyebrow">Orchestrator</span>
                <span className="planner-name">{label}</span>
              </span>
            </button>
          </CollapsibleTrigger>
          <Badge variant="secondary">{launcher} launcher</Badge>
        </header>
        <Separator className="my-2.5" />
        <CollapsibleContent>
          {transcript.loading && (
            <div aria-live="polite" className="loading-inline">
              <Skeleton className="h-2 w-40" />
              Loading transcript…
            </div>
          )}
          {transcript.error !== undefined && (
            <p className="m-0 text-[11px] text-muted-foreground">
              The planner transcript could not be read:{" "}
              {transcript.error.message}
            </p>
          )}
          {transcript.conversation?.conversation.turns.map((turn) => (
            <TurnCard key={turn.id} turn={turn} />
          ))}
        </CollapsibleContent>
      </article>
    </Collapsible>
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
