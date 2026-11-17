# Command surface for ai-orchestrator. `just --list` is the index.
#
# `just bootstrap` must work from a clean clone; `just check` is the deterministic
# quality tier (fails on any issue, no warnings-only mode). Every verb below the
# quality tier is a thin wrapper over one of the published CLIs this repository
# pins, and the e2e suite drives these recipes for real.

set shell := ["bash", "-euo", "pipefail", "-c"]
set positional-arguments
# Where `just` writes a shebang recipe's body before executing it. Left unset, it is
# the caller's `XDG_RUNTIME_DIR`, which a publication inherits: with that a `noexec`
# mount, every shebang recipe was refused at the pre-push gate (#1087). The setting
# takes a literal path resolved against the working directory, and `just` does not
# create it, so the backtick below does — a top-level assignment runs before any recipe
# body, in that same directory, so the directory exists wherever this justfile runs
# from, a bare copy of the file included; a tracked placeholder reached only checkouts.
# `/.logs/` is ignored, so a body in flight never dirties the tree. The
# `XDG_RUNTIME_DIR` mappings in the `oneharness.*.toml` files protect provider turns,
# not recipe execution, and stay. `tests/e2e/registry_and_recipes/test_just_tempdir_e2e.py` drives it.
set tempdir := ".logs/just"
# Tolerated when it fails so a plain recipe still runs in a tree that cannot be
# written; a shebang recipe there fails with `just`'s own message naming the path.
_create_recipe_tempdir := `mkdir -p .logs/just 2>/dev/null || true`
# No recipe's `uv run` syncs the environment on its way into a command: session setup is
# the one place a checkout is provisioned (AGENTS.md, "The checks"), and every recipe
# that runs `uv run` depends on `_provisioned`, or runs it through a script sourcing
# `scripts/require-provisioned.sh`, either of which refuses a checkout it has not
# provisioned rather than letting uv run another checkout's tools.
export UV_NO_SYNC := "1"

# The coverage floor is not restated here: `[tool.coverage.report] fail_under` in
# pyproject.toml is its one source, enforced by the Nx `coverage` target this recipe
# runs — the one place that sees every measuring tier's data combined.
repo_root := justfile_directory()

# List available recipes.
default:
    @just --list

# Set up from a clean clone: install the toolchain, sync the Python env, provision the
# Chromium the locked Playwright names (which `just render-diagram` draws in; Playwright
# skips a revision already installed), and activate the committed git hooks (the pre-push
# llmlint gate).
#
# `--force` is what distinguishes this from the install session setup performs just
# before it: that one reconciles the installed tree against the lockfile, while
# bootstrap discards the tree and installs from nothing — a clone can carry a
# `node_modules` no lockfile describes, and reconciling against a lockfile keeps
# whatever it does not name. Nothing past these two installs: `scripts/nx.sh` runs the
# `bootstrap` targets and heals nothing.
# llmlint: ignore[changed_behavior_has_e2e] This provisioning journey is run on every clean-clone bootstrap; recursively bootstrapping from its own e2e would replace the active test environment.
bootstrap:
    ./scripts/session-setup.sh
    ./scripts/workspace-install.sh --force
    ./node_modules/.bin/playwright install chromium
    ./scripts/nx.sh run-many -t bootstrap
    git config core.hooksPath .githooks
    # Allow local-mode lifecycle pushes into this non-bare checkout.
    git config receive.denyCurrentBranch updateInstead

