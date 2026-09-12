"""A plan whose release adoption could never complete on this host is refused before dispatch.

The reads no journey can reach: malformed fields, an `onevcs` that answers nothing, an
answer missing a key, a checkout with no origin of its own. What is doubled is `onevcs`,
at the boundary this repository doubles every published CLI a recipe delegates to — it is
what says which targets a repository resolves, which adoption its rungs answer, and
which identity an alias names — and the checkout this host's own origin is read off is a
real one made here, so a test about "this host's own repository" is not a test about
whichever checkout the suite happens to run in.
`tests/plan_tooling/test_check_plan_recipe_e2e.py` drives the real verb through the real
`just check-plan`, against scratch producers that really declare targets.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from scratch_identity import registered

from orchestrator import adoption_guard
from orchestrator.adoption_guard import (
    JUDGED_TIER,
    Adoption,
    AdoptionError,
    Host,
    Node,
    Target,
    Targets,
    check_plan,
    nodes,
    refusals,
    releasing,
    resolved_adoption,
    targets,
    this_hosts_origin,
)
from orchestrator.plan_store import NodeId
from orchestrator.publication_guard import Workflow
from orchestrator.root import REPO_ROOT

#: The wheel this host installs from the engine's producer, as `host_installs` names it,
#: and a crate the same producer declares that nothing here installs.
WHEEL = ("pypi", "pypi:onepipeline-cli")
CRATE = ("crate", "crate:onepipeline")

#: The origin a made-here checkout stands in as this host's own repository under.
OWN = "github.com/acme/harness"


def _answer(
    identity: str,
    *declared: tuple[str, str],
    adoption: str | None = None,
    default: str | None = None,
    probed: bool = False,
) -> dict[str, Any]:
    """One `release targets --json` answer, in the shape the installed `onevcs` writes.

    ``probed`` writes the artifact id on each target's probe alone rather than in the
    declaration, which is where the verb carries it for a target the host override adds.
    """
    answer: dict[str, Any] = {
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


def _stand_in(root: Path, monkeypatch: pytest.MonkeyPatch, program: str) -> None:
    """Put one Python program on PATH under the name `onevcs`, first."""
    binary = root / "bin"
    binary.mkdir(parents=True, exist_ok=True)
    written = binary / "onevcs"
    written.write_text(f"#!{sys.executable}\n{program}", encoding="utf-8")
    written.chmod(written.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")


def _onevcs(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    releases: dict[str, Any],
    resolved: dict[str, tuple[str, str]] | None = None,
) -> Path:
    """A stand-in `onevcs` answering the three verbs this module asks, for named repos.

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
    _stand_in(
        root,
        monkeypatch,
        "import sys\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"releases = {answers!r}\n"
        f"policies = {policies!r}\n"
        "verb = sys.argv[1:]\n"
        "if verb[:2] == ['release', 'targets'] and verb[3:] == ['--json']:\n"
        "    found = releases.get(verb[2])\n"
        "elif verb[:1] == ['resolve'] and len(verb) == 2:\n"
        "    found = policies.get(verb[1], (None, None))[0]\n"
        "elif verb[:2] == ['rules', 'check'] and len(verb) == 3:\n"
        "    found = policies.get(verb[2], (None, None))[1]\n"
        "else:\n"
        "    found = None\n"
        "if found is None:\n"
        "    raise SystemExit(2)\n"
        "sys.stdout.write(found)\n",
    )
    return log


def _own_checkout(
    root: Path, monkeypatch: pytest.MonkeyPatch, origin: str | None = OWN, *, remote: str = "scp"
) -> Path:
    """A real checkout this module reads its own origin off, in place of the suite's.

    A remote of its own so a test about this host's repository is about a repository
    the test made, in whichever of the three clone-URL forms ``remote`` names; ``None``
    for a checkout that has no `origin` at all.
    """
    checkout = root / "own"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(checkout)], check=True)
    if origin is not None:
        host, _, path = origin.partition("/")
        url = {
            "scp": f"git@{host}:{path}.git",
            "ssh": f"ssh://git@{host}/{path}.git",
            "https": f"https://{host}/{path}.git",
        }[remote]
        subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin", url], check=True)
    monkeypatch.setattr(adoption_guard, "REPO_ROOT", checkout)
    return checkout


