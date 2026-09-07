# Command surface for ai-orchestrator. `just --list` is the index.
#
# `just bootstrap` must work from a clean clone; `just check` is the deterministic
# quality tier (fails on any issue, no warnings-only mode). Every verb below the
# quality tier is a thin wrapper over one of the published CLIs this repository
# pins, and the e2e suite drives these recipes for real.

set shell := ["bash", "-euo", "pipefail", "-c"]
set positional-arguments

# The coverage floor is not restated here: `[tool.coverage.report] fail_under` in
# pyproject.toml is its one source, enforced by the Nx `coverage` target this recipe
# runs — the one place that sees every measuring tier's data combined.
repo_root := justfile_directory()

# List available recipes.
default:
    @just --list

# Set up from a clean clone: install the toolchain, sync the Python env, and
# activate the committed git hooks (the pre-push llmlint gate).
#
# `--force` is what distinguishes this from the self-heal every `scripts/nx.sh`
# already performs: that one reconciles the installed tree against the lockfile,
# which a refreshed lockfile is enough to trigger, while bootstrap discards the
# tree and installs from nothing — a clone can carry a `node_modules` no lockfile
# describes, and reconciling against a lockfile keeps whatever it does not name.
# llmlint: ignore[changed_behavior_has_e2e] This provisioning journey is run on every clean-clone bootstrap; recursively bootstrapping from its own e2e would replace the active test environment.
bootstrap:
    ./scripts/session-setup.sh
    ./scripts/workspace-install.sh --force
    ./scripts/nx.sh run-many -t bootstrap
    git config core.hooksPath .githooks
    # Allow local-mode lifecycle pushes into this non-bare checkout.
    git config receive.denyCurrentBranch updateInstead

# Full quality gate: format check, lint, type check, persona validation, tests
# (unit + e2e, coverage enforced), and the Nx cache contract. Must pass before any
# commit.
#
# Every captured stage writes through `scripts/preserved-log.sh` to `.logs/<label>.log`
# (owner-only, credential values redacted, truncated per run). That log survives the
# process, so a failure is still readable afterwards and a *running* recipe can be
# followed with `tail -f .logs/check.log` instead of through /proc.
#
# Two Nx invocations because two questions: what the diff reaches, and what no diff
# can speak for. `test-checkouts` reads other repositories and `coverage` reads what
# the tier beside it just measured, so both stay out of the selection — as
# `workspace:check-nx-cache` always has. AGENTS.md carries why skipping the rest
# checks nothing less, and `tests/nx_inputs.py` declares which tier is in which half.
# llmlint: ignore[changed_behavior_has_e2e] The public recipe is the real deterministic gate invoked by this task and pre-push; its sequencing failures use subprocess doubles to avoid recursively invoking the same full suite.
check:
    @source ./scripts/preserved-log.sh; preserved_log_open "{{repo_root}}" check; log=$PRESERVED_LOG; selection=$(./scripts/nx-selection.sh) || { echo "check: could not decide which projects to run over; repair the failure scripts/nx-selection.sh reported and retry" >&2; exit 1; }; read -ra selected <<<"$selection"; { ./scripts/nx.sh "${selected[@]}" -t format-check,lint,typecheck,test,test-docs,test-recipes && ./scripts/nx.sh run-many -t test-checkouts,coverage && ./scripts/nx.sh run workspace:check-nx-cache; } 2>&1 | redact_secrets >"$log" || { cat "$log" >&2; echo "check: deterministic checks failed; fix the reported findings and retry (full output: $log)" >&2; exit 1; }; total=$(./scripts/coverage-total.sh "{{repo_root}}"); echo "check: all deterministic checks passed${total:+ (line coverage ${total}%)}; project selection: $selection"

# Complete pre-push gate: deterministic checks followed by llmlint on this branch.
#
# A passing run says everything it has to say on one line: the line coverage it
# measured, which base commit the llmlint verdict covers, and whether that verdict
# was judged now or replayed. The judge is non-deterministic and a clean run of it
# is cached, so "green" is a claim about one judged diff against one base commit —
# and a worker whose gate replayed a cached run needs to know that, and which base
# commit it covers, before it settles. The coverage total is measured either
# way; printing it here is what stops a reader opening `.coverage` by hand.
# llmlint: ignore[changed_behavior_has_e2e] Running the complete gate from a test would recursively run this same suite; the tier whose provenance is passed through here is proven end to end in tests/e2e/test_llmlint_cache_e2e.py.
# Both arguments default to empty and are passed straight through, because
# `scripts/comparison-base.sh` is the one source of which remote and base the gate
# judges against — including the environment chain the lifecycle exports it under.
# Restating that chain here would not merely duplicate it: these values are passed
# positionally, so the copy would always win and a name changed in the script would
# silently keep resolving through the stale one.
gate remote="" base="":
    @comparison=$(scripts/comparison-base.sh "$1" "$2")
    @source ./scripts/preserved-log.sh; preserved_log_open "{{repo_root}}" gate-check; log=$PRESERVED_LOG; just check 2>&1 | redact_secrets >"$log" || { cat "$log" >&2; echo "gate: deterministic checks failed; fix the reported findings and rerun 'just gate' (full output: $log)" >&2; exit 1; }
    @source ./scripts/preserved-log.sh; comparison=$(scripts/comparison-base.sh "$1" "$2"); preserved_log_open "{{repo_root}}" gate-llmlint; log=$PRESERVED_LOG; just lint-llm-diff "$comparison" 2>&1 | redact_secrets >"$log" || { cat "$log" >&2; echo "gate: llmlint failed; clear the reported findings against 'just lint-llm-diff $comparison' alone, then rerun 'just gate ${comparison%%/*} ${comparison#*/}' once to confirm (full output: $log)" >&2; exit 1; }; provenance=$(grep -m1 -E '^lint-llm-diff: (judged|replayed) ' "$log" || echo "lint-llm-diff: verdict provenance unavailable"); note=$(grep -q '^lint-llm-diff: ignoring ' "$log" && echo " [ignored an ambient global Nx cache skip]" || true); total=$(./scripts/coverage-total.sh "{{repo_root}}"); echo "gate: complete gate passed${total:+ (line coverage ${total}%)}; ${provenance#lint-llm-diff: }${note}"

