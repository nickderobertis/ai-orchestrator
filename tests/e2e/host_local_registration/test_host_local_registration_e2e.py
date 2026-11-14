"""A host's own checkout list and rules overlay are what session startup's selection reads.

Every host keeps its checkout list outside every checkout, at
`${XDG_CONFIG_HOME:-$HOME/.config}/ai-orchestrator/onevcs.checkouts`, and no tracked list
stands in for it; each repository declares its own default publication policy in its
`onevcs.toml`, and the host's rules overlay at `.../ai-orchestrator/onevcs.rules.yml`
holds rules only to override a declaration or for a repository that declares nothing.
Nothing names either file to the recipes: session startup runs `just repos-bootstrap
--detach` with no list, and an operator runs `just repos-apply` with none, so the
selection is the recipes' own and is what is driven here.

So the journey sandboxes `HOME` and `XDG_CONFIG_HOME` around a host list and overlay over
synthetic identities and a stand-in of this tree carrying its own `onevcs.toml`, runs both
recipes exactly as session startup invokes them — `just --justfile <root>/justfile
--working-directory <root> ...`, with no checkouts argument and
`ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS` removed from the environment — and reads what they
did back through the installed `onevcs` and the marks each stand-in's own `just bootstrap`
leaves. With the host list gone, both refuse naming where it belongs.

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
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
from sibling_facts import override_declaration
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


#: The synthetic private identities the host's list and overlay name, declaring nothing,
#: so the overlay's rule is the whole of each one's policy.
QUIETHARBOR = "github.com/hiddenco/quietharbor"
PEBBLECRAFT = "github.com/nickderobertis/pebblecraft"
PRIVATE = {
    QUIETHARBOR: Checkout(
        QUIETHARBOR, "projects/quietharbor", Policy("change-open", "required", 1)
    ),
    PEBBLECRAFT: Checkout(PEBBLECRAFT, "projects/pebblecraft", Policy("local-direct", "none", 2)),
}
#: A public checkout no rule names, whose own `onevcs.toml` declares its policy.
DECLARED = Checkout("github.com/scratchowner/declared", "projects/declared")
DECLARATION = 'schema_version = 2\n\n[policy]\npublication = "change-auto"\napprovals = "none"\n'
#: A stand-in of this tree: this repository's identity, carrying this tree's own
#: `onevcs.toml` byte for byte, so what resolves is what this repository declares.
THIS_TREE = Checkout(
    "github.com/nickderobertis/ai-orchestrator", ".ai-orchestrator/repos/ai-orchestrator"
)
#: A checkout on disk that the host's list does not name: neither registered nor bootstrapped.
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


def _stand_in(home: Path, relative: str, identity: str, declaration: str | None = None) -> Path:
    """A committed checkout of ``identity`` at ``~/relative`` whose bootstrap leaves a mark.

    A producer the tracked override names declares its default target, as every real one
    does, because the installed `onevcs` refuses the configuration otherwise; a
    ``declaration`` is committed as the checkout's `onevcs.toml`.
    """
    checkout = home / relative
    checkout.mkdir(parents=True)
    (checkout / "justfile").write_text(
        f'bootstrap:\n    @echo "{checkout.name}" >> "${MARKS_VARIABLE}"\n', encoding="utf-8"
    )
    _git("init", "-q", "--initial-branch", "main", cwd=checkout)
    _git("remote", "add", "origin", f"https://{identity}.git", cwd=checkout)
    if (declared := override_declaration(f"https://{identity}.git")) is not None:
        (checkout / "release-targets.toml").write_text(declared, encoding="utf-8")
    if declaration is not None:
        (checkout / "onevcs.toml").write_text(declaration, encoding="utf-8")
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
    # A fetched clone's `origin/HEAD`, so `onevcs` reads the base here rather than asking
    # the real host for its default branch.
    _git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=checkout)
    _git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main", cwd=checkout)
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
        self.declared = _stand_in(self.home, DECLARED.path, DECLARED.identity, DECLARATION)
        self.this_tree = _stand_in(
            self.home,
            THIS_TREE.path,
            THIS_TREE.identity,
            (REPO_ROOT / "onevcs.toml").read_text(encoding="utf-8"),
        )
        self.omitted = _stand_in(self.home, OMITTED.path, OMITTED.identity)
        (self.config / HOST_RULES).parent.mkdir(parents=True)
        (self.config / HOST_RULES).write_text(HOST_RULES_OVERLAY, encoding="utf-8")
        (self.config / HOST_CHECKOUTS).write_text(
            "# This host's complete list: its private checkouts and the public ones it keeps.\n"
            + "".join(f"~/{checkout.path}\n" for checkout in PRIVATE.values())
            + f"~/{DECLARED.path}\n~/{THIS_TREE.path}\n",
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


def _row(output: str, identity: str) -> list[str]:
    """The resolved-policy table's row for ``identity``, split on whitespace."""
    table = output.split("apply-repo-registry: resolved policy", 1)[1]
    return next(line for line in table.splitlines() if f" {identity} " in line).split()


