# ai-orchestrator

A local **orchestration harness**: an orchestrator agent takes one large task,
splits it into a dependency graph of smaller subtasks, and drives each to
completion by dispatching a coding agent with a fitting **persona**, supervised by
a simulated user that pushes back until the subtask is actually done. Independent
subtasks run in parallel; dependents wait only for what they need.

The engines are published and installed, not built here:
[onepipeline](https://github.com/nickderobertis/onepipeline) owns the plan, the
continuous reconciler that drives it, and the planner channel;
[oneagentgraph](https://github.com/nickderobertis/oneagentgraph) owns the
dispatches; [onevcs](https://github.com/nickderobertis/onevcs) owns repository
identity and publication; and
[onepipeline-ui](https://github.com/nickderobertis/onepipeline-ui) owns the read
API and the browser view. What lives here is the configuration layer over them:
the personas, the harness routing, the llmlint tier, and a `just` recipe per verb.

The orchestrator is an agent following [`AGENTS.md`](AGENTS.md).

## Quick start

On a brand-new machine, do the manual one-time steps in
[`docs/host-setup.md`](docs/host-setup.md) first — harness logins, workspace trust,
and the per-machine repository registry are not in this repository.

```sh
just bootstrap          # install the adopted CLIs + sync the Python env
just check              # deterministic gate (format, lint, types, personas, tests)
just gate               # complete pre-push gate, including llmlint

# Launch one example project mixing agents, repos, and human gates, and supervise it:
just orchestrate examples:tracked-release

# One subtask is a one-task project — the same engine, the same ledger:
just orchestrate examples:scheduler-research
just orchestrate examples:health-endpoint

# Read the next planner surface, and reply to it:
just channel-next <run-id>
just channel-approve <run-id>

# Integrate completed workstreams for a repo whose rules say local:
just integrate claude/api claude/docs --push

# Verify and publish a lifecycle-preserved branch through its registered workflow:
just repo-recover ai-orchestrator/engineer/abc123 --repo /path/to/checkout
```

## How it fits together

| Piece | What it is |
| --- | --- |
| [`AGENTS.md`](AGENTS.md) | The orchestrator's durable instructions: decompose → schedule → dispatch, and the granularity judgment. |
| `config/onejudge.base.yaml` | The one base config: settings common to every subtask. |
| `config/*.version` | The exact adopted release of every tool session setup installs from PyPI: onejudge, oneharness, oneagentgraph, onevcs, onepipeline, and onepipeline-ui. |
| `personas/` | Per-persona onejudge deltas (roles). See [`personas/README.md`](personas/README.md). |
| `graphs/` | The three agent graphs a run launches — `dag-scope.yaml` for the monitor and its pacemaker (one config each, differing only in the per-turn deadline), `node-scope.yaml` for every dispatched node, and `pr-author.yaml` for the body each remote change request opens under. `onepipeline` ships these *paths*, not the files, because they name the configs and personas above. |
| `oneharness.toml` / `oneharness.judge.toml` | The two conversation sides (agent / judge) — harness + model selection. |
| `oneharness.orchestrator.toml` / `oneharness.check-in.toml` | The two dag-scope members' sides. Same routing; only the per-turn deadline differs, and [that is deliberate](docs/onejudge-integration.md#choosing-a-deadline-per-side). |
| `justfile` | The command surface: one thin wrapper per published verb, plus this repository's own quality tier. |
| `orchestrator/` | What this layer still decides on its own: the history-label contract and the redaction rule. |
| `docs/` | [Host setup](docs/host-setup.md) · [tracked graph model](docs/orchestration.md) · [repository lifecycle](docs/repo-lifecycle.md) · [onejudge integration](docs/onejudge-integration.md) · [DAG Observatory](docs/dag-ui.md) |

## DAG Observatory

The published browser view shows current and historical DAGs grouped by their
launching Claude or Codex session: a live graph, node tasks and results,
PR/check/gate/log detail, per-role transcripts, and a whole-run planner view.

```sh
just telemetry-server   # the published read API over ./runs
just dag-ui             # the published bundle, on the same origin
```

Then open the address `just dag-ui` prints. See [`docs/dag-ui.md`](docs/dag-ui.md).

## One tracked graph

`just orchestrate` launches one. Omitted `kind` means `agent`: without `repo` it
dispatches directly, while with `repo` it runs the isolated clone→gate→publish
lifecycle. `kind: human` records an action for a person and never invokes a
harness. Lifecycle nodes may contain a nested `steps` DAG mixing agent and human
steps on one resumable branch.

Every run is recorded, and `just runs`, `just status`, `just monitor`, `just
results`, and `just goals` are the read-only views over that record. See the
[orchestration model](docs/orchestration.md) for tracking and attestation details.

## Status

An early, local, private proof-of-concept — "enough to prove the setup natively
manages multiple onejudge processes." The full gate runs locally (`just check`);
CI and repo governance are deliberately deferred (see the "Stack and composition"
section of [`AGENTS.md`](AGENTS.md)). The e2e suite drives the real recipes, the
real harness CLI, and real Nx, doubling only the paid model and the published CLIs
each recipe delegates to.
