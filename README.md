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
just bootstrap          # install the adopted onejudge SDK/CLI + sync the Python env
just check              # deterministic gate (format, lint, types, personas, tests)
just gate               # complete pre-push gate, including llmlint

# Dispatch one subtask with a persona (task passed over the CLI):
just dispatch engineer "Add a /health endpoint and test it."

# Run one recorded graph mixing agents, repos, and human gates:
just run-plan examples/tracked-graph.example.json

# After doing a reported human action, attest it and release its dependents:
just next-round <run-id> --complete-human release-approval

# Integrate completed workstreams for a repo explicitly registered local:
just integrate claude/api claude/docs --push

# Verify and publish a lifecycle-preserved branch through its registered workflow:
just repo-recover ai-orchestrator/engineer/abc123 --repo /path/to/checkout

# Self-dispatch from a safety clone while publishing through the canonical identity:
just repo-task /path/to/canonical engineer - \
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
| `config/onejudge.version` | The exact supported onejudge SDK/CLI version. |
| `personas/` | Per-persona onejudge deltas (roles). See [`personas/README.md`](personas/README.md). |
| `oneharness.toml` / `oneharness.judge.toml` | The two conversation sides (agent / judge) — harness + model selection. |
| `orchestrator/` | The mechanics: base⊕persona merge, tracked mixed-graph scheduling, lifecycle publication, and run ledger. |
| `docs/` | [Tracked graph model](docs/orchestration.md) · [repository lifecycle](docs/repo-lifecycle.md) · [onejudge integration](docs/onejudge-integration.md) |

## DAG Observatory

The web UI in `apps/dag-ui` shows current and historical DAGs grouped by their
launching Claude or Codex session. It provides a live React Flow graph, node
tasks and results, PR/check/gate/log detail, per-role oneharness transcripts,
and a whole-run planner view.

With the read-only telemetry server listening on its default loopback address,
run:

```sh
just bootstrap
just dag-ui
```

Then open the address Vite prints. See [`docs/dag-ui.md`](docs/dag-ui.md) for
addresses, usage, production build, and verification details.

## One tracked graph

`just run-plan` is the canonical executor. Omitted `kind` means `agent`: without
`repo` it dispatches onejudge directly, while with `repo` it runs the isolated
clone→gate→publish lifecycle. `kind: human` records an action for a person and
never invokes a harness. Lifecycle nodes may contain a nested `steps` DAG mixing
agent and human steps on one resumable branch.

Every invocation is recorded under `runs/<run-id>/round-NN` unless `--no-record`
is passed. Results have top-level state `complete`, `waiting`, or `failed` and
node statuses `done`, `waiting`, `blocked`, `failed`, or `skipped`. Waiting output
names the action and what it directly unblocks; blocked nodes name the transitive
human references in `blocked_by`. A complete graph exits 0, waiting or failed
exits 1, and invalid input exits 2. See the
[orchestration model](docs/orchestration.md) for tracking and attestation details.

## Status

An early, local, private proof-of-concept — "enough to prove the setup natively
manages multiple onejudge processes." The full gate runs locally (`just check`);
CI and repo governance are deliberately deferred (see the "Stack and composition"
section of [`AGENTS.md`](AGENTS.md)). The e2e suite drives the real onejudge CLI
with only the paid model faked.