# Full quality gate: format check, lint, type check, persona validation, tests
# (unit + e2e, coverage enforced), and budgets. Must pass before any commit.
#
# Every captured stage writes through `scripts/preserved-log.sh` to `.logs/<label>.log`
# (owner-only, credential values redacted, truncated per run). That log survives the
# process, so a failure is still readable afterwards and a *running* recipe can be
# followed with `tail -f .logs/check.log` instead of through /proc.
#
# Two phases because two questions: what the diff reaches, and what no diff can speak
# for. `test-checkouts` reads other repositories and `coverage` reads what the tier
# beside it just measured, so both stay out of the selection. A project's
# own `budgets.yaml` is measured by its `budgets` target, so in the selection: its
# budgets run only when a change touches it. Its `host` budgets, which no memo can
# answer, are `budgets-host`'s, outside it. Every `budgets.yaml` in the tree is
# validated by `workspace:validate-budgets` outside it, because a malformed one
# anywhere must never reach a push, whichever project the change touched. Nx takes one
# selection per invocation, so these are two invocations, and the second runs whatever
# the first returned: one run reports every failing target, and the recipe fails if
# either phase did, naming each that failed. AGENTS.md carries why
# skipping the rest checks nothing less, and `tests/nx_inputs.py` declares which tier
# is in which half.
# llmlint: ignore[changed_behavior_has_e2e] The public recipe is the real deterministic gate invoked by this task and pre-push; its sequencing failures use subprocess doubles to avoid recursively invoking the same full suite.
check:
    @source ./scripts/preserved-log.sh; preserved_log_open "{{repo_root}}" check; log=$PRESERVED_LOG; selection=$(./scripts/nx-selection.sh) || { echo "check: could not decide which projects to run over; repair the failure scripts/nx-selection.sh reported and retry" >&2; exit 1; }; read -ra selected <<<"$selection"; failed=(); ./scripts/nx.sh "${selected[@]}" -t format-check,lint,typecheck,test,test-docs,test-recipes,budgets 2>&1 | redact_secrets >>"$log" || failed+=("the diff selection"); ./scripts/nx.sh run-many -t test-checkouts,coverage,validate-budgets,budgets-host 2>&1 | redact_secrets >>"$log" || failed+=("test-checkouts,coverage,validate-budgets,budgets-host"); if (( ${#failed[@]} )); then cat "$log" >&2; phases=$(printf '%s; ' "${failed[@]}"); echo "check: deterministic checks failed in ${#failed[@]} of 2 phases (${phases%; }); fix every reported finding and retry (full output: $log)" >&2; exit 1; fi; total=$(./scripts/coverage-total.sh "{{repo_root}}"); echo "check: all deterministic checks passed${total:+ (line coverage ${total}%)}; project selection: $selection"

# Complete pre-push gate: llmlint's model-free `validate` against the comparison base,
# then llmlint's judged diff on this branch, then the deterministic checks. Cheapest
# refusal first: `validate` is seconds of static checks — the config, every ignore
# directive naming a real rule, a fragment edited without its version bump — and the
# judged diff is about a minute, where `just check` can run the better part of an hour.
# A judged refusal learned after that hour sends the branch back to pay the whole suite
# again, so the judge runs before it. The accepted cost is an occasional judged turn
# spent on a tree whose suite then fails; a green verdict is cached on the tree and the
# base commit, so the re-run after a suite fix that leaves the judged content unchanged
# replays it rather than judging again, and only a fix that moves the tree re-judges.
#
# A passing run says everything it has to say on one line, printed once every stage
# has passed: the line coverage `just check` measured, which printing here is what
# stops a reader opening `.coverage` by hand. The base commit the llmlint verdict
# covers is the first line of that stage's log, as `just lint-llm-diff` prints it.
# llmlint: ignore[changed_behavior_has_e2e] Running the complete gate from a test would recursively run this same suite; the stage order, refusals and summary are driven through traced Nx and llmlint doubles in tests/e2e/workspace_copies/test_workspace_contract_e2e.py, and its judged stage against the comparison base is proven end to end in tests/e2e/workspace_copies/test_llmlint_cache_e2e.py.
# Both arguments default to empty and are passed straight through, because
# `scripts/comparison-base.sh` is the one source of which remote and base the gate
# judges against — including the environment chain the lifecycle exports it under.
# Restating that chain here would not merely duplicate it: these values are passed
# positionally, so the copy would always win and a name changed in the script would
# silently keep resolving through the stale one.
gate remote="" base="":
    @comparison=$(scripts/comparison-base.sh "$1" "$2")
    @source ./scripts/preserved-log.sh; comparison=$(scripts/comparison-base.sh "$1" "$2"); preserved_log_open "{{repo_root}}" gate-llmlint-validate; log=$PRESERVED_LOG; just lint-llm-validate --diff-base "$comparison" 2>&1 | redact_secrets >"$log" || { cat "$log" >&2; echo "gate: llmlint validate failed; fix the llmlint config or ignore directive it names (a rule that does not exist, or a fragment edited without its version bump) against 'just lint-llm-validate --diff-base $comparison' alone, then rerun 'just gate ${comparison%%/*} ${comparison#*/}' (full output: $log)" >&2; exit 1; }
    @source ./scripts/preserved-log.sh; comparison=$(scripts/comparison-base.sh "$1" "$2"); preserved_log_open "{{repo_root}}" gate-llmlint; log=$PRESERVED_LOG; just lint-llm-diff "$comparison" 2>&1 | redact_secrets >"$log" || { cat "$log" >&2; echo "gate: llmlint failed; clear the reported findings against 'just lint-llm-diff $comparison' alone, then rerun 'just gate ${comparison%%/*} ${comparison#*/}' once to confirm (full output: $log)" >&2; exit 1; }; preserved_log_open "{{repo_root}}" gate-check; log=$PRESERVED_LOG; just check 2>&1 | redact_secrets >"$log" || { cat "$log" >&2; echo "gate: deterministic checks failed; fix the reported findings against 'just check' alone, then rerun 'just gate ${comparison%%/*} ${comparison#*/}' (full output: $log)" >&2; exit 1; }; total=$(./scripts/coverage-total.sh "{{repo_root}}"); echo "gate: complete gate passed${total:+ (line coverage ${total}%)}"

# Whole suite (unit + e2e) with coverage enforced on the orchestrator package.
#
# Extra Nx flags pass straight through, which is how one tier is forced to re-run
# rather than replay: `just test --skip-nx-cache`. It is deliberately
# per-invocation — an exported global cache skip re-rolls every tier from every
# unrelated command, including the ones whose contract is cache replay.
#
# A green run says one line, like `check`: the suite's own output is the failure
# report, and it is streamed in full when there is one.
#
# It runs one tier `check` never does: `test-pypi` — `session-setup-pypi`'s journeys that
# install the adopted published tools from PyPI, and `plan-store-dependency-read`'s, which
# fetches an earlier plan-store release from there — which no diff selection may reach.
test *nx_args:
    @log=$(mktemp); trap 'rm -f "$log"' EXIT; ./scripts/nx.sh run-many -t test,test-docs,test-recipes,test-checkouts,test-pypi,coverage {{nx_args}} >"$log" 2>&1 || { cat "$log" >&2; echo "test: suites failed; fix the reported findings and rerun 'just test'" >&2; exit 1; }; echo "test: all suites passed"

# The e2e suite alone (the real recipes, wrappers, and harness CLI) — quick inner loop.
#
# Same worker count and distribution as the `test` tier, for the same reason: the
# journeys wait on subprocesses rather than compute, so the wall clock is latency
# and the workers are nearly free.
test-e2e: _provisioned
    # llmlint: ignore[tool_output_is_signal] Watching one suite run as it goes is the only thing this recipe is for; `just test` is the one that reduces a green run to a line.
    @uv run pytest tests/e2e tests/plan_tooling tests/ask_seam -n 4 --dist loadgroup

# Lint Python (ruff) and the shell script (shellcheck); fail on findings.
lint:
    ./scripts/nx.sh affected -t lint

# Static type check.
typecheck:
    ./scripts/nx.sh affected -t typecheck

# Format the codebase in place.
format:
    ./scripts/nx.sh affected -t format

# Fail if anything is unformatted (used by the gate).
format-check:
    ./scripts/nx.sh run-many -t format-check

# Upgrade dependencies, then re-run the full gate; commit the refreshed lockfile.
#
# Captured through `scripts/preserved-log.sh` like every other stage that swallows
# its own output: an upgrade that fails inside `uv lock` or `bun update` leaves this
# log as the only account of which constraint could not be solved, and a `mktemp`
# file an EXIT trap removes takes that account with it.
# llmlint: ignore[changed_behavior_has_e2e] The public recipe's real Bun success/failure paths run in an isolated fixture; uv and Nx are subprocess doubles because recursively running the full upgraded suite from pytest cannot terminate.
upgrade:
    @source ./scripts/preserved-log.sh; preserved_log_open "{{repo_root}}" upgrade; log=$PRESERVED_LOG; { uv lock --upgrade && uv sync && bun update --latest nx playwright typescript@6 && ./scripts/nx.sh run-many -t build,lint,typecheck,test,test-docs,test-recipes,test-checkouts,test-pypi,coverage; } 2>&1 | redact_secrets >"$log" || { cat "$log" >&2; echo "upgrade: repair dependency constraints or target findings and retry (full output: $log)" >&2; exit 1; }; echo "upgrade: dependencies refreshed and targets passed"

# --- delegated verbs ------------------------------------------------------
#
# Every recipe in this section is a thin wrapper over one of the published CLIs
# pinned in `config/*.version`: `onepipeline` (runs and the planner channel),
# `oneagentgraph` (dispatch, personas, scratch), `onevcs` (repository identities
# and publication), and `onepipeline-api` (the read API and the browser view).
#
# The recipe names and argument shapes are the operating surface of this host —
# every planner habit and every doc reference here names them — so a wrapper
# absorbs a CLI whose shape differs rather than passing the difference on. Where a
# published verb genuinely does something else than the recipe used to, the recipe
# that lost it says so in its own comment rather than pretending inside the shell.

# Launch a run on this host's monitor graph and stay attached: the engine drives
# the DAG continuously to settlement while the attached launch streams the run's
# merged events, and returns when the run settles — the graph completed, a
# blocking planner surface is waiting, or nothing is driving the run (exit 3).
# `--detach` returns at the launch record instead, for a long unattended run.
# Either way the run leads its own session, so Ctrl-C detaches rather than
# stopping it.
#
# That attached stream is the whole merged view, not the `planner` profile `just
# monitor` defaults to: the read-time profiles are options of the reading verbs, and
# a launch takes none. Reattach with `just monitor <run-id>` for the narrower one.
#
# The two graph flags and the label this adds are the whole difference between this
# recipe and the other launches `scripts/onepipeline.sh` runs. The rest of a launch's
# settings — the run-end hooks, the drafting graph, the bus configuration, the dispatch-env
# hook and the envelope reviewer among them — are config/onepipeline.launch.yaml's, which
# every `start` names; the other launchers blank the hooks and the drafting graph, so this
# recipe is the one whose runs keep them. Both graph flags ship defaulted to nothing,
# because the published crate ships the flags and not the documents:
#
#   * `--dag-graph graphs/dag-scope.yaml` — no agent is required to run a plan at
#     all, and this is the opt-in: the active monitor that watches the run and
#     reports its progress.
#   * `--review-reply-graph graphs/review-reply.yaml` — a launch naming none answers
#     no reviewer: a rework's owed replies stay owed and surfaced, and a `decline` is
#     refused, so `just review-comments` would find comments nothing could reply to.
#   * `--label kind=main` — the run kind the browser groups a plan's runs by. No
#     `--group`: the engine groups a launch from a board copy through the copy's
#     origin, and a launch from its own project by that project.
#
# An operator who names any of them themselves keeps it, per flag and including
# `--dag-graph off` (and per label key for `kind`), and so does one naming a setting the
# launch config carries, a blank hook included: the flags refuse to
# be given twice, and the caller's intent is the specific one.
#
# `just orchestrate --adopt <run-id>` is `onepipeline adopt`: the published surface
# splits adoption into its own verb, and this recipe keeps the one spelling the
# planner doctrine names. `--adopt` has to lead, because everything after it is the
# adopt verb's own. It takes none of these flags — adoption attaches a fresh driver
# to an intact ledger, which already records the graphs, hooks and labels its launch chose.
# `scripts/orchestrate.sh` is the mechanism.
#
# Per-side routing is a property of the launched graphs. `onepipeline start`
# forwards `--set` to the dag graph and `--node-set` to every dispatched node
# graph; docs/onejudge-integration.md gives the exact config-ref overrides.
#
# All three shapes dispatch, so all three carry the ask-manager seam their workers
# stop and ask through; `scripts/onepipeline.sh` establishes it for `start` and
# `adopt` alike, and refuses a launch whose wrapper it cannot run.
[doc('Launch a qualified onetaskgraph project on the monitor and drafting graphs; `--detach` returns at its launch record and `--adopt <run-id>` resumes it.')]
orchestrate *args:
    @./scripts/orchestrate.sh "$@"

# The planning road (AGENTS.md, "The plan flow"): each recipe below is a thin wrapper over
# library verbs, the workflow itself being `templates/planning/`'s plan graphs and its policy
# `graphs/planner.yaml`'s judge panel and `config/plan-checklist.llmlint.yml`.
#
# `just plan <brief.md> [destination]` creates a planning project from
# `templates/planning/planning.md.j2` — the plan's id read from the brief's `Plan project:`
# line, its title from the brief's H1, the checklist's rules as `llmlint rules` prints them —
# and launches it under `onepipeline flow run`, so the planning run and the spikes run it
# starts are one flow. The project is titled `<plan title> planning`, the words the
# navigation shows, and the flow named for it in the only characters a flow name takes,
# every other one a `-`; the project id keeps the slug and timestamp.
[group("planning")]
[doc("Draft, spike, check, review, document and copy a plan from a brief, in one flow.")]
plan brief destination="plans":
    #!/usr/bin/env bash
    set -euo pipefail
    brief_text=$(cat -- {{ quote(brief) }}) || { echo "plan: the brief {{ brief }} cannot be read; name a readable brief file, then retry" >&2; exit 2; }
    plan=$(sed -n 's/^Plan project:[[:space:]]*//p' <<<"$brief_text" | head -n 1)
    [ -n "$plan" ] || { echo "plan: {{ brief }} states no 'Plan project: <source>:<project>' line; add one naming the project the planner writes, such as 'Plan project: authoring:<name>', then retry" >&2; exit 2; }
    title=$(sed -n '/^#[[:space:]]/{s/^#[[:space:]]*//;s/[[:space:]]*$//;p;q;}' <<<"$brief_text")
    [ -n "$title" ] || { echo "plan: {{ brief }} has no '# <title>' heading; open it with the plan's title, whose first words name what the work changes, then retry" >&2; exit 2; }
    native=${plan#*:}
    id="$native-planning-$(date +%Y%m%d%H%M%S)"
    checklist=$(llmlint rules -c config/plan-checklist.llmlint.yml --files "projects/$native.md" "tasks/$native-task.md" --format markdown) ||
        { echo "plan: llmlint could not print the plan checklist's rules (its diagnostic is above); repair config/plan-checklist.llmlint.yml or run 'just setup-llmlint', then retry" >&2; exit 2; }
    .venv/bin/onetaskgraph project create "${plan%%:*}" --id "$id" --title "$title planning" \
        --template templates/planning/planning.md.j2 --metadata onepipeline.schema_version=3 \
        --var "plan=$plan" --var "native=$native" --var "plan_title=$title" --var brief={{ quote(brief) }} \
        --var "brief_text=$brief_text" --var destination={{ quote(destination) }} \
        --var "checklist=$checklist" \
        --no-interactive >&2
    exec scripts/onepipeline.sh flow run --name "${title//[^A-Za-z0-9._-]/-}-planning" -- scripts/onepipeline.sh start "${plan%%:*}:$id" \
        --launch-config config/onepipeline.workflows.yaml --dag-graph off --label kind=planning

# `just finish-plan <source>:<project> [destination]` runs the planning road from `check`
# onward over a plan already drafted, or edited since: `templates/planning/finishing.md.j2`,
# created in `authoring` and launched in its own flow under the board credential, since the
# plan it finishes may be a board copy; its design document is written from the plan's own
# description. Project and flow are named as `just plan` names them, from the plan's stored
# title followed by `finishing`.
[group("planning")]
[doc("Check, review, document and copy an already drafted plan, in one flow.")]
finish-plan plan destination="plans":
    #!/usr/bin/env bash
    set -euo pipefail
    plan={{ quote(plan) }}
    native=${plan#*:}
    id="$native-finishing-$(date +%Y%m%d%H%M%S)"
    shown=$(scripts/plan-store.sh .venv/bin/onetaskgraph project show "$plan" --json --no-interactive) && brief_text=$(jq -er '.items[0].item.content // ""' <<<"$shown") ||
        { echo "finish-plan: $plan could not be read from the plan store (its diagnostic is above); name a plan the store holds, then retry" >&2; exit 2; }
    title=$(jq -r '.items[0].item.title // ""' <<<"$shown")
    [ -n "$title" ] || { echo "finish-plan: $plan has no title in the plan store; give it the brief's title, whose first words name what the work changes, then retry" >&2; exit 2; }
    scripts/plan-store.sh .venv/bin/onetaskgraph project create authoring --id "$id" --title "$title finishing" \
        --template templates/planning/finishing.md.j2 --metadata onepipeline.schema_version=3 \
        --var "plan=$plan" --var "native=$native" --var destination={{ quote(destination) }} \
        --var "brief_text=$brief_text" \
        --no-interactive >&2
    exec scripts/plan-store.sh scripts/onepipeline.sh flow run --name "${title//[^A-Za-z0-9._-]/-}-finishing" -- scripts/onepipeline.sh start "authoring:$id" \
        --launch-config config/onepipeline.workflows.yaml --dag-graph off --label kind=planning

# `just check-plan <source>:<project>` is the engine's own plan loader with this host's one
# registered check, the planning road's `check` step exactly: `onepipeline plan check`, whose
# load also asks onevcs whether each release adoption can complete, and
# `orchestrator/host_adoption_check.py`'s two host-install rules. Exit 1 is a refusal.
[group("planning")]
[doc("Load a plan as its launch will, with this host's install check registered.")]
check-plan plan:
    @scripts/plan-store.sh scripts/onepipeline.sh plan check {{ quote(plan) }} --check orchestrator/host_adoption_check.py

# `just review-plan <source>:<project>` judges, with `graphs/planner.yaml`'s judge-only run,
# every task and the project whose `plan-review` attestation is not valid under the current
# bar, and attests each pass: an edited plan is re-reviewed by running it again, judging only
# what changed. `scripts/review-plan-item.sh` is the command; exit 1 is a judged refusal.
[group("planning")]
[doc("Judge every unreviewed task and the project of a plan, attesting each pass.")]
review-plan plan:
    @scripts/plan-store.sh scripts/review-plan-item.sh {{ quote(plan) }}

# `just approve-design <source>:<project> [run]` records the user's approval on the board
# copy's design document — `onetaskgraph document attest --kind design-approval`, under the
# design-doc template's digest — and, given the planning run, attests its waiting
# `approve-design` node and adopts the run so it settles. Editing the document or the template voids it; `just orchestrate`'s
# launch check refuses the plan until it is approved again.
[group("planning")]
[doc("Record the user's approval of a plan's design document and release the planning run.")]
approve-design plan run="":
    #!/usr/bin/env bash
    set -euo pipefail
    plan={{ quote(plan) }}
    document=$(scripts/plan-store.sh .venv/bin/onetaskgraph document list --source "${plan%%:*}" --project "$plan" --json --no-interactive |
        jq -er --arg plan "$plan" '[.items[] | select(.item.metadata["onetaskgraph.template"].template == "onepipeline:design-doc") | .id] | if length == 1 then .[0] else error("approve-design: \($plan) holds \(length) design documents rendered from onepipeline:design-doc, not one; write it with just finish-plan \($plan) when there is none, or remove all but the current one with the plan store, then retry") end')
    bar=$(scripts/onepipeline.sh template resolve design-doc --json | jq -er '.digest | select(type == "string" and . != "")') ||
        { echo "approve-design: the engine resolved no design-doc template digest to record the approval under; repair the template until 'scripts/onepipeline.sh template check design-doc' passes, then retry" >&2; exit 2; }
    scripts/plan-store.sh .venv/bin/onetaskgraph document attest "$document" --kind design-approval --by approve-design \
        --bar "$bar" --no-interactive
    [ -z {{ quote(run) }} ] || { scripts/onepipeline.sh attest {{ quote(run) }} approve-design && scripts/onepipeline.sh adopt {{ quote(run) }} --detach; }

# Render one Mermaid source to the PNG a design document references: `just render-diagram
# <source.mmd> <output.png>`. The locked mermaid-cli draws it in the locked font, on the
# Chromium the locked Playwright names, at an 880-px page; the committed
# `config/mermaid/` files and the script's header say what else it reads. It attaches the
# image to nothing: the plan store's own `--asset` gives a document its images. Exit 2 is
# an argument refused, 3 an absent browser or mermaid-cli, and 1 a source mermaid-cli could
# not render or a PNG that could not be placed; none touches the output path.
[doc("Render a Mermaid source to the PNG a design document references.")]
render-diagram *args:
    @./scripts/render-diagram.sh "$@"

# `just copy-plan <source>:<project> [destination]` copies a plan, its tasks and its
# documents with `onetaskgraph project copy`; attestations travel with the copy.
[group("planning")]
[doc("Copy a plan, its tasks and its documents onto the plan board.")]
copy-plan plan destination="plans":
    @scripts/plan-store.sh .venv/bin/onetaskgraph project copy {{ quote(plan) }} --to {{ quote(destination) }} --documents --no-interactive

# Read the next planner surface, with the events that led to it: `just channel-next
# <run-id>`.
#
# Reads through the `planner` profile, which is `onepipeline`'s own default and the
# reason this recipe adds no flag: that profile is the pipeline's own events — node
# and step completion and failure, decisions, planner surfaces, and the monitor and
# check-in updates — and not each dispatched worker's turns. `--filter detailed`
# widens it to the whole merged stream the monitor member reads, `--filter
# <spec>` takes a filter file or inline JSON, and `--all` reads the store through no
# profile at all. The two are mutually exclusive; the CLI says so. `--flow <ID>` in place
# of the run reads a launch flow's own channel.
# llmlint: ignore[tool_output_is_signal] channel-next returns a structured bounded status or a validated transport error for planner recovery.
channel-next *args:
    @./scripts/onepipeline.sh next "$@"

# `just channel-reply <run-id> [FILE] [--correlation C]` — the envelope is read from
# FILE, or from stdin when none is named. It carries a verdict, versioned live graph
# edits, or both. `just channel-reply --flow <ID> FILE` answers a launch flow's own
# channel, with a verdict alone: a flow has no graph to edit.
#
# It is the engine's own submission verb, `onepipeline reply`, through
# `scripts/onepipeline.sh` so the acting session is named as every other verb names it,
# and it adds nothing: the engine routes the envelope by its halves. It opens the run's
# channel under the bus configuration the run's launch recorded — this host's
# config/onemessagebus.yaml, as it stood at that launch — so the declared authors and their
# grants, and the engine's envelope reviewer `config/onepipeline.launch.yaml` names, refuse
# an envelope whole before anything is appended.
# A verdict binds to the ask `--correlation` names, or to the one ask pending; a
# correlation nothing pending holds, and `--correlation` on an envelope carrying no
# verdict half, are refused. Edits to a run a driver holds are queued and waited on for
# ONEPIPELINE_REPLY_TIMEOUT_SECONDS; edits to a run nothing drives are applied by the
# reply itself, and a completion verdict or a `complete` closes a settled run whose
# driver has exited. Stdout is the engine's one-line receipt,
# `{"reply":N,"state":"delivered|applied|queued",…}`, at exit 0 — a `queued` one
# included, with its advice on stderr — and every refusal exits 2. A `queued` edit
# resolves even if its driver dies first: `just orchestrate --adopt` replays each
# envelope the dead driver claimed exactly once.
# `[no-exit-message]` because the engine's own refusal is the whole account of a failed
# reply, and `just`'s line after it would only restate the exit status.
# llmlint: ignore-block[tool_output_is_signal] This process is replaced by the engine's verb, so its receipt and refusals are the whole of the output; a block because `just` takes no comment between an attribute and its recipe.
[no-exit-message]
channel-reply run *args:
    @./scripts/onepipeline.sh reply "$@"
# llmlint: ignore-end[tool_output_is_signal]

# Raise a non-blocking planner status update: `just channel-surface <run-id> [TEXT]`.
channel-surface *args:
    @./scripts/planner-surface.sh "$@"

# jq encodes the prose, so a quote or a newline in a reason reaches the envelope intact
# rather than truncating it. `just channel-reject <run-id> <why>` and `just
# channel-continue <run-id> <what-next>` are the other two.
# Send a verdict as a reply envelope through `just channel-reply`: `just channel-approve <run-id>`.
channel-approve run *text:
    @"{{just_executable()}}" --justfile "{{justfile()}}" _verdict approve "$@"

channel-reject run *text:
    @"{{just_executable()}}" --justfile "{{justfile()}}" _verdict reject "$@"

channel-continue run *text:
    @"{{just_executable()}}" --justfile "{{justfile()}}" _verdict continue "$@"

_verdict verdict run *text:
    #!/usr/bin/env bash
    set -euo pipefail
    verdict="$1" run="$2"; shift 2; text="$*"
    command -v jq >/dev/null || { echo "channel-$verdict: jq is not on PATH, so the verdict cannot be encoded; install jq, then send it again" >&2; exit 2; }
    if [ "$verdict" = approve ] && [ -n "$text" ]; then echo "channel-approve: approve does not accept a message; send it again with the run id alone" >&2; exit 2; fi
    if [ "$verdict" != approve ] && [ -z "$text" ]; then echo "channel-$verdict: $verdict requires a message; send it again with the reason after the run id" >&2; exit 2; fi
    jq -cn --arg verdict "$verdict" --arg text "$text" 'if $verdict == "approve" then {version: 3, completion: true, reason: "approved"} else {version: 3, completion: false, reason: $text, message: $text} end' \
        | "{{just_executable()}}" --justfile "{{justfile()}}" channel-reply "$run"

# Reclaim the dead working directories this host accumulates: `onevcs sweep`, then
# `oneagentgraph sweep`, each with the caller's options and each printing its own report,
# whose "Families not examined" section names every family it left and who owns it. The
# second runs whatever the first exits, and the recipe fails if either did. The floor is
# `--min-age-hours 4` when the caller names none, because both verbs' own 24 hours
# reclaims almost nothing on a host that churns workspaces hourly (docs/orchestration.md,
# "The recorded run"). Both run under the host sweep lock `scripts/host-sweep.sh` holds,
# so a sweep another caller holds is named and nothing is swept beside it; session setup
# starts this recipe detached through that script, at most once an hour.
# llmlint: ignore[tool_output_is_signal] the two verbs' own reports are what this command is run to read.
sweep *args: _provisioned
    #!/usr/bin/env bash
    set -euo pipefail
    . "{{repo_root}}/scripts/host-sweep.sh"
    host_sweep_hold "$@" || exit $?
    floor=(--min-age-hours 4)
    for argument in "$@"; do
        case "$argument" in --min-age-hours | --min-age-hours=*) floor=() ;; esac
    done
    status=0
    # The verbs are not handed the lock: nothing they leave running may hold it.
    uv run onevcs sweep "$@" "${floor[@]}" 9>&- || status=$?
    uv run oneagentgraph sweep "$@" "${floor[@]}" 9>&- || status=$?
    host_sweep_record "$status"
    exit "$status"

# Three verbs, one per branch state, and between them they cover every state a
# branch here can be in — so no branch state is a reason to reach for raw `git` or
# `gh`. Pick by what the branch *is*, not by what is convenient:
#
#   `just publish-branch` — a complete branch no session holds, whose work is
#       finished and whose provenance is clean. Verifies it and publishes it under
#       the policy its identity's rules resolve.
#   `just repo-recover`   — a preserved branch carrying an unattested incomplete-step
#       marker. It is the one that knows how to attest that marker, which is why an
#       incomplete branch must never be finished off with an ordinary commit.
#   `just integrate`      — the local merge train: named finished branches merged
#       into their base in order, optionally pushed.
#
# `just recoverable` is what tells you which state a branch is in, and prints the
# command that lands it.

# Verify and publish a complete unpublished branch that no session holds, under the
# policy its identity's rules resolve.
# `just publish-branch <branch> --repo <checkout> [--title <T>] [--policy <P>]
#  [--body <TEXT> | --body-file <PATH>] [--no-draft]`.
#
# The state between the two verbs below: `onevcs recover` is for a branch whose
# provenance is incomplete, and `integrate` is a local merge train that opens no
# change request. A branch that is simply *done* and unpublished had neither of
# those, which is what left an agent reaching for `gh pr create` by hand.
#
# The engine's `onepipeline publish-branch` drafts the change request's body through
# `graphs/pr-author.yaml`, the drafter a run's own publications use, and forwards every
# other argument to the linked `onevcs publish-branch` unchanged. A caller's own
# `--body`/`--body-file` and `--no-draft` skip the drafting turn, a `local-direct`
# identity is never drafted for, and a draft that cannot run never blocks the landing.
#
# `--policy` may narrow the rules-resolved policy but never widen it past requiring
# approvals; the CLI enforces that rather than this recipe.
# llmlint: ignore[tool_output_is_signal] what this verified and where it published the branch — the merge path's verdict, the route taken, and the change request's URL — is the product an operator runs it for, exactly as for the `integrate` train below.
publish-branch *args:
    @./scripts/onepipeline.sh publish-branch --pr-author-graph graphs/pr-author.yaml "$@"

# Verify and publish a lifecycle-preserved branch through its registered workflow,
# attesting the incomplete-step marker it carries.
# `just repo-recover <branch> --repo <canonical-checkout> [--title <T>]
#  [--body <TEXT> | --body-file <PATH>] [--no-draft]`.
#
# This is the incomplete-provenance verb. A branch with nothing left incomplete is
# `just publish-branch`'s; `onevcs recover` takes no `--policy`, because the policy a
# recovered branch publishes under is the one its rules already resolved. It drafts the
# body through the same engine verb and drafter, under the same escapes, and a branch
# reaches it because its workstream died — so what it was doing is exactly what no
# reader can reconstruct from the diff.
# llmlint: ignore[tool_output_is_signal] what this verified, what it attested, and where it published the branch — the merge path's verdict, the recovered marker, and the change request's URL — is the product an operator runs it for, exactly as for `publish-branch` above.
repo-recover *args:
    @./scripts/onepipeline.sh repo-recover --pr-author-graph graphs/pr-author.yaml "$@"

# Pull review feedback into a run: `just review-comments <run-id> [--json]`.
#
# `onepipeline review`: it readjusts the run's unlanded drafts onto what has landed on
# the host first, then reads every draft's reviewer comments in one batched read per
# repository, surfaces each pending one to the planner as a `review-comment` against the
# node owning its change request, and drafts and posts every reply a settled rework owes
# through `graphs/review-reply.yaml` — the graph this host's launches name. It starts no
# work on a comment: that is the manager's `rework` or `decline`. Comments are found only
# when this recipe is run; nothing schedules it and no driver polls for them.
#
# It runs with the environment a drafting turn needs, as `just publish-branch` does —
# `scripts/onepipeline.sh` says why — and with standard input closed, so a manager's
# watch or a script can run it and nothing it starts waits on a terminal.
# llmlint: ignore[tool_output_is_signal] the receipt — what was readjusted, which comments were surfaced against which node, which replies were posted — is the product a manager runs it for.
[doc("Readjust a run's drafts, surface their pending review comments and post the replies its reworks owe.")]
review-comments *args:
    @./scripts/onepipeline.sh review "$@" </dev/null

# Readjust a run's drafts onto what has landed on the host: `just readjust-stack
# <run-id> [--json]`.
#
# `onepipeline readjust`, the pass every review trigger runs first, on its own: a draft a
# person merged on the host is recorded landed, and every draft above it is moved onto
# its nearest unlanded ancestors or the base — merged in, republished and retargeted,
# with no agent turn unless the merge conflicts, when the draft's own worker is
# dispatched to conclude it. Nothing polls for landings, so this is how a manager asks
# for that move without pulling review comments. Same environment and closed standard
# input as `just review-comments`, for the same reasons.
# llmlint: ignore[tool_output_is_signal] the receipt — what each draft was moved onto, what landed, which verdicts are still outstanding — is the product a manager runs it for.
[doc("Readjust a run's drafts onto what has landed on the host.")]
readjust-stack *args:
    @./scripts/onepipeline.sh readjust "$@" </dev/null

# The across-round derivation this recipe used to print has no successor verb,
# because it has no successor step: the engine reconciles a live desired graph
# continuously, so a change to the plan is a live edit applied to the running graph
# rather than a document derived between rounds. `just channel-reply` is where an
# edit goes. This recipe stays only to say so.
replan *args:
    @echo "replan: the graph is reconciled continuously, so there is no between-rounds derivation to print — send the change as a live edit with 'just channel-reply <run-id>' (see docs/orchestration.md, 'Live graph edits')" >&2; exit 2

# Update, verify, fast-forward, and optionally push completed workstream branches:
# `just integrate claude/a claude/b --push`.
#
# The engine's own `onepipeline integrate`, which runs the linked `onevcs integrate`
# with every argument in order and supervises it in this session's flow, exactly as
# `publish-branch` and `repo-recover` above are supervised: the owning `just watch`
# wakes on the train's milestones and blockers and the Stop hook holds while it runs.
# The wrapper is named by `repo_root` because the identity is the registered checkout
# the recipe runs *inside* (`just --working-directory <checkout> integrate …`).
# The branches have to be named. `onevcs integrate` takes a required `<BRANCHES>...`,
# so the auto-discovery this recipe used to do when given none is gone — omitting
# them is a usage error rather than a train over everything outstanding. `just
# recoverable` is where that discovery lives now: it lists every preserved
# unpublished branch and the command that lands each one, and its output is what
# feeds this argument list.
# It queues for the identity's merge-queue lock like every other publication here, so
# `scripts/onepipeline.sh` exports the bound `scripts/lock-timeout.sh` derives from how
# long this identity's gate last took; a caller who exported one of their own keeps it.
# llmlint: ignore[tool_output_is_signal] the requested readable per-branch train summary is this verb's product.
integrate *args: _provisioned
    @"{{repo_root}}/scripts/onepipeline.sh" integrate "$@"

# List recorded tracked-graph runs, who launched them, and their latest status,
# **grouped by the project each was launched from** — the engine's own default from the
# adopted release, with the runs naming no project in their own `(no project)` group.
# `just runs --flat` is the ungrouped list by run id, which is what a reader scanning for
# one run id wants; `just runs --mine` lists only the runs this session launched.
# `just status` and `just goals` given no run render the same grouping.
# llmlint: ignore[tool_output_is_signal] the requested multi-line run ledger is this viewing command's product.
runs *args:
    @./scripts/onepipeline.sh runs "$@"

# The host's one queue of what people must do or decide: `just human-items report` prints
# every open item, blocking first, with its full body — the report a manager prints on every
# update — and `list`, `show`, `answer`, `complete`, `dismiss [--reason TEXT]`, `watch` and
# `config` are the engine's own verbs over the same live store the browser's **Action items**
# page acts on. Run here, a write records this session as its actor on the user's behalf.
# llmlint: ignore[tool_output_is_signal] the item report, list and receipts are this viewing and acting command's product.
human-items *args:
    @./scripts/onepipeline.sh human-items "$@"

# Stop a run this session launched, tree and all: `just stop <run-id>`. Refuses a run
# another planner launched, or one with no recorded launcher, unless given --force.
# llmlint: ignore[tool_output_is_signal] the ownership refusal and what was stopped are this command's product.
stop *args:
    @./scripts/onepipeline.sh stop "$@"

# Soft-shut this host's running work down: `just shutdown <run-id> | --mine | --host
# [--grace SECONDS] [--force]`. Asks every live dispatch to commit, stops what is left,
# and preserves every branch on its origin; the run stays adoptable (see AGENTS.md).
# llmlint: ignore[tool_output_is_signal] the per-run, per-dispatch and per-branch report is what an operator runs a shutdown to read.
shutdown *args:
    @./scripts/onepipeline.sh shutdown "$@"

# llmlint: ignore[tool_output_is_signal] the requested cross-project goal inventory is this viewing command's product.
goals *args:
    @./scripts/onepipeline.sh goals "$@"

# Show every node outcome in one run and the evidence each one left.
# llmlint: ignore[tool_output_is_signal] this command is the requested results view.
results *args:
    @./scripts/onepipeline.sh results "$@"

# Read one run's dispatched turns: `just transcript <run-id> [node]`.
#
# The read that reaches a settled dispatch's own evidence, which the `planner`
# profile every other manager view defaults to leaves out. On the adopted
# onepipeline it renders each turn's tool *calls* and one blank line per tool
# result; the outputs are in `runs/<run-id>/events.jsonl`, which is the
# authoritative record either way. See AGENTS.md's "Command surface".
# llmlint: ignore[tool_output_is_signal] the requested per-turn transcript is this viewing command's product.
transcript *args:
    @./scripts/onepipeline.sh transcript "$@"

# Every oneharness session a run's dispatches opened: `just agents <run-id> [<node>]`,
# or `just agents --project <source:project>` for the union across a project's runs.
#
# The read that answers "which of the sessions in this host's store belong to this run?"
# It is the engine reading the run's own pointer file — `<run root>/oneharness-sessions.jsonl`
# — and grouping what it finds by session, so it covers every dispatch the engine
# started: a node's, each step of a lifecycle node, the observer graph and the change
# request drafter. The transcripts themselves do not move: they stay in this host's
# default oneharness store, and each entry carries the three fields that open it there.
# `just monitor` does NOT fold this — see docs/orchestration.md, "Monitoring a live run".
# llmlint: ignore[tool_output_is_signal] the requested per-run agent inventory is this viewing command's product.
agents *args:
    @./scripts/onepipeline.sh agents "$@"

# Record an unverified, non-blocking follow-up against a run, as its manager:
# `just follow-up <run-id> --title TITLE --repository HOST/OWNER/NAME [--path PATH]... < body.md`.
#
# The same draft a dispatch writes through `$ORCHESTRATOR_FOLLOW_UP_DRAFT`: `onefollowup
# draft` into the run's batch, authored `manager`. `just follow-up --help` states the body's
# headings. Anything the run needs decided now is a channel reply, never a draft.
[doc('Draft an unverified, non-blocking follow-up against a run as its manager; the body is read from stdin.')]
follow-up *args:
    @./scripts/follow-up.sh "$@"

# Verify a finished run's follow-ups through onefollowup: `just follow-ups <run-id>`.
#
# Asks `onefollowup snapshot` whether the run's batch (its public id) holds anything to
# verify: for an empty one it prints one line saying so and launches nothing. Otherwise it
# creates the `follow-ups` plan from `templates/follow-ups.md.j2` — prepare, the verifying
# agent, check, publish, settle — and launches it detached, printing the follow-up run's id
# and its watch command on one line.
# `onefollowup.yaml` is the whole of what the loop is configured by; `scripts/run-ended.sh`
# runs this recipe when a run ends with every node done.
[doc("Create and launch the plan verifying a finished run's follow-ups through onefollowup.")]
follow-ups run:
    #!/usr/bin/env bash
    set -euo pipefail
    export PATH="{{repo_root}}/node_modules/.bin:{{repo_root}}/.venv/bin:$PATH"
    export ONEPIPELINE_TEMPLATE_ROOT="{{repo_root}}/templates"
    run=$1
    store=${ONEFOLLOWUP_STORE:-{{repo_root}}}
    [ -d "$store" ] || store=$(dirname -- "$store")
    batch=$(onepipeline status "$run" --json | jq -er '.public_id')
    # onefollowup alone decides whether the batch holds anything to verify; a failure or
    # refusal leaves its diagnostic on stderr and ends the recipe before anything is created.
    # It runs as the follow-up plan's steps do: here, owned by the launching manager session.
    if [ -z "${ONEFOLLOWUP_HOOKS__OWNER__SESSION:-}" ]; then
        . "{{repo_root}}/scripts/launcher-session.sh"
        export ONEFOLLOWUP_HOOKS__OWNER__SESSION="${ONEPIPELINE_LAUNCHER_SESSION:-}"
    fi
    snapshot=$(cd "{{repo_root}}" && onefollowup snapshot --batch "$batch")
    if jq -e '.empty == true' <<<"$snapshot" >/dev/null; then
        printf 'batch %s of run %s holds no follow-up drafts; no follow-up run was launched\n' "$batch" "$run"
        exit 0
    fi
    jq -e '.empty == false' <<<"$snapshot" >/dev/null || {
        echo "follow-ups: onefollowup snapshot answered no boolean 'empty' for batch $batch; adopt the release config/onefollowup.version names with 'just bootstrap', then rerun 'just follow-ups $run'" >&2
        exit 2
    }
    runs=${ONEPIPELINE_RUNS_DIR:-{{repo_root}}/runs}
    project="$run-follow-ups"
    attempt=1
    while [ -e "$runs/$project" ]; do attempt=$((attempt + 1)); project="$run-follow-ups-$attempt"; done
    loader=$(mktemp)
    trap 'rm -f "$loader"' EXIT
    onepipeline template resolve follow-up-task --json >"$loader"
    onetaskgraph project create follow-up-runs --id "$project" --title "$project" \
        --template "{{repo_root}}/templates/follow-ups.md.j2" --search-path "{{repo_root}}/templates" \
        --var "project=$project" --var "run=$run" --var "batch=$batch" --var "checkout={{repo_root}}" \
        --var "store=$store" --var "task_loader=$loader" --no-interactive \
        --metadata 'onepipeline.schema_version=3' \
        --metadata "onepipeline.goal={\"text\": \"Verify the follow-ups of run $run\"}" >/dev/null
    ./scripts/onepipeline.sh start "follow-up-runs:$project" --dag-graph off --detach \
        --label kind=follow-ups --launch-config config/onepipeline.workflows.yaml >&2
    printf 'follow-up run: %s; watch it with: just watch %s\n' "$project" "$project"

# Answer people's comments on follow-up tickets through onefollowup:
# `just follow-ups-answer-comments [--dry-run]`.
#
# Creates the `follow-up-comments` plan from `templates/follow-up-comments.md.j2` — gather,
# answer, settle — and runs it attached in a flow. Each of its steps is this recipe again:
# `--gather` runs `onefollowup comments gather`, keeping its answer in the run's own
# directory for the steps after it; `--answer` creates each owning batch's feedback plan from
# `templates/follow-up-feedback.md.j2` for the files `onefollowup comments launches` writes
# and runs it attached in the same flow, failing when one fails; `--settle` runs
# `onefollowup comments settle`. `--dry-run` runs only `onefollowup comments gather --dry-run`.
[doc("Answer people's comments on follow-up tickets through onefollowup; `--dry-run` reports only.")]
follow-ups-answer-comments *args:
    #!/usr/bin/env bash
    set -euo pipefail
    export PATH="{{repo_root}}/node_modules/.bin:{{repo_root}}/.venv/bin:$PATH"
    export ONEPIPELINE_TEMPLATE_ROOT="{{repo_root}}/templates"
    # The owner `onefollowup.yaml`'s review sink names: a launch's, else this manager session's.
    if [ -z "${ONEFOLLOWUP_HOOKS__OWNER__SESSION:-}" ]; then
        . "{{repo_root}}/scripts/launcher-session.sh"
        export ONEFOLLOWUP_HOOKS__OWNER__SESSION="${ONEPIPELINE_LAUNCHER_SESSION:-}"
    fi
    gathered="${ONEPIPELINE_RUN_ROOT:-}/gathering.json"
    case "${1:-}" in
        --dry-run)
            exec onefollowup comments gather --dry-run
            ;;
        --gather)
            onefollowup comments gather | tee "$gathered"
            ;;
        --answer)
            launches=$(onefollowup comments launches --snapshot "$(jq -er '.snapshot' "$gathered")")
            # Parsed before the loop, so a launch list jq cannot read fails the step rather than
            # leaving the loop below nothing to run.
            listed=$(jq -er '.launches[] | [.batch, .feedback] | @tsv' <<<"$launches" || jq -e '.launches == []' <<<"$launches" >/dev/null) || {
                echo "follow-ups-answer-comments: onefollowup comments launches answered a launch list this step cannot read; adopt the release config/onefollowup.version names with 'just bootstrap', then rerun 'just follow-ups-answer-comments'" >&2
                exit 2
            }
            loader=$(mktemp)
            trap 'rm -f "$loader"' EXIT
            onepipeline template resolve follow-up-task --json >"$loader"
            failed=0
            while IFS=$'\t' read -r batch feedback; do
                [ -n "$batch" ] || continue
                stamp=$(basename -- "$feedback" .md)
                project="$batch-answers-$stamp"
                onetaskgraph project create follow-up-runs --id "$project" --title "$project" \
                    --template "{{repo_root}}/templates/follow-up-feedback.md.j2" --search-path "{{repo_root}}/templates" \
                    --var "project=$project" --var "batch=$batch" --var "checkout={{repo_root}}" \
                    --var "feedback=$feedback" --var "task_loader=$loader" --no-interactive \
                    --metadata 'onepipeline.schema_version=3' \
                    --metadata "onepipeline.goal={\"text\": \"Answer the comments on batch $batch\"}" >/dev/null
                ./scripts/onepipeline.sh start "follow-up-runs:$project" --dag-graph off \
                    --label kind=follow-ups --launch-config config/onepipeline.workflows.yaml || failed=1
            done <<<"$listed"
            exit "$failed"
            ;;
        --settle)
            exec onefollowup comments settle --snapshot "$(jq -er '.snapshot' "$gathered")"
            ;;
        "")
            project="follow-up-comments-$(date -u +%Y%m%dT%H%M%SZ | tr 'TZ' 'tz')"
            onetaskgraph project create follow-up-runs --id "$project" --title "$project" \
                --template "{{repo_root}}/templates/follow-up-comments.md.j2" --search-path "{{repo_root}}/templates" \
                --var "project=$project" --no-interactive \
                --metadata 'onepipeline.schema_version=3' \
                --metadata 'onepipeline.goal={"text": "Answer the comments on follow-up tickets"}' >/dev/null
            exec ./scripts/onepipeline.sh flow run --name "$project" -- \
                ./scripts/onepipeline.sh start "follow-up-runs:$project" --dag-graph off \
                --label kind=follow-ups --launch-config config/onepipeline.workflows.yaml
            ;;
        *)
            echo "follow-ups-answer-comments: '$1' is not an option; usage: just follow-ups-answer-comments [--dry-run]" >&2
            exit 2
            ;;
    esac

