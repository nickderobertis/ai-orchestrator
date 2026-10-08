"""Which spike branches `orchestrator/spike_branches.py` hands the success hook to discard.

`tests/run_end_hooks/test_spike_branch_discard_e2e.py` drives the hook through real launches
and reads what it left on a real origin. This holds the readings behind it over real
stores: a real Git origin listed through the pinned `onevcs`'s own `resolve` of a checkout
registered in a scratch registry, a run root holding the launch record and plan the engine
writes, and the board-launched case, where the plan is traced back to the `authoring`
project whose copies name the launched one — which no launch here can reach, since a
journey never copies onto the board, read here off a real `authoring` store.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] These are the coverage tier's
measure of an `orchestrator/` module, whose every line the 100% floor `pyproject.toml` sets
must be covered by `orchestrator:test`; the real `git` and pinned `onevcs` they run are local,
seconds-long, and the boundary each answer is read from, so a project of their own would take
these lines out of the measurement the floor is held to.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import plan_root_variable
import pytest
from conftest import git

from orchestrator import plan_store, spike_branches
from orchestrator.plan_store import QualifiedProjectId
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

ONEVCS = REPO_ROOT / ".venv" / "bin" / "onevcs"
COMMITTER = ("-c", "user.email=test@example.com", "-c", "user.name=ai-orchestrator-test")


def _origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *branches: str) -> Path:
    """A registered checkout whose real origin holds ``branches`` beside `main`."""
    monkeypatch.setenv("ONEVCS_HOME", str(tmp_path / "onevcs"))
    bare, checkout = tmp_path / "origin.git", tmp_path / "checkout"
    git("init", "-q", "--bare", "-b", "main", str(bare))
    git("init", "-q", "-b", "main", str(checkout))
    (checkout / "README.md").write_text("seed\n", encoding="utf-8")
    git("add", "-A", cwd=checkout)
    git(*COMMITTER, "commit", "-qm", "chore: seed", cwd=checkout)
    git("remote", "add", "origin", str(bare), cwd=checkout)
    git("push", "-q", "origin", "main", cwd=checkout)
    for branch in branches:
        git("push", "-q", "origin", f"main:refs/heads/{branch}", cwd=checkout)
    registered = subprocess.run(
        [str(ONEVCS), "register", str(checkout)], capture_output=True, text=True, check=False
    )
    assert registered.returncode == 0, registered.stderr
    return checkout


def _run_root(tmp_path: Path, project: object, tasks: object) -> Path:
    root = tmp_path / "runs" / "the-run"
    root.mkdir(parents=True)
    (root / spike_branches.LAUNCH_RECORD).write_text(
        json.dumps({"run_id": "the-run", "project": project}), encoding="utf-8"
    )
    (root / spike_branches.PLAN_RECORD).write_text(
        json.dumps({"name": "the-run", "tasks": tasks}), encoding="utf-8"
    )
    return root


def _drafted(native: str, copies: dict[str, str] | None) -> None:
    """An `authoring` project as the plan store keeps it, recording the copies it made."""
    write_plan_project(
        Path(os.environ[plan_root_variable.name()]),
        {"schema_version": 3, "goal": {"text": "Page it"}, "name": native, "tasks": []},
        native_id=native,
        project_metadata=None if copies is None else {plan_store.COPIES_KEY: copies},
    )


def test_a_plan_launched_from_authoring_is_named_by_its_own_native_id() -> None:
    assert (
        spike_branches.plan_native_id(QualifiedProjectId("authoring:cursor-plan")) == "cursor-plan"
    )


def test_a_board_launched_plan_is_named_by_the_authoring_project_whose_copies_name_it() -> None:
    unique = str(os.getpid())
    _drafted(f"unrelated-{unique}", None)
    _drafted(f"other-plan-{unique}", {"plans": f"plans:ANOTHER-{unique}"})
    _drafted(f"cursor-plan-{unique}", {"plans": f"plans:I_kw{unique}"})

    assert (
        spike_branches.plan_native_id(QualifiedProjectId(f"plans:I_kw{unique}"))
        == f"cursor-plan-{unique}"
    )
    assert spike_branches.plan_native_id(QualifiedProjectId("plans:NEVER-COPIED")) == "NEVER-COPIED"


def test_an_authoring_source_that_cannot_be_read_names_the_launched_projects_own_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    not_a_root = tmp_path / "not-a-directory"
    not_a_root.write_text("", encoding="utf-8")
    monkeypatch.setenv(plan_root_variable.name(), str(not_a_root))

    assert spike_branches.plan_native_id(QualifiedProjectId("plans:I_kw123")) == "I_kw123"


def test_the_repositories_are_every_one_the_recorded_plan_names_once(tmp_path: Path) -> None:
    root = _run_root(
        tmp_path, "authoring:p", [{"repo": "one"}, {"repo": "two"}, {"repo": "one"}, {"id": "x"}]
    )

    assert spike_branches.repositories(root) == ["one", "two"]


@pytest.mark.parametrize(
    "tasks",
    [None, [{"repo": "one"}, "not a node"], [{"repo": ""}], [{"repo": 7}], [{"repo": "a\tb"}]],
    ids=[
        "no-list",
        "a-node-not-an-object",
        "a-blank-repository",
        "a-repository-not-a-name",
        "a-repository-splitting-the-record",
    ],
)
def test_a_recorded_plan_this_cannot_read_node_by_node_is_refused(
    tmp_path: Path, tasks: object
) -> None:
    """Read as naming no repository, it would keep every spike while saying nothing."""
    root = _run_root(tmp_path, "authoring:p", tasks)

    with pytest.raises(OSError, match=r"records (no list of nodes|a node whose repository)"):
        spike_branches.repositories(root)


@pytest.mark.parametrize("native", ["cursor-*", "cursor?", "[cursor]", "a/../b", "-cursor"])
def test_a_plan_id_a_branch_pattern_would_read_is_refused(native: str) -> None:
    """The id becomes part of the pattern every discarded branch is matched by."""
    with pytest.raises(OSError, match="holds a character a branch pattern reads"):
        spike_branches.plan_native_id(QualifiedProjectId(f"authoring:{native}"))


@pytest.mark.parametrize("project", [None, "no-colon", 7])
def test_a_launch_record_naming_no_qualified_project_is_refused(
    tmp_path: Path, project: object
) -> None:
    root = _run_root(tmp_path, project, [])

    with pytest.raises(OSError, match="names no qualified project"):
        spike_branches.launched_project(root)


def test_an_unreadable_record_is_refused_naming_it(tmp_path: Path) -> None:
    with pytest.raises(OSError, match="could not be read"):
        spike_branches.launched_project(tmp_path)


def test_the_origin_lists_exactly_the_plans_spike_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = _origin(
        tmp_path,
        monkeypatch,
        "nick/cursor-plan/spike-a",
        "nick/cursor-plan/spike-b",
        "nick/cursor-plan/build-listing",
        "nick/cursor-plan-later/spike-a",
    )

    listed = spike_branches.spike_branches(str(checkout), spike_branches.PlanId("cursor-plan"))

    assert listed == ["nick/cursor-plan/spike-a", "nick/cursor-plan/spike-b"]


def test_a_repository_onevcs_cannot_resolve_is_unlisted_naming_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONEVCS_HOME", str(tmp_path / "onevcs"))

    with pytest.raises(spike_branches.Unlisted, match=r"`.*onevcs resolve nowhere` exited \d+"):
        spike_branches.spike_branches("nowhere", spike_branches.PlanId("cursor-plan"))


@pytest.mark.parametrize(
    ("answered", "said"),
    [
        ("{}", "named no publication checkout"),
        ('{"publication_checkout": 7}', "named no publication checkout"),
        ("[]", "did not answer with its identity"),
        ("not json", "did not answer with its identity"),
        ('{"publication_checkout": "/registered"}', "named no identity"),
    ],
    ids=["none", "not-a-path", "not-an-object", "not-json", "no-identity"],
)
def test_a_resolve_answer_naming_no_checkout_is_unlisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answered: str, said: str
) -> None:
    answering = tmp_path / "onevcs"
    answering.write_text(f"#!/bin/sh\necho '{answered}'\n", encoding="utf-8")
    answering.chmod(0o755)
    monkeypatch.setattr(spike_branches, "installed_onevcs", lambda: str(answering))

    with pytest.raises(spike_branches.Unlisted, match=said):
        spike_branches.spike_branches("somewhere", spike_branches.PlanId("cursor-plan"))


def test_a_command_that_cannot_start_is_unlisted(tmp_path: Path) -> None:
    with pytest.raises(spike_branches.Unlisted, match="did not run"):
        spike_branches.answer([str(tmp_path / "missing")])


def test_a_command_that_outlives_its_bound_is_unlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(spike_branches, "TIMEOUT_SECONDS", 0.2)
    with pytest.raises(spike_branches.Unlisted, match=r"`sleep 5` did not run: .*timed out"):
        spike_branches.answer(["sleep", "5"])


def test_the_installed_onevcs_is_the_one_asked() -> None:
    assert spike_branches.installed_onevcs() == str(ONEVCS)


def test_a_checkout_with_no_installed_onevcs_asks_the_one_on_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_path = tmp_path / "bin" / "onevcs"
    on_path.parent.mkdir()
    on_path.write_text("#!/bin/sh\n", encoding="utf-8")
    on_path.chmod(0o755)
    monkeypatch.setattr(spike_branches, "REPO_ROOT", tmp_path / "bare-checkout")
    monkeypatch.setenv("PATH", str(on_path.parent))
    assert spike_branches.installed_onevcs() == str(on_path)

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert spike_branches.installed_onevcs() == "onevcs"


def test_main_answers_a_line_per_branch_and_per_repository_it_could_not_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout = _origin(tmp_path, monkeypatch, "nick/cursor-plan/spike-a")
    root = _run_root(
        tmp_path, "authoring:cursor-plan", [{"repo": str(checkout)}, {"repo": "nowhere"}]
    )

    assert spike_branches.main([str(root)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"branch\t{checkout}\tnick/cursor-plan/spike-a"
    assert lines[1].startswith("unlisted\tnowhere\t`"), lines


def test_main_folds_a_reason_onto_one_record_with_no_tab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reason carries another tool's text, and a break in it must not read as a record."""
    on_path = tmp_path / "bin" / "onevcs"
    on_path.parent.mkdir()
    on_path.write_text(
        "#!/bin/sh\nprintf 'resolve failed\\tbranch\\tx\\tnick/p/spike-a\\n' >&2\nexit 1\n",
        encoding="utf-8",
    )
    on_path.chmod(0o755)
    monkeypatch.setattr(spike_branches, "REPO_ROOT", tmp_path / "bare-checkout")
    monkeypatch.setenv("PATH", f"{on_path.parent}{os.pathsep}{os.environ['PATH']}")
    root = _run_root(tmp_path, "authoring:cursor-plan", [{"repo": "somewhere"}])

    assert spike_branches.main([str(root)]) == 0

    (line,) = capsys.readouterr().out.splitlines()
    kind, repository, why = line.split("\t")
    assert (kind, repository) == ("unlisted", "somewhere")
    assert why.endswith("exited 1: resolve failed branch x nick/p/spike-a"), why


