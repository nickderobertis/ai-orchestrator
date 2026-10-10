"""Spike-only: project the wide-diff complete gate after the fix (item 5), from recorded runs."""
import heapq, json
wide = json.load(open("results/runs/gate-wide-conftest-cold/summary.json"))
narrow = json.load(open("results/runs/gate-narrow-script-warm/summary.json"))
proto = json.load(open("results/runs/three-prototype/summary.json"))
THREE = {"orchestrator-e2e:test", "plan-tooling:test", "orchestrator:test-docs"}
after3 = {t["task"]: t["duration_s"] for r in proto["nx_runs"] for t in r["tasks"]}
phase1 = [r for r in wide["nx_runs"] if r["command"].startswith("nx affected")][0]["tasks"]
others = [(t["start_offset_s"], t["task"], t["duration_s"]) for t in phase1 if t["task"] not in THREE]
# three slots (Nx default parallel); the three big tiers start first, as they did in both runs,
# then the other phase-1 tasks in the order the wide run started them, each on the first free slot.
slots = [after3[k] for k in ("orchestrator-e2e:test", "plan-tooling:test", "orchestrator:test-docs")]
heapq.heapify(slots)
for _, task, d in sorted(others):
    heapq.heappush(slots, heapq.heappop(slots) + d)
p1 = max(slots)
work = sum(after3[k] for k in THREE) + sum(d for _, _, d in others)
p2 = [r for r in narrow["nx_runs"] if r["command"].startswith("nx run-many")][0]["duration_s"]
cnc = [r for r in wide["nx_runs"] if "check-nx-cache" in r["command"]][0]["duration_s"]
g = wide["gate_phases"]; lint = g["llmlint_validate_s"] + g["lint_llm_diff_s"] + g["handoff_s"]
det = p1 + p2 + cnc
out = {
 "after_three_tiers_s": {k: after3[k] for k in THREE},
 "other_phase1_tasks_sum_s (wide-cold, before)": round(sum(d for _, _, d in others), 1),
 "phase1_total_work_s": round(work, 1), "phase1_lower_bound_work_over_3_s": round(work / 3, 1),
 "phase1_list_scheduled_s": round(p1, 1),
 "phase2_uncached_s (narrow-script run)": p2, "check_nx_cache_s (wide-cold run)": cnc,
 "deterministic_tier_projected_s": round(det, 1),
 "llmlint_phases_replayed_s (wide-cold run: validate+lint-llm-diff+handoff)": round(lint, 1),
 "complete_gate_wide_projected_s": round(det + lint, 1),
 "complete_gate_wide_before_s": wide["wall_clock_s"],
}
print(json.dumps(out, indent=1))
