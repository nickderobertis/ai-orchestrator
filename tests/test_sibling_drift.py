"""A sibling's contradiction refuses only the change that edits this host's registration.

`tests/sibling_facts.py` settles every disagreement between a registered sibling and this
repository's registration by what the change being checked touches: the change edits one
of the registration files, and it fails; it edits none, and the disagreement is reported
as a `SiblingDrift` warning. Driven here on real clones of a real origin, through the
real `scripts/comparison-base.sh` the gate resolves its base with, once for a branch that
touches a registration file and once for one that does not — and with the contradiction
a real reader finds in a scratch producer, so what is settled is what a host would see.
The persona reconciliations are driven the same way: what the real recipe and identifier
readers report of a drifted persona against a stand-in of the repository it reviews, once
for a branch editing a registration file and once for one editing only that persona.

llmlint: ignore-file[shell_test_tiers_stay_split] Not a shell test suite: a pytest check of
the Python settlement in `tests/sibling_facts.py`, which resolves its base through the
gate's own `scripts/comparison-base.sh` because that is the base a publication judges. The
memoized `codeWorkspace` key covers `scripts/`, so what these tests run is inside the key
of the tier they run in.
"""

from __future__ import annotations

import os
import re
import subprocess
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from persona_identifiers import identifiers_at, undefined_identifiers
from persona_recipes import persona_at, undefined_recipes
from persona_stand_in import stand_in_for
from registered_checkouts import discover_checkouts
from sibling_facts import (
    REGISTRATION_FILES,
    SiblingDrift,
    advance,
    commit_change,
    declaration,
    declared_targets,
    declaring_identities,
    held_checkouts,
    published_clone,
    read_declarations,
    registration_changes,
    release_contradictions,
    scratch_checkout,
    settle_drift,
)

from orchestrator.host_installs import INSTALLED
from orchestrator.root import REPO_ROOT

PRODUCER = "github.com/nickderobertis/onejudge"

#: The repo-specific persona whose reconciliations are settled below, and the two drifts
#: put into a copy of it: a recipe its repository does not define and a path it does not
#: track, one for each reconciliation.
PERSONA = "personas/crozier/crozier-corpus.yaml"
DRIFTS = {"`just check`": "`just gate`", "tests/e2e.rs": "tests/corpus-e2e.rs"}


def _git(cwd: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True)


def _clone_of_a_published_base(tmp_path: Path) -> Path:
    return published_clone(tmp_path / "gated", (PERSONA,))


def _contradiction(tmp_path: Path) -> list[str]:
    """What the readers find in a producer that dropped the override's default target."""
    checkout = scratch_checkout(
        tmp_path / "onejudge",
        PRODUCER,
        {"release-targets.toml": declaration({"crate": "crate:onejudge"})},
    )
    return release_contradictions(
        {PRODUCER: "cli"}, {PRODUCER: declared_targets(checkout)}, INSTALLED, frozenset()
    )


@pytest.mark.parametrize("registration", REGISTRATION_FILES)
def test_a_change_editing_a_registration_file_fails_for_the_contradiction(
    tmp_path: Path, registration: str
) -> None:
    clone = _clone_of_a_published_base(tmp_path)
    commit_change(clone, registration)
    findings = _contradiction(tmp_path)

    assert registration_changes(clone, {}) == (registration,)
    with pytest.raises(pytest.fail.Exception) as failed:
        settle_drift(findings, root=clone, comparison={})

    assert f"this change edits {registration}" in str(failed.value)
    assert PRODUCER in str(failed.value)
    assert "'cli'" in str(failed.value)


def test_a_change_editing_no_registration_file_reports_the_contradiction_without_failing(
    tmp_path: Path,
) -> None:
    clone = _clone_of_a_published_base(tmp_path)
    commit_change(clone, "README.md")
    findings = _contradiction(tmp_path)

    assert registration_changes(clone, {}) == ()
    with pytest.warns(SiblingDrift) as reported:
        settle_drift(findings, root=clone, comparison={})

    assert PRODUCER in str(reported[0].message)
    assert "'cli'" in str(reported[0].message)


