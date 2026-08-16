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

A third role sits beside both: the **monitor** `just orchestrate` attaches watches
a tracked graph rather than doing the work or driving it, so it has its own config,
`oneharness.orchestrator.toml` — named by `graphs/dag-scope.yaml`'s `monitor`
member, and forced by `scripts/oneharness-orchestrator.sh` on the manual and smoke
path. The `check-in` pacemaker scheduled beside it in that same
graph has a fourth, `oneharness.check-in.toml`, which is that routing verbatim and
differs in one field only; [Choosing a deadline per
side](#choosing-a-deadline-per-side) is why, and re-merging the two is a silent
regression rather than a tidy-up.

Edit those files to change the harness or model on a side for every run on this
host. One run changes it with graph config-ref overrides, the only way to give
the two sides *different* identities without moving concurrent runs; that pair is
specified under Harnesses below. `config/onejudge.base.yaml` carries only the loop's own
concerns (persona defaults, session), never harness/model selection.

### The judge side is the one named `oneharness.judge.toml`

Both sides reach `scripts/oneharness-agent.sh` through one `provider.bin`, and the
wrapper tells them apart **by name, confirmed by the config's own declared role**:
the judge / simulated-user side is the turn whose `--config` is named the judge
config's *and* declares `history_labels.role = "judge"`; a caller's config named
anything else must declare `role = "agent"`; and a turn carrying no config at all is
the agent side, running from this repository's own agent config. So there is no config
a caller can name that routes a side without declaring it is that side — a mismatched,
unrecognized, or absent role stops the turn rather than resolving to either one,
because a turn routed as the side it is not still runs and still answers. Everything the agent branch alone does
hangs off that test: forcing `oneharness.toml` when the caller named none, applying
`ORCHESTRATOR_WORKER_HARNESSES` rather than the judge's, streaming, the events file.
`tests/e2e/fake_backend.py` reads the same signal.

The name has two independent sources that agree, which is what makes it a contract
rather than a guess about a private layout. `config/onejudge.base.yaml` pins
`provider.judge_config: oneharness.judge.toml`, and `oneagentgraph` normalizes each
member's configs into its scratch under exactly those two basenames — give a graph
`worker.toml` and `judge.toml` through `--set` and the member directory still holds
`oneharness.toml` and `oneharness.judge.toml`. A two-party member gets both files; a
single-sided one gets only the agent's, which is how `fake_backend.py` tells a
pacemaker turn from a two-party agent turn.
`tests/test_dispatch_environment_contract.py` reconciles the wrapper's name against
the base config's, because a rename in one alone would route every turn as the agent
side and answer rather than fail.

**The rule used to be the absence of `--config`**, because onejudge left the agent
side's config implicit and named only the judge's. That was never the property which
distinguished the sides — only a proxy for it — and, measured against onepipeline
0.6.3, the proxy stopped holding: a dispatched agent side now arrives carrying
`--config <member-scratch>/oneharness.toml`. Under the old rule every agent turn was
read as a judge turn. `just smoke` and the manual probes below are what run through
this wrapper, and they would have kept working *quietly wrong* — the turn still runs,
with the judge's routing and the config's own model instead of the agent side's.
`tests/e2e/test_agent_wrapper_sides_e2e.py` is what holds the current rule; three of
its journeys fail against the absence-based one.

## Provider wiring

This repository uses three onejudge provider arrangements:

- `oneharness` is the live worker path. Its agent and simulated-user sides use
  the two harness configs above.
- `command` is the deterministic test path. A local JSON-lines process stands in
  for the paid harness boundary.
- The monitor is a two-sided onejudge member whose judge side is the **live
  planner**: its agent side runs under `oneharness.orchestrator.toml`, and its
  judge side is a command provider reaching `onepipeline channel serve` through
  `scripts/channel-serve.py`. That filter exists because the two halves agree on
  the response object and not on the request.
  <!-- llmlint: ignore[no_redundant_instruction_pointers] The two stdin shapes are one contract with one source, and `contracts_have_one_source_or_a_drift_gate` is why they are not restated here; this names where that source is rather than re-advertising the document. -->
  [Serving the channel as the monitor's judge
  side](orchestration.md#serving-the-channel-as-the-monitors-judge-side) holds both
  shapes and the refusal a direct wiring gets.

`onepipeline start --dag-graph` launches that graph beside the run it is driving.
The wiring is specific to the observer graph; worker dispatch retains its ordinary
oneharness or command provider and simulated-user loop.

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

Repo-specific personas are catalogued under a slash-qualified name such as
`crozier/crozier-corpus`; general cross-repo roles retain top-level names. That is
the catalog's spelling, not a plan node's: a node names a built-in role or a path
relative to `graphs/`, and
[which of these files a dispatch actually reads](../personas/README.md#which-of-these-files-a-dispatch-actually-reads)
is where that resolution is measured.

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
`config/oneharness.version` release and rejects a stale binary. Version 0.10.1 is
the adopted release: it puts a pre-spawn/post-spawn hook pair on `RunControls`, so a
library **embedder** owns the harness child a run starts rather than losing it to the
process tree. That is not an abstract capability here — it is the compile floor
`oneagentgraph` 0.2.18 names for converting a single-sided member's turn from a
spawned `oneharness` CLI into `oneharness_core::io::run::run_supervised`, which is
why this repository's own paid-model substitution had to grow a second seam (see
[Testing against a harness without a paid model](#testing-against-a-harness-without-a-paid-model)).
It succeeds 0.10.0, which **bounds a control socket's address at construction**:
`sockaddr_un.sun_path` is 108 bytes on Linux and 104 on the BSD lineage, `bind`
past it fails with `ENAMETOOLONG`, and a session name one byte too long had turned
every controlled dispatch on a host into an unreachable run. That release is why a
dispatched worker here now reports a control address rather than a reason it could
not have one — measured on a real launch, below. Behind it, 0.9.0 refuses a
contradictory option pair (`{all: true, harnesses: ["codex"]}`) instead of dropping
one half and running the other, so a turn nobody asked for cannot be billed and
reported as a success. Each of those three is a major bump for a Rust consumer
matching `oneharness-core`'s types exhaustively and nothing at all for a consumer of
the CLI, which is what this repository is. Behind them, 0.8.0 binds a controlled
turn's mechanism to the **candidate serving it** rather than to the chain as a whole.
The validator had required one mechanism across every candidate, which a fallback
chain can never satisfy once it mixes harness families, so the five-identity chains on
this host were refused a control socket that only one candidate would ever hold — see
[streaming and turn control are independent
concerns](#streaming-and-turn-control-are-independent-concerns). It in turn succeeds 0.7.2, which
stopped `--control` counting **candidates** where it meant concurrent turns — the
validator refused any selection holding more than one entry and never received the
`fallback_mode` flag that would have told it a chain runs exactly one live turn.
Behind that, 0.7.1 added a deterministic per-selection harness
answer (`--bin ID=PATH`, and `ONEHARNESS_BIN_<ID>` beside it), a test-support surface
for a consumer that drives several selections in one run — this document's own
passthrough and precedence probes below use it. Behind that, 0.7.0
made a per-turn deadline optional by default while retaining explicit `timeout = 0`
as the no-deadline spelling — the floor for
[choosing a deadline per side](#choosing-a-deadline-per-side) and so for a monitor
turn that watches a whole run. That release in turn succeeds 0.6.5, which carried
[oneharness PR #1213](https://github.com/nickderobertis/oneharness/pull/1213),
lifting the rejection that made `--stream` and `run_mode = "fallback"`
mutually exclusive — the floor for [streaming the agent
side](#streaming-the-agent-side) without giving up the chain. It contains auth
variants shipped in 0.5.6 and succeeds 0.3.24, the first release to carry the
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

The two sides are different jobs, and the providers are not equally good at them.
On the adopted stack an identity choice is a choice of oneharness config ref. Make
each per-run config declare exactly the intended identity, then override the two
refs without editing a shared config:

```sh
just orchestrate plan.json \
  --node-set members.worker.agent.oneharness_config=/tmp/worker-codex.toml \
  --node-set members.worker.judge.oneharness_config=/tmp/judge-claude-alt2.toml
```

`onepipeline start` forwards each `--node-set` opaquely to every node-scope
<!-- llmlint: ignore[changed_behavior_has_e2e] The real node-scope journey proves
this repository's forwarding path; dag-scope forwarding is owned by onepipeline. -->
`oneagentgraph run`; use `--set` with the corresponding dag member path for the
dag-scope conversation instead. oneagentgraph resolves each ref independently and
invokes oneharness directly with that side's resolved config.

Do not put `ONEHARNESS_HARNESSES` in the graph's `env`: it is process-wide **and
beats config**, so using it to move the worker silently moves the judge too.

```
$ printf 'harnesses = ["claude-code:alternate2"]\n' > /tmp/prec.toml
$ ONEHARNESS_HARNESSES=codex oneharness run --config /tmp/prec.toml --print-command --prompt hi
  selected: codex
```

The per-run file must preserve the selected identity's section from the target
repository's own config — its model, `env_from`, `unset_env`, and any harness args
are part of the identity. A variant is selectable only if that target config
<!-- llmlint: ignore[contracts_have_one_source_or_a_drift_gate] The external
repositories remain authoritative; this adoption-time contrast is required
operator guidance that this repository cannot derive from sibling working trees. -->
declares it: `onevcs` declares `codex:alternate` and
`claude-code:alternate2`, while `oneagentgraph` and `onepipeline` declare only
plain `codex` and `claude-code`. A mismatched ref is resolved at launch, but an
<!-- llmlint: ignore[changed_behavior_has_e2e] Delayed rejection is upstream
oneagentgraph/oneharness behavior; this repository's launch boundary only forwards
the config ref, whose real accepted journey is covered above. -->
undeclared or unusable variant is not refused until that dispatch starts, so
inspect the target config before spending a long gate run.

With neither override set nothing changes: each side resolves the config ref in
`graphs/node-scope.yaml`. `scripts/oneharness-agent.sh` is still used by `just
smoke` and manual probes, but the adopted launcher no longer calls it; its legacy
`ORCHESTRATOR_WORKER_HARNESSES` / `ORCHESTRATOR_JUDGE_HARNESSES` selection path
cannot route an adopted run.

#### Choosing a model per side

Picking the identity does not pick the tier. Every role's config pins a `model` per
harness — `oneharness.judge.toml` pins `claude-sonnet-5` on all three of its Claude
identities *by design*.
That config is the authority for the tier and identity count; inspect it when either
changes rather than treating this explanatory sentence as a second contract.
Model overrides are graph-native fields:

<!-- llmlint: ignore[changed_behavior_has_e2e] Model-field interpretation and
validation belong to the published oneagentgraph graph contract; this repository
only documents the operator-facing spelling alongside its tested config-ref path. -->

```sh
just orchestrate plan.json \
  --node-set members.worker.agent.oneharness_config=/tmp/worker-claude.toml \
  --node-set members.worker.agent.model=claude-opus-5 \
  --node-set members.worker.judge.oneharness_config=/tmp/judge-claude.toml \
  --node-set members.worker.judge.model=claude-opus-5
```

**A model override is accepted only with a side config naming identities of one
<!-- llmlint: ignore[changed_behavior_has_e2e] The single-family validation and
provider-rejection recovery are upstream oneagentgraph/oneharness behavior, not a
behavior implemented at this configuration layer's launch boundary. -->
harness family.** That is a hard graph-validation rule: one model
applies to whichever candidate the chain selects, and oneharness's `fallback` mode
falls through only a candidate that cannot run at all — never a task failure — so an
unpaired model reaches a codex candidate carrying a Claude model name and kills the
dispatch on a provider rejection instead of degrading. Requiring both in one breath
makes that unconstructable before a provider starts.

The model **value** is deliberately not checked against an allowlist, and that
asymmetry with the identity is the point. An identity selects credentials and
environment routing that only this repository configures, so naming an unconfigured
one must refuse. A model name is passed straight through to the harness the operator
named in the same breath, where an unknown one fails loudly at the provider rather
than quietly running something else.

##### Why the wrapper does two things, not one

`ONEHARNESS_MODEL` is *not* the counterpart of `ONEHARNESS_HARNESSES`, and reading it
as one is the trap this section exists for. Measured against the adopted oneharness
0.10.1, a config's per-harness `model` **beats** the variable, while the `--model`
flag on an invocation's own argv beats the config — a precedence that is a fact about
one release, so the literal above is derived from `config/oneharness.version` by
`tests/test_onejudge_version.py::test_the_model_precedence_claim_names_the_adopted_oneharness`
and an upgrade fails here until this measurement is redone:

```
$ ONEHARNESS_HARNESSES=claude-code:primary ONEHARNESS_MODEL=claude-opus-5 \
    oneharness run --config oneharness.judge.toml --print-command --prompt hi
  claude-code:primary ran claude-sonnet-5      # the config won
$ ONEHARNESS_HARNESSES=claude-code:primary \
    oneharness run --config oneharness.judge.toml --model claude-opus-5 \
    --print-command --prompt hi
  claude-code:primary ran claude-opus-5        # the flag won
```

So the wrapper applies each side's model **twice**: it names it on that branch's own
`oneharness run`, which is what actually decides the turn, and it exports
`ONEHARNESS_MODEL`, which is what carries the choice onward. Because that export
lands in the side's own process, **everything that side subsequently runs inherits
it** — a worker's own `just gate`, and any `llmlint` invocation inside that gate.

That inheritance is bounded by the same precedence, and the llmlint tier is where it
shows. `llmlint` invokes oneharness with **no `--model` at all** — verified here with
a spy binary in `LLMLINT_ONEHARNESS_BIN` recording its own argv, rather than inferred
from llmlint's config schema:

```
$ LLMLINT_ONEHARNESS_BIN=/tmp/spy.sh llmlint --diff --diff-base HEAD
  spy argv: run --system-file <tmp> --prompt 'Evaluate each rule ...' --schema <tmp>
            --cwd <repo> --timeout 600 --mode read-only --require-available --compact
  ONEHARNESS_MODEL=claude-opus-5     # inherited, and the argv names no model
```

`oneharness.llmlint.toml` pins a `model` on every identity it names, so that
inherited variable loses to the config and the tier keeps its own pinned model. What
*does* move the llmlint tier is the harness half: `ONEHARNESS_HARNESSES` is
process-wide and beats config. Do not export it around an orchestration run: use
the side-specific graph config-ref override above. llmlint is a separate process
and continues to select from `oneharness.llmlint.toml`.

With neither model flag set nothing changes here either: no branch names a model of
its own, no variable carries one, and each side runs exactly the model its config
pins for the identity it landed on.

#### Choosing a deadline per side

The third thing a side has, and the one with **no graph-native field**: a member
takes `oneharness_config`, `model`, and `stream`, and nothing else. So unlike the
identity and the tier, a per-member `timeout` can only be expressed by giving that
member its own config file.

Since oneharness 0.7.0, absent means no deadline. `timeout = 0` (or `--timeout 0`)
also means no deadline, preserving the explicit spelling used by the orchestrator.

<!-- llmlint: ignore[contracts_have_one_source_or_a_drift_gate] The per-side values
below are read from each config by the real CLI in
tests/e2e/test_oneharness_timeout_e2e.py; this table is the operator-facing summary
of that check, not a second declaration of it. -->

| Side | Config | Deadline |
| --- | --- | --- |
| Orchestrator agent | `oneharness.orchestrator.toml` | **none** (`timeout = 0`) |
| `check-in` pacemaker | `oneharness.check-in.toml` | 240s, stated |
| Judge / simulated user | `oneharness.judge.toml` | **none**, the release default |
| Worker agent | `oneharness.toml` | **none**, the release default |
| LLM lint | `oneharness.llmlint.toml` | **none**, the release default |

The monitor is the exception because of what one of its turns *is*: a watch that
lasts as long as the run does — reading the detailed stream, judging it, and
surfacing what it finds — where a deadline would end the watching rather than bound
it. Under the 120-second default three consecutive runs died, each reported only as
`member-died
rule=provider-failure cause=timeout` — which reads as a provider problem and is not
one. Two things make that diagnosis expensive, and both are worth knowing before
reading a timeout as an outage: a timeout deliberately does **not** fall through a
`fallback` chain (it would mask a real failure), so no identity ordering rescues it;
and from outside, a killed process is indistinguishable from a harness that
vanished, so unrelated dispatch failures get misattributed alongside it.

**The pacemaker is why this needed a second file rather than one line.** `check-in`
shared `oneharness.orchestrator.toml`, and a `0` written there would have given a
scheduled member no deadline too. That is the worse failure: a killed pacemaker turn
at least ends and is retried on the next tick, while a wedged one that never dies
holds its slot silently forever. Everything else was rejected for being unable to
express one member: `ONEHARNESS_TIMEOUT` is process-wide for the whole
`oneagentgraph run` **and** beats every file, exactly like `ONEHARNESS_HARNESSES`
above; and an `ONEHARNESS_BIN` wrapper appending `--timeout 0` is process-wide too,
so it could only tell the members apart by pattern-matching the `--config` path it
was handed. A per-member `timeout` in `oneagentgraph`'s member config would be
better than any of these and is proposed upstream; until it exists, two files is the
seam.

So: never point two members at one config to save a copy, and read the difference
from the CLI rather than the file —

```
$ oneharness config --config oneharness.orchestrator.toml | jq .timeout
  { "value": 0,   "source": "oneharness.orchestrator.toml" }
$ oneharness config --config oneharness.check-in.toml | jq .timeout
  { "value": 240, "source": "oneharness.check-in.toml" }
$ oneharness config --config oneharness.toml | jq .timeout
  { "value": null, "source": null }
```

oneharness 0.7.0 separately retains a 120-second approval-wait safety deadline for
prompt-capable headless modes unless a timeout is explicitly chosen. The committed
paths do not trip that policy: workers and the orchestrator run in `bypass`, and
`oneharness list` reports `bypass` as `headless: "clean"` for both codex and
claude-code — every family their chains name — so no turn on them is asked to approve
anything. A side that could prompt must keep a finite deadline, or pass
`--permit-prompts` deliberately.

#### What a spawned provider inherits, and what that is not

oneharness passes `ONEHARNESS_HARNESSES` to the provider it spawns **verbatim**, and
sets nothing when nothing selected one. It does *not* narrow the variable to the
candidate it ended up running — through oneharness 0.10.1, confirmed against the binary:

```
$ ONEHARNESS_HARNESSES=codex,claude-code oneharness run --prompt hi   # fell through to codex
  the child saw ONEHARNESS_HARNESSES='codex,claude-code'
$ oneharness run --config <chain.toml> --prompt hi                    # chain from config
  the child saw no ONEHARNESS_HARNESSES at all
```

That is why a dispatch leaks its selection: the wrapper exports the variable, so the
provider *and everything that provider then runs* — a worker's own `just gate`, and
therefore this suite — inherit it. `DISPATCH_SELECTION_ENV` and the fixtures over it
exist for exactly that inheritance: it names every variable a per-side choice can
arrive under — both harness variables, both model variables, and the two
process-wide ones they are resolved into — in one place, so a reader that drops the
list is genuinely isolated rather than isolated from half of it.

The two are told apart by **provenance, not by value**. Every selection is dropped at
each process boundary the suite owns — `tests/conftest.py` for its own environment,
and a journey that launches the wrapper builds that launch's environment from
nothing rather than inheriting one (`tests/e2e/test_quota_fallthrough_e2e.py`'s
`_agent_turn` passes `PATH`, `HOME`, and the values it is asserting on, and no
more) — and each journey then states the value it wants; so a selection a recorded
turn observes is one that journey put there, and an inherited one reaches nothing.

Do not "adapt" a selection journey to a value you did not state. Reading the single
identity a run happened to be routed to — `codex` on *both* sides of the default path,
where the worker should record its whole substituted chain and the judge none — means a
selection leaked in, not that oneharness narrowed one. That misreading has landed here
once already, and adapting the assertions is what removed the gate that would have
caught it.

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

The *launch* — and only the launch — is retried, a bounded number of times with a
short backoff between attempts.
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
`oneharness.judge.toml`, `oneharness.orchestrator.toml`, or
`oneharness.check-in.toml`; every other pushed diff skips it.

### The record a fallback chain is judged by

`run_mode = "fallback"` records **every candidate it attempts**, in priority order,
and stops at the first that can actually run the task — so one turn can leave
several records in one session, and only the last of them is the launch path's
outcome. The smoke judges that one: it is held to the whole bar above, and the
records ahead of it are read as the chain doing its job. Holding all of them to
that bar is what failed this smoke for weeks of healthy launches while
`claude-code:alternate`'s weekly quota was gone and `claude-code:alternate2` served
every turn — and, because the pre-push hook selects this smoke for any diff
touching `scripts/`, it blocked publication of work that had already passed its
gate.

A candidate only counts as fallen through when its own record says it never ran the
task: `failure_kind` of `quota` or `auth`, or a `skipped` status, which
carries no exit code, no duration, and no accounting at all. `rate_limit` is
deliberately not in that set — oneharness stops the chain on one, because that
record carries work the provider already billed for (see
`tests/e2e/test_quota_fallthrough_e2e.py`) — so a `rate_limit` record ahead of
another describes something the chain does not do, and fails the smoke as an
unclassified candidate failure. A chain whose *every* candidate refused fails too,
naming each identity and its reason so the operator knows which subscription to
restore.

A candidate's own word for what became of it is checked rather than believed. These
records are read back out of a store nothing in the smoke wrote, and each one
reaches both the verdict and the operator's report, so a record must *back* the
reason it names: it has to identify the harness it was written for, and it has to
show that nothing was spent — no successful turn, and every counter it reports at
zero. Absent
accounting is not evidence of spend and is accepted: a skipped candidate records a
null for every counter, and so does an auth refusal on this host. A refusal that
names no identity would otherwise be reported as "an unidentified harness fell
through", and one carrying billed tokens is a candidate that ran — excusing either
as fallback is the launch breakage this smoke exists to name. For the same reason
only a `type: "run"` line can stand in for the selected candidate: a store also
holds an index whose lines wrap a record inside an envelope that names no harness
of its own, and the verdict is read off the session's *last* turn.

A pass names the fallen-through candidates on their own lines, above the verdict:

```
smoke: fell through claude-code:alternate (quota); the fallback chain handed the turn to the next identity
smoke: passed via claude-code:alternate2 (recorded cost: $0.063882)
```

Both lines name the *identity* rather than the harness, because a chain's two
Claude subscriptions are one harness and differ only by variant.

`tests/e2e/test_quota_fallthrough_e2e.py` drives that path for real — the wrapper,
the chain, the classifier, and the record the verdict is read back out of — with
both candidates replaced at oneharness's own `ONEHARNESS_BIN_*` seam. Two things
make a journey of that shape possible to write safely, and both are easy to get
wrong:

- **Drop the dispatch's harness pin.** This repository runs its own suite from
  inside a dispatch, which exports `ORCHESTRATOR_WORKER_HARNESSES`;
  `scripts/oneharness-agent.sh` applies it *over* any `ONEHARNESS_HARNESSES` the
  journey sets, by design. A journey that inherits it runs on the pinned identity.
- **Name bare identities, never variants.** `ONEHARNESS_BIN_*` keys on a harness
  id and there is no spelling of it that reaches a variant —
  `ONEHARNESS_BIN_CLAUDE_CODE` leaves `claude-code:alternate` resolving to the real
  `claude`.

Together they are a money hazard rather than a style point: a journey that misses
either one spawns a live subscription with its double sitting unused, and a billed
run and a free one look identical from the assertions. The guard against the first
is that `_agent_turn` builds its launch environment from nothing, so there is no
inherited pin to apply over what the journey sets; against the second, that it
names plain `claude-code` and `codex`. A new journey of this shape launches
through that builder rather than spelling an environment inline, which is how one
would escape both.

Net: the orchestration setup is harness-agnostic and correct. On a
no-unprivileged-userns host, dispatch codex with
`--oneharness-mode bypass` and the allowlister gate.
The same constraint applies inside a worker's gate: llmlint normally requests a
read-only oneharness judge, which makes codex create a bubblewrap network
namespace and can fail at loopback setup with `RTM_NEWADDR`. llmlint still has no
mode override at the `LLMLINT_MIN` floor `scripts/setup-llmlint.sh` declares, so
dispatches point `LLMLINT_ONEHARNESS_BIN` at
`scripts/llmlint-oneharness.sh`. The wrapper keeps Codex in `read-only` mode and
adds only its network permission, avoiding the unsupported network namespace
while retaining the OS-enforced read-only filesystem; `scripts/session-setup.sh`
also persists that setting for interactive sessions.

### Streaming the agent side

A planner supervises through what the harness reports, and until oneharness 0.6.5
the agent side reported nothing until a turn *ended*. `--events` is a **format**
switch: it guarantees the single end-of-turn report carries the normalized
tool-call transcript, upgrading claude-code to `stream-json` — and says nothing
about *when* that arrives. Dispatches here routinely spend 600-2000 seconds on turn
one, so `just status <run-id>` printed "No dispatched tasks recorded" for a
lifecycle agent that had been working for half an hour, and a healthy node twice
got reported as possibly dead.

`--stream` delivers those same normalized events **as they occur**, then a final
result line, and implies `--events`' format selection. Until [oneharness PR
#1213](https://github.com/nickderobertis/oneharness/pull/1213) it was refused under
`run_mode = "fallback"`, so adopting it would have meant giving up the chain that
kept this host dispatching through an HTTP 429. That restriction is gone: a
fallback chain streams, candidates run one at a time, and **a streamed chain
selects the same candidate a buffered one would**. A candidate that falls through
has published nothing a consumer could act on, and its transcript is still in
`results`.

Three pieces make it work here.

**The filter.** onejudge parses its provider's stdout as exactly **one** JSON
document, and a stream is many NDJSON lines wrapped in
`{"type":…}` envelopes — it rejects them outright. So
`scripts/oneharness-agent.sh` runs the child through
`scripts/oneharness-stream.py` instead of `tee`. That filter appends every line to
the same raw stdout record `tee` kept, republishes each event as a bounded
`agent.activity` summary, and forwards the terminal line's `report` — cut out as
the exact text oneharness wrote, not re-serialized — as the one document onejudge
reads. Anything it does not recognize is forwarded verbatim, so a degraded run's
bare report still arrives intact.

**The probe.** Before each dispatched turn the wrapper runs
`oneharness run --config <agent config> --stream --print-command <the caller's own
arguments>`, with stdin closed so a `--prompt-file -` cannot consume the task. The
real CLI renders what it would spawn and spawns nothing: it applies the same
up-front validation a real run applies — `--stream` against this config's
`run_mode` and chain, an `ONEHARNESS_HARNESSES`/`ONEHARNESS_MODELS` selection, a
caller's `--schema` or batch prompts — writes no history record, and returns in
single-digit milliseconds. A CLI too old to know `--stream` rejects the argument
the same way, so one question covers every reason a dispatch might not be able to
stream. **A no answer is never a dispatch failure**: `--events` stays selected and
the turn runs exactly as it did before, transcript and all. A turn with no status
directory — nothing is watching it — keeps `--events` for the same reason, since a
stream would have nowhere to publish.

**The reader.** The published views read those publications back out of the
scratch root the sweep examines, which is the only place a dispatch's
watchdog directory is: the run directory never learns that path and the dispatch
never learns the run directory, so each summary carries the graph locator it was
dispatched with. It is a trust boundary — the files sit under a shared root and a
subprocess writes them — so every field is validated, bounded, redacted, and scoped
to the run that asked. `just status <run-id>` is the view that renders it, adding
`now Bash just gate (7 event(s), 4s ago)` to the in-flight line. The journal join
still supplies the guarantee that *dispatched, no completed turn* is not *no
dispatch*; streaming only ever adds to it, and a node with no activity reads
exactly as it did before.

What still proves each piece: the filter's own path guards in
`tests/test_stream_filter.py`, real-CLI stream **selection** over this repository's
own chain in `tests/e2e/test_oneharness_control_e2e.py`, and a rejected candidate
falling through on `auth` in `tests/e2e/test_quota_fallthrough_e2e.py`. The
end-to-end journey that drove a *live* streamed turn through the filter went with the
suite cut, so the reader is a covered surface and the stream flowing through it is
not.

#### Streaming and turn control are independent concerns

`--stream` decides **when** a turn's transcript arrives. `--control` opens an
out-of-band socket so a separate `oneharness interrupt --session <NAME>` can abort
the in-flight turn without killing the dispatch. They share nothing: one is an
output format and its timing, the other is a second channel into a live turn. As of
oneharness 0.8.0 a multi-identity `run_mode = "fallback"` chain supports **both at
once**, whatever families it mixes — 0.6.5 lifted the stream/fallback rejection,
0.7.2 lifted the control one whose validator had been counting *candidates* where it
meant concurrent turns, and 0.8.0 lifted the last of it by binding the mechanism to
the candidate that serves the turn.

That last one is what removed the constraint this host actually had. Every role's
chain here mixes claude-code and codex, and the two declare different turn-control
mechanisms (`oneharness list`'s per-harness `control` field:
`claude-control-request` and `codex-app-server`). Requiring one mechanism for the
whole chain refused those chains outright; binding late asks only that the candidate
holding the turn declare one, so each is planned with its own — measured against the
adopted release:

```
$ oneharness run --config oneharness.toml --session probe --control --stream \
    --print-command --prompt hi
  planned: claude-code:alternate, claude-code:alternate2, codex, codex:alternate,
           claude-code:primary
  claude-code:alternate → claude -p --input-format stream-json …   # claude-control-request
  codex                 → codex app-server                          # codex-app-server
$ oneharness run --config <same chain, claude-code identities only> --session probe \
    --control --stream --print-command --prompt hi
  planned: claude-code:alternate, claude-code:alternate2, claude-code:primary
```

**Turning streaming off is never the response to a control failure.** A workaround
of exactly that shape was written against this repository and rejected: it set the
worker member's `stream: false` in `graphs/node-scope.yaml` and routed the provider
through a shell wrapper, trading away the per-turn visibility [above](#streaming-the-agent-side) to dodge a
refusal that had nothing to do with it. No member carries a `stream` key — each takes
oneagentgraph's default of `true` — and the stream flag was never the lever.

**A dispatch here does ask for `--control`, and on the adopted stack it binds.**
`oneagentgraph` opens a two-party member's *agent* turn controllable and addresses it
as `<member session>-skill`, so this is not a capability held in reserve; it is on
every dispatched worker. What used to make it unreachable was the address rather than
the chain: a unix socket address is capped at 108 bytes on Linux, and a session name
past that made `bind` fail with `ENAMETOOLONG` — which oneharness 0.10.0 fixed by
bounding the address at construction. Measured on a real single-node launch of the
adopted stack, the worker's onejudge report carries

```json
"control": {
  "session": "node-scope-1786887436992-494181-worker-skill",
  "session_dir": "/home/nick.guest/.local/state/oneharness/sessions",
  "cwd": "."
}
```

with `control_unavailable` null — a 107-byte address, which with its terminating NUL
is exactly the 108 the platform allows. So the margin is nil rather than comfortable:
a longer member session name is the thing that would take turn control away again,
and it would report `control_unavailable` rather than fail loudly.

`tests/e2e/test_oneharness_control_e2e.py` holds all four claims against the real
CLI — the committed mixed-family chain taking control with each candidate on its own
mechanism, a multi-identity chain carrying control and streaming at once, streaming
alone opening no channel, and no graph member declaring `stream`. Every case plans with `--print-command`, so the
proof costs no provider turn.

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
from leaked descendants or `.git` churn. Descendants left behind after the root has
vanished are reaped too, so they cannot pollute a retry — see
[What teardown is allowed to signal](#what-teardown-is-allowed-to-signal) for how a
dispatch decides which processes are still its own.

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
process activity. Above both, the engine watches every in-flight dispatch for the
life of the run and raises a non-blocking `quiet-worker` proposal for one that has
recorded nothing past `ONEPIPELINE_STALL_AFTER_SECONDS` (default `2400`).

### Dispatch scratch ownership

Each dispatch works in an `orchestrator-watchdog-*` scratch directory, and the
unattended sweep (`just sweep-scratch`, session setup) may delete it
concurrently. The pid recorded in `<dir>/pid` cannot
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

### What teardown is allowed to signal

The same "recorded pids are not identities" rule governs the end of every dispatch,
and for a while it did not. Teardown signalled the union of every pid the liveness
watcher had ever sampled — each `bunx nx`, each pytest worker, each `git` and
`uv run` child — and nothing was ever removed from that union, so a traced run
measured 28% of the signalled pids already exited. This host's `pid_max` is
4,194,304 and its counter demonstrably completes a full cycle in under a day, while
one dispatch holds recorded pids for the length of a turn and often far longer. A
remembered pid is therefore a slot, not a process, and signalling one is how a
planner's own work gets interrupted by somebody else's cleanup — the incident
`AGENTS.md` records under "never derive a process list from `ps` and signal it",
reached by a different derivation.

Teardown therefore asks `owned_tree` what it can prove *right now*. The evidence is
the environment stamp: `processes_stamped_for` asks the same question
`orphaned_dispatch_processes` asks of a finished dispatch's leavings — which live
processes carry `ORCHESTRATOR_AGENT_STATUS_DIR` naming this dispatch's own status
directory — and the kernel fixes that environment at `exec`, so no process can shed
it.

- **The stamped processes** are always selected: they *are* the evidence. This is
  what reaches a descendant whose parent has already exited, adopted by init and
  unreachable by any walk.
- **Parentage** is walked from the recorded root only once that root is itself
  stamped. An unproven number may name a recycled stranger, and walking it would
  select that stranger's whole subtree. Nothing is given up by the gate: a live
  descendant of a proven root is this dispatch's even if it carries no stamp of its
  own — including one in a process group of its own, which no `killpg` reaches.
- **The process group** is signalled only while a stamped process is still *in* that
  group. That is stronger than it looks — the kernel keeps a pid allocated for as
  long as any live process names it as a group, so a group still holding one of ours
  cannot have had its id handed to anybody else.

**Order is part of the proof, not a detail of it.** A pid is a capability its holder
can destroy: the members whose existence reserves a group id are the same ones
teardown is about to kill, so signalling the proven processes first would release the
number and every later `killpg` would be aimed at one the kernel was free to reuse. A
single snapshot does not make three handles safe — it makes them safe *until the
first signal*. So `tear_down` runs exactly one broad operation, first, before it has
signalled anything: `terminate_proven_process_group` on the proven group, which is
also the only handle that reaches work spawned into this dispatch since the snapshot.
Nothing is walked, enumerated, or grouped from a number afterwards, and
`terminate_tree` is not used on this path at all — the walk it would repeat already
happened, while the root was proven alive.

**Each process is signalled through one mechanism, never two.** `owned_tree`
partitions its proven set by process group while that membership can still be read,
so a member the group covers is terminated *by the group*, `SIGKILL` included, and is
never passed to the pid phase. Handing it on would mean signalling, one grace period
later, a number this teardown had itself just released — the same defect the group
ordering fixes, arriving through the other door. What remains for the pid phase is
exactly what `killpg` cannot reach: a stamped orphan reparented to init, and a
descendant that put itself in a session of its own.

**A pid is revalidated before every signal, not once per phase.** The pid phase has
the same `SIGTERM`-then-`SIGKILL` shape as the group one, and the same problem inside
it: the ordinary outcome of the first signal is that the process exits, which frees
its number during the grace period the second signal waits out. So that remainder is
carried as `ProcessIdentity` — the pid paired with the kernel's start token for the
process holding it — captured in `owned_tree` while ownership is proven, and
`terminate_identified_processes` re-checks it immediately before the `SIGTERM` and
again before the `SIGKILL`. A worker that shut itself down on the first signal is
never sent a second; one that ignored it is still itself, and is killed. The start
token rather than the environment stamp, because the stamp cannot answer for a
parentage-proven descendant that has since `exec`ed something which never carried it,
while every process has a start time. `terminate_processes` keeps its pid-tuple
signature for callers that hold only numbers and takes that identity at the moment of
the call — the best such a caller can do, and still better than not asking.

`terminate_proven_process_group` applies the same rule to its own two signals: it
re-asks for the proof before the `SIGKILL`, because its own `SIGTERM` can be what
released the number. Refusing that second signal costs nothing — a group the
`SIGTERM` emptied has nothing left to kill — while insisting on it would mean
signalling a reservation the caller had just given up. It also does no reaping pass,
because enumerating a group's members after killing them is that same mistake once
more; callers that must reap hold an exact pid set for it.
`orchestrator.watchdog.terminate_process_group` keeps the enumerating behaviour for
`gitops` and `verify`, which hold their group leader as a live child of their own for
the whole call.

`externally_waited` is not a substitute for any of this: it governs which pids this
process may `waitpid` for, not which ones get signalled.

Proving nothing signals nothing, which is the right failure direction — the cost is a
leaked process the next scratch sweep reaps on this same stamp, against interrupting
work that was never ours. The lock-based `_dispatch_is_finished` proof the sweep
applies is deliberately not consulted here: a live dispatch asking about its own tree
holds that lock and would find every one of its own processes retained by it. Naming
its own directory is the stronger claim of the two — the sweep has to infer which
dispatch a stamp belongs to, while this caller created the path it matches.

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
- **A dispatched lifecycle member starts in its own worktree**, and the launch's
  journal is where that is read. `onevcs` appends `session-opened` naming the
  worktree it cut for the node's branch; `oneagentgraph` appends `member-started`
  naming the directory it started that node's `worker` member in; against the
  adopted onepipeline 0.3.1 the two are the same path, and the `--cwd` oneharness
  is handed for that member's agent turns is that path again. Measured, not
  inferred: a lifecycle dispatch of this repository ran `pwd` as its first action
  and got
  `/home/nick.guest/.onevcs/workspaces/github.com-nickderobertis-ai-orchestrator-c2fddf4e28b4/runs/s-cec0174198d8/worktree`,
  the worktree its own `session-opened` names. That is worth stating because it was
  twice reported fixed and never measured — once in a wheel this dispatch path never
  called, once in a crate release that could not be adopted — which is why a task
  template here still told every worker where to commit. It is also why the check is
  now `tests/e2e/test_worker_start_directory_e2e.py` rather than a report: it
  launches one lifecycle node for real and compares those two journal records and
  the served `--cwd`. What the measurement does *not* cover is a node dispatched
  with no worktree at all — a direct agent node has none to be placed in, and its
  `member-started` records `.`, the directory the run was launched from.
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
- **Use the tracked graph for coordinated work.** `just orchestrate` accepts direct
  agents, repository lifecycle agents, and explicit human nodes in one recorded
  DAG. A lifecycle `steps` list may mix agent steps with `kind: human` steps on a
  resumable branch. Human nodes never call onejudge; after a person performs the
  reported action, an `attest` command over `just channel-reply` records an
  attestation and releases only its dependents. Completed direct agents and
  lifecycle steps are not dispatched again.
- **Run one subtask as a one-node plan.** There is no separate single-dispatch
  command: a plan holding one direct node or one lifecycle node goes through the
  same executor, ledger, and progress views as a wide DAG, so no piece of running
  work is invisible to them. See `examples/single-node-direct.plan.json` and
  `examples/single-node-lifecycle.plan.json`.
- **Inspect a `not-completed` branch.** This status commonly means the agent hit
  the turn cap at the moment it finished, not that its work failed or vanished.
  Agents commit incrementally, and the lifecycle preserves those commits on the
  branch. Check its commit delta before deciding whether to recover or redispatch.
  `just repo-recover <branch> --repo <checkout>` verifies and publishes a branch
  whose provenance is incomplete; `just publish-branch <branch> --repo <checkout>`
  is the one for a branch that is simply finished and unpublished.
- **Choose publication from identity type and workflow.** Omitted type is inferred
  from authenticated GitHub login versus normalized origin owner; declare the
  node's `repo_type` when that cannot resolve — it is a plan field, and no `onevcs`
  verb takes a type option. Team defaults to a ready-for-review open
  PR; single-owner preserves local direct or remote auto publication. Multiple
  aliases share one identity, and one rule in the rules file resolves the policy
  for all of them: edit that rule to change type or workflow, and confirm the
  result with `just repos`. Configure a local single-owner repository's
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

## Testing against a harness without a paid model

onejudge's `command` provider speaks a small JSON-lines protocol
([onejudge v0.4.0 docs/protocol.md](https://github.com/nickderobertis/onejudge/blob/v0.4.0/docs/protocol.md)),
so any command can stand in for the harness — which is how the engines that
dispatch prove themselves in their own repositories. What this repository's own
suite drives is the layer above: the real recipes, the real wrapper scripts, and
the **real** `oneharness` CLI over this repository's own configs and fallback
chain, with the paid model replaced at oneharness's own shipped mock-responder
seam (`tests/e2e/mock_oneharness.py`, `tests/e2e/fake_codex.py`). That is the one
sanctioned mock of a genuinely external service; the wrapper, the CLI, the chain,
and the recorded history all run for real, and the fixtures reject any CLI whose
version is not the adopted `config/oneharness.version` value.

**That substitution needs two seams now, and knowing which covers what is the whole
point.** A launch journey pins `ONEAGENTGRAPH_ONEHARNESS_BIN` at
`tests/e2e/fake_backend.py`, which delegates to the real CLI under `--mock-harness`.
It covers every turn `oneagentgraph` reaches by spawning a CLI — which, since
`oneagentgraph` 0.2.18, is no longer all of them: a single-sided `kind: oneharness`
member (this repository's `check-in` pacemaker) runs its turn through the oneharness
*library* on a thread of the graph process, so that variable never reaches it, and the
member would otherwise have gone straight to a paid subscription. What is still a
process there is the provider itself, so the journeys also pin `ONEHARNESS_BIN_CODEX`
at `tests/e2e/fake_codex.py` — every config in this repository names `codex` first, so
pinning that identity's binary is what keeps a suite run off a subscription. The two
do not collide: measured against oneharness 0.10.1, a harness selected with
`--mock-harness` keeps the mock binary and ignores `ONEHARNESS_BIN_<ID>`, so the
two-party path is unaffected by the second pin.
