"""Spike-only runner/sampler/summariser behind measure.sh.

``collect.py run --out DIR --root ROOT --nx-cache DIR --mode M -- CMD...`` starts CMD in
ROOT, and while it runs:

* polls the process table once a second for the gate's own phases — the ``just
  lint-llm-validate`` / ``just lint-llm-diff`` / ``just check`` children and the
  top-level ``scripts/nx.sh`` invocations ``just check`` makes (anything below a pytest
  is a journey's own nested run and is ignored);
* samples load1 from /proc/loadavg every 5 s and running dispatches from
  ``scripts/budget-dispatches.sh`` every 60 s, as the gate-time budget records them;
* snapshots every version of Nx's ``run.json`` in the isolated cache directory, which
  Nx rewrites at the end of each invocation with every task's cache status and timing.

It then writes ``summary.json`` beside the raw material (``stdout.log``, ``stderr.log``,
``samples.jsonl``, ``procs.jsonl``, ``nx-runs/``, ``pytest/`` and the gate's ``.logs``).
``collect.py summarise DIR`` rebuilds ``summary.json`` from that raw material alone.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

PHASE_RECIPES = ("lint-llm-validate", "lint-llm-diff", "check")


def read_procs() -> dict[int, tuple[int, list[str]]]:
    procs = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as fh:
                stat = fh.read().decode(errors="replace")
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                argv = [a.decode(errors="replace") for a in fh.read().split(b"\0") if a]
        except (OSError, IndexError, ValueError):
            continue
        procs[int(entry)] = (ppid, argv)
    return procs


def classify(pid: int, procs: dict, root_pid: int) -> tuple[str, str] | None:
    """Return (kind, label) for a gate-level process, or None."""
    ppid, argv = procs[pid]
    chain = []
    cur = ppid
    while cur and cur != root_pid and cur in procs:
        chain.append(procs[cur][1])
        cur = procs[cur][0]
    if cur != root_pid and pid != root_pid:
        return None  # not a descendant
    if any(any("pytest" in a for a in anc) for anc in chain):
        return None
    if not argv:
        return None
    base = os.path.basename(argv[0])
    if base == "just" and len(argv) > 1 and argv[1] in PHASE_RECIPES:
        return ("just", argv[1])
    if base == "bash" and any(a.endswith("scripts/nx.sh") for a in argv[:3]):
        idx = next(i for i, a in enumerate(argv) if a.endswith("scripts/nx.sh"))
        under_check = any(
            os.path.basename(anc[0]) == "just" and len(anc) > 1 and anc[1] == "check" for anc in chain if anc
        )
        under_lint = any(
            os.path.basename(anc[0]) == "just" and len(anc) > 1 and anc[1] == "lint-llm-diff" for anc in chain if anc
        )
        where = "check" if under_check else ("lint-llm-diff" if under_lint else "other")
        return ("nx", where + " " + " ".join(argv[idx + 1 :]))
    return None


DISPATCH_ROW = re.compile(r"^  [^ ]+ +[^ ]+ +[^ ]+ +[0-9]+[hms][0-9hms]*( |$)")


def dispatches(root: Path) -> dict:
    """`just host` read as scripts/budget-dispatches.sh reads it, plus the proven rows.

    The budget script answers `unknown` whenever any row is UNPROVEN; this host carries
    stale UNPROVEN rows from long-dead runs, so the proven live rows are recorded beside it.
    """
    try:
        out = subprocess.run(
            ["just", "host"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=180,
            env={**{k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "GATE_PROFILE"))}, "UV_NO_SYNC": "1"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"budget_script_equivalent": "unknown", "proven": None, "unproven": None}
    rows = [line for line in out.stdout.splitlines() if DISPATCH_ROW.match(line)]
    unproven = sum("UNPROVEN:" in r for r in rows)
    proven = len(rows) - unproven
    return {
        "budget_script_equivalent": len(rows) if (out.returncode == 0 and unproven == 0) else "unknown",
        "proven": proven if out.returncode == 0 else None,
        "unproven": unproven if out.returncode == 0 else None,
    }


def run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    root = Path(args.root)
    nx_cache = Path(args.nx_cache)
    (out / "nx-runs").mkdir(exist_ok=True)
    meta = json.loads(Path(args.meta).read_text()) if args.meta else {}
    env = dict(os.environ)
    t0 = time.time()
    with open(out / "stdout.log", "wb") as so, open(out / "stderr.log", "wb") as se:
        proc = subprocess.Popen(args.cmd, cwd=root, env=env, stdout=so, stderr=se)
    stop = threading.Event()
    seen_runs: set[str] = set()

    def snap_runs() -> None:
        # Content-compared on every poll: two runs' run.json can share size and mtime second.
        target = nx_cache / "run.json"
        while True:
            final = stop.is_set()
            try:
                data = target.read_bytes()
                digest = hashlib.sha1(data).hexdigest()
                if digest not in seen_runs:
                    seen_runs.add(digest)
                    (out / "nx-runs" / f"{time.time():.3f}-{digest[:8]}.json").write_bytes(data)
            except OSError:
                pass
            if final:
                break
            time.sleep(0.2)

    def sample_dispatches(fh) -> None:  # noqa: ANN001
        while not stop.is_set():
            fh.write(json.dumps({"t": time.time(), "dispatches": dispatches(root)}) + "\n")
            fh.flush()
            stop.wait(120)

    samples = open(out / "samples.jsonl", "w")  # noqa: SIM115
    threads = [threading.Thread(target=snap_runs, daemon=True), threading.Thread(target=sample_dispatches, args=(samples,), daemon=True)]
    for t in threads:
        t.start()
    seen: dict[tuple[int, str, str], list[float]] = {}
    last_load = 0.0
    with open(out / "procs.jsonl", "w") as pfh:
        while proc.poll() is None:
            now = time.time()
            procs = read_procs()
            for pid in procs:
                c = classify(pid, procs, proc.pid)
                if c is None:
                    continue
                key = (pid, c[0], c[1])
                if key not in seen:
                    seen[key] = [now, now]
                    pfh.write(json.dumps({"event": "start", "pid": pid, "kind": c[0], "label": c[1], "t": now}) + "\n")
                    pfh.flush()
                else:
                    seen[key][1] = now
            if now - last_load >= 5:
                with open("/proc/loadavg") as lf:
                    load1 = float(lf.read().split()[0])
                samples.write(json.dumps({"t": now, "load1": load1}) + "\n")
                samples.flush()
                last_load = now
            time.sleep(0.5)
    t1 = time.time()
    stop.set()
    time.sleep(0.5)
    samples.write(json.dumps({"t": t1, "dispatches": dispatches(root)}) + "\n")
    samples.close()
    for t in threads:
        t.join(timeout=5)
    phases = [{"pid": k[0], "kind": k[1], "label": k[2], "first_seen": v[0], "last_seen": v[1]} for k, v in seen.items()]
    meta.update({"start": t0, "end": t1, "exit_status": proc.returncode, "cmd": args.cmd})
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    (out / "phases.json").write_text(json.dumps(phases, indent=2))
    # The gate's own preserved logs, already redacted by the recipes that wrote them.
    logs = root / ".logs"
    if logs.is_dir():
        dest = out / "logs"
        dest.mkdir(exist_ok=True)
        for f in logs.glob("*.log"):
            if f.stat().st_mtime >= t0 - 1:
                shutil.copy2(f, dest / f.name)
    summarise(out)
    return proc.returncode


def iso(ts: str) -> float:
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def summarise(out: Path) -> dict:
    meta = json.loads((out / "meta.json").read_text())
    phases = json.loads((out / "phases.json").read_text())
    t0, t1 = meta["start"], meta["end"]
    loads, disp = [], []
    for line in (out / "samples.jsonl").read_text().splitlines():
        rec = json.loads(line)
        if "load1" in rec:
            loads.append(rec["load1"])
        if "dispatches" in rec:
            disp.append(rec["dispatches"])
    budget_eq = [d.get("budget_script_equivalent") for d in disp]
    proven = [d["proven"] for d in disp if isinstance(d.get("proven"), int)]
    host = {
        "load1_max": max(loads) if loads else None,
        "load1_mean": round(sum(loads) / len(loads), 2) if loads else None,
        "load1_samples": len(loads),
        "dispatches_max": max(budget_eq) if budget_eq and all(isinstance(x, int) for x in budget_eq) else "unknown",
        "dispatches_proven_max": max(proven) if proven else None,
        "dispatches_samples": disp,
    }
    # Gate phases from the just recipes, check sub-phases from the top-level nx.sh calls.
    phase_rows = []
    for p in sorted(phases, key=lambda p: p["first_seen"]):
        phase_rows.append(
            {
                "kind": p["kind"],
                "label": p["label"],
                "start_offset_s": round(p["first_seen"] - t0, 1),
                "duration_s": round(p["last_seen"] - p["first_seen"], 1),
            }
        )
    # Nx runs: keep the snapshots whose run.startTime matches a top-level nx.sh start.
    nx_starts = [p for p in phases if p["kind"] == "nx"]
    runs = []
    for f in sorted((out / "nx-runs").glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        rs = iso(data["run"]["startTime"])
        if not (t0 - 2 <= rs <= t1 + 2):
            continue
        match = min(nx_starts, key=lambda p: abs(p["first_seen"] - rs), default=None)
        if match is None or abs(match["first_seen"] - rs) > 6:
            continue
        tasks = []
        for task in data["tasks"]:
            tasks.append(
                {
                    "task": task["taskId"],
                    "cache": task.get("cacheStatus"),
                    "status": task.get("status"),
                    "start_offset_s": round(iso(task["startTime"]) - t0, 1),
                    "duration_s": round(iso(task["endTime"]) - iso(task["startTime"]), 1),
                }
            )
        runs.append(
            {
                "invocation": match["label"],
                "command": data["run"]["command"],
                "start_offset_s": round(rs - t0, 1),
                "duration_s": round(iso(data["run"]["endTime"]) - rs, 1),
                "tasks": tasks,
                "snapshot": f.name,
            }
        )
    gate_phases = None
    logs = out / "logs"
    v, g = logs / "gate-llmlint-validate.log", logs / "gate-llmlint.log"
    if v.exists() and g.exists():
        check_start = min((p["first_seen"] for p in phases if p["kind"] == "just" and p["label"] == "check"), default=g.stat().st_mtime)
        ve, ge = v.stat().st_mtime, g.stat().st_mtime
        gate_phases = {
            "method": "validate ends at its log's last write; the judged-lint phase ends at its log's last write; `just check` from its process start to the gate's exit",
            "llmlint_validate_s": round(ve - t0, 1),
            "lint_llm_diff_s": round(ge - ve, 1),
            "handoff_s": round(check_start - ge, 1),
            "just_check_s": round(t1 - check_start, 1),
        }
    lint = None
    for path in [out / "stderr.log", out / "stdout.log", *sorted((out / "logs").glob("*.log"))]:
        if path.exists():
            m = re.search(r"^lint-llm-diff: (judged|replayed)[^\n]*", path.read_text(errors="replace"), re.M)
            if m:
                lint = m.group(0)
                break
    pytest_summary = summarise_pytest(out / "pytest")
    summary = {
        **{k: meta[k] for k in meta if k not in ("start", "end")},
        "started_at": dt.datetime.fromtimestamp(t0, dt.UTC).isoformat(),
        "wall_clock_s": round(t1 - t0, 1),
        "host": host,
        "gate_phases": gate_phases,
        "phases": phase_rows,
        "lint_llm_diff_provenance": lint,
        "nx_runs": runs,
        "pytest_tiers": pytest_summary,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def summarise_pytest(directory: Path) -> dict:
    if not directory.is_dir():
        return {}
    tiers: dict[str, dict] = {}
    tests_out: dict[str, list] = collections.defaultdict(list)
    for f in sorted(directory.glob("*.jsonl")):
        recs = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
        if not recs:
            continue
        tier = recs[0]["tier"]
        t = tiers.setdefault(
            tier, {"controller": None, "tests": collections.defaultdict(lambda: {"duration": 0.0, "group": None, "exc": None, "worker": None})}
        )
        if recs[0]["role"] in ("controller", "solo"):
            start = recs[0]["t"]
            fin = next((r for r in recs if r["type"] == "finish"), None)
            t["controller"] = {
                "start": start,
                "wall_clock_s": round(fin["t"] - start, 1) if fin else None,
                "exitstatus": fin["exitstatus"] if fin else None,
                "args": recs[0]["args"],
            }
            if recs[0]["role"] == "controller":
                continue
        for r in recs:
            if r["type"] != "phase":
                continue
            row = t["tests"][r["nodeid"]]
            row["duration"] += r["duration"]
            row["group"] = r["group"]
            row["worker"] = r["worker"]
            if r["exc"] and r["exc"] not in ("Skipped",) and row["exc"] is None:
                row["exc"] = f"{r['when']}:{r['exc']}"
    result = {}
    for tier, t in tiers.items():
        tests = t["tests"]
        groups: dict[str, float] = collections.defaultdict(float)
        group_counts: dict[str, int] = collections.Counter()
        modules: dict[str, float] = collections.defaultdict(float)
        for nodeid, row in tests.items():
            groups[str(row["group"])] += row["duration"]
            group_counts[str(row["group"])] += 1
            modules[nodeid.split("::")[0]] += row["duration"]
            tests_out[tier].append({"nodeid": nodeid, **row, "duration": round(row["duration"], 3)})
        summed = sum(r["duration"] for r in tests.values())
        result[tier] = {
            "wall_clock_s": (t["controller"] or {}).get("wall_clock_s"),
            "exitstatus": (t["controller"] or {}).get("exitstatus"),
            "tests": len(tests),
            "summed_test_s": round(summed, 1),
            "group_s": {g: round(v, 1) for g, v in sorted(groups.items(), key=lambda kv: -kv[1])},
            "group_tests": dict(group_counts),
            "failures": sorted(n for n, r in tests.items() if r["exc"]),
            "slowest_modules": [[m, round(v, 1)] for m, v in sorted(modules.items(), key=lambda kv: -kv[1])[:30]],
        }
    (directory.parent / "pytest-tests.json").write_text(json.dumps(tests_out, indent=1))
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="verb", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--root", required=True)
    r.add_argument("--nx-cache", required=True)
    r.add_argument("--meta")
    r.add_argument("cmd", nargs=argparse.REMAINDER)
    s = sub.add_parser("summarise")
    s.add_argument("out")
    a = ap.parse_args()
    if a.verb == "run":
        if a.cmd and a.cmd[0] == "--":
            a.cmd = a.cmd[1:]
        return run(a)
    print(json.dumps(summarise(Path(a.out)), indent=2)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