def test_an_uncommitted_edit_to_a_registration_file_counts_as_the_change(
    tmp_path: Path,
) -> None:
    """A worker's own run of the checks sees the edit it is about to commit."""
    clone = _clone_of_a_published_base(tmp_path)
    commit_change(clone, "README.md")
    (clone / "config" / "onevcs.workspaces.yml").write_text("edited\n", encoding="utf-8")

    assert registration_changes(clone, {}) == ("config/onevcs.workspaces.yml",)
    with pytest.raises(pytest.fail.Exception):
        settle_drift(_contradiction(tmp_path), root=clone, comparison={})


def test_a_named_comparison_base_is_the_one_the_change_is_measured_against(
    tmp_path: Path,
) -> None:
    """The base the publication names decides, not the one discovery would pick."""
    clone = _clone_of_a_published_base(tmp_path)
    commit_change(clone, "config/onevcs.rules.yml")
    _git(clone, "push", "-q", "origin", "work")
    commit_change(clone, "README.md")

    assert registration_changes(clone, {}) == ("config/onevcs.rules.yml",)
    assert registration_changes(clone, {"ONEVCS_COMPARISON_BASE": "work"}) == ()


def test_a_tree_with_no_comparison_base_fails_rather_than_assuming_it_is_unrelated(
    tmp_path: Path,
) -> None:
    lone = tmp_path / "lone"
    lone.mkdir()
    _git(lone, "init", "-q", "-b", "main")
    _git(lone, "commit", "-q", "--allow-empty", "-m", "chore: start")

    assert registration_changes(lone, {}) is None
    with pytest.raises(pytest.fail.Exception, match="no comparison base resolved"):
        settle_drift(_contradiction(tmp_path), root=lone, comparison={})


def test_nothing_to_settle_neither_fails_nor_reports(tmp_path: Path) -> None:
    """No finding asks no question: not even a tree with no base is failed or warned of."""
    lone = tmp_path / "lone"
    lone.mkdir()

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        settle_drift([], root=lone, comparison={})


def _persona_report(tmp_path: Path, reconciliation: str) -> str:
    """What one real persona reconciliation reports of a drifted copy, against a stand-in."""
    tracked = REPO_ROOT / PERSONA
    stand_in = stand_in_for(tracked, tmp_path / "stand-in", {})
    drifted = tmp_path / "drifted" / tracked.parent.name / tracked.name
    drifted.parent.mkdir(parents=True)
    prose = tracked.read_text(encoding="utf-8")
    for corrected, drift in DRIFTS.items():
        assert corrected in prose, f"{PERSONA} no longer names {corrected}, so it cannot drift"
        prose = prose.replace(corrected, drift)
    drifted.write_text(prose, encoding="utf-8")
    report = (
        undefined_recipes(persona_at(drifted), stand_in)
        if reconciliation == "recipes"
        else undefined_identifiers(identifiers_at(drifted), stand_in)
    )
    assert report is not None, f"the {reconciliation} reconciliation reported no drift"
    return report


@pytest.mark.parametrize(
    ("reconciliation", "drift"),
    [("recipes", "`just gate`"), ("identifiers", "`tests/corpus-e2e.rs`")],
)
def test_a_persona_reconciliation_fails_a_change_editing_a_registration_file(
    tmp_path: Path, reconciliation: str, drift: str
) -> None:
    clone = _clone_of_a_published_base(tmp_path)
    commit_change(clone, "config/onevcs.checkouts")
    report = _persona_report(tmp_path, reconciliation)

    with pytest.raises(pytest.fail.Exception) as failed:
        settle_drift([report], root=clone, comparison={})

    assert "this change edits config/onevcs.checkouts" in str(failed.value)
    assert drift in str(failed.value)


@pytest.mark.parametrize(
    ("reconciliation", "drift"),
    [("recipes", "`just gate`"), ("identifiers", "`tests/corpus-e2e.rs`")],
)
def test_a_persona_reconciliation_reports_a_persona_only_change_without_failing(
    tmp_path: Path, reconciliation: str, drift: str
) -> None:
    clone = _clone_of_a_published_base(tmp_path)
    commit_change(clone, PERSONA)
    report = _persona_report(tmp_path, reconciliation)

    assert registration_changes(clone, {}) == ()
    with pytest.warns(SiblingDrift) as reported:
        settle_drift([report], root=clone, comparison={})

    assert drift in str(reported[0].message)


