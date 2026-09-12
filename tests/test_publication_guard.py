"""A lifecycle node this host's publication policy would refuse is refused before dispatch.

Two shapes reach a dispatch, run for as long as the work takes, and are refused only
once the branch is finished: a node whose `title` the destination repository's own
`commit-msg` hook rejects, and a node consuming a release target on an identity whose
workflow opens no change request. One node of this host lost finished, judge-passed work
to a `refactor:` subject and its dependent was skipped for it; another ran an hour and
thirty-six minutes before the second was reported.

The git checkout and its hook here are real — the hook is written, made executable, and
**run**, which is the whole of how this refusal and a repository's own rule stay one
statement. What is doubled is `onevcs`, at the boundary this repository doubles every
published CLI a recipe delegates to: it is what says which identity a `repo` names and
which policy that identity resolves, and asking the real one would make this tier's
memoized verdict depend on another repository's registration.
`tests/plan_tooling/test_check_plan_recipe_e2e.py` drives the real one, through the real
`just check-plan`, against a registered scratch identity.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from scratch_identity import registered, rules_for

from orchestrator import publication_guard
from orchestrator.publication_guard import (
    Destination,
    PublicationError,
    Workflow,
    check_plan,
    commit_msg_hook,
    destination,
    hook_refusal,
    publishing_nodes,
    refusals,
)
from orchestrator.root import REPO_ROOT

#: A `commit-msg` hook of the shape this host's own is: it reads the subject and nothing
#: else, because `onevcs` runs one against a subject it is about to publish where no
#: index, diff or branch exists. It refuses a type its repository cuts no release from.
HOOK = """#!/usr/bin/env bash
set -euo pipefail
subject=$(head -n 1 "$1")
case "$subject" in
    feat:*|fix:*|perf:*) exit 0 ;;
esac
echo "commit-msg: this repository does not release from '${subject%%:*}:'" >&2
exit 1
"""

#: A workflow that opens a change request, so a node consuming a release target on it is
#: waiting on a publication that identity really makes.
OPENS_A_CHANGE_REQUEST = Workflow.CHANGE_AUTO


def _checkout(root: Path, *, hook: str | None = HOOK) -> Path:
    """A real git checkout, with a real `commit-msg` hook where one is asked for."""
    checkout = root / "publication"
    checkout.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(checkout)], check=True)
    if hook is None:
        return checkout
    hooks = checkout / ".githooks"
    hooks.mkdir()
    written = hooks / "commit-msg"
    written.write_text(hook, encoding="utf-8")
    written.chmod(written.stat().st_mode | stat.S_IXUSR)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "core.hooksPath", ".githooks"], check=True
    )
    return checkout


def _onevcs(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    answers: dict[str, tuple[Path, str]],
    origins: dict[str, str] | None = None,
) -> None:
    """Put a stand-in `onevcs` on PATH answering for exactly the repositories named.

    The two verbs answer as the real one does: `resolve` a JSON object naming the
    identity and the publication checkout — and the identity's `origin`, for each
    repository ``origins`` names one for — and `rules check` its line-oriented report
    whose `publication:` line is the policy. Anything else exits 2, which is what the
    real one does for a repository this host has not registered.
    """
    resolved = {
        repo: [
            json.dumps(
                {"identity": f"local/{repo}", "publication_checkout": str(checkout)}
                | ({"origin": origins[repo]} if origins and repo in origins else {})
            ),
            f"repo: {repo}\nidentity: local/{repo}\npublication: {workflow} (from rule 1)\n",
        ]
        for repo, (checkout, workflow) in answers.items()
    }
    _stand_in(
        root,
        monkeypatch,
        "import json, sys\n"
        f"answers = {resolved!r}\n"
        "verb = sys.argv[1:]\n"
        "if verb[:1] == ['resolve'] and len(verb) == 2:\n"
        "    repo, index = verb[1], 0\n"
        "elif verb[:2] == ['rules', 'check'] and len(verb) == 3:\n"
        "    repo, index = verb[2], 1\n"
        "else:\n"
        "    raise SystemExit(2)\n"
        "found = answers.get(repo)\n"
        "if found is None:\n"
        "    raise SystemExit(2)\n"
        "sys.stdout.write(found[index])\n",
    )


def _stand_in(root: Path, monkeypatch: pytest.MonkeyPatch, program: str) -> None:
    """Put one Python program on PATH under the name `onevcs`, first."""
    binary = root / "bin"
    binary.mkdir(parents=True, exist_ok=True)
    written = binary / "onevcs"
    written.write_text(f"#!{sys.executable}\n{program}", encoding="utf-8")
    written.chmod(written.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")


def _plan(**node: Any) -> dict[str, Any]:
    """A plan of one lifecycle node, in the shape the engine's loader hands a check.

    `Any` because that document is the engine's own open contract, read here as the
    untyped JSON a registered check is handed on stdin. A typed node model would be a
    second declaration of it, and the malformed shapes several journeys below hand over
    on purpose — a node whose `id` is a number, a `tasks` that is a string — are exactly
    what such a model would refuse to express.
    """
    return {"tasks": [{"id": "work", "title": "feat: do it", "repo": "service", **node}]}


def test_a_title_the_destinations_own_hook_refuses_is_refused_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The subject a node's title becomes, ruled on by the repository that will land it.

    `onevcs` passes the title to the squash commit and to `gh pr create` and never
    re-derives it, so a hook that refuses it refuses the publication — after the whole
    dispatch has been paid for.
    """
    _onevcs(
        tmp_path,
        monkeypatch,
        answers={"service": (_checkout(tmp_path), Workflow.LOCAL_DIRECT)},
    )

    found = refusals(_plan(title="refactor: rename the reader"))

    assert [(one.node, one.field) for one in found] == [("work", "title")]
    assert "'refactor: rename the reader'" in found[0].reason, found[0].reason
    assert "does not release from 'refactor:'" in found[0].reason, found[0].reason
    assert "local/service" in found[0].reason, found[0].reason


