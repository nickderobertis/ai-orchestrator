# Budgets

A **budget** is a measurable requirement with a threshold: how slow a page may be, how much
of a quota a run may spend, how long a gate may take. A plan states its budgets before any
work starts, the user approves them in the plan's design document, and the merge path holds
the work to them. This is how a feature that turns out slow, quota-hungry or expensive once
it meets realistic use is caught before it lands rather than after.

Budgets are registered with the library
[`onebudgetspec`](https://github.com/nickderobertis/onebudgetspec). Its budgets file and
its result format are that library's contract, and its own documentation is the authority
for the file's shape. This page restates none of it beyond the keys a plan's budgets name.

## Principles

- **Approved budgets are the requirement, no more and no less.** What the user approves is
  what must be met. Optimizing past an approved budget is not wanted.
- **The measure of record is where the product owner feels the impact.** That is often the
  user's experience, and not only that: API spend, quota headroom, gate time and change
  cycle time all matter to the product owner. Measure as close to that impact as you can —
  UI timing over API timing, API timing over the timing of one method, full cycle time over
  a count of gate runs. Use an inner measure only when the outer one cannot be checked, and
  say why.
- **A budget is a product-owner-level outcome.** It is what the product owner tracks: a
  quota's headroom on a realistic run, a latency someone waits through, the size of what
  reaches a reader. Each budget carries the threshold the user approves, so a plan's
  budgets read as a short list somebody can audit and adjust.
- **Detailed figures are telemetry, not budgets.** Per-step, per-phase and per-operation
  figures, and a second unit of the same concern (requests beside points), are still
  recorded on every run. The budget's analysis reports them as the breakdown of its figure,
  in the onebudgetspec SDK reporter's `detail`, so a failed budget says which part grew.
- **A budget's command analyses telemetry the gate's tests already record**, and reports its
  figure through the onebudgetspec SDK. It never runs a scenario of its own just to measure,
  except under the standalone-measurement exception the onebudgetspec fragment's
  `budgets_reuse_gate_telemetry` rule states: a behaviour no existing gate exercises, or one
  whose recording there would cost more than measuring it on its own.
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
  root file; a project's own `budgets` Nx target (`nx.json`) checks every budget strictly.
- **Host conditions are recorded for the manager's judgement.** Each result carries the
  conditions it was taken under, such as load, memory and other work running, so the
  manager can tell an extreme or unlikely circumstance from a regression.
- **There are no baselines.** The approved budget is the line to stay within, so nothing
  stores a previous measurement to compare against.

The rule of the three principles on a budget's level, its telemetry and its command is
stated once, with worked examples, in dero-skills' create-repo reference: [When is a
requirement a budget][budget-rule]. This page restates what a planner of this host needs
from it.

[budget-rule]: https://github.com/nickderobertis/dero-skills/blob/main/skills/bootstrap/create-repo/references/tools/onebudgetspec.md#when-is-a-requirement-a-budget

## The budgets files

<!-- llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] this restates the onebudgetspec keys a plan's budgets name, which the task that added it requires; their reconciliation with the installed library is held by `tests/test_budgets_files.py`, and the vocabularies in it are held to `orchestrator/plan_budgets.py` by `tests/test_plan_budgets_template.py`. -->
A budgets file is named `budgets.yaml`. Of each budget in it, a plan's budget names the
`id`, the `command`, the `unit`, the `direction` (`max` or `min`) and the
`threshold`, and the convention below uses its `description`; onebudgetspec's documentation
states the rest. **The command analyses the telemetry the gate's tests record**, so there
is no workload field: the workload is the one those tests run at, and anyone who needs it
reads the command and the tests whose records it reads.
<!-- llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate] -->

Files nest. A repository's **root** `budgets.yaml` holds the budgets that must always be
checked, which are its repo-wide budgets, enforced on the merge path. Each project's own
`budgets.yaml` holds the budgets scoped to that project and is checked by its
affected-only target, so a budget runs only when a change touches what it measures, save
one labelled `host`, which every check runs.

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

<!-- llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] This restates the data model `orchestrator/plan_budgets.py` states once, which a planner reads here; `tests/test_plan_budgets_template.py` fails the moment its records, vocabularies or caps part from that module's own. -->
A plan states its budgets in two homes, and `orchestrator/plan_budgets.py` states the shape
of both once.

- **Each budget lives on the task that owns it.** The node that implements a budget owns its
  command and its registration, so its task carries the budget: the task's
  `orchestrator.budgets` metadata holds a list of budgets, and the task, rendered from this
  host's `plan-task` template with the same list as its `budgets` answer, shows them as a
  `## Budgets` section after its `## Why`. Each budget states its `id`; a `name`, short plain
  English on one line of at most 60 characters; its `basis`, how solid the number is, one of
  `measured`, `arithmetic`, `design`, `published docs` or `estimate`; the `repository` and
  the `file` it is registered in, with its `file_change`, one of `add`, `change` or `none`;
  the `measure`, what is measured and the breakdown its analysis reports, and the
  `inner_measure_reason` when the measure is taken further inward;
  the `unit`, the `direction` (`max` or `min`) and the `threshold`; the `workload` it holds
  at; the `evidence` the number rests on; and the `command`, which analyses the recorded
  telemetry, as it will be registered. Modern records also state
  `schema_version` (integer literal 2), `source` (`direct` or `telemetry`) and
  `check_runtime_seconds` (finite number >= 0, excluding booleans). Direct executes the
  work/check whose result it measures; a command executing work and recording results is
  direct. Telemetry reads existing timing/history records without executing measured work.
  Runtime estimates elapsed seconds of the entire command at the stated workload, including
  setup/reads, distinct from the target, unit and basis; evidence states its source.
  Never infer mode or runtime from basis or command text. A command analysing recorded
  telemetry is `telemetry`; `direct` is left for a standalone measurement under the
  exception above.
