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
# already performs: bootstrap re-applies the lockfile even when a stale
# `node_modules` is present, which is exactly what a refreshed lockfile needs.
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
# llmlint: ignore[changed_behavior_has_e2e] The public recipe is the real deterministic gate invoked by this task and pre-push; its sequencing failures use subprocess doubles to avoid recursively invoking the same full suite.
check:
    @source ./scripts/preserved-log.sh; preserved_log_open "{{repo_root}}" check; log=$PRESERVED_LOG; { ./scripts/nx.sh run-many -t format-check,lint,typecheck,test,test-docs,test-recipes,coverage && ./scripts/nx.sh run workspace:check-nx-cache; } 2>&1 | redact_secrets >"$log" || { cat "$log" >&2; echo "check: deterministic checks failed; fix the reported findings and retry (full output: $log)" >&2; exit 1; }; total=$(./scripts/coverage-total.sh "{{repo_root}}"); echo "check: all deterministic checks passed${total:+ (line coverage ${total}%)}"

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
    @log=$(mktemp); trap 'rm -f "$log"' EXIT; ./scripts/nx.sh run-many -t test,test-docs,test-recipes,coverage {{nx_args}} >"$log" 2>&1 || { cat "$log" >&2; echo "test: suites failed; fix the reported findings and rerun 'just test'" >&2; exit 1; }; echo "test: all suites passed"

# The e2e suite alone (the real recipes, wrappers, and harness CLI) — quick inner loop.
#
# Same worker count and distribution as the `test` tier, for the same reason: the
# journeys wait on subprocesses rather than compute, so the wall clock is latency
# and the workers are nearly free.
test-e2e:
    # llmlint: ignore[tool_output_is_signal] Watching one suite run as it goes is the only thing this recipe is for; `just test` is the one that reduces a green run to a line.
    @uv run pytest tests/e2e -n 4 --dist loadgroup

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
    @source ./scripts/preserved-log.sh; preserved_log_open "{{repo_root}}" upgrade; log=$PRESERVED_LOG; { uv lock --upgrade && uv sync && bun update --latest nx playwright typescript@6 && ./scripts/nx.sh run-many -t build,lint,typecheck,test,test-docs,test-recipes,coverage; } 2>&1 | redact_secrets >"$log" || { cat "$log" >&2; echo "upgrade: repair dependency constraints or target findings and retry (full output: $log)" >&2; exit 1; }; echo "upgrade: dependencies refreshed and targets passed"

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

# Launch the dedicated orchestrator with a host-visible live planner channel, then
# stay attached: it streams what `just monitor` streams and returns when the run
# settles — the graph completed, a blocking planner surface is waiting, or nothing
# is driving the run (exit 3). `--detach` returns at the launch record instead,
# for a long unattended run. Either way the run leads its own session, so Ctrl-C
# detaches rather than stopping it.
#
# `just orchestrate --adopt <run-id>` is `onepipeline adopt`: the published surface
# splits adoption into its own verb, and this recipe keeps the one spelling the
# planner doctrine names. `--adopt` has to lead, because everything after it is the
# adopt verb's own.
#
# The per-side `--worker-harness` / `--judge-harness` / `--worker-model` /
# `--judge-model` flags have no successor on `onepipeline start`: which harness and
# model each side of the conversation runs on is now a property of the run's agent
# graph, overridden per run with `oneagentgraph run --set`.
# llmlint: ignore[tool_output_is_signal] orchestrate reports validated launch failures and the caller can retry after correcting the named input.
[doc('Launch the orchestrator on a live planner channel and stay attached until the run settles (`--detach` returns at the launch record); `--adopt <run-id>` attaches a fresh driver to an intact ledger.')]
orchestrate *args:
    @if [[ "${1:-}" == "--adopt" ]]; then ./scripts/onepipeline.sh adopt "${@:2}"; else ./scripts/onepipeline.sh start "$@"; fi

# Execute the current round of a launched run: `just run-plan <run-id>`.
#
# This is the engine verb the orchestrator member drives, not the plan launcher it
# used to be. A plan file is launched by `just orchestrate`, which starts the run
# and the driver that calls this; a plan file passed here is not a run id and the
# CLI says so.
[doc('Execute the current round of a launched run: `just run-plan <run-id>`. A plan file is launched with `just orchestrate`.')]
run-plan *args:
    @./scripts/onepipeline.sh round run "$@"

# Transition a run to its next round, folding the last round's results and the
# planner edits accepted while it ran: `just next-round <run-id>`.
# llmlint: ignore[tool_output_is_signal] the tracked result and continuation guidance are this command's product.
next-round *args:
    @./scripts/onepipeline.sh round next "$@"

