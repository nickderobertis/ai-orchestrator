"""`just recoverable` prints resume commands somebody can paste and get a body.

`onevcs recoverable` writes a `Resume:` line per preserved branch, and that line exists
to be pasted. What it renders is its own argv — `onevcs publish-branch <BRANCH> --repo
<PATH>` — and on this host pasting that is the one way to land a branch whose change
request then opens with an empty description: drafting reaches `onevcs` through the
`just` recipes and nowhere else. This manager copied one of those lines during the
session that produced this work, which is how the trap was found.

So the recipe re-renders each resume command in its `just` form and passes every other
line of the report through untouched. Both halves matter and both are asserted here
against the real report: a rewrite that also reformatted the listing would make this
host's inventory disagree with the one `onevcs recoverable` prints everywhere else, and
a rewrite that touched `--json` would corrupt the `recover_command` other consumers
read.

Everything is real — the recipe, the wrapper, `onevcs`, git, and a throwaway origin.
What makes it safe is isolation rather than substitution: `ONEVCS_HOME` points at a
scratch registry and the repository is a throwaway checkout of a throwaway bare origin,
so nothing this host has registered is read.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import NamedTuple

from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The base every branch here is preserved against.
BASE = "main"

#: A finished branch no session holds, which `onevcs` resumes with `publish-branch`.
COMPLETE_BRANCH = "claude/finished-work"

#: A branch carrying an unattested incomplete-step marker, which `onevcs` resumes with
#: `recover` — the one verb whose `just` recipe is named differently, and so the one
#: rewrite a reader would get wrong by pasting.
INCOMPLETE_BRANCH = "claude/interrupted-work"

#: What marks that branch's last commit as work a step did not finish, in the two ways
#: `onevcs` recognizes one: the subject suffix, and the trailer under this host's own
#: configured prefix.
INCOMPLETE_SUBJECT = "feat: land half the work (incomplete step)"
INCOMPLETE_TRAILER = "Orchestrator-Status: incomplete"

#: How `onevcs` renders each resume command, and how the recipe must render it instead.
#: Per landing verb, because the recipe name differs from the published verb for
#: `recover` alone and a rewrite that mapped every verb to its own name would pass a
#: test written only against `publish-branch`.
REWRITES = {
    "onevcs publish-branch": "just publish-branch",
    "onevcs recover": "just repo-recover",
}


class Registry(NamedTuple):
    """One throwaway identity holding both preserved branch states."""

    #: The registered publication checkout the branches are found in.
    checkout: Path
    #: The environment carrying `ONEVCS_HOME`, which is what makes this isolated.
    environment: dict[str, str]


def _git(*arguments: str, cwd: Path) -> str:
    """Run git for real, failing loudly."""
    done = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert done.returncode == 0, f"git {' '.join(arguments)}: {done.stderr or done.stdout}"
    return done.stdout


def _run(*arguments: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run one command from this checkout, against the scratch registry."""
    return subprocess.run(
        arguments,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _registry(tmp_path: Path) -> Registry:
    """A registered identity holding one complete and one incomplete preserved branch."""
    home = tmp_path / "onevcs-home"
    home.mkdir()
    (home / "rules.yml").write_text(
        "version: 2\n"
        "trailer_prefix: Orchestrator-\n"
        "rules: []\n"
        "default:\n"
        "  publication: local-direct\n"
        "  approvals: none\n"
        "  gate:\n"
        '    command: ["true"]\n',
        encoding="utf-8",
    )
    seed = tmp_path / "seed"
    _git("init", "-q", "-b", BASE, str(seed), cwd=tmp_path)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "init", cwd=seed)
    origin = tmp_path / "origin.git"
    _git("clone", "-q", "--bare", str(seed), str(origin), cwd=tmp_path)
    checkout = tmp_path / "checkout"
    _git("clone", "-q", str(origin), str(checkout), cwd=tmp_path)

    _git("checkout", "-q", "-b", COMPLETE_BRANCH, cwd=checkout)
    (checkout / "shipped.txt").write_text("the work\n", encoding="utf-8")
    _git("add", "-A", cwd=checkout)
    _git("commit", "-q", "-m", "feat: finish the work", cwd=checkout)

    _git("checkout", "-q", BASE, cwd=checkout)
    _git("checkout", "-q", "-b", INCOMPLETE_BRANCH, cwd=checkout)
    (checkout / "half.txt").write_text("half the work\n", encoding="utf-8")
    _git("add", "-A", cwd=checkout)
    _git("commit", "-q", "-m", f"{INCOMPLETE_SUBJECT}\n\n{INCOMPLETE_TRAILER}\n", cwd=checkout)
    # Back on the base, which is the state a publication checkout is kept in.
    _git("checkout", "-q", BASE, cwd=checkout)

    environment = dict(os.environ)
    environment["ONEVCS_HOME"] = str(home)
    registered = _run("just", "register-repo", str(checkout), environment=environment)
    assert registered.returncode == 0, registered.stderr + registered.stdout
    return Registry(checkout, environment)


