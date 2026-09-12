#!/usr/bin/env bash
# `just repos` — list the registered identities and checkouts, and audit their merge paths.
#
# One thing happens here that a bare `onevcs repos` does not do: the published flag is
# spelled `--audit-gates`, the planner doctrine names it `--audit-gate-coverage`, and
# this absorbs the difference. The audit itself is the published one — since onevcs
# 0.21.0 it names, per identity, each check the host requires before a merge, read off
# that repository's own branch protection and rulesets — so nothing here rewrites it.
# A filter used to, out of a tracked copy of every sibling's required checks; the copy
# went stale whenever a sibling renamed a check and failed every branch here at the end
# of its gate, and `onevcs` reporting the checks itself is what retired it.
set -euo pipefail

published=()
for argument in "$@"; do
  case "$argument" in
    --audit-gate-coverage | --audit-gates) published+=(--audit-gates) ;;
    *) published+=("$argument") ;;
  esac
done

# llmlint: ignore[tool_output_is_signal] The requested registry listing, or the per-identity audit, is this command's whole product; reducing it would leave nothing.
exec uv run onevcs repos ${published[@]+"${published[@]}"}
