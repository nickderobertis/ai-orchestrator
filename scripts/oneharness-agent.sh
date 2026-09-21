#!/usr/bin/env bash
# llmlint: ignore-file[changed_behavior_has_e2e] subprocess tests drive every wrapper branch; only the paid oneharness child is replaced at the repository's designated external seam.
# Force the orchestrator's agent config; target-project discovery must not override it.
#
# onejudge routes BOTH conversation sides through this one provider.bin, so this wrapper
# must decide which side each turn is. A config is named to select the side and its own
# `history_labels` `role` must agree; a disagreement stops the turn rather than being
# resolved by guessing, because a turn routed as the side it is not still runs and
# answers. Never reintroduce a rule based on a config being ABSENT: that held only while
# onejudge left the agent side's implicit, and onepipeline 0.3.1 names both.
# See docs/onejudge-integration.md, "The judge side is the one named oneharness.judge.toml".
#
# A caller that names its own config keeps it, on either side: `oneharness run` rejects a
# repeated `--config`, and a dispatched agent side's config is the one its graph pinned.
# The agent config below is forced only when the caller named none.
#
# That same branch is where each side's harness SELECTION is resolved. oneharness's
# own ONEHARNESS_HARNESSES is process-wide and beats config, so one value set by the
# parent would move both sides at once. ORCHESTRATOR_WORKER_HARNESSES and
# ORCHESTRATOR_JUDGE_HARNESSES are per-side instead: each is applied to only its own
# branch's `exec`, so a side carrying an explicit value never inherits the other
# side's — nor an ambient process-wide one. See orchestrator/harnesses.py, which
# validates both against the configs before a dispatch ever starts.
#
# ORCHESTRATOR_WORKER_MODEL and ORCHESTRATOR_JUDGE_MODEL are the model half of that
# same seam, applied the same way and on the same two branches. Each is applied
# twice, because the two mechanisms cover different ground: `--model` on this
# branch's own `oneharness run` is the only one that beats the `model` a config pins
# for the selected harness (oneharness 0.16.0 lets that config value beat
# ONEHARNESS_MODEL), and the exported ONEHARNESS_MODEL is what carries the side's
# choice to everything it subsequently runs. That precedence is a fact about one
# release, so config/oneharness.version owns the literal above and
# tests/test_onejudge_version.py fails here on an upgrade until it is re-measured.
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(dirname -- "$script_dir")
# The worker config maps these portable, non-secret parent values into
# CLAUDE_CONFIG_DIR for each of its Claude-subscription children; the derivation is
# shared with the other wrappers so the roles cannot drift apart.
alt_config_helper="$script_dir/claude-alt-config-dir.sh"
if [ ! -f "$alt_config_helper" ] || [ ! -r "$alt_config_helper" ]; then
    echo "oneharness-agent: required helper is not a readable regular file: $alt_config_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/claude-alt-config-dir.sh
. "$alt_config_helper"
resolve_claude_alt_config_dir oneharness-agent || exit $?
alternate_config_dir=$ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR
alternate2_config_dir=$ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR
primary_backup_config_dir=$ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR
# The worker chain's last candidate is a second Codex identity, whose variant maps
# this portable value into CODEX_HOME. oneharness refuses to run when the
# indirection is unset, so it must be exported even on a host that never
# authenticated one; see the fallthrough note in the helper.
codex_alt_helper="$script_dir/codex-alt-home.sh"
if [ ! -f "$codex_alt_helper" ] || [ ! -r "$codex_alt_helper" ]; then
    echo "oneharness-agent: required helper is not a readable regular file: $codex_alt_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/codex-alt-home.sh
. "$codex_alt_helper"
ensure_codex_alt_home oneharness-agent || exit $?
# Both dispatch configs map a turn's XDG_RUNTIME_DIR out of the dispatch's own scratch,
# so the runtime directory a turn writes into is the work it is doing rather than the
# launching session's — which on this host is mounted `noexec`, and is why a shebang
# recipe failed inside every dispatch. Inside a dispatch the engine has already set that
# variable and the helper leaves it alone; this wrapper is the caller that is not one,
# and oneharness refuses to start a variant whose indirection is unset.
node_scratch_helper="$script_dir/node-scratch-dir.sh"
if [ ! -f "$node_scratch_helper" ] || [ ! -r "$node_scratch_helper" ]; then
    echo "oneharness-agent: required helper is not a readable regular file: $node_scratch_helper; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