#: What the finding says of a declaration that parses to a shape the reader does not know.
MISSHAPEN = "parses, but its `target` is not an array of tables"
#: Declarations a producer's fetched base may carry that say nothing this reader can read,
#: each with what the finding says of it: one no TOML parser reads, two that parse to a
#: `target` that is not an array of tables — a table, and an array of strings — and one
#: whose bytes are not UTF-8, which TOML requires and no parser is handed.
UNREADABLE = {
    "unparsable": ("[[target]\nid = \n", "is not valid TOML"),
    "target-is-a-table": ('[target]\nid = "pypi:onejudge-cli"\n', MISSHAPEN),
    "target-holds-strings": ('target = ["pypi:onejudge-cli"]\n', MISSHAPEN),
    "not-utf-8": (b'[[target]]\nid = "pypi:onejudge-\xff\xfecli"\n', "is not UTF-8"),
}


def _unreadable_declaration(tmp_path: Path, document: str | bytes) -> list[str]:
    """What the readers find in a producer whose fetched base carries ``document``."""
    checkout = scratch_checkout(tmp_path / "onejudge", PRODUCER, {"release-targets.toml": document})
    read = read_declarations({PRODUCER: checkout})
    assert PRODUCER not in read.found
    return read.unreadable


@pytest.mark.parametrize(("document", "said"), UNREADABLE.values(), ids=UNREADABLE)
def test_an_unreadable_declaration_fails_a_change_editing_a_registration_file(
    tmp_path: Path, document: str | bytes, said: str
) -> None:
    clone = _clone_of_a_published_base(tmp_path)
    commit_change(clone, "config/onevcs.releases.yml")

    with pytest.raises(pytest.fail.Exception) as failed:
        settle_drift(_unreadable_declaration(tmp_path, document), root=clone, comparison={})

    assert "this change edits config/onevcs.releases.yml" in str(failed.value)
    assert PRODUCER in str(failed.value)
    assert f"release-targets.toml at origin/main {said}" in str(failed.value)


@pytest.mark.parametrize(("document", "said"), UNREADABLE.values(), ids=UNREADABLE)
def test_an_unreadable_declaration_is_reported_for_a_persona_only_change(
    tmp_path: Path, document: str | bytes, said: str
) -> None:
    clone = _clone_of_a_published_base(tmp_path)
    commit_change(clone, PERSONA)

    with pytest.warns(SiblingDrift) as reported:
        settle_drift(_unreadable_declaration(tmp_path, document), root=clone, comparison={})

    assert f"release-targets.toml at origin/main {said}" in str(reported[0].message)


#: What a recipe runner may print for `--dump --dump-format json` that this reader cannot
#: read: no JSON at all, JSON whose `recipes` is not the map of names it expects, and
#: bytes that are not UTF-8.
UNREAD_DUMPS = {
    "not-json": b"recipes: check\n",
    "recipes-is-a-list": b'{"recipes": ["check"]}\n',
    "not-utf-8": b'{"recipes": \xff\xfe}\n',
}


@pytest.mark.parametrize("dump", UNREAD_DUMPS.values(), ids=UNREAD_DUMPS)
def test_a_recipe_dump_of_an_unknown_shape_is_settled_as_drift_rather_than_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dump: bytes
) -> None:
    """The persona recipe reconciliation reads a sibling's justfile through `just`'s own dump."""
    runner = tmp_path / "bin" / "just"
    runner.parent.mkdir()
    (tmp_path / "dump.json").write_bytes(dump)
    runner.write_text(f"#!/bin/sh\ncat {tmp_path / 'dump.json'}\n", encoding="utf-8")
    runner.chmod(0o755)
    monkeypatch.setenv("PATH", f"{runner.parent}:{os.environ['PATH']}")
    stand_in = stand_in_for(REPO_ROOT / PERSONA, tmp_path / "stand-in", {})
    report = undefined_recipes(persona_at(REPO_ROOT / PERSONA), stand_in)
    assert report is not None and "could not parse the recipes" in report

    persona_only = published_clone(tmp_path / "persona-only", (PERSONA,))
    commit_change(persona_only, PERSONA)
    with pytest.warns(SiblingDrift, match="could not parse the recipes"):
        settle_drift([report], root=persona_only, comparison={})

    registering = published_clone(tmp_path / "registering", (PERSONA,))
    commit_change(registering, "config/onevcs.checkouts")
    with pytest.raises(pytest.fail.Exception, match="could not parse the recipes"):
        settle_drift([report], root=registering, comparison={})