def _plan(*tasks: dict[str, Any]) -> dict[str, Any]:
    """A plan in the shape the engine's loader hands a check, read here as open JSON."""
    return {"tasks": list(tasks)}


def _producer(repo: str = "library") -> dict[str, Any]:
    return {"id": "producer", "title": "feat: release it", "repo": repo}


def _consumer(**fields: Any) -> dict[str, Any]:
    return {
        "id": "consumer",
        "title": "feat: adopt it",
        "repo": "service",
        "deps": ["producer"],
        **fields,
    }


def _fields(found: list[adoption_guard.Refusal]) -> list[tuple[str, str]]:
    return [(str(one.node), one.field) for one in found]


def test_a_consumed_target_the_producer_does_not_resolve_is_refused_listing_what_it_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wait on a target the producer does not release is one no probe can answer."""
    _onevcs(tmp_path, monkeypatch, releases={"library": _answer("id/library", WHEEL, CRATE)})
    plan = _plan(
        _producer(),
        _consumer(consumes={"producer": "npm"}, merge_policy=Workflow.CHANGE_OPEN.value),
    )

    (refused,) = refusals(plan)

    assert (refused.node, refused.field) == ("consumer", "consumes")
    assert "'npm'" in refused.reason
    assert "resolves: pypi, crate" in refused.reason
    assert "`consumes: {producer: <target>}`" in refused.reason


def test_a_consumed_target_on_a_producer_resolving_none_is_refused_whether_or_not_it_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A producer with no declaration resolves nothing, and the entry names something."""
    _onevcs(tmp_path, monkeypatch, releases={"library": _answer("id/library")})
    plan = _plan(_producer(), _consumer(consumes={"producer": "pypi"}))

    (refused,) = refusals(plan)

    assert refused.field == "consumes"
    assert "resolves: none" in refused.reason


def test_a_consumed_target_the_producer_resolves_is_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _onevcs(tmp_path, monkeypatch, releases={"library": _answer("id/library", WHEEL)})
    plan = _plan(_producer(), _consumer(consumes={"producer": "pypi"}))

    assert refusals(plan) == []


@pytest.mark.parametrize(
    ("consumes", "producer"),
    (
        pytest.param({"producer": 7}, _producer(), id="a value that is not a target name"),
        pytest.param({"absent": "pypi"}, _producer(), id="a dependency the plan has no node for"),
        pytest.param({"producer": "pypi"}, {"id": "producer"}, id="a dependency naming no repo"),
        pytest.param(
            {"producer": "pypi"}, _producer("elsewhere"), id="a repo onevcs cannot answer"
        ),
    ),
)
def test_a_consumes_entry_this_cannot_read_against_a_producer_is_passed_over(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    consumes: dict[str, Any],
    producer: dict[str, Any],
) -> None:
    """The engine has already refused what it refuses about `consumes`; the rest is silence."""
    _onevcs(tmp_path, monkeypatch, releases={"library": _answer("id/library", WHEEL)})

    assert refusals(_plan(producer, _consumer(consumes=consumes))) == []


def test_a_published_node_behind_a_release_with_no_target_is_refused_as_held_for_ever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not-answered never releases a hold, so the refusal names both remedies."""
    _onevcs(tmp_path, monkeypatch, releases={"library": _answer("id/library", WHEEL)})
    plan = _plan(_producer(), _consumer(adoption="published"))

    (refused,) = refusals(plan)

    assert (refused.node, refused.field) == ("consumer", "adoption")
    assert "held for ever" in refused.reason
    assert "`consumes: {producer: <target>}`" in refused.reason
    assert "`default_target` in config/onevcs.releases.yml" in refused.reason


def test_the_published_rung_onevcs_answers_holds_a_node_stating_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The node's own field first, then the repository and global rungs `onevcs` answers."""
    _onevcs(
        tmp_path,
        monkeypatch,
        releases={
            "library": _answer("id/library", WHEEL),
            "service": _answer("id/service", adoption="published"),
        },
    )

    (refused,) = refusals(_plan(_producer(), _consumer()))

    assert refused.field == "adoption"
    assert "adopts `published`" in refused.reason


