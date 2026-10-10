#!/usr/bin/env bash
# Spike-only gate profiler (spike-gate-profile). Throwaway: no tests, no lint.
#
#   measure.sh offline EDIT...                      which test targets' keys cover each edit (runs nothing)
#   measure.sh judge  --commit SHA [--edit EDIT] --label L          judge the tree once (one paid llmlint turn)
#   measure.sh tier   --commit SHA [--edit EDIT] --cache cold|warm --label L   time `just check`
#   measure.sh gate   --commit SHA [--edit EDIT] --cache cold|warm --label L   time `just gate origin main`
#   measure.sh three  --commit SHA [--patch FILE] --cache cold|warm --label L  the three big tiers at once (three.sh)
#
# --patch FILE applies a diff (the prototype) to SHA and commits it, in place of an edit.
#
# EDIT is one of the representative edits below (by name or path). A measured tree is
# SHA with that one edit committed on a detached HEAD in this checkout, so the tree is
# exactly the base plus the edit; the checkout is returned to its branch afterwards.
#
# Every run uses an isolated Nx cache under $GATE_PROFILE_HOME (default
# ~/.cache/gate-profile-spike), never the origin-keyed one real gates replay from:
# XDG_CACHE_HOME points at $GATE_PROFILE_HOME/xdg, whose entries symlink every other
# ~/.cache entry so only ai-orchestrator/nx is private. `cold` starts from an empty Nx
# cache seeded only with the judged llmlint verdicts in $GATE_PROFILE_HOME/verdicts;
# `warm` keeps whatever the previous run left. Results land in
# $GATE_PROFILE_HOME/runs/<label>/ (summary.json + raw material); copy them onto the
# branch afterwards. Nothing here pushes.
set -euo pipefail

HOME_DIR=${GATE_PROFILE_HOME:-$HOME/.cache/gate-profile-spike}
XDG=$HOME_DIR/xdg

# name -> "path:line": insert one empty line after line <line> (itself empty) of <path>.
declare -A EDITS=(
  [wide-conftest]="tests/conftest.py:120"
  [wide-justfile]="justfile:7"
  [narrow-script]="scripts/budget-dispatches.sh:17"
  [narrow-persona]="personas/researcher.yaml:9"
  [narrow-doc]="docs/budgets.md:2"
)

die() { echo "measure: $*" >&2; exit 2; }

resolve_edit() {
  local e=$1
  if [[ -n ${EDITS[$e]+x} ]]; then echo "${EDITS[$e]}"; return; fi
  for k in "${!EDITS[@]}"; do [[ ${EDITS[$k]%%:*} == "$e" ]] && { echo "${EDITS[$k]}"; return; }; done
  die "unknown edit '$e' (known: ${!EDITS[*]})"
}

setup_xdg() {
  mkdir -p "$XDG/ai-orchestrator" "$HOME_DIR/verdicts" "$HOME_DIR/runs" "$HOME_DIR/archive"
  local e n
  for e in "$HOME"/.cache/*; do
    n=${e##*/}
    [[ $n == ai-orchestrator || $n == gate-profile-spike ]] && continue
    [[ -e $XDG/$n ]] || ln -s "$e" "$XDG/$n"
  done
  for e in "$HOME"/.cache/ai-orchestrator/*; do
    n=${e##*/}
    [[ $n == nx ]] && continue
    [[ -e $XDG/ai-orchestrator/$n ]] || ln -s "$e" "$XDG/ai-orchestrator/$n"
  done
}

nx_cache_dir() {  # the directory scripts/nx.sh derives, under the isolated XDG root
  local key
  key=$(printf '%s' "$(git -C "$ROOT" config --get remote.origin.url)" | sha256sum | cut -c1-16)
  echo "$XDG/ai-orchestrator/nx/$key"
}

seed_cold() {
  local dir=$1 h
  if [[ -d $dir ]]; then mv "$dir" "$HOME_DIR/archive/nx-$(date +%s)"; fi
  mkdir -p "$dir/terminalOutputs"
  for h in "$HOME_DIR"/verdicts/*; do
    [[ -d $h ]] || continue
    cp -a "$h/entry" "$dir/${h##*/}"
    [[ -f $h/terminalOutput ]] && cp -a "$h/terminalOutput" "$dir/terminalOutputs/${h##*/}"
  done
}

