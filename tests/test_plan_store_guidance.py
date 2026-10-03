"""What this checkout says about its plan store, held to the store it configures.

Two documents name the source a plan of this repository is stored in and quote the
repository that source files its issues in, and each has a file that decides it: the
name is read back out of the prose and looked up in the real `onetaskgraph.yaml` rather
than restated here, because a claim checked against a literal in this file would survive
the configuration moving underneath it — and a store the prose names but nothing reaches
is exactly what a half-finished retreat would leave behind.

The one claim here that reads no prose is the artifact half of "in force by merging":
whether the installed plan-store CLI ships any of the crate the two landings that
document classifies that way actually touch, measured on the binary this checkout
installed. The operator rule for adding a board `Status` option is read both ways: the
prose has to state the verb and its `--apply` flag once, and the installed CLI has to
carry them, because a rule naming a verb the binary lacks sends an operator back to the
hand-written mutation the rule exists to forbid. The Linear routing prose in the manager's
document, the operator's and the planner's prompt is held to the configured route and to
every state the `hellopatient` mapping writes.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from plan_sources import read_default_sources, read_plan_sources
from published_tools import ONETASKGRAPH_BIN

from orchestrator import plan_store
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The documents that state where a plan of this repository lives.
MANAGER = "AGENTS.md"
ORCHESTRATION = "docs/orchestration.md"
#: The GitHub Projects source this repository configures and plans against. Named as a
#: literal because this source is never repointed — a live run's settlements are
#: projected back to the project it was launched from — so a value derived from the file
#: could not fail when somebody edited it.
BOARD = "plans"
#: The sentence both documents open their plan-store claim with. The source name inside
#: it is the prose's own answer to "where is a plan of this repository stored", and it
#: is what every check below looks up in the configuration.
PLANNED_AGAINST = re.compile(
    r"A plan of (?:\*\*)?this(?:\*\*)? repository is stored on the `([^`]+)` "
    r"GitHub Projects board"
)
#: The section that records where a plan lives. Every other mention points at it rather
#: than restating it.
STORE_SECTION = "Where a plan of this repository lives"


def _flat(text: str) -> str:
    """One line, so a claim is found wherever the paragraph happens to wrap."""
    return " ".join(text.split())


def _configuration() -> str:
    return (REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8")


def _configured_repository() -> str:
    named = re.search(r"^\s+repository:\s*(\S+)\s*$", _configuration(), re.MULTILINE)
    assert named is not None, (
        "onetaskgraph.yaml's `plans` source has to name the repository its issues are "
        "created in; the adopted release refuses a write without it"
    )
    return named.group(1)


def _document(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def _named_in_prose(name: str) -> str:
    """The source that document tells a reader a plan of this repository is stored in."""
    named = PLANNED_AGAINST.search(_flat(_document(name)))
    assert named is not None, (
        f"{name} has to state which source a plan of this repository is stored in, in the "
        "sentence this gate reads — otherwise nothing holds it to the configured store"
    )
    return named.group(1)


@pytest.mark.parametrize("name", [MANAGER, ORCHESTRATION])
def test_the_repository_the_documents_quote_is_the_repository_the_source_names(
    name: str,
) -> None:
    """Both documents quote the `plans` source's own repository, not a remembered one."""
    configured = _configured_repository()
    quoted = set(re.findall(r"`repository: (\S+)`", _document(name)))
    assert quoted == {configured}, (
        f"{name} quotes `repository: {sorted(quoted)}` where onetaskgraph.yaml names "
        f"{configured}; a board's issues are filed where that file says and nowhere else"
    )


@pytest.mark.parametrize("name", [MANAGER, ORCHESTRATION])
def test_the_source_each_document_names_is_one_the_configuration_defaults_to(
    name: str,
) -> None:
    """Prose naming a store nobody reads is the failure this check exists to catch.

    The name is taken from the sentence itself and looked up in the real
    `onetaskgraph.yaml`: a document left naming a source that was renamed, deleted, or
    dropped from `default_sources` describes a store no command reaches, and reads
    exactly like current guidance.
    """
    planned = _named_in_prose(name)
    sources = read_plan_sources(_configuration())
    defaults = read_default_sources(_configuration())
    assert planned in sources, (
        f"{name} tells a reader a plan of this repository is stored under `{planned}`, "
        f"which onetaskgraph.yaml does not configure at all (it configures {sorted(sources)})"
    )
    assert planned in defaults, (
        f"{name} names the `{planned}` source, which onetaskgraph.yaml configures but does "
        f"not default to (it defaults to {defaults}); a plan stored there goes unlisted by "
        "every read that names no source"
    )
    assert planned == BOARD, (
        f"{name} sends a reader to `{planned}` where this repository plans on the "
        f"`{BOARD}` board; see {MANAGER}, '{STORE_SECTION}'"
    )


