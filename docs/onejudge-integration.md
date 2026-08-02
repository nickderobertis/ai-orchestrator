# onejudge integration

How this repo calls [onejudge](https://github.com/nickderobertis/onejudge) and how
the two conversation sides are wired.

This repository adopts the exact onejudge version declared in
`config/onejudge.version` and pins that version of the PyPI distribution
`onejudge`. The distribution exposes the Python SDK as `onejudge_sdk` and depends
on the matching `onejudge-cli` wheel. `just bootstrap` verifies both the SDK import
and the resolved CLI's exact `onejudge --version` output; setup exits non-zero
unless the installed distribution and CLI match the declaration.

## The layering

```
oneharness  →  one harness invocation, one JSON report   (WHICH harness + model)
onejudge    →  simulated-user loop that drives a task to completion  (this repo calls it)
```

`onejudge run` points a harness at a task and lets an LLM-driven **simulated user
supervise** it — pushing back, asking for verification, re-prompting — until a
`done_when` condition holds or `max_turns` is hit. That supervision is what lets a
subtask reach "actually done" instead of "the agent said done."

## The two sides of the conversation

A onejudge run is a two-party conversation. Harness/model selection for each side
lives in an oneharness config, not in onejudge:

| Side | Role | Config file |
| --- | --- | --- |
| **Agent** | does the work | `oneharness.toml` (discovered from the repo root) |
| **Judge / simulated user** | supervises + scores | `oneharness.judge.toml` (via the base config's `provider.judge_config`) |

A third role sits above both: the **orchestrator** process `just orchestrate`
launches drives a tracked graph rather than doing the work, so it has its own
config, `oneharness.orchestrator.toml`, forced by
`scripts/oneharness-orchestrator.sh`.

Edit those files (or use oneharness's `ONEHARNESS_*` env overrides) to change
the harness or model on a side for every run on this host; pass
`--worker-harness` / `--judge-harness` to change it for one dispatch, which is
the only way to give the two sides *different* providers (see [Choosing a harness
per side](#choosing-a-harness-per-side)). `config/onejudge.base.yaml` carries only
the loop's own concerns (persona defaults, session), never harness/model selection.

## Provider wiring

This repository uses three onejudge provider arrangements:

- `oneharness` is the live worker path. Its agent and simulated-user sides use
  the two harness configs above.
- `command` is the deterministic test path. A local JSON-lines process stands in
  for the paid harness boundary.
- The live orchestrator uses a `split` provider: its `skill` side is either the
  configured oneharness provider — with its `bin` pinned to
  `scripts/oneharness-orchestrator.sh`, since a launch has no `--project-dir` to
  pin it through — or a command provider, while its `judge` side is
  a command invoking `orchestrator.channel.relay_supervisor`. The relay forwards
  supervisor requests over the run's FIFOs to the live planner and returns the
  planner's completion or continuation reply to onejudge. Final boolean/score
  judge calls mirror the persisted planner verdict; the launch removes standalone
  model evals and assessment because the live planner is completion authority.

`orchestrator.dispatch.launch_orchestrator` creates this split config and launches
`onejudge run` with a detached `subprocess.Popen`. This wiring is specific to the
orchestrator persona; worker dispatch retains its ordinary oneharness or command
provider and simulated-user loop.

## How a persona becomes a run

```
config/onejudge.base.yaml   (shared: provider, agent preamble, defaults)
        ⊕
personas/<name>.yaml or personas/<repo>/<name>.yaml
                            (delta: agent role + supervisor persona)
        ⊕
task "<the subtask>"         (passed to the SDK, never merged into a file)
        ↓  orchestrator.config.build_effective_config
effective config object     →  onejudge_sdk.OneJudge.run   →  validated RunResult
```

Repo-specific personas are addressed by their slash-qualified catalog name, such
as `crozier/crozier-corpus`; general cross-repo roles retain top-level names.

`dispatch` passes the effective config object and task to
`onejudge_sdk.OneJudge.run`. The SDK owns the temporary config, stdin task
transport, CLI invocation, and report-contract validation; the orchestrator maps
its typed `RunResult` into the existing `Report`. Exit codes mirror onejudge: `0`
completed, `1` incomplete, and `2` bad config or provider/runtime failure (raised
as a `DispatchError` with onejudge's stderr). The adopted version emits report
schema v4, including the unified supervisor's completion reason and
the optional final `assessment` this repository uses to surface follow-up work.

## The init process

`onejudge init` scaffolds all three files — `oneharness.toml`,
`oneharness.judge.toml`, and a starter `onejudge.yaml` — by shelling out to
`oneharness init`. The adopted exact release lives in
`config/oneharness.version`; `just bootstrap` installs and verifies that version:

```sh
onejudge init --force    # writes the two oneharness configs + a starter onejudge.yaml
onejudge schema          # the annotated, authoritative config reference
```

<!-- llmlint: ignore[contracts_have_one_source_or_a_drift_gate] This prose explains the user-facing routing contract; the TOML files remain authoritative and existing integration tests validate their selections. -->
The committed configs are **that init output** with deliberate routing edits:
the worker prefers an alternate Claude subscription, the judge prefers Codex and
can fall back only to the primary Claude subscription, and both add an
`IS_SANDBOX` env so claude-code runs under root. init's starter
`onejudge.yaml` is not kept — `config/onejudge.base.yaml` supersedes it as the base
this repo merges personas onto.

The committed base and adapter follow the adopted onejudge schema: persona-authored
`agent.instructions` is internal ai-orchestrator vocabulary and is translated to
onejudge's `system_prompt`; no obsolete onejudge `agent` block reaches the CLI.
The real-CLI e2e suite checks these schema and CLI surfaces before it drives the
same SDK-to-CLI path used in production dispatch.

**Getting the adopted oneharness on this box.** The prebuilt oneharness
release binary needs a newer glibc than the host provides, and the crates.io build
lags behind the 0.3.x releases that added `init`. The **PyPI `oneharness-cli`
wheel** (a manylinux build) is the one that both runs on the host's glibc and
carries `init`, so `scripts/session-setup.sh` installs the exact
`config/oneharness.version` release and rejects a stale binary. Version 0.6.3 is
the adopted release; it contains auth variants shipped in 0.5.6 and succeeds
0.3.24, the first release to carry the
process-tree timeout and partial telemetry fix from
[oneharness PR #1147](https://github.com/nickderobertis/oneharness/pull/1147),
so that fix stays in effect. It is also the floor for **quota fallthrough on
Codex**: before
[oneharness PR #1208](https://github.com/nickderobertis/oneharness/pull/1208),
Codex declared an exhausted account as a `turn.failed` event on stdout that no
classifier read, so `failure_kind` stayed unset and a chain stopped dead at the
exhausted identity instead of reaching the next one — the `codex:alternate`
fallback below was unreachable exactly when it was needed. v0.6.2 had fixed only
the Claude side of the same gap.

**Watch for a stale cargo oneharness.** An earlier `cargo install oneharness`
leaves a 0.2.x binary in `~/.cargo/bin`; its `run` lacks `--mode`, which onejudge's
oneharness provider needs. If it precedes the wheel on `PATH`, live dispatch dies
with a confusing `provider error ... Broken pipe` (the harness process rejects the
flags and exits before the prompt is written). `session-setup.sh` removes it once
the wheel is installed; keep `~/.local/bin` ahead of `~/.cargo/bin` regardless.

## Harnesses and the live path

Live dispatch drives a real harness, chosen by `oneharness.toml`'s fallback chain.
**Every role names the same five identities** — `claude-code:alternate`,
`claude-code:alternate2`, `codex`, `codex:alternate`, `claude-code:primary` — and
the roles differ only in the order they try them. The worker order is that list as
written: both alternate Claude subscriptions first, because the personas are tuned
against that model tier, then Codex, then the primary Claude identity as the last
resort. A variant is a named per-harness preset selected as `<harness>:<variant>`;
it composes the base harness settings with child-only model, environment, and
credential routing.
`scripts/claude-alt-config-dir.sh` is the one source of BOTH alternate config
directories: it derives `ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR` as `$HOME/.claude-alt`
and `ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR` as `$HOME/.claude-alt2` unless the caller
overrides them, and every wrapper — agent, orchestrator, and llmlint — sources it,
because oneharness refuses to start whenever a named variant's `env_from` source is
unset in the parent. Each variant maps its portable path to `CLAUDE_CONFIG_DIR`
only inside its own child and masks ambient Anthropic API/OAuth credentials so they
cannot outrank subscription auth. If one directory is absent, unauthenticated, or
quota-limited, fallback proceeds to the next candidate; a host with only its
primary Claude identity therefore still dispatches through an authenticated Codex.

### The second Codex identity

Every role's chain names `codex:alternate`, a second Codex account that absorbs
an exhausted quota without changing which subscription that role competes for.
`scripts/codex-alt-home.sh` is its one source, the counterpart of the Claude helper
above: it derives `ORCHESTRATOR_CODEX_ALT_HOME` as `$HOME/.codex-alt` unless the
caller overrides it, and all three wrappers — agent, orchestrator, and llmlint —
source it. The variant maps that portable path to `CODEX_HOME` only inside the
alternate child and unsets `OPENAI_API_KEY`, which would otherwise outrank the
ChatGPT tokens `codex login` writes there and silently bill the wrong account.

Authenticate the second account with `CODEX_HOME="$HOME/.codex-alt" codex login`
(check it with `codex login status` under the same variable). Until then the
candidate simply costs nothing, because the helper guarantees the **directory
exists**: oneharness distinguishes the two "not set up yet" states, and only one
degrades. A `CODEX_HOME` that exists but holds no credentials is classified
`failure_kind: "auth"` and falls through to the next harness; a `CODEX_HOME` that
does not exist at all is an unclassified hard failure that falls through to
nothing. An empty directory is therefore what makes the committed chains safe on a
host with one Codex login, and it is exactly where the login above writes.
`--exclude` cannot stand in for this — it filters only `--all`, never an explicit
`harnesses` chain — and oneharness refuses to start whenever the indirection is
unset in the parent, which is why every wrapper exports it rather than only the
roles that expect to reach the candidate.

### The second alternate Claude subscription

`claude-code:alternate2` is a second Claude *subscription*, credentialed from its
own `$HOME/.claude-alt2` config directory. It exists for quota headroom: when the
first alternate plan hits its usage limit mid-run, the worker reaches a second
Claude account rather than degrading to Codex, so output stays on the tier the
personas were tuned against. Authenticate it with `CLAUDE_CONFIG_DIR="$HOME/.claude-alt2" claude`
and `/login` inside that session.

Unlike `codex:alternate`, this candidate needs **no directory to be created for
it**, and the helper deliberately creates none. Probed on this host against both
states — an absent directory and an empty one — claude-code reported the same
thing:

```
"text": "Not logged in · Please run /login",
"failure_kind": "auth",
oneharness: no selected harness could be run — all 1 fallback candidate(s) failed
to start (claude-code:alternate2 [auth]); nothing executed
```

`auth` is the classification that falls through to the next candidate, so an
unauthenticated second plan is safe and free in every committed chain exactly as an
unauthenticated `codex:alternate` is. (claude-code also creates its config
directory itself on first run, which is why there is nothing to pre-create — and
why `scripts/oneharness-agent.sh` still substitutes a chain without the absent
alternates: dropping them keeps a dispatch from leaving a config directory on disk
for an account nobody has logged into. It drops **only** those candidates, reading
the rest from `oneharness.toml` so the degraded chain is always a subsequence of
the committed one.)

The **orchestrator** reverses the worker's order. It is a long-lived supervisory
process, not a worker, so `oneharness.orchestrator.toml` selects both Codex
identities first: it does not stand in front of the workers for the subscriptions
they depend on while Codex can still carry the role. Past that it does reach them,
in the worker's own relative order, and the operator accepts that trade —
contending for the workers' Claude quota beats stalling a supervisory process every
workstream waits on.
`launch_orchestrator` pins `scripts/oneharness-orchestrator.sh` as the launched
process's oneharness binary, which forces that config (upward discovery from the
repo root would find the worker chain) and exports the same shared
`ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR` and `ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR`
defaults — so `just orchestrate` launches on a
fresh shell with nothing exported by hand. That launch also forwards
`--oneharness-mode` (default `bypass`) as `ONEHARNESS_MODE`, like every other
dispatch entry point; without it the orchestrator runs at claude-code's
non-interactive default, which denies outright every command outside
`.claude/settings.json` — including the `just monitor` its own persona mandates.

The **judge** leads with Codex for the same reason and, past both Codex
identities, now reaches the workers' alternate subscriptions before
`claude-code:primary`. It keeps its cheaper-supervisor intent through `model`
rather than through isolation: all three of its Claude variants are
`claude-sonnet-5`, where every other role uses `claude-opus-5`. The
`claude-code:primary` variant removes `CLAUDE_CONFIG_DIR` and higher-precedence
Anthropic credentials, selecting Claude's default `$HOME/.claude` identity and
never an alternate account.

**llmlint** uses `oneharness.llmlint.toml` through
`scripts/llmlint-oneharness.sh`, and is **no longer Codex-only**: it carries the
same five identities in the same supervisory order. That trade is deliberate — a
blocking pre-push check that can still be judged beats one isolated from the Claude
quota that is left. It does mean this tier can contend for the workers'
subscriptions once both Codex accounts are exhausted.

One thing had to move for that. The wrapper used to append Codex's sandbox grant as
a trailing `-- -c 'sandbox_permissions=[...]'`, and oneharness appends a trailing
`HARNESS_ARG` to **whichever** harness fallback selects. On claude-code, `-c` is
`--continue` — a boolean — so the permission string would be swallowed as a
positional prompt and silently replace the lint prompt, producing a confident
verdict on the wrong input rather than an error. The grant now lives in
`[harness.codex] args` in `oneharness.llmlint.toml`, where it reaches both Codex
identities and no Claude one. `tests/test_llmlint_oneharness_wrapper.py` proves
that at the real oneharness boundary.

### Choosing a harness per side

The two sides are different jobs, and the providers are not equally good at them —
one can be the stronger author while another is the stronger reviewer. Say so per
dispatch:

```sh
just dispatch engineer "…" --worker-harness codex --judge-harness claude-code:alternate
just repo-task <repo> engineer "…" --worker-harness codex:alternate --judge-harness codex
just orchestrate plan.json --worker-harness codex --judge-harness claude-code:alternate2
```

Both flags take an identity exactly as a config's `harnesses` chain writes one,
comma-separated for a fallback chain of the operator's own. `just run-plan` and
`just repo-plan` take them too, since they share the lifecycle option group.

Neither flag is oneharness's `ONEHARNESS_HARNESSES`, and that is the whole point:
that variable is process-wide **and beats config**, so exporting it to move the
worker onto codex silently moves the judge there as well.

```
$ printf 'harnesses = ["claude-code:alternate2"]\n' > /tmp/prec.toml
$ ONEHARNESS_HARNESSES=codex oneharness run --config /tmp/prec.toml --print-command --prompt hi
  selected: codex
```

Each flag instead sets its own variable — `ORCHESTRATOR_WORKER_HARNESSES` or
`ORCHESTRATOR_JUDGE_HARNESSES` — and `scripts/oneharness-agent.sh` resolves them
per branch: the agent turn (no `--config` in its args) reads the worker one, the
judge turn (already carrying `--config <judge_config>`) reads the judge one, and
each is applied to only that branch's own `exec`. A side carrying an explicit value
therefore never inherits the other side's, nor an ambient process-wide one the
parent exported. `just orchestrate` carries the pair in the launched process's
environment, so every round's workers and judges inherit the same choice.

`orchestrator/harnesses.py` validates each value against **that side's** config
before anything is dispatched, and names every selectable identity when it refuses:

```
$ just dispatch engineer "…" --worker-harness opencode
dispatch: --worker-harness 'opencode': 'opencode' is not a harness oneharness.toml
configures; select from claude-code:alternate, claude-code:alternate2, codex,
codex:alternate, claude-code:primary
```

A run that quietly used a different provider than it was told to is worse than one
that refused to start — which is also why an override is taken **verbatim**: the
absent-alternate substitution above narrows a chain nobody chose, but dropping an
identity an operator named would run a provider they did not ask for. An
unauthenticated one falls through, or fails the dispatch when it is the only
candidate, and either way says so.

With neither flag set nothing changes: each side resolves its own config chain, and
the agent branch still substitutes a chain without an absent alternate Claude
identity. The other two roles are out of scope — `oneharness.orchestrator.toml` and
`oneharness.llmlint.toml` keep resolving through their own wrappers, untouched by
either variable.

To address an identity explicitly in a diagnostic run, use the composed id:

```sh
ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR="$HOME/.claude-alt" \
  oneharness run --config oneharness.toml \
  --harness claude-code:alternate --prompt "Reply with OK"
ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR="$HOME/.claude-alt2" \
  oneharness run --config oneharness.toml \
  --harness claude-code:alternate2 --prompt "Reply with OK"
oneharness run --config oneharness.judge.toml \
  --harness claude-code:primary --prompt "Reply with OK"
ORCHESTRATOR_CODEX_ALT_HOME="$HOME/.codex-alt" \
  oneharness run --config oneharness.toml \
  --harness codex:alternate --prompt "Reply with OK"
```

A `codex:alternate` or `claude-code:alternate2` probe that reports `fell_through:
[{"harness": "...", "reason": "auth"}]` is the unauthenticated state, not a broken
config; run the `codex login` above, or for the second Claude plan:

```sh
CLAUDE_CONFIG_DIR="$HOME/.claude-alt2" claude
```

then `/login` in that session.

The offline gate needs neither identity; the
timeout e2e gate drives the adopted oneharness with a local fixture, and each live
harness has an **environment requirement** for its tools to actually execute:

- **codex** runs as its own process and executes tools directly, so it remains the
  worker fallback and judge primary. It sandboxes via **bubblewrap**,
  which needs **unprivileged user namespaces**; where the host disallows them (e.g.
  Ubuntu's AppArmor `restrict_unprivileged_userns`), codex can't create its sandbox
  and falls back to read-only. **The fix is `--oneharness-mode bypass`** (codex's
  `--dangerously-bypass-approvals-and-sandbox`): no OS sandbox, so writes work
  regardless of the kernel. To keep a guardrail in place of the sandbox, wire
  **allowlister** as codex's PreToolUse hook with the `repo-write` profile
  (`scripts/session-setup.sh` does this): it auto-allows repo edits and the
  project's build/test, and holds dangerous commands (`rm -rf`, force-push,
  publish) for approval. Verified live end-to-end: a dispatched agent created and
  verified a file in its `--project-dir`, gated by allowlister.
- **claude-code** works standalone, but **inside a bridged/managed Claude Code
  session its nested tool calls are deferred to the outer controller**
  (`stop_reason: tool_deferred`) and never execute — so an orchestrator running
  *inside* such a session cannot dispatch tool-using claude-code agents. Run the
  orchestrator from a standalone shell (or CI), or use codex per above.

Run `just smoke` for a minimal paid check of this seam. It sends one trivial
prompt through the real `scripts/oneharness-agent.sh` heartbeat branch to the
fallback-selected real harness, using a temporary target and history store. The
command requires exactly one agent turn, then checks that the stored prompt is
non-empty and byte-for-byte equal to the dispatched task and that the selected
harness wrote a record satisfying the launch contract: a supported schema, a named
harness, a successful status and exit, a measured duration, and well-formed input
and output token counts.

Native per-phase telemetry is deliberately *not* required. oneharness normalizes
whatever each harness reports and leaves the rest unset, and neither shipped
harness reports all of it: codex records `started_at`/`model_ms`/`tool_ms` but
prices nothing and reports no cache-write count, while claude-code prices its turn
but records only a measured duration, with `started_at` absent and `finished_at`
null. Requiring `validated_native_fields` here failed every healthy claude-code
dispatch, so native-timing completeness stays a telemetry-*quality* signal and the
launch guard checks only what a launch must produce.
Counters and timings that *are* present are still validated, so a malformed or
contradictory record still fails. Its quota cost is one real harness invocation;
the provider may leave dollar cost unreported (Codex does). It is not part of
`just gate`.

The *launch* — and only the launch — is retried, up to
`orchestrator.smoke.LAUNCH_ATTEMPTS` times with a short backoff between attempts.
This is the half of the check the host can break while nothing is wrong with the
launch path: under a concurrent e2e load, oneharness has reported `fallback harness
… ran but did not succeed` for a harness that started and then died, and the same
command passed standalone moments before and after. Since the pre-push hook selects
this smoke whenever the pushed diff touches `scripts/`, the worker generating that
load is usually the one whose publication it blocks. Nothing is relaxed by
retrying: a launch path that is genuinely broken fails every attempt and still
fails, a recorded turn that violates the contract above fails on the first attempt
without paying for a second, and a passing run reports how many launches it took.
`tests/e2e/test_smoke_contention_e2e.py` drives the real recipe under a live load
of real dispatches, with only the paid provider CLI doubled through oneharness's
own `ONEHARNESS_BIN_CODEX` seam.
Pre-push runs it only when the pushed endpoint diff touches `scripts/`,
`config/oneharness.version`, `config/onejudge.base.yaml`, `oneharness.toml`,
`oneharness.judge.toml`, or `oneharness.orchestrator.toml`; every other pushed diff
skips it.

Net: the orchestration setup is harness-agnostic and correct. On a
no-unprivileged-userns host, dispatch codex with
`--oneharness-mode bypass` and the allowlister gate; run-plan takes the same flag.
The same constraint applies inside a worker's gate: llmlint normally requests a
read-only oneharness judge, which makes codex create a bubblewrap network
namespace and can fail at loopback setup with `RTM_NEWADDR`. llmlint 0.3.23 has
no mode override, so dispatches point `LLMLINT_ONEHARNESS_BIN` at
`scripts/llmlint-oneharness.sh`. The wrapper keeps Codex in `read-only` mode and
adds only its network permission, avoiding the unsupported network namespace
while retaining the OS-enforced read-only filesystem; `scripts/session-setup.sh`
also persists that setting for interactive sessions.

### Dispatch inactivity watchdog

The July 22, 2026 `merge-queue-local` closeout exposed a failure mode distinct
from a slow model turn. The worker had completed its gate (the final llmlint
record was around 19:27 local time), then the oneharness/Codex descendants
disappeared without another history record or commit. The parent orchestrator
remained live because the Python SDK was still awaiting `onejudge`'s
`process.communicate()`. With no caller-wide timeout and no inactivity
supervision at that boundary, neither a report nor an exception reached the
lifecycle journal, so the node could not settle or surface a failure. The
evidence path is the run's oneharness history timeline, lifecycle `events.jsonl`,
worktree commit log, and contemporaneous process tree: the first three stop
after the green gate while the last shows the provider descendants gone and the
owning orchestrator still alive.

Dispatch wraps the SDK-owned `onejudge` process with a stable pid and additionally
wraps the agent-side oneharness process with its own pid, completion marker, and
monotonic heartbeat. A vanished agent pid or heartbeat deadline settles as the
distinct incomplete `worker-died` outcome promptly, independent of CPU/I/O
from leaked descendants or `.git` churn. The last observed process tree is reaped
even after its root has vanished, so those descendants cannot pollute a retry.

`worker-died` also carries **why**, because provider throttling, quota
exhaustion, an OOM kill, and a genuine crash otherwise all reach the supervisor
as the same dead process tree. The wrapper records the agent harness's raw exit
status (`agent.exit_code`), its exit disposition (`agent.failure`, which
distinguishes a signal from an exit status), and its stderr (`agent.stderr`)
beside the heartbeat. `AGENT_STATUS_NAMES` is the one source for those filenames
and a drift gate holds the wrapper to it. Dispatch reads them back, redacted, and
composes one sentence: the liveness rule that fired, wrapped in the watchdog pid
and the child's exit status, followed by the recorded disposition and the stderr
tail. `Report.outcome_detail` carries the reason alone, `Report.stderr` the whole
sentence, and the node result and journal `node-failed` / `step-settled` events
carry it onward. Each of the four death paths names itself, so a harness failure
to escalate reads differently from a worker that simply stopped.

An incomplete dispatch that is *not* a death says how far it got. onejudge exits
1 both for a worker that exhausted its turns and for one that stopped for any
other reason, so `Report` carries the cap the dispatch asked for and the settled
detail compares the turns actually taken against it. Only a run that reached its
cap is reported as having hit it; a run that stopped short says so and carries
the unmet verdict, the assessment, or the harness stderr behind it.
Set `ORCHESTRATOR_WORKER_HEARTBEAT_TIMEOUT` to a positive number of seconds; it
defaults to `60`.

The wrapper refreshes that heartbeat every 0.5s, so the deadline is not a latency
budget — it is the margin by which a *live* worker may be starved of CPU before
the harness declares it dead. This harness runs many agents at once, so a
contended host routinely deschedules that loop for far longer than a few seconds;
a threshold near the write cadence reaps healthy workers under exactly the load
the harness creates. Detection of a genuinely dead worker does not depend on this
margin: when the agent exits, its heartbeat stops permanently, so a generous
deadline delays that settlement without weakening it.

The older activity watchdog remains as a separate slow-stall backstop. Changes
in descendant membership, cumulative CPU or I/O counters, or files under the
checkout's `.git` directory reset its inactivity clock. Configure that
bounded interval with `ORCHESTRATOR_DISPATCH_STALL_TIMEOUT` in seconds (positive
integer or decimal); it defaults to `600` seconds. This differs from
`ONEHARNESS_TIMEOUT`, which limits one model turn regardless of intervening
process activity. `run-plan --round-budget SECONDS` adds an outer round liveness
budget (default `14400`); exceeding it cooperatively cancels workers and sends a
blocking proposal over the planner channel.

### Dispatch scratch ownership

Each dispatch works in an `orchestrator-watchdog-*` scratch directory, and the
unattended sweep (`just sweep-scratch`, session setup, every recorded round
transition) may delete it concurrently. The pid recorded in `<dir>/pid` cannot
decide that: it is the *worker's*, and the wrapper `execvpe`s onejudge in place,
so it dies the moment the worker exits — while the dispatcher is still reaping
the reparented process tree and parsing the report out of the same directory. A
sweep that trusted it destroyed healthy in-flight dispatches and their evidence.
Recorded pids are also not identities: the kernel recycles them, so an unrelated
live process inheriting the number pinned dead scratch forever, and an
unreadable-signal pid was treated as live outright.

The dispatcher therefore holds an exclusive `flock` on `<dir>/owner.lock` for its
whole `TemporaryDirectory` scope, and that file records its own pid plus the
kernel's start token for it. The sweeper reclaims a watchdog directory only when
a non-blocking exclusive acquisition succeeds — the kernel's own answer to "can
anything still be using this tree?", released only when the owner releases the
directory or dies — *and* the recorded pid-with-start-token no longer identifies
a live process, so a filesystem that does not honor `flock` still cannot strand a
live dispatch. Directories predating the lock keep the pid-only judgment, now
made through the same start-token identity, and a lock that cannot be opened on
its own terms — symlinked, unreadable — never authorizes removal. Everything the
proof does not clear is reported as retained rather than silently kept.

## Dispatching playbook

- **Prepare the harness environment.** claude-code on the alternate subscription
  is the preferred worker; Codex is its fallback and the preferred judge.
  Codex installs in `~/.local/node/bin`. Keep that
  directory on `PATH` so worker fallback, supervision, and llmlint remain available;
  `scripts/session-setup.sh` persists the path. The dispatch code also sets
  `ONEHARNESS_TIMEOUT` to 10,800 seconds (three hours), a temporary hard per-turn
  ceiling so legitimate build-heavy agents can finish. Set the variable
  explicitly to override it; onejudge's `max_turns` and the lifecycle `--timeout`
  still bound the whole run independently. Finer phase budgets remain tracked
  in issue #6. Project dispatch also pins the agent-side
  oneharness `--config` to this repo's config, which selects the configured
  alternate-subscription Claude model before the configured Codex fallback. A global
  `ONEHARNESS_MODELS` chain cannot be used here: onejudge supplies `--session`,
  and oneharness rejects multi-model runs combined with a named session.
- **A session name is scoped to a working directory.** oneharness records
  `session name -> harness conversation token` in a store shared by every run
  (`~/.local/state/oneharness/sessions`), and the harness files that conversation
  under the directory that created it. So a recorded name resumed from a *different*
  directory fails before the first turn: the agent process exits, its wrapper parks,
  and the dispatch reports `worker-died`. The lifecycle names a session after the
  branch and every run cuts that branch a worktree under its own run root, so
  `dispatch.scoped_session` folds the worktree into the name. Steps and retries
  within one run share the worktree, and therefore one conversation; a later run
  pinned, resumed, or recovered onto the same branch gets its own. Give any new
  caller that dispatches into a per-run directory the same treatment.
- **Use the tracked graph for coordinated work.** `just run-plan` accepts direct
  agents, repository lifecycle agents, and explicit human nodes in one recorded
  DAG. A lifecycle `steps` list may mix agent steps with `kind: human` steps on a
  resumable branch. Human nodes never call onejudge; after a person performs the
  reported action, `just next-round RUN --complete-human NODE[/STEP]` records an
  attestation and releases only its dependents. Completed direct agents and
  lifecycle steps are not dispatched again.
- **Prefer the one-command wrapper.** Use
  `just repo-task-auto <repo> <persona> "<task>"`. It sets the dispatch
  environment and reports the branch's commit delta after the run, making
  stranded work visible. Use `just repo-task` or `orchestrator-repo-task` when
  the wrapper is unavailable.
- **Inspect a `not-completed` branch.** This status commonly means the agent hit
  the turn cap at the moment it finished, not that its work failed or vanished.
  Agents commit incrementally, and the lifecycle preserves those commits on the
  branch. Check its commit delta before deciding whether to recover or redispatch.
  The wrapper prints `just repo-recover <branch> --repo <checkout>`; that command
  verifies and publishes the preserved branch through its registered workflow.
- **Choose publication from identity type and workflow.** Omitted type is inferred
  from authenticated GitHub login versus normalized origin owner; pass
  `--repo-type` when that cannot resolve. Team defaults to a ready-for-review open
  PR; single-owner preserves local direct or remote auto publication. Multiple
  aliases share one identity. Migrate every alias atomically with `just
  migrate-repo-type <alias> --repo-type <single-owner|team>` or `just
  migrate-repo-workflow <alias> --workflow <local|remote>`. Configure a local
  single-owner repository's
  working repository with
  `git config receive.denyCurrentBranch updateInstead` so that push can update
  the checked-out base branch.

See [the repository lifecycle](repo-lifecycle.md) for clone, gate, recovery, and
merge mechanics.

## Rolling out a bundled llmlint plugin

For a bundled plugin such as `config_lint`, land the rule change in llmlint and
release that llmlint version to PyPI first. The consumer gate resolves bundled
plugins offline from its installed llmlint binary, not from the plugin URL, so an
unreleased rule cannot be activated downstream. After the release is available,
bump each consumer's `LLMLINT_MIN` floor and refresh its lock/install state; that
floor bump is the rollout switch that makes the normal gate use the new bundled
rule. Run the llmlint release gate before downstream consumer gates.

## Testing against onejudge without a paid model

onejudge's `command` provider speaks a small JSON-lines protocol
([onejudge v0.3.4 docs/protocol.md](https://github.com/nickderobertis/onejudge/blob/v0.3.4/docs/protocol.md)),
so any command can stand in for the harness. The e2e suite points it at
`tests/e2e/fake_backend.py` — a deterministic backend — so the gate drives the
**real** onejudge CLI and loop across a real subprocess boundary, faking only the
paid model/harness. This is the one sanctioned mock (a genuinely external service),
and it is confined to the provider seam; the merge, SDK dispatch, CLI, and report
validation all run for real. That backend implements the adopted version's protocol v4 unified
`supervisor` operation; the e2e fixture rejects any real CLI whose version is not
the adopted `config/onejudge.version` value.
