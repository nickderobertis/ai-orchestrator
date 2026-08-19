#!/usr/bin/env bash
# Draft one branch's change request body out of band:
# `scripts/draft-pr-body.sh <branch> --repo <checkout> [--base <ref>] [--out <path>]`.
#
# The drafter reached from anywhere, where `onepipeline`'s publication closeout is the
# drafter reached from a run. What binds the two is that this composes the same task,
# runs the same graph in a tree of the same branch, and reads the body back out of the
# same field: `graphs/pr-author.yaml`, `oneharness.pr-author.toml`, and
# `config/pr-author-body.schema.json` stay the one drafting surface, and a second
# drafter beside them would be a second contract nothing reconciles. Which commands
# reach this and why out-of-band landing needed it are in `docs/repo-lifecycle.md`.
#
# Three things about it are load-bearing.
#
#   * **The turn runs in a temporary detached worktree of the branch**, cut from the
#     named checkout under `mktemp -d` and removed on every exit path. The run path
#     gives its drafter the node's own worktree; a drafter given anything else reads
#     the wrong tree's files and the wrong `.github/pull_request_template.md`. The
#     publication checkout itself is never worked in — that is this repository's
#     standing rule — so it is left byte-identical, its worktree list included.
#   * **The composed task is `onepipeline`'s**, with the one thing out-of-band
#     landing does not have replaced. The run path appends the node's rendered task,
#     which is an operator's own statement of what the work is for. There is no node
#     here, so what is appended instead is what the branch says about itself: its
#     name, its base, and the full commit messages of `<base>..<branch>`. Those are a
#     record of what was done and not a statement of why it mattered, and the
#     composed text says so — because `graphs/pr-author.yaml`'s member is told to
#     source `## Why` from the task's `## Why`, and there is none here. A thin `##
#     Why` is the correct outcome; a fabricated one is the failure.
#   * **Only a body the schema accepted is ever printed.** `oneharness` validates
#     every drafting turn against `config/pr-author-body.schema.json` and re-prompts a
#     refused one, and the report records per attempt whether it conformed. Publishing
#     the plausible-looking prose of an answer the schema rejected is exactly the
#     defect that flag exists to prevent, so a refused answer is never read.
#
# It does not try to recover the branch's original node task. For a branch a run left
# behind that would be the better substitute — but `onepipeline` publishes no read
# that maps a session or a branch to its node's task, and reconstructing one from the
# runs-root journal would encode a private layout into this repository.
#
# Exits: 0 with the body (on stdout, or in `--out`'s file with stdout silent);
# 3 when no body was drafted, naming which of `onepipeline`'s three endings it was;
# 2 for usage, for a base that could not be resolved, or for an environment the
# drafting turn could not be started in. Nothing but a drafted body ever reaches
# stdout. There is no retry of its own — `schema_max_retries` in
# `oneharness.pr-author.toml` is the only retry there is.
#
# llmlint: ignore-file[tool_output_is_signal] The body on stdout and the ending on
# stderr are this command's whole product: an operator runs it to obtain a change
# request body, and the one line naming which of the three endings a bodyless run hit
# is what decides whether the fix is the graph, the schema, or the persona's prose.
# The landing recipes carry the same scoped ignore for the same reason.
set -euo pipefail

#: The drafting graph, relative to the repository root this runs from. Every ref
#: inside it resolves against its own directory, so it must be named as a path here
#: and the working directory must be the root.
PR_AUTHOR_GRAPH="graphs/pr-author.yaml"

#: `onepipeline`'s own opening sentence, verbatim. It is what tells the drafter what
#: kind of answer this is, and `graphs/pr-author.yaml`'s member interpolates the whole
#: composed task back into its own prose — so this text, and nothing else, is what
#: names the branch to the model.
ONEPIPELINE_OPENING="Read this branch's diff and write the change request's body, following the repository's own template. The task this branch delivered:"

