# three-prototype failure triage (taken at 3b6a1ef7 + prototype patch, i.e. the tree of branch commit 67db1222)

Read from logs/nx.3669445.log (orchestrator-e2e:test) and logs/nx.3669447.log (orchestrator:test-docs).

| failure | cause | prototype-caused? |
|---|---|---|
| test_observer_graph_liveness_e2e::test_a_paced_monitor_keeps_the_run_watched_between_its_turns | a `member-died` envelope 2 s after the run settled (03:49:41 vs settle 03:49:39), load1 peak 18.44 | no: re-run alone twice on the prototype tree (`.venv/bin/python -m pytest <nodeid>`), passed both times — a shutdown race under load |
| test_workspace_contract_e2e::test_a_worktree_provisioned_before_the_pin_moved_reinstalls_from_the_lockfile | its copy runs `just format-check`; ruff would reformat the 5 prototype-edited test files | yes, artefact: the prototype is unformatted spike code (no lint by design) |
| test_nx_cache_scope (13 tests: `lands_on_one_worker`, `collected_into_one_xdist_group`, `each_unit_depends_on_exactly_...`, 9 `every_tier_is_keyed_...`, `each_ask_seam_journey_is_keyed_...`) | drift gates encoding the group rule and the Nx dependency graph; the prototype strips the group and makes tests/conftest.py import tests/e2e/nx_workspace.py without adding the `support-nx-workspace` edge to each project.json | yes, expected: the real fix rewrites these two rules and adds the edge. Two of them (`lands_on_one_worker`, `collected_into_one_xdist_group`) also failed in gate-wide-conftest-cold, as harness artefacts of the plugin in nested collection probes |

No failure or timeout is a reader observing a toolchain mid-rewrite.
