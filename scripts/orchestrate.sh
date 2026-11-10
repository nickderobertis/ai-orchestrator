#!/usr/bin/env bash
# `just orchestrate`'s mechanism; the comment above the recipe states each default, why a
# caller's own flag wins, and why `--adopt` adds none (an adopted run keeps the labels it
# was launched with). A flag counts as named in either spelling, `--flag VALUE` or
# `--flag=VALUE`. The run-end hooks and the drafting graph are not among those defaults:
# config/onepipeline.launch.yaml states them, and `scripts/onepipeline.sh` names that
# document on every `start`.
set -euo pipefail

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P) || {
    echo "orchestrate: this recipe could not resolve the checkout it was run from; run it from a readable checkout" >&2
    exit 2
}

if [[ "${1:-}" == "--adopt" ]]; then
    exec "$script_dir/onepipeline.sh" adopt "${@:2}"
fi

flags=(--dag-graph --review-reply-graph)
values=(graphs/dag-scope.yaml graphs/review-reply.yaml)
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

# The run's kind, which the engine records as an opaque label and the browser groups by:
# a caller's own `kind` label wins in either spelling, because the engine refuses a key
# named twice. No `--group`: a launch from a board copy is grouped through the copy's
# origin, and one from its own project by that project.
kind=
previous=
for argument in "$@"; do
    if [[ "$argument" == --label=kind=* || ( "$previous" == "--label" && "$argument" == kind=* ) ]]; then
        kind=1
        break
    fi
    previous=$argument
done
[[ -n "$kind" ]] || defaults+=(--label kind=main)

# llmlint: ignore[tool_output_is_signal] Streaming the run as it goes is what an attached launch is for, and its validated launch failures name the input to correct; `--detach` is the spelling that returns one line.
exec "$script_dir/onepipeline.sh" start "$@" ${defaults[@]+"${defaults[@]}"}