# Whole suite (unit + e2e) with coverage enforced on the orchestrator package.
#
# Extra Nx flags pass straight through, which is how one tier is forced to re-run
# rather than replay: `just test --skip-nx-cache`. It is deliberately
# per-invocation — an exported global cache skip re-rolls every tier from every
# unrelated command, including the ones whose contract is cache replay.
#
# A green run says one line, like `check`: the suite's own output is the failure
# report, and it is streamed in full when there is one.
test *nx_args:
    @log=$(mktemp); trap 'rm -f "$log"' EXIT; ./scripts/nx.sh run-many -t test,test-docs,test-recipes,test-checkouts,coverage {{nx_args}} >"$log" 2>&1 || { cat "$log" >&2; echo "test: suites failed; fix the reported findings and rerun 'just test'" >&2; exit 1; }; echo "test: all suites passed"

# The e2e suite alone (the real recipes, wrappers, and harness CLI) — quick inner loop.
#
# Same worker count and distribution as the `test` tier, for the same reason: the
# journeys wait on subprocesses rather than compute, so the wall clock is latency
# and the workers are nearly free.
test-e2e:
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
    @source ./scripts/preserved-log.sh; preserved_log_open "{{repo_root}}" upgrade; log=$PRESERVED_LOG; { uv lock --upgrade && uv sync && bun update --latest nx playwright typescript@6 && ./scripts/nx.sh run-many -t build,lint,typecheck,test,test-docs,test-recipes,test-checkouts,coverage; } 2>&1 | redact_secrets >"$log" || { cat "$log" >&2; echo "upgrade: repair dependency constraints or target findings and retry (full output: $log)" >&2; exit 1; }; echo "upgrade: dependencies refreshed and targets passed"

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
# The two graph flags this adds are the whole difference between this recipe and a
# bare `onepipeline start`. Both ship defaulted to nothing, because the published
# crate ships the flags and not the documents — the documents name this operator's
# own oneharness configs and personas — so naming them is what makes an operator on
# this host get them and a bare `onepipeline start` elsewhere not:
#
#   * `--dag-graph graphs/dag-scope.yaml` — no agent is required to run a plan at
#     all, and this is the opt-in: the active monitor that watches the run and the
#     pacemaker that reports it.
#   * `--pr-author-graph graphs/pr-author.yaml` — a launch naming none opens its
#     change requests with the body its plan states, or with none, which is how
#     every pull request this harness opened came to carry `Published by onevcs.`
#     as its Why.
#
# An operator who names either one themselves keeps it, per flag and including
# `--dag-graph off`: the flags refuse to be given twice, and the caller's intent is
# the specific one.
#
# `just orchestrate --adopt <run-id>` is `onepipeline adopt`: the published surface
# splits adoption into its own verb, and this recipe keeps the one spelling the
# planner doctrine names. `--adopt` has to lead, because everything after it is the
# adopt verb's own. It takes neither graph flag — adoption attaches a fresh driver
# to an intact ledger, which already records the graphs its launch chose.
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
    # llmlint: ignore[tool_output_is_signal] Streaming the run as it goes is what an attached launch is for, and its validated launch failures name the input to correct; `--detach` is the spelling that returns one line.
    @if [[ "${1:-}" == "--adopt" ]]; then ./scripts/onepipeline.sh adopt "${@:2}"; else defaults=(); for pair in "--dag-graph graphs/dag-scope.yaml" "--pr-author-graph graphs/pr-author.yaml"; do read -r flag ref <<<"$pair"; named=; for argument in "$@"; do if [[ "$argument" == "$flag" || "$argument" == "$flag"=* ]]; then named=1; break; fi; done; [[ -n "$named" ]] || defaults+=("$flag" "$ref"); done; ./scripts/onepipeline.sh start "$@" ${defaults[@]+"${defaults[@]}"}; fi

