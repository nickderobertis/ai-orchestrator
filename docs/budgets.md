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
- **Which figure a budget measures** is the plan checklist's
  `plan_budget_inner_measure_has_reason`, read off the budget's `measure` and
  `inner_measure_reason`.
- **What a budget is and what its command runs are the onebudgetspec fragment's rules**:
  `budgets_track_product_owner_outcomes`, `budgets_reuse_gate_telemetry`,
  `budgets_scoped_to_minimal_tree` and `budget_commands_measure_directly`, which the plan
  checklist re-declares for plans. A budget's command reports its figure through the
  onebudgetspec SDK, with the breakdown that explains it in the SDK reporter's `detail`.
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

Those fragment rules are stated once, with worked examples, in dero-skills' create-repo
reference: [When is a requirement a budget][budget-rule]. The rules a plan's budgets are judged by while the plan is reviewed are stated
once, in the plan checklist, `config/plan-checklist.llmlint.yml`, and where this page
touches one it names the rule rather than restating it.

[budget-rule]: https://github.com/nickderobertis/dero-skills/blob/main/skills/bootstrap/create-repo/references/tools/onebudgetspec.md#when-is-a-requirement-a-budget

## The budgets files

<!-- llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] this restates the onebudgetspec keys a plan's budgets name, which the task that added it requires; their reconciliation with the installed library is held by `tests/test_budgets_files.py`, and the vocabularies in it are held to `orchestrator/plan_budgets.py` by `tests/test_plan_budgets_template.py`. -->
A budgets file is named `budgets.yaml`. Of each budget in it, a plan's budget names the
`id`, the `command`, the `unit`, the `direction` (`max` or `min`) and the
`threshold`, and the convention below uses its `description`; onebudgetspec's documentation
states the rest. There is no workload field: where a budget's figure comes from is
`budgets_reuse_gate_telemetry`'s, and anyone who needs the workload reads the command and
what it reads.
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
  exception `budgets_reuse_gate_telemetry` admits.
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
- **What a plan states about where a telemetry budget's figure comes from and how it is
  taken** is the plan checklist's `plan_budget_names_its_telemetry_source` and
  `plan_budget_measurement_is_decided`, because the fragment's
  `expensive_tests_stay_behind_their_own_edge` and `budgets_reuse_gate_telemetry` judge
  only what a plan states.
- **Which budgets file a budget is registered in** is `budgets_scoped_to_minimal_tree`'s.
- **A check's own cost counts toward the gate-time budget.**
- **What a task's criteria say about budgets** is the plan checklist's
  `plan_budget_owner_criteria_name_budget_and_workload` and
  `plan_criteria_omit_repo_wide_budgets`.
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
