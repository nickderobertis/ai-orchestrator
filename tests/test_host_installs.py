"""The table of what this host installs, held to every copy of it.

`orchestrator/host_installs.py` states, per producer, which wheel this host installs,
what the producer's own declaration calls it, and which `config/<pin>.version` it
governs. Three other places this repository writes carry a piece of that same answer —
`pyproject.toml`'s pinned distributions, the `config/*.version` files, and
`config/onevcs.releases.yml`'s ``default_target`` per producer — and a piece that moved
in one of them without the table is the drift a `published` node would then wait on:
the wrong artifact, or one no release carries. Those are reconciled in the cached tier.

The fourth piece is each producer's own `release-targets.toml`, which is the producer's
to change and is never copied here. What the table and the override claim of it is read
against the declaration itself, at its fetched base, by `tests/sibling_facts.py`: the
readers are driven against scratch producer checkouts in the cached tier, and against
this host's checkouts in `reads_checkouts`, where a true contradiction refuses only a
change that edits the registration and is reported, not failed, for any other.
"""

from __future__ import annotations

import importlib.metadata
import tomllib
import warnings
from pathlib import Path

import pytest
from registered_checkouts import RepoIdentity
from sibling_facts import (
    RELEASE_RULE,
    SiblingDrift,
    advance,
    declaration,
    declared_targets,
    held_checkouts,
    override_default_targets,
    read_declarations,
    registered_checkouts,
    release_contradictions,
    scratch_checkout,
    settle_drift,
)

from orchestrator.host_installs import (
    AWAITING_FIRST_ADOPTION,
    INSTALLED,
    Installed,
    by_artifact,
    by_producer,
    rendered,
)
from orchestrator.root import REPO_ROOT

#: The rows this table is fixed to, stated here in full rather than derived from the
#: module: a test that read them out of the code under test would hold the module to
#: itself. Ten producers, the override's order, the engine wheel alone marked as what a
#: dispatch runs, and `llmlint` alone governed by no pin, naming its installer instead.
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
    Installed(
        "github.com/nickderobertis/onebudgetspec",
        "pypi",
        "pypi:onebudgetspec-cli",
        "onebudgetspec",
        False,
    ),
    Installed(
        "github.com/nickderobertis/llmlint",
        "cli",
        "pypi:llmlint-cli",
        None,
        False,
        "scripts/setup-llmlint.sh",
    ),
)

PYPROJECT = REPO_ROOT / "pyproject.toml"
VERSION_FILES = REPO_ROOT / "config"
RELEASES = REPO_ROOT / "config" / "onevcs.releases.yml"

#: The one installed wheel no dependency in the project lock carries: `llmlint-cli`, which
#: `scripts/setup-llmlint.sh` installs as a `uv tool`, capped by `config/oneharness.version`
#: rather than pinned. Stated here rather than read off the rows' ``installer`` so that a
#: row gaining an installer it does not need is refused below instead of admitted.
INSTALLED_OUTSIDE_PYPROJECT = frozenset({"llmlint-cli"})
#: The pinned distributions that are libraries this package imports rather than a
#: producer's wheel: nothing on this host runs them as a tool, no `config/*.version`
#: governs them, and no release target of a repository this host dispatches against
#: names them — so a row would claim a producer where there is only a dependency.
#: `pyyaml` is the one YAML reader `orchestrator/workspaces_overlay.py` composes the
#: installed workspaces file with.
LIBRARIES_THIS_PACKAGE_IMPORTS = frozenset({"pyyaml"})
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
    "unknown", ["github.com/nickderobertis/unruled", "onepipeline", "", "github.com/x/onepipeline"]
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
        if row.pin is None:
            assert f"`{row.installer}` installs with no `config/` pin" in line, line
            assert "config/None" not in line, line
        else:
            assert f"config/{row.pin}.version" in line
        assert ("every dispatched node runs" in line) == row.governs_dispatch, line
    assert rendered().endswith("\n")


