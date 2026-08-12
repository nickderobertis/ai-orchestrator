#!/usr/bin/env bash
# Bring this host's `onevcs` registry up to the tracked repository configuration.
#
# Two tracked files describe it: `config/onevcs.checkouts` lists every checkout to
# register, and `config/onevcs.rules.yml` is the rules file that decides how each
# resulting identity publishes and what verifies it. This script installs the
# second and registers the first, then proves that every registered checkout
# resolves to a rule rather than falling through to the reviewed default.
#
# It is the reproducible form of the migration off the pre-adoption
# `~/.ai-orchestrator/repos.json` registry, and it is re-runnable: registration is
# keyed by alias and the rules file is a whole-file replacement, so a second run
# leaves the same registry a first one did. Run it after editing either tracked
# file, and on a new host after cloning the checkouts.
#
# Registration goes through `onevcs register`, never through the registry document
# itself: what that command writes is what `onevcs` considers valid, and a document
# edited around it is a shape nothing verified.
#
# llmlint: ignore-file[tool_output_is_signal] The per-checkout table this prints is
# the product, not progress chatter: an operator runs this to see which policy each
# repository landed on, and a merge path silently one notch wider than it was is the
# whole failure this exists to prevent. A summary line alone would say a registry
# was written without saying what it says.
# llmlint: ignore-file[changed_behavior_has_e2e] Real recipe journeys cover every
# operator-controlled branch. Filesystem fault branches and malformed output from
# the pinned onevcs CLI are defensive diagnostics whose fault injection would mock
# the layer this integration suite is required to exercise for real.
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
repo_root="$(dirname -- "$script_dir")"

# The same `uv run` every `onevcs` recipe reaches the CLI through, pinned to this
# repository so the version registering a checkout is the version the recipes use
# regardless of where this was invoked from.
repo_recipe() {
    just --justfile "$repo_root/justfile" --working-directory "$repo_root" "$@"
}

valid_alias() {
    [[ $1 =~ ^[A-Za-z0-9._-]+$ ]]
}

checkouts_file="$repo_root/config/onevcs.checkouts"
rules_file="$repo_root/config/onevcs.rules.yml"
dry_run=false

