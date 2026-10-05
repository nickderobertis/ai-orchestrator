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

import os
import shutil
from pathlib import Path

import pytest
from registered_checkouts import Disagreement, publication_resolutions, resolution_findings
from scratch_identity import seeded
from sibling_facts import (
    REGISTRATION_FILES,
    SiblingDrift,
    commit_change,
    published_clone,
    settle_drift,
)

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

    Which checkouts this host holds, and how the registry over them answers, move with no
    change here, so every finding — a disagreement, an identity a spelling could not be
    resolved for, a checkout whose identity could not be read — is settled by
    `tests/sibling_facts.py`: failed for a change editing a registration file, reported as
    drift for any other.
    """
    # llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] Settled rather than
    # asserted: ai-orchestrator#1529 rules that a check whose subject is a sibling's live state
    # refuses only a push changing one of the five registration files and reports the drift
    # otherwise; `tests/sibling_facts.py`'s `settle_drift` decides it, and this module's
    # fixture tests drive both pushes through it.
    settle_drift(resolution_findings(publication_resolutions()))
    # llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


#: The two changes the live comparison's findings are settled for below: one editing a
#: registration file, which fails, and one editing only a persona, which is reported.
PERSONA = "personas/crozier/crozier-corpus.yaml"
PUSHES = ("config/onevcs.checkouts", PERSONA)


def _settle(tmp_path: Path, findings: list[str], said: str, changed: str) -> None:
    """Settle ``findings`` for a change editing ``changed``, failing or reporting as it should."""
    assert findings, "the fixture produced nothing to settle"
    clone = published_clone(tmp_path / "gated", (PERSONA,))
    commit_change(clone, changed)
    if changed in REGISTRATION_FILES:
        with pytest.raises(pytest.fail.Exception, match=f"this change edits {changed}") as failed:
            settle_drift(findings, root=clone, comparison={})
        assert said in str(failed.value)
    else:
        with pytest.warns(SiblingDrift) as reported:
            settle_drift(findings, root=clone, comparison={})
        assert said in str(reported[0].message)


@pytest.mark.parametrize("changed", PUSHES)
def test_a_redirected_publication_is_settled_by_what_the_change_touches(
    tmp_path: Path, changed: str
) -> None:
    """The disagreement the live comparison exists to catch, settled both ways."""
    manifest, home = _controlled(tmp_path / "registry", "zcheckout")

    findings = resolution_findings(publication_resolutions(manifest, home))

    _settle(tmp_path, findings, "first listed checkout 'zcheckout'", changed)


#: `onevcs resolve` stand-ins, one per way its answer cannot be read, each with what the
#: finding says. `None` is a CLI present but not executable, which no process can run.
UNREADABLE_ANSWERS = {
    "no-answer": ("echo 'no such checkout' >&2; exit 1", "answered no publication checkout"),
    "os-error": (None, "could not run"),
    "not-utf-8": ("printf '\\377\\n'", "not UTF-8"),
    "not-json": ("echo 'publication checkout: here'", "not JSON"),
    "misshapen": ("echo '{\"checkouts\": []}'", "naming no `publication_checkout`"),
}


@pytest.mark.parametrize("changed", PUSHES)
@pytest.mark.parametrize(("body", "said"), UNREADABLE_ANSWERS.values(), ids=UNREADABLE_ANSWERS)
def test_an_answer_that_cannot_be_read_is_settled_rather_than_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str | None, said: str, changed: str
) -> None:
    """Each way `onevcs resolve` fails to answer is a finding naming the identity."""
    manifest, home = _controlled(tmp_path / "registry", "checkout")
    stand_in = tmp_path / "bin" / "onevcs"
    stand_in.parent.mkdir()
    if body is None:
        # A process searches past a file it cannot execute, so the PATH holds no other
        # `onevcs` to find: only `git`, which reading the manifest's identities needs.
        git = shutil.which("git")
        assert git is not None
        (stand_in.parent / "git").symlink_to(git)
        stand_in.write_text("#!/bin/sh\n", encoding="utf-8")
        stand_in.chmod(0o644)
        path = str(stand_in.parent)
    else:
        stand_in.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        stand_in.chmod(0o755)
        path = f"{stand_in.parent}:{os.environ['PATH']}"

    with monkeypatch.context() as patched:
        patched.setenv("PATH", path)
        resolved = publication_resolutions(manifest, home)

    assert list(resolved.unresolved) == [ORIGIN], resolved
    assert said in resolved.unresolved[ORIGIN], resolved.unresolved
    _settle(tmp_path, resolution_findings(resolved), said, changed)


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
