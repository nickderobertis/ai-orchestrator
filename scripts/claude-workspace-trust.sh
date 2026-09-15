#!/usr/bin/env bash
# Shared, sourceable implementation for marking claude-code workspaces trusted, in
# every configuration directory this host dispatches through.
set -euo pipefail

# The one spelling of the vendor field this file reads and writes. claude-code's
# configuration format has no importable source here, so the next best thing is that
# it is written down once: both jq programs below take it as `$trust_key` rather than
# restating it, and a rename reaches the decision pass, the pruning candidacy test,
# and the write together or not at all.
readonly CLAUDE_TRUST_KEY=hasTrustDialogAccepted

# Two jq programs, and the second one runs only when the first says the file has to
# change. Every dispatch marks its clone, its worktree, and its result, so this call
# is on the hot path of the whole harness; a pass that reserialized the configuration
# to decide made each mark cost the size of a file that only ever grew, behind one
# host-wide lock. Deciding is a parse, and a parse is what a read has to pay anyway.
#
# shellcheck disable=SC2016  # A jq program: every `$` here names a jq binding, not a shell one.
CLAUDE_TRUST_DECISION_FILTER='
try (
  if type != "object" or ((.projects // {}) | type != "object")
  then "" | halt_error(3)
  else . end
  | (.projects // {}) as $projects
  | ($ARGS.positional | map({key: ., value: true}) | from_entries) as $requested
  | (
      if $ARGS.positional
         | map($projects[.] | if type == "object" then .[$trust_key] else null end)
         | any(. != true)
      then "update"
      else "current"
      end
    ),
    (
      $projects
      | to_entries[]
      | select(
          ($requested[.key] | not)
          and (.key | startswith("/"))
          and (.key | contains("\n") | not)
          and (.value | type == "object")
          and ((.value | keys) - [$trust_key] | length == 0)
        )
      | .key
    )
) catch ("" | halt_error(3))
| . + "\n"
'

# The write, in one pass: drop the entries the caller proved dead, then trust every
# requested root. Candidacy above is decided by an entry's *keys*: one holding
# nothing beyond `hasTrustDialogAccepted` — whatever that key says, or an empty
# object saying nothing — records only a decision this function can make again,
# while one carrying any other key is a session Claude recorded and never a
# candidate. A requested root whose recorded value is not an object cannot be added
# to, so it is replaced rather than merged; the only thing it could have held is the
# trust decision being set right now.
# shellcheck disable=SC2016  # A jq program: every `$` here names a jq binding, not a shell one.
CLAUDE_TRUST_UPDATE_FILTER='
($deadlist | split("\n") | map(select(length > 0))) as $dead
| .projects = ((.projects // {}) | delpaths($dead | map([.])))
| reduce $ARGS.positional[] as $root (.;
    .projects[$root] = (
      (.projects[$root] | if type == "object" then . else {} end)
      + {($trust_key): true}
    ))
'

mark_claude_config_trust() (
  if (( $# < 2 )); then
    echo "claude-workspace-trust: configuration path and at least one workspace path are required" >&2
    return 2
  fi
  local config_path=$1
  shift
  # Before any early return, because these are the caller's arguments rather than
  # facts about the host: a configuration that happens to be absent is not a reason
  # to accept a relative or empty workspace path, and answering 0 to one would report
  # a root as trusted that this function never could have marked.
  #
  # The configuration path is checked the same way and first, because it is the one
  # that reaches the filesystem: it names the lock, the temporary files beside it,
  # the jq operand, and the target of the atomic replace, so a relative one writes
  # somewhere nothing reads and one beginning with `-` is read as an option by
  # whichever of those tools it reaches.
  if [[ -z $config_path || $config_path != /* ]]; then
    echo "claude-workspace-trust: configuration path must be a nonempty absolute path: '$config_path'" >&2
    return 2
  fi
  local root
  for root in "$@"; do
    if [[ -z $root || $root != /* ]]; then
      echo "claude-workspace-trust: workspace path must be a nonempty absolute path: '$root'" >&2
      return 2
    fi
  done
  [ -f "$config_path" ] || return 0
  if ! command -v jq >/dev/null 2>&1; then
    echo "claude-workspace-trust: jq is unavailable; install jq or add it to PATH, then retry" >&2
    return 1
  fi
  if ! command -v flock >/dev/null 2>&1; then
    echo "claude-workspace-trust: flock is unavailable; install util-linux or add flock to PATH, then retry" >&2
    return 1
  fi
  local -a trust_temporaries=()
  # Both codes are the same false positive under two shellcheck versions: 0.11+ calls a
  # trap-only function uninvoked (SC2329), 0.10 calls its body unreachable (SC2317).
  # shellcheck disable=SC2329,SC2317  # Invoked from the exit trap below, which shellcheck cannot follow.
  cleanup_trust_files() {
    (( ${#trust_temporaries[@]} > 0 )) || return 0
    local -a stale=("${trust_temporaries[@]}")
    trust_temporaries=()
    if ! rm -f "${stale[@]}"; then
      echo "claude-workspace-trust: cannot remove temporary files for $config_path; fix directory permissions, then retry" >&2
      return 1
    fi
  }
  forget_trust_file() {
    local -a kept=()
    local recorded
    for recorded in "${trust_temporaries[@]}"; do
      [ "$recorded" = "$1" ] || kept+=("$recorded")
    done
    trust_temporaries=("${kept[@]}")
  }
  # shellcheck disable=SC2329,SC2317  # Invoked by name as this subshell's EXIT trap handler.
  on_trust_exit() {
    local status=$?
    cleanup_trust_files || status=1
    exit "$status"
  }
  # A killed dispatch used to leave its half-written copy of a large configuration
  # beside the original — 3.9 GB of them, on this host. Cleanup belongs to every
  # exit, not to the paths that happen to return; the signal traps below turn a
  # terminated call into one of those exits.
  trap on_trust_exit EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP
  if ! exec 9>"${config_path}.trust.lock"; then
    echo "claude-workspace-trust: cannot open the lock beside $config_path; fix directory permissions, then retry" >&2
    return 1
  fi
  if ! flock 9; then
    echo "claude-workspace-trust: cannot lock $config_path; verify flock and filesystem locking, then retry" >&2
    return 1
  fi
  [ -f "$config_path" ] || return 0
  local decision decision_status=0
  # `--args` claims every remaining argument, so the configuration is named before it
  # and stdin is closed: a jq that found no file would otherwise read this caller's
  # stdin and hang under the lock every other dispatch is waiting on. jq's own words
  # are kept rather than discarded — an unreadable file, an unusable jq, and a
  # malformed configuration each need a different repair, and only jq can tell them
  # apart. The shape rejection below deliberately says nothing, so its message is
  # this function's alone.
  decision=$(jq -j --arg trust_key "$CLAUDE_TRUST_KEY" "$CLAUDE_TRUST_DECISION_FILTER" \
    "$config_path" --args "$@" 2>&1 </dev/null) || decision_status=$?
  case $decision_status in
    0) ;;
    3)
      echo "claude-workspace-trust: $config_path must contain a JSON object with an optional projects object; repair or replace it, then retry" >&2
      return 1
      ;;
    *)
      echo "claude-workspace-trust: jq exited $decision_status reading $config_path: ${decision:-it reported nothing}; the file is not valid JSON or jq could not read it, so repair or replace the file, or fix the reported failure, then retry" >&2
      return 1
      ;;
  esac
  local -a decided=()
  mapfile -t decided <<<"$decision"
  # A zero exit is not by itself a decision: anything named `jq` on PATH can produce
  # one, and reading a record that is not there would send this straight to the write
  # path with no idea what it is writing.
  case ${decided[0]-} in
    update | current) ;;
    *)
      echo "claude-workspace-trust: the decision pass over $config_path answered '${decision:-nothing}' instead of a decision; verify that jq on PATH is jq, then retry" >&2
      return 1
      ;;
  esac
  # Trust-only entries whose workspace is provably gone. Nothing else prunes them,
  # and the e2e suite alone registers hundreds of throwaway paths per run, so an
  # unpruned configuration grows without bound and makes every later mark more
  # expensive.
  # shellcheck disable=SC2329  # Invoked from the candidate loop below.
  provably_absent() {
    local path=$1 ancestor
    if [ -e "$path" ]; then
      return 1
    fi
    # A failed lookup is not evidence on its own: an unreadable ancestor fails it
    # too, and dropping the entry then would forget a workspace that is merely out
    # of reach. Absence is authoritative only below an ancestor we can search.
    ancestor=${path%/*}
    while [ -n "$ancestor" ] && [ ! -e "$ancestor" ]; do
      ancestor=${ancestor%/*}
    done
    [ -x "${ancestor:-/}" ]
  }
  local -a dead=()
  local candidate
  for candidate in "${decided[@]:1}"; do
    # The filter offers absolute paths only; anything else reached this stream from
    # somewhere the filter does not speak for.
    if [[ $candidate == /* ]] && provably_absent "$candidate"; then
      dead+=("$candidate")
    fi
  done
  if [ "${decided[0]}" = current ] && (( ${#dead[@]} == 0 )); then
    return 0
  fi
  local updated
  if ! updated=$(mktemp "${config_path}.trust.XXXXXX"); then
    echo "claude-workspace-trust: cannot create a temporary file beside $config_path; fix directory permissions, then retry" >&2
    return 1
  fi
  trust_temporaries+=("$updated")
  local dead_source=/dev/null
  if (( ${#dead[@]} > 0 )); then
    if ! dead_source=$(mktemp "${config_path}.trust.XXXXXX"); then
      echo "claude-workspace-trust: cannot create a temporary file beside $config_path; fix directory permissions, then retry" >&2
      return 1
    fi
    trust_temporaries+=("$dead_source")
    # The decision filter never offers a key containing a newline, so a line is a path.
    if ! printf '%s\n' "${dead[@]}" >"$dead_source"; then
      echo "claude-workspace-trust: cannot record the stale entries of $config_path; verify available disk space, then retry" >&2
      return 1
    fi
  fi
  if ! jq --rawfile deadlist "$dead_source" --arg trust_key "$CLAUDE_TRUST_KEY" \
    "$CLAUDE_TRUST_UPDATE_FILTER" "$config_path" \
    --args "$@" >"$updated" </dev/null; then
    echo "claude-workspace-trust: cannot add $* to $config_path; verify the JSON and available disk space, then retry" >&2
    return 1
  fi
  if ! chmod --reference="$config_path" "$updated"; then
    echo "claude-workspace-trust: cannot preserve permissions for $config_path; fix file ownership, then retry" >&2
    return 1
  fi
  if ! mv "$updated" "$config_path"; then
    echo "claude-workspace-trust: cannot atomically replace $config_path; fix directory permissions, then retry" >&2
    return 1
  fi
  forget_trust_file "$updated"
)

# Every claude-code configuration a dispatch of this host can run under, one absolute
# path per line, in the order the harness chains name their identities. Every chain
# names all four claude-code identities and differs only in order, and the primary is
# last on every one of them, just after the primary-backup: those two are what run once
# both alternate subscriptions are exhausted, which is exactly when this host is under
# load. Naming only some of them therefore leaves a chain trusted in some directories
# and blocked in the rest at the moment it needs them most.
#
# Every identity — the primary included — is named by the indirection oneharness maps
# into its variant's CLAUDE_CONFIG_DIR, as scripts/claude-alt-config-dir.sh resolves it,
# and claude-code with CLAUDE_CONFIG_DIR set keeps its configuration at
# `$CLAUDE_CONFIG_DIR/.claude.json`. `$HOME/.claude.json` is deliberately not named: it
# is the file claude-code keeps when nothing sets CLAUDE_CONFIG_DIR, and no chain
# identity runs that way any more.
#
# An identity this cannot name costs only that identity. A misconfigured override says
# nothing about the others, so reporting and continuing marks three identities where
# returning would have marked none. A refused identities file is the one failure shared
# by several: it costs every identity the environment does not name outright, because a
# file that was refused cannot say which directory it meant.
claude_trust_config_paths() {
  if (( $# < 1 )); then
    echo "claude-workspace-trust: a caller name is required" >&2
    return 2
  fi
  local caller=$1 status=0 file_refused=false variable
  if ! _load_claude_identities_file "$caller"; then
    echo "$caller: the Claude identities file was refused, so only the identities the environment names are named; correct the file, then retry; continuing" >&2
    status=1
    file_refused=true
  fi
  for variable in "${CLAUDE_IDENTITY_CONFIG_VARIABLES[@]}"; do
    if [[ $file_refused == true && -z ${!variable-} ]]; then
      echo "$caller: $variable cannot be resolved while the Claude identities file is refused, so that identity's Claude config is not named; correct the identities file as the refusal above says, or set $variable in the environment, then retry; continuing" >&2
      status=1
    elif resolve_claude_identity_config_dir "$caller" "$variable"; then
      # llmlint: ignore[tool_output_is_signal] Each line is this function's return value, not a success message: mark_claude_workspaces reads the stream into an array with mapfile, one configuration path per chain identity, as it read one per identity before this change.
      printf '%s\n' "${!variable}/.claude.json"
    else
      echo "$caller: $variable resolution failed, so that identity's Claude config is not named; correct the configured path, then retry; continuing" >&2
      status=1
    fi
  done
  return "$status"
}

mark_claude_workspaces() {
  if (( $# < 2 )); then
    echo "claude-workspace-trust: caller name and at least one workspace path are required" >&2
    return 2
  fi
  local caller=$1
  shift
  # A subshell, so a resolver that aborts on an unset HOME takes the pass that named
  # the paths with it rather than this caller — which for the `post-checkout` hook is
  # the difference between marking what it could and failing a checkout. Its own
  # diagnostics still reach this stderr, and nothing it exports is read here.
  local -a config_paths=()
  mapfile -t config_paths < <(claude_trust_config_paths "$caller")
  local config_path
  for config_path in ${config_paths[@]+"${config_paths[@]}"}; do
    mark_claude_config_trust "$config_path" "$@" \
      || echo "$caller: Claude workspace trust setup failed for $config_path; correct the preceding error, then retry; continuing" >&2
  done
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  if ! SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd); then
    echo "claude-workspace-trust: cannot resolve its script directory; restore directory access, then retry" >&2
    exit 1
  fi
  # The standalone entry point resolves this fixed sibling path at runtime.
  # shellcheck source=scripts/claude-alt-config-dir.sh
  source "$SCRIPT_DIR/claude-alt-config-dir.sh" \
    || { echo "claude-workspace-trust: cannot load the alternate config resolver; restore scripts/claude-alt-config-dir.sh, then retry" >&2; false; }
  mark_claude_workspaces claude-workspace-trust "$@"
fi
