"""Summarise an strace -f -ttt -e trace=openat,execve capture (spike-only)."""
import argparse, collections, json, re
p = argparse.ArgumentParser()
p.add_argument("strace"); p.add_argument("tracked"); p.add_argument("root")
p.add_argument("--label"); p.add_argument("--rc", type=int); p.add_argument("--wall", type=float); p.add_argument("--nodeids", nargs="*")
a = p.parse_args()
tracked = set(open(a.tracked).read().split("\n")) - {""}
open_re = re.compile(r'^(\d+)\s+([\d.]+)\s+openat\(AT_FDCWD, "([^"]+)", ([^)]*)\) = (\d+)')
exec_re = re.compile(r'^(\d+)\s+([\d.]+)\s+execve\("([^"]+)", \[(.*?)\]')
exit_re = re.compile(r'^(\d+)\s+([\d.]+)\s+\+\+\+ (exited|killed)')
root = a.root.rstrip("/") + "/"
read_root, read_copy = collections.Counter(), collections.Counter()
procs = {}
def rel(path):
    if path.startswith(root):
        r = path[len(root):]
        return ("root", r) if r in tracked else (None, None)
    parts = path.split("/")
    for i in range(1, len(parts)):
        r = "/".join(parts[i:])
        if r in tracked and "/" in r or (r in tracked and i == len(parts) - 1 and r in {"justfile", "nx.json", "package.json", "pyproject.toml", "uv.lock", "bun.lock", "AGENTS.md", "llmlint.yml", "onetaskgraph.yaml", "budgets.yaml"}):
            return ("copy", r)
    return (None, None)
for line in open(a.strace, errors="replace"):
    m = open_re.match(line)
    if m:
        if "O_DIRECTORY" in m.group(4):
            continue
        kind, r = rel(m.group(3))
        if kind == "root": read_root[r] += 1
        elif kind == "copy": read_copy[r] += 1
        continue
    m = exec_re.match(line)
    if m and " = -1 " not in line:
        argv = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(4))
        procs[m.group(1)] = {"start": float(m.group(2)), "argv": " ".join(argv)[:200]}
        continue
    m = exit_re.match(line)
    if m and m.group(1) in procs:
        procs[m.group(1)]["end"] = float(m.group(2))
durs = collections.defaultdict(lambda: [0, 0.0])
for pr in procs.values():
    if "end" in pr:
        key = " ".join(pr["argv"].split()[:3])
        durs[key][0] += 1; durs[key][1] += pr["end"] - pr["start"]
top = sorted(((k, n, round(s, 1)) for k, (n, s) in durs.items()), key=lambda t: -t[2])[:25]
def tops(c):
    groups = collections.Counter()
    for r in c: groups[r.split("/")[0] if "/" in r else r] += 1
    return dict(groups.most_common())
json.dump({"label": a.label, "nodeids": a.nodeids, "exit_status": a.rc, "wall_clock_s": a.wall,
           "tracked_read_in_root": sorted(read_root), "tracked_read_in_copies": sorted(read_copy),
           "by_top_dir_root": tops(read_root), "by_top_dir_copies": tops(read_copy),
           "process_count": len(procs),
           "slowest_process_kinds_inclusive_s": [{"argv_head": k, "count": n, "summed_s": s} for k, n, s in top]},
          __import__("sys").stdout, indent=1)
