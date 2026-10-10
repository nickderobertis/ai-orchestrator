"""Spike-only: per-writer isolation setup overhead from a run's isolation.jsonl, beside each
writer module's test seconds in that run and in the before run (wide-conftest, grouped)."""
import collections, gzip, json, sys
from pathlib import Path
run, before = Path(sys.argv[1]), Path(sys.argv[2])
def tests(r):
    f = r / "pytest-tests.json"
    d = json.loads(f.read_text()) if f.exists() else json.loads(gzip.open(r / "pytest-tests.json.gz").read())
    per = collections.defaultdict(lambda: [0.0, 0])
    for rows in d.values():
        for x in rows:
            m = x["nodeid"].split("::")[0]; per[m][0] += x["duration"]; per[m][1] += 1
    return per
after_t, before_t = tests(run), tests(before)
iso = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0]))
for line in (run / "isolation.jsonl").read_text().splitlines():
    e = json.loads(line)
    mod = e["test"].split("::")[0] or "(no test)"
    iso[mod][e["kind"]][0] += e["seconds"]; iso[mod][e["kind"]][1] += 1
# What isolation adds over the before design, per kind: a copy that already existed before
# (copy_checkout journeys) adds only the private node_modules; a writer that ran at the root
# before (nx_wrapper_version, project_boundaries, session_setup) adds its whole copy.
RAN_AT_ROOT_BEFORE = ("test_nx_wrapper_version_e2e.py", "test_project_boundaries.py", "test_locked_plan_store_setup_e2e.py")
out = {}
for mod in sorted(set(iso) | {m for m in after_t if any(k in m for k in ("nx_cache_scope_e2e", "llmlint_", "budget_target", "budget_telemetry", "workspace_contract", "nx_wrapper", "project_boundaries", "locked_plan_store", "dag_ui_serving"))}):
    kinds = {k: {"seconds": round(v[0], 2), "count": v[1]} for k, v in iso.get(mod, {}).items()}
    added_kinds = ("node_modules_cp_al", "copy_working_tree", "git_init", "venv_sync") if mod.endswith(RAN_AT_ROOT_BEFORE) else ("node_modules_cp_al",)
    added = sum(v["seconds"] for k, v in kinds.items() if k in added_kinds)
    out[mod] = {"isolation_steps": kinds, "isolation_added_s": round(added, 2), "added_kinds": list(added_kinds),
                "tests_after": after_t.get(mod, [0, 0])[1], "test_s_after": round(after_t.get(mod, [0, 0])[0], 1),
                "test_s_before_grouped": round(before_t.get(mod, [0, 0])[0], 1)}
total = round(sum(v["isolation_added_s"] for v in out.values()), 2)
json.dump({"per_writer_module": out, "isolation_added_total_s": total,
           "wall_clock_s": json.loads((run / "summary.json").read_text())["wall_clock_s"]}, sys.stdout, indent=1)
