#!/usr/bin/env bash
# Photograph the DAG Observatory at every viewport in the matrix, into a gallery of
# this invocation's own.
#
# The operator iterates on this UI visually and cannot otherwise see it while a change
# is being made: a polish problem at one width stays invisible until somebody starts
# the app by hand at that width. This runs the browser tier's own fixture server and
# Vite stack (`apps/dag-ui/screenshots.config.ts` reuses `playwright.config.ts`, which
# chooses free ports and a fixture directory per run), so two of these at once is two
# independent stacks — and the gallery directory is made here, per invocation, for the
# same reason: the one thing the Playwright configs do not already keep apart.
#
# The gallery is named exactly once, by whichever ending the run reaches: the path on
# success, and on failure the same path alongside what went wrong — so a capture that
# died part way through still says where its partial images are.
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
repo_root="$(dirname -- "$script_dir")"

# Nx is not involved, but Playwright is, and a freshly created worktree has no
# `node_modules` at all. This is the same self-heal every `scripts/nx.sh` performs.
"$script_dir/workspace-install.sh" || exit 1

gallery_root="$repo_root/apps/dag-ui/.screenshots"
mkdir -p "$gallery_root" || {
    echo "dag-ui-screens: cannot create the gallery root '$gallery_root'; repair its parent permissions and retry" >&2
    exit 1
}
gallery="$(mktemp -d "$gallery_root/gallery-XXXXXXXX")" || {
    echo "dag-ui-screens: cannot create a gallery directory beneath '$gallery_root'; free some space and retry" >&2
    exit 1
}
status=0
# llmlint: ignore[tool_output_is_signal] Playwright's per-capture progress is what tells the operator which surfaces have been photographed while the tier runs.
(cd "$repo_root/apps/dag-ui" && DAG_UI_SCREENSHOT_DIR="$gallery" bunx playwright test --config screenshots.config.ts "$@") || status=$?

if [ "$status" -eq 0 ]; then
    echo "dag-ui-screens: gallery at $gallery/index.html"
    exit 0
fi
echo "dag-ui-screens: playwright exited $status without capturing every surface; the surface it stopped on is named in its output above (a run that reached none of them is usually the fixture server or Vite failing to start, which that output also reports). Fix what it names and rerun 'just dag-ui-screens'; whatever it managed is at $gallery" >&2
exit 1