# Launch a planner on a manager-written brief: `just plan <BRIEF.md> [--name NAME]
# [--max-turns N] [--repo ALIAS] [--execution-checkout ALIAS] [--direct]
# [--no-design-doc] [<onepipeline start flags>]`. Those six flags are the recipe's own;
# everything else reaches `onepipeline start` untouched.
#
# It writes the local project rather than asking a manager to remember its shape;
# `scripts/plan.sh` states what has to be right about that shape and why. The
# ask-manager seam every launch exports is not among them — that is
# `scripts/onepipeline.sh`'s, for `start` and `adopt` alike — but the run id is: this
# recipe owns the plan's `name`, so it refuses one already taken rather than letting
# the engine mint a different id than the one it printed.
#
# The node it writes is a **lifecycle** node, so the planner works in a worktree cut
# from the registered `ai-orchestrator-isolated` safety clone rather than in this
# shared checkout — which the self-dispatch rule in `AGENTS.md` forbids authoring in,
# and which a direct planner dispatch cut a branch in, committed to, and left checked
# out, failing a finished publication and destroying a manager's plan files. `--repo`
# and `--execution-checkout` name a different registered pair.
#
# `--direct` keeps the old shape for a planning dispatch that must cut no worktree at
# all. It is a working directory nobody owns exclusively, so such a dispatch **may
# write only to gitignored paths, may not commit, and may not leave the checkout on
# any branch but its base**; nothing enforces that, which is why the recipe says it on
# every launch that takes the flag.
#
# Unlike `just orchestrate` it names `--dag-graph off`: the ledger, the surfaces and
# the DAG UI place are `onepipeline start`'s own, so what an observer would add to a
# planning run is a monitor watching it for drift from the plan it is what writes. A
# caller who names one keeps it, exactly as `just orchestrate` keeps a caller's own.
#
# **The plan it writes is one node, and the rest of the flow is `just finish-plan`.** The
# document a person reviews the plan as has to be written from *reviewed* content, and a
# run cannot interject a review between its own nodes — a review record is written by this
# repository's own code and never by a dispatched agent. So when the planner has settled
# and this launch's closeout has recorded what it authored, `just plan` hands over to
# `scripts/finish-plan.sh`: review, check, launch the document, copy both into the
# destination, report where that destination holds them. `--to` names that destination and
# reaches the tail; `--no-design-doc` stops after the planner, and drops with it the
# `Plan project: <source>:<project>` line the tail needs to find the plan at all. A
# `--detach`ed launch keeps the planner alone and prints the command that finishes it,
# because it hands back before the plan exists.
[doc('Launch a planner on a manager-written brief, then review, document, copy and report the plan it writes.')]
plan *args:
    @./scripts/plan.sh "$@"

# Finish a plan a planner has already authored: `just finish-plan <brief.md> [--to SOURCE]
# [--name NAME] [--repo ALIAS] [--execution-checkout ALIAS] [--direct] [--no-design-doc]
# [<onepipeline start flags>]`.
#
# This is the tail of the planning flow and its one implementation — `just plan` runs the
# planner and then delegates to exactly this, so the two entry points cannot drift. Run it
# on its own when a plan was edited after it was authored: the edit leaves that task
# unreviewed, so the review below spends a real judged turn on it and the document is then
# written from content something has read.
#
# Five steps, in the order the tooling enforces rather than the order an operator
# remembers: review the plan, check it the way its own launch will, launch the
# design-document node as its own one-node project, copy the plan and its documents into
# the destination, and report where that destination holds the project and the document —
# read back out of the store, because a destination decides its own ids and where its
# records live.
#
# The refusals are told apart by exit status, because a builder that could not tell them
# apart would retry one as the other: 1 is the review refusing the plan's criteria, 3 is
# the pre-launch check refusing the plan, 4 is the document launch not settling, 5 is the
# destination refusing the copy, and 2 is a flow that could not run at all.
[doc("Review a plan, write the design document a person reads it as, copy both up, and report where they landed.")]
finish-plan *args:
    @./scripts/finish-plan.sh "$@"

# Read a qualified plan project against the bar each of its nodes will actually be
# judged against, and against the engine's own plan loader: `just check-plan
# <source>:<project>`.
#
# The structure of a plan is decided by `onepipeline plan check`, which runs every
# refusal `onepipeline start` would make, so a pre-dispatch refusal is a launch refusal
# by construction. This repository's own checks run beside it as a registered `--check`
# script, `scripts/plan-check.sh`: the review bar each node resolves to, this host's
# operational appendix, and whether anything has reviewed the criteria. Against an engine
# carrying no such verb the same checks run directly, and the accepted line names which
# path read the plan, because the narrower one leaves the structural refusal for the
# launch to make. AGENTS.md carries what re-implementing the loader cost.
#
# Two things about the *result* are this seam's. Exit 1 is a refusal, each naming its
# source — `engine` or the registered check — the node, the field, and the reason; exit 2
# is a project that could not be read or a check that could not be run, so nothing was
# judged. A plan builder branches on the difference. And it reads the project only: it
# launches nothing, spends no provider turn, and is safe to run beside live work.
[doc('Refuse a plan whose node would be judged against a demand its task does not state.')]
check-plan *args:
    @uv run orchestrator-check-plan "$@"

