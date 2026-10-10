---
title: 'feat(budgets): budget what the product owner tracks and record the rest as telemetry'
status: todo
project: nonfunctional-requirements-are-budgets
depends_on: [{"id":"feat-create-repo-state-when-a-requirement-is-a-budget-and-lint-tests-that-assert-one","kind":"blocks","item":"task"},{"id":"fix-tests-prove-a-step-did-not-wait-by-observing-its-held-double-not-the-clock","kind":"blocks","item":"task"},{"id":"feat-check-print-each-result-s-detail-under-its-line-in-text-output","kind":"blocks","item":"task"}]
metadata:
  onepipeline.deps:
  - run:A-onebudgetspec-convention--upstreamed--encoded-in-create-repo--and-applied-to-its-consumers#aio-obs-convention-r3
  onepipeline.execution_checkout: ai-orchestrator-isolated
  onepipeline.id: aio-budgets
  onepipeline.max_turns: 120
  onepipeline.persona: engineer
  "onetaskgraph.template": {"answers_digest":"sha256:f8bad2ab2749ad6b9305f65dce1a54629826295327411620b7ff7e552b4b80a2","body_digest":"sha256:fc54570da39734ae7ba3048da64b3bbbfeb0d1542af56c7cb43709b48b7d967b","digest":"sha256:2b9a5855d9a64466bf9c0bd5843e0b8d79f1055e8187d7526c045a2253f22ff3","template":"onepipeline:plan-task"}
  "orchestrator.plan-review": {"by":"review-plan","key":"24db3ff6d66722ea040f207d59eea8956117d18cd97cb2d8b1c1211b8f180ca5","reviewed_at":"2026-10-08T13:40:38.253005+00:00"}
  "onetaskgraph.copies": {"plans":"plans:I_kwDOTXCWqs8AAAABVmqE1g"}
  "orchestrator.budgets": [{"basis":"measured","command":"uv run --directory .. python -m tests.budget_telemetry","direction":"max","evidence":"Measured 160 on onetaskgraph 0.3.6 by the realistic-run journey in tests/e2e/test_onetaskgraph_host_e2e.py: searches 24, write path 116, board check 20, with requests equal to points for every document. That is the figure the test enforces today, min(208, 160).","file":"orchestrator/budgets.yaml","file_change":"add","id":"follow-up-run-points","inner_measure_reason":"The live board's shared allowance cannot be spent from the gate, so the run is priced on the loopback board stand-in with the drift-checked upstream prices.","measure":"Modelled GitHub GraphQL points one realistic follow-up run spends on the followups board, out of the board token's shared 5,000-point hourly allowance. Its detail breaks the figure down by phase (searches, write path, board check), by document, and by step, with request counts beside each.","name":"Board points a realistic follow-up run spends","repository":"github.com/nickderobertis/ai-orchestrator","threshold":160,"unit":"points","workload":"One follow-up run: 16 tickets (12 new, each searched for by root cause and by text, then decided, validated and copied; 4 already bound, edited and re-copied), 4 evidence comments each with its re-estimate, and the launch's one board check, on a followups stand-in holding 400 other items."},{"basis":"measured","command":"uv run --directory .. python -m tests.budget_telemetry","direction":"max","evidence":"Measured on onetaskgraph 0.3.6 by test_one_tickets_filing_re_copy_withdrawal_and_comment_answer_stay_within_their_budget: filing 8, re-copy 4, answer 7, withdrawal 4. User ruling: 23, today's measured figure, not the 34 that the per-step ceilings 10/7/9/8 sum to.","file":"orchestrator/budgets.yaml","file_change":"add","id":"ticket-lifecycle-points","inner_measure_reason":"The live board's shared allowance cannot be spent from the gate, so the steps are priced on the loopback board stand-in with the drift-checked upstream prices.","measure":"Modelled GitHub GraphQL points one follow-up ticket's life spends on the followups board: filing, re-copy after an edit, answering a person's comment, and withdrawal. This covers the answer and withdrawal steps the realistic run does not drive. Its detail gives points and requests per step.","name":"Board points one ticket's whole life spends","repository":"github.com/nickderobertis/ai-orchestrator","threshold":23,"unit":"points","workload":"One ticket filed, re-copied after an edit, answered once in feedback mode, and withdrawn, on a followups stand-in holding 60 other items."},{"basis":"design","command":"uv run --directory ../.. python -m tests.budget_telemetry","direction":"max","evidence":"Inherited ceiling: OWN_READ_BOUND_SECONDS = 10.0 in tests/unfinished/test_unfinished_e2e.py bounds the same read today. The user approved keeping it; no measurement backs the figure, and the journey's recorded telemetry will.","file":"tests/unfinished/budgets.yaml","file_change":"add","id":"own-sessions-read-seconds","inner_measure_reason":"","measure":"Wall clock of `just unpublished --own --no-disk --json`, the read the Stop hook and a manager wait through before a turn may end. Its detail gives the scale it ran at.","name":"Time to list a manager's own unpublished sessions","repository":"github.com/nickderobertis/ai-orchestrator","threshold":10,"unit":"seconds","workload":"Three onevcs identities with twenty closed sessions each and ten preserved branches, the scale registry tests/unfinished/test_unfinished_e2e.py builds."},{"basis":"design","command":"uv run --directory .. python -m tests.budget_telemetry","direction":"max","evidence":"Inherited ceiling: NAMED_FAILURE_LIMIT = 400, which both lost-turn journeys assert today. The user approved keeping it; no measurement backs the figure, and the journeys' recorded telemetry will.","file":"orchestrator/budgets.yaml","file_change":"add","id":"lost-turn-surface-chars","inner_measure_reason":"","measure":"Characters of the planner surface a lost monitor turn raises, which a manager reads on the channel. This is the largest across every cause, identity and run-id case, and its detail gives each case's size.","name":"Length of a lost monitor turn's surface","repository":"github.com/nickderobertis/ai-orchestrator","threshold":400,"unit":"characters","workload":"Every cause, identity and run-id case the two lost-turn journeys drive, tests/e2e/test_monitor_quiet_turn_e2e.py and tests/e2e/test_lost_turn_wire_contract_e2e.py, taking the largest surface."},{"basis":"design","command":"uv run --directory .. python -m tests.budget_telemetry","direction":"max","evidence":"Inherited: the existing approved budget, unchanged in id, unit, direction and threshold; its threshold is a design ceiling, not an observed figure. Only its command moves, from re-running the tests to analysing their recorded telemetry.","file":"orchestrator/budgets.yaml","file_change":"change","id":"plan-level-prompt-chars","inner_measure_reason":"","measure":"Characters of the plan-level prompt `just review-plan` composes, which must stay well inside the smallest context window a candidate of the plan-level role answers in. Its detail names the fixture the figure came from.","name":"Size of the plan-level review prompt","repository":"github.com/nickderobertis/ai-orchestrator","threshold":200000,"unit":"characters","workload":"A realistic 44-node, 52-budget plan: the larger of the baseline-shaped and host-shaped fixtures in tests/test_plan_review.py."},{"basis":"design","command":"uv run --directory .. python -m tests.budget_telemetry","direction":"max","evidence":"Inherited: the existing approved budget, unchanged in id, unit, direction and threshold; its threshold is a design ceiling, not an observed figure. Only its command moves, from re-running the tests to analysing their recorded telemetry.","file":"orchestrator/budgets.yaml","file_change":"change","id":"plan-level-prompt-chars-per-node","inner_measure_reason":"","measure":"Characters the costliest node of either realistic fixture adds to the plan-level prompt, so a plan's prompt grows with its node count and never with its task bodies' length. Its detail names the node and fixture.","name":"Review prompt characters each node adds","repository":"github.com/nickderobertis/ai-orchestrator","threshold":1600,"unit":"characters","workload":"The same 44-node, 52-budget fixtures in tests/test_plan_review.py."}]
repositories:
- github.com/nickderobertis/ai-orchestrator
---
## What

Move the cost figures this repository's tests assert into a short list of
product-owner-level budgets. Keep every finer figure as recorded telemetry that the
budget reports as its breakdown. Then put the two new onebudgetspec lint rules in force
here.

**The user's rules.**
- **Three cases.** When a test's subject is how much a behaviour costs at a realistic
  workload, and the bound is a tolerance someone could raise or lower, that figure is a
  budget and the test never compares it with a threshold. Wall-clock discriminators are
  node `aio-timing-events`' work and have landed before this node. Time-feature
  contracts, exact counts, absent operations and product-enforced limits stay test
  assertions. Among those that stay are `LAUNCH_VALIDATION_BOARD_CHECKS == 1`,
  `_bounded_step`'s one-read-per-item, no-board-walk, no-search-for-a-bound-item and
  no-wide-page checks, `plan_review.SUMMARY_LIMIT`, and `task_body.BODY_LIMIT` and
  `WARN_FROM`.
- **The level.** A budget is an outcome the product owner tracks. Per-step, per-phase
  and per-operation figures, and a second unit of the same concern (requests beside
  points), are telemetry. They are recorded on every run and reported as the breakdown
  of their budget's figure, so a failed budget says which part grew.
- **The command.** A budget's command analyses telemetry the gate's tests already
  record, and reports through the onebudgetspec Python SDK. It never re-runs a scenario
  just to measure, except under the standalone-measurement exception the fragment's
  `budgets_reuse_gate_telemetry` states.