# Reads the drafted body out of one graph run's events, in `onepipeline`'s own terms.
#
# The envelope and report protocol below is `oneagentgraph`'s, restated here because
# nothing publishes it as a library this shell can call. What keeps the restatement
# honest is that it is never exercised against a fixture: every journey in
# `tests/e2e/test_draft_pr_body_e2e.py` runs the real `oneagentgraph` against the real
# graph, so a release that renames `member-settled`, moves `report_path`, or restructures
# `results[]` fails those journeys rather than silently drafting nothing.
#
# The read is its read reproduced: each `member-settled` envelope names a retained
# member report, and the body is the first `results[]` entry that both conformed to
# the schema and has something in it. The three endings are its vocabulary too, kept
# apart because they take three different fixes — the graph or its quota, the schema
# or the prompt that answers it, the persona's prose.
#
# Which ending an exhausted schema retry budget lands on is measured rather than
# assumed, and it is not the one the name suggests: on the adopted `oneagentgraph` a
# member whose every answer was refused **dies** and retains no report at all, so the
# only honest reading left is `dispatch-failed` — the dispatch ran without succeeding.
# `schema-refused` is what a report whose entries all failed validation would be, which
# is the shape a chain that fell through to a second candidate can still produce. Do
# not collapse the two on the strength of the word: they point an operator at different
# files.
READ_PROGRAM='
import json, pathlib, sys

events, out, status = sys.argv[1], sys.argv[2], sys.argv[3]

# The three ending names, each on a line of its own, because the vocabulary belongs to
# onepipeline rather than to this script: tests/drafting_task_contract.py reads them
# back from here and holds them against the pinned engine. One name per line, quoted,
# is the whole of that reader contract.
DISPATCH_FAILED_ENDING = "dispatch-failed"
SCHEMA_REFUSED_ENDING = "schema-refused"
NO_BODY_ENDING = "no-body"

DISPATCH_FAILED = "the drafting dispatch could not start or did not succeed"
SCHEMA_REFUSED = (
    "the drafting dispatch answered nothing the schema it was validated against accepted"
)
NO_BODY = "the drafting dispatch succeeded and there was no body in what it answered with"


def give_up(ending: str, detail: str) -> None:
    sys.stderr.write(f"draft-pr-body: no body was drafted ({ending}): {detail}\n")
    raise SystemExit(3)


def mapping(value: object) -> dict:
    """Whatever value that is, as something get can be asked.

    oneagentgraph owns these documents and this reads a handful of their fields, so a
    shape it did not expect is read as an absent field and classified as an ending —
    never raised as a traceback where a diagnostic belongs.
    """
    return value if isinstance(value, dict) else {}


results = []
try:
    recorded = pathlib.Path(events).read_text(encoding="utf-8")
except OSError as unreadable:
    # llmlint: ignore[changed_behavior_has_e2e] The file is one this script created under its own scratch directory moments earlier; reaching this means the filesystem the suite itself runs on stopped holding it mid-run.
    give_up(DISPATCH_FAILED_ENDING, f"{DISPATCH_FAILED} (its events could not be read: {unreadable})")
