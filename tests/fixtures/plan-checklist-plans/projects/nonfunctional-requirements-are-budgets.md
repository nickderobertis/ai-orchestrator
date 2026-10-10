---
title: Every repository holds its measurable non-functional requirements as budgets, never as assertions in test code
status: todo
metadata:
  "onepipeline.goal": {"text":"Make onebudgetspec budgets a baseline requirement of every repository dero-skills' create-repo builds, rather than an opt-in. State in create-repo when a requirement is a budget, and at what level: budgets track what the product owner tracks, finer figures are telemetry reported as the budget's breakdown, and a budget's command analyses recorded telemetry through the onebudgetspec SDK. Enforce it with judged rules in the separate onebudgetspec llmlint fragment, and state it in ai-orchestrator's planning convention. The rule sorts each timing or size check three ways: a cost figure at a realistic workload goes in a budgets.yaml and is never asserted in a test; a wall-clock bound used to tell two behaviours apart is rewritten to wait on an event; a test of a time feature keeps its assertion. Bring ai-orchestrator under the same rule with six product-owner-level budgets at today's measured figures, every finer figure reported in a budget's breakdown, and have onebudgetspec print that breakdown in its text output."}
  onepipeline.schema_version: 3
  onetaskgraph.copies:
    plans: plans:I_kwDOTXCWqs8AAAABVmqBUQ
  onetaskgraph.template:
    answers_digest: sha256:76bb28c6f40d3137652f1d30662e3a3124e6bf0828d6a9a82d14ffd1c073835c
    body_digest: sha256:a37e4c746f3e5f10a6d19b476d8012cf64cc18e21d3a9f41271be6cd1d934c2b
    digest: sha256:20634261d034e324c57eb40f6f07e9f4346638500fa74e1e07f84a7ae6b61b6f
    template: onepipeline:plan-description
  orchestrator.plan-budgets:
    checklist:
    - budget: own-sessions-read-seconds
      concern: latency
      not_applicable: ''
      summary: ''
    - budget: follow-up-run-points
      concern: quota and rate-limit headroom
      not_applicable: ''
      summary: ''
    - budget: ticket-lifecycle-points
      concern: quota spent by the board steps the realistic run does not drive
      not_applicable: ''
      summary: ''
    - budget: follow-up-run-points
      concern: how the work scales with data
      not_applicable: ''
      summary: ''
    - budget: plan-level-prompt-chars
      concern: resource use
      not_applicable: ''
      summary: ''
    - budget: lost-turn-surface-chars
      concern: size of what reaches a reader
      not_applicable: ''
      summary: ''
    - budget: ''
      concern: spend
      not_applicable: n/a because no node adds a paid call on any runtime path. The judged cases cr-budget-rule adds to dero-skills' skilltest project run only when someone invokes `just skilltest`, never in the gate, at a few minutes of judge turns per run, which is what the existing onebudgetspec cases already cost.
      summary: No runtime path gains a paid call; the new judged cases run only on `just skilltest`, outside the gate.
    - budget: ''
      concern: gate time
      not_applicable: n/a because no new budget is needed. ai-orchestrator's repo-wide `gate-time` covers it. The budgets analyse telemetry the gate's own test runs already write, and two existing budgets stop re-running tests, so the change saves seconds. The event-driven rewrites can only shorten waits. onebudgetspec adds a few journeys. dero-skills gains one `budgets` recipe that measures nothing yet.
      summary: Budgets read telemetry the gate already writes, and two stop re-running tests; repo-wide gate-time covers the rest.
    - budget: ''
      concern: change cycle time
      not_applicable: n/a because no node adds a required check. aio-budgets waits once for onebudgetspec's automated release carrying obs-text-detail, which publishes on merge. The dero-skills and onebudgetspec nodes land under change-auto as before, and ai-orchestrator's `cycle-time` budget reports these landings like any other.
      summary: No new required check; one wait on an automated onebudgetspec release, reported by cycle-time like any landing.
    overview: |
      Execution plan nonfunctional-requirements-are-budgets.

      ## What this plan delivers

      Six lifecycle nodes, all `engineer`:

      ```
      cr-budget-rule         (dero-skills: the "when is a requirement a budget" reference section,
                              with the three cases and the level rule; AGENTS.md.template line;
                              rules tests_hold_no_nonfunctional_thresholds and
                              budgets_track_product_owner_outcomes in
                              tools/onebudgetspec.llmlint.yml at 1.1.0; judged cases)  [starts now]
      obs-text-detail        (onebudgetspec: `check` text output prints each result's detail
                              under its line)                                           [starts now]
      aio-timing-events      (ai-orchestrator: wall-clock discriminators rewritten to wait on events)
                              <- run:A-onebudgetspec-convention...#aio-obs-convention-r3

      cr-budgets-baseline    <- cr-budget-rule
                              (dero-skills: --tool removed, budgets wiring and checks for every repo,
                               dero-skills wired itself)
      aio-budget-convention  <- cr-budget-rule
                              (ai-orchestrator: docs/budgets.md, planner persona, budgets templates
                               and the plan review state the level and telemetry rules)
      aio-budgets            <- cr-budget-rule, aio-timing-events, obs-text-detail (its release),
                                run:...#aio-obs-convention-r3
                              (ai-orchestrator: six product-owner-level budgets analysing recorded
                               telemetry through the onebudgetspec SDK, every finer figure in a
                               budget's breakdown; both rules in force at @1; onebudgetspec pin moved)
      ```

      **The rule.** A number in a test falls into one of three cases. A cost figure becomes a
      budget. A wall-clock discriminator is rewritten to wait on an event. A time-feature
      contract stays an assertion. Budgets sit at the level the product owner tracks. Finer
      figures (per step, per phase, per operation, or a second unit of the same concern) are
      telemetry recorded on every run and reported as the budget's breakdown in `detail`, so a
      failed budget says which part grew. A budget's command analyses that telemetry through
      the onebudgetspec SDK.

      **Contracts.**
      - Between the dero-skills and ai-orchestrator nodes: the fragment URL
        `https://raw.githubusercontent.com/nickderobertis/dero-skills/main/skills/bootstrap/create-repo/assets/llmlint/tools/onebudgetspec.llmlint.yml@1`,
        the rule names `tests_hold_no_nonfunctional_thresholds` and
        `budgets_track_product_owner_outcomes`, and the fragment's `version: 1.1.0`, all fixed
        by `cr-budget-rule`.
      - Between onebudgetspec and ai-orchestrator: `check --output text` keeps every result
        line unchanged and follows it with each line of a non-empty `detail`, indented two
        spaces, as fixed by `obs-text-detail`.
      - aio-budgets adopts that release through `config/onebudgetspec.version`, under this
        repository's published rung.
    realistic_data:
    - artifact: tests/github_board.py and the journeys in tests/e2e/test_onetaskgraph_host_e2e.py
      choice: generator
      data: The `followups` board, its items and its GraphQL pricing
      reason: The live board's shared hourly allowance cannot be spent from the gate. The loopback stand-in the journeys already seed (60 and 400 other items) is cheap to generate, needs no fixture upkeep, and prices each request with the upstream PRICES table the suite drift-checks.
      summary: Generated loopback board stand-in, already seeded by the journeys; priced with the drift-checked upstream prices.
    - artifact: tests/unfinished/test_unfinished_e2e.py
      choice: generator
      data: A manager's onevcs registry at scale
      reason: Real registries are host state that cannot be shared. The journey already builds 3 identities, 60 closed sessions and 10 preserved branches with the real onevcs at no fixture cost.
      summary: Generated scale registry built with the real onevcs by the existing unfinished journey.
    repo_wide_effects:
    - budget: gate-time
      effect: Slightly lower. Two existing budgets stop re-running their tests and read recorded telemetry instead, and the event-driven rewrites shorten waits. The new budgets add only a telemetry read each.
      repository: github.com/nickderobertis/ai-orchestrator
      summary: 'Slightly lower: two budgets stop re-running tests, and event-driven rewrites shorten waits.'
    - budget: ''
      effect: none
      repository: github.com/nickderobertis/dero-skills
      summary: ''
    - budget: ''
      effect: none
      repository: github.com/nickderobertis/onebudgetspec
      summary: ''
    sizing: |-
      A follow-up run of 16 tickets on a 400-item board, which spends board points from an
      hourly allowance every session shares. One ticket's life on a 60-item board. A manager's
      own-sessions read over 3 identities, 60 closed sessions and 10 preserved branches. The
      largest lost-turn surface. A 44-node plan's review prompt.
    spike_findings: []
    ten_x: |
      At 10×, a board of about 4,000 items and 160-ticket follow-up runs, the product owner first
      notices the follow-up path's share of the board token's hourly GraphQL allowance, which
      every session on the host shares. `follow-up-run-points` covers it at today's measured
      160 points per realistic run. Its breakdown by phase, document and step says which part
      grew when it fails. The per-step structural tests (one read per item, no board walk, no
      wide page) stay assertions, so the cost cannot start growing with the board unseen. This
      plan moves those figures; it does not change how the work scales.
    ten_x_summary: At 10× (about 4,000 board items, 160-ticket runs), the follow-up path's share of the board token's hourly GraphQL allowance; follow-up-run-points covers it.
    workload: |
      This plan moves existing thresholds and adds none. Its realistic workloads are the ones
      the journeys already build:
      - **A realistic follow-up run:** 16 tickets (12 new, each searched for by root cause and
        by text; 4 already bound and re-copied), 4 evidence comments with re-estimates, and the
        launch's one board check, on a `followups` stand-in holding 400 other items.
      - **One ticket's life:** filing, re-copy, one answered comment and withdrawal, on a
        stand-in holding 60 other items.
      - **The own-sessions read:** 3 identities × 20 closed sessions and 10 preserved branches.
      - **The plan-level review prompt:** a 44-node, 52-budget plan.
      - **Lost monitor turns:** every cause, identity and run-id case the two lost-turn
        journeys drive.
      For the lint rules: create-repo composes a few repositories a month, and each llmlint
      run judges the test and `budgets.yaml` files a change touches.
  "orchestrator.plan-review": {"by":"review-plan","key":"3466cace087c948d28f01db1e60c825a42a100a9cd6dc01d4d76951f70de1af6","reviewed_at":"2026-10-08T13:41:12.742384+00:00"}