def test_a_row_names_a_pin_or_an_installer_and_never_both() -> None:
    """Exactly one of the two says how a release of the row's wheel reaches this host.

    A pinned wheel arrives through `pyproject.toml` and the lock, so an installer beside
    its pin would be a second account of one install; a row with neither is a wheel
    nothing here says how to adopt. The installer is a script this repository carries,
    so the row cannot name one that is not there.
    """
    for row in INSTALLED:
        assert (row.pin is None) != (row.installer is None), row
        if row.installer is not None:
            assert (REPO_ROOT / row.installer).is_file(), row
            assert not row.governs_dispatch, row
    assert {
        _distribution(row) for row in INSTALLED if row.installer is not None
    } == INSTALLED_OUTSIDE_PYPROJECT


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


def test_every_pin_file_has_a_row_and_every_pinned_row_a_pin_file() -> None:
    pins = {path.stem for path in VERSION_FILES.glob("*.version")}
    rows = {row.pin for row in INSTALLED if row.pin is not None}
    assert pins == rows, (
        f"config/*.version names {sorted(pins - rows)} with no row, and the table names "
        f"{sorted(rows - pins)} with no such file"
    )


def test_pinned_wheels_are_rows_and_rows_are_pinned_or_a_stated_exception() -> None:
    """`pyproject.toml` and the table name the same wheels, both ways round.

    A pinned distribution no row names is one this host installs without saying what
    release target it is; a row whose wheel is not pinned is one this host claims to
    install and does not — with the stated exceptions above, each read rather than
    waved through: the SDK's requirement is what carries the CLI wheel, and it has to
    carry it at the pinned version, and a library this package imports is pinned like
    the wheels and named as the library it is.
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

    unnamed = set(pinned) - wheels - set(SDK_CARRYING_A_ROWS_WHEEL) - LIBRARIES_THIS_PACKAGE_IMPORTS
    assert not unnamed, (
        f"pyproject.toml pins {sorted(unnamed)}, which no row's artifact names; add the "
        "row, or state here why it is not a producer's release target"
    )
    assert set(pinned) >= LIBRARIES_THIS_PACKAGE_IMPORTS, (
        f"{sorted(LIBRARIES_THIS_PACKAGE_IMPORTS - set(pinned))} is declared a library this "
        "package imports and pyproject.toml no longer pins it"
    )
    unpinned = wheels - set(pinned) - set(carried) - INSTALLED_OUTSIDE_PYPROJECT
    assert not unpinned, (
        f"the table says this host installs {sorted(unpinned)}, which pyproject.toml does not pin"
    )


def _override_default_targets() -> dict[RepoIdentity, str]:
    """Each producer's `default_target` as `config/onevcs.releases.yml` writes it."""
    return override_default_targets(RELEASES.read_text(encoding="utf-8"))


#: The producers whose override rule precedes their first adoption, stated here in full
#: for the reason `EXPECTED_ROWS` is: none, since `onebudgetspec`'s first release was
#: adopted with its row, its pin and its dependency.
EXPECTED_AWAITING: frozenset[RepoIdentity] = frozenset()
#: A producer that has released nothing yet, standing in for the next one whose override
#: rule precedes its first adoption: the set is empty, and what it admits still has to be
#: shown on a copy holding one.
UNRELEASED: RepoIdentity = "github.com/nickderobertis/unreleased"


def override_complaints(
    defaults: dict[RepoIdentity, str],
    rows: tuple[Installed, ...],
    awaiting: frozenset[RepoIdentity],
) -> list[str]:
    """Every way the override's default targets disagree with the table and the set.

    Taken as arguments rather than read here so that each refusal can be shown on a
    defective copy, beside the tracked one passing.
    """
    expected = {row.producer: row.target for row in rows}
    found = [
        f"{producer} is both a row and awaiting its first adoption; the node that adds "
        "the row removes it from the set"
        for producer in sorted(awaiting & expected.keys())
    ]
    found += [
        f"config/onevcs.releases.yml names {producer}'s default target {target!r}, and "
        "neither a row nor the awaiting-first-adoption set accounts for it"
        for producer, target in sorted(defaults.items())
        if producer not in expected and producer not in awaiting
    ]
    found += [
        f"{producer} is awaiting its first adoption, and config/onevcs.releases.yml names "
        "no default target for it"
        for producer in sorted(awaiting - defaults.keys())
    ]
    found += [
        f"config/onevcs.releases.yml names {producer}'s default target "
        f"{defaults.get(producer)!r}, and its row says {target!r}"
        for producer, target in expected.items()
        if defaults.get(producer) != target
    ]
    return found


