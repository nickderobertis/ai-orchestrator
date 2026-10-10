"""Spike-only: sum a run's per-test seconds into the proposed journey groups (item 6)."""
import collections, gzip, json, re, sys
from pathlib import Path
run = Path(sys.argv[1])
data = json.loads(gzip.open(run / "pytest-tests.json.gz").read())
TIERS = ["orchestrator-e2e:test", "orchestrator-e2e:test-recipes", "orchestrator-e2e:test-checkouts",
         "plan-tooling:test", "plan-tooling:test-docs", "orchestrator:test-docs"]
import importlib.util, os
_spec = importlib.util.spec_from_file_location("split_map_groups", os.path.join(os.path.dirname(os.path.abspath(__file__)), "split_map_groups.py"))
_sm = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_sm)
GROUPS = _sm.GROUPS
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