#: A tracked file whose name is not UTF-8, declaring the persona's literal type: what a
#: sibling's tree may hold, read by both identifier reconciliations' git searches.
ODD_NAME = os.fsdecode(b"src/odd-\xff.rs")


@pytest.mark.parametrize(
    ("changed", "settles"), [("config/onevcs.checkouts", "fails"), (PERSONA, "reports")]
)
def test_a_tracked_name_that_is_not_utf_8_is_read_and_settled_rather_than_raised(
    tmp_path: Path, changed: str, settles: str
) -> None:
    tracked = REPO_ROOT / PERSONA
    stand_in = stand_in_for(tracked, tmp_path / "stand-in", {})
    advance(stand_in, {ODD_NAME: "struct Corpus {\n    unmatched: bool,\n}\n"})
    clone = published_clone(tmp_path / "gated", (PERSONA,))
    commit_change(clone, changed)

    report = undefined_identifiers(identifiers_at(tracked), stand_in)
    assert report is not None and "declares no brace-delimited `Corpus`" in report

    if settles == "fails":
        with pytest.raises(pytest.fail.Exception, match=f"this change edits {changed}"):
            settle_drift([report], root=clone, comparison={})
    else:
        with pytest.warns(SiblingDrift, match="declares no brace-delimited `Corpus`"):
            settle_drift([report], root=clone, comparison={})


@contextmanager
def _unenterable(directory: Path) -> Iterator[None]:
    """``directory`` with every permission taken away for the span: a checkout no read enters."""
    if os.geteuid() == 0:
        pytest.skip("root enters a directory whatever its mode, so none is unenterable")
    directory.chmod(0)
    try:
        yield
    finally:
        directory.chmod(0o755)


def _settle_for_change(tmp_path: Path, findings: list[str], said: str, changed: str) -> None:
    """Settle ``findings`` for a change editing ``changed``, failing or reporting as it should."""
    clone = published_clone(tmp_path / f"gated-{len(list(tmp_path.glob('gated-*')))}", (PERSONA,))
    commit_change(clone, changed)
    if changed in REGISTRATION_FILES:
        with pytest.raises(pytest.fail.Exception, match=f"this change edits {changed}") as failed:
            settle_drift(findings, root=clone, comparison={})
        assert said in str(failed.value)
    else:
        with pytest.warns(SiblingDrift, match=re.escape(said)):
            settle_drift(findings, root=clone, comparison={})


#: The two changes every live-sibling reading below is settled for: one editing a
#: registration file, which fails, and one editing only a persona, which is reported.
PUSHES = ("config/onevcs.releases.yml", PERSONA)


@pytest.mark.parametrize("changed", PUSHES)
def test_a_producer_checkout_this_host_cannot_enter_is_settled_rather_than_read_as_silent(
    tmp_path: Path, changed: str
) -> None:
    """An OS error reading a producer is a finding, never a producer declaring nothing."""
    checkout = scratch_checkout(
        tmp_path / "onejudge",
        PRODUCER,
        {"release-targets.toml": declaration({"cli": "pypi:onejudge-cli"})},
    )
    with _unenterable(checkout):
        read = read_declarations({PRODUCER: checkout})

    assert PRODUCER not in read.found
    assert len(read.unreadable) == 1 and f"{checkout} cannot be read" in read.unreadable[0]
    _settle_for_change(tmp_path, read.unreadable, f"{checkout} cannot be read", changed)


