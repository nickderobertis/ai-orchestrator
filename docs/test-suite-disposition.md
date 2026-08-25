# Test-suite disposition after the package split

This audit was made against `just --list`, not against the former Python command
surface. “Keep” means the subject is a decision made by this checkout. Upstream
paths name the evidence inspected in the sibling source checkout under
`~/projects`; they are not substitutes for this checkout's integration journeys.

## Measured before and after

| Measure | Base `fd7eb5a` | This change |
| --- | ---: | ---: |
| Source-level `def test_` / `async def test_` occurrences | 325 | 324 |
| Items from `pytest --collect-only -p no:xdist` | 507 | 507 |
| Lines across `tests/**/*.py` | 11,953 | 11,889 |
| `just check` wall-clock | 159.29 seconds | 167.13 seconds |

The task's stated 301-test count was stale: prior adoption commits `538d6ee`,
`a53b473`, and `85d7824` had already changed the suite before base `fd7eb5a`.
Definitions and collected items are both reported because parametrization makes
them different measures; the collected total stays level because three removed
items were replaced by three recipe-coverage items.

## File disposition

| Test file | Disposition | Evidence |
| --- | --- | --- |
| `tests/test_agent_allowlist.py` | Keep all | Drift gate for this checkout's `.claude/settings.json`. |
| `tests/test_coverage_gate.py` | Keep all | Proves this checkout's 100% floor is enforceable. |
| `tests/test_decomposition_guidance.py` | Keep all | Drift gate over this checkout's planner prose. |
| `tests/test_dispatch_cwd_does_not_shadow.py` | Keep all | Proves this checkout's agent wrapper and installed-CLI boundary. |
| `tests/test_dispatch_environment_contract.py` | Keep all | Reconciles this checkout's wrappers, hook, and isolation fixture. |
| `tests/test_labels.py` | Keep all | Unit contract for surviving `orchestrator/labels.py`. |
| `tests/test_llmlint_oneharness_wrapper.py` | Keep all | Proves this checkout's llmlint harness routing. |
| `tests/test_nx_cache_scope.py` | Keep all | Unit drift gates for this checkout's Nx keys. |
| `tests/test_oneharness_usage_wrapper.py` | Keep all | Proves this checkout's usage/fallback wrapper. |
| `tests/test_onejudge_version.py` | Keep all | Drift gate over this checkout's adopted pin. |
| `tests/test_published_tools.py` | Keep all | Drift gate from this checkout's four pins to dependencies and installed entry points. |
| `tests/test_redaction.py` | Keep all | Unit contract for surviving `orchestrator/redaction.py`. |
| `tests/test_session_setup.py` | Keep all | Each case covers provisioning logic still implemented by `scripts/session-setup.sh`; size is not upstream ownership. |
| `tests/test_smoke_selector.py` | Keep | Proves the selector in this checkout's `scripts/smoke.sh`. |
| `tests/test_stream_filter.py` | Keep all | Proves the stream/single-report adaptation in this checkout's harness wrapper. |
| `tests/e2e/test_dag_ui_serving_e2e.py` | Keep all | Drives this checkout's server/proxy recipes around the published UI bundle. UI behavior itself is not asserted. |
| `tests/e2e/test_delegated_recipes_e2e.py` | Convert | Keeps every existing journey, adds the previously absent real `repo-policy` delegation and `lint-llm-validate` validator journey. |
| `tests/e2e/test_gate_selection_e2e.py` | Keep all | Drives this checkout's comparison-base script and pre-push hook. |
| `tests/e2e/test_llmlint_cache_e2e.py` | Keep all | Required real Nx cached-verdict journey. |
| `tests/e2e/test_llmlint_two_path_verdict_e2e.py` | Keep all | Required real llmlint two-judge-path journey. |
| `tests/e2e/test_nx_cache_scope_e2e.py` | Keep all | Required real-Nx, linked-worktree cache-key journey. |
| `tests/e2e/test_oneharness_timeout_e2e.py` | Keep | Drives the real wrapper and real oneharness CLI timeout boundary. |
| `tests/e2e/test_orchestrate_launch_e2e.py` | Convert; details below | Retains configuration/adoption proof and removes three `onepipeline` semantic re-proofs. |
| `tests/e2e/test_quota_fallthrough_e2e.py` | Keep all | Required real oneharness fallback-chain journey. |
| `tests/e2e/test_repo_registry_apply_e2e.py` | Keep all | Drives this checkout's tracked checkout/rules installer; it does not re-prove onevcs lifecycle behavior. |
| `tests/e2e/test_session_setup_e2e.py` | Keep all | Required real PyPI installation journey for adopted releases. |
| `tests/e2e/test_workspace_contract_e2e.py` | Convert | Keeps every existing test and adds the missing non-recursive proof of the `test-e2e` entry point; all other cases still drive this checkout's quality, bootstrap, log, screenshot, and workspace wrappers. |

