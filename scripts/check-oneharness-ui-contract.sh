#!/usr/bin/env bash
set -euo pipefail
root="$(git rev-parse --show-toplevel)" || { echo "oneharness-ui contract: run from a repository checkout and retry" >&2; exit 1; }
[[ "$root" = /* && -r "$root/config/oneharness-ui.commit" && -r "$root/config/oneharness-ui.types.sha256" ]] || { echo "oneharness-ui contract: restore readable commit and SHA-256 pin files, then retry" >&2; exit 1; }
commit="$(tr -d '[:space:]' <"$root/config/oneharness-ui.commit")" || { echo "oneharness-ui contract: read the commit pin and retry" >&2; exit 1; }
expected="$(tr -d '[:space:]' <"$root/config/oneharness-ui.types.sha256")" || { echo "oneharness-ui contract: read the SHA-256 pin and retry" >&2; exit 1; }
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo "oneharness-ui contract: repair invalid commit pin" >&2; exit 1; }
[[ "$expected" =~ ^[0-9a-f]{64}$ ]] || { echo "oneharness-ui contract: repair invalid SHA-256 pin" >&2; exit 1; }
temp="$(mktemp)" || { echo "oneharness-ui contract: make temporary storage available and retry" >&2; exit 1; }
trap 'rm -f "$temp"' EXIT
source_url="${ONEHARNESS_UI_TYPES_URL:-https://raw.githubusercontent.com/nickderobertis/oneharness-ui/$commit/apps/conversation-ui/src/features/conversations/presentational-types.ts}"
[[ "$source_url" =~ ^(https|file):// ]] || { echo "oneharness-ui contract: source URL must use https:// or file://" >&2; exit 1; }
curl_log="$(mktemp)" || { echo "oneharness-ui contract: make temporary storage available and retry" >&2; exit 1; }
trap 'rm -f "$temp" "$curl_log"' EXIT
curl -fL "$source_url" -o "$temp" 2>"$curl_log" || { cat "$curl_log" >&2; echo "oneharness-ui contract: fetch pinned source and retry" >&2; exit 1; }
actual="$(sha256sum "$temp" | cut -d' ' -f1)" || { echo "oneharness-ui contract: verify sha256sum and cut are available, then retry" >&2; exit 1; }
[[ "$actual" =~ ^[0-9a-f]{64}$ ]] || { echo "oneharness-ui contract: integrity tools returned an invalid SHA-256 value; repair them and retry" >&2; exit 1; }
[[ "$actual" == "$expected" ]] || { echo "oneharness-ui contract: expected $expected but fetched $actual; update reviewed fixture and hash together" >&2; exit 1; }
cmp -s "$temp" "$root/docs/dag-ui/oneharness-ui-contract.d.ts" || { echo "oneharness-ui contract: regenerate the checked-in declaration from the pinned source" >&2; exit 1; }
grep -Fq "$commit" "$root/docs/dag-ui/design.md" || { echo "oneharness-ui contract: synchronize design pin" >&2; exit 1; }
printf 'oneharness-ui contract: pinned upstream source verified\n'
