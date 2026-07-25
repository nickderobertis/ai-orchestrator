#!/usr/bin/env bash
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
commit="$(tr -d '[:space:]' <"$root/config/oneharness-ui.commit")"
expected="$(tr -d '[:space:]' <"$root/config/oneharness-ui.types.sha256")"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo "oneharness-ui contract: repair invalid commit pin" >&2; exit 1; }
temp="$(mktemp)"; trap 'rm -f "$temp"' EXIT
source_url="${ONEHARNESS_UI_TYPES_URL:-https://raw.githubusercontent.com/nickderobertis/oneharness-ui/$commit/packages/ui/src/types.ts}"
curl -fsSL "$source_url" >"$temp" || { echo "oneharness-ui contract: fetch pinned source and retry" >&2; exit 1; }
actual="$(sha256sum "$temp" | cut -d' ' -f1)"
[[ "$actual" == "$expected" ]] || { echo "oneharness-ui contract: update reviewed fixture and hash together" >&2; exit 1; }
cmp -s "$temp" "$root/docs/dag-ui/oneharness-ui-contract.d.ts" || { echo "oneharness-ui contract: regenerate the checked-in declaration from the pinned source" >&2; exit 1; }
grep -Fq "$commit" "$root/docs/dag-ui/design.md" || { echo "oneharness-ui contract: synchronize design pin" >&2; exit 1; }
printf 'oneharness-ui contract: pinned upstream source verified\n'
