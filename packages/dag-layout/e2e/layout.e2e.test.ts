import { expect, test } from "bun:test";

// eslint-disable-next-line @nx/enforce-module-boundaries -- This consumer journey intentionally resolves the workspace package export.
import { layoutDag } from "@ai-orchestrator/dag-layout";

test("equivalent reordered inputs produce the checked-in serialized golden", async () => {
  const fixture = (await Bun.file(
    new URL("./fixtures/layout-input.json", import.meta.url),
  ).json()) as Parameters<typeof layoutDag>[0];
  const golden = await Bun.file(
    new URL("./fixtures/layout-output.json", import.meta.url),
  ).text();
  const first = `${JSON.stringify(layoutDag(fixture), null, 2)}\n`;
  const reordered = {
    nodes: [...fixture.nodes].reverse(),
    edges: [...fixture.edges].reverse(),
  };

  expect(first).toBe(golden);
  expect(`${JSON.stringify(layoutDag(reordered), null, 2)}\n`).toBe(golden);
});

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
  expect(layout.nodes.map(({ id, style }) => ({ id, style }))).toEqual([
    { id: "build", style: "success" },
    { id: "publish", style: "blocked" },
    { id: "test", style: "active" },
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

test("a package consumer receives edges in stable ID order", () => {
  const layout = layoutDag({
    nodes: [
      { id: "build", label: "Build", kind: "agent", state: "done" },
      { id: "publish", label: "Publish", kind: "human", state: "waiting" },
      { id: "test", label: "Test", kind: "agent", state: "running" },
    ],
    edges: [
      { id: "test-publish", source: "test", target: "publish" },
      { id: "build-publish", source: "build", target: "publish" },
      { id: "build-test", source: "build", target: "test" },
    ],
  });

  expect(layout.edges.map(({ id }) => id)).toEqual([
    "build-publish",
    "build-test",
    "test-publish",
  ]);
});

test("a package consumer receives the longest dependency rank", () => {
  const layout = layoutDag({
    nodes: [
      { id: "build", label: "Build", kind: "agent", state: "done" },
      { id: "publish", label: "Publish", kind: "human", state: "waiting" },
      { id: "test", label: "Test", kind: "agent", state: "running" },
    ],
    edges: [
      { id: "build-publish", source: "build", target: "publish" },
      { id: "build-test", source: "build", target: "test" },
      { id: "test-publish", source: "test", target: "publish" },
    ],
  });

  expect(layout.nodes.find(({ id }) => id === "publish")).toMatchObject({
    x: 560,
  });
});

test("a package consumer receives a vertically routed edge", () => {
  const layout = layoutDag({
    nodes: [
      { id: "build-a", label: "Build A", kind: "agent", state: "done" },
      { id: "build-b", label: "Build B", kind: "agent", state: "done" },
      { id: "test-a", label: "Test A", kind: "agent", state: "running" },
      { id: "test-b", label: "Test B", kind: "agent", state: "running" },
    ],
    edges: [
      { id: "build-a-test-a", source: "build-a", target: "test-a" },
      { id: "build-b-test-b", source: "build-b", target: "test-b" },
      { id: "build-b-test-a", source: "build-b", target: "test-a" },
    ],
  });

  expect(layout.nodes.map(({ id, y }) => ({ id, y }))).toEqual([
    { id: "build-a", y: 0 },
    { id: "build-b", y: 104 },
    { id: "test-a", y: 0 },
    { id: "test-b", y: 104 },
  ]);
  expect(
    layout.edges.find(({ id }) => id === "build-b-test-a")?.points,
  ).toEqual([
    { x: 200, y: 140 },
    { x: 240, y: 140 },
    { x: 240, y: 36 },
    { x: 280, y: 36 },
  ]);
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

test("a package consumer receives terminal failure style tokens", () => {
  const layout = layoutDag({
    nodes: [
      {
        id: "cancelled",
        label: "Cancelled",
        kind: "agent",
        state: "cancelled",
      },
      { id: "failed", label: "Failed", kind: "agent", state: "failed" },
    ],
    edges: [],
  });

  expect(layout.nodes.map(({ id, style }) => ({ id, style }))).toEqual([
    { id: "cancelled", style: "muted" },
    { id: "failed", style: "danger" },
  ]);
});
