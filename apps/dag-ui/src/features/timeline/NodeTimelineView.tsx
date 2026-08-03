import type { RunTimeline } from "@ai-orchestrator/dag-model";
import type { TelemetryClient } from "@ai-orchestrator/telemetry-client";
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Button,
  Card,
  cn,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@oneharness/ui";
import {
  ArrowLeft,
  ExternalLink,
  OctagonPause,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useMemo } from "react";
import { isUnhealthy, type NodeView, recordedReason } from "../runs/run-model";
import { StateBadge } from "../runs/StateBadge";
import {
  isNodeTab,
  NODE_TAB_LABELS,
  type NodeTab,
} from "../runs/useUrlSelection";
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
  conversationRevision,
  selectedTab,
  onSelectTab,
}: {
  readonly client: TelemetryClient;
  readonly runId: string;
  readonly node: NodeView;
  readonly timeline?: RunTimeline;
  readonly timelineError?: Error;
  readonly selectedItemId?: string;
  readonly onSelectItem: (id?: string) => void;
  readonly onBack: () => void;
  readonly conversationRevision?: number;
  readonly selectedTab: NodeTab;
  readonly onSelectTab: (tab: NodeTab) => void;
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
  const prUrl =
    node.detail?.publication === undefined
      ? (node.result?.pr ?? undefined)
      : node.detail.publication.pr_url;

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
          <StateBadge state={node.status} />
          <span className="node-view-meta">
            {node.kind} node · {node.telemetry?.turns ?? 0} turns ·{" "}
            {node.telemetry?.lint ?? 0} lint turns
          </span>
          {prUrl !== undefined && (
            <a
              className="node-view-pr"
              href={prUrl}
              rel="noreferrer"
              target="_blank"
            >
              Pull request <ExternalLink size={12} />
            </a>
          )}
        </div>
      </header>

      <NodeProblemBanner node={node} />

      <Tabs
        className="node-tabs"
        onValueChange={(value) => {
          if (isNodeTab(value)) onSelectTab(value);
        }}
        value={selectedTab}
      >
        <TabsList aria-label="Node details" variant="line">
          {Object.entries(NODE_TAB_LABELS).map(([value, label]) => (
            <TabsTrigger key={value} value={value}>
              {label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent className="node-tab-panel" value="task">
          <pre>{node.task.task}</pre>
        </TabsContent>
        <TabsContent className="node-tab-panel" value="criteria">
          <pre>{node.task.done_when ?? "No completion criteria recorded."}</pre>
        </TabsContent>
        <TabsContent className="node-tab-panel" value="dependencies">
          <dl className="facts">
            <div>
              <dt>Dependencies</dt>
              <dd>{node.task.deps?.join(", ") || "None"}</dd>
            </div>
          </dl>
        </TabsContent>
        <TabsContent className="node-tab-panel" value="pr">
          <dl className="facts">
            <div>
              <dt>Publication</dt>
              <dd>
                <Publication node={node} />
              </dd>
            </div>
            <div>
              <dt>Outcome</dt>
              <dd>{formatValue(node.result?.detail)}</dd>
            </div>
          </dl>
        </TabsContent>
        <TabsContent className="node-tab-panel" value="checks">
          <dl className="facts">
            <div>
              <dt>Verification coverage</dt>
              <dd>
                Hook:{" "}
                {node.detail?.verification.pre_push_hook
                  ? "present"
                  : "not recorded"}
                {" · "}Required checks:{" "}
                {node.detail?.verification.required_checks?.join(", ") ||
                  "none configured"}
              </dd>
            </div>
            <div>
              <dt>Observed checks</dt>
              <dd>
                {(node.detail?.verification.checks ?? []).length === 0
                  ? "No checks observed"
                  : node.detail?.verification.checks?.map((check, index) => (
                      <span key={check.name}>
                        {index > 0 && " · "}
                        {check.url ? (
                          <a href={check.url} rel="noreferrer" target="_blank">
                            {check.name}: {check.state}
                          </a>
                        ) : (
                          `${check.name}: ${check.state}`
                        )}
                      </span>
                    ))}
              </dd>
            </div>
          </dl>
        </TabsContent>
        <TabsContent className="node-timeline-panel" value="timeline">
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
                conversationRevision={conversationRevision}
                node={node}
                row={selected}
                runId={runId}
              />
            </div>
          )}
        </TabsContent>
      </Tabs>
    </section>
  );
}

function Publication({ node }: { readonly node: NodeView }) {
  const publication = node.detail?.publication;
  if (publication === undefined) {
    return typeof node.result?.pr === "string" && node.result.pr ? (
      <a
        className="node-view-pr"
        href={node.result.pr}
        rel="noreferrer"
        target="_blank"
      >
        Pull request <ExternalLink size={12} />
      </a>
    ) : (
      <>Not recorded</>
    );
  }
  const links = publication.merged
    ? [
        [publication.pr_url, "Pull request"],
        [
          publication.commit_url,
          `Commit ${publication.commit?.slice(0, 8) ?? ""}`,
        ],
      ]
    : [
        [publication.pr_url, "Pull request"],
        [publication.branch_url, publication.branch ?? "Branch"],
      ];
  const renderedLinks = links.filter(([url]) => url);
  if (renderedLinks.length === 0) return <>Not recorded</>;
  return (
    <>
      {renderedLinks.map(([url, label], index) => (
        <span key={url}>
          {index > 0 && " · "}
          <a
            className="node-view-pr"
            href={url}
            rel="noreferrer"
            target="_blank"
          >
            {label} <ExternalLink size={12} />
          </a>
        </span>
      ))}
    </>
  );
}

/** Leads an unhealthy node's detail view with its server-recorded reason. */
function NodeProblemBanner({ node }: { readonly node: NodeView }) {
  if (!isUnhealthy(node.status)) return null;
  // Not one condition: `blocked` moves when a person acts and `skipped` never will.
  // They share a banner because neither is this node's own failure — the heading
  // below states which of the two it is, and the term list names the difference.
  const dependencyDecided =
    node.status === "blocked" || node.status === "skipped";
  // The same chain the graph card's line reads, so the card and the view it opens
  // cannot explain one failure two ways.
  const detail = recordedReason(node);
  // `|| undefined`, not `?? undefined`: a recorded empty string is a field the run
  // wrote nothing into, and treating it as a reason would head a term with no value.
  const error = node.result?.error || undefined;
  const exitCode = node.result?.exit_code;
  return (
    // `Alert` carries `role="alert"` itself, so opening a node that is in trouble
    // announces what is wrong rather than leaving it to be noticed.
    // Held work takes the warning tone its badge and card already carry, not the
    // failure's red: it has not gone wrong, it is waiting on something that has.
    <Alert
      className={cn(
        "m-5 w-auto",
        dependencyDecided && "border-warning bg-warning-surface text-warning",
      )}
      variant={dependencyDecided ? "default" : "destructive"}
    >
      {dependencyDecided ? <OctagonPause /> : <TriangleAlert />}
      <AlertTitle>
        {dependencyDecided
          ? `This node is ${node.status}`
          : `This node ${node.status === "cancelled" ? "was cancelled" : node.status === "not-completed" ? "did not complete" : "failed"}${
              node.failure ? `: ${node.failure.class}` : ""
            }`}
      </AlertTitle>
      <AlertDescription>
        <dl className="facts">
          {dependencyDecided && (
            <div>
              <dt>{node.status === "skipped" ? "Unmet" : "Blocked by"}</dt>
              <dd>
                {node.blockers.length > 0
                  ? node.blockers.join(", ")
                  : "Nothing recorded; the run has not written what holds it."}
              </dd>
            </div>
          )}
          {!dependencyDecided && (
            <div>
              <dt>Detail</dt>
              <dd>{detail ?? "No reason was recorded for this outcome."}</dd>
            </div>
          )}
          {/* The recorded error is shown beside the detail rather than instead of
              it: a lifecycle records prose in `detail` and the scheduler records the
              exception text in `error`, and they are usually not the same sentence.
              When they are, the chain above already led with it. */}
          {error !== undefined && error !== detail && (
            <div>
              <dt>Error</dt>
              <dd>{error}</dd>
            </div>
          )}
          {typeof exitCode === "number" && (
            <div>
              <dt>Exit code</dt>
              <dd>{exitCode}</dd>
            </div>
          )}
        </dl>
      </AlertDescription>
    </Alert>
  );
}

function formatValue(value: unknown): string {
  if (value === undefined || value === null || value === "")
    return "Not recorded";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}