**The budgets.** There are exactly the six in this task's `## Budgets` section. All are
`measure: reported` and `direction: max`.
- `orchestrator/budgets.yaml` gains `follow-up-run-points` (160 points),
  `ticket-lifecycle-points` (23 points) and `lost-turn-surface-chars` (400 characters).
  It keeps `plan-level-prompt-chars` (200000) and `plan-level-prompt-chars-per-node`
  (1600) at their id, unit, direction and threshold, changing only their commands.
- A new `tests/unfinished/budgets.yaml` takes `own-sessions-read-seconds` (10 seconds),
  labelled `host`.
- The deterministic budgets are cached. `own-sessions-read-seconds` never reports a
  wall-clock figure restored from a cache. It either reads a figure its test measured
  during the same budgets run, or it is a standalone measurement under the exception
  above. Say which, and why.

**Each budget's breakdown is the telemetry that explains it**, reported as the SDK
reporter's `detail`. Every line names a part and its figure.
- `follow-up-run-points`: points per phase (searches, write path, board check); points
  and requests per document (`search`, `originLookup`, `createIssue` and the rest); and
  points and requests per step the per-step journey
  (`test_follow_up_commands_and_account_check_read_only_their_items_once`) records
  (unbound status, create copy, bound status, bound re-copy, re-estimate, evidence post,
  account board check). The per-step figures come from the 60-item journey, so the
  detail labels them as such and does not add them into the 160.
- `ticket-lifecycle-points`: points and requests for each of filing, re-copy, answering
  a comment and withdrawal.
- `lost-turn-surface-chars`: the size of each cause, identity and run-id case across
  `tests/e2e/test_monitor_quiet_turn_e2e.py` and
  `tests/e2e/test_lost_turn_wire_contract_e2e.py`, with the largest case named. The
  budget's figure is that largest case.
- `own-sessions-read-seconds`: the scale it ran at (identities, closed sessions,
  preserved branches).
- The two plan-level prompt budgets: the fixture each figure came from.

**What stops being a budget.** The draft's 35 budgets become these six. Every figure
below is still recorded as telemetry by the journey that measures it today, and is named
in a breakdown above, with one exception.
- `UNBOUND_STATUS_BUDGET`, `CREATE_COPY_BUDGET`, `BOUND_STATUS_BUDGET`,
  `BOUND_COPY_BUDGET`, `RE_ESTIMATE_BUDGET`, `EVIDENCE_POST_BUDGET` and
  `BOARD_CHECK_BUDGET` in the host journey, in requests and points: in
  `follow-up-run-points`' breakdown.
- `REALISTIC_RUN_POINTS`, `REALISTIC_SEARCH_POINTS`, `REALISTIC_WRITE_POINTS`,
  `REALISTIC_BOARD_CHECK_POINTS` and the `REALISTIC_MEASURED_*` figures: replaced by
  `follow-up-run-points` and its phase breakdown.
- `FILING_*`, `RECOPY_*`, `ANSWER_*` and `WITHDRAWAL_*` in requests and points: replaced
  by `ticket-lifecycle-points` and its step breakdown.
- **The one exception:** `tests/plan_tooling/test_follow_ups_recipe_e2e.py`'s launch
  board-check figures (`BOARD_CHECK_BUDGET` through the recipe, and
  `LAUNCH_REFUSAL_BUDGET`). That journey still records them as telemetry, but no
  budget's breakdown carries them. Reading them would make the `orchestrator` project's
  budgets depend on the `plan_tooling` test target, coupling two projects' caches for a
  4-request figure. The launch's exact-count assertion stays a test, and no
  `tests/plan_tooling/budgets.yaml` is created.

**Audit the whole test tree first.** The list above is the planner's inventory, not a
complete one. Read every file under `tests/` for any measurable non-functional cost a
test asserts or bounds: wall clock; request, query, call or API-points counts; bytes,
characters or artifact sizes; memory; money, tokens or model turns. Classify each one:
- **A cost figure** becomes telemetry in an existing budget's breakdown if it is a part
  or second unit of that budget's figure. Otherwise it is a candidate new budget.
- **A wall-clock discriminator** that `aio-timing-events` missed is rewritten the same
  way, to wait on an event.
- **A figure that stays an assertion**, with a one-line reason.
A candidate new budget, or any figure you would fold into a breakdown that is not on the
list above, is escalated before the inventory changes. Ask the manager through
`$ORCHESTRATOR_ASK_MANAGER` with its file, line, value and what it measures.

**How.**
- Add `onebudgetspec-sdk` as a dev dependency at the same version as
  `config/onebudgetspec.version`.
- Move `config/onebudgetspec.version` to the release carrying node `obs-text-detail`'s
  text output. That release also carries the SDK's `report`, which first shipped in
  0.1.3. The release this node waits for is stated in the references block the harness
  appends.
- The existing journeys keep running as they do. Each also writes the parts it measures
  to temporary, uncommitted telemetry, declared as its test target's output.
- One generic analysis, `python -m tests.budget_telemetry`, serves every budget. It
  chooses the budget by `ONEBUDGETSPEC_BUDGET_ID`, which onebudgetspec sets for every
  command. It sums or selects the budget's figure from the recorded parts, and calls
  `onebudgetspec_sdk.report(value, detail)` with the breakdown. It never reads a
  `budgets.yaml` and never compares with a threshold. No per-budget script.
- Each project's `budgets` target depends on the test target that records its
  telemetry.
- Each description is one or two sentences on what is measured and what it protects.
- Remove each retired constant, and the assertion that held it, from test code.
  `RequestBudget` stops being a threshold carrier. `_bounded_step` and `_step_cost`
  record figures and keep only their structural checks. Keep or retire
  `BUDGET_MEASURED_ON` on its merits, and say which and why.
- Do not edit `docs/budgets.md`, `personas/planner.yaml` or the budgets templates. Node
  `aio-budget-convention` owns them.

**The rules in force here.** After `aio-obs-convention-r3`, `llmlint.yml` lists
`https://raw.githubusercontent.com/nickderobertis/dero-skills/main/skills/bootstrap/create-repo/assets/llmlint/tools/onebudgetspec.llmlint.yml@1`.
Keep that `@1` entry, adding it if it is missing. Make sure the resolved plugin is the
`1.1.0` release that node `cr-budget-rule` landed, carrying
`tests_hold_no_nonfunctional_thresholds` and `budgets_track_product_owner_outcomes`.
Then add one terse line to `AGENTS.md`'s "Tests are context engineering" section: a
cost figure in this repository's tests is a budget at the product-owner level, with
finer figures as its recorded breakdown, and a wall-clock bound never tells two
behaviours apart.


## Why

The user wants every non-functional requirement that carries a number to be something
they can audit, track and adjust in one place, a `budgets.yaml`. They also want that place
to read as a short list of what the product owner tracks: the board quota a realistic
follow-up run spends, what one ticket's life costs, how long a read someone waits through
takes, and how much text reaches a reader. Dozens of step-level numbers would make it
hard to audit and adjust. The detail must not be lost, because a failed budget has to
point at what to fix, so every finer figure stays as recorded telemetry, shown as its
budget's breakdown. Today about thirty quota and size ceilings sit as constants in
`tests/github_board.py` and its journeys, unseen until one fails or is quietly loosened.
This repository is the canonical example of how these libraries are used together, so it
follows the rule it hands every new repository. Every total sits at today's measured
figure, so approving this plan approves no looser bar than today's.


## Budgets

### Board points a realistic follow-up run spends

- **Id:** `follow-up-run-points`
- **Measure:** Modelled GitHub GraphQL points one realistic follow-up run spends on the followups board, out of the board token's shared 5,000-point hourly allowance. Its detail breaks the figure down by phase (searches, write path, board check), by document, and by step, with request counts beside each.
- **Inner measure, because:** The live board's shared allowance cannot be spent from the gate, so the run is priced on the loopback board stand-in with the drift-checked upstream prices.
- **Target:** at most 160 points
- **Basis:** measured
- **Workload:** One follow-up run: 16 tickets (12 new, each searched for by root cause and by text, then decided, validated and copied; 4 already bound, edited and re-copied), 4 evidence comments each with its re-estimate, and the launch's one board check, on a followups stand-in holding 400 other items.
- **Evidence:** Measured 160 on onetaskgraph 0.3.6 by the realistic-run journey in tests/e2e/test_onetaskgraph_host_e2e.py: searches 24, write path 116, board check 20, with requests equal to points for every document. That is the figure the test enforces today, min(208, 160).
- **Command:** `uv run --directory .. python -m tests.budget_telemetry`
- **Registered in:** `orchestrator/budgets.yaml` of `github.com/nickderobertis/ai-orchestrator`, an entry this task adds

### Board points one ticket's whole life spends

- **Id:** `ticket-lifecycle-points`
- **Measure:** Modelled GitHub GraphQL points one follow-up ticket's life spends on the followups board: filing, re-copy after an edit, answering a person's comment, and withdrawal. This covers the answer and withdrawal steps the realistic run does not drive. Its detail gives points and requests per step.
- **Inner measure, because:** The live board's shared allowance cannot be spent from the gate, so the steps are priced on the loopback board stand-in with the drift-checked upstream prices.
- **Target:** at most 23 points
- **Basis:** measured
- **Workload:** One ticket filed, re-copied after an edit, answered once in feedback mode, and withdrawn, on a followups stand-in holding 60 other items.
- **Evidence:** Measured on onetaskgraph 0.3.6 by test_one_tickets_filing_re_copy_withdrawal_and_comment_answer_stay_within_their_budget: filing 8, re-copy 4, answer 7, withdrawal 4. User ruling: 23, today's measured figure, not the 34 that the per-step ceilings 10/7/9/8 sum to.
- **Command:** `uv run --directory .. python -m tests.budget_telemetry`
- **Registered in:** `orchestrator/budgets.yaml` of `github.com/nickderobertis/ai-orchestrator`, an entry this task adds

