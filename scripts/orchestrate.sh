#!/usr/bin/env bash
# `just orchestrate`'s mechanism; the comment above the recipe states each default, why a
# caller's own flag wins, and why `--adopt` adds none. Two things are this script's alone:
# a flag counts as named in either spelling, `--flag VALUE` or `--flag=VALUE`, and the hook
# is named by an absolute path, because the engine spawns it from the launch record's
# directory rather than from this checkout.
set -euo pipefail

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) || {
    echo "orchestrate: this recipe could not resolve the checkout it was run from; run it from a readable checkout" >&2
    exit 2
}

if [[ "${1:-}" == "--adopt" ]]; then
    exec "$script_dir/onepipeline.sh" adopt "${@:2}"
fi

hook="$script_dir/run-ended.sh"
if [ ! -f "$hook" ] || [ ! -x "$hook" ]; then
    echo "orchestrate: the run-end hook is not an executable file at $hook, so a run launched now would fail to start it when it ends; restore it from the repository and 'chmod +x' it, then retry" >&2
    exit 2
fi

flags=(--dag-graph --pr-author-graph --success-hook --failure-hook)
values=(graphs/dag-scope.yaml graphs/pr-author.yaml "$hook" "$hook")
defaults=()
for index in "${!flags[@]}"; do
    flag=${flags[$index]}
    named=
    for argument in "$@"; do
        if [[ "$argument" == "$flag" || "$argument" == "$flag"=* ]]; then
            named=1
            break
        fi
    done
    [[ -n "$named" ]] || defaults+=("$flag" "${values[$index]}")
done

# llmlint: ignore[tool_output_is_signal] Streaming the run as it goes is what an attached launch is for, and its validated launch failures name the input to correct; `--detach` is the spelling that returns one line.
exec "$script_dir/onepipeline.sh" start "$@" ${defaults[@]+"${defaults[@]}"}