# shellcheck source=scripts/node-scratch-dir.sh
. "$node_scratch_helper" || {
    echo "oneharness-agent: required helper $node_scratch_helper could not be loaded; it is readable but did not load — restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
}
if ! command -v ensure_node_scratch_dir >/dev/null 2>&1; then
    echo "oneharness-agent: helper $node_scratch_helper loaded but defines no ensure_node_scratch_dir; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
ensure_node_scratch_dir oneharness-agent || exit $?
alternate_harness=claude-code:alternate
agent_config="$repo_root/oneharness.toml"
# The judge side is identified by this basename, and only by it. The name is restated
# here rather than derived from anything — nothing at run time can tell the wrapper
# which config onejudge hands the judge — so the copy is reconciled with the declaring
# side, `config/onejudge.base.yaml`'s `judge_config:`, by
# tests/test_dispatch_environment_contract.py. That test is what makes a rename fail
# loudly instead of routing every turn as the agent side.
judge_config="$repo_root/oneharness.judge.toml"
judge_config_name=${judge_config##*/}
# Both are `run` flags with no config key, so this wrapper is the only place the
# agent side can adopt either, and neither may be repeated -- hence two arrays
# rather than one string, each emptied where the caller already asked for it. The
# selection between them is made below; why it exists is in
# docs/onejudge-integration.md, "Streaming the agent side".
agent_events=(--events)
agent_stream=()
# The `--model` this branch names, empty unless its side was given one. Placed ahead
# of the caller's arguments below because oneharness honors the FIRST --model it is
# given, so a hand-run diagnostic that passes one of its own cannot displace the
# side's choice silently.
side_model=()
# The filter that reconciles a streamed turn with onejudge, which parses this
# process's stdout as exactly one JSON document. See the script for what it does.
stream_filter="$script_dir/oneharness-stream.py"

# Emit the identities a config's `harnesses` chain names, in order, one per line;
# emit nothing when it declares none, and fail non-zero when the file cannot be
# read, cannot be parsed, or declares a chain that is not a list of identities. A
# malformed chain is refused rather than filtered down to its usable members: the
# selection it would authorize is not the one the file was trying to declare.
#
# Read with tomllib rather than by scanning the file here, because this is the
# second reader of one contract: orchestrator/harnesses.py validates a selection
# against the same key before a dispatch starts, and this wrapper checks it again
# at the variable's own boundary. A hand-rolled scanner would be a second answer to
# the same question — one that could take an identity quoted inside a comment for a
# configured one, or miss one a valid file wrote differently.

# The one interpreter every python helper here runs: this repository's own where it
# exists, so both readers of that contract run the same tomllib and the stream filter
# runs the same build the rest of the harness does. A worktree with no virtualenv yet
# still resolves the system `python3`.
repo_interpreter() {
    if [ -x "$repo_root/.venv/bin/python3" ]; then
        printf '%s\n' "$repo_root/.venv/bin/python3"
    else
        printf '%s\n' python3
    fi
}

config_harness_chain() {
    local interpreter
    interpreter=$(repo_interpreter)
    "$interpreter" -c '
import sys, tomllib

try:
    with open(sys.argv[1], "rb") as config:
        chain = tomllib.load(config).get("harnesses")
except (OSError, tomllib.TOMLDecodeError) as error:
    sys.exit(f"oneharness-agent: cannot read {sys.argv[1]}: {error}")
if chain is None:
    sys.exit(0)
if not isinstance(chain, list) or not all(
    isinstance(identity, str) and identity for identity in chain
):
    sys.exit(f"oneharness-agent: {sys.argv[1]} declares a malformed harnesses chain: {chain!r}")
for identity in chain:
    print(identity)
' "$1"
}

# The `role` a config stamps on its own history entries, or nothing when it stamps
# none. This is what a config says it IS, written by whoever wrote the file and
# carried verbatim into a member's scratch — the agent-side configs here declare
# `role = "agent"` and the judge's declares `role = "judge"`. Used to confirm that a
# config named like the judge's really is the judge artifact, so the branch that
# grants judge-side routing turns on the file's own declared identity and not only on
# a basename a caller chose.
config_role() {
    local interpreter
    interpreter=$(repo_interpreter)
    "$interpreter" -c '
import sys, tomllib

try:
    with open(sys.argv[1], "rb") as config:
        labels = tomllib.load(config).get("history_labels")
except (OSError, tomllib.TOMLDecodeError) as error:
    sys.exit(f"oneharness-agent: cannot read {sys.argv[1]}: {error}")
if not isinstance(labels, dict):
    sys.exit(0)
role = labels.get("role")
if role is None:
    sys.exit(0)
if not isinstance(role, str):
    sys.exit(f"oneharness-agent: {sys.argv[1]} declares a malformed history_labels role: {role!r}")
print(role)
' "$1"
}

# Apply one side's selection to this process only, after checking it against the
# config that side is about to run from. The dispatch layer validates the same way
# before anything starts (orchestrator/harnesses.py), but this is the boundary a
# hand-set variable arrives at, and a selection nobody can honor must stop the turn
# rather than reach oneharness as a chain it will run something else for.
# $1 names the variable for diagnostics, $2 is its value, $3 the config to check.
apply_side_selection() {
    local variable=$1 value=$2 config=$3 selectable candidate
    local -a requested
    if ! selectable=$(config_harness_chain "$config" | tr '\n' ' '); then
        echo "oneharness-agent: cannot read the harness chain $variable is selected from; correct $config, or unset $variable to use its chain in order, then retry" >&2
        return 2
    fi
    if [ -z "${selectable// /}" ]; then
        echo "oneharness-agent: $config declares no 'harnesses' chain to select from; restore it from the repository, then retry" >&2
        return 2
    fi
    # Split on commas alone: an unquoted expansion would also glob, so a value
    # containing `*` could silently become whatever the cwd happens to hold.
    IFS=',' read -r -a requested <<<"$value"
    # Space-delimited on both sides so one identity cannot match another by prefix.
    for candidate in "${requested[@]}"; do
        case " $selectable" in
            *" $candidate "*) ;;
            *)
                echo "oneharness-agent: $variable '$value': '$candidate' is not a harness $config configures; select from ${selectable% }" >&2
                return 2
                ;;
        esac
    done
    export ONEHARNESS_HARNESSES="$value"
}

