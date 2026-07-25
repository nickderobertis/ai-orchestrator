import { expect, test } from "bun:test";

// This consumer journey intentionally resolves the workspace package export.
// eslint-disable-next-line @nx/enforce-module-boundaries
import { layoutDag } from "@ai-orchestrator/dag-layout";

test("a package consumer cannot render an unsupported runtime node state", () => {
  const payload = JSON.parse(
    '{"nodes":[{"id":"agent","label":"Agent","kind":"agent","state":"paused"}],"edges":[]}',
  );

  expect(() => layoutDag(payload)).toThrow(
    "DAG node agent has unsupported state paused",
  );
});

test("a package consumer gets stable rows for disconnected same-rank nodes", () => {
  const layout = layoutDag({
    nodes: [
      { id: "b", label: "B", kind: "agent", state: "pending" },
      { id: "a", label: "A", kind: "agent", state: "pending" },
    ],
    edges: [],
  });

  expect(layout.nodes.map(({ id, x, y }) => ({ id, x, y }))).toEqual([
    { id: "a", x: 0, y: 0 },
    { id: "b", x: 0, y: 104 },
  ]);
  expect({ width: layout.width, height: layout.height }).toEqual({
    width: 200,
    height: 176,
  });
});

test("a package consumer can render an empty graph", () => {
  expect(layoutDag({ nodes: [], edges: [] })).toEqual({
    width: 0,
    height: 0,
    nodes: [],
    edges: [],
  });
});