for line in recorded.splitlines():
    if not line.startswith("{"):
        continue
    try:
        envelope = json.loads(line)
    except json.JSONDecodeError:
        continue
    if mapping(envelope).get("kind") != "member-settled":
        continue
    reported = mapping(mapping(envelope).get("payload")).get("report_path")
    if not isinstance(reported, str) or not reported:
        continue
    # llmlint: ignore[boundary_inputs_validated] What oneagentgraph itself reports about a graph this script just launched, not third-party input. The only thing done with it is a read, and every way that read can fail falls through to the ending below; what it yields is then validated by shape rather than trusted. Checking the path against an expected directory would assert a pinned tool layout this script does not own, which is more to break and no safer.
    try:
        report = json.loads(pathlib.Path(reported).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    reported_results = mapping(report).get("results")
    if isinstance(reported_results, list):
        results.extend(reported_results)

# No recorded answer at all is the graph never having run a turn, whatever it exited
# with; a recorded one is classified by what it says, because a turn that answered is
# a dispatch that ran.
if not results:
    give_up(DISPATCH_FAILED_ENDING, f"{DISPATCH_FAILED} (the drafting graph exited {status})")

conformed = [result for result in results if mapping(result).get("schema_valid") is True]
if not conformed:
    # llmlint: ignore[changed_behavior_has_e2e] Not producible on the adopted oneagentgraph: a member whose every answer the schema refused dies and retains no report at all, so a scripted all-refused run reaches the dispatch-failed ending above instead — which tests/e2e/test_draft_pr_body_e2e.py drives. Reaching this line needs a retained report whose entries all failed validation, which only a chain that settled on a different candidate can produce, and every candidate but the scripted one is a paid provider this suite refuses to spend.
    give_up(SCHEMA_REFUSED_ENDING, SCHEMA_REFUSED)

for result in conformed:
    body = mapping(mapping(result).get("structured")).get("body")
    if isinstance(body, str) and body.strip():
        if out:
            try:
                pathlib.Path(out).write_text(body, encoding="utf-8")
            except OSError as unwritable:
                # llmlint: ignore[changed_behavior_has_e2e] The destination is checked for an existing writable directory before the turn is spent, so reaching this means it stopped being one while the turn ran; driving that would mean racing the filesystem the suite runs on.
                sys.stderr.write(f"draft-pr-body: the drafted body could not be written to {out}: {unwritable}; name a path in a writable directory, or omit --out to print it\n")
                raise SystemExit(2) from None
        else:
            sys.stdout.write(body)
        raise SystemExit(0)

give_up(NO_BODY_ENDING, NO_BODY)
'

fail() {
    echo "draft-pr-body: $1; $2" >&2
    exit 2
}

# Every value here becomes an argument to `git` or a path, and a value that starts
# with a dash is read by one of them as a flag rather than as the thing it names.
# Refused where it was typed, so it can be named in the diagnostic: past this point
# it would be somebody else's unrecognized-option error about a command the caller
# never ran.
refuse_option_shaped() {
    case "$2" in
        -*)
            fail "$1 was given '$2', which git would read as an option rather than as a ref" \
                "name a ref that does not begin with a dash"
            ;;
    esac
}

usage() {
    echo "usage: scripts/draft-pr-body.sh <branch> --repo <checkout> [--base <ref>] [--out <path>]" >&2
}

# llmlint: ignore[changed_behavior_has_e2e] Reachable only when this script's own directory stops being enterable between its launch and its first line; no journey can produce that without racing the filesystem the test itself runs on.
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || fail "this recipe could not resolve the checkout it was run from" \
    "run it from a checkout, so the graph and configs it names are that checkout's"
repo_root=$(dirname -- "$script_dir")

python="$repo_root/.venv/bin/python3"
[ -x "$python" ] || python=python3

branch="${1:-}"
if [ -z "$branch" ]; then
    usage
    fail "no branch was named" "name the branch whose body is to be drafted"
fi
case "$branch" in
    -*)
        usage
        fail "the first argument must be the branch, got the flag '$branch'" \
            "name the branch first, then any flags after it"
        ;;
esac
shift

checkout=""
base=""
out=""
while [ $# -gt 0 ]; do
    case "$1" in
        --repo)
            [ $# -ge 2 ] || fail "--repo was given no value" "name the checkout the branch is read from"
            checkout="$2"
            shift 2
            ;;
        --repo=*)
            checkout="${1#--repo=}"
            [ -n "$checkout" ] || fail "--repo was given no value" "name the checkout the branch is read from"
            shift
            ;;
        --base)
            [ $# -ge 2 ] || fail "--base was given no value" "name the ref the branch is diffed against, or omit --base to resolve it from onevcs"
            refuse_option_shaped --base "$2"
            base="$2"
            shift 2
            ;;
        --base=*)
            base="${1#--base=}"
            [ -n "$base" ] || fail "--base was given no value" "name the ref the branch is diffed against, or omit --base to resolve it from onevcs"
            refuse_option_shaped --base "$base"
            shift
            ;;
        --out)
            [ $# -ge 2 ] || fail "--out was given no value" "name the file the body is written to, or omit --out to print it"
            out="$2"
            shift 2
            ;;
        --out=*)
            out="${1#--out=}"
            [ -n "$out" ] || fail "--out was given no value" "name the file the body is written to, or omit --out to print it"
            shift
            ;;
        *)
            usage
            fail "'$1' is not an argument this drafter takes" \
                "name the branch once, and pass only --repo, --base, and --out after it"
            ;;
    esac
