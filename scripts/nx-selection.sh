#!/usr/bin/env bash
# The Nx selection arguments `just check` narrows its deterministic tier with.
#
# The base is `scripts/comparison-base.sh`'s — the ref the judged tier and the
# publishing push already use — rather than Nx's own `defaultBase`, which in a
# publication clone is the commit being pushed: an empty diff, and a gate selecting
# nothing at the moment it decides whether work reaches a remote. AGENTS.md carries
# why narrowing is sound at all.
set -euo pipefail

script_dir=${0%/*}
if [[ $script_dir == "$0" ]]; then script_dir=.; fi

# The status `comparison-base.sh` refuses with when this tree has no base to offer — no
# such remote, no unique branch to discover. A fresh copy of this tree is in that state,
# so it is the one status that falls back to running every project, and the fallback says
# nothing: `just check` reports the selection it made either way, and there is nothing
# for a reader to repair. Every other refusal names a base that was asked for and cannot
# be used, and reaches the branch below with its own report — checking everything in
# silence there would leave a misconfigured comparison identity looking like a fresh copy.
# This status is a contract between the two scripts, and
# `tests/e2e/test_workspace_contract_e2e.py` reconciles it against what that one exits with.
readonly NO_BASE_AVAILABLE=2

status=0
base=$("$script_dir/comparison-base.sh" 2>/dev/null) || status=$?

if (( status == 0 )); then
  printf 'affected --base %s\n' "$base"
elif (( status == NO_BASE_AVAILABLE )); then
  printf 'run-many\n'
else
  # Asked again with its own stderr let through: it reads and decides nothing, and its
  # report is the only account of which base was refused and how it is repaired.
  "$script_dir/comparison-base.sh" >/dev/null || true
  echo "nx-selection: scripts/comparison-base.sh named no base to narrow against (exit $status), so which projects to check is undecided; repair what it reported above and retry" >&2
  exit 1
fi