# Spend the judged turn that clears a plan's authored content: `just review-plan
# <source>:<project>`.
#
# `just check-plan` refuses a task carrying no review record for what it currently
# says, and this is the command that records one. It reads each unreviewed task against
# `personas/planner.yaml`'s own bar — the judge every planner-written plan already
# passes through, so that a plan an operator wrote by hand, or a planner's plan an
# operator then tweaked, is read by the same reviewer rather than by nobody.
#
# Three things about the result. It records a **pass** and nothing else, so a refusal
# leaves nothing behind to replay. A record it wrote is authoritative — `check-plan`
# accepts it and spends no second turn on identical content. And it takes no flag that
# lets a plan past that refusal, because an escape here is reached under exactly the
# time pressure that produced the two unreviewed plans this gate exists to catch.
#
# Exit 1 is a refusal, naming each task and the reason; exit 2 is a plan or a review
# turn that could not be read at all, so nothing was recorded either way.
[doc("Review a plan's unreviewed task content against the planner's own bar and record each pass.")]
review-plan *args:
    @./scripts/review-plan.sh "$@"

# Record the user's approval of the design document a plan is read as: `just
# approve-design <source>:<project>`.
#
# This is the gate on dispatch, and the one a person is actually the subject of. A plan
# is not what somebody outside the domain can review; the one short document
# `config/design-doc-template.md` states is, and a planning run's `design-doc` node
# writes it into the plan's own project. So the document goes to the user, and this
# command records that they approved it — after which `just orchestrate` will launch that
# plan, and before which it refuses to.
#
# The record goes onto the document in the plan store, so it travels with the plan
# through `just copy-plan` exactly as a review record travels with a task: approve where
# you draft, then copy up. It is keyed on the document's own authored content *and* on
# that template, so editing the document leaves it unapproved and moving the template
# leaves every approved document unapproved.
#
# There is no flag that skips this and none is coming; running it on an unchanged
# document a second time writes nothing and says so. Exit 1 is a refusal — no design
# document, or more than one, with nothing recorded either way.
#
# The install line is `just plans`'s, for its reason: a fresh worktree or a publication
# clone fires no `SessionStart` hook, so the plan-store CLI this reads is healed into this
# checkout's own `.venv/bin` at the release this checkout pinned.
[doc("Record the user's approval of one plan project's design document.")]
approve-design *args:
    @./scripts/onetaskgraph-install.sh
    @uv run orchestrator-approve-design "$@"

# Copy a cleared plan onto the board this repository plans against: `just copy-plan
# <source>:<project> [--to SOURCE] [<onetaskgraph project copy flags>]`.
#
# This is the step between the two recipes above, and the reason it is a command rather
# than a habit: a plan is drafted in a local Markdown source, cleared there by `just
# review-plan`, and only then copied onto the `plans` board it is launched from. That
# order is the only one that works — a review record is an entry of the task's own
# Markdown document, so a plan authored on the board can never carry one and
# `check-plan` refuses it for want of one. Before anything is written to the
# destination this reads the plan and refuses it when a task carries no record for what
# it currently says, naming each such task; it spends no judged turn and reviews
# nothing itself.
#
# `--to` names a different configured destination; everything else reaches
# `onetaskgraph project copy` untouched — `--dry-run`, `--recreate`, `--match-by` —
# rather than being re-declared by a wrapper with no opinion about them. A `--set`
# among them configures the copy alone, so repoint a source in `onetaskgraph.yaml` or
# through the store's own `ONETASKGRAPH_` variables, which the pre-flight read sees too.
#
# Exit 1 is this recipe's own refusal with nothing written, exit 3 is the destination
# refusing a plan every task of which carried a record, and exit 2 is a plan or a
# toolchain that could not be read at all. A plan builder branches on the difference:
# reading the first as the second is how "nothing has reviewed this" gets retried as an
# outage of the board.
#
# The install line is `just plans`'s, for its reason: session setup runs on a
# `SessionStart` hook that a fresh worktree and a publication clone never fire, so the
# CLI this reads is healed into this checkout's own `.venv/bin` and is the release this
# checkout pinned rather than whichever copy another checkout provisioned last.
# A block rather than the line-scoped directive every sibling recipe here carries, and
# the reason is `just`'s grammar rather than a wider claim: the offending line is the
# `orchestrator-copy-plan` invocation, a line-scoped directive covers only the line
# after it, and `just` refuses a comment between a `[doc(...)]` attribute and the recipe
# it annotates — so the narrowest reachable scope is this recipe. Placed in the body
# instead it would be echoed to the operator on every run, which is the opposite of what
# the rule asks for.
# llmlint: ignore-block[tool_output_is_signal] the store's per-record report — one line per project and task, naming it created, updated or unchanged — is what a copy is run to produce, so it reaches the operator whole, exactly as for the `plans` reader below. This command's own output is only its refusals, each naming the next action.
[doc('Copy a reviewed plan project, and its tasks, onto the plan board this repository launches from.')]
copy-plan *args:
    @./scripts/onetaskgraph-install.sh
    @uv run orchestrator-copy-plan "$@"
# llmlint: ignore-end[tool_output_is_signal]

# Read the next planner surface, with the events that led to it: `just channel-next
# <run-id>`.
#
# Reads through the `planner` profile, which is `onepipeline`'s own default and the
# reason this recipe adds no flag: that profile is the pipeline's own events — node
# and step completion and failure, decisions, planner surfaces, and the monitor and
# check-in updates — and not each dispatched worker's turns. `--filter monitor`
# widens it to the detailed activity stream the monitor member reads, `--filter
# <spec>` takes a filter file or inline JSON, and `--all` reads the store through no
# profile at all. The two are mutually exclusive; the CLI says so.
# llmlint: ignore[tool_output_is_signal] channel-next returns a structured bounded status or a validated transport error for planner recovery.
channel-next *args:
    @./scripts/onepipeline.sh next "$@"