@pytest.mark.parametrize(
    "correction",
    (
        pytest.param({"consumes": {"producer": "pypi"}}, id="a named target"),
        pytest.param({"consumes": {"producer": "npm"}}, id="a named target R2 refuses instead"),
        pytest.param({"adoption": "fast", "merge_policy": "change-open"}, id="fast adoption"),
    ),
)
def test_a_published_node_that_names_its_target_or_adopts_fast_is_not_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, correction: dict[str, Any]
) -> None:
    _onevcs(tmp_path, monkeypatch, releases={"library": _answer("id/library", WHEEL)})
    plan = _plan(_producer(), _consumer(**{"adoption": "published"} | correction))

    assert "adoption" not in {one.field for one in refusals(plan)}


def test_a_producer_default_target_ends_the_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _onevcs(
        tmp_path,
        monkeypatch,
        releases={"library": _answer("id/library", WHEEL, default="pypi")},
        resolved={"service": ("/srv/service", Workflow.CHANGE_AUTO.value)},
    )
    _own_checkout(tmp_path, monkeypatch)
    plan = _plan(_producer(), _consumer(adoption="published", consumes={"producer": "pypi"}))

    assert refusals(plan) == []


def test_a_fast_node_behind_a_release_on_a_local_direct_identity_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behind an unreleased dependency a fast node publishes as a draft, refused by name."""
    _onevcs(
        tmp_path,
        monkeypatch,
        releases={"library": _answer("id/library", WHEEL)},
        resolved={"service": ("/srv/service", Workflow.LOCAL_DIRECT.value)},
    )

    (refused,) = refusals(_plan(_producer(), _consumer()))

    assert (refused.node, refused.field) == ("consumer", "adoption")
    assert "adopts `fast`" in refused.reason
    assert "producer (id/library)" in refused.reason
    assert "'local-direct'" in refused.reason
    assert "`adoption: published`" in refused.reason
    assert "change-open, change-auto, change-direct" in refused.reason
    assert "drop the edge" in refused.reason


def test_the_policy_the_node_states_decides_over_its_repositorys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stated `local-direct` on an identity that opens change requests still refuses."""
    _onevcs(
        tmp_path,
        monkeypatch,
        releases={"library": _answer("id/library", WHEEL)},
        resolved={"service": ("/srv/service", Workflow.CHANGE_AUTO.value)},
    )

    assert refusals(_plan(_producer(), _consumer())) == []
    (refused,) = refusals(_plan(_producer(), _consumer(merge_policy="local-direct")))
    assert refused.field == "adoption"


@pytest.mark.parametrize(
    "why",
    (
        pytest.param({"merge_policy": "brand-new"}, id="a stated policy outside the vocabulary"),
        pytest.param({"repo": "unregistered"}, id="a repository whose policy nobody can say"),
        pytest.param({"repo": None}, id="a node naming no repository"),
    ),
)
def test_a_publication_this_cannot_read_decides_nothing_about_a_fast_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, why: dict[str, Any]
) -> None:
    _onevcs(tmp_path, monkeypatch, releases={"library": _answer("id/library", WHEEL)})

    assert refusals(_plan(_producer(), _consumer(**why))) == []


@pytest.mark.parametrize(
    "producer",
    (
        pytest.param({"id": "producer"}, id="a dependency naming no repository"),
        pytest.param(_producer("service"), id="a dependency inside the node's own repository"),
        pytest.param(_producer("also-service"), id="the same identity under another spelling"),
        pytest.param(_producer("undeclared"), id="a repository resolving no target"),
        pytest.param(_producer("elsewhere"), id="a repository onevcs cannot answer for"),
    ),
)
def test_a_dependency_that_releases_nothing_holds_and_refuses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, producer: dict[str, Any]
) -> None:
    """The engine's own rule: no target, no repository, or the node's own repository."""
    _onevcs(
        tmp_path,
        monkeypatch,
        releases={
            "service": _answer("id/service", WHEEL),
            "also-service": _answer("id/service", WHEEL),
            "undeclared": _answer("id/undeclared"),
        },
        resolved={"service": ("/srv/service", Workflow.LOCAL_DIRECT.value)},
    )

    assert refusals(_plan(producer, _consumer())) == []
    assert refusals(_plan(producer, _consumer(adoption="published"))) == []