keep_verdicts() {  # copy every lint-llm-diff cache entry a run produced into verdicts/
  local out=$1 dir=$2 f hash
  for f in "$out"/nx-runs/*.json; do
    [[ -f $f ]] || continue
    while read -r hash; do
      [[ -n $hash && -d $dir/$hash ]] || continue
      mkdir -p "$HOME_DIR/verdicts/$hash"
      cp -a "$dir/$hash/." "$HOME_DIR/verdicts/$hash/entry"
      [[ -f $dir/terminalOutputs/$hash ]] && cp -a "$dir/terminalOutputs/$hash" "$HOME_DIR/verdicts/$hash/terminalOutput"
      printf '%s\n' "$(jq -c --arg h "$hash" '{hash:$h, run:.run.command, task:(.tasks[]|select(.hash==$h))}' "$f")" >"$HOME_DIR/verdicts/$hash/recorded.json"
    done < <(jq -r '.tasks[] | select(.taskId=="workspace:lint-llm-diff") | .hash' "$f")
  done
}

mode=${1:-}; shift || true
ROOT=$(git rev-parse --show-toplevel)
HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

if [[ $mode == offline ]]; then
  (( $# )) || die "offline needs at least one edit"
  paths=()
  for e in "$@"; do spec=$(resolve_edit "$e" 2>/dev/null || echo "$e:0"); paths+=("${spec%%:*}"); done
  cd "$ROOT" && exec .venv/bin/python "$HERE/offline.py" --affected "${paths[@]}"
fi

[[ $mode == tier || $mode == gate || $mode == judge || $mode == three ]] || die "usage: measure.sh offline|judge|tier|gate ... (see header)"
commit="" edit="" cache=warm label="" patch=""
while (( $# )); do
  case $1 in
    --commit) commit=$2; shift 2 ;;
    --edit) edit=$2; shift 2 ;;
    --patch) patch=$(realpath "$2"); shift 2 ;;
    --cache) cache=$2; shift 2 ;;
    --label) label=$2; shift 2 ;;
    *) die "unknown argument $1" ;;
  esac
done
[[ -n $commit && -n $label ]] || die "--commit and --label are required"
[[ $cache == cold || $cache == warm ]] || die "--cache is cold or warm"
out=$HOME_DIR/runs/$label
[[ ! -e $out ]] || die "$out exists; choose another --label"

# Run from a copy: checking out the measured commit removes this directory.
if [[ -z ${GATE_PROFILE_REEXEC:-} ]]; then
  copy=$HOME_DIR/harness/$label
  rm -rf "$copy"; mkdir -p "$copy"; cp -a "$HERE/." "$copy/"
  GATE_PROFILE_REEXEC=1 exec "$copy/measure.sh" "$mode" --commit "$commit" ${edit:+--edit "$edit"} ${patch:+--patch "$patch"} --cache "$cache" --label "$label"
fi

cd "$ROOT"
[[ -z $(git status --porcelain) ]] || die "the checkout is dirty; commit or remove changes first"
branch=$(git symbolic-ref --quiet --short HEAD) || die "run from a branch, not a detached HEAD"
restore() { git checkout --quiet "$branch"; }
trap restore EXIT
setup_xdg
git checkout --quiet --detach "$commit"
base_sha=$(git rev-parse HEAD)
edit_spec=""
if [[ -n $edit ]]; then
  edit_spec=$(resolve_edit "$edit")
  p=${edit_spec%%:*}; l=${edit_spec##*:}
  [[ -z $(sed -n "${l}p" "$p") ]] || die "line $l of $p is not empty at $commit; the edit is defined against another base"
  sed -i "${l}a\\
" "$p"
  git add "$p"
  GIT_AUTHOR_DATE="2026-10-09T00:00:00Z" GIT_COMMITTER_DATE="2026-10-09T00:00:00Z" \
    git commit --quiet -m "fix: gate-profile spike edit to $p" -m "Throwaway measurement tree; never published."
fi
if [[ -n $patch ]]; then
  git apply "$patch"
  git add -A
  GIT_AUTHOR_DATE="2026-10-09T00:00:00Z" GIT_COMMITTER_DATE="2026-10-09T00:00:00Z" \
    git commit --quiet -m "fix: gate-profile spike prototype ${patch##*/}" -m "Throwaway measurement tree; never published."