# Register a repository checkout alias. Publication policy and approvals come from
# the repository's own `onevcs.toml` and any host rule overriding it, never from flags here; `onevcs` detects
# the gate the checkout itself carries, which is a different thing and not the routing.
register-repo *args: _provisioned
    @uv run onevcs register "$@"

# List repository identities and checkout aliases.
# `just repos --audit-gate-coverage` also reports, per identity, every required check
# on its merge path — read off the repository's own branch protection at that moment,
# each able to refuse a merge, and none run by anything on this host, since onevcs
# 0.11.0 removed the gate it used to run itself.
# llmlint: ignore[tool_output_is_signal] the requested repo registry listing is this viewing command's product.
repos *args:
    @./scripts/repos.sh "$@"

# Report the policy one repository publishes under, and the rule that decided it:
# `just repo-policy <identity|alias|origin|path>`.
#
# This is the routing answer, and `just repos` is not: that listing prints the gate
# `onevcs register` guessed from the checkout, while what a publication actually does
# comes from the repository's own `onevcs.toml` declaration and the host rule that
# overrides it, each line naming its source — see `docs/host-setup.md`.
# llmlint: ignore[tool_output_is_signal] the matched rule and the policy that followed are this viewing command's product.
repo-policy *args: _provisioned
    @uv run onevcs rules check "$@"