done

[ -n "$checkout" ] || {
    usage
    fail "no checkout was named" \
        "pass --repo with the registered publication checkout the branch is read from"
}
[ -d "$checkout" ] || fail "the checkout '$checkout' is not a directory" \
    "name the identity's publication checkout, as 'just repos' lists it"
# Resolved before anything changes the working directory: every path a caller typed
# is relative to where they typed it, and the graph run below has to happen at the
# repository root.
checkout=$(CDPATH='' cd -- "$checkout" && pwd) || fail "the checkout '$checkout' could not be entered" \
    "check its permissions, then retry"
git -C "$checkout" rev-parse --git-dir >/dev/null 2>&1 || fail "the checkout '$checkout' is not a git repository" \
    "name the identity's publication checkout, as 'just repos' lists it"
# `show-ref --verify` and not `rev-parse --verify`: the argument is a branch *name*
# everywhere below it — the worktree is cut from it, the log range is built from it, and
# the composed task names it to the drafter — and `rev-parse` validates a revision
# *expression*, so `main^{commit}` resolves through `refs/heads/<expression>` and is
# admitted as a branch this checkout does not have. `show-ref --verify` compares the
# whole ref name, so only a branch that exists under that exact name passes.
git -C "$checkout" show-ref --verify --quiet "refs/heads/$branch" || fail "the checkout '$checkout' has no branch '$branch'" \
    "import it first with 'just import-branch $branch --repo $checkout', or name a branch that checkout holds"

if [ -n "$out" ]; then
    out_directory=$(dirname -- "$out")
    if [ ! -d "$out_directory" ] || [ ! -w "$out_directory" ]; then
        fail "the body cannot be written to '$out'" \
            "name a path in an existing writable directory, or omit --out to print the body"
    fi
    # A destination that is already a directory is a mistake in the command line, not a
    # fault of the run: nothing can write a file there. Refused here, because past this
    # point the next thing that happens is a real provider turn, and the caller would
    # pay for it to reach the same refusal.
    [ ! -d "$out" ] || fail "the body cannot be written to '$out', which is a directory" \
        "name the file the body goes in, not the directory it goes under"
    out=$(CDPATH='' cd -- "$out_directory" && pwd)/$(basename -- "$out") || fail "the path '$out' could not be resolved" \
        "name a path in an existing writable directory, or omit --out to print the body"
fi