### Time to list a manager's own unpublished sessions

- **Id:** `own-sessions-read-seconds`
- **Measure:** Wall clock of `just unpublished --own --no-disk --json`, the read the Stop hook and a manager wait through before a turn may end. Its detail gives the scale it ran at.
- **Target:** at most 10 seconds
- **Basis:** design
- **Workload:** Three onevcs identities with twenty closed sessions each and ten preserved branches, the scale registry tests/unfinished/test_unfinished_e2e.py builds.
- **Evidence:** Inherited ceiling: OWN_READ_BOUND_SECONDS = 10.0 in tests/unfinished/test_unfinished_e2e.py bounds the same read today. The user approved keeping it; no measurement backs the figure, and the journey's recorded telemetry will.
- **Command:** `uv run --directory ../.. python -m tests.budget_telemetry`
- **Registered in:** `tests/unfinished/budgets.yaml` of `github.com/nickderobertis/ai-orchestrator`, an entry this task adds

### Length of a lost monitor turn's surface

- **Id:** `lost-turn-surface-chars`
- **Measure:** Characters of the planner surface a lost monitor turn raises, which a manager reads on the channel. This is the largest across every cause, identity and run-id case, and its detail gives each case's size.
- **Target:** at most 400 characters
- **Basis:** design
- **Workload:** Every cause, identity and run-id case the two lost-turn journeys drive, tests/e2e/test_monitor_quiet_turn_e2e.py and tests/e2e/test_lost_turn_wire_contract_e2e.py, taking the largest surface.
- **Evidence:** Inherited ceiling: NAMED_FAILURE_LIMIT = 400, which both lost-turn journeys assert today. The user approved keeping it; no measurement backs the figure, and the journeys' recorded telemetry will.
- **Command:** `uv run --directory .. python -m tests.budget_telemetry`
- **Registered in:** `orchestrator/budgets.yaml` of `github.com/nickderobertis/ai-orchestrator`, an entry this task adds

### Size of the plan-level review prompt

- **Id:** `plan-level-prompt-chars`
- **Measure:** Characters of the plan-level prompt `just review-plan` composes, which must stay well inside the smallest context window a candidate of the plan-level role answers in. Its detail names the fixture the figure came from.
- **Target:** at most 200000 characters
- **Basis:** design
- **Workload:** A realistic 44-node, 52-budget plan: the larger of the baseline-shaped and host-shaped fixtures in tests/test_plan_review.py.
- **Evidence:** Inherited: the existing approved budget, unchanged in id, unit, direction and threshold; its threshold is a design ceiling, not an observed figure. Only its command moves, from re-running the tests to analysing their recorded telemetry.
- **Command:** `uv run --directory .. python -m tests.budget_telemetry`
- **Registered in:** `orchestrator/budgets.yaml` of `github.com/nickderobertis/ai-orchestrator`, an entry this task changes

### Review prompt characters each node adds

- **Id:** `plan-level-prompt-chars-per-node`
- **Measure:** Characters the costliest node of either realistic fixture adds to the plan-level prompt, so a plan's prompt grows with its node count and never with its task bodies' length. Its detail names the node and fixture.
- **Target:** at most 1600 characters
- **Basis:** design
- **Workload:** The same 44-node, 52-budget fixtures in tests/test_plan_review.py.
- **Evidence:** Inherited: the existing approved budget, unchanged in id, unit, direction and threshold; its threshold is a design ceiling, not an observed figure. Only its command moves, from re-running the tests to analysing their recorded telemetry.
- **Command:** `uv run --directory .. python -m tests.budget_telemetry`
- **Registered in:** `orchestrator/budgets.yaml` of `github.com/nickderobertis/ai-orchestrator`, an entry this task changes

## Acceptance criteria

- `orchestrator/budgets.yaml` registers exactly `follow-up-run-points` (points, max 160), `ticket-lifecycle-points` (points, max 23), `lost-turn-surface-chars` (characters, max 400), `plan-level-prompt-chars` (characters, max 200000) and `plan-level-prompt-chars-per-node` (characters, max 1600). A new `tests/unfinished/budgets.yaml` registers `own-sessions-read-seconds` (seconds, max 10), labelled `host`. No other budget is added in any budgets file, and no `tests/plan_tooling/budgets.yaml` exists. `onebudgetspec validate --recursive .` passes.
- Each budget holds at its realistic workload, reported at or under its threshold when the finished tree is run through Nx. `follow-up-run-points`: one follow-up run of 16 tickets (12 new, 4 bound and re-copied), 4 evidence comments with re-estimates and one board check, on a `followups` stand-in holding 400 other items. `ticket-lifecycle-points`: one ticket filed, re-copied, answered once and withdrawn on a stand-in holding 60 other items. `own-sessions-read-seconds`: three identities with twenty closed sessions each and ten preserved branches. `lost-turn-surface-chars`: the largest surface across every cause, identity and run-id case of both lost-turn journeys. The plan-level prompt budgets: their 44-node, 52-budget fixtures.
- Every budget's command runs one generic analysis that selects the budget by `ONEBUDGETSPEC_BUDGET_ID`, reads telemetry the gate's tests recorded during their normal run, and reports through `onebudgetspec_sdk.report` with a `detail`. No command reads a `budgets.yaml`, compares with a threshold, or re-runs a scenario only to measure, except `own-sessions-read-seconds` where the completion report names the standalone-measurement exception it uses. Each touched project's `budgets` target depends on the test target that records its telemetry. Each description is one or two sentences on what is measured and what it protects.
- Each budget's `detail` carries the breakdown stated in What. `follow-up-run-points` gives points per phase, points and requests per document, and the per-step journey's points and requests per step, labelled as that journey's. `ticket-lifecycle-points` gives points and requests per step. `lost-turn-surface-chars` gives each case's size and names the largest. Every figure the draft made a budget appears in one of these breakdowns, except the launch figures of `tests/plan_tooling/test_follow_ups_recipe_e2e.py`. That journey still records those as telemetry, and the completion report names them as the one exception.
- A missing, empty or unreadable telemetry file makes a budget's command fail through onebudgetspec, naming the budget; no budget reports a default or passing value in its place. The test target that records a deterministic budget's telemetry declares it as a cached output, so a cached test result restores exactly the telemetry its dependent budget reads. `own-sessions-read-seconds` never reports a wall-clock figure restored from a cache. A budgets run never reads telemetry left from an earlier tree.
- `config/onebudgetspec.version` names the onebudgetspec version that this task's references block names for `obs-text-detail`. The lockfile pins `onebudgetspec-sdk` and `onebudgetspec-cli` at that same version as dev dependencies. The tests that read that pin, `tests/test_linked_libraries.py` among them, pass on the finished tree. The merge path's install from the pin later compares it with the registry, and that comparison's result is not this node's bar.
- A test in this repository's suite proves budget enforcement end to end with the real just, Nx and onebudgetspec. It uses a fixture workspace that takes the `budgets` target default as `nx.json` ships it, and a fixture test target that records parts through this node's recorder and is read by `tests.budget_telemetry`. It shows four things. (a) With the threshold set below the recorded figure, the functional test target passes while the `budgets` target fails, and its output names the budget, its actual and threshold, and the breakdown lines from `detail`. (b) With the threshold restored, both targets pass. (c) With the telemetry absent, the `budgets` target fails. (d) On a second run with the test target served from cache, the restored telemetry is what the budget reads.
- The completion report shows the same over-threshold-then-restored sequence on `follow-up-run-points`, with the failing output's breakdown lines quoted. No lowered threshold is committed.
- No test under `tests/` asserts a cost figure (time, requests, queries, points, bytes, characters, memory or money held to a tolerance at a realistic workload) against a fixed threshold, or against one read from a `budgets.yaml`. The retired constants are gone from test code. Time-feature contracts, exact counts, absent operations, product-enforced limits (`SUMMARY_LIMIT`, `BODY_LIMIT`, `WARN_FROM`) and hang guards keep their assertions.
- The completion report lists every measurable non-functional cost the audit found under `tests/`, each with its file and line and one outcome: the budget it is, the budget whose breakdown carries it, rewritten to wait on an event, or kept as a test assertion with its reason. Every departure from this task's inventory carries a manager ruling the report names, and the finished inventory conforms to those rulings. Every claim in the report is true of the tree as it finally stands.
- `llmlint.yml` lists the onebudgetspec fragment's URL at `@1` exactly once. llmlint's resolved configuration includes `tests_hold_no_nonfunctional_thresholds` and `budgets_track_product_owner_outcomes`, as `cr-budget-rule` landed them. A judged llmlint run of those two rules over every file under `tests/` and every `budgets.yaml` their `files` globs select finds no violation, with no suppression added.
- `AGENTS.md` carries one terse line stating that a cost figure in this repository's tests is a budget at the level the product owner tracks, with finer figures as its recorded breakdown, and that a wall-clock bound never tells two behaviours apart.
- On the finished tree, every test module this node changed passes in full, and the diff-scoped judged lint over the branch is green.

## Additional info

The user's and manager's rulings this node applies are fixed and are not reopened:
- the three-case line;
- the level rule, which supersedes the earlier ruling that made requests and points
  separate budgets (requests are now telemetry);
- the six budgets at the thresholds above, with `ticket-lifecycle-points` at 23 (the
  measured figure, not the 34 the old per-step ceilings sum to);
