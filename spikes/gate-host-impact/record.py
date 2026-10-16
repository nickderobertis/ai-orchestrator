"""Spike prototype: one measurement of what a cold pre-push gate does to the host.

Throwaway. It attributes to the gate exactly two places — the root it makes for the gate
under ``$ONEPIPELINE_NODE_SCRATCH_DIR`` and every path a writer registered into
``$ORCHESTRATOR_GATE_SCRATCH`` — and never reads, counts or removes anything else in the
host's ``/tmp``. It signals only processes in its own descendant tree.

Order, which is the measurement's rulings:
  1. root with ``tmp/`` and ``scratch/``; TMPDIR/TMP/TEMP -> ``tmp/``.
  2. become a child subreaper, so every orphan the gate leaves is re-parented here.
  3. run ``just gate <remote> <base>`` with NX_SKIP_NX_CACHE=true, ORCHESTRATOR_GATE_PUSH unset.
  4. every 5 s (and once at exit) ``du -s --block-size=1`` over ``tmp/`` + registered paths.
  5. at gate exit: list every live descendant at any depth (leaked), then count leftovers,
     then wait 10 s, TERM the tree, 5 s, re-walk, KILL what remains.
  6. remove the root and every registered path, and nothing else.
"""

from __future__ import annotations

import argparse
import ast
import ctypes
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CHECKOUT))
from orchestrator.redaction import redact, secret_values  # noqa: E402

PR_SET_CHILD_SUBREAPER = 36
SAMPLE_SECONDS = 5.0
PROC_SAMPLE_SECONDS = 1.0
DISK_FLOOR_BYTES = 5 * 1024**3
LEAK_GRACE_SECONDS = 10.0
TERM_GRACE_SECONDS = 5.0
UV_LOCK = re.compile(r"^uv-[0-9a-f]{16}\.lock$")
CLK_TCK = os.sysconf("SC_CLK_TCK")
SELF = os.getpid()

_SECRETS = secret_values()


def now() -> float:
    return time.monotonic()


def boot_time() -> float:
    for line in Path("/proc/stat").read_text().splitlines():
        if line.startswith("btime "):
            return float(line.split()[1])
    return 0.0


BTIME = boot_time()


# ---------------------------------------------------------------------- process tree


class Excluded:
    """Pids of the recorder's own sampler commands, whose subtrees are never the gate's."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pids: set[int] = set()

    def add(self, pid: int) -> None:
        with self._lock:
            self._pids.add(pid)

    def discard(self, pid: int) -> None:
        with self._lock:
            self._pids.discard(pid)

    def snapshot(self) -> set[int]:
        with self._lock:
            return set(self._pids)


EXCLUDED = Excluded()


def read_stat(pid: int) -> tuple[int, str, int, str] | None:
    """(ppid, state, starttime ticks, comm) or None when the process is gone."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    lparen, rparen = raw.find("("), raw.rfind(")")
    comm = raw[lparen + 1 : rparen]
    fields = raw[rparen + 2 :].split()
    return int(fields[1]), fields[0], int(fields[19]), comm


def descendants(root: int = SELF, *, with_zombies: bool = False) -> dict[int, tuple[int, str, int, str]]:
    """Every process under ``root`` at any depth, found by walking parent links in /proc.

    Excludes ``root`` itself and the subtree of every sampler command the recorder runs.
    """
    table: dict[int, tuple[int, str, int, str]] = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            stat = read_stat(int(entry))
            if stat is not None:
                table[int(entry)] = stat
    children: dict[int, list[int]] = defaultdict(list)
    for pid, (ppid, *_rest) in table.items():
        children[ppid].append(pid)
    excluded = EXCLUDED.snapshot()
    found: dict[int, tuple[int, str, int, str]] = {}
    stack = list(children.get(root, []))
    while stack:
        pid = stack.pop()
        if pid in excluded or pid in found:
            continue
        stat = table[pid]
        stack.extend(children.get(pid, []))
        if stat[1] == "Z" and not with_zombies:
            continue
        found[pid] = stat
    return found


def read_environ(pid: int) -> dict[str, str]:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {}
    env = {}
    for item in raw.split(b"\0"):
        if b"=" in item:
            key, _, value = item.partition(b"=")
            env[key.decode(errors="replace")] = value.decode(errors="replace")
    return env


def cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return " ".join(part.decode(errors="replace") for part in raw.split(b"\0") if part)


