#!/usr/bin/env bash
# Body of the cached Nx `workspace:lint-llm-diff` target: judge the branch diff
# against one resolved base commit. Run it through `just lint-llm-diff <base>`,
# which resolves the base ref to the commit this reads and keys the cache on.
#
# Nothing here records or replays a verdict. llmlint runs, its `-v` report is this
# task's terminal output, and its exit status is this task's exit status — so Nx
# caches a clean run and replays that report verbatim, while a run with findings
# and a run that never reached a verdict both stay uncached and re-judge. `-v` is
# what makes replay worth having: it itemizes every rule and prints the
# `See full results with llmlint history <id>` pointer, so a restored run says as
# much as a fresh one.
#
# A green run elides one thing, and that is a size decision rather than a taste one.
# `-v` sends the report to stdout but the oneharness debug view to stderr, and two
# lines of it — one serialized judge call each, every judged file's content inside
# the prompt — are 214KB of a 226KB run. Nx replays a cache hit as one burst and
# exits, and a burst larger than a single pipe buffer loses its tail: through
# `scripts/nx.sh`, whose redaction filter is a pipe, a 226KB replay arrives cut off
# at ~64KB, taking the per-rule verdicts and the history pointer with it. So a green
# keeps every diagnostic line — including `See full results with llmlint history
# <id>`, which is where those payloads are retrievable in full — and elides only the
# payloads themselves. Failures are exempt because they are never cached and so
# never replayed: they stream out as the judge produces them, whole.
#
# The base arrives as `LLMLINT_DIFF_BASE_SHA` rather than an argument because Nx
# hashes declared environment variables but not target arguments: keying and
# judging on the same value is what stops a clean verdict computed against one
# base from being replayed for another.
#
# `role=llmlint` stamps this tier's own harness sessions so they are separable from
# the agent/judge sessions of the work being linted (whose roles come from
# oneharness.toml / oneharness.judge.toml). It is layered over — not substituted
# for — the graph labels inherited when a dispatched agent runs its own gate, so a
# finding stays attributable to the run/round/node that provoked it. Those labels
# deliberately stay out of the cache key: they identify who asked, not what is
# judged.
#
# llmlint: ignore-file[boundary_inputs_validated] The label guard below deliberately
# bounds no value *length*. `orchestrator/labels.py` caps a value at 256 code points;
# bash's `=~` counts whatever its locale calls a character, so an interval quantifier
# here would reject a short non-ASCII value that module accepts — narrower than the
# contract, which is the disagreement this second opinion exists to avoid.
# tests/test_labels.py::test_the_recipe_guard_is_deliberately_wider_than_the_value_length_rule
# states and holds that split. This is file-scoped rather than line-scoped only
# because the drift gate lifts `key=`, `value=` and the condition as three *adjacent*
# lines, so a directive between them would break the reconciliation it is defending.
# llmlint: ignore-file[changed_behavior_has_e2e] Every journey this script has —
# a judged and a replayed clean run, findings and a broken toolchain re-judging,
# each invalidation case, a refused base, and unusable labels — runs end to end in
# tests/e2e/test_llmlint_cache_e2e.py. What remains are host-failure guards on the
# checkout layout; simulating a broken filesystem is the guard's job, not a journey's.
set -euo pipefail

# Every refusal below ends this task within milliseconds of printing it, and Nx
# 23.1.0's run-commands, when it has no terminal to run the task in (the gate, a
# dispatch, the suite), collects the task's output on the child's `exit` rather than
# after its pipes drain, then exits itself: a refusal is intermittently dropped from
# the terminal output this tier reports, leaving only Nx's "exited with non-zero
# status". So each one is also appended to the file `just lint-llm-diff` names in
# LLMLINT_DIFF_REFUSALS, which the recipe prints only when Nx lost it. That copy is
# never read on success, so it plays no part in what Nx caches or replays.
refuse() {
  printf '%s\n' "$1" >&2
  if [[ -n "${LLMLINT_DIFF_REFUSALS:-}" ]]; then printf '%s\n' "$1" >>"$LLMLINT_DIFF_REFUSALS" || true; fi
  exit 1
}

