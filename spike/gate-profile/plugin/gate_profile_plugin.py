"""Spike-only pytest plugin: per-test durations and xdist groups for one gate tier.

Loaded through ``PYTEST_ADDOPTS=-p gate_profile_plugin`` with this directory on
``PYTHONPATH`` by ``measure.sh``. It records only the *top-level* tier processes Nx
starts in the measured checkout: a pytest whose rootdir is not ``GATE_PROFILE_ROOT``
(a journey's copy) is ignored, and so is a pytest started from inside a recorded one
(``GATE_PROFILE_TOP`` is set by the first controller and inherited by everything it
spawns; a worker records only when its parent is that controller).

Each recording process appends JSON lines to ``$GATE_PROFILE_OUT/<project>.<target>.<pid>.jsonl``.
Stdlib only, so it imports in any interpreter a nested run might use.
"""

from __future__ import annotations

import json
import os
import time

_OUT = os.environ.get("GATE_PROFILE_OUT")
_ROOT = os.environ.get("GATE_PROFILE_ROOT")
_state: dict = {"fh": None, "role": None, "tier": None}


def _write(record: dict) -> None:
    fh = _state["fh"]
    if fh is not None:
        fh.write(json.dumps(record) + "\n")
        fh.flush()


def pytest_configure(config):  # noqa: ANN001
    if not _OUT or not _ROOT:
        return
    try:
        rootdir = os.path.realpath(str(config.rootpath))
    except Exception:  # noqa: BLE001
        return
    if rootdir != os.path.realpath(_ROOT):
        return
    workerinput = getattr(config, "workerinput", None)
    if workerinput is None:
        if os.environ.get("GATE_PROFILE_TOP"):
            return
        os.environ["GATE_PROFILE_TOP"] = str(os.getpid())
        role = "controller"
        worker = None
    else:
        if os.environ.get("GATE_PROFILE_TOP") != str(os.getppid()):
            return
        role = "worker"
        worker = workerinput.get("workerid")
    tier = "{}:{}".format(
        os.environ.get("NX_TASK_TARGET_PROJECT", "?"), os.environ.get("NX_TASK_TARGET_TARGET", "?")
    )
    os.makedirs(_OUT, exist_ok=True)
    path = os.path.join(_OUT, f"{tier.replace(':', '.')}.{os.getpid()}.jsonl")
    _state.update(fh=open(path, "a", encoding="utf-8"), role=role, tier=tier, worker=worker)  # noqa: SIM115
    _write(
        {
            "type": "configure",
            "role": role,
            "tier": tier,
            "worker": worker,
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "args": list(config.invocation_params.args),
            "t": time.time(),
        }
    )


def pytest_collection_finish(session):  # noqa: ANN001
    if _state["fh"] is None:
        return
    groups: dict[str, int] = {}
    for item in session.items:
        marker = item.get_closest_marker("xdist_group")
        name = (marker.args[0] if marker.args else marker.kwargs.get("name")) if marker else None
        groups[str(name)] = groups.get(str(name), 0) + 1
    _write({"type": "collected", "role": _state["role"], "count": len(session.items), "groups": groups, "t": time.time()})


def pytest_runtest_makereport(item, call):  # noqa: ANN001
    # Recorded from the call info rather than a hookwrapper so the plugin stays
    # independent of pluggy's wrapper API across pytest releases.
    if _state["fh"] is None or _state["role"] != "worker":
        return
    marker = item.get_closest_marker("xdist_group")
    group = (marker.args[0] if marker.args else marker.kwargs.get("name")) if marker else None
    _write(
        {
            "type": "phase",
            "nodeid": item.nodeid,
            "when": call.when,
            "start": call.start,
            "stop": call.stop,
            "duration": call.duration,
            "exc": call.excinfo.typename if call.excinfo is not None else None,
            "group": group,
            "worker": _state.get("worker"),
        }
    )


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001
    if _state["fh"] is None:
        return
    _write({"type": "finish", "role": _state["role"], "exitstatus": int(exitstatus), "t": time.time()})