---
Execution plan nonfunctional-requirements-are-budgets.

## What this plan delivers

Six lifecycle nodes, all `engineer`:

```
cr-budget-rule         (dero-skills: the "when is a requirement a budget" reference section,
                        with the three cases and the level rule; AGENTS.md.template line;
                        rules tests_hold_no_nonfunctional_thresholds and
                        budgets_track_product_owner_outcomes in
                        tools/onebudgetspec.llmlint.yml at 1.1.0; judged cases)  [starts now]
obs-text-detail        (onebudgetspec: `check` text output prints each result's detail
                        under its line)                                           [starts now]
aio-timing-events      (ai-orchestrator: wall-clock discriminators rewritten to wait on events)
                        <- run:A-onebudgetspec-convention...#aio-obs-convention-r3

cr-budgets-baseline    <- cr-budget-rule
                        (dero-skills: --tool removed, budgets wiring and checks for every repo,
                         dero-skills wired itself)
aio-budget-convention  <- cr-budget-rule
                        (ai-orchestrator: docs/budgets.md, planner persona, budgets templates
                         and the plan review state the level and telemetry rules)
aio-budgets            <- cr-budget-rule, aio-timing-events, obs-text-detail (its release),
                          run:...#aio-obs-convention-r3
                        (ai-orchestrator: six product-owner-level budgets analysing recorded
                         telemetry through the onebudgetspec SDK, every finer figure in a
                         budget's breakdown; both rules in force at @1; onebudgetspec pin moved)
```

