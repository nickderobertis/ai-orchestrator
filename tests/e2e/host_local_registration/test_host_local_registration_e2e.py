"""A host's own checkout list and rules overlay are what session startup's selection reads.

A host that also works in private repositories keeps their checkouts and rules outside
every checkout: a complete checkout list at
`${XDG_CONFIG_HOME:-$HOME/.config}/ai-orchestrator/onevcs.checkouts`, which replaces the
tracked `config/onevcs.checkouts` whole, and a rules overlay at
`.../ai-orchestrator/onevcs.rules.yml`, which `onevcs rules apply` composes over the
tracked `config/onevcs.rules.yml`. Nothing names either file to the recipes: session
startup runs `just repos-bootstrap --detach` with no list, and an operator runs `just
repos-apply` with none, so the selection is the recipes' own and is what is driven here.

So the journey sandboxes `HOME` and `XDG_CONFIG_HOME` around a complete host-local list
and overlay over synthetic identities, runs both recipes exactly as session startup
invokes them — `just --justfile <root>/justfile --working-directory <root> ...`, with no
checkouts argument and `ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS` removed from the
environment — and reads what they did back through the installed `onevcs` and the marks
each stand-in's own `just bootstrap` leaves. Then it removes the override and runs the
same invocations again, which must select the tracked list instead.

llmlint: ignore-file[e2e_not_mocked] The stand-in checkouts are real git repositories
under a scratch `HOME`, standing in for this host's own clones, which a test may not
register or bootstrap; the recipes, the scripts, `onevcs`, `just` and git are all real.

It runs in the `host-local-registration` project of its own, keyed on the registration and
bootstrap recipes, the scripts they run and the files they read, apart from the offline
recipe journeys.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
from waits import timeout as e2e_timeout
from waits import until

from orchestrator.root import REPO_ROOT

#: The variable each stand-in's bootstrap appends its name through, outside the checkout.
MARKS_VARIABLE = "STAND_IN_MARKS"
#: The variable a journey could name a list through; session startup names none, and the
#: dispatch environment a test runs in may carry one, so it is removed rather than set.
CHECKOUTS_VARIABLE = "ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS"
#: Where both recipes look for the host's own files under its XDG configuration home.
HOST_CHECKOUTS = Path("ai-orchestrator") / "onevcs.checkouts"
HOST_RULES = Path("ai-orchestrator") / "onevcs.rules.yml"


class Policy(NamedTuple):
    """What the overlay declares for one identity, and the rule position it holds there."""

    publication: str
    approvals: str
    position: int


class Checkout(NamedTuple):
    """One checkout a journey stands in: its identity, its path under the sandboxed `HOME`,
    and, for a private one, the policy the overlay declares for it."""

    identity: str
    path: str
    policy: Policy | None = None


#: The synthetic private identities the host's list and overlay name. Neither tracked file
#: names either.
QUIETHARBOR = "github.com/hiddenco/quietharbor"
PEBBLECRAFT = "github.com/nickderobertis/pebblecraft"
PRIVATE = {
    QUIETHARBOR: Checkout(
        QUIETHARBOR, "projects/quietharbor", Policy("change-open", "required", 1)
    ),
    PEBBLECRAFT: Checkout(PEBBLECRAFT, "projects/pebblecraft", Policy("local-direct", "none", 2)),
}
#: A public checkout the host's list repeats from the tracked list, at the spelling the
#: tracked list gives it, so the composed rules route it by its tracked rule.
REPEATED = Checkout(
    "github.com/nickderobertis/crozier", ".ai-orchestrator/repos/nickderobertis__crozier"
)
#: A tracked-list entry the host's list omits: present on disk, and neither registered nor
#: bootstrapped while the override is there.
OMITTED = Checkout("github.com/nickderobertis/onevcs", "projects/onevcs")

HOST_RULES_OVERLAY = (
    "version: 4\n"
    "rules:\n"
    "  - match: {host: github.com, owner: hiddenco, name: quietharbor}\n"
    "    publication: change-open\n"
    "    approvals: required\n"
    "    visibility: private\n"
    "  - match: {host: github.com, owner: nickderobertis, name: pebblecraft}\n"
    "    publication: local-direct\n"
    "    approvals: none\n"
    "    visibility: private\n"
)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _stand_in(home: Path, relative: str, identity: str) -> Path:
    """A committed checkout of ``identity`` at ``~/relative`` whose bootstrap leaves a mark."""
    checkout = home / relative
    checkout.mkdir(parents=True)
    (checkout / "justfile").write_text(
        f'bootstrap:\n    @echo "{checkout.name}" >> "${MARKS_VARIABLE}"\n', encoding="utf-8"
    )
    _git("init", "-q", "--initial-branch", "main", cwd=checkout)
    _git("remote", "add", "origin", f"https://{identity}.git", cwd=checkout)
    _git("add", "-A", cwd=checkout)
    _git(
        "-c",
        "user.name=Journey",
        "-c",
        "user.email=journey@example.invalid",
        "commit",
        "-q",
        "-m",
        "seed",
        cwd=checkout,
    )
    return checkout


class Host:
    """A sandboxed host: its `HOME`, configuration and cache homes, and its stand-ins.

    ``xdg`` is how the host spells its configuration home: an absolute `XDG_CONFIG_HOME`
    (``None``), none at all (``"unset"``), or a relative one the recipes must ignore for
    `$HOME/.config`; in the last two the host's files sit under `$HOME/.config`.
    """

    def __init__(self, root: Path, xdg: str | None = None) -> None:
        self.home = root / "home"
        self.xdg = xdg
        self.config = root / "xdg" if xdg is None else self.home / ".config"
        self.cache = root / "cache"
        self.marks = root / "marks"
        for directory in (self.home, self.config, self.cache):
            directory.mkdir(parents=True)
        self.private = {
            identity: _stand_in(self.home, relative, identity)
            for identity, (_, relative, _) in PRIVATE.items()
        }
        self.repeated = _stand_in(self.home, REPEATED.path, REPEATED.identity)
        self.omitted = _stand_in(self.home, OMITTED.path, OMITTED.identity)
        (self.config / HOST_RULES).parent.mkdir(parents=True)
        (self.config / HOST_RULES).write_text(HOST_RULES_OVERLAY, encoding="utf-8")
        (self.config / HOST_CHECKOUTS).write_text(
            "# This host's complete list: its private checkouts and the public one it keeps.\n"
            + "".join(f"~/{checkout.path}\n" for checkout in PRIVATE.values())
            + f"~/{REPEATED.path}\n",
            encoding="utf-8",
        )

    @property
    def environment(self) -> dict[str, str]:
        """What session startup would run under on this host, and nothing the test chose.

        `ONEVCS_HOME` is removed with the list variable, so the registry is the default one
        under the sandboxed `HOME`, as it is for a session that names none.
        """
        inherited = {
            name: value
            for name, value in os.environ.items()
            if name not in {CHECKOUTS_VARIABLE, "ONEVCS_HOME", "XDG_CONFIG_HOME"}
        }
        if self.xdg is None:
            inherited["XDG_CONFIG_HOME"] = str(self.config)
        elif self.xdg != "unset":
            inherited["XDG_CONFIG_HOME"] = self.xdg
        return {
            **inherited,
            "HOME": str(self.home),
            "XDG_CACHE_HOME": str(self.cache),
            MARKS_VARIABLE: str(self.marks),
        }

    @property
    def registry(self) -> Path:
        return self.home / ".onevcs"

    def recipe(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        """One recipe, invoked the way `scripts/session-setup.sh` invokes `repos-bootstrap`."""
        return subprocess.run(
            [
                "just",
                "--justfile",
                str(REPO_ROOT / "justfile"),
                "--working-directory",
                str(REPO_ROOT),
                *arguments,
            ],
            cwd=self.home,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(600),
            env=self.environment,
        )

    def onevcs(self, *arguments: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        """The installed `onevcs`, reading the registry under the sandboxed `HOME`."""
        return subprocess.run(
            ["uv", "run", "--project", str(REPO_ROOT), "onevcs", *arguments],
            cwd=REPO_ROOT,
            text=True,
            input=stdin,
            capture_output=True,
            timeout=e2e_timeout(120),
            env=self.environment,
        )

    def registered(self) -> tuple[set[str], set[str]]:
        """The identities and checkout paths the registry under the sandboxed `HOME` holds."""
        document = json.loads((self.registry / "registry.json").read_text(encoding="utf-8"))
        return set(document["identities"]), {
            record["path"] for record in document["checkouts"].values()
        }

    def marked(self) -> list[str]:
        return sorted(self.marks.read_text(encoding="utf-8").split()) if self.marks.exists() else []

    def bootstrap_until_settled(self, expected: set[Path]) -> subprocess.CompletedProcess[str]:
        """Session startup's `repos-bootstrap --detach`, repeated until its jobs have ended.

        The first call starts a detached job per due checkout and returns; a later call by
        the same invocation reports each one `running` until it ends and `unchanged` once
        it has completed, so it is re-asked until every expected checkout reads
        `unchanged`, which also leaves no job of this journey running past it.
        """
        first = self.recipe("repos-bootstrap", "--detach")
        assert first.returncode == 0, first.stdout + first.stderr
        for checkout in expected:
            assert _kind(first, checkout) == "started", first.stdout + first.stderr
        latest = [first]

        def settled() -> bool:
            again = self.recipe("repos-bootstrap", "--detach")
            assert again.returncode == 0, again.stdout + again.stderr
            latest.append(again)
            return all(_kind(again, checkout) == "unchanged" for checkout in expected)

        until(
            "every detached bootstrap this journey started to complete",
            settled,
            seconds=120,
            state=lambda: latest[-1].stdout + latest[-1].stderr,
        )
        return first


def _kind(result: subprocess.CompletedProcess[str], checkout: Path) -> str | None:
    """The first word of the report line naming ``checkout``, or `None` when none does."""
    lines = [
        line
        for line in result.stdout.splitlines()
        if any(token.rstrip(":") == str(checkout) for token in line.split())
    ]
    assert len(lines) <= 1, result.stdout
    return lines[0].split()[0] if lines else None


def _reported(output: str, key: str) -> str:
    """One field of `onevcs rules check`, without its `(from rule 1)` annotation."""
    for line in output.splitlines():
        name, separator, value = line.partition(": ")
        if separator and name.strip() == key:
            return value.split(" (from ")[0].strip()
    raise AssertionError(f"no {key!r} in:\n{output}")


def test_session_startups_selection_reads_the_host_local_list_and_overlay(
    tmp_path: Path,
) -> None:
    host = Host(tmp_path)
    private = set(host.private.values())
    selected = {*private, host.repeated}

    applied = host.recipe("repos-apply")

    assert applied.returncode == 0, applied.stdout + applied.stderr
    identities, paths = host.registered()
    assert identities == {*PRIVATE, REPEATED.identity}, applied.stdout
    assert paths == {str(path) for path in selected}, applied.stdout
    assert str(host.omitted) not in applied.stdout
    rules = host.registry / "rules.yml"
    assert (
        f"rules      the rules composed from 2 file(s) are at {rules}  (from "
        f"{REPO_ROOT / 'config' / 'onevcs.rules.yml'} overlaid by {host.config / HOST_RULES})"
    ) in applied.stdout, applied.stdout

    # The routing and publication policy the overlay declares, read back through the
    # installed `onevcs rules check`, decided by the overlay's own rule ahead of every
    # tracked one; and the host never asked, since the overlay states each is private.
    for identity, checkout in PRIVATE.items():
        assert checkout.policy is not None
        publication, approvals, position = checkout.policy
        checked = host.onevcs("rules", "check", identity)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        hostname, owner, name = identity.split("/")
        matched = f"rule {position} {{host: {hostname}, owner: {owner}, name: {name}}}"
        assert _reported(checked.stdout, "matched") == matched, checked.stdout
        assert _reported(checked.stdout, "publication") == publication, checked.stdout
        assert _reported(checked.stdout, "approvals") == approvals, checked.stdout
        assert _reported(checked.stdout, "rules") == str(rules), checked.stdout
        inspected = host.onevcs(
            "boundary", "inspect", "--input", "-", stdin=json.dumps({"repository": identity})
        )
        assert inspected.returncode == 0, inspected.stdout + inspected.stderr
        assert json.loads(inspected.stdout) == {"visibility": "private"}
    repeated = host.onevcs("rules", "check", REPEATED.identity)
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert _reported(repeated.stdout, "publication") == "change-auto", repeated.stdout
    assert _reported(repeated.stdout, "approvals") == "none", repeated.stdout
    assert _reported(repeated.stdout, "matched").startswith("rule "), repeated.stdout

    bootstrapped = host.bootstrap_until_settled(selected)

    assert host.marked() == sorted(path.name for path in selected)
    assert _kind(bootstrapped, host.omitted) is None, bootstrapped.stdout

    # With the override gone, the very same invocations select the tracked list: the
    # omitted public checkout is registered and bootstrapped, the private ones are not.
    (host.config / HOST_CHECKOUTS).unlink()
    shutil.rmtree(host.registry)
    shutil.rmtree(host.cache)
    host.cache.mkdir()
    host.marks.unlink()
    tracked = {host.repeated, host.omitted}

    reapplied = host.recipe("repos-apply")

    assert reapplied.returncode == 0, reapplied.stdout + reapplied.stderr
    identities, paths = host.registered()
    assert identities == {REPEATED.identity, OMITTED.identity}, reapplied.stdout
    assert paths == {str(path) for path in tracked}, reapplied.stdout
    for path in private:
        assert str(path) not in reapplied.stdout

    rebootstrapped = host.bootstrap_until_settled(tracked)

    assert host.marked() == sorted(path.name for path in tracked)
    for path in private:
        assert _kind(rebootstrapped, path) is None, rebootstrapped.stdout


@pytest.mark.parametrize("xdg", ["unset", "relative-config"])
def test_with_no_absolute_xdg_config_home_the_host_files_are_read_under_home(
    tmp_path: Path, xdg: str
) -> None:
    """Unset or relative, `XDG_CONFIG_HOME` gives way to `$HOME/.config` for both files.

    A relative value is not honoured: were it read, its list would not exist and the
    tracked one would be selected, registering the omitted checkout and not the private ones.
    """
    host = Host(tmp_path, xdg)
    selected = {*host.private.values(), host.repeated}

    applied = host.recipe("repos-apply")

    assert applied.returncode == 0, applied.stdout + applied.stderr
    identities, paths = host.registered()
    assert identities == {*PRIVATE, REPEATED.identity}, applied.stdout
    assert paths == {str(path) for path in selected}, applied.stdout
    assert f"overlaid by {host.config / HOST_RULES})" in applied.stdout, applied.stdout
    checked = host.onevcs("rules", "check", QUIETHARBOR)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert _reported(checked.stdout, "publication") == "change-open", checked.stdout

    bootstrapped = host.bootstrap_until_settled(selected)

    assert host.marked() == sorted(path.name for path in selected)
    assert _kind(bootstrapped, host.omitted) is None, bootstrapped.stdout
