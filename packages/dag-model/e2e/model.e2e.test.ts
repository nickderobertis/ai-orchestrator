import { expect, test } from "bun:test";

// eslint-disable-next-line @nx/enforce-module-boundaries -- This verifies the package export as a consumer uses it.
import { parseRunList, runDetailSchema } from "@ai-orchestrator/dag-model";

test("a package consumer validates an API response through the public export", () => {
  expect(
    parseRunList({
      api_version: 1,
      telemetry_schema_version: 6,
      observed_at: "2026-07-26T12:00:00Z",
      runs: [],
    }).runs,
  ).toEqual([]);
});

test("a package consumer rejects incompatible list and detail payloads", () => {
  expect(() =>
    parseRunList({
      api_version: 2,
      telemetry_schema_version: 6,
      observed_at: "2026-07-26T12:00:00Z",
      runs: [],
    }),
  ).toThrow();
  expect(
    runDetailSchema.safeParse({
      api_version: 1,
      telemetry_schema_version: 6,
      observed_at: "2026-07-26T12:00:00Z",
      run: {},
      rounds: [{ node_states: { build: "paused" } }],
      conversations: [],
    }).success,
  ).toBe(false);
});