# `just channel-reply <run-id> [FILE]` — the envelope is read from FILE, or from
# stdin when none is named. It carries a legacy verdict, versioned live graph
# edits, or both.
#
# It reaches `onepipeline reply` through `scripts/channel-reply.sh`, which forwards
# the caller's own arguments and adds exactly one refusal: an envelope the run's
# pending blocking question provably cannot use. `delivered` is a transport receipt
# and not a receipt that anybody could read the reply — three envelopes omitting
# `completion` were each reported delivered and each discarded by the asking
# wrapper, leaving a planner blocked for thirty-five minutes with nothing to say so.
#
# The verb's one-line answer therefore comes back carrying `halves` — whether the
# staged envelope held a verdict, and how many edits rode with it — beside `notes`,
# the disposition the engine recorded for each one. The engine answers a single
# `state` word for the whole envelope, so one carrying both halves reports `applied`
# and says nothing about the verdict; `halves` is what this recipe can prove from the
# bytes it staged, and it names what was sent rather than claiming what landed.
# llmlint: ignore[tool_output_is_signal] channel-reply validates the reply and names transport/rendezvous failures so the planner can reattach and retry.
channel-reply *args:
    @./scripts/channel-reply.sh "$@"

# Raise a non-blocking planner status update: `just channel-surface <run-id> [TEXT]`.
channel-surface *args:
    @./scripts/planner-surface.sh "$@"

# The three legacy verdicts, each rendered as the reply envelope
# `onepipeline reply` accepts: `just channel-approve <run-id>`,
# `just channel-reject <run-id> <why>`, `just channel-continue <run-id> <what-next>`.
channel-approve *args:
    @./scripts/planner-verdict.sh approve "$@"

channel-reject *args:
    @./scripts/planner-verdict.sh reject "$@"

channel-continue *args:
    @./scripts/planner-verdict.sh continue "$@"

# Reclaim the dead working directories this host accumulates: `scripts/sweep.sh`
# composes `oneagentgraph sweep` and `onevcs sweep` and adds the trailer naming what
# neither examined. Quiet on success — the two reports and that trailer appear only
# when a verb failed or a family went unexamined, and `--dry-run` always prints them.
# What it still does not reclaim is docs/orchestration.md, "The recorded run".
sweep *args:
    @./scripts/sweep.sh "$@"

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
#  [--body <TEXT> | --body-file <PATH>]`.
#
# The state between the two verbs below: `onevcs recover` is for a branch whose
# provenance is incomplete, and `integrate` is a local merge train that opens no
# change request. A branch that is simply *done* and unpublished had neither of
# those, which is what left an agent reaching for `gh pr create` by hand.
#
# Give it a body. A remote lifecycle publication has its body drafted for it by
# `graphs/pr-author.yaml`; a branch landed by hand has nobody drafting one, and
# every one of them opened with an empty description until onevcs 0.7.0 took a
# caller's. Naming both `--body` and `--body-file` is refused rather than ranked.
#
# `--policy` may narrow the rules-resolved policy but never widen it past requiring
# approvals; the CLI enforces that rather than this wrapper.
#
# This is one of the two commands here that draft a change request's body: the branch
# and `--repo` are read out of the arguments, `scripts/draft-pr-body.sh` writes the
# body, and it reaches `onevcs` as `--body-file`. A caller's own `--body`/`--body-file`
# wins and `--no-draft` skips it; see `scripts/land-branch.sh`.
# llmlint: ignore[tool_output_is_signal] what this verified and where it published the branch — the merge path's verdict, the route taken, and the change request's URL — is the product an operator runs it for, exactly as for the `integrate` train below.
publish-branch *args:
    @./scripts/land-branch.sh publish-branch "$@"

# Verify and publish a lifecycle-preserved branch through its registered workflow,
# attesting the incomplete-step marker it carries.
# `just repo-recover <branch> --repo <canonical-checkout> [--title <T>]
#  [--body <TEXT> | --body-file <PATH>]`.
#
# It takes a body on the same terms `publish-branch` does, and wants one more: a
# branch reached this verb because its workstream died, so what it was doing is
# exactly what no reader can reconstruct from the diff.
#
# This is the incomplete-provenance verb. A branch with nothing left incomplete is
# `just publish-branch`'s; `onevcs recover` takes no `--policy`, because the policy a
# recovered branch publishes under is the one its rules already resolved.
#
# It drafts the change request's body the same way `just publish-branch` does, under
# the same two escapes; see `scripts/land-branch.sh`.
# llmlint: ignore[tool_output_is_signal] what this verified, what it attested, and where it published the branch — the merge path's verdict, the recovered marker, and the change request's URL — is the product an operator runs it for, exactly as for `publish-branch` above.
repo-recover *args:
    @./scripts/land-branch.sh recover "$@"

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
# The branches have to be named. `onevcs integrate` takes a required `<BRANCHES>...`,
# so the auto-discovery this recipe used to do when given none is gone — omitting
# them is a usage error rather than a train over everything outstanding. `just
# recoverable` is where that discovery lives now: it lists every preserved
# unpublished branch and the command that lands each one, and its output is what
# feeds this argument list.
# llmlint: ignore[tool_output_is_signal] the requested readable per-branch train summary is this verb's product.
integrate *args:
    @uv run onevcs integrate "$@"

