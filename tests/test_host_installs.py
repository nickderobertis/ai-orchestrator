"""The table of what this host installs, held to every copy of it.

`orchestrator/host_installs.py` states, per producer, which wheel this host installs,
what the producer's own declaration calls it, and which `config/<pin>.version` it
governs. Four other places carry a piece of that same answer — `pyproject.toml`'s pinned
distributions, the `config/*.version` files, `config/onevcs.releases.yml`'s
``default_target`` per producer, and each producer's own `release-targets.toml` — and a
piece that moved in one of them without the table is the drift a `published` node would
then wait on: the wrong artifact, or one no release carries.

The first three are this workspace's, reconciled in the cached tier. The fourth lives in
another repository's checkout, so its reconciliation is `reads_checkouts` — the uncached
tier, for the reason that marker exists: a memo keyed on this workspace would replay a
green straight across the day a producer renames a target. The fixtures under
`tests/fixtures/release-targets/` are copies of those declarations, one per producer,
standing in for them in the registry-apply journey; each is held whole to the
declaration it copies in that same tier, so the journey's scratch producers keep
declaring what the real ones do.
"""

from __future__ import annotations

import importlib.metadata
import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
from registered_checkouts import registered_checkouts

from orchestrator.host_installs import INSTALLED, Installed, by_artifact, by_producer, rendered
from orchestrator.root import REPO_ROOT

#: The rows this table is fixed to, stated here in full rather than derived from the
#: module: a test that read them out of the code under test would hold the module to
#: itself. Eight producers, the override's order, and the engine wheel alone marked as
#: what a dispatch runs.
EXPECTED_ROWS = (
    Installed(
        "github.com/nickderobertis/onepipeline", "pypi", "pypi:onepipeline-cli", "onepipeline", True
    ),
    Installed("github.com/nickderobertis/onevcs", "pypi", "pypi:onevcs-cli", "onevcs", False),
    Installed(
        "github.com/nickderobertis/oneagentgraph",
        "pypi",
        "pypi:oneagentgraph-cli",
        "oneagentgraph",
        False,
    ),
    Installed("github.com/nickderobertis/onejudge", "cli", "pypi:onejudge-cli", "onejudge", False),
    Installed(
        "github.com/nickderobertis/oneharness",
        "cli-wheel",
        "pypi:oneharness-cli",
        "oneharness",
        False,
    ),
    Installed(
        "github.com/nickderobertis/onetaskgraph",
        "pypi",
        "pypi:onetaskgraph-cli",
        "onetaskgraph",
        False,
    ),
    Installed(
        "github.com/nickderobertis/onepipeline-ui",
        "pypi-cli",
        "pypi:onepipeline-api-cli",
        "onepipeline-ui",
        False,
    ),
    Installed(
        "github.com/nickderobertis/onemessagebus",
        "pypi",
        "pypi:onemessagebus-cli",
        "onemessagebus",
        False,
    ),
)

PYPROJECT = REPO_ROOT / "pyproject.toml"
VERSION_FILES = REPO_ROOT / "config"
RELEASES = REPO_ROOT / "config" / "onevcs.releases.yml"
#: One stand-in `release-targets.toml` per producer, named by the producer's repository
#: name, which `tests/e2e/test_repo_registry_apply_e2e.py` commits at a scratch
#: producer's base so `onevcs release targets` has a declaration to resolve the
#: override's `default_target` against.
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "release-targets"

#: One rule of the override, as the tracked file writes every rule: a one-line flow
#: `match` naming the repository, then its fields one per indented line up to the next
#: rule. Read as text because this package ships no YAML parser; a rule spelled any
#: other way is not found, which fails the reconciliation below rather than passing it.
RELEASE_RULE = re.compile(
    r"- match: \{host: (?P<host>[^,]+), owner: (?P<owner>[^,]+), name: (?P<name>[^}]+)\}\n"
    r"(?P<fields>(?:    \S.*\n)*)"
)

