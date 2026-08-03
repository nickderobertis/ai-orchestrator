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
import { formatDurationSeconds } from "../../lib/time";
import { launchLabel } from "../runs/run-model";
import { useConversation } from "../timeline/useConversation";

export function OverallView({
  client,
  detail,
  timeline,
  timelineError,
  conversationRevision,
}: {
  readonly client: TelemetryClient;
  readonly detail: RunDetail;
  readonly timeline?: RunTimeline;
  readonly timelineError?: Error;
  readonly conversationRevision?: number;
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
            {detail.rounds.at(-1)?.plan.goal?.text && (
              <p>{detail.rounds.at(-1)?.plan.goal?.text}</p>
            )}
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
              value={formatDurationSeconds(detail.run.timing.wall_seconds)}
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
                <h3>Run-level sessions</h3>
              </div>
              {/* The sessions are read off the timeline, so a timeline that has not
                  arrived or could not be read is not the same answer as a run that
                  recorded no planner conversation — saying so would be a claim about
                  a record nothing has looked at. */}
              {/* llmlint: ignore[changed_behavior_has_e2e] the run detail and the
                  timeline are read from the same strict journal, so no served run
                  fails one and not the other; a browser reaches this only when the
                  whole API is unreachable, which the offline journey covers at the
                  header banner. App.test.tsx proves this surface through the real
                  client. */}
              {timelineError !== undefined ? (
                <p className="m-0 text-[11px] text-muted-foreground">
                  The run's sessions could not be read: {timelineError.message}
                </p>
              ) : timeline === undefined ? (
                <p
                  aria-live="polite"
                  className="m-0 text-[11px] text-muted-foreground"
                >
                  Loading the run's sessions…
                </p>
              ) : sessions.length === 0 ? (
                <p className="m-0 text-[11px] text-muted-foreground">
                  No run-level conversation is available.
                </p>
              ) : (
                sessions.map((span, index) => (
                  <RunLevelSession
                    client={client}
                    conversationId={
                      span.reference?.kind === "conversation"
                        ? span.reference.value
                        : undefined
                    }
                    initiallyOpen={index === 0}
                    conversationRevision={conversationRevision}
                    key={span.id}
                    label={span.label}
                    launch={launchLabel(detail.launch)}
                    role={span.agent_role}
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
 * Run-level is wider than "the planner": the orchestrator's own session and every
 * per-round check-in are recorded at no node, and all of them belong here. The run
 * detail no longer carries transcripts at all, so this fetches the one session it is
 * showing — the first is open on arrival because that is the planner conversation an
 * operator came to the overall view to read.
 */
function RunLevelSession({
  client,
  runId,
  conversationId,
  label,
  launch,
  role,
  initiallyOpen,
  conversationRevision,
}: {
  readonly client: TelemetryClient;
  readonly runId: string;
  readonly conversationId?: string;
  readonly label: string;
  readonly launch: string;
  /** The dispatch's semantic role, served on the span it was read from. */
  readonly role?: string;
  readonly initiallyOpen: boolean;
  readonly conversationRevision?: number;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const transcript = useConversation(
    client,
    open ? runId : undefined,
    open ? conversationId : undefined,
    conversationRevision,
  );
  return (
    <Collapsible onOpenChange={setOpen} open={open}>
      <article>
        <header className="transcript-header">
          <CollapsibleTrigger asChild>
            <button className="session-toggle" type="button">
              <ChevronRight
                className={open ? "rotate-90" : ""}
                size={14}
                aria-hidden="true"
              />
              <span>
                <span className="eyebrow">
                  {role === undefined ? "Run-level" : `Run-level · ${role}`}
                </span>
                <span className="session-name">{label}</span>
              </span>
            </button>
          </CollapsibleTrigger>
          <Badge variant="secondary">{launch}</Badge>
        </header>
        <Separator className="my-2.5" />
        <CollapsibleContent>
          {transcript.loading && (
            <div aria-live="polite" className="loading-inline">
              <Skeleton className="h-2 w-40" />
              Loading transcript…
            </div>
          )}
          {/* llmlint: ignore[changed_behavior_has_e2e] a transcript the server named
              in the timeline it just served and then refused: unproducible from a
              conforming server, proven through the real client in App.test.tsx. */}
          {transcript.error !== undefined && (
            <p className="m-0 text-[11px] text-muted-foreground">
              This transcript could not be read: {transcript.error.message}
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