**The rule.** A number in a test falls into one of three cases. A cost figure becomes a
budget. A wall-clock discriminator is rewritten to wait on an event. A time-feature
contract stays an assertion. Budgets sit at the level the product owner tracks. Finer
figures (per step, per phase, per operation, or a second unit of the same concern) are
telemetry recorded on every run and reported as the budget's breakdown in `detail`, so a
failed budget says which part grew. A budget's command analyses that telemetry through
the onebudgetspec SDK.

**Contracts.**
- Between the dero-skills and ai-orchestrator nodes: the fragment URL
  `https://raw.githubusercontent.com/nickderobertis/dero-skills/main/skills/bootstrap/create-repo/assets/llmlint/tools/onebudgetspec.llmlint.yml@1`,
  the rule names `tests_hold_no_nonfunctional_thresholds` and
  `budgets_track_product_owner_outcomes`, and the fragment's `version: 1.1.0`, all fixed
  by `cr-budget-rule`.
- Between onebudgetspec and ai-orchestrator: `check --output text` keeps every result
  line unchanged and follows it with each line of a non-empty `detail`, indented two
  spaces, as fixed by `obs-text-detail`.
- aio-budgets adopts that release through `config/onebudgetspec.version`, under this
  repository's published rung.


## Budgets

**What we're sizing for.** A follow-up run of 16 tickets on a 400-item board, which spends board points from an
hourly allowance every session shares. One ticket's life on a 60-item board. A manager's
own-sessions read over 3 identities, 60 closed sessions and 10 preserved branches. The
largest lost-turn surface. A 44-node plan's review prompt.

