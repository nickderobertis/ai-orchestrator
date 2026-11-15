# Budgets

A **budget** is a measurable requirement with a threshold: how slow a page may be, how much
of a quota a run may spend, how long a gate may take. A plan first decides whether its work
adds or changes a budget. Only then does it perform the budget analysis and state its
budgets before any work starts, the user approves them in the plan's design document, and the merge path holds
the work to them. This is how a feature that turns out slow, quota-hungry or expensive once
it meets realistic use is caught before it lands rather than after.

Budgets are registered with the library
[`onebudgetspec`](https://github.com/nickderobertis/onebudgetspec). Its budgets file and
its result format are that library's contract, and its own documentation is the authority
for the file's shape. This page restates none of it beyond the keys a plan's budgets name.

## Principles

- **Approved budgets are the requirement, no more and no less.** What the user approves is
  what must be met. Do not optimize merely to beat that threshold; repairing regressions
  found during an investigation follows the guidance below, even within budget.
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
  root budget still refuse the push — `gate-leftovers`, `gate-peak-temp-disk` and
  `gate-leaked-processes` among them, the three figures that same gate run records about
  its effect on the host, whose measurement `orchestrator/gate_impact.py`'s docstring
  states. **A time target is enforced only in CI, where measurement is consistent**, and
  this repository has no CI, so on this host a seconds budget is labelled `host-variable`:
  still measured on every check and reported with its actual, budget, headroom and host
  conditions, and over it warns rather than refuses. `gate-time` carries it; `own-sessions-read-seconds`
  predates the rule and is strict until it is relabelled. The hook reads the label on the
  root file; a project's uncached `budgets-host` target (`scripts/budgets-host.sh`) reads it
  on the project's `host` budgets, where an errored measurement still fails, and a
  `host-variable` budget is always labelled `host` too, so the cached `budgets` target never
  selects it. A budget labelled `accepted-overrun` is over its bar by a cost the user
  accepted while an upstream fix lands, and its registration names that fix's follow-up
  and says why it is over. The cached `budgets` target (`scripts/budgets.sh`) measures and
  reports it on every check, but over it warns rather than fails until the label comes
  off. `plan-approval-points` carries it, over because onetaskgraph's public write
  boundary re-reads the approved item's project before the approval's metadata write.
  Every other budget, a points or a count budget among them, is checked strictly by both
  targets.
- **Host conditions are recorded for the manager's judgement.** Each result carries the
  conditions it was taken under, such as load, memory and other work running, so the
  manager can tell an extreme or unlikely circumstance from a regression.
- **Stored baselines are informational.** Reports can compare against stored previous
  measurements, including constant baselines and historical measurements. An informational
  baseline is never a line to hold: the approved budget remains the requirement.

Those fragment rules are stated once, with worked examples, in dero-skills' create-repo
reference: [When is a requirement a budget][budget-rule]. The rules a plan's budgets are judged by while the plan is reviewed are stated
once, in the plan checklist, `config/plan-checklist.llmlint.yml`, and where this page
touches one it names the rule rather than restating it.

[budget-rule]: https://github.com/nickderobertis/dero-skills/blob/main/skills/bootstrap/create-repo/references/tools/onebudgetspec.md#when-is-a-requirement-a-budget

## Investigating performance

**Investigate performance within existing budgets.** Suspect the current change first
when performance degrades, before intermittent slowness or the outside world. For a
performance problem such as a CI timeout, read the PR report first through existing
`onevcs change comments` or the host PR view; skip that duplicate read when a failing
budget check already identifies the problem. Read earlier PR comments and job results
when several changes may contribute. Never time base versus branch on a shared host:
profile to investigate, and use CI comparisons through the repository's existing
publication path, with its existing permissions.

When reading a report or seeing a failing budget check, fix the regression as part of
the work. For adverse movement of at least one standard deviation (≥1SD) in a report
being read, make the change cheaper or make room in existing code, profiling to find
where. This includes adverse constant-baseline changes and historical flags. Improvements
show numbers without flags; even a large improvement is no reason to optimize further.
Stay inside existing budgets: make room for new tests and code by speeding up existing
work, never by raising limits or timeouts.

A failed ordinary budget makes the required budgets check red; the engine's existing
retry carries that refusal to the worker. Otherwise read the report only when
investigating a performance problem. Informational flags fail no check and require no
new read, notification or dispatch. Do not require a report arriving after a worker
finishes before that worker can settle.

## The budgets files

<!-- llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] this restates the onebudgetspec keys a plan's budgets name, which the task that added it requires; their reconciliation with the installed library is held by `tests/test_budgets_files.py`, and the vocabularies in it are stated once by the `schema:` of `templates/plan-task-budgets.md.j2`, which the plan store holds every rendering to. -->
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

<!-- llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] This restates the data model the `schema:` declarations of `templates/plan-task-budgets.md.j2` and `templates/plan-description.md.j2` state once, which a planner reads here; the plan store holds every rendering to those schemas, so a record breaking one is refused whatever this page says, and `tests/e2e/planning/` renders one and reads the refusal. -->
A plan first decides whether it needs an added/changed task budget. With none and no
existing analysis, it renders `plan-description` from overview alone and omits
`orchestrator.plan-budgets`; it performs no further budget analysis. Existing analysis
records are preserved and validated in full. When analysis is needed, it lives in two homes,
and the `schema:` declarations of `templates/plan-task-budgets.md.j2` and
`templates/plan-description.md.j2` state the shape of both once.

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
  drafted. Each task's `## Budgets` section and the plan's description are renderings of
  the same records, and the engine refuses, at `just check-plan` and again at launch, a body
  that is no longer the rendering its provenance records.
- **The design document summarizes introduced or modified targets.** The template's
  `budgets_guidance` is the single rendering rule. The writer answers `plan_budgets` with
  the project's `orchestrator.plan-budgets` metadata and `budgets` with every task's
  `orchestrator.budgets` records, in full and as the store holds them, inventing none. Task records and existing project analysis,
  including repo-wide effects, remain intact regardless of what the design document shows.
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
- **A budget a task adds or changes binds that task's judge through a criterion the
  `plan-task` template derives from its record**, after the authored criteria. Why, and what
  each kind of budget's criterion asks, is stated once, in the Jinja comment beside that
  template's `more_criteria` block. An authored criterion restating an owned budget is the
  plan checklist's `plan_criteria_do_not_restate_budgets`, and one naming a repo-wide budget
  the task does not own is its `plan_criteria_omit_repo_wide_budgets`.
- **Goals and budgets trace to each other.** Every non-functional goal a task states names
  the `id` of the budget behind it, and every budget a plan adds or changes traces to a goal
  some task states; what a goal is, is the `goals` variable of `templates/plan-task.md.j2`.
<!-- llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate] -->

## Plans that predate budgets

No list exempts a plan from budgets: a plan written before this requirement is reviewed
like any other the next time `just review-plan` judges it. Exact current key sets
are the implicit legacy format, which the template schemas still read; version 2 selects
modern records, and an unknown version, an extra key, a partial field or a malformed type is
refused. Legacy metadata, rendered task and project bodies and copied records stay
unchanged, without invented source or runtime facts. The `schema:` declarations of
`templates/plan-task-budgets.md.j2` and `templates/plan-description.md.j2` are the schema
authority.