def test_a_title_that_hook_accepts_is_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half that makes the refusal worth having: a sound plan still launches."""
    _onevcs(
        tmp_path,
        monkeypatch,
        answers={"service": (_checkout(tmp_path), Workflow.LOCAL_DIRECT)},
    )

    assert refusals(_plan(title="fix: correct the reader")) == []


def test_a_destination_that_declares_no_hook_is_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing here is knowable, so nothing is refused.

    A plan is checked against repositories this checkout may never have seen, and
    refusing one for what this host cannot read would refuse plans that launch today.
    """
    _onevcs(
        tmp_path,
        monkeypatch,
        answers={"service": (_checkout(tmp_path, hook=None), Workflow.LOCAL_DIRECT)},
    )

    assert refusals(_plan(title="refactor: rename the reader")) == []


def test_a_hook_that_is_not_executable_is_read_as_no_hook(tmp_path: Path) -> None:
    """git runs an executable hook and skips anything else, and so does this."""
    checkout = _checkout(tmp_path)
    hook = checkout / ".githooks" / "commit-msg"
    hook.chmod(hook.stat().st_mode & ~stat.S_IXUSR & ~stat.S_IXGRP & ~stat.S_IXOTH)

    assert commit_msg_hook(checkout) is None


def test_the_hook_is_found_where_git_itself_would_find_it(tmp_path: Path) -> None:
    """`core.hooksPath` decides, and git is what is asked rather than the setting read.

    A publication's disposable clone is given the lender's hooks path when it is cut, so
    the hook resolved here is the hook that will run. A checkout that sets none falls
    back to git's own default, which is a real state for a repository whose hooks are
    installed rather than tracked.
    """
    declared = _checkout(tmp_path)
    assert commit_msg_hook(declared) == declared / ".githooks" / "commit-msg"

    default = _checkout(tmp_path / "other", hook=None)
    installed = default / ".git" / "hooks" / "commit-msg"
    installed.write_text(HOOK, encoding="utf-8")
    installed.chmod(installed.stat().st_mode | stat.S_IXUSR)
    assert commit_msg_hook(default) == installed