def test_the_overrides_default_target_per_producer_is_the_rows_target() -> None:
    """The override names, per producer, the target the table says this host installs.

    Both directions: a producer in the override with no row is a default target nothing
    says the artifact of, and a row with no rule is a producer a consumer naming no
    `consumes` would get no target from — held for ever under `published`. The one
    admitted gap is a producer on the awaiting-first-adoption set, whose rule precedes
    the release that would give it a row.
    """
    assert AWAITING_FIRST_ADOPTION == EXPECTED_AWAITING
    assert (
        override_complaints(_override_default_targets(), INSTALLED, AWAITING_FIRST_ADOPTION) == []
    )


def test_a_producer_in_the_override_with_neither_a_row_nor_an_entry_is_refused() -> None:
    defaults = {**_override_default_targets(), "github.com/nickderobertis/unadopted": "pypi"}

    assert override_complaints(defaults, INSTALLED, AWAITING_FIRST_ADOPTION) == [
        "config/onevcs.releases.yml names github.com/nickderobertis/unadopted's default "
        "target 'pypi', and neither a row nor the awaiting-first-adoption set accounts for it"
    ]


def test_the_set_is_what_admits_an_awaiting_producer() -> None:
    """A rule ahead of a first adoption passes on the set, and is unaccounted for off it."""
    defaults = {**_override_default_targets(), UNRELEASED: "pypi"}

    assert override_complaints(defaults, INSTALLED, frozenset({UNRELEASED})) == []
    assert override_complaints(defaults, INSTALLED, frozenset()) == [
        f"config/onevcs.releases.yml names {UNRELEASED}'s default target 'pypi', and neither "
        "a row nor the awaiting-first-adoption set accounts for it"
    ]


def test_the_onebudgetspec_row_is_what_accounts_for_its_tracked_rule() -> None:
    """Adopted, onebudgetspec's rule is admitted by its row: without the row it is refused."""
    producer = "github.com/nickderobertis/onebudgetspec"
    without = tuple(row for row in INSTALLED if row.producer != producer)

    assert len(without) == len(INSTALLED) - 1
    assert override_complaints(_override_default_targets(), without, AWAITING_FIRST_ADOPTION) == [
        f"config/onevcs.releases.yml names {producer}'s default target 'pypi', and neither "
        "a row nor the awaiting-first-adoption set accounts for it"
    ]


def test_a_producer_with_both_a_row_and_an_entry_on_the_set_is_refused() -> None:
    """The adopting node adds the row and empties the entry together; half of that fails."""
    producer = "github.com/nickderobertis/onebudgetspec"

    assert override_complaints(_override_default_targets(), INSTALLED, frozenset({producer})) == [
        f"{producer} is both a row and awaiting its first adoption; the node that adds the "
        "row removes it from the set"
    ]


def test_an_entry_on_the_set_the_override_does_not_name_is_refused() -> None:
    awaiting = AWAITING_FIRST_ADOPTION | {"github.com/nickderobertis/unruled"}

    assert override_complaints(_override_default_targets(), INSTALLED, awaiting) == [
        "github.com/nickderobertis/unruled is awaiting its first adoption, and "
        "config/onevcs.releases.yml names no default target for it"
    ]


def test_a_rows_default_target_moving_in_the_override_is_refused() -> None:
    row = INSTALLED[0]
    defaults = {**_override_default_targets(), row.producer: "crate"}

    assert override_complaints(defaults, INSTALLED, AWAITING_FIRST_ADOPTION) == [
        f"config/onevcs.releases.yml names {row.producer}'s default target 'crate', and its "
        f"row says {row.target!r}"
    ]


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


def _scratch_producers(root: Path, defaults: dict[RepoIdentity, str] | None = None) -> Path:
    """A checkout list of one scratch producer per rule of the tracked override.

    Each declares only what this repository decides of it: a row's target under the
    row's artifact, and for a producer awaiting its first adoption the override's
    default target under an id of its own, since nothing here names that artifact yet.
    """
    rows = {row.producer: row for row in INSTALLED}
    paths = [
        scratch_checkout(
            root / producer.rpartition("/")[2],
            producer,
            {
                "release-targets.toml": declaration(
                    {
                        target: rows[producer].artifact
                        if producer in rows
                        else f"pypi:{producer.rpartition('/')[2]}-cli"
                    }
                )
            },
        )
        for producer, target in (defaults or _override_default_targets()).items()
    ]
    manifest = root / "checkouts.list"
    manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    return manifest


