import type { RunTimeline } from "@ai-orchestrator/dag-model";
import type { TelemetryClient } from "@ai-orchestrator/telemetry-client";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
  Alert,
  AlertDescription,
  AlertTitle,
  Button,
  Card,
} from "@oneharness/ui";
import { ArrowLeft, ExternalLink, TriangleAlert } from "lucide-react";
import { useEffect, useMemo } from "react";
import type { NodeView } from "../runs/run-model";
import { StateBadge } from "../runs/StateBadge";
import { TimelineItemDetail } from "./TimelineItemDetail";
import { TimelineRail } from "./TimelineRail";
import { findRow, nodeTimeline } from "./timeline-model";

/**
 * One node, read as what it did rather than as a column of stacked blocks.
 *
 * The graph is reduced to a breadcrumb, and the working area becomes master and
 * detail: the node's recorded spans and events in order on the left, and whichever
 * one is open expanded across the rest. The node's own task, criteria, dependencies,
 * PR and gate result stay one disclosure away rather than pushing that record down
 * the page.
 */
export function NodeTimelineView({
  client,
  runId,
  node,
  timeline,
  timelineError,
  selectedItemId,
  onSelectItem,
  onBack,
}: {
  readonly client: TelemetryClient;
  readonly runId: string;
  readonly node: NodeView;
  readonly timeline?: RunTimeline;
  readonly timelineError?: Error;
  readonly selectedItemId?: string;
  readonly onSelectItem: (id?: string) => void;
  readonly onBack: () => void;
}) {
  const projected = useMemo(
    () => nodeTimeline(timeline, node.id),
    [timeline, node.id],
  );
  const selected =
    selectedItemId === undefined
      ? undefined
      : findRow(projected.rows, selectedItemId);

  // Escape is the way out of a full-screen view everywhere else, so it is the way
  // out of this one; the breadcrumb button is the visible half of the same exit.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onBack();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onBack]);

  return (
    <section aria-label={`Timeline for ${node.label}`} className="node-view">
      <header className="node-view-head">
        <nav aria-label="Breadcrumb">
          <ol className="breadcrumb">
            <li>
              <Button onClick={onBack} size="sm" type="button" variant="ghost">
                <ArrowLeft size={14} /> Graph
              </Button>
            </li>
            <li aria-current="page">
              <span className="breadcrumb-node">{node.label}</span>
            </li>
          </ol>
        </nav>
        <div className="node-view-facts">
          <StateBadge state={node.state} />
          <span className="node-view-meta">
            {node.kind} node · {node.telemetry?.turns ?? 0} turns ·{" "}
            {node.telemetry?.lint ?? 0} lint turns
          </span>
          {typeof node.result?.pr === "string" && (
            <a
              className="node-view-pr"
              href={node.result.pr}
              rel="noreferrer"
              target="_blank"
            >
              Pull request <ExternalLink size={12} />
            </a>
          )}
        </div>
      </header>

      <Accordion className="node-summary" collapsible type="single">
        <AccordionItem value="task">
          <AccordionTrigger>Task</AccordionTrigger>
          <AccordionContent>
            <pre>{node.task.task}</pre>
          </AccordionContent>
        </AccordionItem>
        <AccordionItem value="done-when">
          <AccordionTrigger>Completion criteria</AccordionTrigger>
          <AccordionContent>
            {/* A human action names work for a person, not a bar the harness can
                check, so the contract lets it record none. */}
            <pre>
              {node.task.done_when ?? "No completion criteria recorded."}
            </pre>
          </AccordionContent>
        </AccordionItem>
        <AccordionItem value="context">
          <AccordionTrigger>Dependencies, PR and gate</AccordionTrigger>
          <AccordionContent>
            <dl className="facts">
              <div>
                <dt>Dependencies</dt>
                <dd>{node.task.deps?.join(", ") || "None"}</dd>
              </div>
              <div>
                <dt>Pull request</dt>
                <dd>
                  <PullRequest pr={node.result?.pr} />
                </dd>
              </div>
              <div>
                <dt>Gate result</dt>
                <dd>{formatValue(node.telemetry?.gate_attestation)}</dd>
              </div>
              <div>
                <dt>Outcome</dt>
                <dd>{formatValue(node.result?.detail)}</dd>
              </div>
            </dl>
          </AccordionContent>
        </AccordionItem>
      </Accordion>

      {/* llmlint: ignore[changed_behavior_has_e2e] the detail and the timeline are
          read from the same strict journal, so no served run fails one and not the
          other; a browser reaches this only when the whole API is unreachable, and
          then there is no node view to report it in. App.test.tsx drives it through
          the real telemetry client. */}
      {timelineError !== undefined ? (
        <Alert className="m-5 w-auto" variant="destructive">
          <TriangleAlert />
          <AlertTitle>Timeline unavailable</AlertTitle>
          <AlertDescription>{timelineError.message}</AlertDescription>
        </Alert>
      ) : timeline === undefined ? (
        <div aria-live="polite" className="loading-state">
          Loading the recorded timeline…
        </div>
      ) : projected.rows.length === 0 ? (
        <Card className="m-5 items-center gap-2 p-8 text-muted-foreground">
          <p className="m-0">This node has no recorded timeline yet.</p>
          <p className="m-0 text-[11px]">
            Spans and events appear here as the run records them.
          </p>
        </Card>
      ) : (
        <div className="node-view-body">
          <TimelineRail
            onSelect={onSelectItem}
            rows={projected.rows}
            selectedId={selectedItemId}
          />
          <TimelineItemDetail
            client={client}
            node={node}
            row={selected}
            runId={runId}
          />
        </div>
      )}
    </section>
  );
}

function PullRequest({ pr }: { readonly pr?: string | null }) {
  if (typeof pr !== "string" || pr === "") return <>{formatValue(pr)}</>;
  return (
    <a className="node-view-pr" href={pr} rel="noreferrer" target="_blank">
      {pr} <ExternalLink size={12} />
    </a>
  );
}

function formatValue(value: unknown): string {
  if (value === undefined || value === null || value === "")
    return "Not recorded";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}