def describe(pid: int, stat: tuple[int, str, int, str]) -> dict[str, object]:
    ppid, state, start, comm = stat
    env = read_environ(pid)
    secrets = sorted(set(_SECRETS) | set(secret_values(env)), key=lambda kv: (-len(kv[1]), kv[0]))
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        cwd = None
    started_by = {
        key: env[key]
        for key in ("PYTEST_CURRENT_TEST", "NX_TASK_TARGET_PROJECT", "NX_TASK_TARGET_TARGET")
        if key in env
    }
    line = redact(cmdline(pid), secrets)
    return {
        "pid": pid,
        "ppid": ppid,
        "state": state,
        "comm": comm,
        "cmdline": line[:2000],
        "cwd": cwd,
        "started_at_epoch": round(BTIME + start / CLK_TCK, 2),
        "started_by": started_by or attribute_from(line, cwd),
    }


def attribute_from(line: str, cwd: str | None) -> dict[str, str]:
    """Best-effort attribution when the environment names no test or Nx task."""
    out: dict[str, str] = {}
    match = re.search(r"nx(?:\.js)? run ([\w./-]+:[\w-]+)", line)
    if match:
        out["nx_task"] = match.group(1)
    match = re.search(r"(tests/\S+\.py(?:::\S+)?)", line)
    if match:
        out["test_path"] = match.group(1)
    if cwd and "/popen-gw" in cwd:
        out["pytest_tmp_dir"] = cwd
    return out


# --------------------------------------------------------------------------- disk


def registered_paths(scratch: Path) -> list[Path]:
    paths: list[Path] = []
    try:
        entries = sorted(scratch.iterdir())
    except OSError:
        return paths
    for entry in entries:
        try:
            value = entry.read_text().strip()
        except OSError:
            continue
        if value.startswith("/") and Path(value) not in paths:
            paths.append(Path(value))
    return paths