# Bring this host's registry up to its repository configuration: register every
# checkout in the host's own list,
# `${XDG_CONFIG_HOME:-$HOME/.config}/ai-orchestrator/onevcs.checkouts` (refused naming
# that path when missing), install `config/onevcs.rules.yml` overlaid by the host's
# `onevcs.rules.yml` beside that list as the rules file and `config/onevcs.releases.yml`
# as the release override, prove every registered checkout's `publication` and
# `approvals` are each decided by a rule or by its repository's own `onevcs.toml`, and
# report what each producer this host installs resolves out of the override.
# Re-runnable — run it after editing the list, an overlay or a declaration, and on a
# new host. `--dry-run` changes nothing.
# llmlint: ignore[tool_output_is_signal] the per-checkout resolved policy and per-producer release adoption this prints are what an operator applies the configuration to read.
repos-apply *args:
    @./scripts/apply-repo-registry.sh "$@"

# Run each registered sibling checkout's own `just bootstrap` ahead of the dispatch
# that publishes through its gate: `just repos-bootstrap [--checkouts FILE] [--detach]`.
# Session setup calls it last, which is how `just bootstrap` reaches it; docs/host-setup.md,
# "The sibling gates", is the account of what it reads, reports, and costs.
# llmlint: ignore[tool_output_is_signal] The per-checkout table of what happened to each sibling is what an operator runs this to read.
repos-bootstrap *args:
    @./scripts/repos-bootstrap.sh "$@"

