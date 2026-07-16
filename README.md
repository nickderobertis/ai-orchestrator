# ai-orchestrator

A local **orchestration harness** over
[onejudge](https://github.com/nickderobertis/onejudge). An orchestrator agent
takes one large task, splits it into a dependency graph of smaller subtasks, and
drives each to completion by dispatching a onejudge process with a fitting
**persona** — a coding agent supervised by a simulated user that pushes back until
the subtask is actually done. Independent subtasks run in parallel; dependents
wait only for what they need.

The orchestrator is an agent following [`AGENTS.md`](AGENTS.md); this repo gives it
the config, personas, and deterministic scripts to do the mechanical parts.

## Quick start

```sh
just bootstrap          # install adopted onejudge v0.3.0 + sync the Python env
just check              # deterministic gate (format, lint, types, personas, tests)
just gate               # complete pre-push gate, including llmlint

# Dispatch one subtask with a persona (task passed over the CLI):
just dispatch backend-engineer "Add a /health endpoint and test it."

# Run a whole task DAG in parallel:
just run-plan examples/plan.example.json

# Integrate completed workstreams for a repo explicitly registered local:
just integrate claude/api claude/docs --push

# Verify and publish a lifecycle-preserved branch through its registered workflow:
just repo-recover ai-orchestrator/backend-engineer/abc123 --repo /path/to/checkout

# Self-dispatch from a safety clone while publishing through the canonical identity:
just repo-task /path/to/canonical backend-engineer - \
  --execution-checkout /path/to/safety-clone

# Deliberately change publication policy for every alias of one repository identity:
just migrate-repo-workflow local/ai-orchestrator --workflow local
just migrate-repo-type local/ai-orchestrator --repo-type single-owner
```

## How it fits together

| Piece | What it is |
| --- | --- |
| [`AGENTS.md`](AGENTS.md) | The orchestrator's durable instructions: decompose → schedule → dispatch, and the granularity judgment. |
| `config/onejudge.base.yaml` | The one base config: settings common to every subtask. |
| `config/onejudge.version` | The exact supported onejudge CLI version (`0.3.0`). |
| `personas/` | Per-persona onejudge deltas (roles). See [`personas/README.md`](personas/README.md). |
| `oneharness.toml` / `oneharness.judge.toml` | The two conversation sides (agent / judge) — harness + model selection. |
| `orchestrator/` | The mechanics: base⊕persona merge, single dispatch, DAG scheduler. |
| `docs/` | [Orchestration model](docs/orchestration.md) · [onejudge integration](docs/onejudge-integration.md) |

## Status

An early, local, private proof-of-concept — "enough to prove the setup natively
manages multiple onejudge processes." The full gate runs locally (`just check`);
CI and repo governance are deliberately deferred (see the "Stack and composition"
section of [`AGENTS.md`](AGENTS.md)). The e2e suite drives the real onejudge CLI
with only the paid model faked.
