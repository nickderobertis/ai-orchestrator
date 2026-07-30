import { TelemetryClient } from "@ai-orchestrator/telemetry-client";
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Button,
  Skeleton,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  TooltipProvider,
} from "@oneharness/ui";
import {
  RefreshCw,
  Route,
  Satellite,
  TriangleAlert,
  Workflow,
} from "lucide-react";
import { useEffect, useMemo } from "react";
import { NodeDetail } from "../features/detail/NodeDetail";
import { DagGraph } from "../features/graph/DagGraph";
import { RunNavigation } from "../features/navigation/RunNavigation";
import { OverallView } from "../features/overall/OverallView";
import { groupRuns, nodeViews } from "../features/runs/run-model";
import { useDagTelemetry } from "../features/runs/useDagTelemetry";
import { useUrlSelection } from "../features/runs/useUrlSelection";

const defaultClient = new TelemetryClient(window.location.origin, {
  fetch: window.fetch.bind(window),
});

export function App({
  client = defaultClient,
}: {
  readonly client?: TelemetryClient;
}) {
  const telemetry = useDagTelemetry(client);
  const selection = useUrlSelection();
  const groups = useMemo(
    () => groupRuns(telemetry.list?.runs ?? [], telemetry.details),
    [telemetry.list, telemetry.details],
  );
  const selectedRunId =
    selection.runId &&
    telemetry.list?.runs.some(({ run_id }) => run_id === selection.runId)
      ? selection.runId
      : telemetry.list?.runs.at(0)?.run_id;
  const detail = selectedRunId
    ? telemetry.details.get(selectedRunId)
    : undefined;
  const nodes = useMemo(() => (detail ? nodeViews(detail) : []), [detail]);
  const selectedNode = nodes.find(({ id }) => id === selection.nodeId);
  const liveRunIds = useMemo(
    () =>
      new Set(
        (telemetry.list?.runs ?? [])
          .filter(
            ({ state }) => !["complete", "failed", "cancelled"].includes(state),
          )
          .map(({ run_id }) => run_id),
      ),
    [telemetry.list],
  );

  useEffect(() => {
    if (selectedRunId && selection.runId && selection.runId !== selectedRunId) {
      const params = new URLSearchParams(window.location.search);
      params.set("run", selectedRunId);
      params.delete("node");
      window.history.replaceState(
        null,
        "",
        `${window.location.pathname}?${params}`,
      );
      window.dispatchEvent(new PopStateEvent("popstate"));
    }
  }, [selection.runId, selectedRunId]);

  return (
    <TooltipProvider>
      <div className="app-shell">
        <RunNavigation
          groups={groups}
          selectedRunId={selectedRunId}
          liveRunIds={liveRunIds}
          onSelect={selection.selectRun}
        />
        <main className="workspace">
          <header className="topbar">
            <div>
              <p className="eyebrow">Execution telemetry</p>
              <h2>{selectedRunId ?? "No DAG selected"}</h2>
            </div>
            <div className="topbar-actions">
              <span className="connection">
                <Satellite
                  size={15}
                  className={telemetry.hasUpdates ? "pulse" : ""}
                />
                {telemetry.hasUpdates ? "Updates received" : "Awaiting updates"}
              </span>
              <Button
                onClick={() => void telemetry.refresh()}
                size="sm"
                type="button"
                variant="outline"
              >
                <RefreshCw size={14} /> Refresh
              </Button>
            </div>
          </header>
          {telemetry.error && (
            // `Alert` carries `role="alert"` itself, so the banner keeps announcing
            // itself the moment a read fails.
            <Alert
              className="rounded-none border-x-0 border-t-0"
              variant="destructive"
            >
              <TriangleAlert />
              <AlertTitle>Live telemetry issue</AlertTitle>
              <AlertDescription>{telemetry.error.message}</AlertDescription>
            </Alert>
          )}
          <Tabs
            className="min-h-0 flex-1 gap-0"
            onValueChange={(value) =>
              value === "overall"
                ? selection.showOverall()
                : selection.selectNode(undefined)
            }
            value={selection.view}
          >
            <div className="view-tabs">
              <TabsList aria-label="DAG views" variant="line">
                <TabsTrigger value="graph">
                  <Workflow size={15} /> Graph
                </TabsTrigger>
                <TabsTrigger value="overall">
                  <Route size={15} /> Overall
                </TabsTrigger>
              </TabsList>
            </div>
            {/* One content region for whichever view is selected: the other tab's
                panel is unmounted by the primitive, exactly as it is for any tab set. */}
            <TabsContent className="min-h-0" value={selection.view}>
              {/* A run is selected but its detail has not arrived yet — still loading. The
                  empty state means the server serves no run at all, so it is reached only
                  once the list is known and holds none. */}
              {!detail && (telemetry.loading || selectedRunId !== undefined) ? (
                <div aria-live="polite" className="loading-state">
                  <Skeleton className="h-2 w-48" />
                  <Skeleton className="h-2 w-32" />
                  Loading execution history…
                </div>
              ) : detail ? (
                selection.view === "overall" ? (
                  <OverallView detail={detail} />
                ) : (
                  <div className="graph-layout">
                    <DagGraph
                      nodes={nodes}
                      selectedNodeId={selectedNode?.id}
                      onSelectNode={selection.selectNode}
                    />
                    {selectedNode && (
                      <NodeDetail
                        node={selectedNode}
                        onClose={() => selection.selectNode(undefined)}
                      />
                    )}
                  </div>
                )
              ) : (
                <div className="empty-state">
                  <Workflow size={34} />
                  <h2>No DAG runs found</h2>
                  <p>Start an orchestrated run to see it appear here.</p>
                </div>
              )}
            </TabsContent>
          </Tabs>
        </main>
      </div>
    </TooltipProvider>
  );
}