- the launch figures as the one telemetry exception;
- the deterministic budgets cached and the wall-clock one labelled `host`;
- the SDK at the onebudgetspec pin, which moves to the release carrying
  `obs-text-detail`.
The figures were measured on onetaskgraph 0.3.6 with the real journeys: the realistic
run at 160 (searches 24, write path 116, board check 20, with requests equal to points
for every document), and one ticket's lifecycle at 23 (filing 8, re-copy 4, answer 7,
withdrawal 4). If a figure, id or file looks wrong once you read the code, surface it
through `$ORCHESTRATOR_ASK_MANAGER` with your evidence rather than changing it.

Contracts this node builds against. A shared interface is never changed unilaterally:
if you want a departure, ask the manager and keep building against the agreed surface
until it rules.
- `obs-text-detail`: `onebudgetspec check --output text` prints each result line
  unchanged, followed by every line of a non-empty `detail`, each prefixed with two
  spaces. A result with no detail prints as before.
- `cr-budget-rule`: the fragment URL above, the rule names
  `tests_hold_no_nonfunctional_thresholds` and `budgets_track_product_owner_outcomes`,
  and `version: 1.1.0`.
- Node `aio-obs-convention-r3` of run
  `A-onebudgetspec-convention--upstreamed--encoded-in-create-repo--and-applied-to-its-consumers`
  renamed the project budgets target `budgets` and keyed its cache by measure kind.
  Build on its wiring rather than redoing it.
- `aio-timing-events` already rewrote the wall-clock discriminators. Leave its tests as
  they landed.

Follow this repository's realism rule: the real `just`, Nx, onebudgetspec and journeys,
with nothing doubled above the boundaries the suite already doubles.

The end-to-end budget proof follows the pattern of `tests/e2e/test_budget_target_e2e.py`:
- a fixture workspace taking the target defaults as `nx.json` ships them;
- a fixture test target recording telemetry with this node's recorder, read by
  `tests.budget_telemetry`;
- the real `just`, Nx and onebudgetspec.
To show it on a real project, lower one budget's threshold locally below its actual, run
that project's test and `budgets` targets, then restore the threshold and run them again.
Never commit the lowered threshold.

Checks that exercise this change, each fine to run separately:
- the test modules you change;
- each touched project's `budgets` target through Nx;
- `uv run onebudgetspec validate --recursive .`;
- `just lint-llm <paths>` for a judged run over named files;
- `just lint-llm-diff <base>` over the branch.
When llmlint's plugin cache still holds the 1.0.x fragment, refresh it with
`LLMLINT_PLUGIN_REFRESH=1`. The judged run over the relevant test tree is large, so
`just lint-llm <paths>` takes the paths the rules' `files` select.


## Additional info

### Operational notes for this host

**Run only the checks that exercise what you changed.** The tests that cover the code
you touched, the lint that reads your diff — and nothing wider. The repository's own
automation runs the full bar downstream on the change this work becomes: a `pre-push`
hook where that repository publishes locally, the host's required checks where it
publishes remotely. Neither is waiting for you to have run it first, so running it here
spends twenty to forty minutes of this dispatch learning what the merge path reports
anyway. That is measured rather than estimated: one node in this workstream spent about
84 minutes on three rounds of everything its repository could run, to reach lint findings
a diff-scoped run reports in two.

**Pick those checks from your change, and take the narrowest scope each one supports.**
One test module over the whole suite; the lint over your diff over the lint over the
tree. A repository's own fast deterministic tier is a fine choice when it is what
exercises your change — its `just --list` says which recipe that is — and a poor one when
you reached for it because it was the widest thing available. When a check you ran
reports something, fix that and run **that check** again; nothing here asks you to re-run
its neighbours to confirm, and a wider tier cannot re-check a finding it never made.

**A dispatch that changed nothing tracked owes none of them.** When your deliverable is a
document, a report, or an answer, there is no changed code for those checks to exercise
and you are complete without them — decide that from what the repository shows, never
because running them is slow or inconvenient. `git status` reporting nothing to commit is
then a correct and complete outcome, not a problem to solve by writing a file nobody
asked for.

**A publication check runs downstream, so no worker can run it and none is judged on it.**
The host's required checks and a repository's `pre-push` gate run on the change this
work becomes, after this dispatch settles: downstream publication checks are not
runnable by a worker, and a dispatch is never held to one having passed. That holds on a
retry too. A `checks-failed` retry — a re-dispatch onto the same branch carrying the
merge path's refusal and `onevcs`'s evidence — is judged on its task's stated local
acceptance checks: the repair of what the refusal named, proven by the checks the task
names, and never by the downstream check passing again, which can only happen once the
retry has settled. One retry that fixed and locally verified the failing test its
refusal named was failed for not re-running the required check, which left finished
work unpublished and its dependents skipped until a manager published it by hand.

**Never signal a process you did not identify by PID, and a PID you got from a pattern is
still a pattern kill.** Several managers share this host, and their dispatches, drivers,
publications and test servers run under the same few binary names — `just`, `node`,
`python`, `onepipeline-api` — so a kill by name knows nothing about *whose* work it
matched. The distinction is the tool rather than the pattern: `pgrep` and `ps` **read**,
and either spelling of them is fine, `pgrep -x` included; `pkill` **signals**, and it is
never the answer here, in any spelling. `-x` is the trap, because it reads as the careful
one — `pkill -f 'just gate'` at least needs a narrowing pattern, while `pkill -x just`
takes every `just` on the machine. Reading the table first and piping the pids into `kill`
is no way around it: that is the same pattern kill with more steps.

Two workers reached that place from different directions on 2026-08-24, while another
manager's dispatch had been live over an hour. One ran `pkill -TERM -x just`. The other
ran `ps -eo pid,args | grep 'onepipeline-api serve' | awk '{print $1}' | while read pid;
do kill "$pid"; done` — which obeys the letter of every paragraph below and does the same
damage, because `just dag-ui` and the e2e suites both spawn that server and a pytest run's
were alive at that moment.

**The one process you may signal is one you started yourself, so capture its PID as you
start it:**

    cmd & MYPID=$!
    kill "$MYPID"

Anything you did not start belongs to somebody. Read what it is, report what you found,
and leave it running.

**A wait written against a full command line never ends, because one of those command
lines is yours.** That is a different defect from the one above — it wedges your own
dispatch rather than somebody else's — and it is why `pgrep -f` cannot be waited on.
`pgrep -f` matches its pattern against every process's whole argv and excludes exactly one
process — itself — never the shell that invoked it. So any pattern naming the thing you are
waiting for is, by construction, a substring of the command line of the shell doing the
waiting: `until ! pgrep -f "just gate"` matches its own loop and never exits, and so does
the same loop written against `llmlint-judge.sh`, `scripts/fetch-corpus.sh`, or
`publish-branch`. That is a defect in the polling mechanism rather than a fact about gates,
so it applies to every wait you write.

That has wedged four workers here, and then two more workers and the manager itself in one
night: seven instances of one defect. A crozier corpus audit waited in `until ! pgrep -f
'scripts/fetch-corpus.sh'` with no fetch running at all — the only two matches were its own
polling shell and the manager's diagnostic shell — and waited until it was interrupted. A
`check-plan-bar-conflict` dispatch waited on `! pgrep -f "llmlint-judge.sh"`, whose negation
could never become true, and was cancelled and killed; its replacement then reached for
`pkill -f` against three patterns, which is not a wait at all but the same self-match with a
signal attached, and is refused above. And the manager, an hour after instructing a worker
about this, guarded a launch with `pgrep -f "publish-branch" && echo ABORT || launch`, whose
pattern was a literal substring of the launching shell's own command line: it aborted
unconditionally and then reported a publish as RUNNING that did not exist. That last one is
the evidence that matters most — a rule its own author reproduces an hour after issuing it
is a rule whose wording is the problem rather than its reader.

**Wait with a sentinel file.** Background the whole command you are waiting on — one
command, so that what you waited on is what you meant to run — and poll for a file only
that invocation can write:

    ( just test > /tmp/check.$$.log 2>&1; echo $? > /tmp/check.$$.exit ) &
    until [ -f /tmp/check.$$.exit ]; do sleep 20; done

**A process must finish inside the turn that started it.** Backgrounding is how you wait
on a slow command *within* one turn, which is why the launch and the wait sit together in
that example — both halves belong to the same turn. What does not survive is a background
process left running when the turn ends: nothing carries it across, and the launch gives
you no sign of that, because it succeeded and the turn then ended normally. The loss
shows up a turn later as a sentinel that never appeared and a recipe terminated by
signal, with the whole run to pay for again. It bites hardest where it is most tempting,
because a worker reaches for backgrounding exactly when a command is slow enough to
threaten the turn — here the judged lint tier and a whole test tier, which are the two
runs a node most needs to have made. So run a slow check in the foreground, or background
it and wait out its sentinel before your turn ends; never end a turn intending to read a
result on the next one.

**To ask whether a process is running at all, match the executable, not the command line.**
`pgrep -x onepipeline` matches process *names* exactly — the binary, not its arguments — so
the shell doing the asking, whose own name is `bash`, cannot match it however the pattern
is spelled. Where the binary name is not distinctive enough to identify what you mean
(`just`, `node` and `python` run everything), read the process table instead and drop the
one self-match that form has:

    ps -eo pid,args | grep -v grep | grep 'lint-llm-diff'

