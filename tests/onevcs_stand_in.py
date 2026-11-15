"""A stand-in `onevcs` on `PATH`, at the binary boundary this suite doubles the published CLI.

`orchestrator/host_adoption_check.py` asks the published `onevcs` two questions — which
identity a repository resolves to (`resolve`) and which release targets it resolves
(`release targets --json`) — and nothing else. The unit tier answers them from fixture JSON
through this executable so the check runs exactly as the engine runs it, its own
subprocesses included; the planning journeys
(`tests/e2e/planning/test_plan_policy_e2e.py`) ask the real verbs over a scratch registry,
which is what holds these answer shapes to the installed release.

llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] The answer shapes restate the
published `onevcs` CLI, which is the double's whole purpose; the planning journey named
above drives the same check against the real `onevcs` and holds them to it.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Mapping
from pathlib import Path


def release_answer(
    identity: str,
    *declared: tuple[str, str],
    default: str | None = None,
    probed: bool = False,
) -> dict[str, object]:
    """One `release targets --json` answer, in the shape the installed `onevcs` writes.

    ``declared`` is each ``(name, artifact)`` target; ``probed`` carries the artifact on each
    target's probe alone rather than in the declaration, which is where the verb carries it
    for a target the host override adds.
    """
    answer: dict[str, object] = {
        "identity": identity,
        "targets": [
            {"name": name, "style": "automated", "probe": {"args": [artifact]}}
            for name, artifact in declared
        ],
    }
    if not probed:
        answer["declaration"] = {
            "state": "declared",
            "declared": {"target": [{"id": artifact, "name": name} for name, artifact in declared]},
        }
    if default is not None:
        answer["default_target"] = default
    return answer


def install(
    directory: Path,
    *,
    identities: Mapping[str, str],
    releases: Mapping[str, object],
) -> tuple[Path, Path]:
    """Write a stand-in `onevcs` into ``directory``; answer its directory and its question log.

    ``identities`` maps a repository to the identity `resolve` answers for it; ``releases``
    maps a repository to what `release targets --json` prints for it — a mapping, or literal
    text for an answer that is not JSON. Anything else exits 2, as the real verb does for a
    repository it does not know. Every invocation is appended to the log, one line apiece.
    """
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "asked.log"
    resolved = {repo: json.dumps({"identity": identity}) for repo, identity in identities.items()}
    targets = {
        repo: answer if isinstance(answer, str) else json.dumps(answer)
        for repo, answer in releases.items()
    }
    program = (
        f"#!{sys.executable}\n"
        "import sys\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"resolved = {resolved!r}\n"
        f"targets = {targets!r}\n"
        "match sys.argv[1:]:\n"
        "    case ['resolve', repo]:\n"
        "        found = resolved.get(repo)\n"
        "    case ['release', 'targets', repo, '--json']:\n"
        "        found = targets.get(repo)\n"
        "    case _:\n"
        "        found = None\n"
        "if found is None:\n"
        "    raise SystemExit(2)\n"
        "sys.stdout.write(found)\n"
    )
    written = directory / "onevcs"
    written.write_text(program, encoding="utf-8")
    written.chmod(written.stat().st_mode | stat.S_IXUSR)
    return directory, log


def first_on_path(directory: Path) -> str:
    """A `PATH` finding ``directory`` before everything this process's own `PATH` holds."""
    return f"{directory}{os.pathsep}{os.environ['PATH']}"