def test_session_startups_selection_reads_the_host_list_overlay_and_declarations(
    tmp_path: Path,
) -> None:
    host = Host(tmp_path)
    selected = {*host.private.values(), host.declared, host.this_tree}

    applied = host.recipe("repos-apply")

    assert applied.returncode == 0, applied.stdout + applied.stderr
    identities, paths = host.registered()
    assert identities == {*PRIVATE, DECLARED.identity, THIS_TREE.identity}, applied.stdout
    assert paths == {str(path) for path in selected}, applied.stdout
    assert str(host.omitted) not in applied.stdout
    rules = host.registry / "rules.yml"
    assert (
        f"rules      the rules composed from 2 file(s) are at {rules}  (from "
        f"{REPO_ROOT / 'config' / 'onevcs.rules.yml'} overlaid by {host.config / HOST_RULES})"
    ) in applied.stdout, applied.stdout

    # The routing and publication policy the overlay declares for each private identity,
    # read back through the installed `onevcs rules check`, decided by the overlay's own
    # rule; and the host never asked, since the overlay states each is private.
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

    # A public checkout and this tree are routed by their own declarations, no rule
    # naming either: both fields from the document, in `rules check` and in the table.
    for checkout, path, expected in (
        (DECLARED, host.declared, ("change-auto", "none")),
        (THIS_TREE, host.this_tree, ("local-direct", "none")),
    ):
        checked = host.onevcs("rules", "check", checkout.identity)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        document = path / "onevcs.toml"
        source = f"(from the repository declaration {document})"
        assert _reported(checked.stdout, "matched") == "no rule; the default applies"
        assert f"publication: {expected[0]} {source}" in checked.stdout, checked.stdout
        assert f"approvals: {expected[1]} {source}" in checked.stdout, checked.stdout
        row = _row(applied.stdout, checkout.identity)
        assert row[2:] == [expected[0], "(declaration)", expected[1], "(declaration)"], row

    # Every listed sibling is bootstrapped; the stand-in of this tree is the repository
    # running the recipe, which `repos-bootstrap` skips by identity rather than nests.
    siblings = selected - {host.this_tree}
    bootstrapped = host.bootstrap_until_settled(siblings)

    assert host.marked() == sorted(path.name for path in siblings)
    assert _kind(bootstrapped, host.this_tree) == "skip", bootstrapped.stdout
    assert _kind(bootstrapped, host.omitted) is None, bootstrapped.stdout


def test_with_no_host_checkout_list_both_recipes_refuse_naming_where_it_belongs(
    tmp_path: Path,
) -> None:
    """No tracked list stands in: each recipe names the host's path and the setup doc."""
    host = Host(tmp_path)
    expected = host.config / HOST_CHECKOUTS
    expected.unlink()

    for arguments in (("repos-apply",), ("repos-bootstrap", "--detach")):
        refused = host.recipe(*arguments)

        assert refused.returncode != 0, refused.stdout + refused.stderr
        assert f"this host has no checkout list at {expected}" in refused.stderr, refused.stderr
        assert "docs/host-setup.md" in refused.stderr, refused.stderr
    assert not host.registry.exists()
    assert host.marked() == []


@pytest.mark.parametrize("xdg", ["unset", "relative-config"])
def test_with_no_absolute_xdg_config_home_the_host_files_are_read_under_home(
    tmp_path: Path, xdg: str
) -> None:
    """Unset or relative, `XDG_CONFIG_HOME` gives way to `$HOME/.config` for both files.

    A relative value is not honoured: were it read, no list would be found there and both
    recipes would refuse rather than register the host's checkouts.
    """
    host = Host(tmp_path, xdg)
    selected = {*host.private.values(), host.declared, host.this_tree}

    applied = host.recipe("repos-apply")

    assert applied.returncode == 0, applied.stdout + applied.stderr
    identities, paths = host.registered()
    assert identities == {*PRIVATE, DECLARED.identity, THIS_TREE.identity}, applied.stdout
    assert paths == {str(path) for path in selected}, applied.stdout
    assert f"overlaid by {host.config / HOST_RULES})" in applied.stdout, applied.stdout
    checked = host.onevcs("rules", "check", QUIETHARBOR)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert _reported(checked.stdout, "publication") == "change-open", checked.stdout

    # Every listed sibling is bootstrapped; the stand-in of this tree is the repository
    # running the recipe, which `repos-bootstrap` skips by identity rather than nests.
    siblings = selected - {host.this_tree}
    bootstrapped = host.bootstrap_until_settled(siblings)

    assert host.marked() == sorted(path.name for path in siblings)
    assert _kind(bootstrapped, host.this_tree) == "skip", bootstrapped.stdout
    assert _kind(bootstrapped, host.omitted) is None, bootstrapped.stdout