- **The plan-level answers live in the plan's own description.** The project's
  `orchestrator.plan-budgets` metadata holds them, and the project's description is rendered
  from the `plan-description` template with the same answers: the plan's overview; what it
  is sizing for, a paragraph of at most 600 characters; the full realistic workload; what
  the product owner notices getting worse first at 10× realistic usage, as a one-line
  summary of at most 240 characters and in full; a checklist of concerns, each answered with
  the `id` of a budget a task owns or with an "n/a because …"; each repository's expected
  effect on existing root budgets, or `[]` if none; the realistic-data choices; and
  the spike findings that changed the plan. Every checklist n/a, every actual effect,
  every realistic-data choice and every spike finding carries a one-line `summary`
  of at most 160 characters. Realistic-data detail stays in the description; owning task
  implementation/data notes identify fixtures or generators, never the design summary.
  Modern plan records have `schema_version` (integer literal 2). Each checklist entry has
  `in_scope` (boolean), beside `concern`, `budget`, `not_applicable` and `summary` (strings).
  Exactly one of budget/not_applicable is nonempty; covered concerns are in_scope=true and
  reference an owned budget. N/a keeps the full reason and capped summary. False means
  outside these changes; true with n/a means deliberately unbudgeted within these changes.
  Gate time and change cycle time are not feature concerns: existing root budgets govern
  them, and an anticipated effect belongs only in repo_wide_effects.
  Each effect has exactly `repository`, `budget`, `effect`, `summary` (strings): normalized
  changed-repository origin, nonempty root-budget id, actual nonempty effect (never `none`),
  and a one-line summary of at most 160 characters. Each repository/budget pair occurs once;
  consolidate effects and verify the existing root budget in the repository read. Feature
  registration and unaffected repositories do not belong here.
- **The metadata is authoritative.** A copy carries metadata and never a template's
  answers, so the records are what every reader reads, on the board as where the plan was
  drafted. `just check-plan` renders each task's `## Budgets` section and the plan's
  description from their records and refuses a body that is not that rendering.
- **The design document summarizes them.** It shows what the plan is sizing for, what
  breaks first at 10×, linked budget names, targets, basis, direct/telemetry measurement
  mode and approximate check seconds. Each GitHub issue destination links to its explicit
  `user-content-<budget id>` anchor; the template HTML-escapes the authoritative id and
  percent-encodes the fragment. Destination comes from current task.location, never the
  budget repository. Legacy sections have no new anchor: their summary uses the task URL,
  a compatibility limitation. Linear and local destinations use task URLs too; Linear's
  anchor capability remains unverified. No URL is persisted in budget metadata.
  Only explicitly in-scope n/a summaries and actual named root effects appear — never a budget's workload, evidence, command or
  budgets file, which a reader follows the link for. A plan with no budget still shows
  its named root effects, with its omissions beside them. A plan with no budget and no
  named root effect shows no Budgets section, however many concerns it answered n/a; those
  answers stay in its description and each task's `## Budgets` section. The writer copies
  these answers from `python -m orchestrator.plan_budgets <project>`, and `just
  finish-plan` refuses a document whose answers differ.
- **Only an approved design document changes a budgets file.** A plan proposes its changes
  to each budgets file on the tasks that own them, the design document puts them to the
  user, and approving the design document approves them.
- **Workload belongs to planning.** The plan and each budget state it, and it is never
  written into a budgets file, where it would drift from the tests whose telemetry the
  command analyses.
- **The reason for an inner measure** goes in the budget's `description` and in the
  budget's `inner_measure_reason`.
- **A feature budget lives in the budgets file of the project that owns what it
  measures**, so it is scoped like the rest of the gate, and it stays there after its plan
  lands to protect that code from later changes. Only a budget that must always be checked
  goes in the root file.
- **A check's own cost counts toward the gate-time budget.**
- **The task owning a budget names it in its criteria**, with the realistic workload it
  holds at. Repo-wide budgets are enforced on the merge path and never written into a
  task's criteria.
<!-- llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate] -->

## Plans that predate budgets

`config/budgets-migration.yaml` is the one list of plans written before this requirement.
Each entry is a qualified plan project id and the reason it predates budgets. A plan named
there is exempt from every refusal about its plan-level answers and held to everything
else, and its design document says *"This plan predates budgets: <reason>"* in place of the
budget summary. Adding to the list is a reviewed change to this repository, so a plan cannot
exempt itself. The plan kinds exempt from design approval — a planning launch's project and
a follow-ups launch's — are exempt here too.

Exact current key sets are the implicit legacy format. Version 2 selects modern records;
unknown versions, extra keys, partial fields and malformed types are refused. Legacy
metadata, rendered task/project bodies, copied records and digests stay unchanged, without
invented source/runtime facts. Newly rendered legacy summaries say `not recorded` for both,
omit ambiguous legacy n/a entries and exclude unnamed/none effects. Existing migration
exemptions remain unchanged. `orchestrator/plan_budgets.py` is the schema authority.
