"""`just repos-bootstrap` provisions each registered sibling's gate once per change to it.

A correct, committed change spent three publication attempts discovering, tool by
tool, what its target repository's pre-push gate needed, because nothing on this host
had run that repository's own `just bootstrap` before a dispatch published through it.
This recipe runs every registered checkout's bootstrap ahead of that, and what it owes
is stated as four properties: the first call runs the bootstrap, a second with nothing
moved does not, a call after the checkout's `HEAD` or a named bootstrap input moved
does, and two callers at once run it once — and a caller that waited reads the checkout
as it stands when its wait ends, so one that moved under it is bootstrapped again.

Nothing here is doubled and no real sibling is touched. Every journey registers a
stand-in checkout of its own — a real git repository whose `just bootstrap` leaves a
mark — in a list the recipe is pointed at with `--checkouts`, exactly as `just
repos-apply --checkouts` is, and keeps the memo under a `XDG_CACHE_HOME` of its own.
The recipe, the script, `just`, `git` and `flock` are the real ones.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] Every
journey here is `reads_recipes`: it drives the real recipe over stand-ins it builds and
reads nothing of this repository in-process, which is what `recipeWorkspace` keys, and a
project of its own for one recipe would be a second key naming the same files.
llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] This module is not the
expensive kind: every journey runs the recipe over stand-ins under `tmp_path`, spends no
launch, no provider turn and no install, and the slowest holds a lock for a few seconds
to let a second caller arrive. The recipe tier is where a recipe's journey belongs
(`tests/AGENTS.md`), and every journey there already pays the `scripts/**/*` key.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeIs, get_args

import pytest
from waits import timeout as e2e_timeout
from waits import until

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_recipes

#: The variable a stand-in's bootstrap appends its mark through, so the mark lives
#: outside the checkout: a file inside it that the recipe body names would itself be a
#: bootstrap input, and the mark of one run would be the reason for the next.
MARKS_VARIABLE = "STAND_IN_MARKS"
#: How long a stand-in's slow bootstrap holds the per-checkout lock, for the journey
#: whose subject is two callers arriving while it does.
SLOW_BOOTSTRAP_SECONDS = 3
#: The word the recipe answers a call nested inside another with.
NESTED = "nested inside another repos-bootstrap"
#: The file a held stand-in's bootstrap waits for before it finishes, named through this
#: variable, so the journey about a checkout moving under a waiting caller decides when
#: the first caller's bootstrap ends rather than racing a sleep.
RELEASE_VARIABLE = "STAND_IN_RELEASE"
#: How long a held stand-in's bootstrap waits for its release before giving up, so a
#: journey that never releases it fails rather than hangs.
HELD_BOOTSTRAP_SECONDS = 60


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(checkout: Path, subject: str) -> None:
    _git("add", "-A", cwd=checkout)
    _git(
        "-c",
        "user.name=Journey",
        "-c",
        "user.email=journey@example.invalid",
        "commit",
        "-q",
        "-m",
        subject,
        cwd=checkout,
    )


#: The inputs every stand-in's recipe body names, in the three spellings a recipe line
#: carries a path in: bare, quoted, and behind just's ignore-failure `-` prefix. The
#: memo has to read all three off the body as files.
NAMED_INPUTS = ("setup.sh", "quoted.sh", "tolerated.sh")


def _stand_in(root: Path, *, bootstrap_body: str, origin: str | None = None) -> Path:
    """A real checkout whose `just bootstrap` is `bootstrap_body`, plus the named inputs.

    The recipe body names the three `scripts/` files above so the journeys have
    bootstrap inputs the recipe can read off the body without executing it; the mark is
    appended through `$STAND_IN_MARKS`, outside the checkout. `origin` gives the
    checkout a remote, for the journeys about which repository a checkout is.
    """
    checkout = root
    (checkout / "scripts").mkdir(parents=True)
    for name in NAMED_INPUTS:
        (checkout / "scripts" / name).write_text(f"#!/bin/sh\necho {name}\n", encoding="utf-8")
        (checkout / "scripts" / name).chmod(0o755)
    (checkout / "justfile").write_text(
        'bootstrap:\n    @./scripts/setup.sh\n    @"./scripts/quoted.sh"\n'
        "    -./scripts/tolerated.sh\n"
        + textwrap.indent(textwrap.dedent(bootstrap_body).strip("\n"), "    ")
        + "\n",
        encoding="utf-8",
    )
    _git("init", "-q", "--initial-branch", "main", cwd=checkout)
    if origin is not None:
        _git("remote", "add", "origin", origin, cwd=checkout)
    _commit(checkout, "seed")
    return checkout


def _marking(tmp_path: Path, name: str, *, before: str = "") -> Path:
    """A stand-in whose bootstrap appends one line to the marks file."""
    return _stand_in(
        tmp_path / name,
        bootstrap_body=f'{before}@echo "{name}" >> "${MARKS_VARIABLE}"\n',
    )


def _held(tmp_path: Path, name: str) -> Path:
    """A stand-in whose bootstrap records the `HEAD` it ran at and then waits to be released.

    The mark is the short `HEAD` rather than the name, so a journey can read which tree
    each bootstrap ran against; the wait is bounded so an unreleased one fails.
    """
    return _stand_in(
        tmp_path / name,
        bootstrap_body=(
            f'@git rev-parse --short HEAD >> "${MARKS_VARIABLE}"\n'
            f"@for _ in $(seq 1 {HELD_BOOTSTRAP_SECONDS * 10}); do"
            f' [ -f "${RELEASE_VARIABLE}" ] && break; sleep 0.1; done\n'
        ),
    )


def _short_head(checkout: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _descendants_named(pid: int, name: str) -> list[int]:
    """The processes under `pid` whose executable is `name`, read off the process table.

    A read, never a signal: it is how a journey learns that a caller has reached the
    lock — the `flock` it spawns is a child of the recipe's script — without matching a
    command line, which the shell doing the asking would match too.
    """
    table = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,comm="], check=True, capture_output=True, text=True
    ).stdout
    children: dict[int, list[tuple[int, str]]] = {}
    for row in table.splitlines():
        fields = row.split(None, 2)
        if len(fields) != 3:
            continue
        children.setdefault(int(fields[1]), []).append((int(fields[0]), fields[2]))
    found: list[int] = []
    frontier = [pid]
    while frontier:
        parent = frontier.pop()
        for child, command in children.get(parent, []):
            if command == name:
                found.append(child)
            frontier.append(child)
    return found


def _caller(listing: Path, environment: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        ["just", "repos-bootstrap", "--checkouts", str(listing)],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )


def _listing(tmp_path: Path, *checkouts: Path | str) -> Path:
    """A checkout list of the recipe's tracked shape, naming these paths."""
    listing = tmp_path / "checkouts"
    listing.write_text(
        "# the stand-ins this journey registers\n"
        + "".join(f"{checkout}\n" for checkout in checkouts),
        encoding="utf-8",
    )
    return listing


def _environment_listing(tmp_path: Path, *checkouts: Path) -> Path:
    """A second list, in a directory of its own, for the environment to name."""
    (tmp_path / "env").mkdir()
    return _listing(tmp_path / "env", *checkouts)