Both of those are **reads**, which is what makes `-x` safe here and forbidden the moment
the tool signals. The replacement for the worker wedged above reached for `pkill -f "just
gate"`, `pkill -f "just check"` and `pkill -f "nx run"` while another manager's run was
live. Read to learn what is running, report what you found, and signal nothing you did not
start.

**One sentinel and one log per invocation, and look before you launch one.** That pair is a
shape rather than two literal paths. Several dispatches run on this host at once and they
share `/tmp`, so a second worker writing `/tmp/check.log` writes into the first one's — and
`until [ -f /tmp/check.exit ]` returns immediately on somebody else's exit file, reporting
their result as yours. Name both after the invocation (`/tmp/check.$$.log`, or the node
id), and, before starting it, check whether its sentinel path is already in use by another
invocation; if so, choose a different invocation-specific pair. This path-ownership check
does not prohibit concurrent checks or judged tiers. Three workers collided on shared names
today and not one of the three failures read as what it was: two
deterministic-tier runs deadlocked against the same cargo target-directory lock, so both
logs sat unchanged and neither progressed — about fifteen minutes lost and three
interruptions to a live turn; two whole-e2e runs wrote to one log, and stopping the
duplicate left four `SIGTERM` entries in it that read exactly like real test failures; two
judged-tier runs raced and wasted a roll, which was harmless only by luck. Read a log that
has stopped growing as two commands blocking each other before you read it as one command
working.

<!-- llmlint: ignore[contracts_have_one_source_or_a_drift_gate] The source of this contract is the engine that exports the variable, and it lands in the same run as this paragraph: there is no released copy to reconcile against yet, and this task is explicitly told not to go looking for one in an installed engine. `tests/test_dispatch_appendix.py` holds these four properties and no more, so the day the engine side is adopted the drift gate has an exact set to reconcile. -->
**Write the files this dispatch needs under `ONEPIPELINE_NODE_SCRATCH_DIR`, rather than
under a `/tmp` path you invented.** It is an absolute path to a directory that exists and
is writable when the dispatch starts, is unique to this dispatch, and is not removed while
it runs.

**Root-owned files after a container run.** The pre-push visual guard captures in Docker
as root and can leave `.nx/` and `dist/` unwritable, failing the *next* check with `NX
Permission denied (os error 13)`. There is no passwordless sudo; repair from inside a
container:

    docker run --rm -v "$PWD":/w -w /w mcr.microsoft.com/playwright:v1.61.1-noble \
      chown -R "$(id -u):$(id -g)" /w

**If a test fails in a way that makes no sense, check the host before the test.** On
2026-08-19 two publication attempts against the same already-green branch failed on two
unrelated tests — a 5-second timeout, then a browser remote that never came up — and both
were written up as flakes. The host was at 197G/197G, and the next attempt said so
outright with `No space left on device`. A full disk does not announce itself to a test
runner; it announces itself as an unrelated test failing. One command rules it out:

    df -h /home .

**If a rule looks wrong or misapplied, say so with evidence rather than editing it.**

**One suppression policy, and this is the whole of it.** A site-scoped `ignore` directive
is permitted where the rule is genuinely misapplied at that site **and** the directive
carries a substantive reason saying why. What is forbidden is silencing a finding you
have not answered so that a count moves. What you owe an account of is **every
suppression your own diff writes — added, moved or rewritten — and every one already
standing over a line that diff changes** — each with its site and its reason, in your
completion report, so the manager reads what you left rather than discovering it. Both
halves are decided from the diff alone: a directive line inside it, or a changed line
inside the region a directive covers. A file-scoped directive is outside both unless your
diff writes the directive itself — its region is every line of the file, so counting it
would make this file's own header a debt of every change that edits a word of it.
Suppressions elsewhere in the tree are not yours to inventory: this repository carries several hundred standing directives, and
a list of them would be longer than the report is read under while saying nothing about
the change under review.

That scope is stated because the sentence it replaces read two ways at once: its first
clause reached every directive anywhere in the repository, while its second — *so the
manager reads what you left* — was about this dispatch's own work, in the same breath. A
node was failed on the wider reading for not accounting for suppressions it had never
touched, on a tree whose every target had just run from cold and come back green; it cost
a third dispatch of a run's final node. The reading above is the one the purpose clause
and the scale both support, and it is written so that a worker complying with it and a
judge reading it adversarially land in the same place. It says which lines rather than which sites for the same
reason: *site* was itself readable two ways, and the first worker to apply the sentence
could not decide whether the file-scoped directives at the head of `AGENTS.md` were
owed by a change that edits one of its paragraphs. Apply that same test to whatever
replaces it: read it as a worker trying to comply and as a judge looking for a way to
refuse, and if the two can land in different places it is not one source yet.

`AGENTS.md` states no suppression policy of its own and points here, because this file is
what your judge reads beside your task: while that document said it twice, one
dispatch added four site-scoped ignores each carrying a substantive reason and was failed
for *"the task's categorical instruction never to suppress a rule"* — work that was
correct under one of this repository's own readings and refused under the other.

**`--no-verify` is not an acceptable response to a slow or inconvenient hook.** The hooks
are this repository's enforcement point — there is no CI — so bypassing one commits work
that nothing has checked, and nothing you run afterwards catches it: a check runs over
the finished tree, by which point a bypassed commit is simply history. Two workers
today reached for `git commit --no-verify`, both on a commit they had judged mechanical,
and one of the two was a *merge* commit — a conflict resolution, which is the single most
likely commit in a dispatch to be wrong. No check caught either one; the monitor caught
both. If a hook is slow, wait for it. If it refuses, it is telling you something about the
commit in front of you, and the answer is to fix the commit.

**Commit a coherent working piece the moment it works.** A dirty worktree does not
survive this dispatch: what is uncommitted when your last turn ends is what nothing
recovers — not a retry, not a recovery verb, not a manager reading the branch. So each
piece is its own commit as you finish it, rather than one commit held back to the end.
*The moment it works* is the moment you have seen it work: the checks that piece needs
are run before it is committed rather than after, so that each commit is one you have
evidence for.

**Ask rather than stop.** When you cannot proceed without a decision that is not yours —
a frozen contract you would have to amend, a goal that reads two ways, a constraint you
would have to relax — put the question to your manager over `$ORCHESTRATOR_ASK_MANAGER`,
which every dispatch carries, and go on working on whatever does not depend on the answer.
Ending your turns with the work undone and the question unasked is the one response
nothing can recover: a worker that reasoned about a frozen contract correctly, declined to
amend it unilaterally, named two options for its owner, and then stopped for three turns
lost about fifteen minutes of correct work and settled reporting `ahead of main: 0
commit(s)`.

**Draft a follow-up that can wait; never draft one that cannot.** Work you notice that
is outside your subtask — a bug beside the code you touched, a missing test or script, a
stale document, an improvement to this harness or to the context agents are given — is a
follow-up, and doing it is not yours. Record each one the moment you notice it by piping
its body into `"$ORCHESTRATOR_FOLLOW_UP_DRAFT"`, whose `--help` names the flags and the four
headings that body carries; the command stamps where it came from. Do not collect them into
a list at the end of your last message instead, because nothing reads that list. A draft is
unverified — a follow-up agent checks every one after the run — and it is not your
completion report, which still says what you did. Drafting is only for what can wait:
anything blocking, whether a decision fork, a constraint you cannot meet, or something your
manager should act on now, goes over `$ORCHESTRATOR_ASK_MANAGER` immediately, as the
paragraph above says, and is never drafted in its place.

**A GitHub rate-limit refusal that `gh api rate_limit` disagrees with is the secondary
limiter.** The primary limit is the one that endpoint reports and the one a wait answers.
The secondary limiter is reported by nothing, polled by nothing, and not waited out by
polling — every further attempt extends it. So when a `gh` call is refused for a rate
limit while `gh api rate_limit` still shows budget, stop making that call: deliver the
result another way, name in your report what was refused and what you tried, and leave the
retry to a person.

**Publication goes through the harness.** The session branch reaches its remote only
through `onevcs publish "$ONEVCS_SESSION"`, and its change request is lifted, landed or
kept for the user's review by the lifecycle after you settle. No `git push` of the session branch, no `gh pr create`
for it, no `gh pr ready`, no `gh pr merge`. Finish the branch, commit everything, leave
the tree clean, and report. Landing it is explicitly **not** yours to perform and **not**
part of your acceptance criteria. Two things are yours, and each only when this task's
own `## Additional info` — its own words above these notes — says so:

- **Only when the task's `## Additional info` says the change request may be published
  early**: `onevcs publish "$ONEVCS_SESSION" --draft [--title T] [--body-file PATH]` opens
  the session's change request as a draft, and a later `publish --draft` pushes new
  commits onto that same change request and never opens a second. Commit everything
  first — a dirty tree is committed for you, under a provenance you did not choose.
  `onevcs change describe "$ONEVCS_SESSION" --body-file PATH` replaces its description
  and `onevcs change show "$ONEVCS_SESSION"` reads it back. The description you leave is
  what the drafter finishes from, so start it with what only you know — the evidence and
  where it is. Never mark the draft ready yourself; after you finish, the lifecycle lifts
  it or keeps it as a draft for the user's review according to the repository's policy.
  `ONEVCS_SESSION` is in every lifecycle dispatch's environment.