fi
tree=$(git rev-parse 'HEAD^{tree}')
nxdir=$(nx_cache_dir)
cache_entries_before=$(find "$nxdir" -mindepth 1 -maxdepth 1 -type d ! -name terminalOutputs 2>/dev/null | wc -l)
if [[ $cache == cold ]]; then seed_cold "$nxdir"; fi
mkdir -p "$nxdir"
cache_entries_start=$(find "$nxdir" -mindepth 1 -maxdepth 1 -type d ! -name terminalOutputs | wc -l)

case $mode in
  judge) cmd=(just lint-llm-diff origin/main) ;;
  tier) cmd=(just check) ;;
  gate) cmd=(just gate origin main) ;;
  three) cmd=("$HERE/three.sh") ;;
esac
mkdir -p "$out"
jq -n --arg mode "$mode" --arg label "$label" --arg base "$base_sha" --arg head "$(git rev-parse HEAD)" \
  --arg tree "$tree" --arg edit "${edit:-none}" --arg spec "$edit_spec${patch:+ patch:${patch##*/}}" --arg cache "$cache" \
  --arg nxdir "$nxdir" --argjson before "$cache_entries_before" --argjson start "$cache_entries_start" \
  --arg uv "$(uv --version)" --arg nx "$(node_modules/.bin/nx --version 2>/dev/null | sed -n 's/.*Local: //p')" \
  --arg llmlint "$(llmlint --version 2>/dev/null)" --arg fp "$(./scripts/llmlint-fingerprint.sh 2>/dev/null | sha256sum | cut -c1-16)" \
  '{mode:$mode,label:$label,base_commit:$base,measured_head:$head,tree:$tree,edit:$edit,edit_spec:$spec,
    cache_state:$cache,nx_cache_dir:$nxdir,nx_cache_entries_before_seed:$before,nx_cache_entries_at_start:$start,
    uv:$uv,nx:$nx,llmlint:$llmlint,llmlint_fingerprint_sha256_16:$fp}' >"$out/meta-in.json"

# Nested journeys that replace PYTHONPATH but inherit PYTEST_ADDOPTS (tests/test_coverage_gate.py,
# tests/test_nx_cache_scope.py's collection probes) must still import the plugin, or they fail
# on the harness rather than on the tree: so it is also placed, for the run only, in the root
# environment's site-packages (gitignored, additive, removed on exit). The first wide run
# predates this and carries seven such artefact failures.
site=$(ls -d "$ROOT"/.venv/lib/python3*/site-packages)
cp "$HERE/plugin/gate_profile_plugin.py" "$site/gate_profile_plugin.py"
restore() { rm -f "$site/gate_profile_plugin.py"; git checkout --quiet "$branch"; }
status=0
XDG_CACHE_HOME=$XDG \
GATE_PROFILE_OUT=$out/pytest GATE_PROFILE_ROOT=$ROOT \
PYTHONPATH=$HERE/plugin${PYTHONPATH:+:$PYTHONPATH} \
PYTEST_ADDOPTS="-p gate_profile_plugin${PYTEST_ADDOPTS:+ $PYTEST_ADDOPTS}" \
  python3 "$HERE/collect.py" run --out "$out" --root "$ROOT" --nx-cache "$nxdir" --meta "$out/meta-in.json" -- "${cmd[@]}" || status=$?
keep_verdicts "$out" "$nxdir"
jq -r '"\(.label): \(.mode) exit=\(.exit_status) wall=\(.wall_clock_s)s load1_max=\(.host.load1_max) dispatches_max=\(.host.dispatches_max) lint=\(.lint_llm_diff_provenance)"' "$out/summary.json"
exit 0
