import { layoutDag } from "@ai-orchestrator/dag-layout";
import { Background, Controls, type Edge, ReactFlow } from "@xyflow/react";
import { useMemo } from "react";
import { isUnhealthy, type NodeView, nodeReason } from "../runs/run-model";
import { type DagFlowNode, DagNodeCard } from "./DagNodeCard";
import { DagRoutedEdge } from "./DagRoutedEdge";

const nodeTypes = { dagNode: DagNodeCard };
const edgeTypes = { routed: DagRoutedEdge };

export function DagGraph({
  nodes: nodeViews,
  selectedNodeId,
  onSelectNode,
}: {
  readonly nodes: readonly NodeView[];
  readonly selectedNodeId?: string;
  readonly onSelectNode: (nodeId: string) => void;
}) {
  // The one-line reason a card and the list beside it both state, keyed by node so
  // the layout's own sorted order cannot pair a card with another node's reason.
  const reasons = useMemo(() => {
    const found = new Map<string, string | undefined>();
    for (const node of nodeViews) {
      if (isUnhealthy(node.status)) found.set(node.id, nodeReason(node));
    }
    return found;
  }, [nodeViews]);
  const { nodes, edges } = useMemo(() => {
    const drawn = new Set(nodeViews.map((node) => node.id));
    const layout = layoutDag({
      nodes: nodeViews.map((node) => ({
        id: node.id,
        label: node.label,
        kind: node.kind,
        state: node.status,
      })),
      edges: nodeViews.flatMap((node) =>
        (node.task.deps ?? [])
          // A cross-DAG dependency (`run:<run_id>#<node_id>`) names a node in
          // another run, which this graph cannot draw an edge to. The node detail
          // still lists it, so the prerequisite stays visible.
          .filter((dependency) => drawn.has(dependency))
          .map((dependency) => ({
            id: `${dependency}->${node.id}`,
            source: dependency,
            target: node.id,
          })),
      ),
    });
    return {
      nodes: layout.nodes.map(
        (node): DagFlowNode => ({
          id: node.id,
          type: "dagNode",
          position: { x: node.x, y: node.y },
          data: {
            label: node.label,
            kind: node.kind,
            state: node.state,
            style: node.style,
            selected: node.id === selectedNodeId,
            ...(reasons.get(node.id) === undefined
              ? {}
              : { reason: reasons.get(node.id) }),
          },
          style: { width: node.width, height: node.height },
        }),
      ),
      edges: layout.edges.map(
        (edge): Edge => ({
          id: edge.id,
          source: edge.source,
          target: edge.target,
          type: "routed",
          data: { points: edge.points },
          animated:
            nodeViews.find(({ id }) => id === edge.target)?.status ===
            "running",
        }),
      ),
    };
  }, [nodeViews, reasons, selectedNodeId]);

  return (
    <div className="graph-wrap">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        // React Flow scopes its own variables to `.react-flow`, so the document-level
        // `dark` class never reaches its canvas chrome; this is its own switch for it.
        colorMode="dark"
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        fitView
        minZoom={0.35}
        onNodeClick={(_, node) => onSelectNode(node.id)}
        aria-label="DAG execution graph"
      >
        {/* No minimap: these graphs are a handful of nodes that fit the canvas, so a
            second miniature of them was chrome over the thing being read. */}
        <Background color="var(--border)" gap={22} />
        <Controls showInteractive={false} />
      </ReactFlow>
      {/* The keyboard path to the graph, and the only reading of it available without
          the canvas: it states the same authoritative status each card paints, and the
          same reason each card truncates, so nothing here is pointer-only. */}
      <ol className="accessible-node-list" aria-label="DAG nodes">
        {nodeViews.map((node) => (
          <li key={node.id}>
            <button type="button" onClick={() => onSelectNode(node.id)}>
              {node.label}: {node.status}
              {reasons.get(node.id) === undefined
                ? ""
                : ` — ${reasons.get(node.id)}`}
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
