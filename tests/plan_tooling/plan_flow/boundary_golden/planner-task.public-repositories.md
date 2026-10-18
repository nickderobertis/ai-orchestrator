## What
Decide whether the paginated listing's cursor is an opaque token or a node id.

Plan project: authoring:cursor-shape
Repositories: github.com/example-public-org/open-widget, github.com/example-public-org/open-gadget

## Why
The browser view cannot deep-link to a page until that is settled.

## Acceptance criteria
- The cursor's shape and its type are stated.

## The plan checklist this plan is judged against

This dispatch has a second judge beside its reviewer: the plan checklist, `config/plan-checklist.llmlint.yml`,
which llmlint runs over the description of the plan and every task document each time
the reviewer decides. The panel is not done while any rule below fails, so write the
description and every task against these rules on the first draft. They are listed as
llmlint resolved them when this task was written, the rules of its plugins included, and
are read against a plan rather than code, under the framing the checklist judge is given.

### How the `plan` judge reads the plan

#### You are reading a plan, not code

Every target file is a document of one execution plan, written before any of its
work exists. Each is either the plan's description (its goal, its workload and
sizing, its answer about 10x usage, and its budget checklist) or one plan task's
Markdown document. A task document carries YAML frontmatter, whose
`orchestrator.budgets` record lists the budgets that task owns, and a body with
`## What`, `## Why`, `## Acceptance criteria`, an optional `## Budgets` section
and `## Additional info`. Text under `### Operational notes for this host` is a
standard appendix every task carries: read it as context, never as the plan's
own content, and never report a violation inside it.

The plan describes work in a target repository you cannot see. Judge each rule
against what the plan says will exist once its tasks are done. A rule written
about code, tests, scripts, `budgets.yaml` files or project definitions is
judged against the plan's own statements of them: where each budget will be
registered and what its command will read and measure, what each test will
assert and with what doubled, and which project a test will land in and what
that project will depend on. A plan that states nothing a rule could judge
makes that rule not relevant; never report a rule against a file only because
the repository it describes is not visible to you.

### The rules

#### `budgets_track_product_owner_outcomes`

Budgets track product-owner outcomes, with finer figures as their breakdown.
false when the file registers a budget whose figure is a part of another
budget's figure the repository registers — a step, phase or operation of it
— or the same concern as another budget in a second unit, such as requests
beside points for one quota. true otherwise. Whether a product owner would
care about a figure is never judged.

Judged when the plan registers or keeps a budget, in a task's budget record or a description's budget checklist.

#### `budgets_reuse_gate_telemetry`

