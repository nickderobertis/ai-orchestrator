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
  WORKER_SESSION,
  workerConversation,
  workerTurnsTimeline,
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

// A session that is still being written moves both payloads at once, and the views
// that follow one read the other; a fixture that grew only one would prove nothing.
test("a growing worker session grows its transcript and its timeline together", () => {
  expect(
    dagConversationSchema.parse(workerConversation(3)).conversation.turns,
  ).toHaveLength(3);
  const dispatched = parseRunTimeline(workerTurnsTimeline(3)).spans.filter(
    ({ id }) => id === `dispatch-${WORKER_SESSION}`,
  );
  expect(dispatched).toHaveLength(1);
  expect(dispatched[0]?.events).toHaveLength(3);
});