# List recent dispatched worker sessions across every target repo.
# `just history [RUN]` lists one run's records; the argument is a run id rather
# than the record count it used to be.
# llmlint: ignore[tool_output_is_signal] human-readable history is this viewing command's product.
history *args: _provisioned
    @uv run oneagentgraph history "$@"

# Show one recorded dispatch: `just history-show <record-id>`.
# llmlint: ignore[tool_output_is_signal] the requested multi-line digest is this viewing command's product.
history-show *args: _provisioned
    @uv run oneagentgraph history show "$@"

# Watch one tracked-graph run as one concise event stream: `just monitor <run-id>`.
#
# Same profiles as `just channel-next`, and the same default: the `planner` profile
# the CLI already applies, which is the pipeline's own events rather than every
# dispatched worker's turns. Widen it with `--filter detailed` (the whole merged
# stream, what the monitor member reads), narrow it with `--filter <spec>`, or bypass profiles entirely with
# `--all`.
# llmlint: ignore[tool_output_is_signal] the requested continuous event stream is this viewing command's product.
monitor *args:
    @./scripts/onepipeline.sh monitor "$@"

# Which of this session's runs has nothing watching it: `just unwatched [--session ID]`.
#
# The read behind the `Stop` hook `.claude/settings.json` registers, offered as a
# command so an operator can ask the same question by hand. One line per reported
# run on standard output, nothing at all when there is nothing to report, and
# every run whose evidence could not be resolved on standard error — where it
# changes no status.
#
# It goes through the wrapper every other view does, because ownership is a
# comparison and a reader that did not identify itself matches no run. The hook
# deliberately does not: it reaches the binary directly, so that a turn does not
# end behind this checkout's project-environment lock. Its exit status is the
# whole of what a caller branches on and is carried back unchanged — `6` says a
# run or flow this session owns is unwatched. `--acknowledge-flow <ID> --reason <why>`
# closes a flow that ended non-zero or died, as `--acknowledge` closes a run.
# llmlint: ignore[tool_output_is_signal] one line per unwatched run is what this viewing command is run to produce, and it is the text the hook puts in front of a manager.
unwatched *args:
    @./scripts/onepipeline.sh unwatched "$@"