- **Only when the task's `## Additional info` authorizes a throwaway demonstration change
  request**: after the draft is open, cut a branch from the session branch, commit the
  demonstration on it, push it with `git push -u origin <branch>`, open it with
  `gh pr create --draft --base <session branch> --head <branch> --title "DO NOT MERGE: …"`,
  capture the evidence — `gh pr view <n> --comments`, `gh pr checks <n>`, and `gh api`
  **reads** of that change request's own comments, checks and reviews, never a write to
  anything — then close it with `gh pr close <n> --delete-branch`, return to the session
  branch, and delete the local branch — a local branch left behind makes the session's
  close refuse. Its base is always the session branch, never the repository's base; it
  is never marked ready; it is closed before you finish.

Everything else a push or a change-request verb could do is still not yours.

**A branch you resume may already be on its remote, and a published branch only grows.**
A retry — a `checks-failed` one among them — re-dispatches onto the same branch, which an
earlier publication may already have pushed. So check before you touch its history: `git
fetch` and `git log origin/<branch>`, or the published commit your task's context names.
The repair of a branch already on its remote is new commits on top of the remote's
commit — never an amend, a rebase, a squash, a reset or a force-push of commits already
there. One retry that fixed both refusals its merge path named by amending the two commits
already pushed was refused `non-fast-forward` when it published, and settled
`push-rejected` with the correct tree stranded on the host.

**Every claim you make about the finished work is true of the tree as it finally
stands.** That is the property you are held to, and it says nothing about where your
report sits. This conversation does not end when you report — your supervisor keeps
asking, and answering well means running things — so a report required to be this
dispatch's literal final output is one no correct worker can give. What is required
instead is that nothing you have said about the work is untrue of the tree by the time you
stop.

When work follows your report, say in that same turn what changed and what you re-ran. A
correct delta satisfies this exactly as fully as restating the whole report does. Silence
does not satisfy it at all. Your last substantive turn should leave a reader able to say
what the finished tree contains and what was verified about it, whether that comes from
one report or from a report plus the deltas after it.

**A claim about a check, a test, a lint run, or a commit that was not run or was not made
is false, and fails on its merits.** A report asserting a check passed on a commit where
no such run occurred is that case, and one node of this host's history was correctly
failed for it. So do not carry a claim forward across a change that could have invalidated
it — re-run what the change could have broken, or say which claims you have not re-checked.

**Evidence deliberately about an earlier or induced state is not a false claim.** Proving
that an assertion can fail means making it fail, reading the message, and reverting;
proving that a message used to say nothing means quoting the run from before the change.
A citation of a run taken before a change, or of a failure induced on purpose as evidence,
is correct evidence — provided the citation says which it is. One node of this host was
failed for exactly that: its own criteria required its assertions be observed failing, and
the resulting-tree property above was read as forbidding the citation that requirement
produces.

**A commit that cannot affect a check leaves that check's evidence standing.** *Could have
invalidated it* is a condition, and a commit that fails it does not reach the claim — so a
run taken before a later commit is still evidence for the tree after it, provided the
report names that commit and says why it is inert for that check. What decides it is what
the check reads, never what kind of change the commit is. A comment-only or
documentation-only commit to content a check does not read is inert for that check; the
same comment is not inert for a check that reads comments, which a judged lint does, so
that one is re-run or named as not re-checked. A commit touching anything a check reads is
not inert for it, however small. Two dispatches of one node, about 45 minutes each, were
refused over nothing but a comment-only final commit after their cited check runs, with no
change in whether either tree was correct, and the manager had to send *re-run after every
commit* three separate times to get past it: read as absolute, the conditional rule above
is one no worker can meet, because every commit comes after the last run.

The ordering demand this replaces — that the report come after everything else, and that
anything found later be repaired and the whole report written again — is **withdrawn**. It
failed six of fourteen nodes in one workstream, each with complete committed work, a green
deterministic tier, and no acceptance criterion found unmet, and two of those carried an
escalated warning about it in their own task and failed anyway. A bar finished work
cannot clear teaches everyone to route around the thing that enforces quality; the
property above is what that demand was serving, and it is what survives.

### State the bar in `## Acceptance criteria`, not only here

A judge reads this node's `## Acceptance criteria` and its own review bar. Where the
criteria are silent about something the bar demands, it imports the demand and applies its
own reading of it — and finished, gate-green work has been failed twice that way: once for
running the gate's parts separately, and once for never having *"provided a final verified
completion report"*, a demand in neither the task nor the shared completion clause.

So every demand this node will be held to is stated as a criterion of its own, including
the two the bar makes of every implementation dispatch:

- the behavior this node adds is **proven end to end** by a test or journey that drives the
  real interface, rather than by inspection;
- every claim the dispatch makes about the finished work — what it verified, and the
  evidence for it — is **true of the tree as it finally stands**.

Criteria state properties of the finished tree; the commands that produce them belong in
this section. Whether the criteria answer every demand their own bar — or this appendix —
makes of them is read by `just review-plan <source:project>`, which spends a judged turn
on it and judges it by meaning: criteria that state a demand in their own words answer it,
and no criterion is refused for failing to use a particular phrase. Nothing reads it
deterministically any more, because the phrase matching that did refused wordings the same
review had just asked for.


<!-- onetaskgraph:template-answers
acceptance_criteria:
- '`orchestrator/budgets.yaml` registers exactly `follow-up-run-points` (points, max 160), `ticket-lifecycle-points` (points, max 23), `lost-turn-surface-chars` (characters, max 400), `plan-level-prompt-chars` (characters, max 200000) and `plan-level-prompt-chars-per-node` (characters, max 1600). A new `tests/unfinished/budgets.yaml` registers `own-sessions-read-seconds` (seconds, max 10), labelled `host`. No other budget is added in any budgets file, and no `tests/plan_tooling/budgets.yaml` exists. `onebudgetspec validate --recursive .` passes.'
- 'Each budget holds at its realistic workload, reported at or under its threshold when the finished tree is run through Nx. `follow-up-run-points`: one follow-up run of 16 tickets (12 new, 4 bound and re-copied), 4 evidence comments with re-estimates and one board check, on a `followups` stand-in holding 400 other items. `ticket-lifecycle-points`: one ticket filed, re-copied, answered once and withdrawn on a stand-in holding 60 other items. `own-sessions-read-seconds`: three identities with twenty closed sessions each and ten preserved branches. `lost-turn-surface-chars`: the largest surface across every cause, identity and run-id case of both lost-turn journeys. The plan-level prompt budgets: their 44-node, 52-budget fixtures.'
- Every budget's command runs one generic analysis that selects the budget by `ONEBUDGETSPEC_BUDGET_ID`, reads telemetry the gate's tests recorded during their normal run, and reports through `onebudgetspec_sdk.report` with a `detail`. No command reads a `budgets.yaml`, compares with a threshold, or re-runs a scenario only to measure, except `own-sessions-read-seconds` where the completion report names the standalone-measurement exception it uses. Each touched project's `budgets` target depends on the test target that records its telemetry. Each description is one or two sentences on what is measured and what it protects.
- Each budget's `detail` carries the breakdown stated in What. `follow-up-run-points` gives points per phase, points and requests per document, and the per-step journey's points and requests per step, labelled as that journey's. `ticket-lifecycle-points` gives points and requests per step. `lost-turn-surface-chars` gives each case's size and names the largest. Every figure the draft made a budget appears in one of these breakdowns, except the launch figures of `tests/plan_tooling/test_follow_ups_recipe_e2e.py`. That journey still records those as telemetry, and the completion report names them as the one exception.
- A missing, empty or unreadable telemetry file makes a budget's command fail through onebudgetspec, naming the budget; no budget reports a default or passing value in its place. The test target that records a deterministic budget's telemetry declares it as a cached output, so a cached test result restores exactly the telemetry its dependent budget reads. `own-sessions-read-seconds` never reports a wall-clock figure restored from a cache. A budgets run never reads telemetry left from an earlier tree.
- '`config/onebudgetspec.version` names the onebudgetspec version that this task''s references block names for `obs-text-detail`. The lockfile pins `onebudgetspec-sdk` and `onebudgetspec-cli` at that same version as dev dependencies. The tests that read that pin, `tests/test_linked_libraries.py` among them, pass on the finished tree. The merge path''s install from the pin later compares it with the registry, and that comparison''s result is not this node''s bar.'
- A test in this repository's suite proves budget enforcement end to end with the real just, Nx and onebudgetspec. It uses a fixture workspace that takes the `budgets` target default as `nx.json` ships it, and a fixture test target that records parts through this node's recorder and is read by `tests.budget_telemetry`. It shows four things. (a) With the threshold set below the recorded figure, the functional test target passes while the `budgets` target fails, and its output names the budget, its actual and threshold, and the breakdown lines from `detail`. (b) With the threshold restored, both targets pass. (c) With the telemetry absent, the `budgets` target fails. (d) On a second run with the test target served from cache, the restored telemetry is what the budget reads.
- The completion report shows the same over-threshold-then-restored sequence on `follow-up-run-points`, with the failing output's breakdown lines quoted. No lowered threshold is committed.
- No test under `tests/` asserts a cost figure (time, requests, queries, points, bytes, characters, memory or money held to a tolerance at a realistic workload) against a fixed threshold, or against one read from a `budgets.yaml`. The retired constants are gone from test code. Time-feature contracts, exact counts, absent operations, product-enforced limits (`SUMMARY_LIMIT`, `BODY_LIMIT`, `WARN_FROM`) and hang guards keep their assertions.
- 'The completion report lists every measurable non-functional cost the audit found under `tests/`, each with its file and line and one outcome: the budget it is, the budget whose breakdown carries it, rewritten to wait on an event, or kept as a test assertion with its reason. Every departure from this task''s inventory carries a manager ruling the report names, and the finished inventory conforms to those rulings. Every claim in the report is true of the tree as it finally stands.'
- '`llmlint.yml` lists the onebudgetspec fragment''s URL at `@1` exactly once. llmlint''s resolved configuration includes `tests_hold_no_nonfunctional_thresholds` and `budgets_track_product_owner_outcomes`, as `cr-budget-rule` landed them. A judged llmlint run of those two rules over every file under `tests/` and every `budgets.yaml` their `files` globs select finds no violation, with no suppression added.'
- '`AGENTS.md` carries one terse line stating that a cost figure in this repository''s tests is a budget at the level the product owner tracks, with finer figures as its recorded breakdown, and that a wall-clock bound never tells two behaviours apart.'
- On the finished tree, every test module this node changed passes in full, and the diff-scoped judged lint over the branch is green.
additional_info: |
  The user's and manager's rulings this node applies are fixed and are not reopened:
  - the three-case line;
  - the level rule, which supersedes the earlier ruling that made requests and points
    separate budgets (requests are now telemetry);
  - the six budgets at the thresholds above, with `ticket-lifecycle-points` at 23 (the
    measured figure, not the 34 the old per-step ceilings sum to);
  - the launch figures as the one telemetry exception;
  - the deterministic budgets cached and the wall-clock one labelled `host`;
  - the SDK at the onebudgetspec pin, which moves to the release carrying
    `obs-text-detail`.
  The figures were measured on onetaskgraph 0.3.6 with the real journeys: the realistic
  run at 160 (searches 24, write path 116, board check 20, with requests equal to points
  for every document), and one ticket's lifecycle at 23 (filing 8, re-copy 4, answer 7,
  withdrawal 4). If a figure, id or file looks wrong once you read the code, surface it
  through `$ORCHESTRATOR_ASK_MANAGER` with your evidence rather than changing it.

  Contracts this node builds against. A shared interface is never changed unilaterally:
  if you want a departure, ask the manager and keep building against the agreed surface
  until it rules.
  - `obs-text-detail`: `onebudgetspec check --output text` prints each result line
    unchanged, followed by every line of a non-empty `detail`, each prefixed with two
    spaces. A result with no detail prints as before.
  - `cr-budget-rule`: the fragment URL above, the rule names
    `tests_hold_no_nonfunctional_thresholds` and `budgets_track_product_owner_outcomes`,
    and `version: 1.1.0`.
  - Node `aio-obs-convention-r3` of run
    `A-onebudgetspec-convention--upstreamed--encoded-in-create-repo--and-applied-to-its-consumers`
    renamed the project budgets target `budgets` and keyed its cache by measure kind.
    Build on its wiring rather than redoing it.
  - `aio-timing-events` already rewrote the wall-clock discriminators. Leave its tests as
    they landed.

  Follow this repository's realism rule: the real `just`, Nx, onebudgetspec and journeys,
  with nothing doubled above the boundaries the suite already doubles.

  The end-to-end budget proof follows the pattern of `tests/e2e/test_budget_target_e2e.py`:
  - a fixture workspace taking the target defaults as `nx.json` ships them;
  - a fixture test target recording telemetry with this node's recorder, read by
    `tests.budget_telemetry`;
  - the real `just`, Nx and onebudgetspec.
  To show it on a real project, lower one budget's threshold locally below its actual, run
  that project's test and `budgets` targets, then restore the threshold and run them again.
  Never commit the lowered threshold.

  Checks that exercise this change, each fine to run separately:
  - the test modules you change;
  - each touched project's `budgets` target through Nx;
  - `uv run onebudgetspec validate --recursive .`;
  - `just lint-llm <paths>` for a judged run over named files;
  - `just lint-llm-diff <base>` over the branch.
  When llmlint's plugin cache still holds the 1.0.x fragment, refresh it with
  `LLMLINT_PLUGIN_REFRESH=1`. The judged run over the relevant test tree is large, so
  `just lint-llm <paths>` takes the paths the rules' `files` select.
