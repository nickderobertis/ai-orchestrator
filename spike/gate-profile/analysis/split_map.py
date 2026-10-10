"""Spike-only: sum a run's per-test seconds into the proposed journey groups (item 6)."""
import collections, gzip, json, re, sys
from pathlib import Path
run = Path(sys.argv[1])
data = json.loads(gzip.open(run / "pytest-tests.json.gz").read())
TIERS = ["orchestrator-e2e:test", "orchestrator-e2e:test-recipes", "orchestrator-e2e:test-checkouts",
         "plan-tooling:test", "plan-tooling:test-docs", "orchestrator:test-docs"]
# first matching rule wins; patterns are module-path regexes
GROUPS = [
 ("e2e/toolchain-writers", r"tests/e2e/test_(workspace_contract|nx_wrapper_version|shared_workspace_install|nx_cache_scope|llmlint_cache|llmlint_two_path_verdict|gate_selection)_e2e|tests/test_project_boundaries"),
 ("e2e/budget-gate", r"tests/e2e/test_(budget_target|budget_telemetry|gate_time_budget|budget_capabilities_adopted|lock_timeout_bound)_e2e"),
 ("e2e/publish-and-landing", r"tests/e2e/test_(publish_branch|work_status_and_import|integrate_local_direct|merge_path_audit|redispatch_names_the_published_tip|recoverable_resume_commands|base_freshness|release_adoption_in_force|session_reuse_retry|human_readable_branch|worktree_pool|session_labels|sweep|onevcs_state_snapshot|legacy_runs_root)_e2e"),
 ("e2e/plan-store-host", r"tests/e2e/test_onetaskgraph_host_e2e"),
 ("e2e/registry-and-recipes", r"tests/e2e/test_(repo_registry_apply|repos_bootstrap|delegated_recipes|ask_manager_shim|setup_llmlint|just_tempdir|commit_msg_hook|credential_file_ignored|transcript_recipe|watch_recipe|watch_selector)_e2e"),
 ("e2e/launch-monitor-observer", r"tests/e2e/test_(orchestrate_launch|monitor_\w+|observer_\w+|live_note\w*|supervisory_\w+|extends_chain_dispatch|parked_run_is_driven|adopted_\w+|launch_walks_no_host_identity|reparent_waiting_human_adopted|concurrency_interlock|worker_start_directory|repositories_field_dispatch|dispatch_environment|dispatched_operational_notes|driver_death_is_recoverable|graph_overrides_dispatch|plan_recipe|design_doc_graph|persona_review_bar|path_dispatched_personas|shipped_persona_catalog|lost_turn_wire_contract|controlled_turn_model|judge_panel|template_layers)_e2e"),
 ("e2e/harness-routing", r"tests/e2e/test_(oneharness_\w+|quota_fallthrough|fake_codex_\w+|claude_identity_routing|onemessagebus_cli)_e2e"),
 ("e2e/other", r"tests/e2e/"),
 ("plan-tooling/check-and-review", r"tests/plan_tooling/test_(check_plan_\w+|plan_review|shipped_examples_check_plan|plan_write_refusals|task_templates|plan_task_template|plan_store_record_shapes|design_document_body_limit)"),
 ("plan-tooling/follow-ups", r"tests/plan_tooling/test_(follow_ups\w*|follow_up_drafts|linear_routed_follow_ups)"),
 ("plan-tooling/plan-flow", r"tests/plan_tooling/test_(plan_flow|plan_spike_flow|visual_plan_flow|unwatched_planning_launches|plan_root_env|linear_routed_plans)"),
 ("plan-tooling/finish-approve-copy", r"tests/plan_tooling/test_(finish_plan\w*|approve_design\w*|copy_plan\w*|plan_copy_destination_ids)"),
 ("plan-tooling/other", r"tests/plan_tooling/"),
 ("root drift gates (tests/*.py)", r"tests/[^/]+\.py$"),
]
out = collections.defaultdict(lambda: {"s": 0.0, "n": 0, "tiers": collections.Counter(), "mods": collections.Counter()})
for tier in TIERS:
    for r in data.get(tier, []):
        mod = r["nodeid"].split("::")[0]
        name = next(g for g, pat in GROUPS if re.search(pat, mod))
        o = out[name]; o["s"] += r["duration"]; o["n"] += 1; o["tiers"][tier] += r["duration"]; o["mods"][mod] += r["duration"]
for name, _ in GROUPS:
    if name not in out: continue
    o = out[name]
    print(f"### {name}: {o['s']:.0f} s summed, {o['n']} tests")
    print("  by tier: " + ", ".join(f"{t} {s:.0f}" for t, s in o["tiers"].most_common()))
    print("  heaviest: " + ", ".join(f"{m.split('/')[-1]} {s:.0f}" for m, s in o["mods"].most_common(4)))