# Watch one run until something a supervisor has to act on happens: `just watch
# <run-id> [OPTIONS]`, the engine's own `onepipeline watch` with every argument
# forwarded. AGENTS.md's watch rule states what a watch owes, its `--until` vocabulary
# and its exit statuses, and `tests/test_watch_surface_drift.py` reconciles those
# against the installed engine. `just watch --flow <ID>` watches a launch flow instead —
# `just plan` and the follow-up recipes run as one and print its id first.
# llmlint: ignore[tool_output_is_signal] Watching a run as it happens is the whole of what this command is for; the per-event and per-heartbeat lines are its product, and the unread-surface count inside a heartbeat is the one signal AGENTS.md forbids filtering out.
[doc('Watch one run, blocking, with heartbeats that carry the unread-surface count; see AGENTS.md.')]
watch *args:
    @./scripts/onepipeline.sh watch "$@"

# Session timing and usage, for every run or one named run.
# `--breakdown` renders the operator timing view.
telemetry *args:
    @./scripts/onepipeline.sh telemetry "$@"

# Serve the DAG Observatory — the browser view and the read API it reads, on one
# origin, out of the one published binary. `--ui` is the read API's own flag: the
# bundle of its own release is built into it and answered at every path the API does
# not own, so nothing here serves, proxies or builds a view. The address it binds and
# the runs root it serves are `just telemetry-server`'s, and the server prints both.
# llmlint: ignore[tool_output_is_signal] the served URL and its request log are the foreground development server's operator-facing product.
dag-ui *args:
    @./scripts/telemetry-server.sh --ui "$@"