def test_both_documents_name_the_same_plan_source() -> None:
    """One store, so the two documents cannot send a reader to different ones."""
    assert _named_in_prose(MANAGER) == _named_in_prose(ORCHESTRATION), (
        f"{MANAGER} and {ORCHESTRATION} name different plan sources, so which one holds "
        "this repository's plans depends on which document the reader opened"
    )


#: The crate whose source the live-lane landings under "in force by merging" change,
#: and the one this CLI must not be shipping if that classification is to hold. Named
#: rather than derived: what is being gated is a claim about *this* crate, and a check
#: that asked "does the binary carry every crate it declares" would answer a different
#: question and pass while the classification silently stopped being true.
LIVE_ONLY_CRATE = "onetaskgraph-live"
#: A crate the archive does carry, so the absence above is read as an absence rather
#: than as this measurement having stopped working. Without it a stripped binary, a
#: changed build profile, or a `strings` that found nothing would all pass.
SHIPPED_CRATE = "onetaskgraph-core"
#: How a Rust binary spells the crate a compiled-in source file came from. Panic
#: locations survive stripping, which is what makes this readable at all on the
#: published artifact.
CRATE_SOURCE_PATH = "crates/{crate}/src"


def test_the_installed_plan_store_cli_ships_none_of_the_live_crate() -> None:
    """The artifact half of "in force by merging", measured on the installed binary.

    Three landings this document classifies that way — the gate-selection repair, the
    startup-sweep repair, and the root-causes plan's own allowance re-check — are
    ancestors of the adopted release, so `git tag --contains` answers that the release
    carries them. The archive does not, and every check in this repository that reads a
    pin passes either way, which is exactly the reading that sent one dispatch looking
    for a bump nobody made.

    So the classification is held to the artifact rather than to the ancestry. The only
    compiled source any of them touches is `onetaskgraph-live`, which is a
    dev-dependency of two crates and of nothing else, so the published CLI carries no
    part of it — and the day that stops being true is the day those landings really are
    adopted through this pin and the paragraph saying otherwise is wrong.

    Both directions are asserted. A crate the binary *does* carry has to be found, or an
    absence proves nothing: a stripped binary that embedded no paths at all would satisfy
    a one-sided check while saying nothing about either landing.
    """
    assert ONETASKGRAPH_BIN.is_file(), (
        f"this checkout's own onetaskgraph is missing at {ONETASKGRAPH_BIN} — run 'just bootstrap'"
    )
    compiled = ONETASKGRAPH_BIN.read_bytes()
    shipped = CRATE_SOURCE_PATH.format(crate=SHIPPED_CRATE).encode()
    live = CRATE_SOURCE_PATH.format(crate=LIVE_ONLY_CRATE).encode()

    assert shipped in compiled, (
        f"the installed plan-store CLI embeds no source path for {SHIPPED_CRATE}, so "
        "this measurement can no longer tell a crate the archive omits from one it "
        f"carries. Re-read how {ONETASKGRAPH_BIN} is built before trusting the "
        "absence below"
    )
    assert live not in compiled, (
        f"the installed plan-store CLI now carries {LIVE_ONLY_CRATE}, the crate holding "
        "the only compiled source that the gate-selection, startup-sweep and "
        f"allowance re-check landings touch. {MANAGER} classifies all three as in force "
        "by merging with no pin to move, and that is now wrong: re-read the paragraph "
        "naming pull/433, pull/538 and pull/821 against what this release actually ships"
    )


#: The plan-store verb that sets up a board's fields — its missing `Status` options and the
#: `Priority` field a source's `priority_mapping` names — and the flag that turns its
#: read-only plan into the write; it supersedes `sources status-options` from 0.2.45. Named
#: as literals because they are the contract the prose and the binary are both held to:
#: the verb preserves every existing option id where a hand-written `updateProjectV2Field`
#: mutation re-mints them all, which is what once cleared every item's status on the
#: `followups` board.
#: onetaskgraph https://github.com/nickderobertis/onetaskgraph/issues/1312.
FIELDS_VERB = ("sources", "fields")
APPLY_FLAG = "--apply"
#: How the manager's document is told to run it: through this repository's plan-store
#: recipe, which is what establishes the board credential, and with `--apply` only after
#: the plan a bare invocation prints has been read.
FIELDS_RECIPE = "just plans " + " ".join(FIELDS_VERB) + " <source>"


def _paragraphs(name: str) -> list[str]:
    return [_flat(paragraph) for paragraph in _document(name).split("\n\n")]


