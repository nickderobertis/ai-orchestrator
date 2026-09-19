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
from pathlib import Path

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
    assert "usage: repos-bootstrap.sh [--checkouts FILE]" in helped.stderr

    unvalued = invoke("--checkouts")
    assert unvalued.returncode == 2
    assert "usage: repos-bootstrap.sh" in unvalued.stderr

    unknown = invoke("--everything")
    assert unknown.returncode == 2
    assert "unknown argument '--everything'" in unknown.stderr
    assert _marks(tmp_path) == []


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