#: Every installed CLI now arrives from a dependency in the project lock.
INSTALLED_OUTSIDE_PYPROJECT = frozenset()
#: The one pinned distribution that is not itself a row's artifact: `pyproject.toml`
#: pins the `onejudge` SDK, and the CLI wheel this host runs — the row's artifact —
#: arrives as that SDK's own dependency at the same version. `scripts/session-setup.sh`
#: verifies both, and the reconciliation below reads the SDK's requirement rather than
#: taking the pairing on trust.
SDK_CARRYING_A_ROWS_WHEEL = {
    "onejudge": "onejudge-cli",
    "onetaskgraph-sdk": "onetaskgraph-cli",
}


def test_the_table_holds_exactly_the_expected_rows_in_the_overrides_order() -> None:
    assert INSTALLED == EXPECTED_ROWS
    assert [row.producer for row in INSTALLED if row.governs_dispatch] == [
        "github.com/nickderobertis/onepipeline"
    ]


@pytest.mark.parametrize("row", INSTALLED, ids=lambda row: row.artifact)
def test_by_artifact_answers_each_known_artifact_with_its_row(row: Installed) -> None:
    assert by_artifact(row.artifact) == row


@pytest.mark.parametrize("row", INSTALLED, ids=lambda row: row.producer)
def test_by_producer_answers_each_known_producer_with_its_row(row: Installed) -> None:
    assert by_producer(row.producer) == row


@pytest.mark.parametrize("unknown", ["pypi:onepipeline", "crate:onevcs", "", "onepipeline-cli"])
def test_by_artifact_answers_none_for_an_artifact_no_row_names(unknown: str) -> None:
    assert by_artifact(unknown) is None


@pytest.mark.parametrize(
    "unknown", ["github.com/nickderobertis/llmlint", "onepipeline", "", "github.com/x/onepipeline"]
)
def test_by_producer_answers_none_for_a_producer_no_row_names(unknown: str) -> None:
    assert by_producer(unknown) is None


def test_rendered_names_every_rows_four_names_and_marks_the_one_a_dispatch_runs() -> None:
    lines = rendered().splitlines()
    assert len(lines) == len(INSTALLED)
    for line, row in zip(lines, INSTALLED, strict=True):
        assert row.producer in line
        assert f"`{row.target}`" in line
        assert f"`{row.artifact}`" in line
        assert f"config/{row.pin}.version" in line
        assert ("every dispatched node runs" in line) == row.governs_dispatch, line
    assert rendered().endswith("\n")


def _pinned_distributions() -> dict[str, str]:
    """Every `name==version` `pyproject.toml` pins, by name."""
    document = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    pinned: dict[str, str] = {}
    for requirement in document["project"]["dependencies"]:
        name, separator, version = requirement.partition("==")
        assert separator, f"pyproject.toml does not pin {requirement!r} to one version"
        pinned[name.strip()] = version.strip()
    return pinned


def _distribution(row: Installed) -> str:
    """The distribution name a row's `pypi:<name>` artifact installs."""
    registry, separator, name = row.artifact.partition(":")
    assert separator and registry == "pypi", (
        f"{row.producer}'s artifact {row.artifact!r} is not a PyPI distribution, and the "
        "wheel is the one artifact this host installs from a producer"
    )
    return name


def test_every_pin_file_has_a_row_and_every_row_a_pin_file() -> None:
    pins = {path.stem for path in VERSION_FILES.glob("*.version")}
    rows = {row.pin for row in INSTALLED}
    assert pins == rows, (
        f"config/*.version names {sorted(pins - rows)} with no row, and the table names "
        f"{sorted(rows - pins)} with no such file"
    )