# List recorded tracked-graph runs, who launched them, and their latest status.
# `just runs --mine` lists only the runs this session launched.
# llmlint: ignore[tool_output_is_signal] the requested multi-line run ledger is this viewing command's product.
runs *args:
    @./scripts/onepipeline.sh runs "$@"

# Stop a run this session launched, tree and all: `just stop <run-id>`. Refuses a run
# another planner launched, or one with no recorded launcher, unless given --force.
# llmlint: ignore[tool_output_is_signal] the ownership refusal and what was stopped are this command's product.
stop *args:
    @./scripts/onepipeline.sh stop "$@"

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

# Register a repository checkout alias. Type and workflow come from the rules file
# the identity matches rather than from flags here; `onevcs` detects the gate the
# checkout itself carries, which is a different thing and not the routing.
register-repo *args:
    @uv run onevcs register "$@"

# List repository identities and checkout aliases.
# `just repos --audit-gate-coverage` also reports, per identity, every required check
# on its merge path — each of which can refuse a merge, and none of which anything on
# this host runs, since onevcs 0.11.0 removed the gate it used to run itself.
# llmlint: ignore[tool_output_is_signal] the requested repo registry listing is this viewing command's product.
repos *args:
    @./scripts/repos.sh "$@"

# Report the policy one repository publishes under, and the rule that decided it:
# `just repo-policy <identity|alias|origin|path>`.
#
# This is the routing answer, and `just repos` is not: the type and workflow that
# listing prints are `onevcs register`'s own derivation from the origin, while what
# a publication actually does comes from the rules file — see `docs/host-setup.md`.
# llmlint: ignore[tool_output_is_signal] the matched rule and the policy that followed are this viewing command's product.
repo-policy *args:
    @uv run onevcs rules check "$@"

# Bring this host's registry up to the tracked repository configuration: register
# every checkout in `config/onevcs.checkouts`, install `config/onevcs.rules.yml` as
# the rules file, and prove every registered checkout matched a rule. Re-runnable —
# run it after editing either file, and on a new host. `--dry-run` changes nothing.
# llmlint: ignore[tool_output_is_signal] the per-checkout resolved policy this prints is what an operator applies the configuration to read.
repos-apply *args:
    @./scripts/apply-repo-registry.sh "$@"

# List recent dispatched worker sessions across every target repo.
# `just history [RUN]` lists one run's records; the argument is a run id rather
# than the record count it used to be.
# llmlint: ignore[tool_output_is_signal] human-readable history is this viewing command's product.
history *args:
    @uv run oneagentgraph history "$@"

# Show one recorded dispatch: `just history-show <record-id>`.
# llmlint: ignore[tool_output_is_signal] the requested multi-line digest is this viewing command's product.
history-show *args:
    @uv run oneagentgraph history show "$@"

# Watch one tracked-graph run as one concise event stream: `just monitor <run-id>`.
#
# Same profiles as `just channel-next`, and the same default: the `planner` profile
# the CLI already applies, which is the pipeline's own events rather than every
# dispatched worker's turns. Widen it with `--filter monitor` (what the monitor
# member reads), narrow it with `--filter <spec>`, or bypass profiles entirely with
# `--all`.
# llmlint: ignore[tool_output_is_signal] the requested continuous event stream is this viewing command's product.
monitor *args:
    @./scripts/onepipeline.sh monitor "$@"

# Watch one run until something a supervisor has to act on happens: `just watch
# <run-id> [OPTIONS]`. AGENTS.md's watch rule states what a watch owes and why this
# exists rather than a loop over `just monitor`.
#
# Its options and terminal exit statuses are deliberately not restated here:
# `scripts/watch-run.sh --print-surface` prints them, and that is what
# `tests/test_watch_surface_drift.py` reconciles against the installed engine. A copy in
# this comment is a copy that gate does not read.
# llmlint: ignore[tool_output_is_signal] Watching a run as it happens is the whole of what this command is for; the per-event and per-heartbeat lines are its product, and the unread-surface count inside a heartbeat is the one signal AGENTS.md forbids filtering out.
[doc('Watch one run, blocking, with heartbeats that carry the unread-surface count; see AGENTS.md and `scripts/watch-run.sh --print-surface`.')]
watch *args:
    @./scripts/watch-run.sh "$@"

# Session timing and usage, for every run or one named run.
# `--breakdown` renders the operator timing view.
telemetry *args:
    @./scripts/onepipeline.sh telemetry "$@"

# Serve the published DAG Observatory bundle against a running read API, proxying
# `/api` and `/healthz` to it so the browser view and its data share one origin.
# `DAG_UI_PORT` and `DAG_UI_API_URL` move either end.
# llmlint: ignore[tool_output_is_signal] the served URL and its request log are the foreground development server's operator-facing product.
dag-ui:
    ./scripts/dag-ui.sh

