import { readFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import { FIXTURE_WORKSPACE } from "../playwright.config";

/**
 * What the fixture wrote, read from the file it publishes beside its runs.
 *
 * `e2e/fixtures/serve_fixture.py` is the one source of these ids; naming them again
 * in a spec would be a second one that drifts the moment the fixture changes.
 */
const fixtureSchema = z.object({
  runs: z.object({
    live: z.string().min(1),
    history: z.string().min(1),
    outcomes: z.string().min(1),
    legacy: z.string().min(1),
    sibling: z.string().min(1),
    unattributed: z.string().min(1),
    eventless: z.string().min(1),
    busy: z.string().min(1),
  }),
  foundation_pr: z.string().min(1),
});

export type FixtureFacts = z.infer<typeof fixtureSchema>;

let cached: FixtureFacts | undefined;

/** Everything the fixture published about the runs it is serving. */
export function fixture(): FixtureFacts {
  if (cached === undefined) {
    cached = fixtureSchema.parse(
      JSON.parse(
        readFileSync(join(FIXTURE_WORKSPACE, "fixture-facts.json"), "utf8"),
      ),
    );
  }
  return cached;
}

/** Every run the fixture wrote, and nothing else. */
export function runs(): FixtureFacts["runs"] {
  return fixture().runs;
}