def _contradictions(
    manifest: Path,
    defaults: dict[RepoIdentity, str] | None = None,
    awaiting: frozenset[RepoIdentity] = AWAITING_FIRST_ADOPTION,
) -> list[str]:
    """What the readers find, over the checkouts ``manifest`` lists, against the tracked files.

    ``defaults`` and ``awaiting`` default to the tracked override and set; a journey about
    a producer ahead of its first adoption names a copy holding one.
    """
    declarations = {
        identity: declared_targets(paths[0]) for identity, paths in held_checkouts(manifest).items()
    }
    return release_contradictions(
        defaults or _override_default_targets(), declarations, INSTALLED, awaiting
    )


def _checkout(manifest: Path, producer: RepoIdentity) -> Path:
    return held_checkouts(manifest)[producer][0]


def test_producers_declaring_what_this_repository_decides_contradict_nothing(
    tmp_path: Path,
) -> None:
    manifest = _scratch_producers(tmp_path)

    assert set(held_checkouts(manifest)) == set(_override_default_targets())
    assert _contradictions(manifest) == []


def test_a_producer_gaining_targets_and_a_language_since_registration_contradicts_nothing(
    tmp_path: Path,
) -> None:
    """Normal development in the producer's own repository fails no check here."""
    manifest = _scratch_producers(tmp_path)
    row = INSTALLED[0]
    advance(
        _checkout(manifest, row.producer),
        {
            "release-targets.toml": declaration(
                {row.target: row.artifact, "crate": "crate:scratch", "npm": "npm:@scratch/cli"}
            ),
            "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
        },
    )

    assert declared_targets(_checkout(manifest, row.producer)) == {
        row.target: row.artifact,
        "crate": "crate:scratch",
        "npm": "npm:@scratch/cli",
    }
    assert _contradictions(manifest) == []
    with warnings.catch_warnings():
        warnings.simplefilter("error", SiblingDrift)
        settle_drift(_contradictions(manifest))


def test_a_producer_dropping_the_overrides_default_target_contradicts_it_by_name(
    tmp_path: Path,
) -> None:
    manifest = _scratch_producers(tmp_path)
    row = next(row for row in INSTALLED if row.target == "cli")
    advance(
        _checkout(manifest, row.producer),
        {"release-targets.toml": declaration({"crate": "crate:onejudge", "sdk": "pypi:onejudge"})},
    )

    assert _contradictions(manifest) == [
        f"{row.producer} no longer declares the default target 'cli' "
        "config/onevcs.releases.yml names for it; it declares ['crate', 'sdk']"
    ]


def test_a_producer_renaming_the_artifact_a_row_installs_contradicts_the_table(
    tmp_path: Path,
) -> None:
    manifest = _scratch_producers(tmp_path)
    row = INSTALLED[1]
    advance(
        _checkout(manifest, row.producer),
        {"release-targets.toml": declaration({row.target: "pypi:renamed-cli"})},
    )

    assert _contradictions(manifest) == [
        f"{row.producer} declares its {row.target!r} target as 'pypi:renamed-cli', and "
        f"orchestrator/host_installs.py says this host installs {row.artifact!r} under it"
    ]


def test_an_installed_producer_declaring_no_release_target_contradicts_the_override(
    tmp_path: Path,
) -> None:
    manifest = _scratch_producers(tmp_path)
    row = INSTALLED[2]
    advance(_checkout(manifest, row.producer), {"release-targets.toml": None})

    assert declared_targets(_checkout(manifest, row.producer)) is None
    assert _contradictions(manifest) == [
        f"{row.producer} declares no release targets at its base, and "
        f"config/onevcs.releases.yml names {row.target!r} as its default target"
    ]