def test_a_directory_that_is_no_git_checkout_answers_no_hook(tmp_path: Path) -> None:
    """A publication checkout this host cannot ask git about says nothing about a title."""
    outside = tmp_path / "not-a-checkout"
    outside.mkdir()

    assert commit_msg_hook(outside) is None
    assert commit_msg_hook(tmp_path / "absent") is None


def test_a_host_that_cannot_run_git_says_nothing_about_a_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git is what resolves the hook, so a host without it resolves none.

    The same answer an unreadable checkout gets, and for the same reason: this refusal
    is written to miss rather than to refuse a node it cannot read.
    """
    checkout = _checkout(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    assert commit_msg_hook(checkout) is None


def test_a_hook_that_refuses_without_saying_why_is_still_reported(tmp_path: Path) -> None:
    """A refusal has to name something an operator can act on, even a silent one.

    A hook that exits non-zero refuses the publication commit whatever its reason, so
    reporting it before the dispatch is the accurate answer — but a reason of empty
    string would read as a refusal about nothing.
    """
    checkout = _checkout(tmp_path, hook="#!/usr/bin/env bash\nexit 3\n")

    reported = hook_refusal(checkout / ".githooks" / "commit-msg", checkout, "feat: fine")

    assert reported == "it exited 3 without saying why"


def test_a_hooks_own_report_reaches_the_refusal_bounded_and_printable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another repository's program writes into a message this host prints to a terminal.

    A report carrying escape sequences could move the cursor, colour unrelated output, or
    overwrite the refusal it is quoted inside; one carrying no newline at all could be
    arbitrarily long and bury the node and the field the refusal is about. So it is
    collapsed, every control character is replaced, and the length is bounded with the
    truncation said rather than silent.
    """
    shouting = "\x1b[2J\x1b[H" + "x" * (publication_guard.REPORT_LIMIT + 50)
    _onevcs(
        tmp_path,
        monkeypatch,
        answers={
            "service": (
                _checkout(
                    tmp_path,
                    hook=f'#!/usr/bin/env bash\nprintf %s "{shouting}" >&2\nexit 1\n',
                ),
                Workflow.LOCAL_DIRECT,
            )
        },
    )

    (refused,) = refusals(_plan(title="refactor: rename it"))

    assert "\x1b" not in refused.reason, refused.reason
    assert publication_guard.CONTROL_STAND_IN in refused.reason, refused.reason
    assert f"[{publication_guard.REPORT_LIMIT} of " in refused.reason, refused.reason
    assert "x" * (publication_guard.REPORT_LIMIT + 1) not in refused.reason, refused.reason


def test_a_hook_that_cannot_be_run_at_all_refuses_nothing(tmp_path: Path) -> None:
    """Nothing was learned, so nothing is refused."""
    checkout = _checkout(tmp_path, hook=None)

    assert hook_refusal(checkout / ".githooks" / "commit-msg", checkout, "feat: fine") is None


def test_a_node_consuming_a_release_its_identity_never_publishes_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wait on a publication that identity never makes, named with what it awaits."""
    _onevcs(
        tmp_path,
        monkeypatch,
        answers={"service": (_checkout(tmp_path), Workflow.LOCAL_DIRECT)},
    )

    found = refusals(_plan(consumes={"library": "pypi", "engine": "crate"}))

    assert [(one.node, one.field) for one in found] == [("work", "consumes")]
    assert "engine, library" in found[0].reason, found[0].reason
    assert "'local-direct'" in found[0].reason, found[0].reason
    assert "opens no change request" in found[0].reason, found[0].reason


def test_a_node_consuming_a_release_on_a_change_request_workflow_is_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The identity really does publish one, so the wait is one it can answer."""
    _onevcs(
        tmp_path, monkeypatch, answers={"service": (_checkout(tmp_path), OPENS_A_CHANGE_REQUEST)}
    )

    assert refusals(_plan(consumes={"library": "pypi"})) == []


