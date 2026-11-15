"""`orchestrator/host_adoption_check.py`, driven through the engine's check contract.

The engine runs a registered `plan check --check` executable with the loaded plan as JSON on
stdin and reads one object, `{"refusals": [...]}`, from its stdout. So each rule is shown here
the way the engine reaches it: the module run as its own process, a fixture plan on stdin, and
its answer read back off stdout — once refusing a plan that breaks the rule, and once passing a
plan that breaks neither. The remaining cases drive the same `main()` in this process, over
the same stdin and stdout, because the floor `orchestrator:coverage` enforces measures this
process.

Only the published `onevcs` is doubled, at its binary (`tests/onevcs_stand_in.py`): the two
verbs the check asks, answered from fixture JSON. `git` is the real one, reading this
checkout's own `origin`, or a scratch repository's where a case moves the checkout.
"""

from __future__ import annotations

import io
import json
import runpy
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import onevcs_stand_in
import pytest
from conftest import git

from orchestrator import host_adoption_check, host_installs
from orchestrator.record_shapes import hosted_origin
from orchestrator.root import REPO_ROOT

MODULE = REPO_ROOT / "orchestrator" / "host_adoption_check.py"

#: This host's own repository, as `onevcs` files it.
OWN = "github.com/nickderobertis/ai-orchestrator"
#: A repository of the same owner that is not this one: a node elsewhere.
ELSEWHERE = "github.com/nickderobertis/service"
#: The engine's producer, which this host installs its npm package from.
ENGINE = "github.com/nickderobertis/onepipeline"
#: A producer, by the alias a plan names it with and the identity `onevcs` files it under.
LIBRARY, LIBRARY_ID = "library", "github.com/example/library"

#: The artifacts the engine's producer declares: a crate this host does not install, a wheel
#: it does not install either, and the npm package it does.
CRATE = ("crate", "crate:onepipeline")
WHEEL = ("pypi", "pypi:onepipeline-cli")
NPM = ("npm", "npm:onepipeline-cli")

IDENTITIES = {OWN: OWN, ELSEWHERE: ELSEWHERE, ENGINE: ENGINE, LIBRARY: LIBRARY_ID}
RELEASES: dict[str, object] = {
    LIBRARY: onevcs_stand_in.release_answer(LIBRARY_ID, CRATE, WHEEL, NPM),
    ENGINE: onevcs_stand_in.release_answer(ENGINE, CRATE, WHEEL, NPM, default="npm"),
}


def _node(node_id: str, repo: object, **fields: object) -> dict[str, object]:
    return {"id": node_id, "title": f"feat: {node_id}", "task": "Do it.", "repo": repo, **fields}


#: A node of this repository waiting on the producer's crate: the `uninstalled` rule's case.
UNINSTALLED_PLAN = {
    "tasks": [
        _node("producer", LIBRARY),
        _node("consumer", OWN, deps=["producer"], consumes={"producer": "crate"}),
    ]
}

#: A `published` node elsewhere naming no `consumes` behind the engine: `default-taken`'s case.
DEFAULT_TAKEN_PLAN = {
    "tasks": [
        _node("engine", ENGINE),
        _node("elsewhere", ELSEWHERE, deps=["engine"], adoption="published"),
    ]
}

#: Breaks neither rule: this repository's node names the package this host installs, the node
#: elsewhere names the target it consumes, and the human and command nodes are never read.
PASSING_PLAN = {
    "tasks": [
        _node("producer", LIBRARY),
        _node("engine", ENGINE),
        _node("consumer", OWN, deps=["producer"], consumes={"producer": "npm"}),
        _node("second", OWN, deps=["producer"], consumes={"producer": "npm"}),
        _node(
            "elsewhere",
            ELSEWHERE,
            deps=["engine"],
            adoption="published",
            consumes={"engine": "crate"},
        ),
        {"id": "approve", "kind": "human", "task": "Approve it.", "deps": ["producer"]},
        {"id": "script", "kind": "command", "deps": ["producer"], "repo": OWN},
    ]
}


@pytest.fixture
def stand_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The stand-in `onevcs`, first on this process's `PATH`; answers its question log."""
    directory, log = onevcs_stand_in.install(
        tmp_path / "bin", identities=IDENTITIES, releases=RELEASES
    )
    monkeypatch.setenv("PATH", onevcs_stand_in.first_on_path(directory))
    return log


