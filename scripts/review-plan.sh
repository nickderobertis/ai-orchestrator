#!/usr/bin/env bash
# Review a qualified plan project's unreviewed task content: `just review-plan
# <source>:<project>`.
#
# The judging is `orchestrator/plan_review.py`'s; this script exists for the one thing
# that cannot live there. Every identity chain this repository configures names a
# portable indirection its variant maps into the child — CODEX_HOME for the second
# Codex identity, CLAUDE_CONFIG_DIR for each alternate Claude subscription — and
# oneharness refuses to start while one of them is unset. So they are resolved here,
# exactly as `scripts/llmlint-oneharness.sh` resolves them for the judged lint tier,
# and an unauthenticated identity then falls through the chain rather than hard-failing
# the whole command.
#
# It deliberately takes no flag of its own and forwards none. There is no `--force`, no
# `--skip`, and no environment variable that lets a plan past the refusal this records
# against: an escape here would be reached under exactly the time pressure that produced
# the two unreviewed plans this gate exists to catch. That is also why the argument check
# below is here rather than left to the reviewer's own parser: a rejected invocation has
# to answer with the one form that works, not with a usage line an operator then reads as
# an invitation to find the flag they wanted.
#
# llmlint: ignore-file[changed_behavior_has_e2e] What this script decides — that both
# indirections are resolved before the reviewer runs, and that the project argument
# reaches it untouched — runs end to end in tests/e2e/test_plan_review_e2e.py. What
# remains are host-failure guards on the checkout layout.
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || {
    echo "review-plan: could not resolve the checkout this recipe was run from; run it from a checkout and retry" >&2
    exit 2
}

for helper in codex-alt-home.sh claude-alt-config-dir.sh; do
    if [ ! -f "$script_dir/$helper" ] || [ ! -r "$script_dir/$helper" ]; then
        echo "review-plan: required helper is not a readable regular file: $script_dir/$helper; restore it from the repository or run 'just bootstrap', then retry" >&2
        exit 2
    fi
done
# Each source is checked rather than left to `set -e`, which would exit on a helper
# that is readable but does not load — a truncated or half-written file — with whatever
# bash printed and no repair, and with the review looking to a caller like a refused
# plan rather than a broken checkout.
# shellcheck source=scripts/codex-alt-home.sh
if ! . "$script_dir/codex-alt-home.sh"; then
    echo "review-plan: $script_dir/codex-alt-home.sh is readable but could not be loaded; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
ensure_codex_alt_home "review-plan" || exit $?
# shellcheck source=scripts/claude-alt-config-dir.sh
if ! . "$script_dir/claude-alt-config-dir.sh"; then
    echo "review-plan: $script_dir/claude-alt-config-dir.sh is readable but could not be loaded; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
resolve_claude_alt_config_dir "review-plan" || exit $?
# And the board credential, for the same reason one directory over: this reads the plan
# out of whatever source its id names, a board included, and this checkout supplies that
# credential from its own gitignored `.env`. Only the launch verbs read that file until
# now, so a review of a project held on the board refused with the store's own `missing
# or empty` on a host that had it configured.
if [ ! -f "$script_dir/credentials-env.sh" ] || [ ! -r "$script_dir/credentials-env.sh" ]; then
    echo "review-plan: required helper is not a readable regular file: $script_dir/credentials-env.sh; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/credentials-env.sh
if ! . "$script_dir/credentials-env.sh"; then
    echo "review-plan: $script_dir/credentials-env.sh is readable but could not be loaded; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
export_host_credentials "review-plan" || exit $?

if [ "$#" -ne 1 ] || [ -z "$1" ] || [ "${1#-}" != "$1" ] || [ "${1#*:}" = "$1" ]; then
    echo "review-plan: expected exactly one qualified plan project and nothing else, got: $*" >&2
    echo "review-plan: run 'just review-plan <source>:<project>' — the same id you would hand 'just check-plan'. There is no flag that records a review without one, and none that lets a plan past that refusal." >&2
    exit 2
fi

exec uv run orchestrator-review-plan "$1"
