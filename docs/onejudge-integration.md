# onejudge integration

How this repo calls [onejudge](https://github.com/nickderobertis/onejudge) and how
the two conversation sides are wired.

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

Edit those two files (or use oneharness's `ONEHARNESS_*` env overrides) to change
the harness or model on either side. `config/onejudge.base.yaml` carries only the
loop's own concerns (persona defaults, session), never harness/model selection.

## How a persona becomes a run

```
config/onejudge.base.yaml   (shared: provider, agent preamble, defaults)
        ⊕
personas/<name>.yaml        (delta: agent role + supervisor persona)
        ⊕
--task "<the subtask>"       (passed over the CLI, never merged into a file)
        ↓  orchestrator.config.build_effective_config
effective onejudge config   →  onejudge run <cfg> --task -   →  JSON report
```

`dispatch` writes the effective config to a temp file and runs
`onejudge run <cfg> --task - --format json`, feeding the task on stdin so long,
multi-line tasks need no shell quoting. The report is parsed back into a `Report`
(completed? / verdicts / usage). Exit codes mirror onejudge: `0` completed, `1`
hit the turn cap, `2` bad config (raised as a `DispatchError`).

## The init process

`onejudge init` scaffolds all three files — `oneharness.toml`,
`oneharness.judge.toml`, and a starter `onejudge.yaml` — by shelling out to
`oneharness init` (needs **oneharness 0.3.20+**):

```sh
onejudge init --force    # writes the two oneharness configs + a starter onejudge.yaml
onejudge schema          # the annotated, authoritative config reference
```

The committed `oneharness.toml` / `oneharness.judge.toml` are **that init output**
with two deliberate edits: the judge side runs a cheaper model than the agent, and
both add an `IS_SANDBOX` env so claude-code runs under root. init's starter
`onejudge.yaml` is not kept — `config/onejudge.base.yaml` supersedes it as the base
this repo merges personas onto.

**Getting an init-capable oneharness on this box.** The prebuilt oneharness
release binary needs a newer glibc than the host provides, and the crates.io build
lags behind the 0.3.x releases that added `init`. The **PyPI `oneharness-cli`
wheel** (a manylinux build) is the one that both runs on the host's glibc and
carries `init`, so `scripts/session-setup.sh` installs it with
`uv tool install --upgrade 'oneharness-cli>=0.3.20'`.

## Harnesses and the live path

Live dispatch drives a real harness, chosen by `oneharness.toml`'s fallback chain
(`codex` primary, `claude-code` secondary). The offline gate needs neither; the
live path does, and each harness has an **environment requirement** for its tools
to actually execute:

- **codex** runs as its own process and executes tools directly, so it is the
  preferred nested harness. But it sandboxes via **bubblewrap**, which needs
  **unprivileged user namespaces**. Where the host disallows them (e.g. Ubuntu's
  AppArmor `restrict_unprivileged_userns`), codex can't create its sandbox and
  falls back to read-only — file/shell writes are blocked. The loop still runs
  (agent turns, supervisor, judge, report all work — verified live), only the
  agent's writes fail. Fixes: run on a host that allows unprivileged userns, or
  set codex to a no-OS-sandbox mode (`~/.codex/config.toml sandbox_mode =
  "danger-full-access"`) — a deliberate safety reduction, so opt in knowingly.
- **claude-code** works standalone, but **inside a bridged/managed Claude Code
  session its nested tool calls are deferred to the outer controller**
  (`stop_reason: tool_deferred`) and never execute — so an orchestrator running
  *inside* such a session cannot dispatch tool-using claude-code agents. Run the
  orchestrator from a standalone shell (or CI) for claude-code dispatch to work.

Net: the orchestration setup is harness-agnostic and correct; whether live
tool-using work completes depends on running in an environment that satisfies one
harness's requirement above.

## Testing against onejudge without a paid model

onejudge's `command` provider speaks a small JSON-lines protocol
([docs/protocol.md](https://github.com/nickderobertis/onejudge/blob/main/docs/protocol.md)),
so any command can stand in for the harness. The e2e suite points it at
`tests/e2e/fake_backend.py` — a deterministic backend — so the gate drives the
**real** onejudge CLI and loop across a real subprocess boundary, faking only the
paid model/harness. This is the one sanctioned mock (a genuinely external service),
and it is confined to the provider seam; the merge, dispatch, and report parsing
all run for real.
