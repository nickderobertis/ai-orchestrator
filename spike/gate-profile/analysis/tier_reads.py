"""Spike-only (item 6): attribute a traced tier run's tracked-file reads to the test (and so the
module and split-map group) each xdist worker was running, and to each tier as a whole.

  tier_reads.py RUN_DIR  -> RUN_DIR/tier-reads-summary.json
Reads RUN_DIR/tier-reads/reads.json (trace/filter.py) and RUN_DIR/pytest/*.jsonl (the plugin)."""
import bisect, collections, glob, importlib.util, json, os, re, sys
run = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("sm", os.path.join(here, "split_map_groups.py"))
sm = importlib.util.module_from_spec(spec); spec.loader.exec_module(sm)
EDITS = ["scripts/budget-dispatches.sh", "personas/researcher.yaml", "docs/budgets.md", "tests/conftest.py", "justfile"]
data = json.load(open(os.path.join(run, "tier-reads", "reads.json")))
parent = {int(k): v for k, v in data["parent"].items()}
workers, controllers = {}, {}
for f in glob.glob(os.path.join(run, "pytest", "*.jsonl")):
    for line in open(f):
        r = json.loads(line)
        if r["type"] == "configure":
            (workers if r["role"] in ("worker", "solo") else controllers)[r["pid"]] = r["tier"]
# phase records carry the worker id, not its pid; key intervals by the file's pid instead
intervals = collections.defaultdict(list)
for f in glob.glob(os.path.join(run, "pytest", "*.jsonl")):
    pid = int(re.search(r"\.(\d+)\.jsonl$", f).group(1))
    for line in open(f):
        r = json.loads(line)
        if r["type"] == "phase":
            intervals[pid].append((r["start"], r["stop"], r["nodeid"]))
for v in intervals.values(): v.sort()
def owner(pid):
    seen = 0
    while pid is not None and seen < 200:
        if pid in workers: return ("worker", pid)
        if pid in controllers: return ("controller", pid)
        pid = parent.get(pid); seen += 1
    return (None, None)
def test_at(wpid, t):
    iv = intervals.get(wpid, [])
    i = bisect.bisect_right(iv, (t, float("inf"), "")) - 1
    while i >= 0:
        s, e, n = iv[i]
        if s <= t <= e + 0.5: return n
        if e < t - 5: break
        i -= 1
    return None
tier_reads = collections.defaultdict(set); group_reads = collections.defaultdict(set); module_reads = collections.defaultdict(set)
unattributed = set(); harness = set()
for pid_s, files in data["reads"].items():
    kind, owner_pid = owner(int(pid_s))
    for key, t in files.items():
        path = key.split(":", 1)[1]
        if kind is None:
            harness.add(path); continue
        tier = (workers if kind == "worker" else controllers)[owner_pid]
        tier_reads[tier].add(path)
        if kind == "worker":
            n = test_at(owner_pid, t)
            if n:
                mod = n.split("::")[0]; module_reads[mod].add(path)
                group_reads[sm.group_of(mod)].add(path)
            else:
                unattributed.add(path)
core_reads = collections.defaultdict(set)
for mod, paths in module_reads.items():
    if len(paths) < 700:
        core_reads[sm.group_of(mod)] |= paths
def tops(paths):
    c = collections.Counter(p.split("/")[0] if "/" in p else p for p in paths); return dict(c.most_common())
out = {
 "tiers": {t: {"tracked_files_read": len(p), "by_top_dir": tops(p), "representative_edits_read": [e for e in EDITS if e in p]} for t, p in sorted(tier_reads.items())},
 "groups": {g: {"tracked_files_read": len(p), "by_top_dir": tops(p), "representative_edits_read": [e for e in EDITS if e in p], "files": sorted(p)} for g, p in sorted(group_reads.items())},
 "modules": {m: {"tracked_files_read": len(p), "representative_edits_read": [e for e in EDITS if e in p]} for m, p in sorted(module_reads.items())},
 "whole_checkout_copy_modules (read >=700 of the tracked files)": sorted(m for m, p in module_reads.items() if len(p) >= 700),
 "groups_without_whole_copy_modules": {g: {"tracked_files_read": len(p), "by_top_dir": tops(p), "representative_edits_read": [e for e in EDITS if e in p], "files": sorted(p)}
     for g, p in sorted(core_reads.items())},
 "outside_any_pytest (nx, nx.sh heal, uv run)": {"tracked_files_read": len(harness), "by_top_dir": tops(harness)},
 "worker_reads_outside_a_test_phase": len(unattributed),
 "note": "Python imports usually read __pycache__ rather than the source, so an imported tracked module appears only where its bytecode was stale; tests/nx_inputs.py declares imports separately.",
}
json.dump(out, open(os.path.join(run, "tier-reads-summary.json"), "w"), indent=1)
print(json.dumps({k: v for k, v in out.items() if k in ("tiers",)}, indent=1)[:3000])
print({g: (v["tracked_files_read"], v["representative_edits_read"]) for g, v in out["groups"].items()})
print("without whole-copy modules:", {g: (v["tracked_files_read"], v["representative_edits_read"]) for g, v in out["groups_without_whole_copy_modules"].items()})
