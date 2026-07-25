#!/usr/bin/env bash
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
commit="$(tr -d '[:space:]' <"$root/config/oneharness-ui.commit")"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]]
grep -Fq "$commit" "$root/docs/dag-ui/design.md"
grep -Fq 'interface Conversation {' "$root/docs/dag-ui/oneharness-ui-contract.d.ts"
printf 'oneharness-ui contract: immutable pin and fixture verified\n'
