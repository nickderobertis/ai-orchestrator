import type { RunDetail, RunTimeline } from "@ai-orchestrator/dag-model";
import type { TelemetryClient } from "@ai-orchestrator/telemetry-client";
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Button,
  Card,
  CardContent,
  ScrollArea,
  Timeline,
} from "@oneharness/ui";
import { Activity, Clock3, Cpu, Layers3, TriangleAlert, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { formatDuration, formatDurationSeconds } from "../../lib/time";
import type { NodeView } from "../runs/run-model";
import {
  type GraphRow,
  type GraphSegment,
  graphTimeline,
} from "../timeline/graph-timeline";
import { TimelineItemDetail } from "../timeline/TimelineItemDetail";
import { spanAsRow } from "../timeline/timeline-model";

/**
 * The run as a whole, read as one clock rather than as a list of its parts.
 *
 * There are three linked levels here and each is one click from the next. The graph
 * timeline collapses to a single line — what the run has spent its life on, silence
 * included — opens into one row per node beside the run's own driving sessions, and
 * each of those rows opens again into the category lanes the node view draws. Every
 * row is plotted on one controlled range, so zooming any of them zooms all of them and
 * a column always means the same instant however deep the reader is.
 *
 * There is deliberately no time cursor at this level: a cursor locks a plot to a
 * position in a stream being read beside it, and a graph is many streams at once.
 */
export function OverallView({
  client,
  detail,
  nodes,
  timeline,
  timelineError,
  conversationRevision,
  onSelectNode,
  selectedItemId,
  onSelectItem,
}: {
  readonly client: TelemetryClient;
  readonly detail: RunDetail;
  readonly nodes: readonly NodeView[];
  readonly timeline?: RunTimeline;
  readonly timelineError?: Error;
  readonly conversationRevision?: number;
  readonly onSelectNode: (nodeId: string) => void;
  readonly selectedItemId?: string;
  readonly onSelectItem: (itemId?: string) => void;
}) {
  const runId = detail.run.run_id;
  const graph = useMemo(
    () => graphTimeline(timeline, nodes),
    [timeline, nodes],
  );
  // The one run-level session an operator opened, projected exactly as the node view
  // projects a node's own, so both readings are the same reading.
  const opened = (timeline?.spans ?? []).find(
    (span) => span.id === selectedItemId && span.node_id === undefined,
  );
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && selectedItemId !== undefined)
        onSelectItem();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onSelectItem, selectedItemId]);

  return (
    <div className="overall-view">
      <ScrollArea className="h-full">
        <div className="p-[34px] max-sm:p-3">
          <section className="overall-hero">
            <p className="eyebrow">Whole DAG</p>
            <h2>{runId}</h2>
            {detail.rounds.at(-1)?.plan.goal?.text && (
              <p className="run-goal">
                {detail.rounds.at(-1)?.plan.goal?.text}
              </p>
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
                <h3>Graph timeline</h3>
              </div>
              {/* llmlint: ignore[changed_behavior_has_e2e] the run detail and the
                  timeline are read from the same strict journal, so no served run
                  fails one and not the other; a browser reaches this only when the
                  whole API is unreachable, which the offline journey covers at the
                  header banner. App.test.tsx proves this surface through the real
                  client. */}
              {timelineError !== undefined ? (
                <Alert variant="destructive">
                  <TriangleAlert />
                  <AlertTitle>Timeline unavailable</AlertTitle>
                  <AlertDescription>{timelineError.message}</AlertDescription>
                </Alert>
              ) : timeline === undefined ? (
                <p
                  aria-live="polite"
                  className="m-0 text-[11px] text-muted-foreground"
                >
                  Loading the run's timeline…
                </p>
              ) : timeline.spans.length === 0 ? (
                <p className="m-0 text-[11px] text-muted-foreground">
                  This run has recorded no timeline yet.
                </p>
              ) : (
                <GraphExecution
                  graph={graph}
                  onOpenNode={onSelectNode}
                  onOpenSession={onSelectItem}
                  selectedItemId={selectedItemId}
                />
              )}
            </CardContent>
          </Card>
        </div>
      </ScrollArea>
      {opened !== undefined && (
        <aside aria-label="Item detail panel" className="detail-drawer">
          <Button
            aria-label="Close detail"
            className="drawer-close"
            onClick={() => onSelectItem()}
            size="icon"
            variant="ghost"
          >
            <X />
          </Button>
          <TimelineItemDetail
            client={client}
            conversationRevision={conversationRevision}
            row={spanAsRow(opened)}
            runId={runId}
          />
        </aside>
      )}
    </div>
  );
}

/**
 * The nested plot: the line, and the rows it opens into.
 *
 * The range is held here and handed to every instance, which is the whole of what
 * "one scale" means: the upstream plot positions everything against the range it is
 * given, so a wheel or a brush anywhere reframes the lot together.
 */
function GraphExecution({
  graph,
  onOpenNode,
  onOpenSession,
  selectedItemId,
}: {
  readonly graph: ReturnType<typeof graphTimeline>;
  readonly onOpenNode: (nodeId: string) => void;
  readonly onOpenSession: (itemId?: string) => void;
  readonly selectedItemId?: string;
}) {
  // A different run is a different clock, and none of this framing follows it there:
  // selecting one leaves its timeline unread until the new one lands, so the branch
  // above renders instead and this whole region unmounts with its state. Resetting it
  // by hand as well would be a second answer nothing could tell from the first — it
  // was here, and no test could distinguish having it from not.
  //
  // A *live* run's extent grows on every poll and deliberately does not reset: a
  // reader who zoomed in asked to stay there.
  const [zoom, setZoom] = useState<readonly [number, number]>();
  const [rowsOpen, setRowsOpen] = useState(false);
  const [openRows, setOpenRows] = useState<ReadonlySet<string>>(new Set());
  const range = zoom ?? graph.range;
  const open = (segment: GraphSegment) => {
    if (segment.nodeId !== undefined) onOpenNode(segment.nodeId);
    else if (segment.conversationId !== undefined) onOpenSession(segment.id);
  };
  const lineLanes = graph.line.lanes.filter(({ id }) =>
    graph.line.items.some((item) => item.laneId === id),
  );

  return (
    <div className="graph-timeline" data-rows-open={rowsOpen}>
      <Timeline
        axis={{ origin: graph.range[0] }}
        expanded={false}
        items={graph.line.items}
        label="Graph timeline"
        lanes={lineLanes}
        markers={graph.line.markers}
        onExpandedChange={setRowsOpen}
        onRangeChange={setZoom}
        onSelect={(entry) => open(entry.payload)}
        range={range}
        selectedId={selectedItemId}
      />
      {rowsOpen && (
        <ol className="graph-rows">
          {graph.rows.map((row) => (
            <GraphRowView
              key={row.id}
              onOpen={open}
              onOpenNode={onOpenNode}
              onExpandedChange={(next) =>
                setOpenRows((current) => {
                  const updated = new Set(current);
                  if (next) updated.add(row.id);
                  else updated.delete(row.id);
                  return updated;
                })
              }
              expanded={openRows.has(row.id)}
              origin={graph.range[0]}
              range={range}
              onRangeChange={setZoom}
              row={row}
              selectedItemId={selectedItemId}
            />
          ))}
        </ol>
      )}
    </div>
  );
}

/** One row of the graph: a node, or the sessions that drove the whole run. */
function GraphRowView({
  row,
  range,
  origin,
  expanded,
  onExpandedChange,
  onRangeChange,
  onOpen,
  onOpenNode,
  selectedItemId,
}: {
  readonly row: GraphRow;
  readonly range: readonly [number, number];
  readonly origin: number;
  readonly expanded: boolean;
  readonly onExpandedChange: (expanded: boolean) => void;
  readonly onRangeChange: (range: readonly [number, number]) => void;
  readonly onOpen: (segment: GraphSegment) => void;
  readonly onOpenNode: (nodeId: string) => void;
  readonly selectedItemId?: string;
}) {
  const nodeId = row.nodeId;
  return (
    <li className="graph-row" data-row-kind={row.kind}>
      <header className="graph-row-head">
        {/* The run-level row names the sessions that drive the graph, and there is no
            node behind it to open — so it is a name rather than a control that would
            promise a reading it cannot give. */}
        {nodeId === undefined ? (
          <span className="graph-row-name">{row.label}</span>
        ) : (
          <button
            className="graph-row-name"
            onClick={() => onOpenNode(nodeId)}
            type="button"
          >
            {row.label}
          </button>
        )}
        <span className="graph-row-facts">
          {formatDuration(row.workedMs)} recorded · {formatDuration(row.idleMs)}{" "}
          idle
        </span>
      </header>
      <Timeline
        axis={{ origin }}
        expanded={expanded}
        items={expanded ? row.items : row.line}
        label={`${row.label} timeline`}
        lanes={row.lanes}
        markers={row.markers}
        onExpandedChange={onExpandedChange}
        onRangeChange={onRangeChange}
        onSelect={(entry) => onOpen(entry.payload)}
        range={range}
        selectedId={selectedItemId}
      />
    </li>
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