def test_a_node_of_this_repository_consuming_a_crate_is_refused_naming_the_judged_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crate reaches this host only through the wheel, and the pin question is the judge's."""
    _onevcs(tmp_path, monkeypatch, releases={"library": _answer("id/library", WHEEL, CRATE)})
    _own_checkout(tmp_path, monkeypatch)
    plan = _plan(
        _producer(),
        _consumer(repo=OWN, consumes={"producer": "crate"}, merge_policy="change-open"),
    )

    (refused,) = refusals(plan)

    assert (refused.node, refused.field) == ("consumer", "consumes")
    assert "`crate:onepipeline`" in refused.reason
    assert "`pypi:onepipeline-cli`" in refused.reason
    assert "engine wheel" in refused.reason
    assert f"`{JUDGED_TIER}`'s question" in refused.reason
    assert "`config/<pin>.version`" in refused.reason


def test_a_crate_taken_as_the_producers_default_is_refused_the_same_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _onevcs(
        tmp_path,
        monkeypatch,
        releases={"library": _answer("id/library", WHEEL, CRATE, default="crate")},
    )
    _own_checkout(tmp_path, monkeypatch)

    (refused,) = refusals(_plan(_producer(), _consumer(repo=OWN, adoption="published")))

    assert refused.field == "consumes"
    assert "'crate'" in refused.reason


@pytest.mark.parametrize(
    "task",
    (
        "## Acceptance criteria\n\n- `config/onepipeline.version` names the adopted release.\n",
        "## Acceptance criteria\n\n- The route works.\n",
    ),
    ids=("naming the pin", "silent about the pin"),
)
def test_a_node_of_this_repository_adopting_a_wheel_is_accepted_whatever_its_criteria_say(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task: str
) -> None:
    """No rule here reads a task's prose: the pin question belongs to the judged tier."""
    _onevcs(tmp_path, monkeypatch, releases={"library": _answer("id/library", WHEEL, CRATE)})
    _own_checkout(tmp_path, monkeypatch)
    plan = _plan(
        _producer(),
        _consumer(
            repo=OWN,
            task=task,
            adoption="published",
            consumes={"producer": "pypi"},
            merge_policy="change-open",
        ),
    )

    assert refusals(plan) == []


def test_a_node_of_this_repository_with_nothing_to_wait_on_is_held_rather_than_uninstalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deps the plan has no node for, or that release nothing, earn no artifact question.

    One refusal, R3's, for the producer this node names no target from; the absent node
    and the silent repository are passed over by every rule.
    """
    _onevcs(
        tmp_path,
        monkeypatch,
        releases={"library": _answer("id/library", WHEEL), "quiet": _answer("id/quiet")},
    )
    _own_checkout(tmp_path, monkeypatch)
    plan = _plan(
        _producer(),
        {"id": "silent", "title": "feat: nothing", "repo": "quiet"},
        _consumer(repo=OWN, adoption="published", deps=["producer", "ghost", "silent"]),
    )

    assert _fields(refusals(plan)) == [("consumer", "adoption")]


def test_a_target_whose_artifact_the_answer_does_not_carry_decides_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An answer naming a target with neither a declaration id nor a probe argument."""
    answer = _answer("id/library", CRATE)
    answer["targets"][0]["probe"] = {}
    del answer["declaration"]
    _onevcs(tmp_path, monkeypatch, releases={"library": answer})
    _own_checkout(tmp_path, monkeypatch)
    plan = _plan(_producer(), _consumer(repo=OWN, consumes={"producer": "crate"}))

    assert refusals(plan) == []


def test_the_artifact_is_read_off_the_probe_when_the_declaration_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target the host override adds carries its artifact on its probe alone."""
    _onevcs(tmp_path, monkeypatch, releases={"library": _answer("id/library", CRATE, probed=True)})

    answered = targets("library")

    assert answered is not None
    assert answered.targets == (Target("crate", "crate:onepipeline"),)


def test_a_checkout_with_no_origin_of_its_own_decides_nothing_about_its_own_nodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither R5 nor R7 can name this host's repository when git names none."""
    _onevcs(
        tmp_path,
        monkeypatch,
        releases={"library": _answer("id/library", WHEEL, CRATE, default="crate")},
    )
    _own_checkout(tmp_path, monkeypatch, origin=None)

    assert this_hosts_origin() is None
    assert refusals(_plan(_producer(), _consumer(repo=OWN, adoption="published"))) == []
    assert refusals(_plan(_producer(), _consumer(adoption="published"))) == []


@pytest.mark.parametrize("remote", ("scp", "ssh", "https"))
def test_this_hosts_origin_is_read_off_every_clone_url_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remote: str
) -> None:
    """An ssh remote in either spelling names the same repository an https one does."""
    _own_checkout(tmp_path, monkeypatch, remote=remote)

    assert this_hosts_origin() == OWN