# Photograph the published DAG Observatory at every viewport in the matrix
# (`scripts/dag-ui-screens.sh`, documented in docs/dag-ui.md) against its own
# throwaway API and UI servers, and print the gallery it wrote. The gallery is per
# invocation and gitignored, so two of these at once neither collide nor leave the
# tree dirty. Extra arguments reach `playwright screenshot`.
dag-ui-screens *args:
    ./scripts/dag-ui-screens.sh "$@"

# Serve the read-only DAG telemetry API, loopback-bound by default.
# `--runs-dir`, `--host`, and `--port` keep working; the wrapper renders them as
# the published `--runs-root` and `--bind`.
# llmlint: ignore[tool_output_is_signal] the requested long-running read API is this command's product.
telemetry-server *args:
    @./scripts/telemetry-server.sh "$@"

# Show a run's live state: what is driving it, and what is running.
# llmlint: ignore[tool_output_is_signal] the requested multi-task status report is this viewing command's product.
status *args:
    @./scripts/onepipeline.sh status "$@"

# Show every live dispatch on this host, with its owner and load contribution.
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

# Report everything onevcs knows about one piece of work:
# `just work-status <change-url|session-token|branch|commit> [--json]`.
#
# This is the one verb that answers "what became of it", and it is the answer a
# settled node's own row cannot give: a run records a node's landing as its
# settlement observed it and nothing re-reads it, so a change that merged an hour
# later still reads as not landed there. Ask this instead of inferring from a run.
# llmlint: ignore[tool_output_is_signal] the requested report on one piece of work is this viewing command's product.
work-status *args:
    @uv run onevcs status "$@"

# Make a branch reachable from an identity's registered checkouts:
# `just import-branch <branch> --repo <checkout> [--from <source>] [--as <name>]`.
#
# The landing verbs read a branch from the publication checkout, never from
# wherever a session happens to be working, so work that only exists in a session
# worktree or a run clone is invisible to them until it is imported. Omitting
# `--from` searches everywhere this identity keeps work.
import-branch *args:
    @uv run onevcs import "$@"

# Fast-forward a publication checkout to its origin: `just sync [BRANCH]`.
sync *args:
    @uv run onevcs sync "$@"

# Scaffold a new persona: `just new-persona <name>`. The published verb writes into
# the working directory, so this runs it in `personas/` — including the
# subdirectory a slash-qualified repo-specific name names.
new-persona *args:
    @./scripts/new-persona.sh "$@"

# Validate every persona against the delta contract (also part of `just check`).
validate-personas *args:
    @uv run oneagentgraph persona validate "${@:-personas}"

# Spend one real harness turn proving prompt delivery and complete history telemetry.
# Kept out of `gate`; pre-push selects it only for launch-path changes.
#
# `scripts/smoke.sh` is not ceremony around the published verb: `oneagentgraph
# smoke` runs plain `oneharness` against a config it generates itself, and it takes
# the caller's `ORCHESTRATOR_AGENT_STATUS_DIR` as-is. The wrapper supplies this
# repository's agent harness and an isolated status directory, which is what stops a
# smoke run from inside a dispatch — where the pre-push hook runs it — from
# hijacking that dispatch's own liveness protocol. See the script's header.
smoke *args:
    @./scripts/smoke.sh "$@"

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
# llmlint: ignore[tool_output_is_signal] The selected query's result is this viewing command's product.
plans *args:
    ./scripts/onetaskgraph-install.sh
    ./.venv/bin/onetaskgraph {{args}}

# --- llmlint (LLM-judge tier) --------------------------------------------
# Non-deterministic, harness-backed, and kept OUT of `just check`. It runs at
# pre-push (.githooks/pre-push) and on demand. Config is the composed llmlint.yml.

# Install/refresh the llmlint toolchain (llmlint + its oneharness). Idempotent.
setup-llmlint:
    ./scripts/setup-llmlint.sh

# LLM-judge lint over the whole tree (or pass paths to narrow). Run once with
# `just setup-llmlint` first in a plain terminal.
#
# `role=llmlint` stamps this tier's own harness sessions so they are separable from
# the agent/judge sessions of the work being linted (whose roles come from
# oneharness.toml / oneharness.judge.toml). It is layered over — not substituted
# for — the graph labels inherited when a dispatched agent runs its own gate, so a
# finding stays attributable to the run and node that provoked it.
lint-llm *paths:
    @command -v llmlint >/dev/null 2>&1 || { echo "llmlint not installed — run 'just setup-llmlint'"; exit 1; }
    @PATH="{{repo_root}}/.venv/bin:$PATH" LLMLINT_ONEHARNESS_BIN="{{repo_root}}/scripts/llmlint-oneharness.sh" ONEHARNESS_HISTORY_LABELS="$(uv run orchestrator-history-labels role=llmlint)" llmlint "$@"

# Deterministic, model-free llmlint gate: config structure, ignore directives name
# real rules, edited fragments bumped their version. The fast pre-flight.
lint-llm-validate *args:
    @command -v llmlint >/dev/null 2>&1 || { echo "llmlint not installed — run 'just setup-llmlint'"; exit 1; }
    @llmlint validate "$@"

