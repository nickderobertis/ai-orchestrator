"""A stand-in `onevcs` for the structural guards, at the boundary this suite doubles it.

`orchestrator/adoption_guard.py` and `orchestrator/publication_guard.py` ask the published
`onevcs` which targets a repository resolves, which identity and origin an alias names,
and which publication policy its rules answer. Every tier that reads those guards — the
plan tier and the live-edit check alike — is tested against the same double, so the two
cannot be proven against two different ideas of what `onevcs` says.

llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] The answer shapes and verbs
here restate the published `onevcs` CLI, which is the double's whole purpose; they are
held to the installed release by the journeys that ask the real verb about real scratch
identities — `tests/plan_tooling/test_check_plan_recipe_e2e.py` for `release targets`,
`resolve` and `rules check`,
`tests/ask_seam/structural_reply/test_structural_reply_e2e.py` for a live run —
and by the vocabulary tests in `tests/test_adoption_guard.py` and
`tests/test_publication_guard.py` that read the installed `onevcs`.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest


def release_answer(
    identity: str,
    *declared: tuple[str, str],
    adoption: str | None = None,
    default: str | None = None,
    probed: bool = False,
) -> dict[str, object]:
    """One `release targets --json` answer, in the shape the installed `onevcs` writes.

    ``probed`` writes the artifact id on each target's probe alone rather than in the
    declaration, which is where the verb carries it for a target the host override adds.
    """
    answer: dict[str, object] = {
        "identity": identity,
        "targets": [
            {"name": name, "style": "automated", "probe": {"args": [artifact]}}
            for name, artifact in declared
        ],
        "sources": {},
    }
    if not probed:
        answer["declaration"] = {
            "state": "declared",
            "declared": {"target": [{"id": artifact, "name": name} for name, artifact in declared]},
        }
    if adoption is not None:
        answer["adoption"] = adoption
    if default is not None:
        answer["default_target"] = default
    return answer


def stand_in(root: Path, monkeypatch: pytest.MonkeyPatch, program: str) -> None:
    """Put one Python program on PATH under the name `onevcs`, first."""
    binary = root / "bin"
    binary.mkdir(parents=True, exist_ok=True)
    written = binary / "onevcs"
    written.write_text(f"#!{sys.executable}\n{program}", encoding="utf-8")
    written.chmod(written.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")


def install_three_verb_stand_in(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    releases: dict[str, object],
    resolved: dict[str, tuple[str, str]] | None = None,
) -> Path:
    """Install a stand-in `onevcs` answering the three verbs the guards ask; return its log.

    ``releases`` maps a repository to what `release targets --json` prints for it — a
    dict, or the literal text when a test needs something that is not JSON; ``resolved``
    maps a repository to the origin `resolve` answers and the policy `rules check`
    answers. Anything else exits 2, as the real one does for an unregistered repository.
    The path answered is the log every invocation is appended to, for the test that
    counts them.
    """
    log = root / "asked.log"
    answers = {
        repo: answer if isinstance(answer, str) else json.dumps(answer)
        for repo, answer in releases.items()
    }
    policies = {
        repo: (
            json.dumps(
                {"identity": f"id/{repo}", "publication_checkout": str(root), "origin": origin}
            ),
            f"repo: {repo}\nidentity: id/{repo}\npublication: {workflow} (from rule 1)\n",
        )
        for repo, (origin, workflow) in (resolved or {}).items()
    }
    stand_in(
        root,
        monkeypatch,
        "import sys\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"releases = {answers!r}\n"
        f"policies = {policies!r}\n"
        "match sys.argv[1:]:\n"
        "    case ['release', 'targets', repo, '--json']:\n"
        "        found = releases.get(repo)\n"
        "    case ['resolve', repo]:\n"
        "        found = policies.get(repo, (None, None))[0]\n"
        "    case ['rules', 'check', repo]:\n"
        "        found = policies.get(repo, (None, None))[1]\n"
        "    case _:\n"
        "        found = None\n"
        "if found is None:\n"
        "    raise SystemExit(2)\n"
        "sys.stdout.write(found)\n",
    )
    return log