def test_a_host_that_cannot_run_git_names_no_repository_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    assert this_hosts_origin() is None


def test_a_node_elsewhere_taking_a_producers_default_target_is_refused_naming_consumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default is this host's wheel and says nothing about what that node consumes."""
    _onevcs(
        tmp_path,
        monkeypatch,
        releases={"library": _answer("id/library", WHEEL, CRATE, default="pypi")},
        resolved={"service": ("/srv/service", Workflow.CHANGE_AUTO.value)},
    )
    _own_checkout(tmp_path, monkeypatch)

    (refused,) = refusals(_plan(_producer(), _consumer(adoption="published")))

    assert (refused.node, refused.field) == ("consumer", "consumes")
    assert "`default_target` ('pypi')" in refused.reason
    assert "`consumes: {producer: <target>}`" in refused.reason
    assert "resolves: pypi, crate" in refused.reason


def test_a_hosted_repository_that_is_not_this_hosts_own_is_outside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compared directly as origins, with no `onevcs` asked about the node's repository."""
    _onevcs(
        tmp_path,
        monkeypatch,
        releases={"library": _answer("id/library", WHEEL, default="pypi")},
    )
    _own_checkout(tmp_path, monkeypatch)
    plan = _plan(_producer(), _consumer(repo="github.com/acme/service", adoption="published"))

    (refused,) = refusals(plan)

    assert refused.field == "consumes"
    assert "github.com/acme/service" in refused.reason


@pytest.mark.parametrize(
    "why",
    (
        pytest.param({"consumes": {"producer": "crate"}}, id="the node names its target"),
        pytest.param({"repo": "unregistered"}, id="a repository nobody can place"),
        pytest.param({"adoption": "fast", "merge_policy": "change-open"}, id="fast adoption"),
    ),
)
def test_a_default_is_taken_only_by_a_published_node_that_names_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, why: dict[str, Any]
) -> None:
    _onevcs(
        tmp_path,
        monkeypatch,
        releases={"library": _answer("id/library", WHEEL, CRATE, default="pypi")},
        resolved={"service": ("/srv/service", Workflow.CHANGE_AUTO.value)},
    )
    _own_checkout(tmp_path, monkeypatch)

    assert refusals(_plan(_producer(), _consumer(**{"adoption": "published"} | why))) == []


def test_a_resolution_naming_no_origin_places_nobody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`resolve` answered, but without the one field own-ness is decided on."""
    _stand_in(
        tmp_path,
        monkeypatch,
        "import json, sys\n"
        "if sys.argv[1] == 'resolve':\n"
        "    print(json.dumps({'identity': 'id/service', 'publication_checkout': '/srv'}))\n"
        "else:\n"
        "    print('publication: change-auto')\n",
    )
    _own_checkout(tmp_path, monkeypatch)

    assert Host().is_own("service") is None


@pytest.mark.parametrize(
    ("answer", "why"),
    (
        ("not json at all", "the verb answered something this cannot read"),
        ('{"targets": []}', "it named no identity"),
        ('{"identity": "id/library"}', "it named no targets list"),
        ('{"identity": "id/library", "targets": "pypi"}', "its targets are not a list"),
        ('["id/library"]', "it answered a list rather than an object"),
    ),
)
def test_an_answer_this_cannot_read_decides_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, why: str
) -> None:
    _onevcs(tmp_path, monkeypatch, releases={"library": answer})

    assert targets("library") is None, why
    assert refusals(_plan(_producer(), _consumer(adoption="published"))) == []


def test_an_answer_omitting_the_optional_fields_resolves_them_as_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing `adoption` is `fast` and a missing `default_target` is no default."""
    _onevcs(tmp_path, monkeypatch, releases={"library": _answer("id/library", WHEEL)})

    answered = targets("library")

    assert answered == Targets("id/library", Adoption.FAST, None, (Target(*WHEEL),))


def test_a_target_entry_or_declaration_row_this_cannot_read_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = _answer("id/library", WHEEL)
    answer["targets"].append({"style": "automated"})
    answer["targets"].append("pypi")
    answer["declaration"]["declared"]["target"].append({"name": "crate"})
    answer["declaration"]["declared"]["target"].append("crate")
    answer["default_target"] = 7
    _onevcs(tmp_path, monkeypatch, releases={"library": answer})

    answered = targets("library")

    assert answered is not None
    assert answered.targets == (Target(*WHEEL),)
    assert answered.default_target is None


