import { layoutDag } from "@ai-orchestrator/dag-layout";
import {
  Background,
  Controls,
  type Edge,
  MiniMap,
  ReactFlow,
} from "@xyflow/react";
import { useMemo } from "react";
import type { NodeView } from "../runs/run-model";
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
  const { nodes, edges } = useMemo(() => {
    const layout = layoutDag({
      nodes: nodeViews.map((node) => ({
        id: node.id,
        label: node.label,
        kind: node.kind,
        state: node.state,
      })),
      edges: nodeViews.flatMap((node) =>
        (node.task.deps ?? []).map((dependency) => ({
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
            nodeViews.find(({ id }) => id === edge.target)?.state === "running",
        }),
      ),
    };
  }, [nodeViews, selectedNodeId]);

  return (
    <div className="graph-wrap">
      <ReactFlow
        nodes={nodes}
        edges={edges}
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
        <Background color="#2b2f3a" gap={22} />
        <MiniMap
          pannable
          zoomable
          nodeColor={(node) => tokenColor(statusToken(node.data))}
        />
        <Controls showInteractive={false} />
      </ReactFlow>
      <ol className="accessible-node-list" aria-label="DAG nodes">
        {nodeViews.map((node) => (
          <li key={node.id}>
            <button type="button" onClick={() => onSelectNode(node.id)}>
              {node.label}: {node.state}
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

function tokenColor(token: string): string {
  if (token === "success") return "#38d39f";
  if (token === "danger") return "#ff6b72";
  if (token === "active") return "#d9ff72";
  if (token === "blocked") return "#f2bd68";
  return "#72798a";
}

function statusToken(data: Record<string, unknown>): string {
  return typeof data.style === "string" ? data.style : "neutral";
}