def du_bytes(paths: list[Path]) -> tuple[int, dict[str, int], float]:
    """Allocated bytes over ``paths`` by one ``du`` walk, and how long it took."""
    started = now()
    existing = [str(p) for p in paths if p.exists()]
    if not existing:
        return 0, {}, now() - started
    proc = subprocess.Popen(
        ["du", "-s", "--block-size=1", "--", *existing],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    EXCLUDED.add(proc.pid)
    try:
        out, _ = proc.communicate()
    finally:
        EXCLUDED.discard(proc.pid)
    per: dict[str, int] = {}
    for line in out.splitlines():
        size, _, path = line.partition("\t")
        if size.isdigit():
            per[path] = int(size)
    return sum(per.values()), per, now() - started


def free_bytes(path: Path) -> int:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def allocated(path: Path) -> tuple[int, int]:
    """(entries, allocated bytes) under ``path``, itself included, without following links."""
    entries = 0
    size = 0
    try:
        st = path.lstat()
    except OSError:
        return 0, 0
    entries += 1
    size += st.st_blocks * 512
    if path.is_dir() and not path.is_symlink():
        for dirpath, dirnames, filenames in os.walk(path, onerror=lambda _e: None):
            for name in dirnames + filenames:
                try:
                    sub = os.lstat(os.path.join(dirpath, name))
                except OSError:
                    continue
                entries += 1
                size += sub.st_blocks * 512
    return entries, size


def name_pattern(name: str) -> str:
    """A top-level name with its random parts replaced by ``*``."""
    if UV_LOCK.match(name):
        return "uv-<16 hex>.lock"
    match = re.fullmatch(r"tmp[a-z0-9_]{8}((?:\.\w+)*)", name)
    if match:
        return f"tmp*{match.group(1)}  (Python tempfile default prefix)"
    if re.fullmatch(r"tmp\.[A-Za-z0-9]{10}", name):
        return "tmp.*  (mktemp default template)"
    # tempfile's 8-character suffix [a-z0-9_], mktemp's XXXXXX ([A-Za-z0-9]), a numeric tail.
    pattern = re.sub(r"(?<=[-.])[a-z0-9_]{8}$", "*", name)
    pattern = re.sub(r"(?<=[-.])[A-Za-z0-9]{6}$", "*", pattern)
    pattern = re.sub(r"(?<=-)\d+$", "*", pattern)
    return re.sub(r"^\d+$", "*", pattern)


# ----------------------------------------------------------------- code search


CODE_SUFFIXES = (".py", ".sh", ".bash")


def tracked_code() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=CHECKOUT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    files = []
    for rel in out:
        if rel.startswith(("scratch/", "spikes/")):
            continue
        if rel.endswith(CODE_SUFFIXES) or rel == "justfile" or rel.startswith(".githooks/"):
            files.append(CHECKOUT / rel)
    return files


def enclosing(path: Path, text: str, lineno: int) -> str:
    """The function (Python, shell) or recipe (justfile) holding ``lineno``."""
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return "<unparsed>"
        best = "<module>"
        best_start = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                end = getattr(node, "end_lineno", node.lineno)
                if node.lineno <= lineno <= end and node.lineno >= best_start:
                    best, best_start = node.name, node.lineno
        return best
    lines = text.splitlines()
    for index in range(lineno - 1, -1, -1):
        line = lines[index]
        if path.name == "justfile":
            match = re.match(r"^([A-Za-z_][\w-]*)\b[^:=]*:(?!=)", line)
        else:
            match = re.match(r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{?", line)
        if match:
            closed = path.name != "justfile" and any(
                re.match(r"^\}", between) for between in lines[index + 1 : lineno - 1]
            )
            return "<top level>" if closed else match.group(1)
    return "<top level>"


WRITE_HINT = re.compile(
    r"mkdtemp|mkstemp|mkdir|mktemp|TemporaryDirectory|NamedTemporaryFile|\bopen\(|write_|"
    r"(?<![-=>&])>\s*[\"$/\w]|PARENT|\bdir=|cp |touch "
)

#: A line that only reports (a message, a usage text, a docstring quoting a path).
MESSAGE = re.compile(r"""^\s*(echo|printf|fail|raise|print\()|`""")

#: Search patterns: (kind, regex). Each line matching one is a candidate writer.
SEARCHES = [
    ("literal-tmp", re.compile(r"""(?<![\w:-])["'(=\s]?/tmp(?=[/"'\s)]|$)""")),
    ("tempfile-explicit-dir", re.compile(r"tempfile\.\w+\([^)]*\bdir=")),
    ("mktemp-explicit-dir", re.compile(r"mktemp\b[^|;&]*?(?:-p\s|--tmpdir=|\S*/\S*X{3,})")),
    ("nx-native-file-cache", re.compile(r"nx-native-file-cache|NX_NATIVE_FILE_CACHE_DIRECTORY|NX_CACHE_DIRECTORY")),
    ("home-scratch", re.compile(r"(\$HOME|\$\{HOME\}|Path\.home\(\)|XDG_CACHE_HOME)[^\n]*(\.cache|scratch|/tmp)")),
    ("nx-not-via-wrapper", re.compile(r"\b(bunx|npx)\s+nx\b|node_modules/\.bin/nx\b")),
    ("node-scratch-dir", re.compile(r"ONEPIPELINE_NODE_SCRATCH_DIR")),
]

#: Sites read by hand: file -> (status, why). Anything else is classified heuristically.
CURATED = {
    ("tests/short_state.py", "literal-tmp"): (
        "registered",
        "PARENT = /tmp: a Unix socket address leaves no room to nest the base under the gate's root, so short_state_base registers each /tmp/aio-* base in $ORCHESTRATOR_GATE_SCRATCH (this branch's throwaway patch)",
    ),
    ("tests/short_state.py", "tempfile-explicit-dir"): (
        "registered",
        "tempfile.mkdtemp(prefix='aio-', dir=PARENT=/tmp); registered as above",
    ),
    ("scripts/nx.sh", "literal-tmp"): (
        "registered",
        "this branch's throwaway patch: Nx's plugin-worker sockets get a short /tmp/aio-nx-* directory (NX_SOCKET_DIR), registered in $ORCHESTRATOR_GATE_SCRATCH and removed on exit, because the root's depth pushed them past the socket address limit (attempt 1)",
    ),
    ("scripts/nx.sh", "mktemp-explicit-dir"): (
        "registered",
        "mktemp -d /tmp/aio-nx-XXXXXX for NX_SOCKET_DIR; registered as above",
    ),
    ("tests/pytest_temp_root.py", "tempfile-explicit-dir"): (
        "contained",
        "dir=$PYTEST_DEBUG_TEMPROOT, which is unset at the top session (falls back to TMPDIR) and inherited as a root inside TMPDIR below it",
    ),
    ("scripts/nx.sh", "nx-native-file-cache"): (
        "neither",
        "NX_CACHE_DIRECTORY and NX_NATIVE_FILE_CACHE_DIRECTORY under ${XDG_CACHE_HOME:-$HOME/.cache}/ai-orchestrator/: persistent per-repository caches by design, outside TMPDIR and unregistered; not scratch, so not counted",
    ),
    ("scripts/nx.sh", "home-scratch"): (
        "neither",
        "the same persistent Nx caches under $XDG_CACHE_HOME; by design, not scratch",
    ),
    ("scripts/host-sweep.sh", "home-scratch"): (
        "neither",
        "the sweep's own memo under $XDG_CACHE_HOME/ai-orchestrator/sweep; session setup's, not the gate's",
    ),
    ("scripts/repos-bootstrap.sh", "home-scratch"): (
        "neither",
        "repos-bootstrap memo under $XDG_CACHE_HOME; session setup's, not the gate's",
    ),
    ("orchestrator/follow_up_comments.py", "tempfile-explicit-dir"): (
        "contained",
        "staging directory beside its destination for an atomic rename; lives where the destination does",
    ),
    ("orchestrator/follow_up_drafts.py", "tempfile-explicit-dir"): (
        "contained",
        "atomic-write staging file beside its destination",
    ),
    ("orchestrator/spike_brief.py", "tempfile-explicit-dir"): (
        "contained",
        "atomic-write staging file beside its destination",
    ),
    ("scripts/allowlist-probe.py", "tempfile-explicit-dir"): (
        "contained-if-unset",
        "base = $ONEPIPELINE_NODE_SCRATCH_DIR or tempfile.gettempdir(): follows TMPDIR only when that variable is unset (the harness unsets it for the gate)",
    ),
    ("scripts/render-diagram.sh", "mktemp-explicit-dir"): (
        "contained",
        "staging file beside its own destination",
    ),
    ("tests/e2e/launch_monitor_observer/test_quota_fallthrough_e2e.py", "tempfile-explicit-dir"): (
        "contained",
        "dir=tmp_path, under the pytest root inside TMPDIR",
    ),
}


def classify(rel: str, kind: str, line: str) -> tuple[str, str] | None:
    if (rel, kind) in CURATED:
        return CURATED[(rel, kind)]
    if MESSAGE.search(line) and not re.search(r"\$\(mktemp|mkdtemp", line):
        return None
    if kind == "nx-not-via-wrapper" and rel == "scripts/nx.sh":
        return None  # the wrapper resolving the pinned binary is the one sanctioned run
    if kind in ("literal-tmp", "mktemp-explicit-dir") and re.search(r"\$\{TMPDIR:-/tmp\}", line):
        return ("contained", "${TMPDIR:-/tmp} follows TMPDIR")
    if kind == "node-scratch-dir":
        if rel.startswith("tests/"):
            return None  # tests set it for the process under test; reads only
        return ("contained-if-unset", "prefers $ONEPIPELINE_NODE_SCRATCH_DIR over TMPDIR; contained only when it is unset (the harness unsets it for the gate)")
    if kind == "nx-not-via-wrapper":
        if rel.startswith("tests/"):
            return ("contained", "a test's fake Nx binary inside its own temporary checkout, not a real Nx run")
        return ("neither", "an Nx run that bypasses scripts/nx.sh and its cache keys")
    if kind == "home-scratch":
        if not WRITE_HINT.search(line):
            return None
        return ("neither", "writes under $HOME/$XDG_CACHE_HOME, outside TMPDIR and unregistered")
    if kind == "literal-tmp":
        if not WRITE_HINT.search(line):
            return None  # a /tmp string used as data (a fake path, a message), not a writer
        return ("neither", "writes under a literal /tmp, outside TMPDIR and unregistered")
    if kind == "tempfile-explicit-dir":
        return ("depends-on-dir", "explicit dir= argument; contained when that directory is")
    if kind == "mktemp-explicit-dir":
        return ("depends-on-dir", "explicit template directory; contained when that directory is")
    return ("neither", kind)


def escape_search() -> dict[str, object]:
    findings = []
    data_only: dict[str, int] = defaultdict(int)
    for path in tracked_code():
        rel = str(path.relative_to(CHECKOUT))
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            for kind, regex in SEARCHES:
                if not regex.search(line):
                    continue
                verdict = classify(rel, kind, line)
                if verdict is None:
                    data_only[rel] += 1
                    continue
                status, why = verdict
                findings.append(
                    {
                        "kind": kind,
                        "file": rel,
                        "line": lineno,
                        "function": enclosing(path, text, lineno),
                        "status": status,
                        "why": why,
                        "text": stripped[:240],
                    }
                )
    return {
        "findings": findings,
        "non_writer_mentions": dict(sorted(data_only.items())),
        "note": "static search of tracked code (py, sh, justfile, hooks); 'non_writer_mentions' counts lines that matched a search but read or quote a path rather than write one",
    }


#: Leftover names no tracked code spells, with the tool that writes them.
EXTERNAL_PRODUCERS = {
    "node-compile-cache": "Node's module compile cache: the Nx CLI enables it (node_modules/nx/dist/src/utils/enable-compile-cache.js) and Node defaults it to os.tmpdir()/node-compile-cache unless NODE_COMPILE_CACHE names one; started through scripts/nx.sh",
    "uv-": "uv's per-interpreter lock files in the system temp directory",
    "puppeteer_dev_chrome_profile-": "a Chromium profile puppeteer makes under os.tmpdir() for mermaid-cli, launched by scripts/render-diagram.sh (main) and orchestrator/budgets/design_doc_dag.py (render through mmdc)",
    "org.chromium.Chromium.": "Chromium's own temporary files under TMPDIR, from the same mermaid-cli renders (scripts/render-diagram.sh, orchestrator/budgets/design_doc_dag.py)",
    "cursor-agent-logs-": "the cursor-agent CLI's log directory under TMPDIR, keyed by uid; a harness oneharness can spawn",
    "claude-": "Claude Code's per-user temporary directory (claude-<uid>) under TMPDIR, made by a claude CLI a test or harness turn spawns",
    "onepipeline-scratch": "the onepipeline engine's own scratch directory under TMPDIR (no tracked code spells it)",
    "codex-bwrap-synthetic-mount-targets-": "the codex CLI's bubblewrap mount-target scratch under TMPDIR (the judged llmlint tier's codex turn)",
}

#: A tempfile default name's suffix -> the code writing it with delete=False (it outlives its handle).
def suffix_producers(suffix: str) -> list[str]:
    out = []
    pattern = re.compile(r"suffix=[\"']" + re.escape(suffix) + r"[\"']")
    for path in tracked_code():
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        for lineno, line in enumerate(lines, 1):
            window = " ".join(lines[max(0, lineno - 3) : lineno + 2])
            if pattern.search(line) and "delete=False" in window:
                out.append(f"{path.relative_to(CHECKOUT)}:{lineno} ({enclosing(path, text, lineno)}) NamedTemporaryFile(delete=False)")
    return out


def producers(prefix: str) -> list[str]:
    """Code sites holding the literal ``prefix`` of a leftover name."""
    if len(prefix) < 3:
        return []
    out = [why for name, why in EXTERNAL_PRODUCERS.items() if prefix.startswith(name)]
    if out:
        return out
    for path in tracked_code():
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if prefix not in text:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if prefix in line and not line.strip().startswith("#"):
                out.append(f"{path.relative_to(CHECKOUT)}:{lineno} ({enclosing(path, text, lineno)})")
                break
    return out[:8]


def pattern_producers(pattern: str) -> list[str]:
    match = re.match(r"tmp\*((?:\.\w+)+)  \(Python tempfile", pattern)
    if match:
        return suffix_producers(match.group(1)) or ["Python tempfile default name; no tracked NamedTemporaryFile(delete=False) with this suffix"]
    if pattern.startswith("tmp"):
        return ["a default tempfile/mktemp name: any caller, no prefix to search"]
    prefix = pattern.split("*")[0].split("  ")[0]
    return producers(prefix) if prefix else []


def inventory(places: list[Path]) -> dict[str, object]:
    groups: dict[tuple[str, str], dict[str, object]] = {}
    uv_locks = 0
    uv_lock_bytes = 0
    for place in places:
        if not place.exists():
            continue
        try:
            names = sorted(os.listdir(place))
        except OSError:
            continue
        for name in names:
            full = place / name
            if UV_LOCK.match(name):
                try:
                    st = full.lstat()
                except OSError:
                    continue
                if st.st_size == 0:
                    uv_locks += 1
                    uv_lock_bytes += st.st_blocks * 512
                    continue
            pattern = name_pattern(name)
            key = (str(place), pattern)
            group = groups.setdefault(
                key,
                {"place": str(place), "pattern": pattern, "top_level": 0, "entries": 0, "bytes": 0, "examples": []},
            )
            entries, size = allocated(full)
            group["top_level"] = int(group["top_level"]) + 1  # type: ignore[call-overload]
            group["entries"] = int(group["entries"]) + entries  # type: ignore[call-overload]
            group["bytes"] = int(group["bytes"]) + size  # type: ignore[call-overload]
            examples = group["examples"]
            assert isinstance(examples, list)
            if len(examples) < 3:
                examples.append(name)
    out = []
    for group in groups.values():
        group["produced_by"] = pattern_producers(str(group["pattern"]))
        out.append(group)
    out.sort(key=lambda g: -int(g["bytes"]))  # type: ignore[call-overload]
    return {
        "groups": out,
        "entries_total": sum(int(g["entries"]) for g in out),  # type: ignore[call-overload]
        "bytes_total": sum(int(g["bytes"]) for g in out),  # type: ignore[call-overload]
        "zero_byte_uv_locks": {"count": uv_locks, "allocated_bytes": uv_lock_bytes},
    }


# -------------------------------------------------------------------------- run


class Samplers:
    def __init__(self, root: Path, tmp: Path, scratch: Path) -> None:
        self.root, self.tmp, self.scratch = root, tmp, scratch
        self.stop = threading.Event()
        self.floor_hit = threading.Event()
        self.disk: list[dict[str, object]] = []
        self.host: list[dict[str, object]] = []
        self.seen: set[tuple[int, int]] = set()
        self.max_live = 0
        self.protected: set[int] = set()
        self.t0 = now()
        self.threads = [
            threading.Thread(target=self._disk, daemon=True),
            threading.Thread(target=self._host, daemon=True),
            threading.Thread(target=self._procs, daemon=True),
        ]

    def start(self) -> None:
        for thread in self.threads:
            thread.start()

    def join(self) -> None:
        self.stop.set()
        for thread in self.threads:
            thread.join()

    def sample_disk(self, label: str = "periodic") -> dict[str, object]:
        paths = [self.tmp, *registered_paths(self.scratch)]
        total, per, walk = du_bytes(paths)
        free = free_bytes(self.root)
        sample = {
            "t": round(now() - self.t0, 2),
            "label": label,
            "bytes": total,
            "walk_seconds": round(walk, 3),
            "paths_walked": len(per),
            "per_path": per,
            "free_bytes": free,
        }
        self.disk.append(sample)
        if free < DISK_FLOOR_BYTES:
            self.floor_hit.set()
        return sample

    def _disk(self) -> None:
        while not self.stop.is_set():
            started = now()
            self.sample_disk()
            self.stop.wait(max(0.0, SAMPLE_SECONDS - (now() - started)))

    def _host(self) -> None:
        while not self.stop.is_set():
            started = now()
            load1 = float(Path("/proc/loadavg").read_text().split()[0])
            dispatches: int | None = None
            proc = subprocess.Popen(
                [str(CHECKOUT / "scripts/budget-dispatches.sh")],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                env={k: v for k, v in os.environ.items() if k not in ("TMPDIR", "TMP", "TEMP")},
            )
            EXCLUDED.add(proc.pid)
            try:
                out, _ = proc.communicate()
            finally:
                EXCLUDED.discard(proc.pid)
            if out.strip().isdigit():
                dispatches = int(out.strip())
            self.host.append({"t": round(now() - self.t0, 2), "load1": load1, "dispatches": dispatches})
            self.stop.wait(max(0.0, SAMPLE_SECONDS - (now() - started)))

    def _procs(self) -> None:
        while not self.stop.is_set():
            live = descendants()
            self.max_live = max(self.max_live, len(live))
            for pid, stat in live.items():
                self.seen.add((pid, stat[2]))
            self._reap()
            self.stop.wait(PROC_SAMPLE_SECONDS)

    def _reap(self) -> None:
        """Reap adopted orphans that died, so an hour of them does not pile up as zombies.

        Only zombies whose parent is the recorder and which no `Popen` here owns.
        """
        protected = self.protected | EXCLUDED.snapshot()
        for pid, stat in descendants(with_zombies=True).items():
            if stat[0] == SELF and stat[1] == "Z" and pid not in protected:
                try:
                    os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    pass


def failed_tests(text: str) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"^(?:FAILED|ERROR) (\S+?)(?: - (.*))?$", text, re.M):
        nodeid = match.group(1)
        if "::" in nodeid or nodeid.endswith(".py"):
            found.setdefault(nodeid, {"nodeid": nodeid, "reason": (match.group(2) or "")[:400]})
    return list(found.values())


SOCKET_TOO_LONG = re.compile(r"exceeds the maximum socket length|AF_UNIX path too long|path too long for a Unix|sun_path|socket path .*too long", re.I)


def rerun_without_harness(nodeids: list[str], env: dict[str, str]) -> dict[str, str]:
    """Re-run each failed test once on this tree without the harness's root (TMPDIR as inherited)."""
    results = {}
    for nodeid in nodeids[:20]:
        path = nodeid.split("::")[0]
        if not (CHECKOUT / path).exists():
            results[nodeid] = "not rerun: path not relative to the checkout root"
            continue
        proc = subprocess.run(
            [str(CHECKOUT / ".venv/bin/python"), "-m", "pytest", "-q", "-p", "no:cacheprovider", nodeid],
            cwd=CHECKOUT,
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        results[nodeid] = "passes without the harness" if proc.returncode == 0 else f"also fails without the harness (exit {proc.returncode})"
    return results


def stop_tree(leaked_pids: set[int]) -> dict[str, object]:
    """Wait for the leaked processes, then TERM the whole tree, then KILL what survives."""
    record: dict[str, object] = {"exited_within_grace": [], "needed_term": [], "needed_kill": [], "alive_after_kill": []}
    deadline = now() + LEAK_GRACE_SECONDS
    while now() < deadline and descendants():
        time.sleep(0.25)
    still = descendants()
    record["exited_within_grace"] = sorted(leaked_pids - set(still))
    if not still:
        return record
    for pid in still:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    record["needed_term"] = sorted(still)
    deadline = now() + TERM_GRACE_SECONDS
    while now() < deadline and descendants():
        time.sleep(0.25)
    survivors = descendants()  # re-walked: children started meanwhile included
    for pid in survivors:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    record["needed_kill"] = [describe(pid, stat) for pid, stat in survivors.items()]
    time.sleep(1.0)
    record["alive_after_kill"] = sorted(descendants())
    return record


def reap_zombies() -> None:
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def prior_attempt(path: Path) -> dict[str, object]:
    prior = json.loads(path.read_text())
    return {
        "summary_file": str(path.relative_to(CHECKOUT)) if path.is_relative_to(CHECKOUT) else str(path),
        "measured_at": prior["measured_at"],
        "gate": {k: prior["gate"][k] for k in ("exit_status", "wall_clock_seconds", "processes_started_lower_bound", "log_tail")},
        "peak_temp_disk_bytes": prior["peak_temp_disk"]["bytes"],
        "leaked_processes": prior["leaked_processes"]["count"],
        "leftovers": prior["leftovers"],
        "breakage": prior["breakage"],
        "cleanup": prior["cleanup"],
    }


def safe_remove(path: Path, root: Path) -> str:
    resolved = path.resolve()
    if resolved in (Path("/"), Path("/tmp"), Path.home(), CHECKOUT) or CHECKOUT.is_relative_to(resolved):
        return "refused: not a scratch path"
    if not resolved.exists():
        return "absent"
    shutil.rmtree(resolved, ignore_errors=True)
    if resolved.exists():
        # A test may leave a directory it made unreadable (mode 000): open it up, then retry.
        for dirpath, dirnames, _files in os.walk(resolved):
            for name in dirnames:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o700)
                except OSError:
                    pass
        os.chmod(resolved, 0o700)
        for _ in range(3):
            subprocess.run(["chmod", "-R", "u+rwx", str(resolved)], stderr=subprocess.DEVNULL, check=False)
            shutil.rmtree(resolved, ignore_errors=True)
            if not resolved.exists():
                return "removed after opening unreadable directories"
    return "removed" if not resolved.exists() else "remained"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--base", default="main")
    parser.add_argument("--out", default=str(CHECKOUT / "spikes/gate-host-impact/summary.json"))
    parser.add_argument("--gate-cmd", help="replace `just gate` (harness self-test only)")
    parser.add_argument("--root-parent", help="where the gate's root is made (default $ONEPIPELINE_NODE_SCRATCH_DIR); created and, when empty, removed afterwards")
    parser.add_argument("--prior", action="append", default=[], help="an earlier attempt's summary, carried into this one's prior_attempts")
    args = parser.parse_args()

    node_scratch = Path(os.environ["ONEPIPELINE_NODE_SCRATCH_DIR"])
    root_parent = Path(args.root_parent) if args.root_parent else node_scratch
    made_parent = not root_parent.exists()
    root_parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="gate-host-impact-", dir=root_parent))
    tmp, scratch = root / "tmp", root / "scratch"
    tmp.mkdir()
    scratch.mkdir()
    gate_log = node_scratch / f"{root.name}.gate.log"

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        print(f"record: prctl(PR_SET_CHILD_SUBREAPER) failed: errno {ctypes.get_errno()}", file=sys.stderr)
        return 2

    base_env = dict(os.environ)
    env = dict(base_env)
    env.update({"TMPDIR": str(tmp), "TMP": str(tmp), "TEMP": str(tmp), "ORCHESTRATOR_GATE_SCRATCH": str(scratch), "NX_SKIP_NX_CACHE": "true"})
    removed_env = [k for k in ("ORCHESTRATOR_GATE_PUSH", "ONEPIPELINE_NODE_SCRATCH_DIR", "PYTEST_DEBUG_TEMPROOT") if env.pop(k, None) is not None]

    command = args.gate_cmd.split() if args.gate_cmd else ["just", "gate", args.remote, args.base]
    samplers = Samplers(root, tmp, scratch)
    samplers.start()
    started_wall = time.time()
    t_start = now()
    with gate_log.open("w") as log:
        gate = subprocess.Popen(command, cwd=CHECKOUT, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        samplers.protected.add(gate.pid)
        stopped_by_floor = False
        while True:
            status = gate.poll()
            if status is not None:
                break
            if samplers.floor_hit.is_set():
                stopped_by_floor = True
                break
            time.sleep(0.5)
    t_exit = now()

    # The moment the gate's top process exits (or the floor stopped it): every live member.
    leaked_tree = descendants()
    leaked = [describe(pid, stat) for pid, stat in sorted(leaked_tree.items())]
    samplers.join()
    final = samplers.sample_disk("at-exit")
    registered = registered_paths(scratch)
    # Before any signal: what is left.
    leftovers = inventory([tmp, *registered])
    for path in registered:
        if path.exists():
            # The registered base itself is a leftover: short_state_base removes it on a clean exit.
            leftovers.setdefault("registered_paths_still_present", []).append(str(path))  # type: ignore[union-attr]
    stopping = stop_tree(set(leaked_tree))
    if stopped_by_floor:
        gate.wait(timeout=30)
    reap_zombies()
    gate_status = gate.returncode

    raw = gate_log.read_text(errors="replace")
    gate_text = redact(raw, _SECRETS)
    gate_log.write_text(gate_text)
    check_log = CHECKOUT / ".logs/gate-check.log"
    check_text = redact(check_log.read_text(errors="replace"), _SECRETS) if check_log.exists() and check_log.stat().st_mtime >= started_wall else ""
    failures = failed_tests(gate_text + "\n" + check_text)
    # A lint report quoting this file's own pattern is not a socket failure.
    socket_lines = sorted(
        {line.strip()[:300] for line in (gate_text + check_text).splitlines() if SOCKET_TOO_LONG.search(line) and "AF_UNIX path too long|" not in line}
    )
    reruns = rerun_without_harness([f["nodeid"] for f in failures], base_env) if failures and not args.gate_cmd else {}
    for failure in failures:
        failure["without_harness"] = reruns.get(failure["nodeid"], "not rerun")

    escapes = escape_search()

    peak = max(samplers.disk, key=lambda s: int(s["bytes"]))  # type: ignore[call-overload]
    walks = [float(s["walk_seconds"]) for s in samplers.disk]  # type: ignore[arg-type]
    host_load = [float(h["load1"]) for h in samplers.host]  # type: ignore[arg-type]
    host_disp = [int(h["dispatches"]) for h in samplers.host if h["dispatches"] is not None]  # type: ignore[call-overload]

    cleanup = {"root": str(root), "root_removed": safe_remove(root, root), "registered": {str(p): safe_remove(p, root) for p in registered}}
    if made_parent:
        try:
            root_parent.rmdir()
            cleanup["root_parent"] = f"{root_parent} removed"
        except OSError as exc:
            cleanup["root_parent"] = f"{root_parent} remained: {exc}"

    summary = {
        "schema": "gate-host-impact-spike/1",
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_wall)),
        "checkout_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=CHECKOUT, capture_output=True, text=True).stdout.strip(),
        "command": command,
        "environment": {
            "root": str(root),
            "root_parent": str(root_parent),
            "TMPDIR": str(tmp),
            "ORCHESTRATOR_GATE_SCRATCH": str(scratch),
            "NX_SKIP_NX_CACHE": "true",
            "unset_for_the_gate": removed_env,
            "subreaper": True,
        },
        "complete": not stopped_by_floor,
        "stopped_by": "disk floor (free space under 5 GiB on the root's filesystem)" if stopped_by_floor else None,
        "gate": {
            "exit_status": gate_status,
            "wall_clock_seconds": round(t_exit - t_start, 1),
            "processes_started_lower_bound": len(samplers.seen),
            "processes_counting": f"distinct (pid, start time) pairs in the recorder's descendant tree, sampled every {PROC_SAMPLE_SECONDS:g} s; a process living under {PROC_SAMPLE_SECONDS:g} s between samples is missed, so this is a lower bound",
            "max_concurrent_processes": samplers.max_live,
            "log": str(gate_log),
            "log_tail": gate_text.splitlines()[-40:],
        },
        "peak_temp_disk": {
            "bytes": peak["bytes"],
            "gib": round(int(peak["bytes"]) / 1024**3, 3),  # type: ignore[call-overload]
            "at_t": peak["t"],
            "walk_seconds_at_peak": peak["walk_seconds"],
            "walk_seconds_max": max(walks),
            "walk_seconds_mean": round(sum(walks) / len(walks), 3),
            "cadence_seconds": SAMPLE_SECONDS,
            "at_exit_bytes": final["bytes"],
            "samples": len(samplers.disk),
            "limit": "allocated size (du -s --block-size=1) of <root>/tmp plus every registered path existing at each sample; an allocation made and removed between two 5 s samples is not seen",
            "free_bytes_min": min(int(s["free_bytes"]) for s in samplers.disk),  # type: ignore[call-overload]
        },
        "leaked_processes": {
            "count": len(leaked),
            "meaning": "alive when the disk floor stopped the gate mid-run, so the gate's own working processes, not leaks at a natural exit" if stopped_by_floor else "every member of the descendant tree alive the moment the gate's top process exited, zombies excluded",
            "processes": leaked,
            "stopping": stopping,
        },
        "leftovers": leftovers,
        "breakage": {
            "failed_tests": failures,
            "unix_socket_address_lines": socket_lines,
            "note": "failed tests parsed from the gate's output and .logs/gate-check.log; each re-run once on this tree with the inherited TMPDIR and no registration directory",
        },
        "escapes": escapes,
        "prior_attempts": [prior_attempt(Path(p)) for p in args.prior],
        "host": {
            "load1_peak": max(host_load) if host_load else None,
            "dispatches_peak": max(host_disp) if host_disp else None,
            "samples": len(samplers.host),
            "dispatch_counter": "scripts/budget-dispatches.sh",
        },
        "cleanup": cleanup,
        "disk_samples": [{k: v for k, v in s.items() if k != "per_path"} for s in samplers.disk],
        "host_samples": samplers.host,
    }
    out = Path(args.out)
    out.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: summary[k] for k in ("complete", "gate", "peak_temp_disk")} | {"leaked": len(leaked), "leftover_entries": leftovers["entries_total"], "summary": str(out)}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