def test_an_adoption_word_this_does_not_know_decides_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A third rung some later release adds is neither `fast` nor `published` here."""
    _onevcs(
        tmp_path,
        monkeypatch,
        releases={
            "library": _answer("id/library", WHEEL),
            "service": _answer("id/service", adoption="eventually"),
        },
        resolved={"service": ("/srv/service", Workflow.LOCAL_DIRECT.value)},
    )
    plan = _plan(_producer(), _consumer())

    (node,) = [one for one in nodes(plan) if one.id == "consumer"]
    assert resolved_adoption(node, Host()) is None
    assert refusals(plan) == []
    assert Adoption.known("eventually") is None
    assert Adoption.known(7) is None


def test_a_host_with_no_onevcs_at_all_refuses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    assert targets("library") is None


def test_a_verb_that_does_not_return_is_bounded_rather_than_waited_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stand_in(tmp_path, monkeypatch, "import time\n\ntime.sleep(30)\n")
    monkeypatch.setattr(adoption_guard, "TIMEOUT_SECONDS", 1)

    assert targets("library") is None


def test_each_verb_is_asked_once_per_repository_a_plan_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three nodes of one repository behind one producer spend one answer per question."""
    log = _onevcs(
        tmp_path,
        monkeypatch,
        releases={"library": _answer("id/library", WHEEL)},
        resolved={"service": ("/srv/service", Workflow.LOCAL_DIRECT.value)},
    )
    plan = _plan(
        _producer(),
        _consumer(id="one"),
        _consumer(id="two"),
        _consumer(id="three", adoption="published"),
    )

    found = refusals(plan)

    assert _fields(found) == [("one", "adoption"), ("two", "adoption"), ("three", "adoption")]
    # The producer's own repository is placed too — once — and its `rules check` is never
    # asked, because a `resolve` this host cannot make is the end of that question.
    asked = log.read_text(encoding="utf-8").splitlines()
    assert sorted(asked) == sorted(
        [
            "release targets library --json",
            "release targets service --json",
            "resolve library",
            "resolve service",
            "rules check service",
        ]
    ), asked


def test_only_dispatched_nodes_are_read_and_every_field_leniently() -> None:
    plan = _plan(
        {"id": "approve", "kind": "human", "task": "Merge it."},
        {"title": "feat: unnamed"},
        {"id": 7},
        {
            "id": "loose",
            "repo": "",
            "deps": ["producer", 7],
            "adoption": "later",
            "consumes": "pypi",
            "merge_policy": 3,
        },
        {"id": "tight", "repo": "service", "deps": "producer", "merge_policy": "change-open"},
    )

    assert list(nodes(plan)) == [
        Node(NodeId("loose"), None, (NodeId("producer"),), None, {}, None),
        Node(NodeId("tight"), "service", (), None, {}, Workflow.CHANGE_OPEN),
    ]


@pytest.mark.parametrize(
    "plan",
    ("not a plan", {}, {"tasks": "work"}, {"tasks": ["work"]}, {"tasks": [{"id": 7}]}),
)
def test_a_plan_shape_this_does_not_decide_is_left_to_the_loader(plan: object) -> None:
    assert list(nodes(plan)) == []
    assert refusals(plan) == []


def test_a_dependency_is_placed_against_the_node_it_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`releasing` is the one reading of the engine's rule every rule shares."""
    _onevcs(
        tmp_path,
        monkeypatch,
        releases={
            "library": _answer("id/library", WHEEL),
            "service": _answer("id/service"),
        },
    )
    host = Host()
    producer = Node(NodeId("producer"), "library", (), None, {}, None)
    consumer = Node(NodeId("consumer"), "service", (NodeId("producer"),), None, {}, None)
    placeless = Node(NodeId("placeless"), None, (NodeId("producer"),), None, {}, None)

    assert releasing(consumer, producer, host) == targets("library")
    assert releasing(placeless, producer, host) == targets("library")
    assert resolved_adoption(placeless, host) is Adoption.FAST


