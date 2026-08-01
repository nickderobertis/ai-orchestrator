import { rmSync } from "node:fs";
import { FIXTURE_WORKSPACE } from "../playwright.config";

/**
 * Remove the fixture directory this run allocated for itself.
 *
 * A per-run directory is what keeps two concurrent runs from rebuilding each other's
 * served fixture; without this it would also be what leaves one behind per run.
 */
export default function removeFixtureWorkspace(): void {
  rmSync(FIXTURE_WORKSPACE, { recursive: true, force: true });
}
