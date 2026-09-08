"""A brief that cannot be read is refused as unread, never as a brief that is wrong.

`scripts/plan-brief.sh` is the one grammar `just plan` and `just finish-plan` read a
manager's brief through, and the two readers in it answer different questions about the
same file: whether it is the dispatched task a planning flow can be made from, and which
plan project it names. Both used to answer a *failed read* with a sentence about what the
brief says. `grep` parts a missing section from a read error — 1 against 2 — but
`if ! grep ...` collapsed them, and bash's `read` cannot part a genuine end of file from a
failed read at all. So an author whose brief had become unreadable was told to add a
section that was already there, or to add a `Plan project:` line to a file that was not.

These are unit journeys rather than recipe ones, because neither command can reach the
state: each tests the brief for a readable regular file before it reads one, so a path
that cannot be read is refused by that test and never reaches the reader underneath it.
Driving them through `just plan` would exercise that guard and prove nothing about the
readers, which is where the defect was. So the subject is the shell library's own
contract, and it is driven as what it is — the real file, sourced into a real bash, over
real paths, reading through the real `cat` on this host, with nothing doubled.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.root import REPO_ROOT

#: The grammar under test.
BRIEF_GRAMMAR = REPO_ROOT / "scripts" / "plan-brief.sh"

#: Its two readers of a brief. Both are covered because a reader repaired in one and not
#: the other is exactly the state this is about — and because the launchers call them one
#: after the other, so a manager meets whichever answers first.
BRIEF_READERS = ("plan_brief_is_a_task", "plan_brief_project")

#: Every way this grammar says a refusal is about the file rather than about what the file
#: says. Two, because the readers reach that answer at different depths and both are
#: right: `plan_brief_is_a_task` tests the path for a readable regular file and refuses
#: there, and `plan_brief_project` is handed a brief a caller has already tested and so
#: meets the failure in the reader itself.
READ_REFUSALS = ("could not be read", "is not a readable file")

#: What only a refusal about the content may carry. This is the half that makes these
#: journeys about distinguishability rather than about wording: both are what an
#: unreadable brief was refused with before the readers were repaired, so a reader
#: answering one of them again would pass every "was it refused" assertion while sending
#: its author to edit a file that was never the problem.
CONTENT_REFUSALS = ("states no '## ", "names no plan project")


def _grammar(function: str, brief: Path) -> subprocess.CompletedProcess[str]:
    """Run one reader of the real grammar over ``brief``, in a real bash.

    The helper establishes `set -e` on its own account, so a refusal leaves the shell at
    the status the function returned and this reads it rather than reasoning about it.
    """
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"\n"$2" plan "$3"',
            "grammar",
            str(BRIEF_GRAMMAR),
            function,
            str(brief),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


@pytest.mark.parametrize("function", BRIEF_READERS)
@pytest.mark.parametrize("unreadable", ("absent", "directory"))
def test_a_brief_that_cannot_be_read_is_refused_as_unread_rather_than_as_wrong(
    tmp_path: Path, function: str, unreadable: str
) -> None:
    """A read that failed is reported as one, never as a statement about the content.

    Measured on this file before the readers were repaired, called the way both launchers
    call them — after a `||`, which suppresses the `set -e` the helper establishes:
    `plan_brief_project` over a path that does not exist answered *"names no plan project
    ... add a line reading 'Plan project: <source>:<project>'"*, which is an instruction to
    edit a file that is not there, and over a directory it fell out of the loop with an
    unset `line`.

    Both shapes are driven because they fail at different moments — one cannot be opened
    and one opens and then refuses to be read — and a reader that only checked whether the
    path existed would part them.
    """
    brief = tmp_path / "brief.md"
    if unreadable == "directory":
        brief.mkdir()

    refused = _grammar(function, brief)

    assert refused.returncode != 0, f"an unreadable brief was accepted:\n{refused.stdout}"
    reported = refused.stderr + refused.stdout
    assert any(refusal in reported for refusal in READ_REFUSALS), (
        f"{function} refused an unreadable brief without saying it could not read it, so "
        f"its author is told something about the brief's content instead:\n{reported}"
    )
    stated = [line for line in reported.splitlines() if line.startswith("plan: ")]
    assert stated and all("; " in line for line in stated), (
        f"the refusal states no remedy beside its cause:\n{reported}"
    )
    for refusal in CONTENT_REFUSALS:
        assert refusal not in reported, (
            f"{function} refused a brief it could not read as though it had read one: "
            f"{refusal!r} is a statement about content, and there is none to state "
            f"anything about:\n{reported}"
        )


@pytest.mark.parametrize(
    ("function", "content", "names"),
    (
        (
            "plan_brief_is_a_task",
            "## What\nx\n\n## Why\ny\n",
            "states no '## Acceptance criteria'",
        ),
        ("plan_brief_project", "## What\nx\n", "names no plan project"),
    ),
    ids=("a missing section", "no plan project"),
)
def test_a_brief_that_reads_and_is_wrong_is_still_refused_for_what_it_says(
    tmp_path: Path, function: str, content: str, names: str
) -> None:
    """The other half: a readable brief is judged on its content, and says so.

    Without this the journeys above are satisfied by readers that refused everything as
    unreadable, which is the same defect pointing the other way — an author told to check
    a path when what is wrong is the brief. The two together are the property: what is
    refused names which of the two happened.
    """
    brief = tmp_path / "brief.md"
    brief.write_text(content, encoding="utf-8")

    refused = _grammar(function, brief)

    assert refused.returncode != 0, f"a brief that is not a task was accepted:\n{refused.stdout}"
    reported = refused.stderr + refused.stdout
    assert names in reported, reported
    for refusal in READ_REFUSALS:
        assert refusal not in reported, (
            f"{function} refused a brief it read perfectly well as one it could not read, "
            f"which sends its author to check a path that is fine:\n{reported}"
        )


@pytest.mark.parametrize("terminated", (True, False))
def test_a_brief_the_readers_can_read_is_read_whole(tmp_path: Path, terminated: bool) -> None:
    """And a brief that is a task still parses, including its last line.

    The reader these journeys are about replaced a `read` loop whose `|| [ -n "$line" ]`
    was there for a final line with no newline — and which caught a failed read as one
    too. Dropping that condition is only sound if the last line is still read, so both
    endings are driven and the declaration is deliberately the last line of the brief.
    """
    brief = tmp_path / "brief.md"
    declaration = "Plan project: authoring:cursor-shape"
    brief.write_text(
        f"## What\nx\n\n## Why\ny\n\n## Acceptance criteria\n- z\n\n{declaration}"
        + ("\n" if terminated else ""),
        encoding="utf-8",
    )

    assert _grammar("plan_brief_is_a_task", brief).returncode == 0, (
        "a brief carrying every required section was refused"
    )
    read = _grammar("plan_brief_project", brief)

    assert read.returncode == 0, f"a brief naming its plan project was refused:\n{read.stderr}"
    assert read.stdout == "authoring:cursor-shape", (
        f"the plan project was not read off the brief's last line:\n{read.stdout!r}"
    )
