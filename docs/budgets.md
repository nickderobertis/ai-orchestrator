# Budgets

A **budget** is a measurable requirement with a threshold: how slow a page may be, how much
of a quota a run may spend, how long a gate may take. A plan states its budgets before any
work starts, the user approves them in the plan's design document, and the merge path holds
the work to them. This is how a feature that turns out slow, quota-hungry or expensive once
it meets realistic use is caught before it lands rather than after.

Budgets are registered with the library
[`onebudgetspec`](https://github.com/nickderobertis/onebudgetspec). Its budgets file and
its result format are that library's contract, and its own documentation is the authority
for the file's shape. This page restates none of it beyond the keys a plan's budgets
document names.

## Principles

- **Approved budgets are the requirement, no more and no less.** What the user approves is
  what must be met. Optimizing past an approved budget is not wanted.
- **The measure of record is where the product owner feels the impact.** That is often the
  user's experience, and not only that: API spend, quota headroom, gate time and change
  cycle time all matter to the product owner. Measure as close to that impact as you can —
  UI timing over API timing, API timing over the timing of one method, full cycle time over
  a count of gate runs. Use an inner measure only when the outer one cannot be checked, and
  say why.
- **Measure once.** A check runs its measurement once. It never re-samples until a result
  passes, and never runs the base and the branch side by side.
- **Every result shows actual, budget and headroom**, so a passing result still says how
  close it came.
- **Over budget is a defect on any host.** Close to the line and sometimes over means
  optimize. On this host's pre-push hook, a root budget labelled `host-variable` — the
  gate's own wall clock, `gate-time` — is still measured and reported on every push, but
  over it warns rather than refuses: on a shared, variably loaded host that measurement is
  not consistent, so a strict absolute threshold on it can only be a gate on a consistent
  system such as a CI runner. A failed gate, an errored measurement, and over any other
  root budget still refuse the push. The label is read only by the hook's check of the
  root file; a project's own `budget` Nx target (`nx.json`) checks every budget strictly.
- **Host conditions are recorded for the manager's judgement.** Each result carries the
  conditions it was taken under, such as load, memory and other work running, so the
  manager can tell an extreme or unlikely circumstance from a regression.
- **There are no baselines.** The approved budget is the line to stay within, so nothing
  stores a previous measurement to compare against.

## The budgets files

<!-- llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] this restates the onebudgetspec keys a plan's budgets document names, which the task that added it requires; their reconciliation with the installed library is the `aio-budgets` node's, which adopts that library here, and the vocabularies in it are held to `orchestrator/plan_budgets.py` by `tests/test_plan_budgets_template.py` and `tests/test_plan_budgets.py`. -->
A budgets file is named `budgets.yaml`. Of each budget in it, a plan's budgets document
names the `id`, the `command`, the `unit`, the `direction` (`max` or `min`) and the
`threshold`, and the convention below uses its `description`; onebudgetspec's documentation
states the rest. **The command performs the measurement**, so there is no workload field:
anyone who needs the workload reads the command.
<!-- llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate] -->

Files nest. A repository's **root** `budgets.yaml` holds the budgets that must always be
checked, which are its repo-wide budgets, enforced on the merge path. Each project's own
`budgets.yaml` holds the budgets scoped to that project and is checked by its
affected-only target, so a budget runs only when a change touches what it measures.

## Delivery budgets after landing

A repository opts in to delivery reporting by registering a budget in its root
`budgets.yaml` with `labels: [onepipeline]`. Only this host's run-success hook, through
`just follow-ups`, selects that label; pre-push hooks, CI and Nx targets exclude it.
An `onepipeline`-labelled budget's command runs from the root of that repository's
registered publication checkout with `ONEPIPELINE_RUN_ID` naming the run that landed the
change, `ONEPIPELINE_NODE_ID` naming its node, `ONEPIPELINE_RUNS_DIR` naming the runs root,
and `PATH` putting the launching checkout's locked install first, so `onepipeline` is the
adopted engine. This is what such a command may rely on.

## How a plan states its budgets

- **Every plan writes a budgets document**, the project document `<project>-budgets`,
  rendered from this host's `plan-budgets` template (`templates/plan-budgets.md.j2`). Its
  front matter states the shape of each answer: the realistic workload, a checklist of
  concerns each answered with a budget or a one-line "n/a because …", the answer to *at
  10× realistic usage, what does the product owner notice getting worse first?*, the
  budgets the plan proposes, each repository's expected effect on its repo-wide budgets,
  the realistic-data choices, and the spike findings that changed the plan. `just
  review-plan` and `just check-plan` read it, and the design document shows it.
- **Only an approved design document changes a budgets file.** A plan proposes its changes
  to each budgets file in its budgets document, the design document puts them to the user,
  and approving the design document approves them.
- **Workload belongs to planning.** The plan's budgets document states it, the design
  document shows it, and it is never written into a budgets file, where it would drift from
  the command that actually measures.
- **The reason for an inner measure** goes in the budget's `description` and in the plan's
  budgets document.
- **A feature budget lives in the budgets file of the project that owns what it
  measures**, so it is scoped like the rest of the gate, and it stays there after its plan
  lands to protect that code from later changes. Only a budget that must always be checked
  goes in the root file.
- **A check's own cost counts toward the gate-time budget.**
- **The node that implements a budget owns its command** and its registration in the right
  file, and its criteria name the budget and the realistic workload it holds at. Repo-wide
  budgets are enforced on the merge path and never written into a task's criteria.

## Plans that predate budgets

`config/budgets-migration.yaml` is the one list of plans written before this requirement.
Each entry is a qualified plan project id and the reason it predates budgets. A plan named
there is exempt from every refusal about its budgets document and held to everything else,
and its design document says *"This plan predates budgets: <reason>"* in place of the budget
sections. Adding to the list is a reviewed change to this repository, so a plan cannot
exempt itself. The plan kinds exempt from design approval — a planning launch's project and
a follow-ups launch's — are exempt here too.
