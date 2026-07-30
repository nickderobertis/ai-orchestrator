#!/usr/bin/env bash
# Durable output logs for the recipes that capture their own output.
#
# Sourced, not executed: `scripts/nx.sh` and the `justfile` recipes both need the
# two functions below in the shell that runs the command, and a `mktemp` log
# removed by an EXIT trap is exactly the defect this replaces. A log at a
# deterministic path is readable *while* the command runs (`tail -f
# .logs/check.log`) and still readable after it exits, so diagnosing a stalled or
# failed run never means reading a process's file descriptors through /proc.
#
# One log per label, truncated per run: the newest run of each recipe is the one
# worth keeping, and unbounded history inside a working tree is its own problem.
#
# "Per run" has to mean per *invocation*, though, or the deterministic path
# reintroduces the defect it replaces. This repository's own suite runs `just
# lint-llm-diff` against this checkout from inside `just check`, so a nested
# `scripts/nx.sh` resolves the same `.logs/nx.log` the still-running outer one is
# writing and truncates it — the running check becomes uninspectable exactly when
# a reader needs it. So every invocation records the absolute path it is writing
# in an exported claim list, which descendants inherit; an invocation that finds
# its path already claimed by a live enclosing one takes a distinct destination
# rather than erasing evidence. The claim is the resolved path, not the label, so
# runs in different checkouts never divert each other.
#
# llmlint: ignore-file[robust_shell] This file is sourced, never executed, so
# `set -euo pipefail` here would silently impose errexit on whatever shell sourced
# it — a library must not reach into its caller's options. Both callers
# (`scripts/nx.sh` and the `justfile`, via `set shell := ["bash", "-euo",
# "pipefail", "-c"]`) already run strict, and every function below checks and
# reports its own failures rather than relying on errexit.
#
# shellcheck shell=bash

#: Absolute paths of the logs that enclosing invocations are still writing, one
#: per line. Exported, so every descendant process inherits the set without any
#: caller having to pass it along.
export ORCHESTRATOR_PRESERVED_LOGS="${ORCHESTRATOR_PRESERVED_LOGS-}"

# Open (create and truncate) this repository's log for one labelled command and
# set `PRESERVED_LOG` to its path. Owner-only from creation: preserved evidence
# outlives the terminal that would otherwise have been its only reader.
#
# The path is returned in a variable rather than on stdout on purpose. A caller
# writing `log=$(preserved_log_open ...)` would run this in a subshell, and the
# claim it records would die with that subshell — leaving the next nested
# invocation free to truncate the log this one is about to write.
preserved_log_open() {
    local root=$1 label=$2 dir path
    if [[ ! $label =~ ^[a-z][a-z0-9-]*$ ]]; then
        echo "preserved-log: invalid log label '$label'; use lowercase words and dashes" >&2
        return 1
    fi
    dir="$root/.logs"
    # Canonicalized, because the claim below is compared as a string and callers
    # legitimately spell one root several ways — `just` passes `justfile_directory()`
    # while `nx.sh` derives its own from `$0`. Two spellings of the same file have
    # to be recognized as the same file, or a nested run truncates it after all.
    if ! { mkdir -p "$dir" && chmod 700 "$dir" && dir=$(cd -- "$dir" && pwd -P); }; then
        echo "preserved-log: cannot prepare '$dir'; repair its parent permissions and retry" >&2
        return 1
    fi
    path="$dir/$label.log"
    if _preserved_log_claimed "$path"; then
        # An enclosing invocation is still writing this exact log, so truncating
        # it would erase a run that has not finished producing its evidence.
        path="$dir/$label.$$.log"
    else
        # This invocation owns the stable path, which makes any diverted logs
        # beside it leftovers from nested runs of a previous one.
        rm -f "$dir/$label".[0-9]*.log
    fi
    if ! { : >"$path" && chmod 600 "$path"; }; then
        echo "preserved-log: cannot open '$path'; repair its permissions and retry" >&2
        return 1
    fi
    ORCHESTRATOR_PRESERVED_LOGS="${ORCHESTRATOR_PRESERVED_LOGS:+${ORCHESTRATOR_PRESERVED_LOGS}
}$path"
    export ORCHESTRATOR_PRESERVED_LOGS
    # shellcheck disable=SC2034 # this variable is the function's return value; every
    # caller reads it in the shell that sourced this file.
    PRESERVED_LOG=$path
}

# Whether some enclosing invocation is already writing exactly this log.
_preserved_log_claimed() {
    local candidate=$1 held
    while IFS= read -r held; do
        [[ $held == "$candidate" ]] && return 0
    done <<<"$ORCHESTRATOR_PRESERVED_LOGS"
    return 1
}

# The credential-name grammar, byte-identical to `SECRET_NAME_PATTERN` in
# `orchestrator/redaction.py`, which is its one source. It is copied rather than
# read from there because this file runs in front of `just check`, before any
# virtualenv is guaranteed to exist; tests/test_redaction.py fails if the two
# strings ever differ.
_PRESERVED_LOG_SECRET_NAME='(^|_)(TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|SESSION_?KEY|AUTH)S?$'

# Print `length<TAB>name` for every credential-shaped environment variable worth
# hiding, longest value first so a token that contains a shorter token's value is
# replaced before the shorter one can split it.
_preserved_log_secret_names() {
    local name value
    while IFS= read -r name; do
        value=${!name-}
        # Shorter values collide with ordinary words and would corrupt the very
        # evidence this preserves; a credential that short is not one worth
        # protecting.
        ((${#value} >= 8)) || continue
        [[ $value != *[$'\n\r']* ]] || continue
        [[ ${name^^} =~ $_PRESERVED_LOG_SECRET_NAME ]] || continue
        printf '%s\t%s\n' "${#value}" "$name"
    done < <(compgen -e) | sort -k1,1nr -k2,2
}

# Filter stdin to stdout, replacing every credential value in the environment
# with `<redacted:NAME>`.
#
# Harness credentials are referenced by name and never printed on purpose, but a
# preserved log is durable in a way a terminal is not: one `env` dump inside a
# failing test would record a live token on disk. This closes that off from the
# only place the value is known. Kept in pure Bash on purpose — it sits in front
# of `just check`, so it must not depend on an interpreter or a virtualenv that
# `just bootstrap` has not installed yet.
#
# `orchestrator/redaction.py` applies the identical rule to the evidence the
# Python side preserves; tests/test_redaction.py holds the two to one behavior.
redact_secrets() {
    local -a names=() values=()
    local name line index
    while IFS=$'\t' read -r _ name; do
        names+=("$name")
        values+=("${!name}")
    done < <(_preserved_log_secret_names)
    # `|| [[ -n $line ]]` also emits a final chunk that arrived without a trailing
    # newline; the log gets one appended, which no reader of a captured log can
    # tell apart from the command having written it.
    while IFS= read -r line || [[ -n $line ]]; do
        for index in "${!values[@]}"; do
            line=${line//"${values[index]}"/"<redacted:${names[index]}>"}
        done
        printf '%s\n' "$line"
        line=""
    done
}
