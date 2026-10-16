#!/usr/bin/env bash
# Refuse to run in a checkout session setup has not provisioned, and keep `uv run` from
# provisioning it instead. Sourced by every script that runs `uv run` — `scripts/nx.sh`
# among them — and run by the justfile's private `_provisioned` recipe, which its
# uv-running recipes depend on; either way it ends with the status its check earned.
#
# `UV_NO_SYNC` is what stops a `uv run` syncing the environment on its way into a
# command, because session setup is the one place this checkout installs anything. But
# under it, a uv that finds no environment at all creates an empty one and runs whatever
# the `PATH` offers — another checkout's tools, silently. So the environment uv would
# use is required to hold an interpreter, and its absence is refused naming the recipe
# that provisions it. Whether that environment is *current* is not asked: that is
# session setup's own verification, and nothing here installs anything.
#
# A checkout with no `uv.lock` has no project environment for uv to use, and passes:
# a TypeScript-only tree the real `scripts/nx.sh` is copied into and run in is one.

# Strict only when run as its own process: sourced, it leaves the caller's shell options
# as the caller set them.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    set -euo pipefail
fi
export UV_NO_SYNC=1

require_provisioned() {
    local root environment
    root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)" || return 1
    [[ -f "$root/uv.lock" ]] || return 0
    # uv's own resolution: `UV_PROJECT_ENVIRONMENT` when set, a relative one read
    # against the project root, and `<root>/.venv` otherwise. The workspace contract
    # journeys reconcile the relative reading against the installed uv.
    environment="${UV_PROJECT_ENVIRONMENT:-$root/.venv}"
    [[ "$environment" == /* ]] || environment="$root/$environment"
    if [[ ! -x "$environment/bin/python" ]]; then
        echo "require-provisioned: '$environment' holds no Python environment (no executable bin/python), so this checkout is not provisioned; run 'just session-setup' in '$root', or point UV_PROJECT_ENVIRONMENT at a provisioned environment, and retry" >&2
        return 1
    fi
}

require_provisioned