def test_every_pinned_distribution_is_a_rows_wheel_and_every_rows_wheel_is_pinned() -> None:
    """`pyproject.toml` and the table name the same wheels, both ways round.

    A pinned distribution no row names is one this host installs without saying what
    release target it is; a row whose wheel is not pinned is one this host claims to
    install and does not — with the two stated exceptions above, each read rather than
    waved through: the SDK's requirement is what carries the CLI wheel, and it has to
    carry it at the pinned version.
    """
    pinned = _pinned_distributions()
    wheels = {_distribution(row) for row in INSTALLED}

    carried: dict[str, str] = {}
    for sdk, wheel in SDK_CARRYING_A_ROWS_WHEEL.items():
        assert sdk in pinned, f"pyproject.toml no longer pins the {sdk} SDK"
        requirements = importlib.metadata.requires(sdk) or []
        assert f"{wheel}=={pinned[sdk]}" in requirements, (
            f"the installed {sdk} {pinned[sdk]} requires {requirements}, not the "
            f"{wheel} wheel at that version, so the table's {wheel} row is not what "
            "that pin installs"
        )
        carried[wheel] = pinned[sdk]

    unnamed = set(pinned) - wheels - set(SDK_CARRYING_A_ROWS_WHEEL)
    assert not unnamed, (
        f"pyproject.toml pins {sorted(unnamed)}, which no row's artifact names; add the "
        "row, or state here why it is not a producer's release target"
    )
    unpinned = wheels - set(pinned) - set(carried) - INSTALLED_OUTSIDE_PYPROJECT
    assert not unpinned, (
        f"the table says this host installs {sorted(unpinned)}, which pyproject.toml does not pin"
    )


def _override_default_targets() -> dict[str, str]:
    """Each producer's `default_target` as `config/onevcs.releases.yml` writes it."""
    text = RELEASES.read_text(encoding="utf-8")
    rules = list(RELEASE_RULE.finditer(text))
    assert len(rules) == text.count("- match:"), (
        "config/onevcs.releases.yml no longer writes every rule's match as a one-line flow "
        f"mapping, so only {len(rules)} of {text.count('- match:')} rules were read back"
    )
    defaults: dict[str, str] = {}
    for rule in rules:
        identity = f"{rule['host']}/{rule['owner']}/{rule['name']}"
        named = re.search(r"^    default_target: (\S+)$", rule["fields"], re.MULTILINE)
        if named is not None:
            defaults[identity] = named.group(1)
    return defaults


def test_the_overrides_default_target_per_producer_is_the_rows_target() -> None:
    """The override names, per producer, the target the table says this host installs.

    Both directions: a producer in the override with no row is a default target nothing
    says the artifact of, and a row with no rule is a producer a consumer naming no
    `consumes` would get no target from — held for ever under `published`.
    """
    defaults = _override_default_targets()
    expected = {row.producer: row.target for row in INSTALLED}
    assert defaults == expected, (
        f"config/onevcs.releases.yml names default targets {defaults}; the table says {expected}"
    )


def test_no_rule_in_the_override_restates_a_producers_targets() -> None:
    """Every rule merges the producer's declaration and adds no target of its own.

    A `targets:` here replaces the producer's target whole, so the probe a host copied
    from a repository stops matching that repository the day it moves; `declaration:
    ignore` drops the producer's declaration entirely. Neither is what this host means.
    """
    text = RELEASES.read_text(encoding="utf-8")
    stated = [
        line
        for line in text.splitlines()
        if not line.lstrip().startswith("#") and "targets" in line
    ]
    assert not stated, f"config/onevcs.releases.yml restates targets: {stated}"
    for rule in RELEASE_RULE.finditer(text):
        assert "declaration: ignore" not in rule["fields"], rule.group(0)


#: How `onevcs` refuses a repository its registry does not hold.
NOT_REGISTERED = "is not a registered repository"
REMOTE_BASES = ("origin/HEAD", "origin/main", "origin/master")


def _declared_ids_at_fetched_base(checkout: Path) -> dict[str, str] | None:
    """Each target's id by name, from the declaration at ``checkout``'s fetched base.

    The read `onevcs` makes is of the publication checkout's own base branch, which
    several managers share and any dispatch may leave on a branch of its own; the
    remote-tracking ref moves only when somebody fetches, so it says what the
    repository declares rather than what the checkout happens to be sitting on.
    """
    for candidate in REMOTE_BASES:
        shown = subprocess.run(
            ["git", "-C", str(checkout), "show", f"{candidate}:release-targets.toml"],
            text=True,
            capture_output=True,
            check=False,
        )
        if shown.returncode == 0:
            declared = tomllib.loads(shown.stdout).get("target", [])
            return {target["name"]: target["id"] for target in declared}
    return None