# llmlint scoped to the merge-base diff with main — judges only what the branch
# changed. This is the blocking pre-push check.
#
# The judge is non-deterministic, so the cached Nx `workspace:lint-llm-diff` target
# caches the judge run itself: an unchanged tree judged against an unchanged base
# replays that run's own `-v` report — every rule itemized, plus the
# `llmlint history <id>` pointer — instead of rolling the dice again. There is no
# verdict record to write, restore, or race on; Nx's task cache is the whole
# mechanism. The base ref is resolved to a commit here, before Nx hashes it, so a
# rebased or advanced base misses rather than replaying a verdict computed against
# a different base. The resolved base commit is reported with the verdict, because
# "green" means green *against that commit*: a gate run and the publication rebuild
# that judge different base commits are answering different questions.
#
# A base its **own origin ref has moved past** is refused before any of that, naming
# both refs and both commits — `scripts/base-freshness.sh` decides it. A stale name
# resolves silently and keys the cache perfectly well, so nothing downstream can see
# the disagreement: what comes back is a valid verdict over the wrong range, and a
# worker inside a session clone naming `main` gets one by default. Only strictly
# behind is refused; a base with no origin ref of its own, one level with it, one
# ahead of it, and one that has diverged from it are all judged as before.
#
# Only a clean run is cached, because Nx caches successful tasks only. Findings
# (llmlint exit 1) and a toolchain that never reached a verdict (exit >= 2) both
# re-judge on the next invocation. That is the deliberate trade for deleting the
# record/replay protocol this tier used to smuggle failures through Nx with: a red
# costs a fresh roll every time, and every roll lands in `llmlint history`. A wrong
# *green* still sticks until the tree, the base commit, or the judge configuration
# moves — `--skip-nx-cache` re-judges but neither reads nor writes the cache under
# this Nx, so the next ordinary run replays the same stale entry.
#
# `just lint-llm-diff <base> --skip-nx-cache` is the one supported way to force a
# real re-judge, and it is deliberately per-invocation. An ambient global Nx cache
# skip (`NX_SKIP_NX_CACHE` / `NX_DISABLE_NX_CACHE`, exported to re-judge this tier
# and inherited by everything else) is reported and ignored here: it would re-roll
# a non-deterministic judge from every unrelated command, and it silently breaks
# the checks whose contract is cache replay — the llmlint verdict-replay journeys
# and `scripts/check-nx-cache.sh`. Every other Nx target still honours it.
#
# Provenance comes from Nx's own cache reporting: the task line it annotates, or the
# summary line it prints, only when it replayed a task instead of running it. Both
# are matched because only the first is safe at any size — Nx replays a hit as one
# burst and exits, so a replay larger than a pipe buffer arrives here truncated and
# its summary never does. `tests/e2e/test_llmlint_cache_e2e.py` asserts both the
# judged and the replayed wording, so an Nx upgrade that renames them fails the suite
# rather than quietly reporting every run as freshly judged. Nx's full output is
# shown because it *is* the report now.
lint-llm-diff base="origin/main" *nx_args:
    @command -v llmlint >/dev/null 2>&1 || { echo "llmlint not installed — run 'just setup-llmlint'"; exit 1; }
    @# llmlint: ignore[tool_output_is_signal] The judge's per-rule report and its one-line provenance are this tier's product; a quiet success here would delete the tier's result and leave a replayed run saying less than a fresh one. `@#` so the directive itself stays out of that report.
    @base_sha=$(git rev-parse --verify --quiet "{{base}}^{commit}") || { echo "lint-llm-diff: '{{base}}' does not resolve to a commit; fetch it or pass an existing base" >&2; exit 1; }; behind=$(./scripts/base-freshness.sh "{{base}}") || { echo "lint-llm-diff: whether '{{base}}' still matches its own origin ref could not be decided; run 'scripts/base-freshness.sh {{base}}' to see why" >&2; exit 1; }; if [[ -n "$behind" ]]; then echo "lint-llm-diff: $behind, so judging it would judge commits this branch does not carry; fetch and fast-forward '{{base}}', or name the origin ref as the base" >&2; exit 1; fi; if [[ -n "${NX_SKIP_NX_CACHE:-}${NX_DISABLE_NX_CACHE:-}" ]]; then echo "lint-llm-diff: ignoring the ambient global Nx cache skip; force a fresh judgement of this tier alone with 'just lint-llm-diff {{base}} --skip-nx-cache'" >&2; fi; unset NX_SKIP_NX_CACHE NX_DISABLE_NX_CACHE; report=$(mktemp) || { echo "lint-llm-diff: could not open temporary storage for the judge report; free disk space and retry" >&2; exit 1; }; trap 'rm -f "$report"' EXIT; status=0; LLMLINT_DIFF_BASE_SHA="$base_sha" AI_ORCHESTRATOR_NX_SHOW_OUTPUT=1 ./scripts/nx.sh run workspace:lint-llm-diff {{nx_args}} >"$report" 2>&1 || status=$?; cat "$report"; if grep -qE '^Nx read the output from the cache instead of running the command|^> nx run workspace:lint-llm-diff +\[(local cache|remote cache|existing outputs match the cache)' "$report"; then echo "lint-llm-diff: replayed the recorded verdict for base $base_sha (Nx cache hit)" >&2; else echo "lint-llm-diff: judged this diff against base $base_sha (Nx cache miss)" >&2; fi; exit "$status"