def test_a_policy_the_node_states_decides_what_its_consumes_can_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A node's own `merge_policy` beats its repository's, whichever way it points.

    The engine's loader reads it that way, and this host's own observer-pacing adoption
    is the shape it exists for: a `change-open` node of a `local-direct` repository,
    which the engine accepted and this check refused for a workflow the node does not
    publish under. The converse is the trap the rule closes — a node naming
    `local-direct` on an identity that opens change requests has said it opens none.
    """
    _onevcs(
        tmp_path,
        monkeypatch,
        answers={
            "service": (_checkout(tmp_path), Workflow.LOCAL_DIRECT),
            "hosted": (_checkout(tmp_path / "hosted"), OPENS_A_CHANGE_REQUEST),
        },
    )

    assert (
        refusals(_plan(consumes={"library": "pypi"}, merge_policy=Workflow.CHANGE_OPEN.value)) == []
    )

    (refused,) = refusals(
        _plan(
            repo="hosted",
            consumes={"library": "pypi"},
            merge_policy=Workflow.LOCAL_DIRECT.value,
        )
    )
    assert (refused.node, refused.field) == ("work", "consumes")
    assert "'local-direct'" in refused.reason, refused.reason
    assert "states for itself" in refused.reason, refused.reason


def test_a_stated_policy_outside_the_vocabulary_decides_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine refuses the spelling by name at launch; this reads the repository's."""
    _onevcs(
        tmp_path,
        monkeypatch,
        answers={"service": (_checkout(tmp_path), Workflow.LOCAL_DIRECT)},
    )

    (refused,) = refusals(_plan(consumes={"library": "pypi"}, merge_policy="auto"))

    assert refused.field == "consumes"
    assert "resolves for" in refused.reason, refused.reason


def test_an_empty_consumes_is_not_a_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Naming the field and naming a target are different things."""
    _onevcs(
        tmp_path,
        monkeypatch,
        answers={"service": (_checkout(tmp_path), Workflow.LOCAL_DIRECT)},
    )

    assert refusals(_plan(consumes={})) == []


def test_a_workflow_outside_the_published_vocabulary_decides_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A word this module does not know is unknown rather than either half of the answer.

    The four names are `onevcs`'s published `merge_policy` vocabulary; a release that
    added a fifth would have this refuse nothing about it rather than guess, and the
    title half still answers because it reads the destination's hook rather than a name.
    """
    _onevcs(tmp_path, monkeypatch, answers={"service": (_checkout(tmp_path), "something-new")})

    assert destination("service") == Destination(
        "local/service", tmp_path / "publication", workflow=None, origin=None
    )
    assert refusals(_plan(consumes={"library": "pypi"})) == []
    assert [one.field for one in refusals(_plan(title="chore: tidy"))] == ["title"]


def test_a_repository_this_host_cannot_resolve_is_passed_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unregistered destination is a plan this host has nothing to say about."""
    _onevcs(tmp_path, monkeypatch, answers={})

    assert destination("service") is None
    assert refusals(_plan(title="refactor: rename it", consumes={"library": "pypi"})) == []


def test_a_host_with_no_onevcs_at_all_refuses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI, an unregistered repository and a verb that did not return are one answer.

    Each is "this host cannot say", which is what a plan's author is told rather than
    being refused — so they are collapsed deliberately rather than by omission.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    assert destination("service") is None


@pytest.mark.parametrize(
    ("answer", "why"),
    (
        ("not json at all", "`onevcs resolve` answered something this cannot read"),
        ('{"identity": "local/service"}', "it named no publication checkout"),
        ('{"publication_checkout": "/tmp"}', "it named no identity"),
        ('["local/service"]', "it answered a list rather than an object"),
    ),
)
def test_a_resolution_this_cannot_read_decides_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, why: str
) -> None:
    """Another program's answer is narrowed at the read, and a shape it does not have
    leaves the plan unrefused rather than raising six frames from anything actionable."""
    _stand_in(tmp_path, monkeypatch, f"print({answer!r})\n")

    assert destination("service") is None, why


def test_a_rules_report_with_no_publication_line_leaves_the_workflow_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The policy comes from `rules check` alone, so a report without it says nothing."""
    checkout = _checkout(tmp_path)
    _stand_in(
        tmp_path,
        monkeypatch,
        "import json, sys\n"
        f"resolved = {{'identity': 'local/service', 'publication_checkout': {str(checkout)!r}}}\n"
        "print(json.dumps(resolved) if sys.argv[1] == 'resolve' else 'repo: service')\n",
    )

    resolved = destination("service")

    assert resolved is not None and resolved.workflow is None


