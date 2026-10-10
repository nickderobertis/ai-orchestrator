"""Per-tier and per-module profile of one measured run's pytest-tests.json(.gz)."""
import collections, gzip, json, sys
from pathlib import Path
run = Path(sys.argv[1])
f = run / "pytest-tests.json"
data = json.loads(f.read_text()) if f.exists() else json.loads(gzip.open(run / "pytest-tests.json.gz").read())
summ = json.loads((run / "summary.json").read_text())
mods = collections.defaultdict(lambda: {"s": 0.0, "n": 0, "tiers": set(), "groups": collections.Counter(), "max": 0.0})
print("tier | wall s | tests | summed s | serialised group s (top) ")
for tier, rows in sorted(data.items(), key=lambda kv: -sum(r["duration"] for r in kv[1])):
    t = summ["pytest_tiers"][tier]
    print(f"{tier} | {t['wall_clock_s']} | {t['tests']} | {t['summed_test_s']} | " + ", ".join(f"{g}={v}" for g, v in list(t["group_s"].items())[:3]))
    for r in rows:
        m = r["nodeid"].split("::")[0]
        d = mods[m]; d["s"] += r["duration"]; d["n"] += 1; d["tiers"].add(tier); d["groups"][str(r["group"])] += 1; d["max"] = max(d["max"], r["duration"])
print("\n30 slowest modules: module | summed s | tests | max single test s | tier | groups")
for m, d in sorted(mods.items(), key=lambda kv: -kv[1]["s"])[:30]:
    print(f"{m} | {d['s']:.1f} | {d['n']} | {d['max']:.1f} | {','.join(sorted(d['tiers']))} | {dict(d['groups'])}")
tot = sum(d["s"] for d in mods.values()); n = sum(d["n"] for d in mods.values())
print(f"\nall tiers: {n} tests, {tot:.1f} s summed")
