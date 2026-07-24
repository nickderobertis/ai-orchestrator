import { describe, expect, test } from "bun:test";

import { layoutDag } from "./index.js";

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
    expect(layout.edges[0]?.points).toEqual([
      { x: 200, y: 36 },
      { x: 240, y: 36 },
      { x: 240, y: 36 },
      { x: 280, y: 36 },
    ]);
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
});
