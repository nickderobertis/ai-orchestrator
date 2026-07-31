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

test("selecting a run clears the node and view", () => {
  window.history.replaceState(null, "", "/?run=run-1&node=build&view=overall");
  const { result } = renderHook(() => useUrlSelection());
  act(() => result.current.selectRun("run-2"));
  expect(result.current.runId).toBe("run-2");
  expect(result.current.nodeId).toBeUndefined();
  expect(result.current.view).toBe("graph");
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
