"""Streamed strace filter (spike-only): keep the process tree, each process's cwd, and the
first time each process opened each tracked file (in this checkout or any copy of it)."""
import json, os, re, sys
out, root = sys.argv[1], sys.argv[2].rstrip("/") + "/"
tracked = set(open(os.path.join(out, "tracked.txt")).read().split("\n")) - {""}
TOP = {t for t in tracked if "/" not in t}
parent, cwd, reads, pending = {}, {}, {}, {}
first = None
line_re = re.compile(r"^(\d+) ([\d.]+) (.*)$")
open_re = re.compile(r'openat\(AT_FDCWD, "((?:[^"\\]|\\.)*)"')
child_re = re.compile(r"= (\d+)$")
chdir_re = re.compile(r'chdir\("((?:[^"\\]|\\.)*)"\) = 0')
def rel(path):
    if path.startswith(root):
        r = path[len(root):]
        return ("root", r) if r in tracked else None
    parts = path.split("/")
    for i in range(1, len(parts) - 0):
        r = "/".join(parts[i:])
        if r in tracked and ("/" in r or r in TOP):
            return ("copy", r)
    return None
def record(pid, t, path):
    if not path.startswith("/"):
        path = os.path.join(cwd.get(pid, root), path)
    path = os.path.normpath(path)
    hit = rel(path)
    if hit:
        reads.setdefault(pid, {}).setdefault(hit[0] + ":" + hit[1], t)
for raw in sys.stdin:
    m = line_re.match(raw.rstrip("\n"))
    if not m:
        continue
    pid, t, rest = int(m.group(1)), float(m.group(2)), m.group(3)
    if first is None:
        first = pid; cwd[pid] = root.rstrip("/")
    if rest.startswith("openat("):
        o = open_re.match(rest)
        if not o:
            continue
        if rest.endswith("<unfinished ...>"):
            pending[pid] = o.group(1)
        else:
            record(pid, t, o.group(1))
    elif rest.startswith("<... openat resumed>"):
        p = pending.pop(pid, None)
        if p is not None:
            record(pid, t, p)
    elif rest.startswith(("clone", "fork", "vfork", "<... clone", "<... fork", "<... vfork")):
        c = child_re.search(rest)
        if c:
            child = int(c.group(1)); parent[child] = pid; cwd.setdefault(child, cwd.get(pid, root.rstrip("/")))
    elif rest.startswith("chdir("):
        c = chdir_re.match(rest)
        if c:
            d = c.group(1)
            cwd[pid] = os.path.normpath(d if d.startswith("/") else os.path.join(cwd.get(pid, root), d))
with open(os.path.join(out, "reads.json"), "w") as fh:
    json.dump({"root_pid": first, "parent": parent, "reads": reads}, fh)
