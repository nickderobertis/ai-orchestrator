#!/usr/bin/env bash
# Bring this host's `onevcs` registry up to the tracked repository configuration.
#
# Three tracked files describe it: `config/onevcs.checkouts` lists every checkout to
# register, `config/onevcs.rules.yml` is the rules file that decides how each
# resulting identity publishes and what verifies it, and `config/onevcs.releases.yml`
# is the release override that decides which rung a node of each repository adopts a
# dependency's release on and which of a producer's targets a consumer naming none
# waits for. This script installs the second and third and registers the first, then
# proves that every registered checkout resolves to a rule rather than falling
# through to the reviewed default, and reports what each producer this host installs
# resolves out of the override.
#
# It is the reproducible form of the migration off the pre-adoption
# `~/.ai-orchestrator/repos.json` registry, and it is re-runnable: registration is
# keyed by alias and each installed file is a whole-file replacement, so a second run
# leaves the same registry a first one did. Run it after editing any tracked file,
# and on a new host after cloning the checkouts.
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
releases_file="$repo_root/config/onevcs.releases.yml"
dry_run=false

usage() {
    cat >&2 <<'USAGE'
usage: apply-repo-registry.sh [--dry-run] [--checkouts FILE] [--rules FILE] [--releases FILE]

  --dry-run        Report what would change and change nothing.
  --checkouts FILE Read the checkout list from FILE (default config/onevcs.checkouts).
  --rules FILE     Install FILE as the rules file (default config/onevcs.rules.yml).
  --releases FILE  Install FILE as the release override (default config/onevcs.releases.yml).

The registry it writes is `$ONEVCS_HOME` (`~/.onevcs` when that is unset).
USAGE
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run) dry_run=true; shift ;;
        --checkouts) [[ $# -ge 2 ]] || { usage; exit 2; }; checkouts_file=$2; shift 2 ;;
        --rules) [[ $# -ge 2 ]] || { usage; exit 2; }; rules_file=$2; shift 2 ;;
        --releases) [[ $# -ge 2 ]] || { usage; exit 2; }; releases_file=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "apply-repo-registry: unknown argument '$1'" >&2; usage; exit 2 ;;
    esac
done

for file in "$checkouts_file" "$rules_file" "$releases_file"; do
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
installed_releases="$onevcs_home/releases.yml"

# The same `uv run` the `onevcs` recipes reach the CLI through, for the one verb no
# recipe wraps: `release targets` is a read, and what it answers here is the product.
repo_onevcs() {
    uv run --project "$repo_root" onevcs "$@"
}

# Whether the file already installed at $2 is byte-identical to the candidate at $1:
# `true`, `false`, or a failed comparison, which ends the run rather than guessing.
unchanged_install() {
    [[ -f $2 ]] || { echo false; return 0; }
    cmp_status=0
    cmp -s -- "$1" "$2" || cmp_status=$?
    case $cmp_status in
        0) echo true ;;
        1) echo false ;;
        *) echo "apply-repo-registry: comparing $1 with $2 failed" >&2; exit 1 ;;
    esac
}

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

rules_unchanged=$(unchanged_install "$rules_file" "$installed_rules")
releases_unchanged=$(unchanged_install "$releases_file" "$installed_releases")

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
validation_register() {
    if ! registration=$(ONEVCS_HOME=$validation_home repo_recipe register-repo "$1" 2>&1); then
        echo "$registration" >&2
        echo "apply-repo-registry: could not prepare rules validation with $1" >&2
        exit 1
    fi
    registered_alias=$(awk '/^ *alias: /{print $2; exit}' <<<"$registration")
    if ! valid_alias "$registered_alias"; then
        echo "$registration" >&2
        echo "apply-repo-registry: rules validation registration named no alias" >&2
        exit 1
    fi
    echo "$registered_alias"
}
# A checkout of a repository no rule and no override names, for the validation that
# has to be about the file alone. The override is validated against it rather than
# against a real checkout, because `release targets` also refuses a `default_target`
# the producer's declaration cannot be read to carry — a fact about that checkout's
# momentary state, which the table below reports per producer, not about the file.
# Its directory name is the alias `onevcs` derives, so it is one no listed checkout
# shares: a second registration under one alias replaces the first.
synthetic_path="$validation_home/apply-repo-registry-validation"
if ! mkdir "$synthetic_path" || \
    ! git -C "$synthetic_path" init -q -b main || \
    ! git -C "$synthetic_path" remote add origin https://github.com/validation/registry-rules.git; then
    echo "apply-repo-registry: could not prepare a checkout for rules validation" >&2
    exit 1
fi
synthetic_alias=$(validation_register "$synthetic_path")
validation_alias=$synthetic_alias
if [[ -n $first_path ]]; then
    validation_alias=$(validation_register "$first_path")
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
# The override is validated in the same scratch home, through the one verb that loads
# it: `release targets` refuses a malformed document by name, and a rule matching
# nothing — every rule, against the synthetic identity — is fine.
if ! cp -- "$releases_file" "$validation_home/releases.yml"; then
    echo "apply-repo-registry: could not stage $releases_file for validation" >&2
    exit 1
fi
# llmlint: ignore[boundary_inputs_validated] A producer rule's `default_target` cannot be validated against the file alone: `onevcs` refuses it only against a registered checkout whose base declares the targets, which is that checkout's momentary state on a host several managers share, not a property of the candidate. The per-producer readback after the install is where that answer is read, for every installed producer at once.
if ! validation=$(ONEVCS_HOME=$validation_home repo_onevcs release targets "$synthetic_alias" 2>&1); then
    echo "$validation" >&2
    echo "apply-repo-registry: $releases_file is not a valid onevcs release override" >&2
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
    if [[ $releases_unchanged == true ]]; then
        echo "  releases   unchanged  $installed_releases"
    else
        echo "  releases   would install  $installed_releases"
    fi
    echo "apply-repo-registry: dry run — ${present} checkout(s) would be registered, ${skipped} skipped"
    exit 0
fi

# Replace the installed copy at $2 with the candidate at $1 in one rename, so a copy
# that fails part-way leaves the previous install intact rather than truncated.
install_whole() {
    if ! mkdir -p "$onevcs_home"; then
        echo "apply-repo-registry: could not create $onevcs_home" >&2
        exit 1
    fi
    staged="$onevcs_home/.$(basename -- "$2").$$"
    if ! cp -- "$1" "$staged" || ! mv -f -- "$staged" "$2"; then
        if [[ -e $staged ]] && ! rm -f -- "$staged"; then
            echo "apply-repo-registry: could not remove incomplete $staged" >&2
        fi
        echo "apply-repo-registry: could not atomically install $2" >&2
        exit 1
    fi
}

if [[ $rules_unchanged == true ]]; then
    echo "  rules      unchanged  $installed_rules"
else
    install_whole "$rules_file" "$installed_rules"
    echo "  rules      installed  $installed_rules"
fi
if [[ $releases_unchanged == true ]]; then
    echo "  releases   unchanged  $installed_releases"
else
    install_whole "$releases_file" "$installed_releases"
    echo "  releases   installed  $installed_releases"
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
    # A resolved policy is `{publication, approvals}` and nothing else: onevcs 0.11.0
    # removed the gate, so a third field read here would be one no rules file can set
    # and every checkout would report as invalid.
    if [[ -z $identity || ! $publication =~ ^(local-direct|change-open|change-auto|change-direct)$ || \
        ! $approvals =~ ^(none|required)$ ]]; then
        echo "$resolved" >&2
        echo "apply-repo-registry: policy output for $alias_name is incomplete or invalid" >&2
        exit 1
    fi
    printf '  %-28s %-44s %-13s %s\n' \
        "$alias_name" "$identity" "$publication" "$approvals"
done

if [[ ${#unmatched[@]} -gt 0 ]]; then
    echo "apply-repo-registry: no rule in $rules_file matches ${unmatched[*]}," >&2
    echo "  so each would publish under the reviewed default instead of this host's policy." >&2
    echo "  Add a rule naming its host, owner, and name, then re-run." >&2
    exit 1
fi

# What a consumer naming no `consumes` will get from each producer this host installs,
# read back out of the override just installed through the verb a dispatch resolves it
# with. `orchestrator/host_installs.py` is the one source of which producers those are.
# A producer this registry does not hold, or whose checkout cannot be read at its base
# right now, is reported rather than failed: neither is a fault in the override, and
# an operator reading this table is told which line to act on.
if ! producers=$(uv run --project "$repo_root" python -c '
from orchestrator.host_installs import INSTALLED
for row in INSTALLED:
    print(row.producer)
'); then
    echo "apply-repo-registry: could not read which producers this host installs" >&2
    exit 1
fi
# One reported field of `onevcs release targets`, whose keys can be two words —
# `default target: pypi` — so `field` above, which reads the first word, cannot.
release_field() {
    sed -n "s/^$1: //p" <<<"$2" | head -n 1
}
echo "apply-repo-registry: release adoption"
while IFS= read -r producer; do
    [[ -n $producer ]] || continue
    if resolved=$(repo_onevcs release targets "$producer" 2>&1); then
        default_target=$(release_field "default target" "$resolved")
        adoption=$(release_field adoption "$resolved")
        # A resolved answer is one rung of the two `onevcs` knows and one target name,
        # `none` where the override names no default: a read missing either is not an
        # answer to print as one.
        if [[ ! $adoption =~ ^(fast|published)$ || ! $default_target =~ ^[A-Za-z0-9._-]+$ ]]; then
            echo "$resolved" >&2
            echo "apply-repo-registry: release targets output for $producer is incomplete or invalid" >&2
            exit 1
        fi
        printf '  %-44s default target %-12s adoption %s\n' \
            "$producer" "$default_target" "$adoption"
    elif [[ $resolved == *"is not a registered repository"* ]]; then
        printf '  %-44s not registered here\n' "$producer"
    else
        reason=${resolved#onevcs: }
        printf '  %-44s unresolved: %s\n' "$producer" "${reason//$'\n'/ }"
    fi
done <<<"$producers"

echo "apply-repo-registry: ${present} checkout(s) registered, ${skipped} skipped, every one matched a rule"