root="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)" || {
  refuse "lint-llm-diff: could not locate the repository from this script; reinstall the checkout and retry"
}
# shellcheck source=scripts/llmlint-runtime-env.sh
. "$root/scripts/llmlint-runtime-env.sh" || {
  refuse "lint-llm-diff: could not load the pinned runtime environment; restore scripts/llmlint-runtime-env.sh and retry"
}
base_sha="${LLMLINT_DIFF_BASE_SHA:-}"
[[ "$base_sha" =~ ^[0-9a-f]{40,64}$ ]] || {
  refuse "lint-llm-diff: LLMLINT_DIFF_BASE_SHA must be a resolved commit id; run 'just lint-llm-diff <base>' instead of this target directly"
}
git -C "$root" rev-parse --verify --quiet "${base_sha}^{commit}" >/dev/null || {
  refuse "lint-llm-diff: base commit '$base_sha' is missing from this checkout; fetch it and retry"
}
labels="$(uv run orchestrator-history-labels role=llmlint)" || {
  refuse "lint-llm-diff: could not derive harness history labels; run 'just bootstrap' and retry"
}
# The alphabet is orchestrator/labels.py's, which is the declared trust boundary for
# the label contract: a narrower opinion here could only reject a label that module
# deliberately passed through, such as an inherited key with a hyphen or a value with
# a space. This second opinion is still needed — the renderer above is reached through
# PATH and can be replaced — so it is held to the first one by the drift gate in
# tests/test_labels.py, which lifts these two patterns out of this file and sweeps them
# against that module character by character. Change either side and that gate fails.
key='[A-Za-z0-9][A-Za-z0-9._-]{0,63}'
value='[^,[:cntrl:]]+'
[[ "$labels" =~ ^${key}=${value}(,${key}=${value})*$ ]] || {
  refuse "lint-llm-diff: harness history labels are not comma-separated key=value pairs: '$labels'; correct or unset ONEHARNESS_HISTORY_LABELS and retry"
}

llmlint_runtime_env "$root"
export ONEHARNESS_HISTORY_LABELS="$labels"

diagnostics="$(mktemp)" || {
  refuse "lint-llm-diff: could not open temporary storage for the judge's diagnostics; free disk space and retry"
}
trap 'rm -f "$diagnostics"' EXIT

#: Longest non-payload stderr line this tier has produced is under 600 characters;
#: each serialized judge call is 60-150KB on one line. Anything between the two is
#: kept, so this elides a payload rather than trimming a diagnostic.
readonly DEBUG_PAYLOAD_CHARS=2000

# The judge's own exit status is the task's: Nx caches a task only when it succeeds,
# which is exactly the record-keeping this tier now delegates to it.
# llmlint: ignore[tool_output_is_signal] `-v` is the point of this tier, not noise: Nx replays this run's terminal output in place of a verdict record, so the report has to itemize every rule and carry the `llmlint history <id>` pointer or a replayed run says less than a fresh one.
status=0
llmlint --diff --diff-base "$base_sha" -v 2>"$diagnostics" || status=$?
if ((status != 0)); then
  # Never cached, so never replayed: a failure can afford every byte, and the
  # operator who has to clear it is the one who needs the judge call verbatim.
  cat "$diagnostics" >&2 || echo "lint-llm-diff: the failed run left no readable diagnostics" >&2
else
  awk -v limit="$DEBUG_PAYLOAD_CHARS" '
    length($0) > limit {
      printf "lint-llm-diff: elided a %d-character serialized judge call; `llmlint history <id>` above has the full record\n", length($0)
      next
    }
    { print }
  ' "$diagnostics" >&2 || {
    refuse "lint-llm-diff: could not summarize the judge diagnostics; rerun 'just lint-llm-diff <base> --skip-nx-cache'"
  }
fi
exit "$status"