def _own_origin_is_this_repository() -> None:
    """The precondition the own-node cases rest on: this checkout's `origin` is this repository."""
    url = git("remote", "get-url", "origin", cwd=REPO_ROOT).strip()
    assert hosted_origin(url) == OWN, (
        f"this checkout's origin {url!r} is not {OWN}, so no node reads as this host's own"
    )


def _engine_ran(plan: object) -> tuple[int, dict[str, object], str]:
    """The check as the engine runs it: its own process, ``plan`` on stdin, JSON on stdout."""
    ran = subprocess.run(  # noqa: S603 - this checkout's own registered check
        [sys.executable, str(MODULE)],
        input=json.dumps(plan),
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    answer = json.loads(ran.stdout) if ran.returncode == 0 else {}
    return ran.returncode, answer, ran.stderr


def _in_process(
    plan: str | Mapping[str, object] | list[object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, list[dict[str, str]], str]:
    """The same contract through `main()` in this process, which the coverage floor measures."""
    text = plan if isinstance(plan, str) else json.dumps(plan)
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    status = host_adoption_check.main()
    captured = capsys.readouterr()
    refusals = json.loads(captured.out)["refusals"] if status == 0 else []
    return status, refusals, captured.err


def test_the_engine_contract_refuses_a_node_of_this_repository_waiting_on_an_uninstalled_crate(
    stand_in: Path,
) -> None:
    _own_origin_is_this_repository()
    status, answer, stderr = _engine_ran(UNINSTALLED_PLAN)

    assert status == 0, stderr
    refusals = answer["refusals"]
    assert isinstance(refusals, list) and len(refusals) == 1, answer
    (refusal,) = refusals
    assert refusal["node"] == "consumer" and refusal["field"] == "consumes", refusal
    assert refusal["reason"].startswith("uninstalled: "), refusal
    assert "`crate:onepipeline`" in refusal["reason"], refusal
    assert "`npm:onepipeline-cli`" in refusal["reason"], refusal
    assert f"resolve {OWN}" in stand_in.read_text(encoding="utf-8")


def test_the_engine_contract_refuses_a_published_node_elsewhere_taking_this_hosts_default(
    stand_in: Path,
) -> None:
    _own_origin_is_this_repository()
    status, answer, stderr = _engine_ran(DEFAULT_TAKEN_PLAN)

    assert status == 0, stderr
    refusals = answer["refusals"]
    assert isinstance(refusals, list) and len(refusals) == 1, answer
    (refusal,) = refusals
    assert refusal["node"] == "elsewhere" and refusal["field"] == "consumes", refusal
    assert refusal["reason"].startswith("default-taken: "), refusal
    assert f"{ENGINE}'s default target 'npm'" in refusal["reason"], refusal


def test_the_engine_contract_passes_a_plan_breaking_neither_rule(stand_in: Path) -> None:
    _own_origin_is_this_repository()
    status, answer, stderr = _engine_ran(PASSING_PLAN)

    assert status == 0, stderr
    assert answer == {"refusals": []}
    asked = stand_in.read_text(encoding="utf-8").splitlines()
    assert f"release targets {LIBRARY} --json" in asked, asked
    assert f"release targets {ENGINE} --json" in asked, asked


@pytest.mark.parametrize(
    ("plan", "rule"),
    [
        pytest.param(UNINSTALLED_PLAN, "uninstalled: ", id="uninstalled"),
        pytest.param(DEFAULT_TAKEN_PLAN, "default-taken: ", id="default-taken"),
        pytest.param(PASSING_PLAN, None, id="passing"),
    ],
)
def test_main_answers_in_process_as_the_engine_reads_it(
    stand_in: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    plan: Mapping[str, object],
    rule: str | None,
) -> None:
    _own_origin_is_this_repository()
    status, refusals, stderr = _in_process(plan, monkeypatch, capsys)

    assert status == 0, stderr
    assert [refusal["reason"].split(" ", 1)[0] for refusal in refusals] == (
        [rule.strip()] if rule else []
    )


@pytest.mark.parametrize(
    ("stdin", "said"),
    [
        pytest.param("{not json", "the plan on stdin is not JSON", id="not-json"),
        pytest.param("[]", "the plan on stdin is not a JSON object", id="not-an-object"),
    ],
)
def test_a_plan_the_check_cannot_read_exits_two_naming_why(
    stand_in: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stdin: str,
    said: str,
) -> None:
    status, refusals, stderr = _in_process(stdin, monkeypatch, capsys)

    assert status == 2
    assert refusals == []
    assert said in stderr, stderr


def test_a_host_with_neither_git_nor_onevcs_decides_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no `origin` to read and no `onevcs` to ask, no node is this host's or anyone's."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    status, refusals, stderr = _in_process(UNINSTALLED_PLAN, monkeypatch, capsys)

    assert status == 0, stderr
    assert refusals == []


def test_an_ssh_origin_names_this_repository_too(
    tmp_path: Path,
    stand_in: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A checkout cloned over ssh is still this repository: its scp-like remote is lifted."""
    checkout = tmp_path / "checkout"
    git("init", "-q", str(checkout))
    git("remote", "add", "origin", f"git@github.com:{OWN.split('/', 1)[1]}.git", cwd=checkout)
    monkeypatch.setattr(host_adoption_check, "ROOT", checkout)

    status, refusals, stderr = _in_process(UNINSTALLED_PLAN, monkeypatch, capsys)

    assert status == 0, stderr
    assert [refusal["node"] for refusal in refusals] == ["consumer"], refusals


def _refusals_with(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    plan: Mapping[str, object],
    *,
    identities: Mapping[str, str] = IDENTITIES,
    releases: Mapping[str, object] = RELEASES,
) -> list[dict[str, str]]:
    """``plan``'s refusals with `onevcs` answering ``identities`` and ``releases`` instead."""
    directory, _ = onevcs_stand_in.install(
        tmp_path / "answers", identities=identities, releases=releases
    )
    monkeypatch.setenv("PATH", onevcs_stand_in.first_on_path(directory))
    status, refusals, stderr = _in_process(plan, monkeypatch, capsys)
    assert status == 0, stderr
    return refusals


def _own_consumer(**fields: object) -> dict[str, object]:
    return {
        "tasks": [_node("producer", LIBRARY), _node("consumer", OWN, deps=["producer"], **fields)]
    }


@pytest.mark.parametrize(
    "release",
    [
        pytest.param("not json at all", id="answer-not-json"),
        pytest.param("[1, 2]", id="answer-not-an-object"),
        pytest.param(onevcs_stand_in.release_answer(LIBRARY_ID), id="releases-nothing"),
        pytest.param(onevcs_stand_in.release_answer(OWN, CRATE), id="producer-is-this-repository"),
    ],
)
def test_a_producer_releasing_nothing_into_another_repository_is_never_waited_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    release: object,
) -> None:
    _own_origin_is_this_repository()
    refusals = _refusals_with(
        tmp_path,
        monkeypatch,
        capsys,
        _own_consumer(consumes={"producer": "crate"}),
        releases={LIBRARY: release},
    )

    assert refusals == []


@pytest.mark.parametrize(
    "plan",
    [
        pytest.param({"tasks": [_node("consumer", OWN, deps=["absent"])]}, id="dependency-outside"),
        pytest.param(
            {"tasks": [_node("producer", None), _node("consumer", OWN, deps=["producer"])]},
            id="producer-without-repo",
        ),
        pytest.param(
            {"tasks": ["not a node", _node("consumer", 7, deps=[])]}, id="malformed-entries"
        ),
        pytest.param(
            {
                "tasks": [
                    _node("producer", LIBRARY),
                    _node(
                        "consumer",
                        "unregistered",
                        deps=["producer"],
                        consumes={"producer": "crate"},
                    ),
                ]
            },
            id="repository-onevcs-cannot-resolve",
        ),
        pytest.param(_own_consumer(consumes={"producer": "npm"}), id="installed-package"),
        pytest.param(_own_consumer(consumes={"producer": "wasm"}), id="undeclared-target"),
        pytest.param(_own_consumer(), id="no-target-and-no-default"),
        pytest.param({}, id="no-tasks"),
    ],
)
def test_nothing_the_check_cannot_answer_for_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    plan: Mapping[str, object],
) -> None:
    _own_origin_is_this_repository()
    assert _refusals_with(tmp_path, monkeypatch, capsys, plan) == []


def test_an_identity_onevcs_answers_without_a_name_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A `resolve` answer whose `identity` is empty resolves nothing, so nothing is refused."""
    directory, _ = onevcs_stand_in.install(
        tmp_path / "bin", identities={LIBRARY: "", OWN: ""}, releases=RELEASES
    )
    monkeypatch.setenv("PATH", onevcs_stand_in.first_on_path(directory))

    status, refusals, stderr = _in_process(UNINSTALLED_PLAN, monkeypatch, capsys)

    assert status == 0, stderr
    assert refusals == []


def test_a_target_carried_only_on_its_probe_is_still_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The host override adds targets with no declaration; the artifact is the probe's argument.

    The answer also carries entries the check passes over — one that is no object, another
    target's name, and a probe with no arguments — so only the named target's probe decides.
    """
    _own_origin_is_this_repository()
    probed = onevcs_stand_in.release_answer(LIBRARY_ID, CRATE, probed=True)
    targets = probed["targets"]
    assert isinstance(targets, list)
    probed["targets"] = ["not a target", {"name": "other"}, *targets]
    bare = onevcs_stand_in.release_answer(LIBRARY_ID, probed=True)
    bare["targets"] = [{"name": "crate", "probe": {"args": []}}]

    refused = _refusals_with(
        tmp_path,
        monkeypatch,
        capsys,
        _own_consumer(consumes={"producer": "crate"}),
        releases={LIBRARY: probed},
    )
    unread = _refusals_with(
        tmp_path / "unread",
        monkeypatch,
        capsys,
        _own_consumer(consumes={"producer": "crate"}),
        releases={LIBRARY: bare},
    )

    assert [refusal["node"] for refusal in refused] == ["consumer"], refused
    assert "`crate:onepipeline`" in refused[0]["reason"], refused
    assert unread == []


def test_a_declared_target_naming_no_artifact_leaves_the_probes_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A declaration entry with no `id` is not a declaration of that target's artifact.

    The probe still names the crate, so the node waiting on it is refused for the crate,
    rather than the malformed entry erasing what the probe said.
    """
    _own_origin_is_this_repository()
    answer = onevcs_stand_in.release_answer(LIBRARY_ID, CRATE, probed=True)
    answer["declaration"] = {"state": "declared", "declared": {"target": [{"name": "crate"}]}}

    refused = _refusals_with(
        tmp_path,
        monkeypatch,
        capsys,
        _own_consumer(consumes={"producer": "crate"}),
        releases={LIBRARY: answer},
    )

    assert [refusal["node"] for refusal in refused] == ["consumer"], refused
    assert "`crate:onepipeline`" in refused[0]["reason"], refused


def test_a_node_of_this_repository_naming_no_target_waits_on_the_producers_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _own_origin_is_this_repository()
    crate_default = onevcs_stand_in.release_answer(LIBRARY_ID, CRATE, NPM, default="crate")
    npm_default = onevcs_stand_in.release_answer(LIBRARY_ID, CRATE, NPM, default="npm")

    refused = _refusals_with(
        tmp_path, monkeypatch, capsys, _own_consumer(), releases={LIBRARY: crate_default}
    )
    accepted = _refusals_with(
        tmp_path / "npm", monkeypatch, capsys, _own_consumer(), releases={LIBRARY: npm_default}
    )

    assert [refusal["reason"][:13] for refusal in refused] == ["uninstalled: "], refused
    assert accepted == []


@pytest.mark.parametrize(
    ("producer", "adoption", "consumes", "refused"),
    [
        # A producer awaiting its first adoption is refused like an installed one, while the
        # host has one: once each has been adopted the set is empty and the case has nothing
        # to read.
        *(
            pytest.param(producer, "published", None, True, id="awaiting-first-adoption")
            for producer in sorted(host_installs.AWAITING_FIRST_ADOPTION)[:1]
        ),
        pytest.param(
            LIBRARY_ID, "published", None, False, id="producer-this-host-installs-nothing-of"
        ),
        pytest.param(ENGINE, "fast", None, False, id="fast"),
        pytest.param(ENGINE, "published", {"producer": "crate"}, False, id="target-named"),
    ],
)
def test_default_taken_reads_only_a_published_node_behind_this_hosts_producers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    producer: str,
    adoption: str,
    consumes: dict[str, str] | None,
    refused: bool,
) -> None:
    _own_origin_is_this_repository()
    node = _node("elsewhere", ELSEWHERE, deps=["producer"], adoption=adoption)
    if consumes is not None:
        node["consumes"] = consumes
    plan = {"tasks": [_node("producer", LIBRARY), node]}
    releases = {LIBRARY: onevcs_stand_in.release_answer(producer, CRATE, NPM, default="npm")}

    refusals = _refusals_with(tmp_path, monkeypatch, capsys, plan, releases=releases)

    assert [refusal["reason"][:15] for refusal in refusals] == (
        ["default-taken: "] if refused else []
    )


def test_run_as_a_script_the_check_finds_this_checkouts_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The engine runs the file by path, from anywhere, so it puts this checkout on `sys.path`."""
    root = str(host_adoption_check.ROOT)
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry != root])

    loaded = runpy.run_path(str(MODULE), run_name="host_adoption_check_as_run")

    assert sys.path[0] == root
    assert loaded["host_installs"] is host_installs
