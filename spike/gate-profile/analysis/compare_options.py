"""Spike-only (item 5): the two protections' cost from measured runs, with the arithmetic."""
import json
proj = json.load(open("results/analysis/projection-wide-after.json"))
alone = json.load(open("results/runs/writers-only-target-alone/summary.json"))
iso_run = json.load(open("results/runs/writers-isolated-v2/summary.json"))
iso = json.load(open("results/runs/writers-isolated-v2/isolation-summary.json"))
target = [t for r in alone["nx_runs"] for t in r["tasks"] if t["task"] == "orchestrator:test-toolchain-writers"][0]
writer_s = round(sum(t["summed_test_s"] for t in iso_run["pytest_tiers"].values()), 1)
work = proj["phase1_total_work_s"]
readers_lb = round((work - writer_s) / 3, 1)
tail = proj["phase2_uncached_s (narrow-script run)"] + proj["check_nx_cache_s (wide-cold run)"] + proj["llmlint_phases_replayed_s (wide-cold run: validate+lint-llm-diff+handoff)"]
print(json.dumps({
 "option_i_writer_only_target": {
   "measured_alone_s": target["duration_s"], "exit_status": target["status"],
   "tests": alone["pytest_tiers"]["orchestrator:test-toolchain-writers"]["tests"],
   "group_serialised_s": alone["pytest_tiers"]["orchestrator:test-toolchain-writers"]["group_s"].get("shared-checkout-toolchain"),
   "load1_max": alone["host"]["load1_max"],
   "wide_gate_lower_bound_s": round(target["duration_s"] + readers_lb + tail, 1),
   "arithmetic": f"{target['duration_s']} (writer target, parallelism:false, nothing beside it) + ({work} phase-1 work after - {writer_s} writer-module test s) / 3 slots = {readers_lb} + {round(tail,1)} (phase 2 + check-nx-cache + replayed llmlint)"},
 "option_ii_isolated_writer_toolchains": {
   "isolation_added_total_s": iso["isolation_added_total_s"],
   "writer_modules_wall_ungrouped_s": iso["wall_clock_s"], "writer_modules_summed_test_s": writer_s,
   "wide_gate_projected_s": proj["complete_gate_wide_projected_s"]},
}, indent=1))
