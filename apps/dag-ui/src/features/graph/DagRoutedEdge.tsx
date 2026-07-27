import type { Point } from "@ai-orchestrator/dag-layout";
import { BaseEdge, type Edge, type EdgeProps } from "@xyflow/react";

interface RoutedEdgeData extends Record<string, unknown> {
  readonly points: readonly Point[];
}

type RoutedFlowEdge = Edge<RoutedEdgeData, "routed">;

export function DagRoutedEdge({
  data,
  id,
  markerEnd,
  style,
}: EdgeProps<RoutedFlowEdge>) {
  return (
    <BaseEdge
      id={id}
      path={toPath(data?.points ?? [])}
      markerEnd={markerEnd}
      style={style}
    />
  );
}

function toPath(points: readonly Point[]): string {
  const [first, ...rest] = points;
  if (!first) return "";
  return `M ${first.x} ${first.y} ${rest
    .map((point) => `L ${point.x} ${point.y}`)
    .join(" ")}`;
}