#: crozier's `Corpus` registry type, declaring every field the tracked persona's literal names.
CORPUS_DECLARATION = (
    "pub struct Corpus {\n"
    "    pub name: &'static str,\n"
    "    pub unmatched: &'static [&'static str],\n"
    "}\n"
)
#: A second file declaring the same type, which the reader has to read as well.
SECOND_DECLARATION = "src/also.rs"


@pytest.mark.parametrize("changed", PUSHES)
def test_a_reviewed_checkout_this_host_cannot_enter_is_settled_by_both_persona_readers(
    tmp_path: Path, changed: str
) -> None:
    tracked = REPO_ROOT / PERSONA
    stand_in = stand_in_for(tracked, tmp_path / "stand-in", {"tests/e2e.rs": CORPUS_DECLARATION})
    with _unenterable(stand_in):
        identifiers = undefined_identifiers(identifiers_at(tracked), stand_in)
        recipes = undefined_recipes(persona_at(tracked), stand_in)

    for report in (identifiers, recipes):
        assert report is not None and f"{stand_in} cannot be read" in report, report
        _settle_for_change(tmp_path, [report], f"{stand_in} cannot be read", changed)


@pytest.mark.parametrize("changed", PUSHES)
def test_an_unreadable_declaration_is_named_though_a_readable_one_declares_every_field(
    tmp_path: Path, changed: str
) -> None:
    """A declaration the reader could not read is a finding, whatever the others declare."""
    tracked = REPO_ROOT / PERSONA
    stand_in = stand_in_for(
        tracked,
        tmp_path / "stand-in",
        {"tests/e2e.rs": CORPUS_DECLARATION, SECOND_DECLARATION: CORPUS_DECLARATION},
    )
    assert undefined_identifiers(identifiers_at(tracked), stand_in) is None
    unreadable = stand_in / SECOND_DECLARATION
    unreadable.chmod(0)
    try:
        if os.access(unreadable, os.R_OK):
            pytest.skip("this process reads a file whatever its mode")
        report = undefined_identifiers(identifiers_at(tracked), stand_in)
    finally:
        unreadable.chmod(0o644)

    assert report is not None and SECOND_DECLARATION in report, report
    _settle_for_change(tmp_path, [report], SECOND_DECLARATION, changed)


@pytest.mark.parametrize("changed", PUSHES)
def test_a_declaration_that_is_not_utf_8_is_named_though_every_field_is_still_declared(
    tmp_path: Path, changed: str
) -> None:
    """Replacing the bad bytes would leave every field readable, so decoding decides."""
    tracked = REPO_ROOT / PERSONA
    stand_in = stand_in_for(tracked, tmp_path / "stand-in", {"tests/e2e.rs": CORPUS_DECLARATION})
    advance(stand_in, {SECOND_DECLARATION: CORPUS_DECLARATION.encode() + b"// \xff\xfe\n"})

    report = undefined_identifiers(identifiers_at(tracked), stand_in)

    assert report is not None and f"{SECOND_DECLARATION} is not UTF-8" in report, report
    _settle_for_change(tmp_path, [report], f"{SECOND_DECLARATION} is not UTF-8", changed)


#: A second registered identity, so a listed checkout that cannot be read sits beside one
#: that can, and a path listed for a host layout this host does not have.
OTHER = "github.com/nickderobertis/oneharness"
ABSENT = "not-on-this-host"


def _listed(tmp_path: Path, *paths: Path) -> Path:
    manifest = tmp_path / "checkouts"
    manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    return manifest


def _held_settled(
    tmp_path: Path, manifest: Path, said: str, changed: str
) -> dict[str, list[Path]] | None:
    """`held_checkouts` over ``manifest``, settled for a change editing ``changed``.

    None where the change edits a registration file and the finding failed it.
    """
    clone = published_clone(tmp_path / f"gated-{len(list(tmp_path.glob('gated-*')))}", (PERSONA,))
    commit_change(clone, changed)
    if changed in REGISTRATION_FILES:
        with pytest.raises(pytest.fail.Exception, match=f"this change edits {changed}") as failed:
            held_checkouts(manifest, root=clone, comparison={})
        assert said in str(failed.value)
        return None
    with pytest.warns(SiblingDrift, match=re.escape(said)):
        return held_checkouts(manifest, root=clone, comparison={})