def test_the_policy_is_taken_from_the_rules_and_never_from_the_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`onevcs resolve`'s own `workflow` is `register`'s derivation, not the routing.

    Every identity on this host registers as `remote` while its rules resolve
    `local-direct`, so reading one for the other would answer the wrong thing for every
    repository here. What is asserted is that a `resolve` answer contradicting the rules
    report changes nothing: the rules report decides.
    """
    checkout = _checkout(tmp_path)
    _stand_in(
        tmp_path,
        monkeypatch,
        "import json, sys\n"
        "resolved = {'identity': 'local/service', 'workflow': 'remote', "
        f"'publication_checkout': {str(checkout)!r}}}\n"
        "print(\n"
        "    json.dumps(resolved)\n"
        "    if sys.argv[1] == 'resolve'\n"
        "    else 'publication: local-direct (from rule 1)'\n"
        ")\n",
    )

    resolved = destination("service")

    assert resolved is not None
    assert resolved.workflow == Workflow.LOCAL_DIRECT


def test_the_destination_is_resolved_once_per_repository_a_plan_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan states one repository on most of its nodes, and each ask is two processes."""
    counter = tmp_path / "asked"
    _stand_in(
        tmp_path,
        monkeypatch,
        f"open({str(counter)!r}, 'a', encoding='utf-8').write('x')\nraise SystemExit(2)\n",
    )

    plan = {
        "tasks": [
            {"id": one, "title": "feat: do it", "repo": "service"}
            for one in ("first", "second", "third")
        ]
    }
    assert refusals(plan) == []
    assert counter.read_text(encoding="utf-8").count("x") == 1


def test_only_the_nodes_whose_work_lands_somewhere_are_read() -> None:
    """A node with no repository publishes nothing, and a human node carries no work.

    The steps of a lifecycle node are not read either, and that is the shape rather than
    a simplification: a stepped node names its repository and its title once, and the
    publication those steps end in is the parent's.
    """
    plan = {
        "tasks": [
            {"id": "direct", "title": "feat: research it", "task": "prose"},
            {"id": "approve", "kind": "human", "title": "chore: merge it", "repo": "service"},
            {"id": "untitled", "repo": "service"},
            {"id": "unrepoed", "title": "feat: do it"},
            {
                "id": "lifecycle",
                "title": "feat: land it",
                "repo": "service",
                "steps": [{"id": "one", "title": "chore: step", "repo": "other"}],
            },
        ]
    }

    assert [node.id for node in publishing_nodes(plan)] == ["lifecycle"]


@pytest.mark.parametrize(
    "plan",
    ("not a plan", {}, {"tasks": "work"}, {"tasks": ["work"]}, {"tasks": [{"id": 7}]}),
)
def test_a_plan_shape_this_does_not_decide_is_left_to_the_loader(plan: object) -> None:
    """The engine's own loader has already ruled on structure, so nothing is raised here.

    A traceback would report a well-formed plan as a broken check, and a refusal would
    be this module deciding a plan's shape — which is the second implementation the
    registered-check design exists to retire.
    """
    assert list(publishing_nodes(plan)) == []
    assert refusals(plan) == []


