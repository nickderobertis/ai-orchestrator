#!/usr/bin/env bash
# Run one plan-store command with this checkout's own board credential established:
# `scripts/plan-store.sh <command> [args...]`.
#
# The board recipes go through this because only the launch verbs load
# `scripts/credentials-env.sh`, so a board command run by hand had no credential on a
# host whose `.env` defined one. It establishes the credential, runs the command with
# the caller's own streams — the command's output is the operator's product, so it is
# never captured — and reads only the exit status, to decide whether the note at the
# end naming the file is owed. **The credential is never printed**, only its *name*, and
# only when this process does not define it.
#
# llmlint: ignore-file[tool_output_is_signal] Every command this runs is a plan-store
# read or write whose own output is what an operator ran it for; this wrapper adds one
# conditional line naming the file that would have supplied a credential it can see is
# absent, and nothing else.
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || {
    echo "plan-store: could not resolve the checkout this recipe was run from; run it from a checkout and retry" >&2
    exit 2
}
repo_root=$(dirname -- "$script_dir")

if [ "$#" -eq 0 ]; then
    echo "plan-store: expected a plan-store command to run and got none; this wrapper establishes this checkout's board credential and then runs what it is given, so pass the command, then retry" >&2
    exit 2
fi

credentials_helper="$script_dir/credentials-env.sh"
if [ ! -f "$credentials_helper" ] || [ ! -r "$credentials_helper" ]; then
    echo "plan-store: required helper is not a readable regular file: $credentials_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/credentials-env.sh
if ! . "$credentials_helper"; then
    echo "plan-store: the credentials helper at $credentials_helper is readable but could not be loaded; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
export_host_credentials plan-store || exit "$?"

# Which names the configured sources take their credential from, read out of the file
# that configures them rather than spelled here: a source repointed at a different
# variable has to move this note with it, and reading `onetaskgraph.yaml` is what makes
# that automatic. A checkout with no such file — or one whose sources name none — leaves
# this empty and the note below unreachable, which is correct: there is no credential to
# report absent.
credential_names() {
    [ -r "$repo_root/onetaskgraph.yaml" ] || return 0
    sed -n 's/^[[:space:]]*token_env:[[:space:]]*\([A-Za-z_][A-Za-z0-9_]*\)[[:space:]]*$/\1/p' \
        "$repo_root/onetaskgraph.yaml" | sort -u
}

# Not `exec`, so the note below can be written once the command has answered. The
# command is run as given, the way `env` runs its: each caller is a recipe of this
# repository naming one fixed plan-store command, and a list of permitted ones here would
# be a second copy of those recipes, kept in step by hand.
# llmlint: ignore[boundary_inputs_validated] The command is the recipe's own, fixed in the justfile, and this wrapper is an `env`-shaped front rather than a trust boundary; a name allow-list would duplicate the recipe list.
"$@" || status=$?
status=${status:-0}
if [ "$status" -ne 0 ]; then
    # Only for a name this process genuinely does not have. A command that failed for
    # any other reason gets no line from here, and a credential that *is* set gets none
    # either — the point is to answer the one refusal an operator cannot place, which is
    # a store reporting a variable missing while the file that supplies it sits
    # configured one directory up from where they ran the command.
    # Read once and refused when the read fails, rather than through a here-string that
    # would swallow the pipeline's status and print no note at all — which is the shape
    # of the silence this note exists to end.
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when `onetaskgraph.yaml` stops being readable between `credential_names`'s own check and its read; no journey can produce that without racing the filesystem the test itself runs on.
    if ! names=$(credential_names); then
        echo "plan-store: could not read which credential names $repo_root/onetaskgraph.yaml configures, so the refusal above cannot be placed; check that file is readable, then retry" >&2
        exit "$status"
    fi
    while read -r name; do
        [ -n "$name" ] || continue
        [ -z "${!name-}" ] || continue
        if [ ! -e "$repo_root/.env" ]; then
            echo "plan-store: $name is not set in this environment, and this checkout supplies it from $repo_root/.env, which does not exist; create that file with '$name=<value>' in it, then retry" >&2
            continue
        fi
        # Whether the file has a line for the name is the loader's record — the one
        # parser of that file, which `export_host_credentials` above already ran — and
        # not a second read of it here.
        case " $credential_file_names " in
            *" $name "*)
                # The file has the line and the value is still empty: either the line's
                # own value is, or this process already held the name empty and the
                # helper left that alone, as it leaves every name the environment
                # defines. Both are a value to supply rather than a line to add, and
                # "defines no such name" would send somebody to add a second copy of a
                # line that is there.
                echo "plan-store: $name is empty in this environment, and $repo_root/.env — the file this checkout supplies it from — defines it without a value (a name already exported empty also beats that file); give '$name' a value where it is set, then retry" >&2
                ;;
            *)
                echo "plan-store: $name is not set in this environment, and $repo_root/.env — the file this checkout supplies it from — defines no such name; add '$name=<value>' to that file, then retry" >&2
                ;;
        esac
    done <<<"$names"
fi
exit "$status"