Helper modules (`conftest.py`, `published_tools.py`, `nx_inputs.py`, and the e2e
helpers) contain no tests and remain because the kept journeys import them.

In `test_delegated_recipes_e2e.py`, every pre-existing test is kept unchanged.
The `repo-policy` parameterized case formerly had no assertion at all and now
drives `just repo-policy /checkout` to `onevcs rules check /checkout`; the new
`test_lint_llm_validate_reaches_the_validator` drives the real recipe and proves
it selects validation rather than either judging path.
In `test_workspace_contract_e2e.py`, every pre-existing test is kept unchanged;
the new `test_test_e2e_recipe_names_the_real_tier_without_recursing_into_itself`
asserts the command that cannot safely be invoked from inside its own tier.

## Tests touched in the converted file

| Test | Disposition | Evidence |
| --- | --- | --- |
| `test_the_read_only_planner_views_answer_for_the_settled_run` | Delete | The asserted run-list/status/results/monitor/goals/host/telemetry semantics belong to onepipeline and are covered in `tests/e2e/views.rs` and `tests/e2e/journal.rs` there. Recipe forwarding remains in the delegation table. |
| `test_a_settled_run_carries_a_surface_but_refuses_an_unreadable_reply` | Delete | Surface queueing and reply-state refusal belong to onepipeline and are covered in `tests/e2e/surface.rs` and `tests/e2e/channel.rs` there. The JSON-envelope adaptation remains tested here. |
| `test_stop_refuses_a_run_another_planner_launched` | Delete | Launcher ownership enforcement belongs to onepipeline and is covered in `tests/e2e/driver.rs`; this checkout still proves that its launcher identity reaches a real launch. |
| All other tests in `test_orchestrate_launch_e2e.py` | Keep | They prove shipped plan/schema acceptance, graph files, persona/base routing, paid-provider isolation, launcher derivation, missing-graph diagnostics, and the local merge-policy prose/config drift gates—the exact configuration failures this checkout can introduce. |

## Delegations that need more than spelling

The delegated-CLI double is sufficient for wrappers whose only local decision is
argument adaptation. The following need configuration or state beyond spelling:

| Delegation | Real evidence here, or why it cannot be driven here |
| --- | --- |
| `orchestrate` / `status` / `monitor` / `stop` | `test_orchestrate_launch_e2e.py` launches and settles a real shipped plan, polls its real status, replays its monitor stream, and stops it during cleanup. |
| `channel-approve`, `channel-reject`, `channel-continue`, `channel-reply` | The local decision is JSON-envelope construction, driven through the real verdict wrapper. A successful live reply would test onepipeline channel state, covered upstream in `tests/e2e/channel.rs`, and would race the real orchestrator. `channel-reply` has one local decision beyond spelling and it *is* driven live, in `test_ask_manager_e2e.py`: whether the run's pending blocking question can use the envelope being sent. That is a judgment about this host's own ask wrapper rather than about channel state, and the failure it prevents — an envelope reported `delivered` and discarded unread — is invisible from every answer the engine gives, so nothing upstream could cover it. |
| `channel-next` / `channel-surface` | Local stdin/message forwarding and argument validation are driven here. Queue/read state is onepipeline behavior covered upstream in `tests/e2e/surface.rs`; driving it live would duplicate that engine journey. The one exception is the **hand-out order** — that a blocking surface jumps the queue and that reading past it leaves it pending — which `test_orchestrate_launch_e2e.py` drives live because `AGENTS.md` tells a manager to rely on it, and a manager relying on the previous order would bury a worker's question. |
| `channel-next --filter` / `monitor --filter` / `--all` | The recipes pass these through to `onepipeline`'s own read profiles; that the `planner` default holds and both flags reach past it is driven live in `test_orchestrate_launch_e2e.py`. The profile *specs* are onepipeline's own and are covered by that crate's tests. |
| `runs` / `results` / `goals` / `host` / `telemetry` | These are read-only onepipeline products. This layer only forwards arguments; schemas and state projections are covered upstream in `tests/e2e/views.rs`. |
| `history` / `history-show` | This layer only forwards to oneagentgraph; record/history behavior is covered upstream in `tests/record.rs` and `tests/e2e/verbs.rs`. |
| `smoke` | Driven through the real wrapper and real harness boundary with an isolated history store; agentgraph smoke semantics are covered upstream in `tests/e2e/dispatch.rs`. |
| `sweep` | Driven end to end in `test_sweep_e2e.py`, not only forwarded: it is a composition of two verbs, and the trailer naming what neither examined is this layer's own. Real sweeps, including one that removes for real beside a directory a live process holds — `ONEAGENTGRAPH_STATE_DIR`, `TMPDIR`, `ONEVCS_HOME`, and `AI_ORCHESTRATOR_HOME` put every family inside `tmp_path`, which is isolation rather than substitution. Each verb's own scratch semantics stay covered upstream in oneagentgraph `tests/e2e/verbs.rs`. |
| `validate-personas` | Real persona files are validated by the deterministic gate. The delegated row proves the recipe route; validator semantics are covered by oneagentgraph `tests/contract.rs`. |
| `register-repo`, `repo-recover`, `integrate`, `sync` | A real invocation against *this host's* registry mutates registered repositories, branches, or remotes and is unsafe in this checkout's test run. Local argument forwarding is driven; onevcs covers the operations in `crates/onevcs/tests/e2e/registry.rs`, `lifecycle.rs`, and `cli.rs`. |
| `publish-branch` | Driven end to end in `test_publish_branch_e2e.py`, not only forwarded: it publishes a real branch onto a real base, is refused by the repository's own real `pre-push` hook, and lands past a `gate:` an unmigrated rules file still names — which is how the removal is proven rather than asserted. The row above is about this host's registry, not about the verb — `ONEVCS_HOME` pointed at a scratch registry, over a throwaway origin, reaches nothing of this host's, which is the same isolation `test_repo_registry_apply_e2e.py` uses. This one earns that cost because it is the verb that closes the raw-`git` gap, so "it forwards its arguments" is not the claim worth making about it. |
| `repos`, `recoverable`, `repo-policy`, `repos-apply` | The first two forwarding shapes are driven with the published CLI doubled; real registry/rules installation and policy resolution are driven safely in `test_repo_registry_apply_e2e.py`. |
| `telemetry-server` | Its host/port/address-file adaptations and failures are driven through the real wrapper; the long-running read API is the doubled published boundary. |
| `dag-ui` / `dag-ui-screens` | Real recipes serve the installed bundle, proxy a real HTTP boundary, and drive screenshot argument/error journeys in the UI-serving and workspace modules. |
| `new-persona` | Real scaffolding, invalid input, and destination behavior are driven in the delegated-recipes module. |
| `replan` | Intentionally has no engine verb: its real recipe is driven and proves the migration diagnostic. |

## `just --list` reconciliation

Every listed recipe is covered by one of four real e2e tables/journeys:

- delegated operator verbs are enumerated in `DELEGATIONS` or have a dedicated
  wrapper journey in `test_delegated_recipes_e2e.py`;
- quality/workspace verbs (`bootstrap`, `check`, `test`, `gate`, `upgrade`, format,
  lint, typecheck, llmlint, and `dag-ui-screens`) are driven in
  `test_workspace_contract_e2e.py` and the two llmlint modules;
- `dag-ui` is driven in `test_dag_ui_serving_e2e.py`;
- `repos-apply` and `repo-policy` are driven in
  `test_repo_registry_apply_e2e.py`.

`default` is `just --list` itself. `setup-llmlint` and `session-setup` are exercised
through their real setup journeys. `test-e2e` is the sole recipe that cannot invoke
itself from its own e2e suite without infinite recursion; its exact pytest command
is held by the workspace recipe-routing journey, and this entire tier is its
product. This reconciliation was repeated after the edit by comparing the output
of `just --list` to those recipe inventories.