def _run(tmp_path: Path, listing: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    """One `just repos-bootstrap` over `listing`, memoized under this test's cache."""
    return subprocess.run(
        ["just", "repos-bootstrap", "--checkouts", str(listing)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        env=_environment(tmp_path, **extra_env),
    )


def _environment(tmp_path: Path, **extra_env: str) -> dict[str, str]:
    return {
        **os.environ,
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        MARKS_VARIABLE: str(tmp_path / "marks"),
        **extra_env,
    }


def _marks(tmp_path: Path) -> list[str]:
    marks = tmp_path / "marks"
    return marks.read_text(encoding="utf-8").split() if marks.exists() else []


def _line(result: subprocess.CompletedProcess[str], kind: str, checkout: Path) -> str:
    """The report line for one checkout, which has to be there and be of one kind."""
    lines = [
        line
        for line in result.stdout.splitlines()
        if any(token.rstrip(":") == str(checkout) for token in line.split())
    ]
    assert len(lines) == 1, result.stdout + result.stderr
    assert lines[0].split()[0] == kind, lines[0]
    return lines[0]


def test_first_call_runs_the_bootstrap_and_a_second_with_nothing_moved_reads_the_memo(
    tmp_path: Path,
) -> None:
    stand_in = _marking(tmp_path, "sibling")
    listing = _listing(tmp_path, stand_in)

    first = _run(tmp_path, listing)

    assert first.returncode == 0, first.stdout + first.stderr
    ran = _line(first, "ran", stand_in)
    assert _marks(tmp_path) == ["sibling"]
    # The sibling's own output is kept where the line says, outside every worktree.
    log = Path(ran.split("(log: ")[1].rstrip(")"))
    assert log.is_relative_to(tmp_path / "cache")
    assert "setup" in log.read_text(encoding="utf-8")
    assert "1 ran, 0 unchanged, 0 skipped, 0 refused, 0 failed" in first.stdout

    second = _run(tmp_path, listing)

    assert second.returncode == 0, second.stdout + second.stderr
    _line(second, "unchanged", stand_in)
    assert _marks(tmp_path) == ["sibling"]
    assert "0 ran, 1 unchanged" in second.stdout


def test_a_moved_head_or_a_changed_named_input_runs_the_bootstrap_again(
    tmp_path: Path,
) -> None:
    stand_in = _marking(tmp_path, "sibling")
    listing = _listing(tmp_path, stand_in)
    assert _run(tmp_path, listing).returncode == 0
    assert _marks(tmp_path) == ["sibling"]

    (stand_in / "README").write_text("moved\n", encoding="utf-8")
    _commit(stand_in, "move HEAD")
    after_head = _run(tmp_path, listing)

    assert after_head.returncode == 0, after_head.stdout + after_head.stderr
    _line(after_head, "ran", stand_in)
    assert _marks(tmp_path) == ["sibling", "sibling"]

    # An input the recipe body names, edited without a commit: HEAD is unchanged and
    # the bootstrap still has to run, because what it would do has changed.
    (stand_in / "scripts" / "setup.sh").write_text(
        "#!/bin/sh\necho setup again\n", encoding="utf-8"
    )
    after_input = _run(tmp_path, listing)

    assert after_input.returncode == 0, after_input.stdout + after_input.stderr
    _line(after_input, "ran", stand_in)
    assert _marks(tmp_path) == ["sibling", "sibling", "sibling"]

    # The other two spellings a recipe line names a file in are inputs too.
    for name in ("quoted.sh", "tolerated.sh"):
        (stand_in / "scripts" / name).write_text(
            f"#!/bin/sh\necho {name} again\n", encoding="utf-8"
        )
        after_spelling = _run(tmp_path, listing)
        assert after_spelling.returncode == 0, after_spelling.stdout + after_spelling.stderr
        _line(after_spelling, "ran", stand_in)
    assert _marks(tmp_path) == ["sibling"] * 5

    # The justfile itself, edited without a commit and away from the bootstrap body:
    # a recipe the bootstrap could depend on is part of what `just` parses, so the
    # memo reads the whole parsed justfile, not `HEAD` and the body alone.
    with (stand_in / "justfile").open("a", encoding="utf-8") as justfile:
        justfile.write("\ntoolchain:\n    @echo toolchain\n")
    after_justfile = _run(tmp_path, listing)

    assert after_justfile.returncode == 0, after_justfile.stdout + after_justfile.stderr
    _line(after_justfile, "ran", stand_in)
    assert _marks(tmp_path) == ["sibling"] * 6

    # And a file the recipe does not name is not an input: nothing runs for it.
    (stand_in / "notes.txt").write_text("not named by the recipe\n", encoding="utf-8")
    settled = _run(tmp_path, listing)

    assert settled.returncode == 0, settled.stdout + settled.stderr
    _line(settled, "unchanged", stand_in)
    assert _marks(tmp_path) == ["sibling"] * 6


def test_a_bootstrap_that_reads_its_stdin_takes_nothing_from_the_list(tmp_path: Path) -> None:
    """A sibling's bootstrap gets no stdin of the recipe's, so it cannot eat the checkouts after it.

    The recipe walks its list from a descriptor of its own and hands each bootstrap
    `/dev/null`: a bootstrap that drains stdin — a tool prompting, a `cat` — reads
    end-of-file, and the checkout listed after it is still bootstrapped and reported.
    """
    draining = _marking(tmp_path, "draining", before="@cat >/dev/null\n")
    after = _marking(tmp_path, "after")
    listing = _listing(tmp_path, draining, after)

    result = _run(tmp_path, listing)

    assert result.returncode == 0, result.stdout + result.stderr
    _line(result, "ran", draining)
    _line(result, "ran", after)
    assert _marks(tmp_path) == ["draining", "after"]
    assert "2 ran, 0 unchanged, 0 skipped, 0 refused, 0 failed" in result.stdout


def test_two_concurrent_callers_run_one_bootstrap(tmp_path: Path) -> None:
    """The second caller waits on the first's lock and then reads what it recorded."""
    stand_in = _marking(tmp_path, "slow", before=f"@sleep {SLOW_BOOTSTRAP_SECONDS}\n")
    listing = _listing(tmp_path, stand_in)
    environment = _environment(tmp_path)

    callers = [_caller(listing, environment) for _ in range(2)]
    outputs = [caller.communicate(timeout=e2e_timeout(60)) for caller in callers]

    assert [caller.returncode for caller in callers] == [0, 0], outputs
    assert _marks(tmp_path) == ["slow"]
    kinds = sorted(
        next(line.split()[0] for line in stdout.splitlines() if str(stand_in) in line)
        for stdout, _ in outputs
    )
    assert kinds == ["ran", "unchanged"], outputs


def test_a_checkout_that_moves_while_a_caller_waits_is_bootstrapped_as_it_stands(
    tmp_path: Path,
) -> None:
    """A waiting caller fingerprints the checkout once it holds the lock, not before.

    The first caller's bootstrap holds the lock; a second arrives and waits on it; the
    checkout's `HEAD` then moves. What the second caller owes is a bootstrap of the tree
    as it stands when its wait ends — a stamp taken before the wait would equal the one
    the first caller records and read the moved tree as `unchanged`, leaving the
    sibling's gate provisioned for a tree it no longer is.
    """
    stand_in = _held(tmp_path, "held")
    listing = _listing(tmp_path, stand_in)
    release = tmp_path / "release"
    environment = _environment(tmp_path, **{RELEASE_VARIABLE: str(release)})
    before = _short_head(stand_in)

    first = _caller(listing, environment)
    try:
        until(
            "the first caller to be inside the stand-in's bootstrap",
            lambda: _marks(tmp_path) == [before],
            seconds=30,
            state=lambda: f"marks {_marks(tmp_path)}",
        )
        second = _caller(listing, environment)
        try:
            until(
                "the second caller to be waiting on the stand-in's lock",
                lambda: bool(_descendants_named(second.pid, "flock")),
                seconds=30,
                state=lambda: f"marks {_marks(tmp_path)}",
            )
            (stand_in / "README").write_text("moved while a caller waited\n", encoding="utf-8")
            _commit(stand_in, "move HEAD under the waiting caller")
            after = _short_head(stand_in)
            assert after != before
            release.write_text("", encoding="utf-8")
            second_output = second.communicate(timeout=e2e_timeout(60))
        finally:
            second.kill()
        first_output = first.communicate(timeout=e2e_timeout(60))
    finally:
        first.kill()

    assert first.returncode == 0, first_output
    assert second.returncode == 0, second_output
    assert _marks(tmp_path) == [before, after]
    assert (
        next(line.split()[0] for line in first_output[0].splitlines() if str(stand_in) in line)
        == "ran"
    ), first_output
    assert (
        next(line.split()[0] for line in second_output[0].splitlines() if str(stand_in) in line)
        == "ran"
    ), second_output

    # With the moved tree recorded, the next caller has nothing to run.
    settled = _run(tmp_path, listing, **{RELEASE_VARIABLE: str(release)})
    assert settled.returncode == 0, settled.stdout + settled.stderr
    _line(settled, "unchanged", stand_in)
    assert _marks(tmp_path) == [before, after]


def test_a_failing_bootstrap_is_reported_by_name_and_not_memoized(tmp_path: Path) -> None:
    """A sibling whose bootstrap fails is named, its output kept, and it is run again next time."""
    failing = _stand_in(
        tmp_path / "failing",
        bootstrap_body=(
            f'@echo "failing" >> "${MARKS_VARIABLE}"\n@echo "no cargo-deny here" >&2\n@exit 3\n'
        ),
    )
    healthy = _marking(tmp_path, "healthy")
    listing = _listing(tmp_path, failing, healthy)

    first = _run(tmp_path, listing)

    assert first.returncode == 1, first.stdout + first.stderr
    failed = _line(first, "failed", failing)
    assert "exit 3" in failed
    log = Path(failed.split("(log: ")[1].rstrip(")"))
    assert "no cargo-deny here" in log.read_text(encoding="utf-8")
    # The failure did not stop the next checkout.
    _line(first, "ran", healthy)
    assert sorted(_marks(tmp_path)) == ["failing", "healthy"]
    assert "1 ran, 0 unchanged, 0 skipped, 0 refused, 1 failed" in first.stdout

    second = _run(tmp_path, listing)

    assert second.returncode == 1
    _line(second, "failed", failing)
    _line(second, "unchanged", healthy)
    assert sorted(_marks(tmp_path)) == ["failing", "failing", "healthy"]


def test_a_checkout_of_this_repository_is_skipped_under_either_origin_spelling(
    tmp_path: Path,
) -> None:
    """Both spellings a clone carries resolve to this repository; another origin runs."""
    this_origin = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    identity = this_origin.removesuffix("/").removesuffix(".git").split("://", 1)[-1]
    host, _, owner_name = identity.partition("/")
    stand_ins = {
        "https": _stand_in(
            tmp_path / "https",
            bootstrap_body=f'@echo https >> "${MARKS_VARIABLE}"\n',
            origin=f"https://{host}/{owner_name}",
        ),
        "scp": _stand_in(
            tmp_path / "scp",
            bootstrap_body=f'@echo scp >> "${MARKS_VARIABLE}"\n',
            origin=f"git@{host}:{owner_name}.git",
        ),
        "other": _stand_in(
            tmp_path / "other",
            bootstrap_body=f'@echo other >> "${MARKS_VARIABLE}"\n',
            origin=f"https://{host}/{owner_name}-sibling.git",
        ),
    }
    listing = _listing(tmp_path, *stand_ins.values())

    result = _run(tmp_path, listing)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "this repository" in _line(result, "skip", stand_ins["https"])
    assert "this repository" in _line(result, "skip", stand_ins["scp"])
    _line(result, "ran", stand_ins["other"])
    assert _marks(tmp_path) == ["other"]


def test_an_explicit_list_wins_over_the_one_the_environment_names(tmp_path: Path) -> None:
    """The variable is the journey lever for session setup; the flag is the operator's."""
    named = _marking(tmp_path, "named-by-environment")
    passed = _marking(tmp_path, "passed-as-flag")

    result = _run(
        tmp_path,
        _listing(tmp_path, passed),
        ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS=str(_environment_listing(tmp_path, named)),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    _line(result, "ran", passed)
    assert str(named) not in result.stdout
    assert _marks(tmp_path) == ["passed-as-flag"]


def test_the_list_is_read_as_the_registration_recipe_reads_it(tmp_path: Path) -> None:
    """A missing path, this repository, and a checkout with nothing to run are each skipped.

    The stand-in that runs is listed as `~/...` under a `HOME` of this test's own, and
    carries its recipe in a `Justfile`, so the tilde expansion the registration recipe
    performs and the justfile spellings `just` accepts are both driven here.
    """
    home = tmp_path / "home"
    home.mkdir()
    stand_in = _marking(home, "sibling")
    (stand_in / "justfile").rename(stand_in / "Justfile")
    _commit(stand_in, "spell the justfile as Justfile")
    not_a_root = stand_in / "scripts"
    no_recipe = tmp_path / "no-recipe"
    no_recipe.mkdir()
    (no_recipe / "justfile").write_text("check:\n    @true\n", encoding="utf-8")
    _git("init", "-q", cwd=no_recipe)
    no_justfile = tmp_path / "no-justfile"
    no_justfile.mkdir()
    _git("init", "-q", cwd=no_justfile)
    listing = _listing(
        tmp_path,
        "  # a comment line, and a commented entry below",
        f"{tmp_path / 'absent'}  # not on this host",
        REPO_ROOT,
        no_recipe,
        no_justfile,
        not_a_root,
        "~/sibling",
    )

    result = _run(tmp_path, listing, HOME=str(home))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "not on this host" in _line(result, "skip", tmp_path / "absent")
    assert "this repository" in _line(result, "skip", REPO_ROOT)
    assert "no bootstrap recipe" in _line(result, "skip", no_recipe)
    assert "no justfile" in _line(result, "skip", no_justfile)
    assert "not a checkout root" in _line(result, "refused", not_a_root)
    _line(result, "ran", stand_in)
    assert _marks(tmp_path) == ["sibling"]
    assert "1 ran, 0 unchanged, 4 skipped, 1 refused, 0 failed" in result.stdout

    listing.chmod(0o000)
    try:
        unreadable = _run(tmp_path, listing, HOME=str(home))
    finally:
        listing.chmod(0o644)
    assert unreadable.returncode == 2, unreadable.stdout + unreadable.stderr
    assert f"cannot read the checkout list {listing}" in unreadable.stderr
    assert _marks(tmp_path) == ["sibling"]


def test_a_dotted_justfile_is_found_and_an_unborn_head_is_a_state_of_its_own(
    tmp_path: Path,
) -> None:
    """`.justfile` is the third spelling `just` accepts; a checkout with no commit still runs.

    A freshly initialized checkout has no `HEAD` to read, and its bootstrap is as real
    as any other's: it runs, it is memoized under a `HEAD` of its own, and the first
    commit is a move of `HEAD` like any later one, so the bootstrap runs again.
    """
    dotted = _marking(tmp_path, "dotted")
    (dotted / "justfile").rename(dotted / ".justfile")
    _commit(dotted, "spell the justfile as .justfile")
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    (unborn / "justfile").write_text(
        f'bootstrap:\n    @echo "unborn" >> "${MARKS_VARIABLE}"\n', encoding="utf-8"
    )
    _git("init", "-q", "--initial-branch", "main", cwd=unborn)
    listing = _listing(tmp_path, dotted, unborn)

    first = _run(tmp_path, listing)

    assert first.returncode == 0, first.stdout + first.stderr
    _line(first, "ran", dotted)
    _line(first, "ran", unborn)
    assert _marks(tmp_path) == ["dotted", "unborn"]

    second = _run(tmp_path, listing)

    assert second.returncode == 0, second.stdout + second.stderr
    _line(second, "unchanged", dotted)
    _line(second, "unchanged", unborn)
    assert _marks(tmp_path) == ["dotted", "unborn"]

    _commit(unborn, "seed")
    after_first_commit = _run(tmp_path, listing)

    assert after_first_commit.returncode == 0, after_first_commit.stdout + after_first_commit.stderr
    _line(after_first_commit, "unchanged", dotted)
    _line(after_first_commit, "ran", unborn)
    assert _marks(tmp_path) == ["dotted", "unborn", "unborn"]


def test_a_sibling_bootstrap_that_reaches_the_recipe_again_does_not_wait_on_itself(
    tmp_path: Path,
) -> None:
    """A nested call answers at once instead of waiting on the lock its caller holds.

    A sibling's bootstrap can run a session setup of its own, and one that reached this
    recipe again would otherwise wait on the very lock the outer call holds for it.
    """
    nested_report = tmp_path / "nested-report"
    stand_in = _stand_in(
        tmp_path / "recursive",
        bootstrap_body=(
            f'@echo "recursive" >> "${MARKS_VARIABLE}"\n'
            f'@just --justfile "{REPO_ROOT / "justfile"}" --working-directory "{REPO_ROOT}" '
            f'repos-bootstrap --checkouts "$STAND_IN_LISTING" > "{nested_report}"\n'
        ),
    )
    listing = _listing(tmp_path, stand_in)

    result = _run(tmp_path, listing, STAND_IN_LISTING=str(listing))

    assert result.returncode == 0, result.stdout + result.stderr
    _line(result, "ran", stand_in)
    assert _marks(tmp_path) == ["recursive"]
    assert NESTED in nested_report.read_text(encoding="utf-8")


def test_a_justfile_the_recipe_cannot_read_a_bootstrap_out_of_is_refused_by_name(
    tmp_path: Path,
) -> None:
    """A sibling whose justfile does not parse is reported `refused`, and the rest still run."""
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "justfile").write_text("bootstrap:\n    @echo {{ undefined }}\n", encoding="utf-8")
    _git("init", "-q", cwd=broken)
    healthy = _marking(tmp_path, "healthy")
    listing = _listing(tmp_path, broken, healthy)

    result = _run(tmp_path, listing)

    assert result.returncode == 1, result.stdout + result.stderr
    refused = _line(result, "refused", broken)
    assert "unreadable justfile" in refused
    assert "undefined" in refused
    _line(result, "ran", healthy)
    assert _marks(tmp_path) == ["healthy"]
    assert "1 ran, 0 unchanged, 0 skipped, 1 refused, 0 failed" in result.stdout


def test_a_list_or_an_environment_the_recipe_cannot_work_from_provisions_nothing(
    tmp_path: Path,
) -> None:
    """Each refusal names what was wrong before any sibling's bootstrap has run."""
    stand_in = _marking(tmp_path, "sibling")
    listing = _listing(tmp_path, stand_in)

    (tmp_path / "unsupported").mkdir()
    unsupported = _run(tmp_path, _listing(tmp_path / "unsupported", stand_in, "~someone/checkout"))
    assert unsupported.returncode == 2, unsupported.stdout + unsupported.stderr
    assert "unsupported tilde path '~someone/checkout'" in unsupported.stderr

    absent = _run(tmp_path, tmp_path / "no-such-list")
    assert absent.returncode == 2
    assert "does not exist" in absent.stderr

    relative_cache = _run(tmp_path, listing, XDG_CACHE_HOME="relative/cache")
    assert relative_cache.returncode == 2
    assert "XDG_CACHE_HOME must be an absolute path" in relative_cache.stderr

    (tmp_path / "a-file").write_text("not a directory\n", encoding="utf-8")
    unwritable_cache = _run(tmp_path, listing, XDG_CACHE_HOME=str(tmp_path / "a-file" / "cache"))
    assert unwritable_cache.returncode == 2
    assert "cannot create the memo root" in unwritable_cache.stderr

    bad_home = _run(tmp_path, listing, HOME="relative")
    assert bad_home.returncode == 2
    assert "HOME must name an existing absolute directory" in bad_home.stderr

    (tmp_path / "relative").mkdir()
    relative = _run(tmp_path, _listing(tmp_path / "relative", stand_in, "sibling"))
    assert relative.returncode == 2, relative.stdout + relative.stderr
    assert "relative path 'sibling'" in relative.stderr

    assert _marks(tmp_path) == []


def test_the_recipe_answers_its_usage_and_refuses_an_argument_it_does_not_take(
    tmp_path: Path,
) -> None:
    def invoke(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["just", "repos-bootstrap", *arguments],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            env=_environment(tmp_path),
        )

    helped = invoke("--help")
    assert helped.returncode == 0, helped.stdout + helped.stderr
    assert "usage: repos-bootstrap.sh [--checkouts FILE] [--detach]" in helped.stderr

    # The job `--detach` starts is not a way in: run by hand, it refuses a directory
    # that is not a checkout root, and a real checkout without the lock.
    # llmlint: ignore-block[tests_mirror_real_usage] A hand-run `--job` is exactly the
    # misuse these refusals exist for, so the internal entry point is what has to be
    # driven to prove them; no caller reaches them any other way.
    stand_in = _marking(tmp_path, "sibling")
    for checkout, refusal in (
        (tmp_path, "--job takes a checkout root"),
        (stand_in, "--job runs only under the lock"),
    ):
        job = invoke("--job", str(checkout))
        assert job.returncode == 2, job.stdout + job.stderr
        assert refusal in job.stderr
    # Nor is a descriptor 9 holding some other file's lock this checkout's lock.
    elsewhere = subprocess.run(
        [
            "bash",
            "-c",
            'exec 9>>"$1" && flock -n 9 && exec just repos-bootstrap --job "$2"',
            "locked-elsewhere",
            str(tmp_path / "another.lock"),
            str(stand_in),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        env=_environment(tmp_path),
    )
    assert elsewhere.returncode == 2, elsewhere.stdout + elsewhere.stderr
    assert "--job runs only under the lock" in elsewhere.stderr
    assert _marks(tmp_path) == []

    # This checkout's own lock file, opened but not yet locked: while another caller holds
    # it the job refuses, and once it is free the job takes it, as any caller may.
    memo_dir = _log_of(_line(_run(tmp_path, _listing(tmp_path, stand_in)), "ran", stand_in)).parent
    (stand_in / "scripts" / "setup.sh").write_text("#!/bin/sh\necho moved\n", encoding="utf-8")

    def job_on_the_real_lock() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                "-c",
                'exec 9>>"$1" && exec just repos-bootstrap --job "$2"',
                "own-lock",
                str(memo_dir / "lock"),
                str(stand_in),
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            env=_environment(tmp_path),
        )

    release = tmp_path / "release"
    holder = subprocess.Popen(
        [
            "flock",
            str(memo_dir / "lock"),
            "sh",
            "-c",
            'touch "$1.held"; until [ -f "$1" ]; do sleep 0.1; done',
            "holder",
            str(release),
        ]
    )
    try:
        until(
            "the holder to take the lock",
            lambda: Path(f"{release}.held").exists(),
            seconds=30,
            state=lambda: f"holder exit {holder.poll()}",
        )
        contended = job_on_the_real_lock()
    finally:
        release.write_text("", encoding="utf-8")
        holder.wait(timeout=e2e_timeout(30))
    assert contended.returncode == 2, contended.stdout + contended.stderr
    assert "--job runs only under the lock" in contended.stderr

    free = job_on_the_real_lock()
    assert free.returncode == 0, free.stdout + free.stderr
    assert JobState.read(memo_dir).status == "done"
    assert _marks(tmp_path) == ["sibling", "sibling"]
    # llmlint: ignore-end[tests_mirror_real_usage]

    unvalued = invoke("--checkouts")
    assert unvalued.returncode == 2
    assert "usage: repos-bootstrap.sh" in unvalued.stderr

    unknown = invoke("--everything")
    assert unknown.returncode == 2
    assert "unknown argument '--everything'" in unknown.stderr
    assert _marks(tmp_path) == ["sibling", "sibling"]


def test_a_checkout_it_cannot_enter_or_record_is_refused_and_the_rest_still_run(
    tmp_path: Path,
) -> None:
    """A directory this process may not read, and a memo root it may not write into."""
    sealed = _marking(tmp_path, "sealed")
    healthy = _marking(tmp_path, "healthy")
    listing = _listing(tmp_path, sealed, healthy)
    sealed.chmod(0o000)
    try:
        result = _run(tmp_path, listing)
    finally:
        sealed.chmod(0o755)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "unreadable directory" in _line(result, "refused", sealed)
    _line(result, "ran", healthy)
    assert _marks(tmp_path) == ["healthy"]

    # A memo root that exists and takes no new directory: the per-checkout memo cannot
    # be made, so the checkout is refused by name rather than bootstrapped unrecorded.
    memo_root = tmp_path / "cache" / "ai-orchestrator" / "repos-bootstrap"
    memo_root.chmod(0o555)
    try:
        (tmp_path / "unrecorded").mkdir()
        unrecorded = _run(tmp_path, _listing(tmp_path / "unrecorded", sealed))
    finally:
        memo_root.chmod(0o755)

    assert unrecorded.returncode == 1, unrecorded.stdout + unrecorded.stderr
    assert "no memo directory" in _line(unrecorded, "refused", sealed)
    assert _marks(tmp_path) == ["healthy"]


# No journey below signals a job it started: the one killed partway is killed by its
# own bootstrap, and every journey releases and waits out its jobs before it ends.

#: Every status a job's state records, as the script writes it;
#: `tests/test_repos_bootstrap_docs.py` holds this to the script's own vocabulary.
JobStatus = Literal["running", "done", "failed", "stale"]
JOB_ENDINGS: frozenset[JobStatus] = frozenset({"done", "failed", "stale"})


def _is_job_status(value: str) -> TypeIs[JobStatus]:
    return value in get_args(JobStatus)


@dataclass(frozen=True)
class JobState:
    """What one checkout's `state` file records: how its job stands, and its process."""

    status: JobStatus | None = None
    pid: int | None = None
    exit: str | None = None

    @classmethod
    def read(cls, memo_dir: Path) -> JobState:
        state = memo_dir / "state"
        if not state.exists():
            return cls()
        fields = dict(
            row.split(" ", 1) for row in state.read_text(encoding="utf-8").splitlines() if row
        )
        status = fields.get("status")
        assert status is None or _is_job_status(status), fields
        return cls(
            status=status,
            pid=int(fields["pid"]) if "pid" in fields else None,
            exit=fields.get("exit"),
        )

    @property
    def ended(self) -> bool:
        return self.status in JOB_ENDINGS

    @property
    def alive(self) -> bool:
        """Whether the process the job recorded of itself still exists: a read, not a signal."""
        return self.pid is not None and Path(f"/proc/{self.pid}").exists()

    @property
    def stopped(self) -> bool:
        """Whether nothing is left running for this state, however it ended.

        Every job records its process before it bootstraps, so a state with no `pid` is
        one whose job has not yet begun — unless it has ended, when no job wrote it.
        """
        if self.pid is None:
            return self.ended
        return not self.alive


def _detach(tmp_path: Path, listing: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    """One `just repos-bootstrap --detach` over `listing`, bounded well below any bootstrap."""
    return subprocess.run(
        ["just", "repos-bootstrap", "--detach", "--checkouts", str(listing)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        env=_environment(tmp_path, **extra_env),
    )


def _log_of(line: str) -> Path:
    return Path(line.split("(log: ")[1].rstrip(")"))


def _await_ending(memo_dir: Path, *, seconds: float = 60) -> JobState:
    until(
        f"the job for {memo_dir.name} to record how it ended",
        lambda: JobState.read(memo_dir).ended,
        seconds=seconds,
        state=lambda: f"state {JobState.read(memo_dir)}",
    )
    return JobState.read(memo_dir)


def _settle_jobs(tmp_path: Path, release: Path | None = None) -> None:
    """Release every held stand-in and wait until no job this journey started is alive."""
    if release is not None:
        release.write_text("", encoding="utf-8")
    memo_dirs = [state.parent for state in (tmp_path / "cache").rglob("state")]

    until(
        "every job this journey started to stop",
        lambda: all(JobState.read(memo_dir).stopped for memo_dir in memo_dirs),
        seconds=60,
        state=lambda: f"states {[JobState.read(memo_dir) for memo_dir in memo_dirs]}",
    )


def test_a_detached_call_returns_while_its_job_works_and_reports_it_until_complete(
    tmp_path: Path,
) -> None:
    """The call returns at once; the next reports the job running and starts nothing.

    Once the job has ended, the call after it reads the checkout as complete.
    """
    stand_in = _held(tmp_path, "held")
    listing = _listing(tmp_path, stand_in)
    release = tmp_path / "release"
    environment = {RELEASE_VARIABLE: str(release)}
    head = _short_head(stand_in)
    try:
        first = _detach(tmp_path, listing, **environment)

        assert first.returncode == 0, first.stdout + first.stderr
        started = _line(first, "started", stand_in)
        memo_dir = _log_of(started).parent
        assert "1 started, 0 running, 0 stale, 0 unchanged" in first.stdout
        # The call has returned and the job it started is still inside the bootstrap,
        # holding the lock, with no memo claiming a completion it has not reached.
        until(
            "the job to be inside the stand-in's bootstrap",
            lambda: _marks(tmp_path) == [head],
            seconds=30,
            state=lambda: f"marks {_marks(tmp_path)}, state {JobState.read(memo_dir)}",
        )
        state = JobState.read(memo_dir)
        assert state.status == "running", state
        assert state.alive, state
        assert f"job {state.pid}" in started
        assert not (memo_dir / "stamp").exists()

        second = _detach(tmp_path, listing, **environment)

        assert second.returncode == 0, second.stdout + second.stderr
        running = _line(second, "running", stand_in)
        assert f"pid {state.pid}" in running
        assert "0 started, 1 running" in second.stdout
        # The lock was held, so no second job began beside the first.
        assert _marks(tmp_path) == [head]

        release.write_text("", encoding="utf-8")
        ending = _await_ending(memo_dir)
        assert ending.status == "done", ending
        assert (memo_dir / "stamp").exists()
        assert "memo written" in (memo_dir / "bootstrap.log").read_text(encoding="utf-8")

        third = _detach(tmp_path, listing, **environment)

        assert third.returncode == 0, third.stdout + third.stderr
        assert "completed" in _line(third, "unchanged", stand_in)
        assert _marks(tmp_path) == [head]
        _line(_run(tmp_path, listing, **environment), "unchanged", stand_in)
        assert _marks(tmp_path) == [head]
    finally:
        _settle_jobs(tmp_path, release)


def test_a_failed_job_is_reported_with_its_log_and_started_again(tmp_path: Path) -> None:
    failing = _stand_in(
        tmp_path / "failing",
        bootstrap_body=(
            f'@echo "failing" >> "${MARKS_VARIABLE}"\n@echo "no cargo-deny here" >&2\n@exit 3\n'
        ),
    )
    listing = _listing(tmp_path, failing)
    try:
        first = _detach(tmp_path, listing)

        assert first.returncode == 0, first.stdout + first.stderr
        memo_dir = _log_of(_line(first, "started", failing)).parent
        ending = _await_ending(memo_dir)
        assert ending.status == "failed", ending
        assert ending.exit == "3", ending
        assert not (memo_dir / "stamp").exists()

        second = _detach(tmp_path, listing)

        assert second.returncode == 1, second.stdout + second.stderr
        failed = _line(second, "failed", failing)
        assert "exit 3; started again" in failed
        # The log the line names is the failed job's, kept apart from the one the job
        # just started writes.
        kept = _log_of(failed)
        assert kept.name == "bootstrap.failed.log"
        assert "no cargo-deny here" in kept.read_text(encoding="utf-8")
        assert _await_ending(memo_dir).status == "failed"
        assert _marks(tmp_path) == ["failing", "failing"]
    finally:
        _settle_jobs(tmp_path)


def test_a_job_whose_checkout_moves_under_it_is_reported_stale_and_never_trusted(
    tmp_path: Path,
) -> None:
    stand_in = _held(tmp_path, "held")
    listing = _listing(tmp_path, stand_in)
    release = tmp_path / "release"
    environment = {RELEASE_VARIABLE: str(release)}
    before = _short_head(stand_in)
    try:
        first = _detach(tmp_path, listing, **environment)
        memo_dir = _log_of(_line(first, "started", stand_in)).parent
        until(
            "the job to be inside the stand-in's bootstrap",
            lambda: _marks(tmp_path) == [before],
            seconds=30,
            state=lambda: f"marks {_marks(tmp_path)}, state {JobState.read(memo_dir)}",
        )
        (stand_in / "README").write_text("moved under a running job\n", encoding="utf-8")
        _commit(stand_in, "move HEAD under the job")
        after = _short_head(stand_in)

        while_running = _detach(tmp_path, listing, **environment)

        assert while_running.returncode == 0, while_running.stdout + while_running.stderr
        stale = _line(while_running, "stale", stand_in)
        assert "still running" in stale
        assert "0 started, 0 running, 1 stale" in while_running.stdout
        assert _marks(tmp_path) == [before]

        release.write_text("", encoding="utf-8")
        ending = _await_ending(memo_dir)
        # The bootstrap succeeded, of a tree the checkout no longer is: no memo.
        assert ending.status == "stale", ending
        assert not (memo_dir / "stamp").exists()

        after_it_ended = _detach(tmp_path, listing, **environment)

        assert "started again" in _line(after_it_ended, "stale", stand_in)
        assert _await_ending(memo_dir).status == "done"
        assert _marks(tmp_path) == [before, after]
        _line(_detach(tmp_path, listing, **environment), "unchanged", stand_in)
    finally:
        _settle_jobs(tmp_path, release)


def test_a_job_killed_partway_leaves_no_memo_and_is_reported_killed(tmp_path: Path) -> None:
    """A job that never reached its ending is read as killed, and started again.

    The stand-in's first bootstrap kills its own process group — the job and everything
    under it — which is what a host reboot or an operator's kill does to a job, without
    this journey signalling anything.
    """
    once = tmp_path / "killed-once"
    stand_in = _stand_in(
        tmp_path / "killed",
        bootstrap_body=(
            f'@echo "killed" >> "${MARKS_VARIABLE}"\n'
            f'@if [ ! -f "{once}" ]; then touch "{once}"; kill -KILL 0; fi\n'
        ),
    )
    listing = _listing(tmp_path, stand_in)
    try:
        first = _detach(tmp_path, listing)
        memo_dir = _log_of(_line(first, "started", stand_in)).parent
        until(
            "the job to be killed partway through its bootstrap",
            lambda: once.exists() and JobState.read(memo_dir).stopped,
            seconds=30,
            state=lambda: f"state {JobState.read(memo_dir)}",
        )
        assert JobState.read(memo_dir).status == "running"
        assert not (memo_dir / "stamp").exists()

        second = _detach(tmp_path, listing)

        assert second.returncode == 1, second.stdout + second.stderr
        assert "killed before it finished; started again" in _line(second, "failed", stand_in)
        assert _await_ending(memo_dir).status == "done"
        assert (memo_dir / "stamp").exists()
        assert _marks(tmp_path) == ["killed", "killed"]
    finally:
        _settle_jobs(tmp_path)


def test_a_foreground_failure_is_read_by_the_next_detached_call(tmp_path: Path) -> None:
    """A bootstrap run by hand records its state as a job does, and session start reads it."""
    failing = _stand_in(
        tmp_path / "failing",
        bootstrap_body=f'@echo "failing" >> "${MARKS_VARIABLE}"\n@echo "by hand" >&2\n@exit 4\n',
    )
    listing = _listing(tmp_path, failing)

    by_hand = _run(tmp_path, listing)

    assert by_hand.returncode == 1, by_hand.stdout + by_hand.stderr
    memo_dir = _log_of(_line(by_hand, "failed", failing)).parent
    recorded = JobState.read(memo_dir)
    assert (recorded.status, recorded.exit) == ("failed", "4"), recorded
    try:
        detached = _detach(tmp_path, listing)

        assert detached.returncode == 1, detached.stdout + detached.stderr
        failed = _line(detached, "failed", failing)
        assert "exit 4; started again" in failed
        assert "by hand" in _log_of(failed).read_text(encoding="utf-8")
        assert _await_ending(memo_dir).status == "failed"
        assert _marks(tmp_path) == ["failing", "failing"]
    finally:
        _settle_jobs(tmp_path)


def test_a_state_file_in_a_shape_the_script_never_writes_is_read_as_absent(
    tmp_path: Path,
) -> None:
    """A state edited or truncated by hand is neither trusted nor echoed into a report."""
    stand_in = _marking(tmp_path, "sibling")
    listing = _listing(tmp_path, stand_in)
    try:
        memo_dir = _log_of(_line(_detach(tmp_path, listing), "started", stand_in)).parent
        assert _await_ending(memo_dir).status == "done"
        # The checkout moves, so the next call has a job to start, and the state the
        # last job left is replaced by one no job writes: an unknown status carrying
        # an escape sequence, and a process id that is not a number.
        (stand_in / "scripts" / "setup.sh").write_text("#!/bin/sh\necho moved\n", encoding="utf-8")
        # llmlint: ignore-block[tests_mirror_real_usage] A state in a shape the script
        # never writes exists only because something else wrote it — an edit by hand, a
        # truncated disk — so this journey has to be that something; the call it then
        # makes is the recipe's own.
        (memo_dir / "state").write_text(
            "status \x1b[31mfailed\npid ../1\nexit x\n", encoding="utf-8"
        )
        # llmlint: ignore-end[tests_mirror_real_usage]

        result = _detach(tmp_path, listing)

        assert result.returncode == 0, result.stdout + result.stderr
        _line(result, "started", stand_in)
        assert "\x1b" not in result.stdout
        assert _await_ending(memo_dir).status == "done"
        assert _marks(tmp_path) == ["sibling", "sibling"]

        # A failed ending that lost its exit status is still a failure, and says so.
        (stand_in / "scripts" / "setup.sh").write_text("#!/bin/sh\necho again\n", encoding="utf-8")
        # llmlint: ignore-block[tests_mirror_real_usage] The same hand-edited state as above.
        (memo_dir / "state").write_text("status failed\n", encoding="utf-8")
        # llmlint: ignore-end[tests_mirror_real_usage]

        unrecorded = _detach(tmp_path, listing)

        assert unrecorded.returncode == 1, unrecorded.stdout + unrecorded.stderr
        assert "exit unrecorded; started again" in _line(unrecorded, "failed", stand_in)
        assert _await_ending(memo_dir).status == "done"

        # A completion time in no shape the script writes is left off the report.
        # llmlint: ignore-block[tests_mirror_real_usage] The same hand-edited state as above.
        (memo_dir / "state").write_text("status done\nfinished yesterday\n", encoding="utf-8")
        # llmlint: ignore-end[tests_mirror_real_usage]
        unchanged = _line(_detach(tmp_path, listing), "unchanged", stand_in)
        assert "completed" in unchanged
        assert "yesterday" not in unchanged
    finally:
        _settle_jobs(tmp_path)


def test_a_detached_call_reports_a_bootstrap_run_by_hand_as_running(tmp_path: Path) -> None:
    """A caller by hand holding the lock is reported from its state, and nothing joins it."""
    stand_in = _held(tmp_path, "held")
    listing = _listing(tmp_path, stand_in)
    release = tmp_path / "release"
    environment = _environment(tmp_path, **{RELEASE_VARIABLE: str(release)})
    head = _short_head(stand_in)

    by_hand = _caller(listing, environment)
    try:
        until(
            "the caller by hand to be inside the stand-in's bootstrap",
            lambda: _marks(tmp_path) == [head],
            seconds=30,
            state=lambda: f"marks {_marks(tmp_path)}",
        )

        detached = _detach(tmp_path, listing, **{RELEASE_VARIABLE: str(release)})

        assert detached.returncode == 0, detached.stdout + detached.stderr
        assert "pid " in _line(detached, "running", stand_in)
        assert "0 started, 1 running" in detached.stdout
        release.write_text("", encoding="utf-8")
        output = by_hand.communicate(timeout=e2e_timeout(60))
    finally:
        release.write_text("", encoding="utf-8")
        by_hand.kill()

    assert by_hand.returncode == 0, output
    assert "completed" in _line(_detach(tmp_path, listing), "unchanged", stand_in)
    assert _marks(tmp_path) == [head]


def test_a_log_or_state_the_detached_call_cannot_write_is_refused_and_nothing_starts(
    tmp_path: Path,
) -> None:
    """A read-only log, and a memo directory no file can be created in, each refuse by name."""
    stand_in = _marking(tmp_path, "sibling")
    listing = _listing(tmp_path, stand_in)
    try:
        memo_dir = _log_of(_line(_detach(tmp_path, listing), "started", stand_in)).parent
        assert _await_ending(memo_dir).status == "done"
        (stand_in / "scripts" / "setup.sh").write_text("#!/bin/sh\necho moved\n", encoding="utf-8")
        log = memo_dir / "bootstrap.log"

        # llmlint: ignore-block[tests_mirror_real_usage] A log or memo directory this
        # process may not write is a host's permissions, not something the recipe's
        # interface can be asked to produce, so the journey sets the permissions itself —
        # as the module's sealed checkout and unwritable memo root do — and then makes
        # the recipe's own call.
        log.chmod(0o400)
        try:
            read_only_log = _detach(tmp_path, listing)
        finally:
            log.chmod(0o600)
        assert read_only_log.returncode == 1, read_only_log.stdout + read_only_log.stderr
        assert "unwritable log" in _line(read_only_log, "refused", stand_in)

        memo_dir.chmod(0o500)
        try:
            read_only_memo = _detach(tmp_path, listing)
        finally:
            memo_dir.chmod(0o700)
        assert read_only_memo.returncode == 1, read_only_memo.stdout + read_only_memo.stderr
        assert "unwritable state" in _line(read_only_memo, "refused", stand_in)
        # llmlint: ignore-end[tests_mirror_real_usage]
        assert _marks(tmp_path) == ["sibling"]
        _line(_detach(tmp_path, listing), "started", stand_in)
        assert _await_ending(memo_dir).status == "done"
        assert _marks(tmp_path) == ["sibling", "sibling"]
    finally:
        _settle_jobs(tmp_path)


def test_a_detached_job_for_an_unborn_checkout_is_memoized_like_any_other(
    tmp_path: Path,
) -> None:
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    (unborn / "justfile").write_text(
        f'bootstrap:\n    @echo "unborn" >> "${MARKS_VARIABLE}"\n', encoding="utf-8"
    )
    _git("init", "-q", "--initial-branch", "main", cwd=unborn)
    listing = _listing(tmp_path, unborn)
    try:
        memo_dir = _log_of(_line(_detach(tmp_path, listing), "started", unborn)).parent
        assert _await_ending(memo_dir).status == "done"
        assert "at no-commit" in (memo_dir / "bootstrap.log").read_text(encoding="utf-8")

        assert "completed" in _line(_detach(tmp_path, listing), "unchanged", unborn)
        assert _marks(tmp_path) == ["unborn"]
    finally:
        _settle_jobs(tmp_path)


def test_a_job_that_cannot_write_its_memo_is_run_again_by_the_next_call(tmp_path: Path) -> None:
    """Provisioned and recorded nowhere: the safe side, which the next call corrects."""
    stand_in = _marking(tmp_path, "sibling")
    listing = _listing(tmp_path, stand_in)
    try:
        memo_dir = _log_of(_line(_detach(tmp_path, listing), "started", stand_in)).parent
        assert _await_ending(memo_dir).status == "done"
        (stand_in / "scripts" / "setup.sh").write_text("#!/bin/sh\necho moved\n", encoding="utf-8")
        stamp = memo_dir / "stamp"
        recorded = stamp.read_text(encoding="utf-8")

        # llmlint: ignore-block[tests_mirror_real_usage] A memo this process may not
        # write is a host's permissions, not something the recipe's interface can be
        # asked to produce, so the journey sets them itself and makes the recipe's call.
        stamp.chmod(0o400)
        try:
            _line(_detach(tmp_path, listing), "started", stand_in)
            assert _await_ending(memo_dir).status == "done"
        finally:
            stamp.chmod(0o600)
        # llmlint: ignore-end[tests_mirror_real_usage]
        assert stamp.read_text(encoding="utf-8") == recorded
        assert "memo not written" in (memo_dir / "bootstrap.log").read_text(encoding="utf-8")

        _line(_detach(tmp_path, listing), "started", stand_in)
        assert _await_ending(memo_dir).status == "done"
        assert "completed" in _line(_detach(tmp_path, listing), "unchanged", stand_in)
        assert _marks(tmp_path) == ["sibling"] * 3
    finally:
        _settle_jobs(tmp_path)


def test_a_head_naming_a_commit_git_cannot_read_is_refused_either_way(tmp_path: Path) -> None:
    """A checkout whose `HEAD` is a missing commit has no tree to fingerprint or bootstrap."""
    broken = _marking(tmp_path, "broken")
    # llmlint: ignore-block[tests_mirror_real_usage] A `HEAD` or branch naming a commit
    # the repository lacks is damage no git command will produce — `update-ref` refuses a
    # missing object — so the journey writes the ref file as the damage would, and then
    # makes the recipe's own calls.
    (broken / ".git" / "HEAD").write_text("1" * 40 + "\n", encoding="utf-8")
    # And a `HEAD` still on its branch, where the branch names the missing commit: not a
    # branch with no commit yet, which is a state of its own.
    broken_branch = _marking(tmp_path, "broken-branch")
    (broken_branch / ".git" / "refs" / "heads" / "main").write_text(
        "1" * 40 + "\n", encoding="utf-8"
    )
    # llmlint: ignore-end[tests_mirror_real_usage]
    healthy = _marking(tmp_path, "healthy")
    listing = _listing(tmp_path, broken, broken_branch, healthy)
    try:
        by_hand = _run(tmp_path, listing)

        assert by_hand.returncode == 1, by_hand.stdout + by_hand.stderr
        assert "unreadable HEAD" in _line(by_hand, "refused", broken)
        assert "unreadable HEAD" in _line(by_hand, "refused", broken_branch)
        _line(by_hand, "ran", healthy)

        (healthy / "scripts" / "setup.sh").write_text("#!/bin/sh\necho moved\n", encoding="utf-8")
        detached = _detach(tmp_path, listing)

        assert detached.returncode == 1, detached.stdout + detached.stderr
        assert "unreadable HEAD" in _line(detached, "refused", broken)
        assert "unreadable HEAD" in _line(detached, "refused", broken_branch)
        assert _await_ending(_log_of(_line(detached, "started", healthy)).parent).status == "done"
        assert _marks(tmp_path) == ["healthy", "healthy"]
    finally:
        _settle_jobs(tmp_path)


def test_a_lock_held_with_no_state_recorded_is_reported_held_by_another_caller(
    tmp_path: Path,
) -> None:
    """A holder that records nothing — a caller from before job state — is still never joined."""
    stand_in = _marking(tmp_path, "sibling")
    listing = _listing(tmp_path, stand_in)
    release = tmp_path / "release"
    memo_dir = _log_of(_line(_run(tmp_path, listing), "ran", stand_in)).parent
    (stand_in / "scripts" / "setup.sh").write_text("#!/bin/sh\necho moved\n", encoding="utf-8")
    # llmlint: ignore-block[tests_mirror_real_usage] The holder is a caller that writes no
    # state — one from before this script kept any — so the journey is that caller: the
    # real `flock` on the checkout's real lock file, and nothing else of the script's. The
    # state it leaves is one no job writes, a process and a `HEAD` in no shape the script
    # does, which is read as no state at all.
    (memo_dir / "state").write_text("status running\npid abc\nhead zzz\n", encoding="utf-8")
    holder = subprocess.Popen(
        [
            "flock",
            str(memo_dir / "lock"),
            "sh",
            "-c",
            'touch "$1.held"; until [ -f "$1" ]; do sleep 0.1; done',
            "holder",
            str(release),
        ]
    )
    # llmlint: ignore-end[tests_mirror_real_usage]
    try:
        until(
            "the holder to take the lock",
            lambda: Path(f"{release}.held").exists(),
            seconds=30,
            state=lambda: f"holder exit {holder.poll()}",
        )

        detached = _detach(tmp_path, listing)

        assert detached.returncode == 0, detached.stdout + detached.stderr
        held = _line(detached, "running", stand_in)
        assert "another caller" in held
        assert "abc" not in held
        assert _marks(tmp_path) == ["sibling"]
    finally:
        release.write_text("", encoding="utf-8")
        holder.wait(timeout=e2e_timeout(30))


def test_a_checkout_whose_head_breaks_under_a_running_job_is_refused_and_ends_stale(
    tmp_path: Path,
) -> None:
    stand_in = _held(tmp_path, "held")
    listing = _listing(tmp_path, stand_in)
    release = tmp_path / "release"
    environment = {RELEASE_VARIABLE: str(release)}
    try:
        memo_dir = _log_of(
            _line(_detach(tmp_path, listing, **environment), "started", stand_in)
        ).parent
        until(
            "the job to be inside the stand-in's bootstrap",
            lambda: bool(_marks(tmp_path)),
            seconds=30,
            state=lambda: f"state {JobState.read(memo_dir)}",
        )
        # llmlint: ignore-block[tests_mirror_real_usage] Damage no git command produces,
        # as in the journey over a `HEAD` naming a missing commit.
        (stand_in / ".git" / "HEAD").write_text("1" * 40 + "\n", encoding="utf-8")
        # llmlint: ignore-end[tests_mirror_real_usage]

        broken = _detach(tmp_path, listing, **environment)

        assert broken.returncode == 1, broken.stdout + broken.stderr
        refused = _line(broken, "refused", stand_in)
        assert "unreadable HEAD" in refused and "still running" in refused
        release.write_text("", encoding="utf-8")
        assert _await_ending(memo_dir).status == "stale"
        assert not (memo_dir / "stamp").exists()
    finally:
        _settle_jobs(tmp_path, release)
