import { parseRunDetail, parseRunList } from "@ai-orchestrator/dag-model";
import { expect, test } from "vitest";
import { HISTORY_RUN, LIVE_RUN, runDetail, runList } from "./fixtures";

// A unit fixture that drifts from the served contract would let the views under
// test pass against a payload the real read API can never produce.
test("every fixture payload satisfies the published read-API contract", () => {
  expect(parseRunList(runList).runs).toHaveLength(2);
  expect(parseRunDetail(runDetail(LIVE_RUN)).run.run_id).toBe(LIVE_RUN);
  expect(parseRunDetail(runDetail(HISTORY_RUN)).rounds).toHaveLength(1);
});