def test_the_manager_states_once_that_a_board_field_is_set_up_through_the_verb() -> None:
    """One paragraph names the verb, its `--apply` flag, and the mutation it replaces.

    Stated once because a second statement is where the two drift apart: the operator
    reads whichever one they found, and a copy that still says to mutate by hand is the
    one that wipes a board.
    """
    naming = [paragraph for paragraph in _paragraphs(MANAGER) if " ".join(FIELDS_VERB) in paragraph]
    assert len(naming) == 1, (
        f"{MANAGER} has to state how a board field is set up exactly once, in the "
        f"paragraph describing the boards; found {len(naming)} paragraphs naming "
        f"`{' '.join(FIELDS_VERB)}`"
    )
    (rule,) = naming
    assert f"`{FIELDS_RECIPE}`" in rule, (
        f"{MANAGER} has to send an operator through the plan-store recipe, as "
        f"`{FIELDS_RECIPE}`, so the board credential is established the way every "
        "other board read establishes it"
    )
    assert f"`{FIELDS_RECIPE} {APPLY_FLAG}`" in rule, (
        f"{MANAGER} has to name the `{APPLY_FLAG}` form as the one that writes, after the "
        "bare invocation's plan has been read"
    )
    assert "updateProjectV2Field" in rule and "never" in rule, (
        f"{MANAGER} has to say the hand-written `updateProjectV2Field` mutation is never "
        "the way, because that mutation is what re-mints every option id"
    )