@pytest.mark.parametrize("changed", PUSHES)
def test_a_listed_checkout_this_host_cannot_enter_is_named_and_the_readable_ones_kept(
    tmp_path: Path, changed: str
) -> None:
    """Discovery keeps every readable checkout, names the unreadable, and skips the absent."""
    readable = scratch_checkout(tmp_path / "onejudge", PRODUCER, {"README.md": "x\n"})
    blocked = scratch_checkout(tmp_path / "oneharness", OTHER, {"README.md": "x\n"})
    manifest = _listed(tmp_path, tmp_path / ABSENT, blocked, readable)

    with _unenterable(blocked):
        discovered = discover_checkouts(manifest)
        declaring = declaring_identities(manifest)
        held = _held_settled(tmp_path, manifest, f"{blocked} cannot be read", changed)

    assert discovered.held == {PRODUCER: [readable]}
    assert len(discovered.unreadable) == 1, discovered.unreadable
    assert f"{blocked} cannot be read" in discovered.unreadable[0]
    assert ABSENT not in discovered.unreadable[0]
    assert any(f"{blocked} cannot be read" in finding for finding in declaring.unreadable)
    assert held in (None, {PRODUCER: [readable]})


@pytest.mark.parametrize("changed", PUSHES)
def test_a_git_that_cannot_start_is_named_for_every_listed_checkout_rather_than_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    checkout = scratch_checkout(tmp_path / "onejudge", PRODUCER, {"README.md": "x\n"})
    manifest = _listed(tmp_path, checkout)
    nowhere = tmp_path / "no-git-here"
    nowhere.mkdir()

    with monkeypatch.context() as patched:
        patched.setenv("PATH", str(nowhere))
        discovered = discover_checkouts(manifest)

    assert discovered.held == {}
    assert len(discovered.unreadable) == 1, discovered.unreadable
    assert (
        f"{checkout} cannot be read: `git remote` could not run there" in (discovered.unreadable[0])
    )
    _settle_for_change(tmp_path, list(discovered.unreadable), f"{checkout} cannot be read", changed)


#: Origins a listed checkout may carry that name no identity, each with what discovery
#: says of it: bytes that are not UTF-8, no origin at all, and one with no host or owner.
UNREADABLE_ORIGINS = {
    "not-utf-8": (b"onejudge-\xff.git", "is not UTF-8"),
    "no-origin": (None, "answered no origin"),
    "no-identity": (b"just-a-name", "names no host/owner/name identity"),
}


@pytest.mark.parametrize("changed", PUSHES)
@pytest.mark.parametrize(("origin", "said"), UNREADABLE_ORIGINS.values(), ids=UNREADABLE_ORIGINS)
def test_an_origin_naming_no_identity_is_named_with_its_checkout_rather_than_guessed(
    tmp_path: Path, origin: bytes | None, said: str, changed: str
) -> None:
    readable = scratch_checkout(tmp_path / "oneharness", OTHER, {"README.md": "x\n"})
    odd = scratch_checkout(tmp_path / "onejudge", PRODUCER, {"README.md": "x\n"})
    config = odd / ".git" / "config"
    if origin is None:
        _git(odd, "remote", "remove", "origin")
    else:
        url = f"https://{PRODUCER}.git".encode()
        config.write_bytes(config.read_bytes().replace(url, origin))
    manifest = _listed(tmp_path, odd, readable)

    discovered = discover_checkouts(manifest)

    assert discovered.held == {OTHER: [readable]}
    assert len(discovered.unreadable) == 1, discovered.unreadable
    assert str(odd) in discovered.unreadable[0] and said in discovered.unreadable[0]
    held = _held_settled(tmp_path, manifest, said, changed)
    assert held in (None, {OTHER: [readable]})


def _settled_under_the_gate_environment(
    tmp_path: Path, report: str, said: str, changed: str
) -> None:
    """Settle ``report`` for a change editing ``changed``, outside any fixture's isolation.

    The consumer clone is read under the environment the gate runs in, so first it is
    shown to measure the change as it should — the registration file, or nothing — and
    then the report is failed or reported as that measure decides.
    """
    clone = published_clone(tmp_path / "gated", (PERSONA,))
    commit_change(clone, changed)
    assert registration_changes(clone, {}) == ((changed,) if changed in REGISTRATION_FILES else ())
    _settle_for_change(tmp_path / "settled", [report], said, changed)