true when a budget whose behaviour an existing gate's tests or runtime already
exercise takes its figure from telemetry those runs record and save
temporarily (for example as the test target's output), its `command` only
analysing that data. Also true when no existing gate exercises the measured
behaviour, or when recording it there would cost more than a standalone
measurement. false when a budget re-runs a scenario an existing test already
runs, only to count or time it, where adding telemetry to that test or its
runtime code would be cheaper than the extra gate time.

Judged when the plan registers a budget and states what its command runs or reads.

#### `budgets_scoped_to_minimal_tree`

true when everything specific to a `budgets.yaml`'s measurements lives in the
tree that file's directory roots: the commands it names, the tests they run,
and budget-only fixtures. Calling shared code that lives elsewhere is fine —
a common harness, the code under measurement, a fixture other tests also
use, or telemetry recording added to an existing test or runtime code. false
only when something that exists solely to serve one of its budgets
lives outside that tree.

Judged when the plan registers a budget and states where its budgets file, command, or budget-only fixtures will live.

#### `budget_commands_measure_directly`

true when each budget's `command` runs its measurement directly, or through
one generic runner shared by every budget. false when it goes through a
per-budget wrapper that re-lists budget ids or re-checks that a result was
written — work onebudgetspec already does.

Judged when the plan registers a budget and states its command.

#### `tests_hold_no_nonfunctional_thresholds`

false when the file asserts a cost figure against a fixed bound — wall
clock, request, query or API-point count, payload or artifact size, memory
or money at a realistic workload — or tells two behaviours apart by elapsed
time ("returned within 2 s, so it did not wait") rather than asserting the
observable event. true otherwise, including a configured timeout, deadline
or backoff asserted near its value, an exact count of an action, the
absence of an operation, a limit the product itself enforces, and a timeout
that only guards against a hang.

Judged when the plan states what a test it adds or changes will assert about a timing, size or count figure, or how it bounds a wait.

#### `expensive_tests_stay_behind_their_own_edge`

true when the test lives in a project whose graph edges reach only what it
actually tests, so an unrelated change cannot make it run. false when it sits
in a broadly-depended-on project, or its project depends on unrelated code
(an import, a `dependsOn`, or an implicit dependency), so every change in the
repo pays for it.

Judged when the plan adds or changes a test that is slow or reaches an external service, or a project definition or dependency edge that owns one, or makes a budget's figure depend on one.

#### `budget_descriptions_durable_and_terse`

true when each budget's `description` says, in one or two sentences, what is
measured and what it protects. false when a description reads as session or
changelog narrative — base figures from an old commit, a history of the
figure, or what each journey did — or restates the budget's own fields: its
command, unit, direction or threshold.

Switched off in this checklist: never judged.

#### `onebudgetspec_is_the_only_judge`

true when the code a budget's `command` runs reports its figure (to the file
`ONEBUDGETSPEC_RESULT` names, or as its elapsed run) and leaves the comparison
with the threshold to onebudgetspec. false when that code reads a
`budgets.yaml`, or asserts the figure against a threshold itself.

Switched off in this checklist: never judged.

#### `instruction_layer_localized`

true when subtree rules live in a nested `AGENTS.md` inside each project,
leaving the root `AGENTS.md` to repo-wide constraints only. false when
project-specific rules are piled into the root `AGENTS.md`.

Switched off in this checklist: never judged.

#### `new_projects_keep_the_graph_uniform`

true when the project declares the repo's standard target names for the work
it has (`build`, `lint`, `test`, `typecheck`) and the boundary tags its layer
requires, so `nx affected`/`run-many` fan out by name and the module-boundary
rule can judge it. false when it invents a target name the repo already names,
omits a target it has work for, or is untagged where its layer is tag-enforced.

Switched off in this checklist: never judged.

#### `code_lands_in_the_domain_that_owns_it`

true when a new or changed type, interface (a trait, protocol, abstract base,
or port), store or repository interface, or helper lands in the project of
the domain that owns it — the project whose other code changes when that
concern changes — or in a project that is a genuine cross-domain contract: a
shared identity, an envelope or representation rule, a wire or storage
format, or an interface every domain must agree on. A shared package is a
contract only if adding or changing one domain's concept does not require
editing it. A contract holds what every domain must agree on and never
enumerates the domains: a closed sum type, registry, or table in a central
package with an entry per domain's things is a bag with a type signature,
however it is persisted or served. false when it lands in a project grouped
by the kind of code it is rather than by the concern it serves — a central
`types`, `models`, `store`, `data-access`, `utils`, `common` or `shared`
package several domains depend on — so that a change to one domain's concern
re-touches a package every other domain builds against; and false when it
adds one domain's entry — a variant, a registration, a row, a case — to a
closed sum type, registry, or table in a central package, or declares one
domain's structs in the central package because a central enumeration
reaches them. The project's name is a signal, not the test: a `types`
project holding only the identities, the envelope and the formats every
domain agrees on is a contract, and a `contract` project holding one
domain's structs beside another's is a bag.

Switched off in this checklist: never judged.

#### `plan_criteria_satisfiable_inside_the_dispatch`