def _resume_lines(report: str) -> list[str]:
    """Every resume command the report offers, as the operator reads them."""
    return [line.strip() for line in report.splitlines() if line.strip().startswith("Resume:")]


def test_recoverable_prints_each_resume_command_in_its_drafting_form(
    tmp_path: Path,
) -> None:
    """Both landing verbs are offered as the `just` recipe that drafts a body.

    The claim is per verb rather than about the listing as a whole: `recover` is the
    one whose recipe is named differently, so a rewrite that only stripped `onevcs`
    would offer `just recover`, which is not a recipe at all.
    """
    registry = _registry(tmp_path)

    listed = _run("just", "recoverable", environment=registry.environment)

    assert listed.returncode == 0, listed.stderr + listed.stdout
    offered = _resume_lines(listed.stdout)
    assert offered, f"the listing offered no resume command at all:\n{listed.stdout}"
    assert f"Resume: just publish-branch {COMPLETE_BRANCH} --repo {registry.checkout}" in offered, (
        f"the complete branch is not offered as the recipe that drafts a body:\n{offered}"
    )
    assert f"Resume: just repo-recover {INCOMPLETE_BRANCH} --repo {registry.checkout}" in offered, (
        f"the incomplete branch is not offered as the recipe that drafts a body:\n{offered}"
    )
    assert not any("onevcs" in line for line in offered), (
        f"a raw `onevcs` invocation is still offered to be pasted:\n{offered}"
    )


def test_recoverable_changes_nothing_about_the_report_but_the_resume_commands(
    tmp_path: Path,
) -> None:
    """Every other line is `onevcs`'s own, unmoved.

    This report is another repository's output, and other people read it: an operator
    comparing this host's inventory with one taken anywhere else has to be reading the
    same listing. So the recipe's output is held against the published command's, line
    for line, with the rewrite applied — which fails a wrapper that reflowed a row,
    dropped the scope header, or reordered the branches.
    """
    registry = _registry(tmp_path)

    published = _run("uv", "run", "onevcs", "recoverable", environment=registry.environment)
    listed = _run("just", "recoverable", environment=registry.environment)

    assert published.returncode == 0, published.stderr + published.stdout
    assert listed.returncode == 0, listed.stderr + listed.stdout
    expected = published.stdout
    for published_verb, recipe in REWRITES.items():
        expected = expected.replace(f"Resume: {published_verb} ", f"Resume: {recipe} ")
    assert listed.stdout == expected, (
        "the recipe's listing is not `onevcs`'s own report with the resume commands "
        f"rewritten:\n{listed.stdout}\n--- expected ---\n{expected}"
    )
    # And the rewrite really had something to do, so the comparison above is not two
    # identical reports agreeing about nothing.
    assert expected != published.stdout, (
        f"no resume command was rewritten at all:\n{published.stdout}"
    )


def test_recoverable_passes_the_machine_readable_report_through_untouched(
    tmp_path: Path,
) -> None:
    """`--json` is `onevcs`'s answer, `recover_command` included.

    That field is the argv other consumers read, and rewriting it into a command only
    this checkout has would be a lie about what `onevcs` reports — a caller running it
    somewhere else would be handed a `just` that is not there.
    """
    registry = _registry(tmp_path)

    published = _run(
        "uv", "run", "onevcs", "recoverable", "--json", environment=registry.environment
    )
    listed = _run("just", "recoverable", "--json", environment=registry.environment)

    assert listed.returncode == 0, listed.stderr + listed.stdout
    assert listed.stdout == published.stdout, (
        f"the machine-readable report was rewritten:\n{listed.stdout}"
    )
    rows = json.loads(listed.stdout[listed.stdout.index("[") :])
    assert [row["recover_command"][:2] for row in rows] == [
        ["onevcs", "publish-branch"],
        ["onevcs", "recover"],
    ] or [row["recover_command"][:2] for row in rows] == [
        ["onevcs", "recover"],
        ["onevcs", "publish-branch"],
    ], f"the published argv is not what the rewrite is anchored on:\n{rows}"