budgets:
- basis: measured
  command: uv run --directory .. python -m tests.budget_telemetry
  direction: max
  evidence: 'Measured 160 on onetaskgraph 0.3.6 by the realistic-run journey in tests/e2e/test_onetaskgraph_host_e2e.py: searches 24, write path 116, board check 20, with requests equal to points for every document. That is the figure the test enforces today, min(208, 160).'
  file: orchestrator/budgets.yaml
  file_change: add
  id: follow-up-run-points
  inner_measure_reason: The live board's shared allowance cannot be spent from the gate, so the run is priced on the loopback board stand-in with the drift-checked upstream prices.
  measure: Modelled GitHub GraphQL points one realistic follow-up run spends on the followups board, out of the board token's shared 5,000-point hourly allowance. Its detail breaks the figure down by phase (searches, write path, board check), by document, and by step, with request counts beside each.
  name: Board points a realistic follow-up run spends
  repository: github.com/nickderobertis/ai-orchestrator
  threshold: 160
  unit: points
  workload: 'One follow-up run: 16 tickets (12 new, each searched for by root cause and by text, then decided, validated and copied; 4 already bound, edited and re-copied), 4 evidence comments each with its re-estimate, and the launch''s one board check, on a followups stand-in holding 400 other items.'
- basis: measured
  command: uv run --directory .. python -m tests.budget_telemetry
  direction: max
  evidence: 'Measured on onetaskgraph 0.3.6 by test_one_tickets_filing_re_copy_withdrawal_and_comment_answer_stay_within_their_budget: filing 8, re-copy 4, answer 7, withdrawal 4. User ruling: 23, today''s measured figure, not the 34 that the per-step ceilings 10/7/9/8 sum to.'
  file: orchestrator/budgets.yaml
  file_change: add
  id: ticket-lifecycle-points
  inner_measure_reason: The live board's shared allowance cannot be spent from the gate, so the steps are priced on the loopback board stand-in with the drift-checked upstream prices.
  measure: 'Modelled GitHub GraphQL points one follow-up ticket''s life spends on the followups board: filing, re-copy after an edit, answering a person''s comment, and withdrawal. This covers the answer and withdrawal steps the realistic run does not drive. Its detail gives points and requests per step.'
  name: Board points one ticket's whole life spends
  repository: github.com/nickderobertis/ai-orchestrator
  threshold: 23
  unit: points
  workload: One ticket filed, re-copied after an edit, answered once in feedback mode, and withdrawn, on a followups stand-in holding 60 other items.
- basis: design
  command: uv run --directory ../.. python -m tests.budget_telemetry
  direction: max
  evidence: 'Inherited ceiling: OWN_READ_BOUND_SECONDS = 10.0 in tests/unfinished/test_unfinished_e2e.py bounds the same read today. The user approved keeping it; no measurement backs the figure, and the journey''s recorded telemetry will.'
  file: tests/unfinished/budgets.yaml
  file_change: add
  id: own-sessions-read-seconds
  inner_measure_reason: ''
  measure: Wall clock of `just unpublished --own --no-disk --json`, the read the Stop hook and a manager wait through before a turn may end. Its detail gives the scale it ran at.
  name: Time to list a manager's own unpublished sessions
  repository: github.com/nickderobertis/ai-orchestrator
  threshold: 10
  unit: seconds
  workload: Three onevcs identities with twenty closed sessions each and ten preserved branches, the scale registry tests/unfinished/test_unfinished_e2e.py builds.
- basis: design
  command: uv run --directory .. python -m tests.budget_telemetry
  direction: max
  evidence: 'Inherited ceiling: NAMED_FAILURE_LIMIT = 400, which both lost-turn journeys assert today. The user approved keeping it; no measurement backs the figure, and the journeys'' recorded telemetry will.'
  file: orchestrator/budgets.yaml
  file_change: add
  id: lost-turn-surface-chars
  inner_measure_reason: ''
  measure: Characters of the planner surface a lost monitor turn raises, which a manager reads on the channel. This is the largest across every cause, identity and run-id case, and its detail gives each case's size.
  name: Length of a lost monitor turn's surface
  repository: github.com/nickderobertis/ai-orchestrator
  threshold: 400
  unit: characters
  workload: Every cause, identity and run-id case the two lost-turn journeys drive, tests/e2e/test_monitor_quiet_turn_e2e.py and tests/e2e/test_lost_turn_wire_contract_e2e.py, taking the largest surface.
- basis: design
  command: uv run --directory .. python -m tests.budget_telemetry
  direction: max
  evidence: 'Inherited: the existing approved budget, unchanged in id, unit, direction and threshold; its threshold is a design ceiling, not an observed figure. Only its command moves, from re-running the tests to analysing their recorded telemetry.'
  file: orchestrator/budgets.yaml
  file_change: change
  id: plan-level-prompt-chars
  inner_measure_reason: ''
  measure: Characters of the plan-level prompt `just review-plan` composes, which must stay well inside the smallest context window a candidate of the plan-level role answers in. Its detail names the fixture the figure came from.
  name: Size of the plan-level review prompt
  repository: github.com/nickderobertis/ai-orchestrator
  threshold: 200000
  unit: characters
  workload: 'A realistic 44-node, 52-budget plan: the larger of the baseline-shaped and host-shaped fixtures in tests/test_plan_review.py.'
