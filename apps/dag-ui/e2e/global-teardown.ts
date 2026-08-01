import { rmSync } from "node:fs";
import { FIXTURE_WORKSPACE } from "../playwright.config";

/**
 * Remove the fixture directory this run made for itself, and say which one.
 *
 * A per-run directory is what keeps two concurrent runs from rebuilding each other's
 * served fixture; without this it would also be what leaves one behind per run. The
 * line names the directory because a run that ends badly enough to skip this is a run
 * whose leftovers someone has to recognise.
 */
export default function removeAndReportFixtureWorkspace(): void {
  rmSync(FIXTURE_WORKSPACE, { recursive: true, force: true });
  process.stdout.write(
    `dag-ui e2e: removed fixture workspace ${FIXTURE_WORKSPACE}\n`,
  );
}