#: Justfiles a reviewed checkout may hold that its recipe runner cannot read, each with
#: what the runner itself says: one Just's own lexer refuses, and one that is gone.
UNREAD_JUSTFILES: dict[str, tuple[str | None, str]] = {
    "unterminated-string": ('name := "unterminated\n', "error: Unterminated string"),
    "missing": (None, "error: Failed to read justfile at"),
}


@pytest.mark.parametrize("changed", PUSHES)
@pytest.mark.parametrize(
    ("justfile", "runner_says"), UNREAD_JUSTFILES.values(), ids=UNREAD_JUSTFILES
)
def test_a_reviewed_checkout_whose_justfile_cannot_be_read_is_settled_by_what_the_change_touches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    justfile: str | None,
    runner_says: str,
    changed: str,
) -> None:
    """The recipe reader over a justfile `just` refuses or cannot find, through the real runner.

    `JUST_JUSTFILE` names the stand-in's own justfile, so the runner reads that file and
    never one a directory above the fixture holds.
    """
    stand_in = stand_in_for(REPO_ROOT / PERSONA, tmp_path / "stand-in", {})
    advance(stand_in, {"justfile": justfile})
    with monkeypatch.context() as isolated:
        isolated.setenv("JUST_JUSTFILE", str(stand_in / "justfile"))
        runner = subprocess.run(
            ["just", "--dump", "--dump-format", "json"],
            cwd=stand_in,
            text=True,
            capture_output=True,
            check=False,
        )
        report = undefined_recipes(persona_at(REPO_ROOT / PERSONA), stand_in)

    assert runner.returncode != 0, runner.stdout
    assert runner_says in runner.stderr, runner.stderr
    assert report is not None
    assert f"could not parse the recipes of {stand_in}" in report, report
    assert "could not be reconciled against it at all" in report, report
    _settled_under_the_gate_environment(tmp_path, report, "could not parse the recipes", changed)


def _empty_repository(directory: Path) -> Path:
    directory.mkdir()
    _git(directory, "init", "-q", "-b", "main")
    return directory


def _plain_directory(directory: Path) -> Path:
    directory.mkdir()
    return directory


#: A reviewed checkout that tracks nothing, each with how `git ls-files` ends there: a
#: repository with no commit yet, which is ordinary, and a directory that is no repository.
TRACKING_NOTHING = {
    "empty-repository": (_empty_repository, 0),
    "not-a-repository": (_plain_directory, 128),
}


@pytest.mark.parametrize("changed", PUSHES)
@pytest.mark.parametrize(("make", "listed_exit"), TRACKING_NOTHING.values(), ids=TRACKING_NOTHING)
def test_a_reviewed_checkout_tracking_nothing_is_settled_by_what_the_change_touches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make: Callable[[Path], Path],
    listed_exit: int,
    changed: str,
) -> None:
    """The identifier reader over a tree it can confirm nothing against, settled like drift.

    An empty repository is not malformed; what is reported is that the persona's names
    cannot be reconciled against a tree tracking nothing. Git's discovery stops at the
    fixture's parent, so no repository above it answers for the directory.
    """
    checkout = make(tmp_path / "stand-in")
    with monkeypatch.context() as isolated:
        isolated.setenv("GIT_CEILING_DIRECTORIES", str(checkout.parent))
        for inherited in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            isolated.delenv(inherited, raising=False)
        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=checkout, capture_output=True, check=False
        )
        identifiers = identifiers_at(REPO_ROOT / PERSONA)
        report = undefined_identifiers(identifiers, checkout)

    assert (listed.returncode, listed.stdout) == (listed_exit, b""), listed.stderr
    assert report == (
        f"`git ls-files` listed nothing in {checkout}, so {identifiers.named} could not be "
        "reconciled against it at all"
    )
    _settled_under_the_gate_environment(tmp_path, report, "listed nothing", changed)