### Workload

This plan moves existing thresholds and adds none. Its realistic workloads are the ones
the journeys already build:
- **A realistic follow-up run:** 16 tickets (12 new, each searched for by root cause and
  by text; 4 already bound and re-copied), 4 evidence comments with re-estimates, and the
  launch's one board check, on a `followups` stand-in holding 400 other items.
- **One ticket's life:** filing, re-copy, one answered comment and withdrawal, on a
  stand-in holding 60 other items.
- **The own-sessions read:** 3 identities × 20 closed sessions and 10 preserved branches.
- **The plan-level review prompt:** a 44-node, 52-budget plan.
- **Lost monitor turns:** every cause, identity and run-id case the two lost-turn
  journeys drive.
For the lint rules: create-repo composes a few repositories a month, and each llmlint
run judges the test and `budgets.yaml` files a change touches.


### At 10x realistic usage

**At 10× (about 4,000 board items, 160-ticket runs), the follow-up path's share of the board token's hourly GraphQL allowance; follow-up-run-points covers it.**

At 10×, a board of about 4,000 items and 160-ticket follow-up runs, the product owner first
notices the follow-up path's share of the board token's hourly GraphQL allowance, which
every session on the host shares. `follow-up-run-points` covers it at today's measured
160 points per realistic run. Its breakdown by phase, document and step says which part
grew when it fails. The per-step structural tests (one read per item, no board walk, no
wide page) stay assertions, so the cost cannot start growing with the board unseen. This
plan moves those figures; it does not change how the work scales.


### Checklist

- **latency:** covered by `own-sessions-read-seconds`.
- **quota and rate-limit headroom:** covered by `follow-up-run-points`.
- **quota spent by the board steps the realistic run does not drive:** covered by `ticket-lifecycle-points`.
- **how the work scales with data:** covered by `follow-up-run-points`.
- **resource use:** covered by `plan-level-prompt-chars`.
- **size of what reaches a reader:** covered by `lost-turn-surface-chars`.
- **spend:** not budgeted. n/a because no node adds a paid call on any runtime path. The judged cases cr-budget-rule adds to dero-skills' skilltest project run only when someone invokes `just skilltest`, never in the gate, at a few minutes of judge turns per run, which is what the existing onebudgetspec cases already cost.
- **gate time:** not budgeted. n/a because no new budget is needed. ai-orchestrator's repo-wide `gate-time` covers it. The budgets analyse telemetry the gate's own test runs already write, and two existing budgets stop re-running tests, so the change saves seconds. The event-driven rewrites can only shorten waits. onebudgetspec adds a few journeys. dero-skills gains one `budgets` recipe that measures nothing yet.
- **change cycle time:** not budgeted. n/a because no node adds a required check. aio-budgets waits once for onebudgetspec's automated release carrying obs-text-detail, which publishes on merge. The dero-skills and onebudgetspec nodes land under change-auto as before, and ai-orchestrator's `cycle-time` budget reports these landings like any other.

### Repo-wide effects

- **`github.com/nickderobertis/ai-orchestrator`** (`gate-time`): Slightly lower. Two existing budgets stop re-running their tests and read recorded telemetry instead, and the event-driven rewrites shorten waits. The new budgets add only a telemetry read each.
- **`github.com/nickderobertis/dero-skills`**: none
- **`github.com/nickderobertis/onebudgetspec`**: none

### Realistic data

- **The `followups` board, its items and its GraphQL pricing**, generator: The live board's shared hourly allowance cannot be spent from the gate. The loopback stand-in the journeys already seed (60 and 400 other items) is cheap to generate, needs no fixture upkeep, and prices each request with the upstream PRICES table the suite drift-checks. Artifact: tests/github_board.py and the journeys in tests/e2e/test_onetaskgraph_host_e2e.py
- **A manager's onevcs registry at scale**, generator: Real registries are host state that cannot be shared. The journey already builds 3 identities, 60 closed sessions and 10 preserved branches with the real onevcs at no fixture cost. Artifact: tests/unfinished/test_unfinished_e2e.py

