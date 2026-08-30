#!/usr/bin/env bash
# Give every checkout of one repository the same local Nx cache without putting
# generated state in a worktree. Nx's content hash still guards every replay.
# llmlint: ignore-file[boundary_inputs_validated] This is the trusted local operator command
# surface: XDG_CACHE_HOME/HOME are platform-owned directory roots, and arguments are intentionally
# Nx's own validated CLI. No value is interpreted as repository content or passed to eval.
# llmlint: ignore-file[changed_behavior_has_e2e] scripts/check-nx-cache.sh exercises this wrapper
# through two real linked worktrees, including a cache hit and a changed-source typecheck failure.
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=scripts/preserved-log.sh
. "$script_dir/preserved-log.sh"

# Nx lives in `node_modules/.bin`, which a freshly created worktree does not have.
# Healing it here rather than in one recipe is what stops the same missing install
# from failing `just lint` with "Could not find Nx modules" while `just check`
# quietly repaired it — and what lets a bare `pytest` drive these targets without
# an operator running Bun by hand first. It exits immediately once the installed
# tree matches the lockfile, which is Bun's own answer rather than this wrapper's.
"$script_dir/workspace-install.sh" || exit 1

# The other half of the same self-heal. Every target below runs its tool through
# `uv run`, and `.venv` is ignored state a fresh worktree or publication clone
# arrives without — where a reader of `<root>/.venv/bin` does not fail but falls
# through to another checkout's environment, which is how a gate came to verify a
# branch against versions the branch had already moved. It exits immediately once
# the environment matches the lockfile.
"$script_dir/python-install.sh" || exit 1

# The third, and the one a shared host path used to hide. The plan-store CLI is
# installed into `<root>/.venv/bin` so a checkout reads the release it pinned rather
# than whichever checkout on the host provisioned last — and nothing but this repository
# puts one there, so a fresh worktree or publication clone arrives with none and every
# recipe and test that reads the plan store fails on the missing file. It exits
# immediately once the binary already reports this checkout's pin.
"$script_dir/onetaskgraph-install.sh" || exit 1

repo_identity="$(git config --get remote.origin.url || git rev-parse --show-toplevel)" || { echo "nx: cannot resolve repository identity; run from a Git checkout and retry" >&2; exit 1; }
repo_key="$(printf '%s' "$repo_identity" | sha256sum | cut -c1-16)" || { echo "nx: cannot derive the repository cache key; verify sha256sum is available and retry" >&2; exit 1; }
[[ "$repo_key" =~ ^[0-9a-f]{16}$ ]] || { echo "nx: derived an invalid repository cache key; verify sha256sum output and retry" >&2; exit 1; }
cache_root="${XDG_CACHE_HOME:-${HOME}/.cache}/ai-orchestrator/nx/${repo_key}"
export NX_CACHE_DIRECTORY="$cache_root"

# The *other* Nx cache, keyed the same way so it grows per repository rather than per
# worktree: Nx copies its 22 MB native module into a directory named from the workspace
# root, and every dispatch here works in a new one. A sibling of the computation cache
# rather than that directory itself, because Nx creates `NX_CACHE_DIRECTORY` only when
# it stores a task result — which is what lets a metadata-only invocation leave nothing
# behind — while the native loader creates its own on every invocation. See AGENTS.md,
# "The computation cache those targets use".
export NX_NATIVE_FILE_CACHE_DIRECTORY="${XDG_CACHE_HOME:-${HOME}/.cache}/ai-orchestrator/nx-native/${repo_key}"

# Nx's daemon is off by default here, and any daemon that is turned back on uses
# the installed Nx for its own housekeeping.
#
# A daemon primes an "are AI agents configured?" cache the instant it starts by
# installing `nx@latest` into a fresh system-temp directory — ~80 MB, logged as
# `[LATEST-NX]: Pulling latest Nx...` — and removes it only on a *graceful*
# shutdown. There is one daemon per workspace root and the e2e suite copies this
# checkout per journey, so one worktree accumulated dozens of resident daemons
# (8.9 GB RSS) and the host accumulated ~110 GB of abandoned installs in a day.
# The daemon buys about 0.1s per invocation on this workspace, which does not pay
# for that; `NX_DAEMON=true` still turns it back on for anyone who wants it.
# `NX_USE_LOCAL` is the independent guard that keeps such a daemon from pulling a
# private copy at all. It also makes `nx migrate` plan from the installed Nx
# rather than the latest release, which is the right trade for a wrapper whose job
# is running this workspace's own targets.
export NX_DAEMON="${NX_DAEMON-false}"
export NX_USE_LOCAL=true

# The Bun-written shim rather than a path inside the package: Nx has moved its
# bin entry between releases, and the shim is the one name that cannot. The
# locked install above guarantees one of these exists before every invocation.
NX_BIN="node_modules/.bin/nx"
[[ -x "$NX_BIN" ]] || NX_BIN="node_modules/.bin/nx.cmd"
if [[ ! -x "$NX_BIN" ]]; then
  echo "nx: locked workspace install did not provide an executable node_modules/.bin/nx or node_modules/.bin/nx.cmd; repair the install and retry" >&2
  exit 1
fi

# The log outlives this process on purpose: a failing run needs its full output
# after the fact, and a *running* one has to be inspectable (`tail -f`) without
# reading this process's file descriptors through /proc.
preserved_log_open "$(dirname -- "$script_dir")" nx || exit 1
log=$PRESERVED_LOG
if "$NX_BIN" "$@" 2>&1 | redact_secrets >"$log"; then
  # llmlint: ignore[tool_output_is_signal] Explicit debug output lets the cache-contract check inspect Nx's success evidence, and lets `just lint-llm-diff` show the judge report Nx replayed; default successful invocations still emit one line.
  if [[ "${AI_ORCHESTRATOR_NX_SHOW_OUTPUT:-}" == "1" ]]; then cat "$log"; fi
  printf 'nx: requested targets succeeded\n'
  exit 0
fi
cat "$log" >&2
printf "nx: targets failed; fix the reported project findings and rerun the same 'just' recipe (full output: %s)\n" "$log" >&2
exit 1