# Serve the DAG telemetry API alone — which also stops, adopts, replies to and shuts
# down runs as this session — loopback-bound by default. Every flag is the published
# `onepipeline-api serve`'s own; what the wrapper supplies is the runs root
# `ONEPIPELINE_RUNS_DIR` names, the `--bind` address `config/read-api.address` holds,
# and the `--session` this host acts as.
# llmlint: ignore[tool_output_is_signal] the requested long-running read API is this command's product.
telemetry-server *args:
    @./scripts/telemetry-server.sh "$@"

# Show a run's live state: what is driving it, what is running, and what it is running
# in. The host's provider report is left out unless `--providers` asks for it, and the
# engine prints its `free space:` lines either way, so a supervisor's watch reads the default.
# llmlint: ignore[tool_output_is_signal] the requested multi-task status report is this viewing command's product.
status *args:
    @./scripts/onepipeline.sh status "$@"

# Show every live dispatch on this host, with its owner and load contribution, and the
# free space of the filesystems they are running in.
# llmlint: ignore[tool_output_is_signal] the requested per-dispatch host inventory is this viewing command's product.
host *args:
    @./scripts/onepipeline.sh host "$@"

# List every preserved-but-unpublished branch across the registered repository
# identities and the command that lands each one.
#
# Each resume command is printed in its `just` form, because those are the commands
# that draft a body: the raw `onevcs publish-branch` line `onevcs recoverable` writes
# is pasteable and lands with an empty description. `--json` is passed through whole,
# `recover_command` included, since that field is what other consumers read.
# llmlint: ignore[tool_output_is_signal] the requested recovery inventory is this viewing command's product.
recoverable *args:
    @./scripts/recoverable.sh "$@"

# The preserved-but-unpublished branches a manager session's runs left, and the command
# that lands each one: the engine's own `onepipeline unpublished`, which decides what is
# owed, renders it and records an acknowledgement. Every argument, the output and the
# exit status are the verb's (`0` nothing counted, `7` at least one counted, `1`
# unanswered, `2` refused); `onepipeline unpublished --help` names the targets and flags
# and onepipeline's `docs/contract-divergences.md` its contract. `--session <ID>` is a
# manager session and defaults to the one `scripts/launcher-session.sh` establishes,
# `--host` is every registered identity, and an onevcs `s-` session token is `--token`.
# This recipe names only what is this host's: the acknowledgement directory, the one the
# `Stop` hook in `.claude/settings.json` reads, and `graphs/pr-author.yaml`, so every
# landing command it prints drafts its change request's body.
# llmlint: ignore-block[tool_output_is_signal] the requested unpublished-branch inventory is this viewing command's product, and its exit status is the verb's own, so `just` adds no line of its own about it; a block because `just` takes no comment between an attribute and its recipe.
[no-exit-message]
unpublished *args:
    @./scripts/onepipeline.sh unpublished --acknowledgements "${XDG_STATE_HOME:-$HOME/.local/state}/ai-orchestrator/stop-unfinished/acknowledged" --pr-author-graph "{{ repo_root }}/graphs/pr-author.yaml" "$@"
# llmlint: ignore-end[tool_output_is_signal]

# What this manager session still owes before its turn can end: `just unfinished
# [--session ID] [--json]`. Stacks `onepipeline unwatched` and `just unpublished` for the
# same session, each half under its own heading. Its exit status is the whole of what a
# caller branches on, and `orchestrator/unfinished.py` is its one declaration.
# llmlint: ignore[tool_output_is_signal] the two headed halves are this viewing command's product.
unfinished *args:
    @./scripts/unfinished.sh "$@"

# Report everything onevcs knows about one piece of work:
# `just work-status <change-url|session-token|branch|commit> [--json]`.
#
# This is the one verb that answers "what became of it", and it is the answer a
# settled node's own row cannot give: a run records a node's landing as its
# settlement observed it and nothing re-reads it, so a change that merged an hour
# later still reads as not landed there. Ask this instead of inferring from a run.
# llmlint: ignore[tool_output_is_signal] the requested report on one piece of work is this viewing command's product.
work-status *args: _provisioned
    @uv run onevcs status "$@"

# Make a branch reachable from an identity's registered checkouts:
# `just import-branch <branch> --repo <checkout> [--from <source>] [--as <name>]`.
#
# The landing verbs read a branch from the publication checkout, never from
# wherever a session happens to be working, so work that only exists in a session
# worktree or a run clone is invisible to them until it is imported. Omitting
# `--from` searches everywhere this identity keeps work.
import-branch *args: _provisioned
    @uv run onevcs import "$@"

# Reclaim a superseded branch: `just reclaim-branch <branch> --repo <checkout>
# [--discard] [--dry-run] [--json]`, the line `just recoverable` and `just unpublished`
# print for a branch a landed retry replaced that still differs from its base. It deletes
# the branch from every place that holds it, its origin copy included, and returns any
# pool slot rather than removing it; `onevcs reclaim` refuses a branch it cannot prove
# superseded or that anything live holds, and its exit status is the verb's own.
# `--discard` is for a branch kept on purpose that never landed — a `preserved` node's,
# once nothing needs it — and discards its work; every other reason to keep a branch
# still refuses it.
reclaim-branch *args: _provisioned
    @uv run onevcs reclaim "$@"

# Fast-forward a publication checkout to its origin: `just sync [BRANCH]`.
sync *args: _provisioned
    @uv run onevcs sync "$@"

# Scaffold a new persona: `just new-persona <name>`. The published verb writes into
# the working directory, so this runs it in `personas/` — including the
# subdirectory a slash-qualified repo-specific name names.
new-persona *args:
    @./scripts/new-persona.sh "$@"

# Validate every persona against the delta contract (also part of `just check`).
validate-personas *args: _provisioned
    @uv run oneagentgraph persona validate "${@:-personas}"

