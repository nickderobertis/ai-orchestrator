/** Stable input understood by every DAG renderer. */
export interface DagLayoutInput {
  readonly nodes: readonly DagNode[];
  readonly edges: readonly DagEdge[];
}

export interface DagNode {
  readonly id: string;
  readonly label: string;
  readonly kind: "agent" | "human" | "lifecycle";
  readonly state:
    | "pending"
    | "running"
    | "waiting"
    | "done"
    | "failed"
    | "cancelled";
}

export interface DagEdge {
  readonly id: string;
  readonly source: string;
  readonly target: string;
}

/** Renderer-neutral result; coordinates are integer CSS/SVG pixels. */
export interface DagLayout {
  readonly width: number;
  readonly height: number;
  readonly nodes: readonly PositionedNode[];
  readonly edges: readonly RoutedEdge[];
}

export interface PositionedNode extends DagNode {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface Point {
  readonly x: number;
  readonly y: number;
}

export interface RoutedEdge extends DagEdge {
  readonly points: readonly Point[];
}

const NODE_WIDTH = 200;
const NODE_HEIGHT = 72;
const COLUMN_GAP = 80;
const ROW_GAP = 32;
const NODE_STATES = new Set([
  "pending",
  "running",
  "waiting",
  "done",
  "failed",
  "cancelled",
]);

/** Lay out an acyclic graph in stable left-to-right dependency ranks. */
export function layoutDag(input: DagLayoutInput): DagLayout {
  const nodes = [...input.nodes].sort((left, right) =>
    left.id.localeCompare(right.id),
  );
  const edges = [...input.edges].sort((left, right) =>
    left.id.localeCompare(right.id),
  );
  const byId = new Map(nodes.map((node) => [node.id, node]));
  if (byId.size !== nodes.length) {
    throw new Error("DAG node IDs must be unique");
  }
  for (const node of nodes) {
    if (!NODE_STATES.has(node.state)) {
      throw new Error(
        `DAG node ${node.id} has unsupported state ${node.state}`,
      );
    }
  }

  const incoming = new Map(nodes.map((node) => [node.id, 0]));
  const outgoing = new Map(nodes.map((node) => [node.id, [] as string[]]));
  const edgeIds = new Set<string>();
  for (const edge of edges) {
    if (edgeIds.has(edge.id)) {
      throw new Error("DAG edge IDs must be unique");
    }
    edgeIds.add(edge.id);
    if (!byId.has(edge.source) || !byId.has(edge.target)) {
      throw new Error(`DAG edge ${edge.id} has a missing endpoint`);
    }
    if (edge.source === edge.target) {
      throw new Error(`DAG edge ${edge.id} is a self-edge`);
    }
    incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1);
    outgoing.get(edge.source)?.push(edge.target);
  }

  const ranks = new Map<string, number>();
  let ready = nodes
    .filter((node) => incoming.get(node.id) === 0)
    .map((node) => node.id);
  let visited = 0;
  while (ready.length > 0) {
    const current = ready;
    ready = [];
    for (const id of current) {
      visited += 1;
      const rank = ranks.get(id) ?? 0;
      for (const target of outgoing.get(id) ?? []) {
        ranks.set(target, Math.max(ranks.get(target) ?? 0, rank + 1));
        const remaining = (incoming.get(target) ?? 0) - 1;
        incoming.set(target, remaining);
        if (remaining === 0) ready.push(target);
      }
    }
    ready.sort();
  }
  if (visited !== nodes.length) {
    throw new Error("DAG contains a cycle");
  }

  const rows = new Map<number, number>();
  const positioned = nodes.map((node): PositionedNode => {
    const rank = ranks.get(node.id) ?? 0;
    const row = rows.get(rank) ?? 0;
    rows.set(rank, row + 1);
    return {
      ...node,
      x: rank * (NODE_WIDTH + COLUMN_GAP),
      y: row * (NODE_HEIGHT + ROW_GAP),
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    };
  });
  const positionedById = new Map(positioned.map((node) => [node.id, node]));
  const routed = edges.map((edge): RoutedEdge => {
    const source = positionedById.get(edge.source);
    const target = positionedById.get(edge.target);
    if (!source || !target) throw new Error("DAG layout lost an edge endpoint");
    const start = {
      x: source.x + source.width,
      y: source.y + source.height / 2,
    };
    const end = { x: target.x, y: target.y + target.height / 2 };
    const middleX = Math.round((start.x + end.x) / 2);
    return {
      ...edge,
      points: [
        start,
        { x: middleX, y: start.y },
        { x: middleX, y: end.y },
        end,
      ],
    };
  });
  const width =
    positioned.length === 0
      ? 0
      : Math.max(...positioned.map((node) => node.x + node.width));
  const height =
    positioned.length === 0
      ? 0
      : Math.max(...positioned.map((node) => node.y + node.height));
  return { width, height, nodes: positioned, edges: routed };
}
