#!/usr/bin/env bash
# The one reading of `config/onevcs.checkouts`, shared by every script that walks it.
#
# Sourced, never executed: `scripts/apply-repo-registry.sh` registers the listed
# checkouts and `scripts/repos-bootstrap.sh` provisions their gates, and the two have
# to agree on which paths the list names — a comment stripped one way and a `~`
# expanded another would provision a checkout the registry does not hold, or the
# reverse. Whether a listed path is on this host is the caller's question, answered
# the same way by each (`[[ -d $path ]]`), because what a caller says about a missing
# one is its own report.
#
# llmlint: ignore-file[robust_shell] This file is sourced, so `set -euo pipefail` here
# would reach into the caller's options; both callers already run strict, and the one
# function below reports its own refusal and returns a status the caller acts on.
#
# shellcheck shell=bash

# Print every path the list at $1 names, one per line, `~` expanded against `$HOME`.
#
# Blank lines and `#` comments are dropped. An entry is `~`, `~/...` or absolute, and
# anything else is refused by name with status 2: a `~user` spelling would look up
# another account through the shell, and a relative path would name a different
# directory for every caller's working directory, when the list is one host's record
# of where its checkouts are.
registered_checkout_paths() {
    local file=$1 line trimmed
    # `~` expands against `$HOME`, so a `HOME` that is empty, relative or absent would turn
    # every listed checkout into a path under whatever this was run from.
    if [[ -z ${HOME:-} || $HOME != /* || ! -d $HOME ]]; then
        echo "registered-checkouts: HOME is '${HOME:-}', so the '~' entries in $file cannot be expanded; export HOME as the absolute path of an existing directory and retry" >&2
        return 2
    fi
    if [[ ! -r $file ]]; then
        echo "registered-checkouts: cannot read the checkout list $file; check that it exists and is readable, or name another with --checkouts" >&2
        return 2
    fi
    while IFS= read -r line || [[ -n $line ]]; do
        # `read` without `IFS=` is the trim: it drops the leading and trailing
        # whitespace a commented, indented list is written with.
        read -r trimmed <<<"${line%%#*}"
        [[ -n $trimmed ]] || continue
        if [[ $trimmed == \~* && $trimmed != \~ && $trimmed != \~/* ]]; then
            echo "registered-checkouts: unsupported tilde path '$trimmed' in $file; use ~/path" >&2
            return 2
        fi
        if [[ $trimmed != \~* && $trimmed != /* ]]; then
            echo "registered-checkouts: relative path '$trimmed' in $file; use an absolute path or ~/path" >&2
            return 2
        fi
        printf '%s\n' "${trimmed/#\~/$HOME}"
    done <"$file"
}
