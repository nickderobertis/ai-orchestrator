#!/usr/bin/env bash
# The planner channel's envelope validator, as config/onemessagebus.yaml names it.
#
# `orchestrator/envelope_review.py` is the validator; this only gives it the environment
# its judged turn needs. That turn is one `oneharness run` under a config whose identity
# chain names a portable indirection per alternate identity — CODEX_HOME for the second
# Codex identity, CLAUDE_CONFIG_DIR for each alternate Claude subscription — which
# oneharness refuses to start without. A launch exports them, so the engine's own
# validation already has them; a manager's shell running `just channel-reply` does not.
# They are resolved here exactly as `scripts/review-plan.sh` resolves them for the same
# reviewer, so an unauthenticated identity falls through the chain rather than leaving
# every envelope unjudged.
#
# The bus reads this command's answer by exit status: 0 passes, 1 refuses with stderr as
# the reason, and anything else — a helper that will not load included — is unjudged,
# which the bus never sends.
set -euo pipefail

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || {
    echo "envelope-review: could not resolve the checkout this validator was run from; run it by its path inside a checkout" >&2
    exit 2
}

for helper in codex-alt-home.sh claude-alt-config-dir.sh; do
    if [ ! -f "$script_dir/$helper" ] || [ ! -r "$script_dir/$helper" ]; then
        echo "envelope-review: required helper is not a readable regular file: $script_dir/$helper; restore it from the repository or run 'just bootstrap', then send the envelope again" >&2
        exit 2
    fi
done
helper_failed() {
    echo "envelope-review: required helper $script_dir/$1 failed to load; restore it from the repository or run 'just bootstrap', then send the envelope again" >&2
    exit 2
}
# shellcheck source=scripts/codex-alt-home.sh
. "$script_dir/codex-alt-home.sh" || helper_failed codex-alt-home.sh
ensure_codex_alt_home "envelope-review" || exit 2
# shellcheck source=scripts/claude-alt-config-dir.sh
. "$script_dir/claude-alt-config-dir.sh" || helper_failed claude-alt-config-dir.sh
resolve_claude_alt_config_dir "envelope-review" || exit 2

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when the checkout above a directory this script has just entered stops being enterable mid-run, since entering that directory already required traversing it; a journey producing that would have to remove the checkout it is running from.
cd -- "${script_dir%/scripts}" || {
    echo "envelope-review: could not enter the checkout at ${script_dir%/scripts}; run the validator by its path inside a readable checkout, then send the envelope again" >&2
    exit 2
}
exec uv run python -m orchestrator.envelope_review "$@"