usage() {
    cat >&2 <<'USAGE'
usage: apply-repo-registry.sh [--dry-run] [--checkouts FILE] [--rules FILE]

  --dry-run        Report what would change and change nothing.
  --checkouts FILE Read the checkout list from FILE (default config/onevcs.checkouts).
  --rules FILE     Install FILE as the rules file (default config/onevcs.rules.yml).

The registry it writes is `$ONEVCS_HOME` (`~/.onevcs` when that is unset).
USAGE
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run) dry_run=true; shift ;;
        --checkouts) [[ $# -ge 2 ]] || { usage; exit 2; }; checkouts_file=$2; shift 2 ;;
        --rules) [[ $# -ge 2 ]] || { usage; exit 2; }; rules_file=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "apply-repo-registry: unknown argument '$1'" >&2; usage; exit 2 ;;
    esac
done

for file in "$checkouts_file" "$rules_file"; do
    if [[ ! -f $file ]]; then
        echo "apply-repo-registry: $file does not exist" >&2
        exit 2
    fi
done

# `onevcs` refuses an empty `ONEVCS_HOME` rather than falling back, so this
# resolves the same way its own `home::root` does instead of guessing.
if [[ -n ${ONEVCS_HOME+x} ]]; then
    if [[ -z $ONEVCS_HOME ]]; then
        echo "apply-repo-registry: ONEVCS_HOME is set but empty; unset it or give it a directory" >&2
        exit 2
    fi
    if [[ $ONEVCS_HOME != /* || $ONEVCS_HOME == / ]]; then
        echo "apply-repo-registry: ONEVCS_HOME must name an absolute directory other than /" >&2
        exit 2
    fi
    onevcs_home=$ONEVCS_HOME
else
    if [[ -z ${HOME:-} || $HOME != /* || ! -d $HOME ]]; then
        echo "apply-repo-registry: HOME must name an existing absolute directory" >&2
        exit 2
    fi
    onevcs_home="$HOME/.onevcs"
fi

if [[ -z ${HOME:-} || $HOME != /* || ! -d $HOME ]]; then
    echo "apply-repo-registry: HOME must name an existing absolute directory" >&2
    exit 2
fi

registry_document="$onevcs_home/registry.json"
installed_rules="$onevcs_home/rules.yml"

# A registry that names its own rules file elsewhere would ignore the one installed
# below, so the policy an operator reads here and the policy a publication resolves
# would be different documents. There is no command that repoints it, so this stops
# rather than writing a file nothing reads.
if [[ -f $registry_document ]]; then
    if ! referenced=$(python3 -c '
import json, sys
document = json.load(open(sys.argv[1], encoding="utf-8"))
rules = document.get("rules")
if rules is not None and not isinstance(rules, str):
    raise ValueError("registry rules must be a path string")
print(rules or "")
' "$registry_document"); then
        echo "apply-repo-registry: cannot read a valid registry from $registry_document" >&2
        exit 1
    fi
    if [[ -n $referenced && $referenced != "$installed_rules" ]]; then
        echo "apply-repo-registry: $registry_document names its rules file as $referenced," >&2
        echo "  so the copy this script installs at $installed_rules would be ignored." >&2
        echo "  Point that reference at $installed_rules, or install the rules there instead." >&2
        exit 1
    fi
fi

echo "apply-repo-registry: registry $onevcs_home"

rules_unchanged=false
if [[ -f $installed_rules ]]; then
    cmp_status=0
    cmp -s -- "$rules_file" "$installed_rules" || cmp_status=$?
    case $cmp_status in
        0) rules_unchanged=true ;;
        1) ;;
        *) echo "apply-repo-registry: comparing $rules_file with $installed_rules failed" >&2; exit 1 ;;
    esac
fi

present=0
skipped=0
aliases=()
first_path=""

while IFS= read -r line || [[ -n $line ]]; do
    # `read` without `IFS=` is the trim: it drops the leading and trailing
    # whitespace a commented, indented list is written with.
    read -r trimmed <<<"${line%%#*}"
    [[ -n $trimmed ]] || continue
    if [[ $trimmed == \~* && $trimmed != \~ && $trimmed != \~/* ]]; then
        echo "apply-repo-registry: unsupported tilde path '$trimmed'; use ~/path" >&2
        exit 2
    fi
    path=${trimmed/#\~/$HOME}
    if [[ ! -d $path ]]; then
        echo "  skip       not on this host  $path"
        skipped=$((skipped + 1))
        continue
    fi
    [[ -n $first_path ]] || first_path=$path
    if [[ $dry_run == true ]]; then
        echo "  would register  $path"
        present=$((present + 1))
        continue
    fi
    if ! output=$(repo_recipe register-repo "$path" 2>&1); then
        echo "$output" >&2
        echo "apply-repo-registry: registering $path failed" >&2
        exit 1
    fi
    alias_name=$(awk '/^ *alias: /{print $2; exit}' <<<"$output")
    if ! valid_alias "$alias_name"; then
        echo "$output" >&2
        echo "apply-repo-registry: onevcs register named no alias for $path" >&2
        exit 1
    fi
    aliases+=("$alias_name")
    present=$((present + 1))
done <"$checkouts_file"

# Validate the supplied rules through `onevcs` before replacing the live file.
# A scratch registration gives `rules check` a real identity to resolve while
# keeping malformed configuration away from the registry this run is repairing.
validation_home=$(mktemp -d) || {
    echo "apply-repo-registry: could not create temporary rules validation storage" >&2
    exit 1
}
cleanup_validation() {
    if [[ -d $validation_home ]] && ! rm -rf -- "$validation_home"; then
        echo "apply-repo-registry: could not remove temporary rules validation storage" >&2
    fi
}
trap cleanup_validation EXIT
validation_path=$first_path
if [[ -z $validation_path ]]; then
    validation_path="$validation_home/checkout"
    if ! mkdir "$validation_path" || \
        ! git -C "$validation_path" init -q -b main || \
        ! git -C "$validation_path" remote add origin https://github.com/validation/registry-rules.git; then
        echo "apply-repo-registry: could not prepare a checkout for rules validation" >&2
        exit 1
    fi
fi
if ! validation_registration=$(ONEVCS_HOME=$validation_home repo_recipe register-repo "$validation_path" 2>&1); then
    echo "$validation_registration" >&2
    echo "apply-repo-registry: could not prepare rules validation with $validation_path" >&2
    exit 1
fi
validation_alias=$(awk '/^ *alias: /{print $2; exit}' <<<"$validation_registration")
if ! valid_alias "$validation_alias"; then
    echo "$validation_registration" >&2
    echo "apply-repo-registry: rules validation registration named no alias" >&2
    exit 1
fi
if ! cp -- "$rules_file" "$validation_home/rules.yml"; then
    echo "apply-repo-registry: could not stage $rules_file for validation" >&2
    exit 1
fi
if ! validation=$(ONEVCS_HOME=$validation_home repo_recipe repo-policy "$validation_alias" 2>&1); then
    echo "$validation" >&2
    echo "apply-repo-registry: $rules_file is not a valid onevcs rules file" >&2
    exit 1
fi
if ! rm -rf -- "$validation_home"; then
    echo "apply-repo-registry: could not remove temporary rules validation storage" >&2
    exit 1
fi
trap - EXIT

if [[ $dry_run == true ]]; then
    if [[ $rules_unchanged == true ]]; then
        echo "  rules      unchanged  $installed_rules"
    else
        echo "  rules      would install  $installed_rules"
    fi
    echo "apply-repo-registry: dry run — ${present} checkout(s) would be registered, ${skipped} skipped"
    exit 0
fi

if [[ $rules_unchanged == true ]]; then
    echo "  rules      unchanged  $installed_rules"
else
    if ! mkdir -p "$onevcs_home"; then
        echo "apply-repo-registry: could not create $onevcs_home" >&2
        exit 1
    fi
    staged_rules="$onevcs_home/.rules.yml.$$"
    if ! cp -- "$rules_file" "$staged_rules" || ! mv -f -- "$staged_rules" "$installed_rules"; then
        if [[ -e $staged_rules ]] && ! rm -f -- "$staged_rules"; then
            echo "apply-repo-registry: could not remove incomplete $staged_rules" >&2
        fi
        echo "apply-repo-registry: could not atomically install $installed_rules" >&2
        exit 1
    fi
    echo "  rules      installed  $installed_rules"
fi

# One reported field of `onevcs rules check`, without the `(from rule 1)` provenance
# it annotates each one with — that belongs to the explanation the command gives,
# not to the table this prints.
field() {
    awk -v key="$1:" '$1 == key { $1 = ""; sub(/^ /, ""); sub(/ \(from [^)]*\)$/, ""); print; exit }'
}

# The verification, and the reason the default policy is not a fallback here: a
# checkout that matched no rule publishes under `change-open`/`required`, which is
# safe but is nobody's configured policy. Reporting it as a pass would hide a rule
# that was never written or whose owner or name was mistyped.
unmatched=()
echo "apply-repo-registry: resolved policy"
for alias_name in ${aliases[@]+"${aliases[@]}"}; do
    if ! resolved=$(repo_recipe repo-policy "$alias_name" 2>&1); then
        echo "$resolved" >&2
        echo "apply-repo-registry: resolving the policy for $alias_name failed" >&2
        exit 1
    fi
    if [[ $(field matched <<<"$resolved") != rule\ * ]]; then
        unmatched+=("$alias_name")
    fi
    identity=$(field identity <<<"$resolved")
    publication=$(field publication <<<"$resolved")
    approvals=$(field approvals <<<"$resolved")
    gate=$(field gate <<<"$resolved")
    if [[ -z $identity || ! $publication =~ ^(local-direct|change-open|change-auto|change-direct)$ || \
        ! $approvals =~ ^(none|required)$ || -z $gate ]]; then
        echo "$resolved" >&2
        echo "apply-repo-registry: policy output for $alias_name is incomplete or invalid" >&2
        exit 1
    fi
    printf '  %-28s %-44s %-13s %-9s %s\n' \
        "$alias_name" "$identity" "$publication" "$approvals" "$gate"
done

if [[ ${#unmatched[@]} -gt 0 ]]; then
    echo "apply-repo-registry: no rule in $rules_file matches ${unmatched[*]}," >&2
    echo "  so each would publish under the reviewed default instead of this host's policy." >&2
    echo "  Add a rule naming its host, owner, and name, then re-run." >&2
    exit 1
fi

echo "apply-repo-registry: ${present} checkout(s) registered, ${skipped} skipped, every one matched a rule"