def test_main_refuses_a_run_root_it_cannot_read_and_a_wrong_argument_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert spike_branches.main([str(tmp_path / "no-run")]) == 2
    assert "spike-branches: " in capsys.readouterr().err
    assert spike_branches.main([]) == 2
    assert "usage:" in capsys.readouterr().err


SHA = "a" * 40


@pytest.mark.parametrize(
    ("line", "branch"),
    [
        (f"{SHA}\trefs/heads/nick/cursor-plan/spike-a", "nick/cursor-plan/spike-a"),
        (f"{SHA}\trefs/heads/cursor-plan/spike-a", "cursor-plan/spike-a"),
    ],
    ids=["prefixed", "unprefixed"],
)
def test_a_listed_line_naming_one_of_the_plans_spikes_is_its_branch(line: str, branch: str) -> None:
    assert spike_branches._spike_branch(line, spike_branches.PlanId("cursor-plan")) == branch


@pytest.mark.parametrize(
    ("line", "said"),
    [
        ("not a ref line", "names no branch"),
        (f"{SHA}\trefs/tags/cursor-plan/spike-a", "names no branch"),
        (f"{SHA}\trefs/heads/nick/cursor-plan/spike-a/extra", "is not a spike branch of"),
        (f"{SHA}\trefs/heads/nick/other-cursor-plan/spike-a", "is not a spike branch of"),
    ],
    ids=["not-a-ref", "a-tag", "a-deeper-branch", "another-plan"],
)
def test_a_listed_line_that_is_no_spike_of_the_plan_stops_the_listing(line: str, said: str) -> None:
    """What this answers is deleted, so a line it cannot vouch for is refused, never passed."""
    with pytest.raises(spike_branches.Unlisted, match=said):
        spike_branches._spike_branch(line, spike_branches.PlanId("cursor-plan"))


