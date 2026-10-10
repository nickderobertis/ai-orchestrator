"""Module table per tier from a run's pytest-tests.json(.gz): seconds, tests, max test, groups."""
import collections, gzip, json, sys
from pathlib import Path
run = Path(sys.argv[1]); tiers = sys.argv[2:]
f = run / "pytest-tests.json"
data = json.loads(f.read_text()) if f.exists() else json.loads(gzip.open(run / "pytest-tests.json.gz").read())
for tier in tiers:
    mods = collections.defaultdict(lambda: [0.0, 0, 0.0, collections.Counter()])
    for r in data[tier]:
        m = mods[r["nodeid"].split("::")[0]]; m[0] += r["duration"]; m[1] += 1; m[2] = max(m[2], r["duration"]); m[3][str(r["group"])] += 1
    print(f"## {tier}: {len(mods)} modules, {sum(m[0] for m in mods.values()):.0f} s")
    for name, (s, n, mx, g) in sorted(mods.items(), key=lambda kv: -kv[1][0]):
        print(f"{name} | {s:.1f} | {n} | {mx:.1f} | {','.join(k for k in g if k != 'None') or '-'}")