true when every acceptance criterion can be met from inside the task's own dispatch,
from what its worker controls. false when one rests on something that happens only
after the dispatch settles or outside it: a change request merging, a release or
deploy, the merge path's or a host's required checks passing, a scheduled job, or a
third party acting. A criterion naming a later comparison while stating that its
result is not the task's bar, one about the dispatch's own judged lint, and one about
a draft or demonstration change request the task grants its worker rest on nothing
outside it.

Judged when the task document has an `## Acceptance criteria` section.

#### `plan_criteria_require_realistic_tests`

true when every criterion asking for proof of behaviour requires a test that drives
the real interface the behaviour is reached by, doubling at most a paid, external or
third-party boundary, and asserts the behaviour itself rather than internal state or
a snapshot of output.

Judged when the task's acceptance criteria ask for a test or other proof of behaviour.

#### `plan_workload_states_numbers`

true when every realistic workload the file states gives its scale in numbers: how
many of what, how often, how large. false when one is stated only in words such as
"typical", "a few" or "normal use".

Judged when the file states a realistic workload, in a plan description's workload or a budget's `workload`.

#### `plan_ten_x_names_a_covering_budget`

true when the 10x answer names, by id, a budget the plan registers or keeps whose
figure grows with the effect the answer names.

Judged when the description answers what the product owner notices getting worse first at 10x realistic usage.

#### `plan_budget_command_measures_its_figure`

true when every budget in the task's record names a `command` that, as the plan
describes it, produces that budget's `measure`, in its `unit`, at its `workload`.

Judged when the task's budget record registers or changes a budget.

#### `plan_budget_basis_matches_evidence`

true when each such budget's `evidence` reports a measurement of its figure at its
`workload`.

Judged when the task's budget record holds a budget whose `basis` is `measured`.

#### `plan_budget_inner_measure_has_reason`

true when every budget measures the outermost figure its concern is felt at, or,
where it measures one step, component or inner process of what the user waits on or
pays for, gives a non-empty `inner_measure_reason` saying why.

Judged when the task's budget record registers or changes a budget.

#### `plan_criteria_omit_repo_wide_budgets`

true when the task's acceptance criteria name only budgets the task itself registers
or changes. false when a criterion holds the task to a repository-wide budget it
does not own, such as the gate's own time or another root budget.

Judged when the task document has an `## Acceptance criteria` section.

#### `plan_budget_owner_criteria_name_budget_and_workload`

true when the task's acceptance criteria name, by id, every budget its record
registers or changes, each with the workload it is measured at.

Judged when the task's budget record registers or changes a budget.

#### `plan_budget_names_its_telemetry_source`

true when the task names, for every such budget, the test target or targets whose
tests record what its command reads (the project and target, or the test tier, that
runs them, not only test files) and the project whose budgets file registers it.

Judged when the task's budget record holds a budget whose figure is read from telemetry tests record, by a `source` of `telemetry` or a command that analyses recorded telemetry.

#### `plan_budget_measurement_is_decided`

true when the task states, for every budget it owns, one decided way its figure is
taken (read from telemetry its tests record, or measured fresh by its command)
together with any label the budget carries, such as `host`. false when it leaves the
way to the worker, as an either/or or a "say which".

Judged when the task's budget record registers or changes a budget.

## Additional info

**This dispatch works in a checkout it does not own, so it commits nothing.** It is a
direct node, dispatched into the checkout the flow was launched from — the shared
canonical checkout that several orchestrators use — rather than into a worktree of its
own. So it may write only to gitignored paths, may not commit, may not cut a branch, and
may not leave the checkout on any branch but its base.

That exempts it from one clause of the shared completion bar every dispatch on this host
is judged against — "with every change this dispatch made committed and nothing
half-applied left behind". Here there is nothing to commit, and a clean `git status` is
the correct and complete outcome: what this dispatch writes to a gitignored path is the
deliverable, and committing it would be the failure rather than the proof.