- basis: design
  command: uv run --directory .. python -m tests.budget_telemetry
  direction: max
  evidence: 'Inherited: the existing approved budget, unchanged in id, unit, direction and threshold; its threshold is a design ceiling, not an observed figure. Only its command moves, from re-running the tests to analysing their recorded telemetry.'
  file: orchestrator/budgets.yaml
  file_change: change
  id: plan-level-prompt-chars-per-node
  inner_measure_reason: ''
  measure: Characters the costliest node of either realistic fixture adds to the plan-level prompt, so a plan's prompt grows with its node count and never with its task bodies' length. Its detail names the node and fixture.
  name: Review prompt characters each node adds
  repository: github.com/nickderobertis/ai-orchestrator
  threshold: 1600
  unit: characters
  workload: The same 44-node, 52-budget fixtures in tests/test_plan_review.py.
spikes: []
what: |
  Move the cost figures this repository's tests assert into a short list of
  product-owner-level budgets. Keep every finer figure as recorded telemetry that the
  budget reports as its breakdown. Then put the two new onebudgetspec lint rules in force
  here.

  **The user's rules.**
  - **Three cases.** When a test's subject is how much a behaviour costs at a realistic
    workload, and the bound is a tolerance someone could raise or lower, that figure is a
    budget and the test never compares it with a threshold. Wall-clock discriminators are
    node `aio-timing-events`' work and have landed before this node. Time-feature
    contracts, exact counts, absent operations and product-enforced limits stay test
    assertions. Among those that stay are `LAUNCH_VALIDATION_BOARD_CHECKS == 1`,
    `_bounded_step`'s one-read-per-item, no-board-walk, no-search-for-a-bound-item and
    no-wide-page checks, `plan_review.SUMMARY_LIMIT`, and `task_body.BODY_LIMIT` and
    `WARN_FROM`.
  - **The level.** A budget is an outcome the product owner tracks. Per-step, per-phase
    and per-operation figures, and a second unit of the same concern (requests beside
    points), are telemetry. They are recorded on every run and reported as the breakdown
    of their budget's figure, so a failed budget says which part grew.
  - **The command.** A budget's command analyses telemetry the gate's tests already
    record, and reports through the onebudgetspec Python SDK. It never re-runs a scenario
    just to measure, except under the standalone-measurement exception the fragment's
    `budgets_reuse_gate_telemetry` states.

  **The budgets.** There are exactly the six in this task's `## Budgets` section. All are
  `measure: reported` and `direction: max`.
  - `orchestrator/budgets.yaml` gains `follow-up-run-points` (160 points),
    `ticket-lifecycle-points` (23 points) and `lost-turn-surface-chars` (400 characters).
    It keeps `plan-level-prompt-chars` (200000) and `plan-level-prompt-chars-per-node`
    (1600) at their id, unit, direction and threshold, changing only their commands.
  - A new `tests/unfinished/budgets.yaml` takes `own-sessions-read-seconds` (10 seconds),
    labelled `host`.
  - The deterministic budgets are cached. `own-sessions-read-seconds` never reports a
    wall-clock figure restored from a cache. It either reads a figure its test measured
    during the same budgets run, or it is a standalone measurement under the exception
    above. Say which, and why.

  **Each budget's breakdown is the telemetry that explains it**, reported as the SDK
  reporter's `detail`. Every line names a part and its figure.
  - `follow-up-run-points`: points per phase (searches, write path, board check); points
    and requests per document (`search`, `originLookup`, `createIssue` and the rest); and
    points and requests per step the per-step journey
    (`test_follow_up_commands_and_account_check_read_only_their_items_once`) records
    (unbound status, create copy, bound status, bound re-copy, re-estimate, evidence post,
    account board check). The per-step figures come from the 60-item journey, so the
    detail labels them as such and does not add them into the 160.
  - `ticket-lifecycle-points`: points and requests for each of filing, re-copy, answering
    a comment and withdrawal.
  - `lost-turn-surface-chars`: the size of each cause, identity and run-id case across
    `tests/e2e/test_monitor_quiet_turn_e2e.py` and
    `tests/e2e/test_lost_turn_wire_contract_e2e.py`, with the largest case named. The
    budget's figure is that largest case.
  - `own-sessions-read-seconds`: the scale it ran at (identities, closed sessions,
    preserved branches).
  - The two plan-level prompt budgets: the fixture each figure came from.

  **What stops being a budget.** The draft's 35 budgets become these six. Every figure
  below is still recorded as telemetry by the journey that measures it today, and is named
  in a breakdown above, with one exception.
  - `UNBOUND_STATUS_BUDGET`, `CREATE_COPY_BUDGET`, `BOUND_STATUS_BUDGET`,
    `BOUND_COPY_BUDGET`, `RE_ESTIMATE_BUDGET`, `EVIDENCE_POST_BUDGET` and
    `BOARD_CHECK_BUDGET` in the host journey, in requests and points: in
    `follow-up-run-points`' breakdown.
  - `REALISTIC_RUN_POINTS`, `REALISTIC_SEARCH_POINTS`, `REALISTIC_WRITE_POINTS`,
    `REALISTIC_BOARD_CHECK_POINTS` and the `REALISTIC_MEASURED_*` figures: replaced by
    `follow-up-run-points` and its phase breakdown.
  - `FILING_*`, `RECOPY_*`, `ANSWER_*` and `WITHDRAWAL_*` in requests and points: replaced
    by `ticket-lifecycle-points` and its step breakdown.
  - **The one exception:** `tests/plan_tooling/test_follow_ups_recipe_e2e.py`'s launch
    board-check figures (`BOARD_CHECK_BUDGET` through the recipe, and
    `LAUNCH_REFUSAL_BUDGET`). That journey still records them as telemetry, but no
    budget's breakdown carries them. Reading them would make the `orchestrator` project's
    budgets depend on the `plan_tooling` test target, coupling two projects' caches for a
    4-request figure. The launch's exact-count assertion stays a test, and no
    `tests/plan_tooling/budgets.yaml` is created.

  **Audit the whole test tree first.** The list above is the planner's inventory, not a
  complete one. Read every file under `tests/` for any measurable non-functional cost a
  test asserts or bounds: wall clock; request, query, call or API-points counts; bytes,
  characters or artifact sizes; memory; money, tokens or model turns. Classify each one:
  - **A cost figure** becomes telemetry in an existing budget's breakdown if it is a part
    or second unit of that budget's figure. Otherwise it is a candidate new budget.
  - **A wall-clock discriminator** that `aio-timing-events` missed is rewritten the same
    way, to wait on an event.
  - **A figure that stays an assertion**, with a one-line reason.
  A candidate new budget, or any figure you would fold into a breakdown that is not on the
  list above, is escalated before the inventory changes. Ask the manager through
  `$ORCHESTRATOR_ASK_MANAGER` with its file, line, value and what it measures.

  **How.**
  - Add `onebudgetspec-sdk` as a dev dependency at the same version as
    `config/onebudgetspec.version`.
  - Move `config/onebudgetspec.version` to the release carrying node `obs-text-detail`'s
    text output. That release also carries the SDK's `report`, which first shipped in
    0.1.3. The release this node waits for is stated in the references block the harness
    appends.
  - The existing journeys keep running as they do. Each also writes the parts it measures
    to temporary, uncommitted telemetry, declared as its test target's output.
  - One generic analysis, `python -m tests.budget_telemetry`, serves every budget. It
    chooses the budget by `ONEBUDGETSPEC_BUDGET_ID`, which onebudgetspec sets for every
    command. It sums or selects the budget's figure from the recorded parts, and calls
    `onebudgetspec_sdk.report(value, detail)` with the breakdown. It never reads a
    `budgets.yaml` and never compares with a threshold. No per-budget script.
  - Each project's `budgets` target depends on the test target that records its
    telemetry.
  - Each description is one or two sentences on what is measured and what it protects.
  - Remove each retired constant, and the assertion that held it, from test code.
    `RequestBudget` stops being a threshold carrier. `_bounded_step` and `_step_cost`
    record figures and keep only their structural checks. Keep or retire
    `BUDGET_MEASURED_ON` on its merits, and say which and why.
  - Do not edit `docs/budgets.md`, `personas/planner.yaml` or the budgets templates. Node
    `aio-budget-convention` owns them.

  **The rules in force here.** After `aio-obs-convention-r3`, `llmlint.yml` lists
  `https://raw.githubusercontent.com/nickderobertis/dero-skills/main/skills/bootstrap/create-repo/assets/llmlint/tools/onebudgetspec.llmlint.yml@1`.
  Keep that `@1` entry, adding it if it is missing. Make sure the resolved plugin is the
  `1.1.0` release that node `cr-budget-rule` landed, carrying
  `tests_hold_no_nonfunctional_thresholds` and `budgets_track_product_owner_outcomes`.
  Then add one terse line to `AGENTS.md`'s "Tests are context engineering" section: a
  cost figure in this repository's tests is a budget at the product-owner level, with
  finer figures as its recorded breakdown, and a wall-clock bound never tells two
  behaviours apart.
why: |
  The user wants every non-functional requirement that carries a number to be something
  they can audit, track and adjust in one place, a `budgets.yaml`. They also want that place
  to read as a short list of what the product owner tracks: the board quota a realistic
  follow-up run spends, what one ticket's life costs, how long a read someone waits through
  takes, and how much text reaches a reader. Dozens of step-level numbers would make it
  hard to audit and adjust. The detail must not be lost, because a failed budget has to
  point at what to fix, so every finer figure stays as recorded telemetry, shown as its
  budget's breakdown. Today about thirty quota and size ceilings sit as constants in
  `tests/github_board.py` and its journeys, unseen until one fails or is quietly loosened.
  This repository is the canonical example of how these libraries are used together, so it
  follows the rule it hands every new repository. Every total sits at today's measured
  figure, so approving this plan approves no looser bar than today's.
-->
