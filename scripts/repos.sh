#!/usr/bin/env bash
# `just repos` — list the registered identities and checkouts, and audit their gates.
#
# Two things happen here that a bare `onevcs repos` does not do. The published flag is
# spelled `--audit-gates`; the planner doctrine names it `--audit-gate-coverage`, and
# this absorbs the difference. And an audit's `merge-path coverage:` lines are passed
# through `scripts/merge-path-audit.py`, which replaces each claim about what verifies
# an identity with what can still refuse its merge. See that filter for why the
# published claim cannot be read as coverage on this host.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

published=()
audit=0
for argument in "$@"; do
  case "$argument" in
    --audit-gate-coverage | --audit-gates)
      audit=1
      published+=(--audit-gates)
      ;;
    *) published+=("$argument") ;;
  esac
done

if [ "$audit" -eq 0 ]; then
  # llmlint: ignore[tool_output_is_signal] The requested registry listing is this command's whole product; reducing it would leave nothing.
  exec uv run onevcs repos ${published[@]+"${published[@]}"}
fi

# The repository's own interpreter when this is a provisioned checkout, and the
# system one otherwise — the filter is stdlib-only precisely so both work.
python="$root/.venv/bin/python3"
[ -x "$python" ] || python=python3

# llmlint: ignore[tool_output_is_signal] The audit's per-identity report is the requested product; the filter's whole job is to make each line say more, so summarizing it away would defeat the command.
uv run onevcs repos "${published[@]}" |
  "$python" "$root/scripts/merge-path-audit.py" "$root/config/merge-path-checks.json"
