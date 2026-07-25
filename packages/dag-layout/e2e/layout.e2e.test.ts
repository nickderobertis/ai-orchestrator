import { expect, test } from "bun:test";

// eslint-disable-next-line @nx/enforce-module-boundaries -- This consumer journey intentionally resolves the workspace package export.
import { layoutDag } from "@ai-orchestrator/dag-layout";

test("a package consumer receives connected geometry and routed edges", () => {
  const layout = layoutDag({
    nodes: [
      { id: "publish", label: "Publish", kind: "human", state: "waiting" },
      { id: "build", label: "Build", kind: "agent", state: "done" },
      { id: "test", label: "Test", kind: "agent", state: "running" },
    ],
    edges: [
      { id: "test-publish", source: "test", target: "publish" },
      { id: "build-test", source: "build", target: "test" },
    ],
  });

  expect(layout.nodes.map(({ id, x, y }) => ({ id, x, y }))).toEqual([
    { id: "build", x: 0, y: 0 },
    { id: "publish", x: 560, y: 0 },
    { id: "test", x: 280, y: 0 },
  ]);
  expect(layout.edges[0]?.points).toEqual([
    { x: 200, y: 36 },
    { x: 240, y: 36 },
    { x: 240, y: 36 },
    { x: 280, y: 36 },
  ]);
  expect({ width: layout.width, height: layout.height }).toEqual({
    width: 760,
    height: 72,
  });
});

test("a package consumer cannot render a cyclic graph", () => {
  expect(() =>
    layoutDag({
      nodes: [
        { id: "a", label: "A", kind: "agent", state: "pending" },
        { id: "b", label: "B", kind: "agent", state: "pending" },
      ],
      edges: [
        { id: "a-b", source: "a", target: "b" },
        { id: "b-a", source: "b", target: "a" },
      ],
    }),
  ).toThrow("cycle");
});

test("a package consumer cannot render an unsupported runtime node state", () => {
  const payload = JSON.parse(
    '{"nodes":[{"id":"agent","label":"Agent","kind":"agent","state":"paused"}],"edges":[]}',
  );

  expect(() => layoutDag(payload)).toThrow(
    "DAG node agent has unsupported state paused",
  );
});

test.each([
  {
    name: "duplicate nodes",
    nodes: [
      {
        id: "a",
        label: "A",
        kind: "agent" as const,
        state: "pending" as const,
      },
      {
        id: "a",
        label: "Again",
        kind: "agent" as const,
        state: "pending" as const,
      },
    ],
    edges: [],
    error: "node IDs must be unique",
  },
  {
    name: "duplicate edges",
    nodes: [
      {
        id: "a",
        label: "A",
        kind: "agent" as const,
        state: "pending" as const,
      },
      {
        id: "b",
        label: "B",
        kind: "agent" as const,
        state: "pending" as const,
      },
    ],
    edges: [
      { id: "edge", source: "a", target: "b" },
      { id: "edge", source: "a", target: "b" },
    ],
    error: "edge IDs must be unique",
  },
  {
    name: "missing endpoints",
    nodes: [
      {
        id: "a",
        label: "A",
        kind: "agent" as const,
        state: "pending" as const,
      },
    ],
    edges: [{ id: "edge", source: "a", target: "missing" }],
    error: "missing endpoint",
  },
  {
    name: "self edges",
    nodes: [
      {
        id: "a",
        label: "A",
        kind: "agent" as const,
        state: "pending" as const,
      },
    ],
    edges: [{ id: "edge", source: "a", target: "a" }],
    error: "self-edge",
  },
])("a package consumer rejects $name", ({ nodes, edges, error }) => {
  expect(() => layoutDag({ nodes, edges })).toThrow(error);
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