# Apply one side's model to this process only. The value itself is deliberately not
# checked — it is passed through to the harness the operator named in the same
# breath, where an unknown name fails loudly — but the pairing is, in both of its
# halves. Without that side's identity, one model would reach whichever candidate the
# configured chain selects; with an identity spanning two harness families, it
# reaches a candidate of the wrong one — say a Claude model name on a codex
# candidate. Either way `fallback` does not fall through a *task* failure, so the
# dispatch dies on a provider rejection instead of degrading, which is the outcome
# the pairing rule exists to make unconstructable. orchestrator/harnesses.py refuses
# the same combinations before a dispatch starts; this is the boundary a hand-set
# variable arrives at, having passed through none of that.
# $1 names the model variable for diagnostics, $2 is its value, $3 names the harness
# variable its side must also carry.
apply_side_model() {
    local model_variable=$1 model_value=$2 harness_variable=$3
    local harness_value candidate family seen=' ' families='' family_count=0
    local -a requested
    harness_value=${!harness_variable-}
    if [ -z "$harness_value" ]; then
        echo "oneharness-agent: $model_variable '$model_value' requires $harness_variable, which names the identity the model belongs to; set both, or unset $model_variable to use the model this side's config pins, then retry" >&2
        return 2
    fi
    # Split on commas alone, as the selection above does: an unquoted expansion would
    # also glob, so a value containing `*` could become whatever the cwd happens to
    # hold. A composed identity names its family before the colon.
    IFS=',' read -r -a requested <<<"$harness_value"
    for candidate in "${requested[@]}"; do
        family=${candidate%%:*}
        case "$seen" in
            *" $family "*) continue ;;
        esac
        seen="${seen}${family} "
        families="${families:+$families, }$family"
        family_count=$((family_count + 1))
    done
    if [ "$family_count" -gt 1 ]; then
        echo "oneharness-agent: $model_variable '$model_value': $harness_variable '$harness_value' spans $families, and one model cannot name a model of each; narrow $harness_variable to identities of a single harness, then retry" >&2
        return 2
    fi
    # llmlint: ignore-block[boundary_inputs_validated] The pairing is validated above —
    # an identity this side's config does not configure, and one spanning two harness
    # families, are both refused before this point. The model *value* is deliberately
    # not, and that asymmetry is the design: an identity selects credentials and
    # environment routing only this repository configures, while a model name belongs to
    # the provider the operator named in the same breath, where an unknown one fails
    # loudly rather than quietly running something else. It crosses no shell boundary
    # here — argv element and exported value, never a word this script expands.
    side_model=(--model "$model_value")
    export ONEHARNESS_MODEL="$model_value"
    # llmlint: ignore-end[boundary_inputs_validated]
}

