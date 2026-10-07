#!/usr/bin/env bash
# How long a design document's budget summary runs, in characters, for #1568's real answers.
#
# The `design-doc-budget-summary-length` budget the root `budgets.yaml` registers. It
# renders the design document from the committed fixture `tests/fixtures/budgets/issue-
# 1568.json` — #1568's budget answers in the shape `python -m orchestrator.plan_budgets`
# prints: 9 budgets, 6 concerns answered n/a, 4 repositories (one with an effect, three
# with none), 1 realistic-data choice and no spike finding — through the design-doc template
# as the pinned engine resolves it from this checkout's `templates/`, rendered by the pinned
# plan store, and counts the characters (Unicode code points) of its `## Budgets` section,
# heading included, up to the next section's heading. The other answers the template
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

"$root/.venv/bin/onepipeline" template resolve design-doc --json --template-root "$root/templates" >"$loader" ||
  fail "the pinned engine did not resolve the design-doc template through $root/templates (its diagnostic is above); repair the template until 'scripts/onepipeline.sh template check design-doc' passes, then retry"
"$python" - "$fixture" "$root" >"$answers" <<'PY' ||
import json, sys

sys.path.insert(0, sys.argv[2])
from orchestrator import plan_budgets

answers = json.loads(open(sys.argv[1], encoding="utf-8").read())
shaped = (
    isinstance(answers, dict)
    and set(answers) == {"predates_budgets", "plan_budgets", "budgets"}
    and isinstance(answers["predates_budgets"], str)
    and isinstance(answers["budgets"], list)
    and all(
        isinstance(entry, dict)
        and set(entry) == {"node", "location", *plan_budgets.BUDGET_KEYS}
        and isinstance(entry["node"], str)
        and isinstance(entry["location"], str)
        for entry in answers["budgets"]
    )
)
if not shaped:
    sys.exit(
        "budget-design-doc-summary: the fixture does not hold exactly the three budget "
        "answers `python -m orchestrator.plan_budgets` prints: the predates reason as text, "
        "and each budget with its node and its location as text and the keys a task's "
        "budgets record holds"
    )
try:
    plan_budgets.parse_plan(answers["plan_budgets"])
    plan_budgets.parse_budgets(
        [{key: entry[key] for key in plan_budgets.BUDGET_KEYS} for entry in answers["budgets"]]
    )
except plan_budgets.BudgetsError as exc:
    sys.exit(f"budget-design-doc-summary: the fixture's answers are malformed: {exc}")
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
  fail "the pinned plan store did not render the design document from the fixture (its diagnostic is above); repair the fixture's answers or the template it names until 'tests/test_design_doc_template.py' passes, then retry"
section=$(BUDGET_DESIGN_DOC_BODY="$body" "$python" - <<'PY'
import os, sys

body = os.environ["BUDGET_DESIGN_DOC_BODY"]
if "## Budgets\n" not in body:
    sys.exit(1)
print(len("## Budgets\n" + body.split("## Budgets\n", 1)[1].split("\n## ", 1)[0] + "\n"))
PY
) || fail "the rendering carries no '## Budgets' section to measure, so the fixture adds or changes no budget or the template no longer renders the summary; restore the fixture, or the template's budgets block, until 'tests/test_design_doc_template.py' passes, then retry"
printf '{"value": %s, "detail": "the ## Budgets section the design-doc template renders for the #1568 answers in tests/fixtures/budgets/issue-1568.json"}\n' \
  "$section" >"$result" ||
  fail "the result could not be written to $result, the file onebudgetspec named; check that its directory is writable and has free space, then retry"
