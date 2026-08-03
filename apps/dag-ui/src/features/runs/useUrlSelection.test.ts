import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test } from "vitest";
import { useUrlSelection } from "./useUrlSelection";

beforeEach(() => window.history.replaceState(null, "", "/"));
afterEach(cleanup);

test("reads the selection from the query string", () => {
  window.history.replaceState(
    null,
    "",
    "/?run=run-1&node=build&view=overall&event=event-7",
  );
  const { result } = renderHook(() => useUrlSelection());
  expect(result.current.runId).toBe("run-1");
  expect(result.current.nodeId).toBe("build");
  expect(result.current.itemId).toBe("event-7");
  expect(result.current.view).toBe("overall");
});

test("lands on the overall view when the address names none", () => {
  const { result } = renderHook(() => useUrlSelection());
  expect(result.current.view).toBe("overall");
});

test("opens the node a bookmark names, whether or not it names a view", () => {
  window.history.replaceState(null, "", "/?run=run-1&node=build");
  const { result } = renderHook(() => useUrlSelection());
  expect(result.current.nodeId).toBe("build");
  expect(result.current.view).toBe("graph");
});

test("carries the opened moment of a node's execution", () => {
  window.history.replaceState(null, "", "/?run=run-1&node=build");
  const { result } = renderHook(() => useUrlSelection());
  act(() => result.current.selectItem("dispatch-worker"));
  expect(window.location.search).toContain("event=dispatch-worker");
  expect(result.current.itemId).toBe("dispatch-worker");
  act(() => result.current.selectItem(undefined));
  expect(result.current.itemId).toBeUndefined();
  // Another node recorded different work, so the moment cannot survive the move.
  act(() => result.current.selectItem("dispatch-worker"));
  act(() => result.current.selectNode("ship"));
  expect(result.current.itemId).toBeUndefined();
  expect(result.current.nodeId).toBe("ship");
});

test("defaults, deep-links, and clears the selected node tab", () => {
  window.history.replaceState(null, "", "/?run=run-1&node=build&tab=checks");
  const { result } = renderHook(() => useUrlSelection());
  expect(result.current.nodeTab).toBe("checks");
  act(() => result.current.selectNodeTab("task"));
  expect(result.current.nodeTab).toBe("task");
  expect(window.location.search).toContain("tab=task");
  act(() => result.current.selectNodeTab("timeline"));
  expect(result.current.nodeTab).toBe("timeline");
  expect(window.location.search).not.toContain("tab=");
});

test("selecting a run clears the node and keeps the reading", () => {
  window.history.replaceState(null, "", "/?run=run-1&node=build&view=overall");
  const { result } = renderHook(() => useUrlSelection());
  act(() => result.current.selectRun("run-2"));
  expect(result.current.runId).toBe("run-2");
  expect(result.current.nodeId).toBeUndefined();
  // A reader comparing two runs on the overall view stays on it; only the node,
  // which belonged to the run being left, cannot survive the move.
  expect(result.current.view).toBe("overall");
});

test("selecting and clearing a node moves back to the graph view", () => {
  const { result } = renderHook(() => useUrlSelection());
  act(() => result.current.showOverall());
  expect(result.current.view).toBe("overall");
  act(() => result.current.selectNode("build"));
  expect(result.current.nodeId).toBe("build");
  expect(result.current.view).toBe("graph");
  act(() => result.current.selectNode(undefined));
  expect(result.current.nodeId).toBeUndefined();
  // Leaving a node is a walk back to the graph it sits in, not to the landing view.
  expect(result.current.view).toBe("graph");
});

test("follows a history navigation the browser performs itself", () => {
  const { result } = renderHook(() => useUrlSelection());
  act(() => result.current.selectRun("run-2"));
  expect(result.current.runId).toBe("run-2");
  act(() => {
    // What the browser does for a back button: change the URL, then announce it.
    window.history.replaceState(null, "", "/?run=run-1&node=build");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(result.current.runId).toBe("run-1");
  expect(result.current.nodeId).toBe("build");
});