# llmlint: ignore-block[shell_test_tiers_stay_split] This task requires this module’s
# coverage at the real local Git/onevcs boundary, with seconds-long isolated registries;
# the plan recipe journey stays in the separate plan-tooling tier.
def test_resume_listing_includes_all_registered_checkouts_of_only_its_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _origin(tmp_path, monkeypatch, "host/cursor-plan/spike-a")
    sibling = tmp_path / "sibling"
    git("clone", "-q", str(tmp_path / "origin.git"), str(sibling))
    subprocess.run([str(ONEVCS), "register", str(sibling)], check=True, capture_output=True)
    git("branch", "different/host/cursor-plan/spike-b", cwd=sibling)
    git("branch", "different/host/another-plan/spike-c", cwd=sibling)
    listed = spike_branches.spike_branches(
        str(checkout), spike_branches.PlanId("cursor-plan"), local=True
    )
    assert set(listed) == {"host/cursor-plan/spike-a", "different/host/cursor-plan/spike-b"}
    git("switch", "-q", "different/host/cursor-plan/spike-b", cwd=sibling)
    (sibling / "harness").write_text("measure", encoding="utf-8")
    git("add", "-A", cwd=sibling)
    git(*COMMITTER, "commit", "-qm", "feat: harness", cwd=sibling)
    git("branch", "host/cursor-plan/spike-a", cwd=sibling)
    with pytest.raises(spike_branches.Unlisted, match="conflicting branch"):
        spike_branches.spike_branches(
            str(checkout), spike_branches.PlanId("cursor-plan"), local=True
        )