# The base decides what diff the drafter is shown, so a guess here is a body about
# the wrong change. `onevcs` is what knows a branch's base; when it cannot say — an
# unregistered branch, or a name several identities answer to — the answer is to be
# told one, not to fall back to whatever this host calls its trunk.
if [ -z "$base" ]; then
    if ! status=$(cd "$repo_root" && uv run onevcs status "$branch" --json 2>/dev/null); then
        fail "onevcs could not report branch '$branch', so its base is unknown" \
            "pass --base with the ref this branch is diffed against, or register and import the branch first"
    fi
    base=$(printf '%s' "$status" | "$python" -c '
import json, sys

report = json.load(sys.stdin)
branch = report.get("branch") if isinstance(report, dict) else None
base = branch.get("base") if isinstance(branch, dict) else None
sys.stdout.write(base if isinstance(base, str) else "")
') || base=""
    # llmlint: ignore[changed_behavior_has_e2e] Guards a report `onevcs status` resolved as something other than a branch — a change request, a session, a commit — which carries no `branch.base`. Not producible for a name this script has already verified is a branch of the named checkout, since `onevcs` resolves a branch ahead of a commit and a scratch registry holds no sessions or change requests to shadow it.
    [ -n "$base" ] || fail "onevcs reported branch '$branch' without a base" \
        "pass --base with the ref this branch is diffed against"
fi
git -C "$checkout" rev-parse --verify --quiet "$base^{commit}" >/dev/null || fail "'$base' is not a commit the checkout '$checkout' holds" \
    "fetch it, or pass --base with a ref that checkout can resolve"

# The alternate identities' environment indirections, taken from the two helpers that
# own them rather than derived here: `oneharness` refuses to start a variant whose
# `env_from` names a variable the parent process does not set, and
# `oneharness.pr-author.toml`'s chain names one per alternate identity. A dispatch
# exports all three, so a drafter that inherits them instead of establishing them works
# on the run path and dies `unstartable` on every out-of-band landing — which is the
# only way this script is run.
#
# Placed here rather than at the top so a mistyped command line is refused without
# `ensure_codex_alt_home` creating a directory for a turn that will never run, which is
# the rule `scripts/onepipeline.sh` applies to its own read-only verbs.
alt_config_helper="$script_dir/claude-alt-config-dir.sh"
if [ ! -f "$alt_config_helper" ] || [ ! -r "$alt_config_helper" ]; then
    fail "required helper is not a readable regular file: $alt_config_helper" \
        "restore it from the repository or run 'just bootstrap', then retry"
fi
# shellcheck source=scripts/claude-alt-config-dir.sh
. "$alt_config_helper"
# llmlint: ignore[changed_behavior_has_e2e] What this line owes is that the helper is invoked and its refusal propagated, which `test_an_indirection_its_helper_refuses_stops_the_drafter_before_the_turn` drives per variable. The helper's own branches — an unset HOME, an existing directory it cannot read — are driven at the seam its file declares, where `scripts/claude-alt-config-dir.sh`'s sibling carries the file-scoped ignore naming them; re-driving them through this entry point would prove the same helper a second time.
resolve_claude_alt_config_dir draft-pr-body || exit $?
codex_alt_helper="$script_dir/codex-alt-home.sh"
if [ ! -f "$codex_alt_helper" ] || [ ! -r "$codex_alt_helper" ]; then
    fail "required helper is not a readable regular file: $codex_alt_helper" \
        "restore it from the repository or run 'just bootstrap', then retry"
fi
# shellcheck source=scripts/codex-alt-home.sh
. "$codex_alt_helper"
# llmlint: ignore[changed_behavior_has_e2e] As above: the invocation and the propagated refusal are driven, and this helper's own branches — an unset HOME, a directory it cannot create or restrict — are covered at the seam `scripts/codex-alt-home.sh` declares in its own file-scoped ignore.
ensure_codex_alt_home draft-pr-body || exit $?

# What the branch says about its own work, oldest commit first. Read from the checkout
# rather than from the worktree below so a range git cannot walk is reported before
# anything is created. A branch carrying nothing its base does not is not that case —
# it is drafted from its diff alone, and the composed task says so.
commits=$(git -C "$checkout" log --reverse --format='commit %h%n%n%B' "$base..$branch" --) || fail "the commits of '$base..$branch' could not be read from '$checkout'" \
    "check that both refs are reachable there, then retry"

# llmlint: ignore[boundary_inputs_validated] `mktemp` is the validation, and a second opinion here could only be a worse one: the `--` covers an option-shaped value, and every other way the caller's `TMPDIR` can be wrong — absent, not a directory, not writable, full — is one `mktemp` refuses with the diagnostic below naming it. A pre-check would restate that badly and still have to run this.
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when the host's temporary directory cannot be created at all; driving it would mean breaking the filesystem the suite itself runs on.
scratch=$(mktemp -d -- "${TMPDIR:-/tmp}/orchestrator-draft-pr-body-XXXXXXXX") || fail "a temporary directory for the drafting turn could not be created" \
    "check that ${TMPDIR:-/tmp} is writable, then retry"
worktree="$scratch/branch"

# The checkout is never left holding this script's worktree, on any exit path — not a
# refusal, not a failed turn, not a signal. `git worktree remove` is what takes the
# entry out of the checkout's worktree list; removing only the directory would leave a
# stale entry behind in a checkout this repository promises not to touch.
#
# Named by path, unconditionally, and deliberately not `git worktree prune`. Both would
# clear this entry — `remove --force` works whether or not the directory is still there
# — but `prune` is a whole-checkout sweep: it also deletes every OTHER prunable entry
# that checkout happened to be carrying, which is somebody else's stranded work and not
# this script's to reclaim. Removing exactly what was added is what makes "the checkout
# is left as it was found" true rather than approximately true.
# shellcheck disable=SC2329,SC2317  # Invoked from the EXIT trap below, which shellcheck cannot follow.
clean_up() {
    # Reported rather than swallowed, because the promise this function makes is that
    # the checkout is left as it was found: a worktree entry that outlived the run is
    # exactly what somebody has to be told about, and a scratch directory that survived
    # holds a change request's prose. Neither is fatal — the drafted body, or the ending
    # that says there was none, is what the caller ran this for.
    git -C "$checkout" worktree remove --force "$worktree" >/dev/null 2>&1 ||
        printf 'draft-pr-body: the worktree at %s could not be removed from %s; remove it with git -C %s worktree remove --force %s\n' "$worktree" "$checkout" "$checkout" "$worktree" >&2
    rm -rf -- "$scratch" ||
        printf 'draft-pr-body: the scratch directory %s could not be removed; remove it by hand\n' "$scratch" >&2
}
trap clean_up EXIT
# The signal traps exist so the EXIT trap above runs for an interrupted drafter too.
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

git -C "$checkout" worktree add --detach "$worktree" "$branch" >/dev/null 2>&1 || fail "a worktree at '$branch' could not be cut from '$checkout'" \
    "check that the checkout is clean and writable, then retry"

task="$scratch/task.md"
# The single quotes are the point rather than an oversight: every backtick below is
# literal markdown the drafter reads, and the only expansion this prose wants is the
# `%s` placeholders printf fills from the arguments beside it.
# shellcheck disable=SC2016
{
    printf '%s\n\n' "$ONEPIPELINE_OPENING"
    printf 'This branch is `%s`, and its base is `%s`. It is being drafted out of band,\n' "$branch" "$base"
    printf 'away from any run that could hand you the task it was dispatched under, so what\n'
    printf 'follows is not an operator'\''s statement of what the work is for. It is what the\n'
    printf 'branch says about its own work: the full commit messages of `%s..%s`,\n' "$base" "$branch"
    printf 'oldest first, as their authors wrote them.\n\n'
    printf 'Read them as a record of what was done. Write `## What` honestly from the diff,\n'
    printf 'whatever those messages hold. Take `## Why` only from what they actually say about\n'
    printf 'why the work mattered; where they say nothing about that, let `## Why` be thin — one\n'
    printf 'honest sentence, or the plain fact that the branch'\''s own record does not say. A thin\n'
    printf '`## Why` is a correct outcome here. A `## Why` you invented is not.\n\n'
    if [ -n "$commits" ]; then
        printf 'The commit messages of `%s..%s`, oldest first:\n\n' "$base" "$branch"
        printf '%s\n' "$commits"
    else
        printf 'The branch carries no commit its base does not, so its diff is the only record\n'
        printf 'there is of what it did.\n'
    fi
# llmlint: ignore[changed_behavior_has_e2e] Reachable only when the temporary directory `mktemp -d` just created stops being writable mid-run; driving it would mean breaking the filesystem the suite itself runs on.
} >"$task" || fail "the drafting task could not be written to '$task'" \
    "check that ${TMPDIR:-/tmp} is writable, then retry"

# From the repository root, because every ref inside the graph document resolves
# against that document's own directory and the document is named relatively. Its
# envelopes are captured rather than streamed: stdout belongs to the body alone.
events="$scratch/events.jsonl"
status=0
(cd "$repo_root" && uv run oneagentgraph run "$PR_AUTHOR_GRAPH" \
    --task-file "$task" --dir "$worktree" --output json) >"$events" || status=$?

"$python" -c "$READ_PROGRAM" "$events" "$out" "$status"