# Spend real harness turns proving prompt delivery, complete history telemetry, and
# that claude-code runs an untrusted directory's SessionStart hook under a dispatch's
# bypass mode. Kept out of `gate`; pre-push selects it only for launch-path changes.
#
# `scripts/smoke.sh` is not ceremony around the published verb: `oneagentgraph smoke`
# runs plain `oneharness` in a throwaway directory where no project config is found,
# so the script names this repository's agent config and establishes the indirections
# its variants' `env_from` needs, then runs the trust probe beside it. See its header.
# llmlint: ignore[external_service_suite_stays_out_of_the_affected_tier] The smoke is not in an affected tier: `just gate` never runs it, and the pre-push hook selects it only through `scripts/pre-push-smoke-needed.sh`, for a pushed diff touching the harness routing files it exists to prove, which is the one change that can break them and the only time its paid turns buy anything; this node's task places the trust probe on exactly this path.
smoke *args:
    @./scripts/smoke.sh "$@"

# Render the manager's allowlist, `config/manager-allowlist.toml`, into the two tools a
# manager session is started in — `.claude/settings.json`'s `permissions.allow` and
# `.codex/rules/oneharness.rules` — with the pinned `oneharness sync --exact`, which holds
# each list to exactly the source and leaves every other key, the hooks among them, alone.
# Rules Codex cannot express are reported per rule on stderr and kept for Claude Code.
# `tests/manager_allowlist/test_manager_allowlist_sync.py` runs the same verb with
# `--check`. `project` renders into another directory instead — `just probe-allowlist`'s
# scratch project.
sync-allowlist project=".": _provisioned
    @uv run oneharness sync --exact --config config/manager-allowlist.toml --harness claude-code,codex --cwd "$1"

# Spend real Claude Code and Codex turns driving every row of the manager's invocation
# table, `tests/manager_invocations.py`, against the rendered allowlist in a scratch
# project, and print per row and per tool whether it got through, was stopped, or was
# never tried. Kept out of `check` and the pre-push gate for the reason `smoke` is;
# `scripts/allowlist-probe.py` says how nothing real is reached. `--tool` and `--row`
# narrow it.
# llmlint: ignore[external_service_suite_stays_out_of_the_affected_tier] Not in an affected tier: neither `just check` nor the pre-push gate runs it; an operator runs it by hand after changing the allowlist, the one change it can say anything about, as `smoke` is run for the harness routing.
probe-allowlist *args:
    @./scripts/allowlist-probe.sh "$@"

# Refuse a checkout session setup has not provisioned, naming `just session-setup`;
# `scripts/require-provisioned.sh` says why. Every recipe running `uv run` depends on it.
_provisioned:
    @"{{repo_root}}/scripts/require-provisioned.sh"

# Provision the session toolchain (installs onejudge, oneharness, bun, llmlint, and
# the four published CLIs this repository is a configuration layer over).
# Idempotent; runs automatically via the SessionStart hook. No-ops in CI.
session-setup:
    # llmlint: ignore[tool_output_is_signal] installation progress and per-tool verification diagnostics are the session setup's operator-facing result.
    ./scripts/session-setup.sh

# Read this repository's plan store through the pinned standalone CLI.
# The selected query's result is this viewing command's product; onetaskgraph's
# own diagnostics name failed sources and corrective actions.
#
# This checkout's own `.venv/bin`, never a directory the whole host shares: session
# setup installs the release `config/onetaskgraph.version` names there, so the binary
# this reads is the one this checkout pinned rather than whichever checkout on the
# host provisioned last.
#
# `scripts/plan-store.sh` establishes this checkout's own board credential first; its
# header is the one account of why.
# llmlint: ignore[tool_output_is_signal] The selected query's result is this viewing command's product.
plans *args:
    @./scripts/plan-store.sh ./.venv/bin/onetaskgraph {{args}}

# --- llmlint (LLM-judge tier) --------------------------------------------
# Non-deterministic, harness-backed, and kept OUT of `just check`. It runs at
# pre-push (.githooks/pre-push) and on demand. Config is the composed llmlint.yml.

# Install/refresh the llmlint toolchain (llmlint + its oneharness). Idempotent.
setup-llmlint:
    ./scripts/setup-llmlint.sh

# LLM-judge lint over the whole tree (or pass paths to narrow). Run once with
# `just setup-llmlint` first in a plain terminal.
#
# The judge runs under `oneharness.llmlint.toml`, which `llmlint.yml` names and whose
# `history_labels` stamp `role=llmlint` over any labels a dispatch inherits; the
# identity directories its variants map are exported from the shared resolvers.
lint-llm *paths: _provisioned
    @command -v llmlint >/dev/null 2>&1 || { echo "llmlint not installed — run 'just setup-llmlint'"; exit 1; }
    @source ./scripts/codex-alt-home.sh || { echo "lint-llm: scripts/codex-alt-home.sh could not be loaded; restore it from the repository, then retry" >&2; exit 1; }; ensure_codex_alt_home lint-llm; source ./scripts/claude-alt-config-dir.sh || { echo "lint-llm: scripts/claude-alt-config-dir.sh could not be loaded; restore it from the repository, then retry" >&2; exit 1; }; resolve_claude_alt_config_dir lint-llm; PATH="{{repo_root}}/.venv/bin:$PATH" llmlint "$@"

# Deterministic, model-free llmlint gate: config structure, ignore directives name
# real rules, edited fragments bumped their version. The fast pre-flight.
lint-llm-validate *args:
    @command -v llmlint >/dev/null 2>&1 || { echo "llmlint not installed — run 'just setup-llmlint'"; exit 1; }
    @llmlint validate "$@"

# llmlint scoped to the merge-base diff with main — judges only what the branch
# changed. This is the blocking pre-push check.
#
# The cached Nx target `workspace:lint-llm-diff` runs `llmlint --diff` itself, and
# Nx's task cache is the whole record: a clean run is replayed for an unchanged
# tracked tree, base commit and `llmlint fingerprint` (llmlint's digest of its
# version, config, plugins, prompt and the oneharness configuration it judges
# under), in any worktree, while findings and a run that reached no verdict are
# never cached and re-judge. `just lint-llm-diff <base> --skip-nx-cache` forces a
# re-judge. The base is resolved to a commit here, before Nx hashes it, and printed,
# because "green" means green against that commit. A base its own origin ref has
# moved past is refused first — `scripts/base-freshness.sh` decides it — because a
# stale name yields a valid verdict over commits the branch does not carry.
#
# The judge runs under `oneharness.llmlint.toml`, which `llmlint.yml` names; the
# identity directories its variants map are exported here from the shared resolvers.
# The target hands llmlint, and nothing above it, a temporary directory of its own that
# it removes on exit, because the codex harness leaves its sandbox's mount targets
# (`codex-bwrap-synthetic-mount-targets-*`) in the temporary directory on every turn and
# nothing else removes them; Nx stays on the caller's, whose sockets a deeper directory
# would push past the Unix address limit.
lint-llm-diff base="origin/main" *nx_args:
    @command -v llmlint >/dev/null 2>&1 || { echo "llmlint not installed — run 'just setup-llmlint'"; exit 1; }
    @# Nx 23.1 resolves a run-commands task on the child's `exit` before its pipes drain, so with no terminal (CI, the gate, the pre-push hook) a fast failure's output is lost; NX_RUN_COMMANDS_DIRECTLY=false lost none in 750 runs (the spike-nx-output-loss report). Remove it once Nx collects run-commands output on `close`.
    @# llmlint: ignore[tool_output_is_signal] The judge's per-rule report and the base it is for are this tier's product, replayed as Nx prints it; a quiet success here would delete the tier's result. The unresolvable-base refusal is one line naming the base with no remedy clause, by the manager's ruling for this tier. `@#` so the directive itself stays out of that report.
    @base_sha=$(git rev-parse --verify --quiet "{{base}}^{commit}") || { echo "lint-llm-diff: '{{base}}' does not resolve to a commit" >&2; exit 1; }; behind=$(./scripts/base-freshness.sh "{{base}}") || { echo "lint-llm-diff: whether '{{base}}' still matches its own origin ref could not be decided; run 'scripts/base-freshness.sh {{base}}' to see why" >&2; exit 1; }; if [[ -n "$behind" ]]; then echo "lint-llm-diff: $behind, so judging it would judge commits this branch does not carry; fetch and fast-forward '{{base}}', or name the origin ref as the base" >&2; exit 1; fi; source ./scripts/codex-alt-home.sh || { echo "lint-llm-diff: scripts/codex-alt-home.sh could not be loaded; restore it from the repository, then retry" >&2; exit 1; }; ensure_codex_alt_home lint-llm-diff; source ./scripts/claude-alt-config-dir.sh || { echo "lint-llm-diff: scripts/claude-alt-config-dir.sh could not be loaded; restore it from the repository, then retry" >&2; exit 1; }; resolve_claude_alt_config_dir lint-llm-diff; export PATH="{{repo_root}}/.venv/bin:$PATH" LLMLINT_DIFF_BASE_SHA="$base_sha"; echo "lint-llm-diff: judging this diff against base $base_sha"; NX_RUN_COMMANDS_DIRECTLY=false AI_ORCHESTRATOR_NX_SHOW_OUTPUT=1 ./scripts/nx.sh run workspace:lint-llm-diff {{nx_args}}