def test_a_producer_awaiting_its_first_adoption_passes_with_no_row(tmp_path: Path) -> None:
    """The state the override names a producer in ahead of its first release is valid.

    No install row, a resolved default target, and a declaration that may carry that
    target among others — or nothing at all yet, before the producer declares.
    """
    producer, target = UNRELEASED, "pypi"
    defaults = {**_override_default_targets(), producer: target}
    awaiting = frozenset({producer})
    manifest = _scratch_producers(tmp_path, defaults)
    assert by_producer(producer) is None
    assert _contradictions(manifest, defaults, awaiting) == []

    advance(
        _checkout(manifest, producer),
        {
            "release-targets.toml": declaration(
                {"crate": "crate:unreleased", target: "pypi:unreleased-cli"}
            ),
            "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
        },
    )
    assert _contradictions(manifest, defaults, awaiting) == []

    advance(_checkout(manifest, producer), {"release-targets.toml": None})
    assert _contradictions(manifest, defaults, awaiting) == []


def test_a_producer_awaiting_its_first_adoption_still_contradicts_by_dropping_its_target(
    tmp_path: Path,
) -> None:
    producer, target = UNRELEASED, "pypi"
    defaults = {**_override_default_targets(), producer: target}
    manifest = _scratch_producers(tmp_path, defaults)
    advance(
        _checkout(manifest, producer),
        {"release-targets.toml": declaration({"crate": "crate:unreleased"})},
    )

    assert _contradictions(manifest, defaults, frozenset({producer})) == [
        f"{producer} no longer declares the default target {target!r} "
        "config/onevcs.releases.yml names for it; it declares ['crate']"
    ]


def test_a_repository_the_override_names_no_default_for_is_never_judged(tmp_path: Path) -> None:
    """A registered repository declaring targets the override says nothing of is its business."""
    manifest = _scratch_producers(tmp_path)
    other = scratch_checkout(
        tmp_path / "printobserver",
        "github.com/nickderobertis/printobserver",
        {"release-targets.toml": declaration({"crate": "crate:printobserver"})},
    )
    manifest.write_text(manifest.read_text(encoding="utf-8") + f"{other}\n", encoding="utf-8")

    assert "github.com/nickderobertis/printobserver" in held_checkouts(manifest)
    assert _contradictions(manifest) == []


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] This marker is not a
# tier the default run hides: `orchestrator:test-checkouts` runs exactly `-m
# reads_checkouts`, deliberately uncached, and `just check` includes it. The rule's remedy
# — a project of its own, so `nx affected` can skip it — is the opposite of what this tier
# needs, because its subject is a producer checkout outside the workspace and a memo keyed
# on this workspace would replay whatever that checkout declared when it was recorded.
@pytest.mark.reads_checkouts
def test_a_held_producers_contradiction_fails_only_a_change_editing_the_registration() -> None:
    """The same readers over this host's own producer checkouts, settled by what the change edits.

    A producer this host holds no checkout of is not judged, since there is nothing here
    to read; one it holds is read at its fetched base. A contradiction, or a declaration
    that will not parse, refuses a change that edits the registration and is reported as
    drift for every other.
    """
    held = registered_checkouts()
    defaults = _override_default_targets()
    read = read_declarations(
        {producer: held[producer] for producer in defaults if producer in held}
    )

    # llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] Settled rather than
    # asserted: ai-orchestrator#1529 rules that a check whose subject is a sibling's live state
    # refuses only a push changing one of the five registration files and reports the drift
    # otherwise; `tests/sibling_facts.py`'s `settle_drift` decides it, and
    # `tests/test_sibling_drift.py` drives both pushes through it.
    settle_drift(
        [
            *read.unreadable,
            *release_contradictions(defaults, read.found, INSTALLED, AWAITING_FIRST_ADOPTION),
        ]
    )
    # llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


def test_a_declaration_whose_schema_names_no_target_is_counted_by_its_ids(
    tmp_path: Path,
) -> None:
    """A sibling's later schema is its own business, and never reads as declaring less."""
    checkout = scratch_checkout(
        tmp_path / "printobserver",
        "github.com/nickderobertis/printobserver",
        {
            "release-targets.toml": 'schema_version = 2\n\n[[target]]\nid = "crate:a"\n\n'
            '[[target]]\nid = "pypi:b"\n'
        },
    )

    assert declared_targets(checkout) == {"crate:a": "crate:a", "pypi:b": "pypi:b"}