### Spike findings

No spike finding changed this plan.


<!-- onetaskgraph:template-answers
checklist:
- budget: own-sessions-read-seconds
  concern: latency
  not_applicable: ''
  summary: ''
- budget: follow-up-run-points
  concern: quota and rate-limit headroom
  not_applicable: ''
  summary: ''
- budget: ticket-lifecycle-points
  concern: quota spent by the board steps the realistic run does not drive
  not_applicable: ''
  summary: ''
- budget: follow-up-run-points
  concern: how the work scales with data
  not_applicable: ''
  summary: ''
- budget: plan-level-prompt-chars
  concern: resource use
  not_applicable: ''
  summary: ''
- budget: lost-turn-surface-chars
  concern: size of what reaches a reader
  not_applicable: ''
  summary: ''
- budget: ''
  concern: spend
  not_applicable: n/a because no node adds a paid call on any runtime path. The judged cases cr-budget-rule adds to dero-skills' skilltest project run only when someone invokes `just skilltest`, never in the gate, at a few minutes of judge turns per run, which is what the existing onebudgetspec cases already cost.
  summary: No runtime path gains a paid call; the new judged cases run only on `just skilltest`, outside the gate.
- budget: ''
  concern: gate time
  not_applicable: n/a because no new budget is needed. ai-orchestrator's repo-wide `gate-time` covers it. The budgets analyse telemetry the gate's own test runs already write, and two existing budgets stop re-running tests, so the change saves seconds. The event-driven rewrites can only shorten waits. onebudgetspec adds a few journeys. dero-skills gains one `budgets` recipe that measures nothing yet.
  summary: Budgets read telemetry the gate already writes, and two stop re-running tests; repo-wide gate-time covers the rest.
- budget: ''
  concern: change cycle time
  not_applicable: n/a because no node adds a required check. aio-budgets waits once for onebudgetspec's automated release carrying obs-text-detail, which publishes on merge. The dero-skills and onebudgetspec nodes land under change-auto as before, and ai-orchestrator's `cycle-time` budget reports these landings like any other.
  summary: No new required check; one wait on an automated onebudgetspec release, reported by cycle-time like any landing.
overview: |
  Execution plan nonfunctional-requirements-are-budgets.

  ## What this plan delivers

  Six lifecycle nodes, all `engineer`:

  ```
  cr-budget-rule         (dero-skills: the "when is a requirement a budget" reference section,
                          with the three cases and the level rule; AGENTS.md.template line;
                          rules tests_hold_no_nonfunctional_thresholds and
                          budgets_track_product_owner_outcomes in
                          tools/onebudgetspec.llmlint.yml at 1.1.0; judged cases)  [starts now]
  obs-text-detail        (onebudgetspec: `check` text output prints each result's detail
                          under its line)                                           [starts now]
  aio-timing-events      (ai-orchestrator: wall-clock discriminators rewritten to wait on events)
                          <- run:A-onebudgetspec-convention...#aio-obs-convention-r3

  cr-budgets-baseline    <- cr-budget-rule
                          (dero-skills: --tool removed, budgets wiring and checks for every repo,
                           dero-skills wired itself)
  aio-budget-convention  <- cr-budget-rule
                          (ai-orchestrator: docs/budgets.md, planner persona, budgets templates
                           and the plan review state the level and telemetry rules)
  aio-budgets            <- cr-budget-rule, aio-timing-events, obs-text-detail (its release),
                            run:...#aio-obs-convention-r3
                          (ai-orchestrator: six product-owner-level budgets analysing recorded
                           telemetry through the onebudgetspec SDK, every finer figure in a
                           budget's breakdown; both rules in force at @1; onebudgetspec pin moved)
  ```

  **The rule.** A number in a test falls into one of three cases. A cost figure becomes a
  budget. A wall-clock discriminator is rewritten to wait on an event. A time-feature
  contract stays an assertion. Budgets sit at the level the product owner tracks. Finer
  figures (per step, per phase, per operation, or a second unit of the same concern) are
  telemetry recorded on every run and reported as the budget's breakdown in `detail`, so a
  failed budget says which part grew. A budget's command analyses that telemetry through
  the onebudgetspec SDK.

  **Contracts.**
  - Between the dero-skills and ai-orchestrator nodes: the fragment URL
    `https://raw.githubusercontent.com/nickderobertis/dero-skills/main/skills/bootstrap/create-repo/assets/llmlint/tools/onebudgetspec.llmlint.yml@1`,
    the rule names `tests_hold_no_nonfunctional_thresholds` and
    `budgets_track_product_owner_outcomes`, and the fragment's `version: 1.1.0`, all fixed
    by `cr-budget-rule`.
  - Between onebudgetspec and ai-orchestrator: `check --output text` keeps every result
    line unchanged and follows it with each line of a non-empty `detail`, indented two
    spaces, as fixed by `obs-text-detail`.
  - aio-budgets adopts that release through `config/onebudgetspec.version`, under this
    repository's published rung.
