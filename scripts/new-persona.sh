#!/usr/bin/env bash
# Scaffold a persona into this repository's `personas/` tree.
#
# `oneagentgraph persona new NAME` writes into the working directory, and creates
# the subdirectory a slash-qualified repo-specific name (`crozier/crozier-corpus`)
# names. So the only difference this wrapper absorbs is *where* it runs: the
# recipe's contract is that a new persona lands where `just validate-personas` and
# every dispatch look for it.
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
persona_dir="$(dirname -- "$script_dir")/personas"

args=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --persona-dir)
            [ "$#" -ge 2 ] || { echo "new-persona: --persona-dir needs a directory" >&2; exit 2; }
            persona_dir="$2"
            shift 2
            ;;
        --persona-dir=*)
            persona_dir="${1#--persona-dir=}"
            shift
            ;;
        *)
            args+=("$1")
            shift
            ;;
    esac
done

[ "${#args[@]}" -gt 0 ] || { echo "usage: new-persona.sh <name> [--persona-dir DIR]" >&2; exit 2; }
mkdir -p -- "$persona_dir"
cd -- "$persona_dir"
exec uv run oneagentgraph persona new "${args[@]}"
