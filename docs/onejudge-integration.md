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
`oneharness.judge.toml`, and `onejudge.yaml` — by shelling out to `oneharness init`
(needs **oneharness 0.3.20+**):

```sh
onejudge init            # writes the two oneharness configs + a starter onejudge.yaml
onejudge schema          # the annotated, authoritative config reference
```

**Why the configs here are hand-maintained.** This box has no init-capable
oneharness that runs on its glibc: the prebuilt oneharness 0.3.21 needs a newer
glibc than the host provides, and the crates.io build lags behind the 0.3.x
releases that added `init`. So `oneharness.toml` / `oneharness.judge.toml` /
`config/onejudge.base.yaml` are committed as the equivalent of `init`'s output.
Where a newer oneharness is available, `onejudge init --force` regenerates them.

## Testing against onejudge without a paid model

onejudge's `command` provider speaks a small JSON-lines protocol
([docs/protocol.md](https://github.com/nickderobertis/onejudge/blob/main/docs/protocol.md)),
so any command can stand in for the harness. The e2e suite points it at
`tests/e2e/fake_backend.py` — a deterministic backend — so the gate drives the
**real** onejudge CLI and loop across a real subprocess boundary, faking only the
paid model/harness. This is the one sanctioned mock (a genuinely external service),
and it is confined to the provider seam; the merge, dispatch, and report parsing
all run for real.
