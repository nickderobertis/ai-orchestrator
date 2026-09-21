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
member. The `check-in` pacemaker scheduled beside it in that same
graph has a fourth, `oneharness.check-in.toml`, which is that routing verbatim and
differs in one field only; [Choosing a deadline per
side](#choosing-a-deadline-per-side) is why, and re-merging the two is a silent
regression rather than a tidy-up.

One role pairs those two sides the **other way round**, and it is the only one here
that does. `graphs/design-doc.yaml` runs the role that reads a finished plan and writes
the one short design document a person reviews it as: its agent side is
`oneharness.design-doc.toml`, which leads with both Codex identities, and its judge side
is `oneharness.design-doc-judge.toml`, which leads with both alternate Claude
subscriptions. The reason is what that judge is asked to do — decide whether the
document reads plainly to a technical product manager with no depth in the domain — so
this host puts its Claude subscriptions on the side making that call and Codex on the
side writing to the template. That judge side is also the one supervisory side here
that keeps the working model tier rather than `oneharness.judge.toml`'s cheaper one,
because this review *is* the deliverable's quality bar. Both name all six identities
with the primary Claude subscription last, and both state their own finite deadline;
neither is shared with any other member, for the reason the pacemaker's is not.

Edit those files to change the harness or model on a side for every run on this
host. One run changes it with graph config-ref overrides, the only way to give
the two sides *different* identities without moving concurrent runs; that pair is
specified under Harnesses below. `config/onejudge.base.yaml` carries only the loop's own
concerns (persona defaults, session), never harness/model selection.

### The judge side is the one named `oneharness.judge.toml`

The engine starts both sides as plain `oneharness`. `config/onejudge.base.yaml` names
`bin: oneharness`, and a live dispatch's process tree on this host, measured against
onepipeline 0.41.0, reads `onepipeline drive` → `oneharness run --format json --compact
--events --history --config <member-scratch>/oneharness.toml` → the provider, with no
process of this repository's between them. Which side a turn is, is which config it was
handed: `config/onejudge.base.yaml` pins `provider.judge_config: oneharness.judge.toml`,
and `oneagentgraph` normalizes each member's configs into its scratch under exactly those
two basenames — give a graph `worker.toml` and `judge.toml` through `--set` and the
member directory still holds `oneharness.toml` and `oneharness.judge.toml`. A two-party
member gets both files; a single-sided one gets only the agent's, which is how
`tests/e2e/fake_backend.py` — the double for the paid model at that seam — tells a
pacemaker turn from a two-party agent turn, and a judge turn from either.

### The judge side may be a list of judges

The pinned onejudge accepts a judge side that is a **list** of judges rather than one:
an LLM simulated user, an `llmlint` run over the worker's tree, a custom command
speaking onejudge's command protocol, or several of any of them. Every judge runs
against the same worker turn at once and the panel waits for all of them; when any says
the work is not done, the worker gets one message combining the failing judges' own
instructions, each under a ``## Judge `<label>` (<kind>)`` header, and the report's
`judge_decisions` records which judge decided what on each turn. A single `judge:` is
the one-element shorthand for the list.

This host would reach it through a `kind: onejudge` member's `judge:` in a graph,
written as a list, and never through a persona — a persona may not carry `provider`,
and `oneagentgraph` refuses one that does:

```yaml
members:
  worker:
    kind: onejudge
    base_config: ./onejudge.base.yaml
    agent: {oneharness_config: ./oneharness.toml}
    judge:                                            # one side, as every graph here writes it — or a list
      - oneharness_config: ./oneharness.judge.toml    # a harness side: optional `model`, `label`
      - kind: llmlint                                 # an llmlint side: optional `config`, `bin`, `diff_base`, `args`, `label`
        config: ./llmlint.yml
        diff_base: origin/main
      - command: [my-judge, --flag]                   # a command side: optional `label`
```

**This host stacks none today.** `config/onejudge.base.yaml`'s `provider:` names the one
`oneharness.judge.toml`, every member of the graphs under `graphs/` keeps a single judge
side, and every dispatch keeps its single simulated user; stacking one is a change to a
graph and a manager's decision. The shape is stated in [onejudge v0.13.3's
`judges.md`](https://github.com/nickderobertis/onejudge/blob/v0.13.3/docs/judges.md)
— the config, how a panel decides, and what each surface carries per judge — and, for a
graph member, in [oneagentgraph v0.4.6's
`contract.md`](https://github.com/nickderobertis/oneagentgraph/blob/v0.4.6/docs/contract.md).
`tests/e2e/test_judge_panel_e2e.py` drives the pinned `onejudge run` over a two-judge
list, a single `judge:`, and a single provider, offline.

## Provider wiring

This repository uses three onejudge provider arrangements:

- `oneharness` is the live worker path. Its agent and simulated-user sides use
  the two harness configs above.
- `command` is the deterministic test path. A local JSON-lines process stands in
  for the paid harness boundary.
- The monitor is a two-sided onejudge member whose judge side is the **live
  planner**: its agent side runs under `oneharness.orchestrator.toml`, and its
  judge side is a command provider running `onemessagebus serve surfaces --codec
  monitor` over `config/onemessagebus.yaml` (`graphs/dag-scope.yaml` names it) — the
  bus's generic binding interpreter running the `monitor` binding this host declares
  there, under the grammar onemessagebus's `codecs.md` states. The binding reads
  what onejudge reports on each frame rather than anything it derives: a `supervisor`
  frame whose `turn.outcome` is `taken` is answered with a non-completion and raises
  nothing, one whose outcome is `lost` raises one `monitor-failed` surface naming the
  cause and harness onejudge reported and ends the member, and a `judge` frame asks the
  completion bar as a non-blocking `monitor-completion` question whose ruling is
  relayed as the score. The onejudge codec it replaced is history.
  <!-- llmlint: ignore[no_redundant_instruction_pointers] The binding grammar has one source, onemessagebus's `codecs.md`, and `contracts_have_one_source_or_a_drift_gate` is why it is not restated further here; this names where this host's account of the binding lives rather than re-advertising the document. -->
  [Serving the channel as the monitor's judge
  side](orchestration.md#serving-the-channel-as-the-monitors-judge-side) holds what
  each frame gets, and what the filter it replaced measured.

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
the catalog's spelling and not a plan node's — a node names a built-in role or a
path relative to `graphs/`, never that name.

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

The committed base and every persona follow the adopted onejudge schema directly:
since `oneagentgraph` 0.3.0 a persona **is** a onejudge config fragment, so a role is
written as the top-level `system_prompt` onejudge itself names and there is no
translating vocabulary in between. An `agent:` block in either document is refused
outright, naming the field to write instead. The real-CLI e2e suite checks these
schema and CLI surfaces before it drives the same SDK-to-CLI path used in production
dispatch.

<!-- llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] Per-release claims, each cited to its release, opened by the sentence `tests/test_onejudge_version.py` holds to `config/oneharness.version`: a bump fails there and re-opens this paragraph. -->
**Getting the adopted oneharness on this box.** The prebuilt oneharness
release binary needs a newer glibc than the host provides, and the crates.io build
lags behind the 0.3.x releases that added `init`. The **PyPI `oneharness-cli`
wheel** (a manylinux build) is the one that both runs on the host's glibc and
carries `init`, so `scripts/session-setup.sh` installs the exact
`config/oneharness.version` release and rejects a stale binary. Version 0.16.0 is
the adopted release, and what it adds is the **per-run pointer line**
([oneharness#1326](https://github.com/nickderobertis/oneharness/pull/1326)): a run that
is handed `ONEHARNESS_HISTORY_POINTER_FILE` appends one typed line per harness run to
that file, naming the history session it wrote, and `oneharness history pointers <file>`
reads them back. That is what makes this pin decide something here rather than nothing:
a two-party member's turn spawns this CLI from `PATH`, so it is **this** release — not
`config/onepipeline.version` — that decides whether the agent and judge sides of every
dispatch appear in their run's pointer file at all, while the engine's pin decides it
for the observer's and the drafter's in-process turns. Moving one and not the other
leaves a reader seeing half the agents. The two defaults 0.15.0 flipped
([oneharness#1316](https://github.com/nickderobertis/oneharness/pull/1316)) still stand
and still move no turn here — a `run_mode` nothing set resolves to `fallback`, with
`parallel` the opt-in, and `oneharness run`'s stdout is a human-readable view unless the
reader names the JSON contract (`--format json`, or `--compact`, which alone selects
it) — because every `oneharness.*.toml` sets `run_mode = "fallback"` explicitly, and every
reader of the CLI's stdout in this repository asks for JSON by name — the one bare parser,
`orchestrator/plan_review.py`, spawns `--format json` since this adoption, the
wrappers under `scripts/` forward the `--compact` or `--stream` their callers send (the
onejudge the engine links sends `--compact` on every turn), and the suite's own
journeys that parse a `--print-command` plan or the `list` catalogue name `--format
json` too, because every verb that prints a JSON document flipped, not `run` alone — a
bare reader there was what refused this adoption's first publication. A hand-run `oneharness run`
or `oneharness config` at a shell prints the text view, which is the point; pipe it
into `jq` only under `--format json`, as the examples below do. The linked
`oneharness-core` reaches a dispatch through `config/onepipeline.version` alone, and the
engine adopted here still links a core from before the flip, which changes nothing
either, for the same reason: no config here leaves `run_mode` to the default. The
adoption before this one, 0.14.0, added **how a reader asks the CLI for its output
shape**: the `--format` flag on every verb that prints a JSON document to stdout, with
both defaults left where they were
([oneharness#1312](https://github.com/nickderobertis/oneharness/pull/1312)). It is also
the floor the judged lint tier holds this pin to: `llmlint doctor`, run the way `just
lint-llm` runs it, refuses an older `oneharness` by version before spawning it and names
the floor it wants, so until this pin reached 0.14.0 no branch whose merge path runs
`just lint-llm-diff` through this host's wrapper could publish from this host. The
adoption before that one, 0.12.1, changed **which model a controlled codex turn runs
under**: a `run --control` turn driven over `codex app-server` carries the selected
candidate's own model — `[harness.codex].model`, a variant's `model`, or `--model` — on
`thread/start`, `thread/resume` and `turn/start`, records the model the server says the
thread runs under as `observed_model` beside the requested one, and refuses a thread the
server would run under another model before any turn is sent, classified
`model_mismatch` and falling through a chain as `model-mismatch`
([oneharness#1284](https://github.com/nickderobertis/oneharness/pull/1284)). Until it,
the control path was handed the run-level model alone, so a per-harness `model` reached
the record and never the wire and codex ran its own default — which is how every
codex-first supervisory side here spent Astra while its config and its record said Sol.
The release beside it makes a stopped fallback chain say why it stopped
([oneharness#1286](https://github.com/nickderobertis/oneharness/pull/1286)): the summary
names the identity, its status and its diagnostic, and tells a task failure from a
failure with no observed work and from an unclassified failure after work. The
adoption before that one changed a **classification**: a candidate whose turn
completed and was billed for is no longer reported as a failure, and a failed release is
reported as one ([oneharness#1277](https://github.com/nickderobertis/oneharness/pull/1277)).
That is the same distinction the stopped-without-work reading below is about, applied to
the other side of it — a turn with work behind it is not the untried chain. What 0.11.0
changed reaches this host's **monitor** rather than
its workers: a named session continued under `--control` now genuinely continues the
same conversation, and a control mechanism whose protocol has no resume request
refuses the continuation instead of silently opening a new conversation while the
store, the report and the flag all read healthy. Measured on the installed binary,
which warns at the *first* turn rather than only refusing the second:
<!-- llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate] -->

```
$ ONEHARNESS_HARNESSES=goose oneharness run --control --session probe --prompt hi
  warning: session `probe` starts a NEW conversation on `goose`, and its control
  mechanism `acp-cancel` implements no resume request — so the next
  `--control --session probe` turn will be refused rather than silently starting
  over. Mechanisms that continue a session under --control: claude-code, codex
$ ONEHARNESS_HARNESSES=claude-code oneharness run --control --session probe --prompt hi
  (no warning: `claude-control-request` is one of the two that resume)
```

That naming is the reading to keep: the two mechanisms that continue are
`claude-code`'s and `codex`'s, and every chain this repository ships names only those
two families, so no side here was in the silently-restarting case. Read the warning as
a guard against a *third* family being introduced rather than as a defect being fixed
on this host.

**The stopped-without-work reading arrived one release earlier and is unchanged
here.** A fallback chain stops at a candidate whose failure it cannot classify, and
until 0.10.3 the report said only `ran but did not succeed` — the same sentence a
genuine task failure gets. From 0.10.3 a candidate that showed nothing for itself says
so, with `fallback.stopped_without_work` and `results[].work` of `none` carrying the
same reading into the report and the history record at schema 1.7. Both fields are
additive and declared only on a record that *has* one, which is what keeps an older
reader whole. 0.12.1 finishes the sentence side of that
([oneharness#1286](https://github.com/nickderobertis/oneharness/pull/1286)): a
candidate that did the task's work and failed for a cause nothing could classify now
gets a sentence of its own rather than the task-failure one, and every stop summary
carries the candidate's status and its own words, so which of the three stops it was is
readable without opening the report. **What is no longer true here is that there is such an older reader.**
Through an earlier adoption the `oneagentgraph` this host's
[smoke](#the-record-a-fallback-chain-is-judged-by) judges by linked a `oneharness-core`
a release behind the CLI it spawns; read from both installed wheels' own SBOMs under this
adoption, the `oneagentgraph-cli` wheel and the `oneharness-cli` wheel each declare a
`oneharness-core` of their own, a release apart again and in the same direction as the
adoption before, where the one before that had the pair a release apart the other way
and the one before that had it equal. They are separate artifacts on separate cadences,
so read either the equality or the gap as a coincidence rather than as a rule; this pair
has now been both, four times. Nothing
about that rests on an adopter remembering to check: the pre-push hook selects
`just smoke` for any diff touching `config/oneharness.version`, so the next bump proves
the pairing on a real turn or does not reach the remote.

It succeeds 0.10.3, whose stopped-without-work reading the paragraph above keeps, and
0.10.2 behind that, which puts a pre-spawn/post-spawn hook pair on `RunControls`, so a
library **embedder** owns the harness child a run starts rather than losing it to the
process tree. That is not an abstract capability here — it is the compile floor
`oneagentgraph` 0.2.18 names for converting a single-sided member's turn from a
spawned `oneharness` CLI into `oneharness_core::io::run::run_supervised`, which is
why this repository's own paid-model substitution had to grow a second seam (see
[Testing against a harness without a paid model](#testing-against-a-harness-without-a-paid-model)).
Behind it, 0.10.0 **bounds a control socket's address at construction**:
`sockaddr_un.sun_path` is 108 bytes on Linux and 104 on the BSD lineage, `bind`
past it fails with `ENAMETOOLONG`, and a session name one byte too long had turned
every controlled dispatch on a host into an unreachable run. That release is why a
dispatched worker here now reports a control address rather than a reason it could
not have one — measured on a real launch, below. Behind that, 0.9.0 refuses a
contradictory option pair (`{all: true, harnesses: ["codex"]}`) instead of dropping
one half and running the other, so a turn nobody asked for cannot be billed and
reported as a success. Each of those three is a major bump for a Rust consumer
matching `oneharness-core`'s types exhaustively and nothing at all for a consumer of
the CLI, which is what this repository is. Behind them, 0.8.0 binds a controlled
turn's mechanism to the **candidate serving it** rather than to the chain as a whole.
The validator had required one mechanism across every candidate, which a fallback
chain can never satisfy once it mixes harness families, so the mixed-family chains on
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
**Every role names the same six identities** — `claude-code:alternate`,
`claude-code:alternate2`, `codex:primary`, `codex:alternate`,
`claude-code:primary-backup`, `claude-code:primary` — and the roles differ only in the
order they try them. The worker order is that list as written: both alternate Claude
subscriptions first, because the personas are tuned against that model tier, then Codex,
then the primary-backup Claude subscription and the primary Claude identity as the last
resort. Every chain puts `claude-code:primary-backup` immediately before
`claude-code:primary`. A variant is a named per-harness preset selected as `<harness>:<variant>`;
it composes the base harness settings with child-only model, environment, and
credential routing.

**Every identity in every chain is a variant, and that is a requirement rather than a
style.** `unset_env`, `env_from` and `env_file` are declarable on a variant only, and a
top-level or per-harness `env` can only *set* a name — it cannot remove one and cannot
map one out of the parent process. So a chain naming a bare harness id carries one
candidate no per-identity environment rule can reach, which is why the first Codex
identity is `codex:primary` rather than `codex`: the board credential this host masks
and the runtime directory it repoints would otherwise have covered every candidate in
the chain but that one, which is reached once the subscriptions ahead of it are
spent. That variant declares no `unset_env` for `CODEX_HOME` on purpose — that value is
ambient configuration a developer may export, and this is the identity that honours
it — so the account behind it, and its position in every chain, are what they were.
`scripts/claude-alt-config-dir.sh` is the one source of every Claude identity's config
directory: it derives `ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR` as `$HOME/.claude-alt`,
`ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR` as `$HOME/.claude-alt2`,
`ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR` as `$HOME/.claude-primary-backup` and
`ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR` as `$HOME/.claude`. For each, a non-empty value
in the environment wins, then the host's identities file
(`$XDG_CONFIG_HOME/ai-orchestrator/claude-identities.env`, or the same path under
`$HOME/.config`), then that default. Every entry point that reaches oneharness sources it — the llmlint and
usage wrappers, the launch verbs, the plan reviewer, the drafter
the envelope validator and the smoke — because
oneharness refuses to start whenever a named variant's `env_from` source is unset in the
parent. Each variant maps its portable path to `CLAUDE_CONFIG_DIR`
only inside its own child and masks ambient Anthropic API/OAuth credentials so they
cannot outrank subscription auth. If one directory is absent, unauthenticated, or
quota-limited, fallback proceeds to the next candidate; a host with only its
primary Claude identity therefore still dispatches through an authenticated Codex.

### The primary-backup and primary Claude subscriptions

`claude-code:primary-backup` is a further Claude subscription, reached immediately before
`claude-code:primary` in every chain and credentialed from
`$ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR` (by default
`$HOME/.claude-primary-backup`). Its variant is its config's own `alternate` with that one
directory swapped, so it runs the same model under the same masks. A host that never
logged it in has no such directory: the worker's unselected chain drops it, and every
other entry point's candidate reports `auth` and falls through to the primary.

`claude-code:primary` reads its directory the same way, from
`ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR` (by default `$HOME/.claude`), rather than
unsetting `CLAUDE_CONFIG_DIR`. That is what lets a host whose `~/.claude` is logged in as
another account point both identities at the right directories in its identities file
instead of in a config every host shares. `tests/e2e/test_claude_identity_routing_e2e.py`
drives the refusal a missing indirection meets and the existing-host fall-through.

### The second Codex identity

Every role's chain names `codex:alternate`, a second Codex account that absorbs
an exhausted quota without changing which subscription that role competes for.
`scripts/codex-alt-home.sh` is its one source, the counterpart of the Claude helper
above: it derives `ORCHESTRATOR_CODEX_ALT_HOME` as `$HOME/.codex-alt` unless the
caller overrides it, and every entry point that reaches oneharness — the launch
verbs through `scripts/dispatch-env.sh`, the smoke, and the llmlint wrapper — sources
it. The variant maps that portable path to `CODEX_HOME` only inside the
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
unset in the parent, which is why every entry point exports it rather than only the
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
directory itself on first run, which is why there is nothing to pre-create.)

The **orchestrator** reverses the worker's order. It is a long-lived supervisory
process, not a worker, so `oneharness.orchestrator.toml` selects both Codex
identities first: it does not stand in front of the workers for the subscriptions
they depend on while Codex can still carry the role. Past that it does reach them,
in the worker's own relative order, and the operator accepts that trade —
contending for the workers' Claude quota beats stalling a supervisory process every
workstream waits on.
The monitor member names that config in `graphs/dag-scope.yaml`, and
`scripts/onepipeline.sh` establishes the same four shared
`ORCHESTRATOR_CLAUDE_*_CONFIG_DIR` indirections at driver start through
`scripts/dispatch-env.sh` — so `just orchestrate` launches on a fresh shell with
nothing exported by hand.

The **judge** leads with Codex for the same reason and, past both Codex
identities, now reaches the workers' alternate subscriptions before
`claude-code:primary`. It keeps its cheaper-supervisor intent through `model`
rather than through isolation: all four of its Claude variants are
`claude-sonnet-5`, where every other role uses `claude-opus-5`. The
`claude-code:primary` variant is env-driven like the others: it reads its
`CLAUDE_CONFIG_DIR` from `ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR` (by default
`$HOME/.claude`) and removes higher-precedence Anthropic credentials, so it runs as the
account that directory holds and never as an ambient key.

The **design-doc role reverses the reversal**, and is the one place on this host where
the identity order is chosen for what a side is good at rather than for what it must not
queue in front of. `oneharness.design-doc.toml` writes the document and leads with both
Codex identities; `oneharness.design-doc-judge.toml` reviews it and leads with both
alternate Claude subscriptions, on `claude-opus-5` rather than the judge config's
`claude-sonnet-5`. What that reviewer decides is whether the prose reads plainly to a
non-specialist, which is the document's whole purpose, so the cheaper-supervisor trade
every other judged tier makes is the wrong one here. Past their leading pair each reaches
the other provider's two identities and then `claude-code:primary-backup` and
`claude-code:primary`, so both name all six and neither loses a quota once everything ahead of it is exhausted.
`tests/e2e/test_design_doc_graph_e2e.py` reads both orders back through the graph that
routes them, and `tests/e2e/test_oneharness_timeout_e2e.py` reads them beside every other
config's from the real CLI.

**llmlint** uses `oneharness.llmlint.toml` through
`scripts/llmlint-oneharness.sh`, and is **no longer Codex-only**: it carries the
same six identities in the same supervisory order. That trade is deliberate — a
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
just orchestrate authoring:my-project \
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
`graphs/node-scope.yaml`.

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
just orchestrate authoring:my-project \
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

##### Why `ONEHARNESS_MODEL` is not the lever

`ONEHARNESS_MODEL` is *not* the counterpart of `ONEHARNESS_HARNESSES`, and reading it
as one is the trap this section exists for. Measured against the adopted oneharness
0.16.0, a config's per-harness `model` **beats** the variable, while the `--model`
flag on an invocation's own argv beats the config — a precedence that is a fact about
one release, so the literal above is derived from `config/oneharness.version` by
`tests/test_onejudge_version.py::test_the_model_precedence_claim_names_the_adopted_oneharness`
and an upgrade fails here until this measurement is redone:

```
$ ONEHARNESS_HARNESSES=claude-code:primary ONEHARNESS_MODEL=claude-opus-5 \
    oneharness run --config oneharness.judge.toml --print-command --prompt hi
  claude-code:primary [model claude-sonnet-5]: planned   # the config won
$ ONEHARNESS_HARNESSES=claude-code:primary \
    oneharness run --config oneharness.judge.toml --model claude-opus-5 \
    --print-command --prompt hi
  claude-code:primary [model claude-opus-5]: planned     # the flag won
```

That precedence decides what the *record* names, and until
[oneharness#1284](https://github.com/nickderobertis/oneharness/pull/1284) it decided
that alone on the one path every codex-first side here takes: a `--control` turn driven
over `codex app-server` was handed the run-level model — `--model`, else a top-level
`model` — and never the per-harness one that won above, so `thread/start` carried no
model and codex ran its own default while the record said the config's. On the
adopted CLI, and on the `oneharness-core` the adopted engine links, the controlled turn
runs under the same model the precedence above resolves, the server's own answer is
recorded beside it as `observed_model`, and a thread the server would run under
another model is refused before any turn is sent as `model_mismatch`, falling through
a chain as `model-mismatch`. `tests/e2e/test_controlled_turn_model_e2e.py` re-takes
that against the installed `codex app-server` offline: a config naming
`[harness.codex].model` and no run-level model, read off the request that reaches the
model endpoint and off the report.

So the lever is the graph-native `model` field above, which names the model on that
side's own `oneharness run`, where it decides the turn. A variable exported instead
would lose to every config here, and would still be inherited by **everything that
side subsequently runs** — a worker's own `just gate`, and any `llmlint` invocation
inside that gate.

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

With no model override set nothing changes here either: each side runs exactly the
model its config pins for the identity it landed on.

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
| design-doc writer | `oneharness.design-doc.toml` | 900s, stated |
| design-doc reviewer | `oneharness.design-doc-judge.toml` | 600s, stated |

The monitor is the exception because of what one of its turns *is*: a watch that
lasts as long as the run does — reading the detailed stream, judging it, and
surfacing what it finds — where a deadline would end the watching rather than bound
it. That stays right now that `graphs/dag-scope.yaml` paces the monitor one turn per
300 seconds: the hold is the graph's, kept *between* turns, and a per-turn deadline
never sees it — a turn opens when the hold ends and runs to the judge's answer as it
always did, so `timeout = 0` bounds nothing about the pacing and the pacing changes
nothing about what a turn is. Under the 120-second default three consecutive runs died, each reported only as
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
$ oneharness config --config oneharness.orchestrator.toml --format json | jq .timeout
  { "value": 0,   "source": "oneharness.orchestrator.toml" }
$ oneharness config --config oneharness.check-in.toml --format json | jq .timeout
  { "value": 240, "source": "oneharness.check-in.toml" }
$ oneharness config --config oneharness.toml --format json | jq .timeout
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
candidate it ended up running — through oneharness 0.16.0, confirmed against the binary:

```
$ ONEHARNESS_HARNESSES=codex,claude-code oneharness run --prompt hi   # fell through to codex
  the child saw ONEHARNESS_HARNESSES='codex,claude-code'
$ oneharness run --config <chain.toml> --prompt hi                    # chain from config
  the child saw no ONEHARNESS_HARNESSES at all
```

So a selection exported around a turn leaks: the provider *and everything that
provider then runs* — a worker's own `just gate`, and therefore this suite — inherit
it. `DISPATCH_SELECTION_ENV` in `tests/conftest.py` names oneharness's two
process-wide selections, `ONEHARNESS_HARNESSES` and `ONEHARNESS_MODEL`, and the
fixture over it drops both.

The two are told apart by **provenance, not by value**. Every selection is dropped at
each process boundary the suite owns — `tests/conftest.py` for its own environment,
and a journey that launches oneharness builds that launch's environment from nothing
rather than inheriting one (`tests/e2e/test_quota_fallthrough_e2e.py`'s `_chain_turn`
passes `PATH`, `HOME`, and the values it is asserting on, and no more) — and each
journey then states the value it wants; so a selection a recorded turn observes is one
that journey put there, and an inherited one reaches nothing.

Do not "adapt" a selection journey to a value you did not state. Reading the single
identity a run happened to be routed to means a selection leaked in, not that
oneharness narrowed one. That misreading has landed here once already, and adapting
the assertions is what removed the gate that would have caught it.

To address an identity explicitly in a diagnostic run, use the composed id:

```sh
ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR="$HOME/.claude-alt" \
  oneharness run --config oneharness.toml \
  --harness claude-code:alternate --prompt "Reply with OK"
ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR="$HOME/.claude-alt2" \
  oneharness run --config oneharness.toml \
  --harness claude-code:alternate2 --prompt "Reply with OK"
ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR="$HOME/.claude-primary-backup" \
  oneharness run --config oneharness.llmlint.toml \
  --harness claude-code:primary-backup --prompt "Reply with OK"
ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR="$HOME/.claude" \
  oneharness run --config oneharness.llmlint.toml \
  --harness claude-code:primary --prompt "Reply with OK"
ORCHESTRATOR_CODEX_ALT_HOME="$HOME/.codex-alt" \
  oneharness run --config oneharness.toml \
  --harness codex:alternate --prompt "Reply with OK"
```

A `codex:alternate`, `claude-code:alternate2` or `claude-code:primary-backup` probe that
reports `fell_through:
[{"harness": "...", "reason": "auth"}]` is the unauthenticated state, not a broken
config; run the `codex login` above, or for the second Claude plan:

```sh
CLAUDE_CONFIG_DIR="$HOME/.claude-alt2" claude
```

or for the primary-backup plan, `CLAUDE_CONFIG_DIR="$HOME/.claude-primary-backup" claude`,

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
prompt through plain `oneharness` under this repository's agent chain —
`oneharness.toml`, named as the user-level config through `ONEHARNESS_CONFIG`, with the
identity indirections `scripts/dispatch-env.sh` establishes for a launch — to the
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
command passed standalone moments before and after. On the adopted release that
symptom can arrive under another summary sentence instead — a contended candidate
that died before a tool call or a billed token now reads as having `nothing to show
for it`, and one that died after answering as having `did the task's work and did not
succeed`, since which of the three is printed turns on `fallback.stopped_without_work`
and the candidate's `work` rather than on the cause. All are the same host condition
and none is a launch defect. Since the pre-push hook selects
this smoke whenever the pushed diff touches `scripts/`, the worker generating that
load is usually the one whose publication it blocks. Nothing is relaxed by
retrying: a launch path that is genuinely broken fails every attempt and still
fails, a recorded turn that violates the contract above fails on the first attempt
without paying for a second, and a passing run reports how many launches it took.
The bounded relaunch itself is `oneagentgraph
smoke`'s own policy and is proven in that repository; what this repository's suite
drives is the recipe that spawns it — `tests/e2e/test_delegated_recipes_e2e.py` runs
`just smoke` through the real recipe and holds it to naming this repository's own
agent config, since the published verb runs `oneharness run` in a throwaway directory
where no project config is discovered.

The smoke spends a second turn beside that one, the **trust probe**: claude-code driven
as a dispatch drives it — `oneharness run --config oneharness.toml` naming only the four
claude-code identities, in bypass mode, so `claude -p --permission-mode
bypassPermissions` under one identity's config directory — in a fresh git directory
nothing has trusted, whose `.claude/settings.json` names a `SessionStart` hook writing a
marker. It fails when no claude-code candidate ran and when the marker is absent after
the turn. That hook running untrusted, under the bypass mode every dispatch runs, is why
nothing here marks a workspace trusted; the same delegated-recipes journeys hold the
probe to failing on either.
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
restore. A candidate that stopped the chain having shown *nothing* for itself is
still an unclassified candidate failure and still fails; what the adopted release
changed is that oneharness now says which of the two stops it was, rather than
handing the operator the sentence a real task failure gets.

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

`tests/e2e/test_quota_fallthrough_e2e.py` drives that path for real — the agent
config's chain, the classifier, and the record the verdict is read back out of — with
both candidates replaced at oneharness's own `ONEHARNESS_BIN_*` seam. Two things
make a journey of that shape possible to write safely, and both are easy to get
wrong:

- **Drop an inherited harness selection.** This repository runs its own suite from
  inside a dispatch, and a process-wide `ONEHARNESS_HARNESSES` beats config, so a
  journey that inherits one runs on whatever identity it names.
- **Name bare identities, never variants.** `ONEHARNESS_BIN_*` keys on a harness
  id and there is no spelling of it that reaches a variant —
  `ONEHARNESS_BIN_CLAUDE_CODE` leaves `claude-code:alternate` resolving to the real
  `claude`.

Together they are a money hazard rather than a style point: a journey that misses
either one spawns a live subscription with its double sitting unused, and a billed
run and a free one look identical from the assertions. The guard against the first
is that `_chain_turn` builds its launch environment from nothing, so there is no
inherited selection to apply over what the journey sets; against the second, that it
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

A planner supervises through what the harness reports, and a turn that reported
nothing until it *ended* was invisible for the 600-2000 seconds a dispatch here
routinely spends on turn one: `just status <run-id>` once printed "No dispatched tasks
recorded" for a lifecycle agent half an hour into its work, and a healthy node twice
got reported as possibly dead. `--events` is a **format** switch — it guarantees the
end-of-turn report carries the normalized tool-call transcript — and says nothing about
*when* that arrives; `--stream` delivers the same events **as they occur**, then a
final result line. Since [oneharness PR
#1213](https://github.com/nickderobertis/oneharness/pull/1213) a `run_mode =
"fallback"` chain streams, candidates run one at a time, and **a streamed chain selects
the same candidate a buffered one would**.

The linked engine is what carries it now, with nothing of this repository's in the
path. A member's `stream` is `oneagentgraph`'s default of `true` —
`graphs/node-scope.yaml` declares none — and onejudge at the pin reads oneharness's
streamed NDJSON itself (`provider.stream`, tolerant of a bare report). Each tool event
reaches the run's journal as oneagentgraph's `turn-activity` as it happens, which is
what `just monitor --all`, `just transcript` and the views read.
`tests/e2e/test_orchestrate_launch_e2e.py` reads `turn-activity` off a real launch, and
`tests/e2e/test_transcript_recipe_e2e.py` renders a journal's `turn-activity` through
the transcript recipe.

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
proof costs no provider turn. What the bound turn then *runs under* is the other
half of the same path, and it is the half a `--print-command` plan cannot show: the
model on a controlled codex turn is negotiated on the app-server wire rather than on
the argv, which is why it could carry none for as long as it did, and why the journey
that holds it reads the wire rather than a plan.

### Dispatch liveness and scratch are the engine's

What notices a dispatch that stopped, what owns its scratch directory and what tears
its process tree down are the linked engine's, and nothing here restates them: the
engine raises a non-blocking `quiet-worker` proposal for an in-flight dispatch that has
recorded nothing past `ONEPIPELINE_STALL_AFTER_SECONDS`, the graph applies its own
`ONEAGENTGRAPH_STALL_TIMEOUT` and `ONEAGENTGRAPH_HEARTBEAT_TIMEOUT`, and
`oneagentgraph`'s `src/scratch.rs` holds each member's scratch ownership and the
teardown that reads it. Read a dispatch's liveness from the run's own views rather than
from its process table.

## Dispatching playbook

- **Prepare the harness environment.** claude-code on the alternate subscription
  is the preferred worker; Codex is its fallback and the preferred judge.
  Codex installs in `~/.local/node/bin`. Keep that
  directory on `PATH` so worker fallback, supervision, and llmlint remain available;
  `scripts/session-setup.sh` persists the path. **Nothing here sets
  `ONEHARNESS_TIMEOUT` any more**, and nothing should: since oneharness 0.7.0 an
  absent per-turn deadline means *no* deadline, and the worker, judge, and llmlint
  configs take that default deliberately while `oneharness.orchestrator.toml`,
  `oneharness.check-in.toml`, `oneharness.pr-author.toml`,
  `oneharness.design-doc.toml`, and `oneharness.design-doc-judge.toml` set their own. The
  variable is process-wide for a whole graph run and beats every file, so setting it
  moves every member at once — see [Choosing a deadline per
  side](#choosing-a-deadline-per-side). `onepipeline start` takes no `--timeout`
  either; a node's turn budget is its `max_turns`. Project dispatch also pins the agent-side
  oneharness `--config` to this repo's config, which selects the configured
  alternate-subscription Claude model before the configured Codex fallback. A global
  `ONEHARNESS_MODELS` chain cannot be used here: onejudge supplies `--session`,
  and oneharness rejects multi-model runs combined with a named session.
- **A dispatched lifecycle member starts in its own worktree**, and the launch's
  journal is where that is read. `onevcs` appends `session-opened` naming the
  worktree it cut for the node's branch; `oneagentgraph` appends `member-started`
  naming the directory it started that node's `worker` member in; measured on
  onepipeline 0.3.1 the two are the same path, and the `--cwd` oneharness
  is handed for that member's agent turns is that path again — and
  `tests/e2e/test_worker_start_directory_e2e.py` re-takes that on every gate run
  against whatever release is adopted, so the number dates the first reading rather
  than naming the copy in force. Measured, not
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
  work is invisible to them. See the `examples:scheduler-research` and
  `examples:health-endpoint` projects.
- **Inspect the branch behind a `task-failed` node.** That outcome commonly means
  the agent hit its turn cap at the moment it finished, not that its work failed or
  vanished — onejudge exits 1 both ways. Agents commit incrementally, and the
  settlement pins the branch those commits are on, so check its commit delta before
  deciding whether to recover or redispatch. `task-failed-change-open` is the same
  reading with a change request already waiting: its URL is on the settlement, and
  re-running that work would duplicate a change somebody can already read.
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
([onejudge v0.13.3 docs/protocol.md](https://github.com/nickderobertis/onejudge/blob/v0.13.3/docs/protocol.md)),
so any command can stand in for the harness — which is how the engines that
dispatch prove themselves in their own repositories. What this repository's own
suite drives is the layer above: the real recipes, the real wrapper scripts, and
the **real** `oneharness` CLI over this repository's own configs and fallback
chain, with the paid model replaced at oneharness's own shipped mock-responder
seam (`tests/e2e/mock_oneharness.py`, `tests/e2e/fake_codex.py`). That is the one
sanctioned mock of a genuinely external service; the scripts, the CLI, the chain,
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
at `tests/e2e/fake_codex.py` — pinning that identity's binary is what keeps a suite run
off a subscription. That pin reaches the bare harness id and no variant of it, and every
identity in every chain here is now a variant, so what a journey's chain actually
selects is `codex:primary` and the pin never sees it: the stand-in reaches those
candidates through `PATH`, where `tests/e2e/no-paid-provider/` hands a variant on to the
very binary this variable names and refuses anything named outside this repository's
tests. The two
do not collide: re-measured against the adopted oneharness 0.16.0, a harness selected
with `--mock-harness` keeps the mock binary and ignores `ONEHARNESS_BIN_<ID>`, so the
two-party path is unaffected by the second pin. Held on both halves rather than on the
one that matters — the same chain without `--mock-harness` runs the pinned binary — so
a release that started honouring the pin under the mock is a difference this reading
would show rather than one it would absorb.
