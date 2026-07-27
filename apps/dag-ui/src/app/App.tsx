import { TelemetryClient } from "@ai-orchestrator/telemetry-client";
import { RefreshCw, Route, Satellite, Workflow } from "lucide-react";
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
            <button
              className="secondary-button"
              type="button"
              onClick={() => void telemetry.refresh()}
            >
              <RefreshCw size={14} /> Refresh
            </button>
          </div>
        </header>
        {telemetry.error && (
          <div className="error-banner" role="alert">
            Live telemetry issue: {telemetry.error.message}
          </div>
        )}
        <div className="view-tabs" role="tablist" aria-label="DAG views">
          <button
            type="button"
            role="tab"
            aria-selected={selection.view === "graph"}
            onClick={() => selection.selectNode(undefined)}
          >
            <Workflow size={15} /> Graph
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={selection.view === "overall"}
            onClick={selection.showOverall}
          >
            <Route size={15} /> Overall
          </button>
        </div>
        {telemetry.loading && !detail ? (
          <div className="loading-state" aria-live="polite">
            <span className="loader" /> Loading execution history…
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
      </main>
    </div>
  );
}
