"""Among several checkouts of one identity, the origin form selects the publication checkout.

A plan now names its target repository as a normalized origin in the task record's own
`repositories`, and the engine hands that origin to `onevcs` verbatim. `onevcs resolve
<origin>` answers with **one** of the identity's registered checkouts — `store::resolve`
at `onevcs` v0.21.0 takes the first `BTreeMap` entry whose identity matches, so it is the
checkout whose alias sorts first. On this host that is the canonical checkout for
`ai-orchestrator` (`ai-orchestrator` < `ai-orchestrator-isolated` <
`ai-orchestrator-isolated-2`) and for `printobserver`, by the accident of their names: a
checkout registered later under a name that sorts earlier would redirect every
publication of that identity into it, and nothing would say so until a landing fast-
forwarded the wrong directory.

So the fact is gated rather than remembered. For every identity with more than one held
checkout, the publication checkout the origin form resolves is compared with the one
the first alias `config/onevcs.checkouts` lists for it resolves — read from the
installed `onevcs`, never restated as a rule of the registry. Two controlled registries
prove the comparison can tell the two cases apart, and the live registry is what it is
for.

Uncached, because the subject is this host's registry and the installed CLI: neither is
under any `nx.json` key, and a memoized green here would replay across the very
re-registration it exists to catch.
"""

# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_checkouts` is
# not a narrower key inside a memoized tier: it moves a test out of every memoized tier
# into the uncached `orchestrator:test-checkouts`, because its subject — this host's
# registry and the `onevcs` under `.venv`, which no `nx.json` glob hashes — is outside
# this workspace. A project of its own would give this gate a key, and a memoized green
# would replay across the very re-registration it exists to catch. `tests/conftest.py`'s
# own checkout guard states that reasoning where it enforces the marker, and every
# `reads_checkouts` test in this repository is tiered this way for it.

from __future__ import annotations

from pathlib import Path

import pytest
from registered_checkouts import Disagreement, publication_resolutions
from scratch_identity import seeded

#: A hosted scratch identity with two checkouts, as this host's own pairs are registered.
ORIGIN = "github.com/scratchowner/paired"

#: The execution checkout's alias in both controlled registries. The publication
#: checkout's is what each registry varies: one sorting before it, one after.
EXECUTION = "checkout-isolated"

pytestmark = pytest.mark.reads_checkouts


def _controlled(root: Path, publication: str) -> tuple[Path, Path]:
    """A scratch registry holding one identity, its publication checkout listed first.

    Answers the manifest `seeded` wrote — publication first, then execution, which is
    the order `config/onevcs.checkouts` lists this host's own pairs in — and the
    registry's home.
    """
    identity = seeded(root, publication=publication, execution=EXECUTION, origin=ORIGIN)
    return root / "onevcs.checkouts", identity.home


def test_a_publication_alias_that_sorts_first_is_what_the_origin_form_selects(
    tmp_path: Path,
) -> None:
    """The arrangement this host has: the two spellings agree, and the check passes."""
    manifest, home = _controlled(tmp_path, "checkout")

    resolved = publication_resolutions(manifest, home)

    assert resolved.disagreeing == (), resolved
    assert resolved.agreeing == (ORIGIN,), resolved
    assert resolved.unresolved == {}, resolved


def test_a_publication_alias_that_sorts_after_the_safety_clone_is_named_with_both_checkouts(
    tmp_path: Path,
) -> None:
    """The arrangement this check exists to catch, named the way an operator has to read it.

    `zcheckout` is the publication checkout an operator listed first and means to publish
    from; `checkout-isolated` is the safety clone whose alias sorts before it, and so the
    checkout the origin form actually selects. The finding names the identity and both.
    """
    manifest, home = _controlled(tmp_path, "zcheckout")

    resolved = publication_resolutions(manifest, home)

    assert resolved.disagreeing == (
        Disagreement(
            ORIGIN,
            (tmp_path / EXECUTION).resolve(),
            "zcheckout",
            (tmp_path / "zcheckout").resolve(),
        ),
    ), resolved
    assert resolved.agreeing == (), resolved


def test_every_identity_this_host_holds_several_checkouts_of_publishes_from_the_first() -> None:
    """The live registry: the origin form and the first listed alias select one checkout.

    An identity this registry does not hold both spellings of is reported rather than
    counted either way, because a registry that cannot answer is not evidence about how
    it selects.
    """
    resolved = publication_resolutions()

    assert resolved.disagreeing == (), "\n".join(
        f"{found.identity}: `onevcs resolve {found.identity}` publishes from "
        f"{found.by_origin}, while its first listed checkout {found.first_alias!r} is "
        f"{found.by_first_alias}; a checkout registered under a name that sorts earlier "
        f"has redirected this identity's publications"
        for found in resolved.disagreeing
    )
    assert not resolved.unresolved, resolved.unresolved


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