def _producer_declaration(row: Installed) -> tuple[dict[str, str], str]:
    """Each target's id by name as ``row``'s producer declares it, and where that was read.

    Read through `onevcs release targets --json`, which is the read a dispatch makes.
    A checkout `onevcs` cannot read a declaration out of right now — another manager's
    dispatch has it on a branch — is read at its fetched base instead, and the second
    value says which read answered so a failure can name it.
    """
    checkouts = registered_checkouts()
    assert row.producer in checkouts, (
        f"{row.producer} has no checkout on this host among those "
        "`config/onevcs.checkouts` lists, so its declaration cannot be reconciled here"
    )
    asked = subprocess.run(
        ["uv", "run", "onevcs", "release", "targets", row.producer, "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert asked.returncode == 0 or NOT_REGISTERED not in asked.stderr, (
        f"{row.producer} is not registered in this host's onevcs registry; run "
        f"`just repos-apply`: {asked.stderr}"
    )
    if asked.returncode == 0:
        declaration = json.loads(asked.stdout)["declaration"]
        if declaration["state"] == "declared":
            return {
                target["name"]: target["id"] for target in declaration["declared"]["target"]
            }, f"`onevcs release targets {row.producer} --json`"
    declared = _declared_ids_at_fetched_base(checkouts[row.producer])
    assert declared is not None, (
        f"{row.producer}'s declaration could not be read through onevcs "
        f"({asked.stdout or asked.stderr}) or at {checkouts[row.producer]}'s fetched base"
    )
    return declared, f"{checkouts[row.producer]}'s fetched base"


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] This marker is not a
# tier the default run hides: `orchestrator:test-checkouts` runs exactly `-m
# reads_checkouts`, deliberately uncached, and `just check` includes it. The rule's remedy
# — a project of its own, so `nx affected` can skip it — is the opposite of what this tier
# needs, because its subject is a producer checkout outside the workspace and a memo keyed
# on this workspace would replay whatever that checkout declared when it was recorded.
@pytest.mark.reads_checkouts
@pytest.mark.parametrize("row", INSTALLED, ids=lambda row: row.producer)
def test_each_rows_artifact_is_what_the_producer_declares_under_that_target_name(
    row: Installed,
) -> None:
    """The row's artifact is the id the producer's own declaration gives the row's target.

    The declared target carrying the row's name has to carry the row's artifact as its
    id, or a node waiting on the override's default target waits on something else.
    """
    declared, declared_by = _producer_declaration(row)
    assert declared.get(row.target) == row.artifact, (
        f"{row.producer} declares {declared} at {declared_by}, and the table says its "
        f"`{row.target}` target is `{row.artifact}`; if the producer moved, that is a "
        "finding to report rather than a row to edit"
    )


@pytest.mark.reads_checkouts
@pytest.mark.parametrize("row", INSTALLED, ids=lambda row: row.producer)
def test_each_producers_fixture_declares_the_targets_the_producer_declares(
    row: Installed,
) -> None:
    """The stand-in declaration names every target the producer does, by id, and no other.

    The registry-apply journey registers a scratch producer per row with this fixture
    at its base, and what it proves about the override is only as true as the
    fixture's likeness to the declaration: a default target resolved among targets the
    producer no longer declares is proven against a repository that does not exist. So
    the whole map is held, not only the row's target — and a producer that moved is a
    fixture to refresh from its base, since the fixture is a copy and not a contract.
    """
    declared, declared_by = _producer_declaration(row)
    fixture = FIXTURES / f"{row.producer.rpartition('/')[2]}.toml"
    stand_in = {
        target["name"]: target["id"]
        for target in tomllib.loads(fixture.read_text(encoding="utf-8"))["target"]
    }
    assert stand_in == declared, (
        f"{fixture.relative_to(REPO_ROOT)} declares {stand_in}, and {row.producer} "
        f"declares {declared} at {declared_by}; refresh the fixture's target ids and "
        "names from the producer's base"
    )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