# Whether one `key=value` pair satisfies oneharness's history-label contract: a key
# of 1-64 ASCII letters/digits/dot/underscore/hyphen starting alphanumeric, and a
# non-empty value of at most 256 characters carrying no control character. The comma
# the contract also forbids cannot survive the split below. This mirrors
# orchestrator/labels.py, which is the validating *writer* of the same contract; this
# is the boundary a value hand-set in the environment arrives at instead.
valid_history_label() {
    local pair=$1 key value
    case "$pair" in
        *=*) ;;
        *) return 1 ;;
    esac
    key=${pair%%=*}
    value=${pair#*=}
    [[ $key =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || return 1
    [ -n "$value" ] && [ "${#value}" -le 256 ] || return 1
    [[ $value =~ [[:cntrl:]] ]] && return 1
    return 0
}

# Drop one key from this process's ONEHARNESS_HISTORY_LABELS, keeping every other
# valid pair in the order it arrived. A pair that violates the contract is dropped
# rather than rewritten or passed on, matching how `orchestrator.labels.parse_labels`
# treats an inherited value: what this rewrites is not this process's to correct, and
# a rewrite must not hand oneharness a list it would refuse. The variable is unset
# rather than left empty when nothing survives, because an empty list is not a value
# oneharness accepts.
drop_history_label() {
    local key=$1 pair
    local kept=''
    local -a pairs
    [ -n "${ONEHARNESS_HISTORY_LABELS-}" ] || return 0
    # Split on commas alone, the wire format's one separator; an unquoted expansion
    # would also glob, so a value containing `*` could become whatever the cwd holds.
    IFS=',' read -r -a pairs <<<"$ONEHARNESS_HISTORY_LABELS"
    for pair in "${pairs[@]}"; do
        case "$pair" in
            "$key="*) continue ;;
        esac
        valid_history_label "$pair" || continue
        kept="${kept:+$kept,}$pair"
    done
    if [ -n "$kept" ]; then
        export ONEHARNESS_HISTORY_LABELS="$kept"
    else
        unset ONEHARNESS_HISTORY_LABELS
    fi
}

if [ "${1-}" != "run" ]; then
    echo "oneharness-agent: expected the 'run' subcommand; invoke through onejudge dispatch or retry as 'scripts/oneharness-agent.sh run ...'" >&2
    exit 2
fi
shift

caller_config=false
caller_config_path=
caller_stream=false
expect_config_value=false
# llmlint: ignore[boundary_inputs_validated] this repository's dispatch layer is the only caller and passes exactly one --config; oneharness honors the last value, which this wrapper validates.
for arg in "$@"; do
    if [[ $expect_config_value == true ]]; then
        if [[ -z $arg ]]; then
            echo "oneharness-agent: --config requires a non-empty path; retry with '--config /absolute/path/to/config.toml'" >&2
            exit 2
        fi
        caller_config_path=$arg
        expect_config_value=false
        continue
    fi
    case "$arg" in
        --config)
            if [[ $caller_config == true ]]; then
                echo "oneharness-agent: --config may be provided only once; remove duplicate config arguments and retry" >&2
                exit 2
            fi
            caller_config=true
            expect_config_value=true
            ;;
        --events)
            agent_events=()
            ;;
        --stream)
            # A caller that streams owns its own stdout shape, so this wrapper adds
            # neither a second `--stream` (oneharness refuses the repeat) nor the
            # filter that would rewrite the stream into a buffered report.
            caller_stream=true
            ;;
        --config=*)
            if [[ $caller_config == true ]]; then
                echo "oneharness-agent: --config may be provided only once; remove duplicate config arguments and retry" >&2
                exit 2
            fi
            if [[ -z ${arg#--config=} ]]; then
                echo "oneharness-agent: --config requires a non-empty path; retry with '--config=/absolute/path/to/config.toml'" >&2
                exit 2
            fi
            caller_config=true
            caller_config_path=${arg#--config=}
            ;;
    esac
done
if [[ $expect_config_value == true ]]; then
    echo "oneharness-agent: --config requires a path; retry with '--config /absolute/path/to/config.toml'" >&2
    exit 2
fi
if [[ $caller_config == true ]]; then
    if [[ ! -f "$caller_config_path" || ! -r "$caller_config_path" ]]; then
        echo "oneharness-agent: caller config is not a readable regular file: $caller_config_path; correct the path and retry" >&2
        exit 2
    fi
fi
# Which side this turn is. The name selects it and the config's own declared role must
# be exactly the role of that side — no other value, and not an absent one. So there is
# no config a caller can name that routes a side without saying it is that side.
#
# Every config that reaches here declares one: `oneharness.judge.toml` says `judge`, and
# the agent, orchestrator, and check-in configs all say `agent`. (`oneharness.llmlint.toml`
# says `llmlint` and never arrives here; it goes through scripts/llmlint-oneharness.sh.)
#
# A turn with no config is the agent side, and runs from the repository's own agent
# config below rather than from anything a caller chose — held to the same declared
# role there, once that file is known to be readable.
caller_is_judge=false
if [[ $caller_config == true ]]; then
    if [[ ${caller_config_path##*/} == "$judge_config_name" ]]; then
        caller_is_judge=true
        required_role=judge
    else
        required_role=agent
    fi
    if ! caller_role=$(config_role "$caller_config_path"); then
        echo "oneharness-agent: cannot read the role $caller_config_path declares; correct the file, then retry" >&2
        exit 2
    fi
    if [[ $caller_role != "$required_role" ]]; then
        echo "oneharness-agent: $caller_config_path would run as the ${required_role} side, being $([ "$caller_is_judge" = true ] && echo "named $judge_config_name" || echo "not named $judge_config_name"), but declares history_labels role '${caller_role:-none}' rather than '$required_role'; give this side its own config, or set that role, then retry" >&2
        exit 2
    fi
fi

if [[ $caller_is_judge == true ]]; then
    # This is the judge / simulated-user side, so only its own override applies —
    # and it applies over whatever ONEHARNESS_HARNESSES the parent exported, which
    # is what keeps a worker-side selection from reaching this conversation. It is
    # checked against the caller's own config, the one this turn will run from.
    if [ -n "${ORCHESTRATOR_JUDGE_HARNESSES-}" ]; then
        apply_side_selection ORCHESTRATOR_JUDGE_HARNESSES \
            "$ORCHESTRATOR_JUDGE_HARNESSES" "$caller_config_path" || exit "$?"
    fi
    # Only the judge's own model, for the same reason: the worker's arrives under a
    # variable this branch never reads, so it cannot reach this conversation.
    if [ -n "${ORCHESTRATOR_JUDGE_MODEL-}" ]; then
        apply_side_model ORCHESTRATOR_JUDGE_MODEL \
            "$ORCHESTRATOR_JUDGE_MODEL" ORCHESTRATOR_JUDGE_HARNESSES || exit "$?"
    fi
    # The `agent_role` a dispatch stamps names the WORKER it dispatched, and this is
    # the other side of that conversation. oneharness merges history labels
    # CLI > env > project file, so that inherited value outranked
    # oneharness.judge.toml's own `agent_role = "judge"` and every supervisor session
    # in the store was recorded as its worker's role — which is what showed an
    # operator a strict-evaluator transcript under a row labelled "worker". This
    # branch is the one place that knows which side it is, so the key is dropped here
    # and the judge config's own label stands.
    #
    # Every other inherited label is kept exactly as it arrived, and on the adopted
    # engine that set has a name: the six keys under the `onepipeline.` prefix —
    # onepipeline.run_id, onepipeline.project, onepipeline.scope, onepipeline.node,
    # onepipeline.step and onepipeline.attempt — which are what say where in the graph
    # this session was opened. They are the engine's to compose and not this script's to
    # touch; `orchestrator/labels.py` declares them and
    # `tests/test_engine_history_vocabulary.py` holds each spelling to the pinned
    # engine's own contract. The one key this drops is this host's, which is exactly why
    # dropping it here is safe.
    drop_history_label agent_role
    # Keep every portable indirection available while oneharness resolves config: each
    # of the judge's Claude variants, the primary included, reads its CLAUDE_CONFIG_DIR
    # from one of them, and oneharness may also discover and layer the project config
    # before applying the caller's --config.
    exec oneharness run "${side_model[@]}" "$@"
fi

# `agent_config` becomes whichever config this turn runs from, so the chain validation
# and alternate substitution below judge that file rather than one it may not be using.
# The caller's is already in "$@", where a second --config would be refused.
agent_config_flag=(--config "$agent_config")
if [[ $caller_config == true ]]; then
    agent_config=$caller_config_path
    agent_config_flag=()
fi

if [ ! -f "$agent_config" ] || [ ! -r "$agent_config" ]; then
    echo "oneharness-agent: required agent config is not a readable regular file: $agent_config; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 2
fi
if [[ $caller_config == false ]]; then
    # The implicit config is held to the same declared role as a caller's, so the
    # invariant is total: no config runs a side of this conversation without saying it
    # is that side. It is this repository's own tracked file rather than caller input,
    # which is why it is checked here — after the readability message above, which is
    # the more useful one when it is simply missing — and not in the caller block. The
    # gap this closes is quiet: an `oneharness.toml` whose role stopped saying `agent`
    # would still run every implicit turn, stamping the worker's sessions with another
    # side's label, and nothing else reads that field early enough to notice.
    if ! agent_role=$(config_role "$agent_config"); then
        echo "oneharness-agent: cannot read the role $agent_config declares; correct the file, then retry" >&2
        exit 2
    fi
    if [[ $agent_role != agent ]]; then
        echo "oneharness-agent: $agent_config runs this turn's agent side but declares history_labels role '${agent_role:-none}' rather than 'agent'; set that role, then retry" >&2
        exit 2
    fi
fi
if [ -n "${ORCHESTRATOR_WORKER_HARNESSES-}" ]; then
    # This is the agent side, so only its own override applies — over an ambient
    # ONEHARNESS_HARNESSES as well, since a process-wide value the parent exported
    # is exactly what an explicit per-side choice exists to displace.
    #
    # Taken verbatim: the filtering below narrows a chain nobody chose, whereas
    # dropping an identity an operator named would run a provider they did not ask
    # for. An unauthenticated one fails at the provider instead, loudly.
    apply_side_selection ORCHESTRATOR_WORKER_HARNESSES \
        "$ORCHESTRATOR_WORKER_HARNESSES" "$agent_config" || exit "$?"
elif [ -z "${ONEHARNESS_HARNESSES-}" ]; then
    # An alternate or primary-backup Claude subscription whose config directory does
    # not exist is a candidate this host has never set up. claude-code would still
    # start, create that directory, and report `auth` — so the chain recovers either
    # way, but substituting a filtered one keeps the dispatch from writing a config
    # directory for an account nobody has logged into.
    #
    # Only those absent candidates are dropped: every other identity keeps its
    # configured relative order, including the OTHER optional subscriptions when just
    # one is missing, both Codex identities, and the primary Claude one — which is
    # never dropped, whatever its directory's state, because it is the last resort
    # every host is expected to have. The chain is read from the config rather than
    # restated here, so this can only ever be a subsequence of what oneharness would
    # have selected.
    # Space-delimited on BOTH sides, so `claude-code:alternate` cannot match the
    # `claude-code:alternate2` entry by prefix and drop a candidate that is present.
    absent_identities=" "
    [ -e "$alternate_config_dir" ] || absent_identities="${absent_identities}claude-code:alternate "
    [ -e "$alternate2_config_dir" ] || absent_identities="${absent_identities}claude-code:alternate2 "
    [ -e "$primary_backup_config_dir" ] || absent_identities="${absent_identities}claude-code:primary-backup "
    if [ "$absent_identities" != " " ]; then
        substituted=
        # Read the chain into a variable first: inside a process substitution the
        # reader's own failure would be invisible here, and a config it could not
        # parse would look exactly like one that named nothing to drop.
        if configured_chain=$(config_harness_chain "$agent_config"); then
            while read -r candidate; do
                case "$absent_identities" in
                    *" $candidate "*) continue ;;
                esac
                substituted="${substituted:+$substituted,}$candidate"
            done <<<"$configured_chain"
        else
            echo "oneharness-agent: could not read the configured harness chain from $agent_config (the reader's own error is above); leaving the selection to oneharness — an absent alternate identity may then be tried, so fix the file until 'oneharness config --config $agent_config' loads it" >&2
        fi
        # An empty result means the config declared no chain to narrow; leave the
        # selection alone rather than narrowing it on a guess.
        if [ -n "$substituted" ]; then
            export ONEHARNESS_HARNESSES="$substituted"
        fi
    fi
fi

# Only the worker's own model, and only on this branch — the judge's arrives under a
# variable nothing here reads, so it cannot reach this conversation.
if [ -n "${ORCHESTRATOR_WORKER_MODEL-}" ]; then
    apply_side_model ORCHESTRATOR_WORKER_MODEL \
        "$ORCHESTRATOR_WORKER_MODEL" ORCHESTRATOR_WORKER_HARNESSES || exit "$?"
fi

if [ -z "${ORCHESTRATOR_AGENT_STATUS_DIR-}" ]; then
    # No status directory means no dispatch is watching, so a streamed turn would
    # have nowhere to publish and nobody to read it. `--events` carries the identical
    # transcript in the end-of-turn report, with one fewer moving part and without
    # replacing this `exec` with a filtered pipeline.
    exec oneharness run "${agent_config_flag[@]}" "${side_model[@]}" "${agent_events[@]}" "$@"
fi

status_dir=$ORCHESTRATOR_AGENT_STATUS_DIR
case "$status_dir" in
    /*/orchestrator-watchdog-*/agent) ;;
    *)
        echo "oneharness-agent: invalid worker status directory; retry through orchestrator dispatch" >&2
        exit 2
        ;;
esac
if [ ! -d "$status_dir" ] || [ -L "$status_dir" ]; then
    echo "oneharness-agent: worker status directory is absent or unsafe; retry through orchestrator dispatch" >&2
    exit 2
fi
write_status() {
    status_name=$1
    status_value=$2
    if ! printf '%s\n' "$status_value" >"$status_dir/$status_name.tmp" ||
        ! mv "$status_dir/$status_name.tmp" "$status_dir/$status_name"; then
        echo "oneharness-agent: cannot update $status_name; retry through orchestrator dispatch" >&2
        exit 2
    fi
}
worker_pid=$$
write_status agent.pid "$worker_pid"
if ! rm -f "$status_dir/agent.done" "$status_dir/agent.failed" "$status_dir/agent.exit_code" \
    "$status_dir/agent.failure"; then
    echo "oneharness-agent: cannot reset terminal markers; retry through orchestrator dispatch" >&2
    exit 2
fi
heartbeat_sequence=0
write_status agent.heartbeat "$heartbeat_sequence"
agent_activity=$status_dir/agent.activity

# Ask oneharness itself whether this invocation can be streamed, rather than
# predicting it here: `--print-command` applies a real run's validation and spawns
# nothing, so one question covers every reason the answer might be no. The caller's
# arguments go in verbatim so it describes the real invocation, and stdin is closed
# so a `--prompt-file -` cannot eat the task this turn is about to be given.
# See docs/onejudge-integration.md, "Streaming the agent side".
stream_supported() {
    oneharness run "${agent_config_flag[@]}" "${side_model[@]}" --stream --print-command "$@" \
        >/dev/null 2>&1 </dev/null
}

stream_events=false
if [ "$caller_stream" = false ] && [ -f "$stream_filter" ] && [ -r "$stream_filter" ] &&
    stream_supported "$@"; then
    stream_events=true
    agent_stream=(--stream)
fi

# onejudge hands the agent its task on stdin, but a non-interactive shell assigns /dev/null to
# an asynchronous list's stdin before any explicit redirection, so the backgrounded agent below
# would read an empty prompt and ask for a subtask every turn until it hit the cap. The judge,
# which reaches oneharness through the `exec` pass-throughs above, is never backgrounded and so
# always saw its task -- that asymmetry is the bug. `<&0` would only re-duplicate the /dev/null
# already on fd 0, so save the real stdin here and redirect it back explicitly below.
# llmlint: ignore[boundary_inputs_validated] this duplicates a file descriptor; the payload it
# carries is the onejudge protocol that oneharness itself parses and validates.
exec 3<&0
# A worker that dies before its first turn produces no report and no transcript, so
# the child's own stderr is the only account of why — throttling, quota exhaustion,
# an OOM kill, and a genuine crash are indistinguishable without it. Park it beside
# the terminal markers rather than letting it vanish with the process tree the
# dispatcher is about to tear down; a failing exit replays it below, a successful one
# does not. Credential values are stripped when the dispatcher reads this back.
agent_stderr=$status_dir/agent.stderr
agent_stdout=$status_dir/agent.stdout
if ! : >"$agent_stderr"; then
    echo "oneharness-agent: cannot open the agent stderr record; retry through orchestrator dispatch" >&2
    exit 2
fi
if ! : >"$agent_stdout"; then
    echo "oneharness-agent: cannot open the agent stdout record; retry through orchestrator dispatch" >&2
    exit 2
fi
stdout_fifo=$status_dir/.agent-stdout-pipe
if ! mkfifo "$stdout_fifo"; then
    echo "oneharness-agent: cannot create the agent stdout capture pipe; retry through orchestrator dispatch" >&2
    exit 2
fi
# Both readers say nothing unless the capture itself fails, and that account goes
# into the same durable record the child's own stderr does — appended, so the child
# truncating it at open cannot take the reader's reason with it. Left on the
# wrapper's own stderr it would vanish with the process tree the dispatcher tears
# down, which is the one place a failure explains itself.
if [ "$stream_events" = true ]; then
    # The streamed conduit: the same transparent stdout capture, plus the live
    # activity publication and the unwrapping onejudge's single-document parse
    # needs. Both readers write `$agent_stdout` byte for byte, so everything that
    # reads the raw record back — the quota diagnostic below, the dispatcher —
    # cannot tell which one ran.
    # llmlint: ignore[tool_output_is_signal] the stream filter is the transparent stdout side of the oneharness protocol conduit.
    "$(repo_interpreter)" "$stream_filter" "$agent_stdout" "$agent_activity" <"$stdout_fifo" 2>>"$agent_stderr" &
else
    # llmlint: ignore[tool_output_is_signal] tee is the transparent stdout side of the oneharness protocol conduit.
    tee "$agent_stdout" <"$stdout_fifo" 2>>"$agent_stderr" &
fi
capture_pid=$!
# llmlint: ignore[boundary_inputs_validated] oneharness parses and validates its own protocol input.
oneharness run "${agent_config_flag[@]}" "${side_model[@]}" "${agent_stream[@]}" "${agent_events[@]}" "$@" <&3 >"$stdout_fifo" 2>"$agent_stderr" &
agent_pid=$!
write_status agent.child.pid "$agent_pid"
while agent_state=$(ps -o stat= -p "$agent_pid" 2>/dev/null) &&
    [ -n "$agent_state" ] &&
    [ "${agent_state#Z}" = "$agent_state" ]; do
    heartbeat_sequence=$((heartbeat_sequence + 1))
    write_status agent.heartbeat "$heartbeat_sequence"
    sleep 0.5
done
set +e
wait "$agent_pid"
exit_code=$?
wait "$capture_pid"
capture_exit_code=$?
# llmlint: ignore[robust_shell] Cleanup is best-effort after both processes have settled; a stale FIFO makes the next launch fail closed at mkfifo with its actionable diagnostic.
rm -f "$stdout_fifo"
set -e
if [ "$capture_exit_code" -ne 0 ]; then
    echo "oneharness-agent: agent stdout capture failed with exit $capture_exit_code; the turn's own output above this line is all that was kept, and the reader's reason is at $agent_stderr — retry through orchestrator dispatch, which creates the status directory the capture writes into" >>"$agent_stderr"
    if [ "$exit_code" -eq 0 ]; then
        exit_code=2
    fi
fi
# Record the status before replaying the stream: the dispatcher can conclude this
# worker died the moment the child leaves the process tree, and a large stderr
# would otherwise let it reach that conclusion before the reason was written down.
write_status agent.exit_code "$exit_code"
if [ "$exit_code" -ne 0 ]; then
    if grep -Fq "was created on harness" "$agent_stderr" &&
        grep -Fq "cannot be continued on" "$agent_stderr"; then
        echo "oneharness-agent: dispatch failure: session/harness binding rejection; the named session and both harnesses are shown below; retry with a new --session or the originally bound harness" >>"$agent_stderr"
    fi
    quota_line=$(grep -E -m1 "hit your (session|usage) limit|quota exhausted|rate.?limit" "$agent_stdout" || true)
    if [ -n "$quota_line" ]; then
        echo "oneharness-agent: dispatch failure: harness $alternate_harness is out of quota; $quota_line; configure a usable fallback or retry after the stated reset time" >>"$agent_stderr"
    fi
    # Replay the child's own words only now. A turn that succeeded says everything
    # it has to say through the protocol on stdout, so its harness chatter is noise
    # here; a turn that failed leaves this stream as the only account of why. The
    # record in the status directory is written either way, so nothing is lost by
    # staying quiet on the way out.
    if ! cat "$agent_stderr" >&2; then
        # An unreadable replay must not change the child's fate, which is already
        # decided and recorded; say so and let the exit code below stand.
        echo "oneharness-agent: could not replay the agent stderr record at $agent_stderr; read it from the worker status directory instead" >&2
    fi
    echo "oneharness-agent: agent process $agent_pid exited $exit_code; awaiting dispatcher recovery" >&2
    # The stderr copy above is best-effort by design: failing a live agent turn
    # because a diagnostic copy could not be written would be strictly worse than
    # losing the copy. What must not happen is reporting a capture that stopped
    # working as "the harness said nothing", so re-check it here and say so.
    capture=""
    if ! : >>"$status_dir/agent.stderr"; then
        capture="; agent stderr capture became unwritable, so its tail may be incomplete"
    fi
    # Written before the marker the dispatcher polls for, so the reason is always
    # already there when the failure is observed.
    if [ "$exit_code" -gt 128 ]; then
        write_status agent.failure "agent harness killed by signal $((exit_code - 128))$capture"
    else
        write_status agent.failure "agent harness exited $exit_code$capture"
    fi
    write_status agent.failed "$worker_pid"
    # Stay alive so the dispatcher can observe this failure and recover the tree it
    # is about to tear down — but do nothing while waiting. The empty loop this
    # replaces pinned a whole core at 100% for the entire recovery window, on a host
    # whose every other dispatch was competing for the same cores.
    while :; do
        sleep 3600
    done
fi
write_status agent.done "$worker_pid"
exit "$exit_code"
