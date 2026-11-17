#!/usr/bin/env bash
# How much of a design document's budget summary a person reads before opening anything, in
# characters, for #1568's real answers.
#
# The `design-doc-budget-summary-length` budget the root `budgets.yaml` registers. It
# renders the design document from the committed fixture `tests/fixtures/budgets/issue-
# 1568.json` — #1568's budget answers, the plan's `orchestrator.plan-budgets` record and
# every task's budgets with where each task lives: 9 budgets (5 added, and 4 changed as
# version-3 records carrying `previous`), 6 concerns answered n/a, 4 changed repositories
# (one actual named root effect), 1 realistic-data choice and no spike finding — through the
# design-doc template as the pinned engine resolves it from this checkout's `templates/`,
# rendered by the pinned plan store, after holding each fixture record to the fields that
# section reads. It takes the `## Budgets` section, heading included, up to the next
# section's heading, and counts its characters (Unicode code points) with the body of every
# collapsed `<details>` block excluded and the block's `<summary>` line kept: what a person
# reads before opening any block. The reporter's `detail` gives the full section's length
# and each collapsed block's length, as the breakdown. The other answers the template
# requires are one word each and sit outside that section, so they never reach the count.
# The result goes to the file onebudgetspec names in ONEBUDGETSPEC_RESULT, in its result
# format.
set -euo pipefail

fail() {
  echo "budget-design-doc-summary: $1" >&2
  exit 1
}

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd) ||
  fail "the checkout holding $0 cannot be entered, so the template and fixture it renders cannot be read; make that checkout readable and retry"
result=${ONEBUDGETSPEC_RESULT:-}
[ -n "$result" ] || fail "ONEBUDGETSPEC_RESULT names no result file; run this as the design-doc-budget-summary-length budget's command, through onebudgetspec check"
fixture="$root/tests/fixtures/budgets/issue-1568.json"
[ -r "$fixture" ] || fail "the fixture $fixture cannot be read, so there are no answers to render; restore it with 'git checkout -- tests/fixtures/budgets/issue-1568.json' and retry"
# The engine command this checkout installs from `bun.lock`.
engine="$root/node_modules/.bin/onepipeline"
[ -x "$engine" ] || fail "this checkout has no engine at $engine; provision the checkout with 'just bootstrap' and retry"
python="$root/.venv/bin/python"
[ -x "$python" ] || fail "$python is not this checkout's locked interpreter; provision the checkout with 'just bootstrap' and retry"

scratch=$(mktemp -d "${TMPDIR:-/tmp}/budget-design-doc-summary.XXXXXX") ||
  fail "a scratch directory for the template's loader and answers could not be created; check that ${TMPDIR:-/tmp} is writable and retry"
# Removed on every exit; a removal that fails leaves a scratch directory behind, which is
# named for the operator to delete rather than left to a raw `rm` diagnostic.
discard() {
  rm -rf -- "$scratch" ||
    echo "budget-design-doc-summary: the scratch directory $scratch could not be removed; delete it by hand" >&2
}
trap discard EXIT
loader="$scratch/loader.json"
answers="$scratch/answers.json"

"$engine" template resolve design-doc --json --template-root "$root/templates" >"$loader" ||
  fail "the pinned engine did not resolve the design-doc template through $root/templates (its diagnostic is above); repair the template until 'scripts/onepipeline.sh template check design-doc' passes, then retry"
"$python" - "$fixture" >"$answers" <<'PY' ||
import json, sys

answers = json.loads(open(sys.argv[1], encoding="utf-8").read())
if not isinstance(answers, dict) or not isinstance(answers.get("budgets"), list) or not isinstance(answers.get("plan_budgets"), dict):
    sys.exit(
        "budget-design-doc-summary: the fixture does not hold the design document's "
        "`plan_budgets` object and `budgets` list"
    )
# Each record is read by the summary table by these fields, so one missing or of another
# type is refused here, naming it, rather than rendered as an empty cell.
for position, budget in enumerate(answers["budgets"]):
    missing = [
        key
        for key in ("id", "name", "node", "link", "measure", "unit", "direction", "file_change")
        if not isinstance(budget, dict) or not isinstance(budget.get(key), str) or not budget[key]
    ]
    if isinstance(budget, dict) and (
        isinstance(budget.get("threshold"), bool)
        or not isinstance(budget.get("threshold"), (int, float))
    ):
        missing.append("numeric threshold")
    if missing:
        sys.exit(
            f"budget-design-doc-summary: the fixture's budget {position} lacks "
            f"{', '.join(missing)}; restore the fixture from the repository, then retry"
        )
if not isinstance(answers["plan_budgets"].get("checklist", []), list):
    sys.exit(
        "budget-design-doc-summary: the fixture's plan-level `checklist` is not a list; "
        "restore the fixture from the repository, then retry"
    )
answers.pop("predates_budgets", None)
answers.update(
    what="W", why="Y", architecture="A", units=[], acceptance_criteria=["C"], planned_tasks=[]
)
json.dump(answers, sys.stdout)
PY
  fail "the fixture's answers could not be composed (the diagnostic above names why); restore it with 'git checkout -- tests/fixtures/budgets/issue-1568.json' and retry"
# Rendered from the checkout, whose `onetaskgraph.yaml` is the configuration the pinned
# store reads: onebudgetspec runs this from the directory of the budgets file naming it,
# which a copy of the root file puts outside the checkout, and there a source root the
# environment sets (as a launch or the test suite does) is a setting with no source to
# belong to, which the store refuses before it renders anything.
body=$(cd -- "$root" && "$root/.venv/bin/onetaskgraph" template render --template-loader "$loader" --answers "$answers" --no-interactive) ||
  fail "the pinned plan store did not render the design document from the fixture (its diagnostic is above); repair the fixture's answers or the template it names until the store renders them, then retry"
measured=$(BUDGET_DESIGN_DOC_BODY="$body" "$python" - <<'PY'
import json, os, re, sys

body = os.environ["BUDGET_DESIGN_DOC_BODY"]
if "## Budgets\n" not in body:
    sys.exit(1)
section = "## Budgets\n" + body.split("## Budgets\n", 1)[1].split("\n## ", 1)[0] + "\n"
# A collapsed block as the template writes it: `<details>`, its `<summary>` line, then the
# body a reader sees only on opening it, up to `</details>`.
blocks = list(
    re.finditer(r"^<details>\n<summary>[^\n]*</summary>\n(.*?)^</details>$", section, re.M | re.S)
)
hidden = sum(len(block.group(1)) for block in blocks)
lengths = ", ".join(str(len(block.group(0))) for block in blocks) or "none"
detail = (
    f"read before opening any block, of the ## Budgets section the design-doc template renders "
    f"for the #1568 answers in tests/fixtures/budgets/issue-1568.json; full section "
    f"{len(section)} characters; collapsed blocks {lengths} characters"
)
print(json.dumps({"value": len(section) - hidden, "detail": detail}))
PY
) || fail "the rendering carries no '## Budgets' section to measure, so the fixture adds or changes no budget or the template no longer renders the summary; restore the fixture, or the template's budgets block, until 'tests/test_budgets_files.py' passes, then retry"
printf '%s\n' "$measured" >"$result" ||
  fail "the result could not be written to $result, the file onebudgetspec named; check that its directory is writable and has free space, then retry"