def test_the_raising_face_reports_the_same_answer_one_refusal_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both paths read one answer, so the two cannot disagree about a plan.

    `check_plan` is what the direct path reports one refusal at a time through;
    `orchestrator/plan_check.py` reports them all through the verb. What differs is how
    much of that one answer each is able to print, which is why the refusal it raises is
    asserted to be the same object the other path would render — and why the correction
    that empties the answer empties both.
    """
    _onevcs(
        tmp_path,
        monkeypatch,
        answers={"service": (_checkout(tmp_path), Workflow.LOCAL_DIRECT)},
    )
    refused = _plan(title="refactor: rename it", consumes={"library": "pypi"})

    with pytest.raises(PublicationError) as raised:
        check_plan(refused)

    assert str(raised.value) == f"work: {refusals(refused)[0].reason}"

    corrected = _plan(title="fix: rename it")

    assert refusals(corrected) == [], "the corrected plan is still refused for something"
    assert check_plan(corrected) is None, "the raising face refused a plan its own reader took"


def test_a_node_wrong_in_two_ways_is_told_about_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two independent fields with two independent corrections, reported together.

    A node told only about its title would be retitled, re-checked, and refused again for
    what it consumes — a second launch attempt for what one report could have said.
    """
    _onevcs(
        tmp_path,
        monkeypatch,
        answers={"service": (_checkout(tmp_path), Workflow.LOCAL_DIRECT)},
    )

    found = refusals(_plan(title="refactor: rename it", consumes={"library": "pypi"}))

    assert sorted(one.field for one in found) == ["consumes", "title"], found
    assert {one.node for one in found} == {"work"}, found


#: A hosted repository's origin, in the shape `onevcs resolve` answers it and the task
#: record's own `repositories` holds it.
HOSTED = "github.com/acme/service"

#: The metadata the store's record carries when a plan wrote its repository on the
#: reserved key rather than in `repositories`. The loaded plan carries it verbatim beside
#: the resolved `repo`, which is the only way the two spellings can be told apart.
ON_THE_RESERVED_KEY = {"onepipeline.id": "work", "onepipeline.repo": "service"}


def test_a_hosted_repository_named_on_the_reserved_key_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record shape that files every task's issue in the orchestrator's repository.

    The engine puts a node's repository in the record's own `repositories` and keeps
    `onepipeline.repo` for an identity that list cannot hold; a hosted identity named by
    alias on the key reaches the plan store with `repositories` empty. The refusal names
    the node, what it wrote, and the origin to write instead.
    """
    _onevcs(
        tmp_path,
        monkeypatch,
        answers={"service": (_checkout(tmp_path), Workflow.LOCAL_DIRECT)},
        origins={"service": HOSTED},
    )

    found = refusals(_plan(metadata=ON_THE_RESERVED_KEY))

    assert [(one.node, one.field) for one in found] == [("work", "repo")]
    assert "'service'" in found[0].reason, found[0].reason
    assert f'repositories: ["{HOSTED}"]' in found[0].reason, found[0].reason
    assert "onepipeline.repo" in found[0].reason, found[0].reason


def test_the_same_repository_named_in_repositories_is_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The corrected shape: the loader reads `repo` from the list, and the key is absent."""
    _onevcs(
        tmp_path,
        monkeypatch,
        answers={HOSTED: (_checkout(tmp_path), Workflow.LOCAL_DIRECT)},
        origins={HOSTED: HOSTED},
    )

    assert refusals(_plan(repo=HOSTED, metadata={"onepipeline.id": "work"})) == []


def test_a_path_origin_on_the_reserved_key_is_the_case_the_key_exists_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local checkout `onevcs` knows by its path is not a value `repositories` holds.

    Every scratch identity this repository's own journeys register resolves this way,
    and a refusal here would refuse every one of them.
    """
    _onevcs(
        tmp_path,
        monkeypatch,
        answers={"service": (_checkout(tmp_path), Workflow.LOCAL_DIRECT)},
        origins={"service": str(tmp_path / "service-origin")},
    )

    assert refusals(_plan(metadata=ON_THE_RESERVED_KEY)) == []


def test_an_answer_naming_no_origin_decides_nothing_about_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read leniently: the two refusals that need only the checkout are still made."""
    _onevcs(
        tmp_path,
        monkeypatch,
        answers={"service": (_checkout(tmp_path), Workflow.LOCAL_DIRECT)},
    )

    found = refusals(_plan(title="refactor: rename it", metadata=ON_THE_RESERVED_KEY))

    assert [one.field for one in found] == ["title"], found