@pytest.mark.parametrize(
    ("verb", "output", "said"),
    [
        ("ls-remote", "not a ref", "names no branch"),
        ("ls-remote", "a" * 40 + "\trefs/heads/host/cursor-plan/spike-a..b", "unsafe branch"),
        (
            "ls-remote",
            ("a" * 40 + "\trefs/heads/host/cursor-plan/spike-a\n") * 2,
            "duplicate branch",
        ),
        ("for-each-ref", "not a ref", "unreadable ref"),
    ],
)
def test_resume_refuses_malformed_duplicate_and_unsafe_git_answers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    output: str,
    said: str,
) -> None:
    checkout = _origin(tmp_path, monkeypatch)
    real_git = shutil.which("git")
    assert real_git is not None
    binary = tmp_path / "bin" / "git"
    binary.parent.mkdir()
    binary.write_text(
        f'#!/bin/sh\nif [ "$1" = {shlex.quote(verb)} ]; then\n'
        f"printf '%s\\n' {shlex.quote(output)}\nelse\n"
        f'exec {shlex.quote(real_git)} "$@"\nfi\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ["PATH"])
    with pytest.raises(spike_branches.Unlisted, match=said):
        spike_branches.spike_branches(
            str(checkout), spike_branches.PlanId("cursor-plan"), local=True
        )


# llmlint: ignore-end[shell_test_tiers_stay_split]


@pytest.mark.parametrize(
    ("identity", "listing", "said"),
    [
        (None, "", "named no identity"),
        ("repo", "  checkout\t/path", "unreadable checkout"),
        ("repo", "repo\tgate\n  broken", "unreadable checkout"),
        ("repo", "broken", "unreadable identity"),
        ("repo", "other\tgate\n  checkout\t/path", "no registered checkouts"),
    ],
)
def test_resume_refuses_unreadable_registered_checkout_answers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity: str | None,
    listing: str,
    said: str,
) -> None:
    answering = tmp_path / "onevcs"
    answering.write_text(f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(listing)}\n", encoding="utf-8")
    answering.chmod(0o755)
    monkeypatch.setattr(spike_branches, "installed_onevcs", lambda: str(answering))
    with pytest.raises(spike_branches.Unlisted, match=said):
        spike_branches.registered_checkouts(identity)
