#!/usr/bin/env bash
# Serve the published DAG Observatory against a running read API.
#
# The view is no longer built here: `onepipeline-ui` publishes it as a static
# bundle, pinned in `package.json` at the release `config/onepipeline-ui.version`
# declares, and this serves that bundle. Start the API alongside it with
# `just telemetry-server`; `DAG_UI_API_URL` points somewhere other than its
# loopback default.
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"

# Bun is not running a build here, but the bundle arrives through the same locked
# install every other worktree-local dependency does, and a freshly created
# worktree has none. This is the self-heal `scripts/nx.sh` performs.
"$script_dir/workspace-install.sh" || exit 1

# Where the bundle is and which address the read API answers on are both the
# server's to know, from their own one source; this recipe only starts it.
# llmlint: ignore[tool_output_is_signal] The served URL and the browser's requests are what this foreground command is for.
exec bun "$script_dir/dag-ui-server.js"
