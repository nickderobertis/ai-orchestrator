import {
  dagConversationSchema,
  parseRunDetail,
  parseRunList,
  parseRunTimeline,
} from "@ai-orchestrator/dag-model";
import { expect, test } from "vitest";
import {
  busyTimeline,
  HISTORY_RUN,
  LIVE_RUN,
  runDetail,
  runList,
  runTimeline,
} from "./fixtures";

// A unit fixture that drifts from the served contract would let the views under
// test pass against a payload the real read API can never produce.
test("every fixture payload satisfies the published read-API contract", () => {
  expect(parseRunList(runList).runs).toHaveLength(2);
  expect(parseRunDetail(runDetail(LIVE_RUN)).run.run_id).toBe(LIVE_RUN);
  expect(parseRunDetail(runDetail(HISTORY_RUN)).rounds).toHaveLength(1);
  expect(parseRunTimeline(runTimeline(LIVE_RUN)).spans.length).toBeGreaterThan(
    5,
  );
  expect(parseRunTimeline(runTimeline(HISTORY_RUN)).run_id).toBe(HISTORY_RUN);
  expect(parseRunTimeline(busyTimeline(200)).spans.length).toBeGreaterThan(200);
  for (const conversation of runDetail(LIVE_RUN).conversations) {
    expect(dagConversationSchema.parse(conversation).conversation.id).toBe(
      conversation.conversation.id,
    );
  }
});
