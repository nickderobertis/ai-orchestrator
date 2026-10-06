"""Release adoption's readers, over scratch siblings in the shapes this host's have been in.

`tests/e2e/test_release_adoption_in_force_e2e.py` asks this host's own checkouts which
registered repositories declare release targets and what this host resolves over them;
what it reads them with is `tests/sibling_facts.py`, driven here over real checkouts
made for the purpose: one declaring nothing, one that gained targets and a language
after it was registered, and one that dropped the default target the tracked override
names for it. Which repositories declare, and how many targets, is derived from the
declarations as they now stand — never a list kept here — and only the contradiction is
named.
"""

from __future__ import annotations

from pathlib import Path

from sibling_facts import (
    RELEASES,
    advance,
    declaration,
    declared_targets,
    declaring_identities,
    default_target_complaint,
    held_checkouts,
    override_default_targets,
    release_contradictions,
    scratch_checkout,
)

from orchestrator.host_installs import AWAITING_FIRST_ADOPTION, INSTALLED, by_producer

#: Three scratch siblings, each the shape one of this host's producers has been in: one
#: declaring nothing and named by no rule, one that gained targets and a language after
#: it was registered, and one whose declaration dropped the default target the tracked
#: override names.
QUIET = "github.com/nickderobertis/unruled-producer"
GROWN = "github.com/nickderobertis/onebudgetspec"
CONTRADICTING = "github.com/nickderobertis/onejudge"


def _siblings(tmp_path: Path) -> Path:
    quiet = scratch_checkout(tmp_path / "unruled-producer", QUIET, {"README.md": "x\n"})
    grown = scratch_checkout(
        tmp_path / "onebudgetspec",
        GROWN,
        {"release-targets.toml": declaration({"pypi": "pypi:onebudgetspec-cli"})},
    )
    contradicting = scratch_checkout(
        tmp_path / "onejudge",
        CONTRADICTING,
        {"release-targets.toml": declaration({"cli": "pypi:onejudge-cli"})},
    )
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{quiet}\n{grown}\n{contradicting}\n", encoding="utf-8")
    advance(
        grown,
        {
            "release-targets.toml": declaration(
                {
                    "crate": "crate:onebudgetspec",
                    "pypi": "pypi:onebudgetspec-cli",
                    "npm": "npm:@onebudgetspec/cli",
                }
            ),
            "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
        },
    )
    advance(contradicting, {"release-targets.toml": declaration({"crate": "crate:onejudge"})})
    return manifest


def test_the_declaring_set_is_derived_from_the_siblings_as_they_now_stand(
    tmp_path: Path,
) -> None:
    """A sibling declaring nothing is left out; one that grew is counted as it now is."""
    manifest = _siblings(tmp_path)

    declaring = declaring_identities(manifest)

    assert declaring.counts == {GROWN: 3, CONTRADICTING: 1}
    assert declaring.unreadable == []


def test_only_the_sibling_contradicting_the_tracked_override_is_named(tmp_path: Path) -> None:
    """Growing fails nothing; dropping the override's default target is named, with it."""
    manifest = _siblings(tmp_path)
    declarations = {
        identity: declared_targets(paths[0]) for identity, paths in held_checkouts(manifest).items()
    }
    defaults = override_default_targets(RELEASES.read_text(encoding="utf-8"))

    assert release_contradictions(defaults, declarations, INSTALLED, AWAITING_FIRST_ADOPTION) == [
        f"{CONTRADICTING} no longer declares the default target 'cli' "
        "config/onevcs.releases.yml names for it; it declares ['crate']"
    ]


def test_an_installed_producer_resolves_the_default_its_row_installs() -> None:
    """The resolution the next journey reads is accepted for the row's target alone."""
    defaults = override_default_targets(RELEASES.read_text(encoding="utf-8"))
    row = by_producer(GROWN)
    assert row is not None and GROWN not in AWAITING_FIRST_ADOPTION
    assert defaults[GROWN] == row.target

    assert default_target_complaint(GROWN, defaults[GROWN], defaults) is None
    assert default_target_complaint(GROWN, None, defaults) is not None
    assert default_target_complaint(QUIET, "pypi", defaults) is not None
    assert default_target_complaint(QUIET, None, defaults) is None
    assert default_target_complaint(CONTRADICTING, "cli", defaults) is None
