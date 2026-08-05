import { describe, expect, test } from "bun:test";

import { DAG_NODE_STATES, type DagNode, layoutDag } from "./index.js";

describe("layoutDag", () => {
  test("returns stable dependency-ranked geometry through the public API", () => {
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
  });

  test("gives every served node status a token, and none of them neutral by accident", () => {
    const layout = layoutDag({
      nodes: DAG_NODE_STATES.map(
        (state): DagNode => ({ id: state, label: state, kind: "agent", state }),
      ),
      edges: [],
    });
    expect(
      Object.fromEntries(layout.nodes.map(({ id, style }) => [id, style])),
    ).toEqual({
      pending: "neutral",
      running: "active",
      waiting: "blocked",
      // Held work, not lost work: something outside it has to move. Neutral would
      // read as "nothing to report" about a node that is going nowhere.
      blocked: "blocked",
      skipped: "blocked",
      done: "success",
      "not-completed": "danger",
      failed: "danger",
      // A planner idled this one and its work is preserved, so it is inert by
      // decision rather than lost — the same reading `cancelled` gets.
      parked: "muted",
      cancelled: "muted",
      unknown: "neutral",
    });
  });

  test("rejects cycles at the package boundary", () => {
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
  ])("rejects $name at the package boundary", ({ nodes, edges, error }) => {
    expect(() => layoutDag({ nodes, edges })).toThrow(error);
  });
});