def test_the_raising_face_reports_the_first_refusal_with_its_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The direct path prints one refusal; it names the node, the field and the rule."""
    _onevcs(
        tmp_path,
        monkeypatch,
        releases={"library": _answer("id/library", WHEEL)},
        resolved={"service": ("/srv/service", Workflow.LOCAL_DIRECT.value)},
    )
    plan = _plan(_producer(), _consumer(consumes={"producer": "npm"}), _consumer(id="held"))

    with pytest.raises(AdoptionError) as raised:
        check_plan(plan)

    first, *_ = refusals(plan)
    assert str(raised.value) == f"consumer: consumes: {first.reason}"
    assert len(refusals(plan)) == 3

    corrected = _plan(_producer(), _consumer(merge_policy="change-open"))
    assert refusals(corrected) == []
    assert check_plan(corrected) is None


#: An adoption no release of `onevcs` has ever named, so the override has to refuse it —
#: and its refusal spells every word it would have accepted, which is the vocabulary.
NOT_AN_ADOPTION = "when-somebody-remembers"

#: How that refusal names the accepted words: ``expected `fast` or `published```.
EXPECTED_WORDS = re.compile(r"expected ((?:`[a-z-]+`(?:, | or )?)+)")


def _override(root: Path, adoption: str) -> subprocess.CompletedProcess[str]:
    """Install a scratch release override whose global rung is ``adoption``, through the recipe."""
    root.mkdir(parents=True)
    releases = root / "onevcs.releases.yml"
    releases.write_text(
        f"version: 1\ndefault:\n  adoption: {adoption}\nrepositories: []\n", encoding="utf-8"
    )
    manifest = root / "checkouts"
    manifest.write_text("", encoding="utf-8")
    return subprocess.run(
        [
            "just",
            "repos-apply",
            "--checkouts",
            str(manifest),
            "--rules",
            str(REPO_ROOT / "config" / "onevcs.rules.yml"),
            "--releases",
            str(releases),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(root / "onevcs")},
        text=True,
        capture_output=True,
        check=False,
    )


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_checkouts` moves
# this gate out of every memoized tier into the uncached `orchestrator:test-checkouts`,
# because its subject — the installed `onevcs` under `.venv`, which no `nx.json` glob
# hashes — is outside this workspace; `tests/test_publication_guard.py` tiers its
# vocabulary gate the same way for the same reason.
@pytest.mark.reads_checkouts
def test_the_adoption_vocabulary_is_the_one_the_installed_onevcs_accepts(tmp_path: Path) -> None:
    """Every rung this module knows is one the override accepts, and nothing else is.

    :class:`~orchestrator.adoption_guard.Adoption` is this repository's copy of the
    adoption vocabulary `onevcs` and the engine share, and a copy nothing reconciles is
    how a release that added a rung comes to be read as `unknown` on every node — which
    refuses nothing, silently. So each member is installed as the global rung through the
    real `just repos-apply` and read back through `onevcs release targets`, and a word
    outside the two is refused — with the refusal spelling every word the override
    would have accepted, which is held to be exactly this module's members.

    Uncached, because its subject is the installed CLI rather than anything in this
    workspace.
    """
    for adoption in Adoption:
        root = tmp_path / adoption.value
        applied = _override(root, adoption.value)
        assert applied.returncode == 0, applied.stdout + applied.stderr
        (checkout,) = registered(
            root, [adoption.value], releases=(root / "onevcs.releases.yml").read_text()
        )
        answered = subprocess.run(
            ["onevcs", "release", "targets", str(checkout), "--json"],
            env={**os.environ, "ONEVCS_HOME": str(root / "onevcs")},
            text=True,
            capture_output=True,
            check=False,
        )
        assert answered.returncode == 0, answered.stderr
        assert json.loads(answered.stdout)["adoption"] == adoption.value, (
            f"the installed onevcs resolves {adoption.value!r} as something else, so this "
            f"module's copy of its vocabulary has drifted:\n{answered.stdout}"
        )

    refused = _override(tmp_path / "unknown", NOT_AN_ADOPTION)

    assert refused.returncode != 0, (
        f"the override accepted {NOT_AN_ADOPTION!r} as an adoption, so this module's two "
        f"are no longer the whole vocabulary:\n{refused.stdout}"
    )
    spelled = EXPECTED_WORDS.search(refused.stdout + refused.stderr)
    assert spelled is not None, refused.stdout + refused.stderr
    assert set(re.findall(r"`([a-z-]+)`", spelled[1])) == {one.value for one in Adoption}, (
        f"the installed onevcs accepts {spelled[1]}, which is not this module's vocabulary"
    )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