# Bounded host-side reads and replies for the live planner channel.
# llmlint: ignore[tool_output_is_signal] channel-next returns a structured bounded status or a validated transport error for planner recovery.
channel-next *args:
    @./scripts/onepipeline.sh next "$@"

# `just channel-reply <run-id> [FILE]` — the envelope is read from FILE, or from
# stdin when none is named. It carries a legacy verdict, versioned live graph
# edits, or both.
# llmlint: ignore[tool_output_is_signal] channel-reply validates the reply and names transport/rendezvous failures so the planner can reattach and retry.
channel-reply *args:
    @./scripts/onepipeline.sh reply "$@"

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

# Reclaim the scratch `oneagentgraph` itself produces. Pass `--dry-run` to inspect
# candidates without removing them, `--min-age-hours` to move the stale threshold.
#
# This reclaims less than the name suggests, which is worth knowing before trusting
# it with a full disk: it sweeps the two families that engine owns (`runs`, `temp`)
# and reaps no processes. The volume families a dispatch leaves — the per-invocation
# `nx` install, Nx's per-worktree native binary, pytest run directories, onejudge
# scratch — and a leaked worker are nobody's to collect here. Not papered over with a
# second sweeper beside this one: two cleaners racing one directory is worse. See
# docs/orchestration.md, "Recorded rounds".
sweep-scratch *args:
    @uv run oneagentgraph sweep "$@"

# Verify and publish a lifecycle-preserved branch through its registered workflow.
# `just repo-recover <branch> --repo <canonical-checkout>`.
repo-recover *args:
    @uv run onevcs recover "$@"

# The across-round derivation this recipe used to print is no longer a verb of its
# own: `onepipeline round next` folds the last round's results and the accepted
# planner edits into the next round itself, so the plan of record is derived where
# it is executed rather than in a separate file-in/file-out step. `just next-round`
# is that verb. This recipe stays only to say so.
replan *args:
    @echo "replan: the next round is derived by the round transition itself — run 'just next-round <run-id>'; there is no standalone derive-and-print verb on the published surface" >&2; exit 2

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

# Register a repository checkout alias. Type, workflow, and gate come from the
# rules file the identity matches rather than from flags here.
register-repo *args:
    @uv run onevcs register "$@"

# List repository identities and checkout aliases.
# `just repos --audit-gate-coverage` also reports which identities have merge-path
# verification and which do not.
# llmlint: ignore[tool_output_is_signal] the requested repo registry listing is this viewing command's product.
repos *args:
    @uv run onevcs repos "${@/--audit-gate-coverage/--audit-gates}"

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
# llmlint: ignore[tool_output_is_signal] the requested continuous event stream is this viewing command's product.
monitor *args:
    @./scripts/onepipeline.sh monitor "$@"

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
# llmlint: ignore[tool_output_is_signal] the requested recovery inventory is this viewing command's product.
recoverable *args:
    @uv run onevcs recoverable "$@"

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
# finding stays attributable to the run/round/node that provoked it.
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
    @base_sha=$(git rev-parse --verify --quiet "{{base}}^{commit}") || { echo "lint-llm-diff: '{{base}}' does not resolve to a commit; fetch it or pass an existing base" >&2; exit 1; }; if [[ -n "${NX_SKIP_NX_CACHE:-}${NX_DISABLE_NX_CACHE:-}" ]]; then echo "lint-llm-diff: ignoring the ambient global Nx cache skip; force a fresh judgement of this tier alone with 'just lint-llm-diff {{base}} --skip-nx-cache'" >&2; fi; unset NX_SKIP_NX_CACHE NX_DISABLE_NX_CACHE; report=$(mktemp) || { echo "lint-llm-diff: could not open temporary storage for the judge report; free disk space and retry" >&2; exit 1; }; trap 'rm -f "$report"' EXIT; status=0; LLMLINT_DIFF_BASE_SHA="$base_sha" AI_ORCHESTRATOR_NX_SHOW_OUTPUT=1 ./scripts/nx.sh run workspace:lint-llm-diff {{nx_args}} >"$report" 2>&1 || status=$?; cat "$report"; if grep -qE '^Nx read the output from the cache instead of running the command|^> nx run workspace:lint-llm-diff +\[(local cache|remote cache|existing outputs match the cache)' "$report"; then echo "lint-llm-diff: replayed the recorded verdict for base $base_sha (Nx cache hit)" >&2; else echo "lint-llm-diff: judged this diff against base $base_sha (Nx cache miss)" >&2; fi; exit "$status"