def test_a_verb_that_does_not_return_is_bounded_rather_than_waited_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This runs in front of a launch an operator is holding, so every ask has a bound.

    A repository whose verb hangs is one this module has nothing to say about, which is
    the same answer an unregistered one gets.
    """
    _stand_in(tmp_path, monkeypatch, "import time\n\ntime.sleep(30)\n")
    monkeypatch.setattr(publication_guard, "TIMEOUT_SECONDS", 1)

    assert destination("service") is None


def test_a_hook_that_does_not_return_is_bounded_the_same_way(tmp_path: Path) -> None:
    """The destination's own hook is another program, and is given the same bound."""
    checkout = _checkout(tmp_path, hook="#!/usr/bin/env bash\nsleep 30\n")

    with pytest.MonkeyPatch.context() as bounded:
        bounded.setattr(publication_guard, "TIMEOUT_SECONDS", 1)
        assert hook_refusal(checkout / ".githooks" / "commit-msg", checkout, "feat: fine") is None


#: A publication policy no release of `onevcs` has ever named, so the registry has to
#: refuse it. Spelled to be obviously outside the vocabulary rather than nearly inside
#: it: what this proves is that the refusal happens at all, and a near-miss would leave a
#: reader wondering whether it was refused for being unknown or for being a typo.
NOT_A_WORKFLOW = "merge-it-and-hope"


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_checkouts` is
# not a narrower key inside a memoized tier: it moves a test out of every memoized tier
# into the uncached `orchestrator:test-checkouts`, because its subject — the installed
# `onevcs` under `.venv`, which no `nx.json` glob hashes — is outside this workspace. A
# project of its own would give this gate a key, and a memoized green would replay across
# the very upgrade it exists to catch. `tests/conftest.py`'s own checkout guard states
# that reasoning where it enforces the marker, and every `reads_checkouts` test in this
# repository is tiered this way for it.
@pytest.mark.reads_checkouts
def test_the_workflow_vocabulary_is_the_one_the_installed_onevcs_accepts(tmp_path: Path) -> None:
    """Every policy this module knows is one the registry resolves, and nothing else is.

    :class:`~orchestrator.publication_guard.Workflow` is this repository's copy of
    `onevcs`'s own `merge_policy` vocabulary, and a copy with nothing reconciling it is
    how a release that renamed one of these comes to answer `unknown` for every identity
    on this host — which refuses nothing, silently. So each member is registered as a
    real rule through the real `just repos-apply`, against a scratch registry, and read
    back through the verb this module reads it through.

    The other direction is the half that makes it a vocabulary rather than a list: a
    policy outside it is refused by the registry, so `Workflow.known` answering `None`
    for a word is `onevcs` not knowing it either.

    Uncached, because its subject is the installed CLI rather than anything in this
    workspace: no `nx.json` key covers `.venv`, and a memoized green here would replay
    across the upgrade it exists to catch.
    """
    for workflow in Workflow:
        (checkout,) = registered(tmp_path / workflow.value, [workflow.value], publication=workflow)
        reported = subprocess.run(
            ["onevcs", "rules", "check", str(checkout)],
            env={**os.environ, "ONEVCS_HOME": str(tmp_path / workflow.value / "onevcs")},
            text=True,
            capture_output=True,
            check=False,
        )
        assert reported.returncode == 0, reported.stderr
        assert f"publication: {workflow.value}" in reported.stdout, (
            f"the installed onevcs resolves {workflow.value!r} as something else, so this "
            f"module's copy of its vocabulary has drifted:\n{reported.stdout}"
        )

    refused = _applied(tmp_path / "unknown", NOT_A_WORKFLOW)

    assert refused.returncode != 0, (
        f"the registry accepted {NOT_A_WORKFLOW!r} as a publication policy, so this "
        f"module's four are no longer the whole vocabulary:\n{refused.stdout}"
    )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


def _applied(root: Path, publication: str) -> subprocess.CompletedProcess[str]:
    """Install a scratch rules file naming ``publication``, through the real recipe."""
    root.mkdir(parents=True)
    rules = root / "onevcs.rules.yml"
    rules.write_text(rules_for(publication), encoding="utf-8")
    manifest = root / "checkouts"
    manifest.write_text("", encoding="utf-8")
    return subprocess.run(
        ["just", "repos-apply", "--checkouts", str(manifest), "--rules", str(rules)],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(root / "onevcs")},
        text=True,
        capture_output=True,
        check=False,
    )