realistic_data:
- artifact: tests/github_board.py and the journeys in tests/e2e/test_onetaskgraph_host_e2e.py
  choice: generator
  data: The `followups` board, its items and its GraphQL pricing
  reason: The live board's shared hourly allowance cannot be spent from the gate. The loopback stand-in the journeys already seed (60 and 400 other items) is cheap to generate, needs no fixture upkeep, and prices each request with the upstream PRICES table the suite drift-checks.
  summary: Generated loopback board stand-in, already seeded by the journeys; priced with the drift-checked upstream prices.
- artifact: tests/unfinished/test_unfinished_e2e.py
  choice: generator
  data: A manager's onevcs registry at scale
  reason: Real registries are host state that cannot be shared. The journey already builds 3 identities, 60 closed sessions and 10 preserved branches with the real onevcs at no fixture cost.
  summary: Generated scale registry built with the real onevcs by the existing unfinished journey.
repo_wide_effects:
- budget: gate-time
  effect: Slightly lower. Two existing budgets stop re-running their tests and read recorded telemetry instead, and the event-driven rewrites shorten waits. The new budgets add only a telemetry read each.
  repository: github.com/nickderobertis/ai-orchestrator
  summary: 'Slightly lower: two budgets stop re-running tests, and event-driven rewrites shorten waits.'
- budget: ''
  effect: none
  repository: github.com/nickderobertis/dero-skills
  summary: ''
- budget: ''
  effect: none
  repository: github.com/nickderobertis/onebudgetspec
  summary: ''
sizing: |-
  A follow-up run of 16 tickets on a 400-item board, which spends board points from an
  hourly allowance every session shares. One ticket's life on a 60-item board. A manager's
  own-sessions read over 3 identities, 60 closed sessions and 10 preserved branches. The
  largest lost-turn surface. A 44-node plan's review prompt.
spike_findings: []
ten_x: |
  At 10×, a board of about 4,000 items and 160-ticket follow-up runs, the product owner first
  notices the follow-up path's share of the board token's hourly GraphQL allowance, which
  every session on the host shares. `follow-up-run-points` covers it at today's measured
  160 points per realistic run. Its breakdown by phase, document and step says which part
  grew when it fails. The per-step structural tests (one read per item, no board walk, no
  wide page) stay assertions, so the cost cannot start growing with the board unseen. This
  plan moves those figures; it does not change how the work scales.
ten_x_summary: At 10× (about 4,000 board items, 160-ticket runs), the follow-up path's share of the board token's hourly GraphQL allowance; follow-up-run-points covers it.
workload: |
  This plan moves existing thresholds and adds none. Its realistic workloads are the ones
  the journeys already build:
  - **A realistic follow-up run:** 16 tickets (12 new, each searched for by root cause and
    by text; 4 already bound and re-copied), 4 evidence comments with re-estimates, and the
    launch's one board check, on a `followups` stand-in holding 400 other items.
  - **One ticket's life:** filing, re-copy, one answered comment and withdrawal, on a
    stand-in holding 60 other items.
  - **The own-sessions read:** 3 identities × 20 closed sessions and 10 preserved branches.
  - **The plan-level review prompt:** a 44-node, 52-budget plan.
  - **Lost monitor turns:** every cause, identity and run-id case the two lost-turn
    journeys drive.
  For the lint rules: create-repo composes a few repositories a month, and each llmlint
  run judges the test and `budgets.yaml` files a change touches.
-->