def test_the_installed_plan_store_cli_carries_the_fields_verb_and_its_apply_flag() -> None:
    """The verb the rule names, and its `--apply` flag, run on this checkout's own binary.

    Read off `--help` rather than off a live board: the rule says nothing a board has to
    be touched to check, and a `--help` that refuses is exactly what a release
    before the verb answers (`onetaskgraph sources` had no such subcommand, exit 2).
    """
    assert ONETASKGRAPH_BIN.is_file(), (
        f"this checkout's own onetaskgraph is missing at {ONETASKGRAPH_BIN} — run 'just bootstrap'"
    )
    shown = subprocess.run(
        [str(ONETASKGRAPH_BIN), *FIELDS_VERB, "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert shown.returncode == 0, (
        f"the installed plan-store CLI has no `{' '.join(FIELDS_VERB)}` verb "
        f"(exit {shown.returncode}): {shown.stderr}\n{MANAGER} tells an operator to set up a "
        "board field with it, so the pin has to be on a release that carries it"
    )
    options = [
        line.strip().split()[0] for line in shown.stdout.splitlines() if line.startswith("      --")
    ]
    assert APPLY_FLAG in options, (
        f"`{' '.join(FIELDS_VERB)} --help` lists {options} and no `{APPLY_FLAG}`, "
        f"the flag {MANAGER} names as the one that writes"
    )


#: The roster entry that states which `onetaskgraph` a plan-store read of the package
#: runs: the one the lock installed beside the interpreter, at this path relative to the
#: checkout, and never one from the search path or from the SDK's own environment name.
#: `orchestrator/plan_store.py` is the one authoritative statement; the sentence in the
#: manager's document is a copy, and this is the check that goes red when the two part.
ROSTER_ENTRY = "**`onetaskgraph`**"
LOCKED_INSTALL = ".venv/bin/onetaskgraph"
RESOLUTION_MODULE = "orchestrator/plan_store.py"


def _roster_entry(name: str) -> str:
    """The plan store's one bullet of the tool roster, flattened to a line.

    The roster is one list with no blank line between its bullets, so a paragraph split
    would hand back every tool's entry at once and a claim made of a sibling would pass
    for this one: the bullet is cut from its own opening to the next bullet's.
    """
    entries = re.findall(
        rf"^- {re.escape(ROSTER_ENTRY)}.*?(?=^- \*\*`|\Z)",
        _document(name),
        re.MULTILINE | re.DOTALL,
    )
    assert len(entries) == 1, (
        f"{name} has to carry exactly one roster bullet opening `- {ROSTER_ENTRY}`; found "
        f"{len(entries)}"
    )
    return _flat(entries[0])


def test_the_packages_plan_store_reads_run_the_locked_install_the_roster_names() -> None:
    """The resolution in the code and the sentence in the prose name one binary.

    Held both ways under the suite's own interpreter: the package's resolution answers
    the path the suite says provisioning installs the CLI at, and the manager's roster
    entry states that rule — the locked install beside the interpreter, never a
    `onetaskgraph` from `PATH` or from `ONETASKGRAPH_SDK_BINARY` — pointing at the
    module that says why. A resolution that moved, or a sentence that stopped being
    true, fails here rather than diverging silently.
    """
    resolved = plan_store.locked_binary()
    assert resolved == ONETASKGRAPH_BIN, (
        f"{RESOLUTION_MODULE} resolves the plan-store CLI to {resolved}, where the suite "
        f"says provisioning installs it at {ONETASKGRAPH_BIN}"
    )
    assert Path(plan_store.client().binary).samefile(ONETASKGRAPH_BIN), (
        "the SDK client the package builds runs a binary other than the locked install"
    )

    entry = _roster_entry(MANAGER)
    assert f"`{LOCKED_INSTALL}`" in entry, (
        f"{MANAGER}'s `onetaskgraph` roster entry has to name `{LOCKED_INSTALL}` as the "
        "install every plan-store read runs"
    )
    assert "plan-store read" in entry and "`orchestrator/`" in entry, (
        f"{MANAGER}'s `onetaskgraph` roster entry has to say the rule is about every "
        "plan-store read in `orchestrator/`"
    )
    assert "never" in entry and "`PATH`" in entry, (
        f"{MANAGER}'s `onetaskgraph` roster entry has to say a `onetaskgraph` from `PATH` "
        "is never the one a read runs"
    )
    assert f"`{plan_store.SDK_BINARY_VARIABLE}`" in entry, (
        f"{MANAGER}'s `onetaskgraph` roster entry has to name `{plan_store.SDK_BINARY_VARIABLE}` "
        "as a name a read never resolves the CLI from"
    )
    assert f"`{RESOLUTION_MODULE}`" in entry, (
        f"{MANAGER}'s `onetaskgraph` roster entry has to point at `{RESOLUTION_MODULE}` for "
        "the reason, which is stated there once"
    )
    assert re.search(r"\b\d{4}-\d{2}-\d{2}\b", entry) is None, (
        "the roster entry carries a bare date, which reads as a measurement to re-take"
    )


#: The documents that tell a reader where a plan's petsinc tasks land and what each Linear
#: state means for one: the manager's, the operator's, and the planner's own prompt.
PLANNER = "personas/planner.yaml"
ROUTED_DOCUMENTS = (MANAGER, ORCHESTRATION, PLANNER)
#: The Linear source a `plans` route sends a petsinc task to.
LINEAR = "hellopatient"


def _linear_states() -> dict[str, str]:
    """The workflow state each category is written as, as the store resolves the mapping."""
    prefix = f"sources.{LINEAR}.config.status_mapping."
    states = {
        key[len(prefix) :]: str(value)
        for key, value in plan_store.configured_settings().items()
        if key.startswith(prefix)
    }
    assert states, f"onetaskgraph.yaml maps no workflow state for `{LINEAR}`"
    return states


def _route_patterns() -> list[str]:
    """Each repository pattern a `plans` route sends to the Linear source."""
    routes = plan_store.configured_settings().get(f"sources.{BOARD}.routes")
    assert isinstance(routes, list), routes
    patterns = [
        str(pattern)
        for route in routes
        if isinstance(route, dict) and route.get("to") == LINEAR
        for pattern in route.get("repositories", [])
    ]
    assert patterns, f"`{BOARD}` routes nothing to `{LINEAR}`"
    return patterns


@pytest.mark.parametrize("name", ROUTED_DOCUMENTS)
def test_each_document_names_every_linear_state_and_the_route_the_store_applies(
    name: str,
) -> None:
    """The prose's state names and route are the configuration's, not remembered ones.

    A state renamed in the mapping, or a route moved to another organization, leaves a
    document telling a manager to look for a state or a repository nothing writes.
    """
    text = _flat(_document(name))
    missing = [
        state
        for state in _linear_states().values()
        if re.search(rf"\b{re.escape(state)}\b", text) is None
    ]
    assert not missing, f"{name} never names the Linear state(s) {missing} a plan task reads"
    for pattern in _route_patterns():
        assert pattern in text or pattern.removesuffix("*") in text, (
            f"{name} never names {pattern}, the repositories `{BOARD}` routes to `{LINEAR}`"
        )
    assert f"`{LINEAR}`" in text, f"{name} never names the `{LINEAR}` source"


def test_the_orchestration_table_gives_each_state_the_category_the_source_writes_it_for() -> None:
    """Every row of the state table is one entry of the mapping, and every entry has a row."""
    rows = dict(
        re.findall(r"^\| ([A-Z][A-Za-z ]+?) \| `([a-z-]+)` \|", _document(ORCHESTRATION), re.M)
    )
    expected = {state: category for category, state in _linear_states().items()}
    assert rows == expected, (
        f"{ORCHESTRATION}'s Linear state table reads {rows}, and onetaskgraph.yaml maps {expected}"
    )
