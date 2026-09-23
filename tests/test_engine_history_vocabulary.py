"""The run-history vocabulary this repository's prose uses, against the engines' own.

Every name this host writes down about a dispatch's oneharness history is somebody
else's: the six `onepipeline.*` labels and the three scope words are
`onepipeline`'s, the pointer file's name and the three environment variables it
sets are `onepipeline`'s too, and the setting that turns the pointer line on has
three spellings — a config key, a CLI flag and an environment variable — that are
`oneharness`'s. This repository restates all of them: in
`orchestrator/labels.py`, in every `oneharness.*.toml`'s `history_labels` comment,
and in `docs/orchestration.md` and
`docs/telemetry.md`, which tell a manager how to find the sessions one run opened.

**A restated name that stops being the producer's is worse here than a missing
one.** A reader who filters `oneharness history watch --label onepipeline.run_id=…`
on a key the engine has renamed gets no error and no sessions, which reads exactly
like a run that launched no agents; the same is true of a pointer file nobody
writes and a `--label` prefix nothing stamps. So the names are reconciled against
what each producer declares, at the release this host adopted, rather than
remembered.

`tests/test_engine_contracts.py` beside this module does the same for the outcome
vocabulary and the engine constants the lifecycle prose quotes, and this module
borrows its two engines and its source reader rather than opening a second
definition of which ref each is read at — pairing a checkout with the wrong ref is
the failure that module's docstring is about, and it is not worth having twice.

**What is read, and from where.** `onepipeline`'s `docs/contract.md` at
`v<config/onepipeline.version>` carries a machine-readable block of exactly these
names, which its own `tests/contract.rs` reconciles against the constants in
`onepipeline::agents`; that block is what is read here, so what fails is the
producer's declaration moving and not this repository's reading of prose.
`oneharness` has no such block for the pointer setting, so its three spellings are
looked for in the two files that own them — the config type and the README — at the
tag `config/oneharness.version` names.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] The
subject is two registered checkouts of other repositories, which live outside this
workspace and so outside every `nx.json` input key — hence `reads_checkouts`, the
uncached tier, where a memo cannot replay a green across the adoption this exists to
catch. `tests/test_engine_contracts.py` carries the same directive for the same reason.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping
from typing import NamedTuple

import pytest
from test_engine_contracts import ONEHARNESS, ONEPIPELINE_DOCS, _source

from orchestrator import labels
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_checkouts

#: The engine document the block lives in, relative to that engine's source root.
CONTRACT = "contract.md"
#: The key the block sits under, which is also what makes it findable without a line
#: number: the document is prose with fenced JSON in it, and this is the one object in
#: it that declares this vocabulary.
BLOCK_KEY = "oneharness_history"

#: Where `oneharness` declares the pointer setting, relative to *its* source root
#: (`crates/oneharness-core/src`), and what the setting is called there.
POINTER_CONFIG = "domain/config.rs"
POINTER_CONFIG_KEY = "history_pointer_file"
#: The flag spelling, which lives in the CLI's own documentation rather than in the
#: core, so it is read from the repository root instead of the source root.
POINTER_FLAG = "--history-pointer-file"


class Declared(NamedTuple):
    """The engine's own statement of this vocabulary, narrowed to what is read here.

    A type rather than the parsed mapping, because the mapping's values are `object`
    and every comparison against one needs a cast. Narrowing once, at the boundary the
    document arrives at, is what lets each assertion below be a plain comparison
    between two strings — and it puts the "the block no longer has this shape" failure
    in one place, where it can say what to do about it, instead of at seven type
    suppressions that could each only say a key was missing.
    """

    #: Every key the engine stamps, in the order the block states them.
    labels: tuple[str, ...]
    #: The prefix the engine reserves and strips from what it inherited.
    label_prefix: str
    #: Every value the scope label takes.
    scopes: tuple[str, ...]
    #: The pointer file's own name, under the run's directory.
    pointer_file: str
    #: The three variables the engine sets, by the block's own names for them.
    history_env: str
    pointer_file_env: str
    labels_env: str
    #: The variable the engine declares it never sets.
    never_set_env: str


def _text(block: Mapping[str, object], key: str) -> str:
    """One string field of the block, or a failure naming the field and what came back."""
    value = block.get(key)
    assert isinstance(value, str), (
        f"{ONEPIPELINE_DOCS.crate}'s `{BLOCK_KEY}` block at {ONEPIPELINE_DOCS.ref} gives "
        f"{key!r} as {value!r}, which is not the string this module reads it as. The "
        "producer has changed the block's shape; re-read it before trusting any name "
        "below"
    )
    return value


def _values(block: Mapping[str, object], key: str) -> tuple[str, ...]:
    """One object field's values, in the block's own order, held to being strings."""
    value = block.get(key)
    assert isinstance(value, dict), (
        f"{ONEPIPELINE_DOCS.crate}'s `{BLOCK_KEY}` block at {ONEPIPELINE_DOCS.ref} gives "
        f"{key!r} as {value!r}, which is not the object this module reads its members "
        "out of. The producer has changed the block's shape"
    )
    return tuple(_text(value, member) for member in value)


def _declared() -> Declared:
    """The engine's own `oneharness_history` block, at the release this host runs.

    Found by scanning the document's fenced JSON rather than by line number, and the
    match is required to be unique: a second block declaring the same key would mean
    this module had picked one of two answers without saying which.
    """
    document = _source(ONEPIPELINE_DOCS, CONTRACT)
    fenced = [
        json.loads(fence) for fence in re.findall(r"```json\n(.*?)\n```", document, re.DOTALL)
    ]
    found = [
        block
        for parsed in fenced
        if isinstance(parsed, dict) and isinstance(block := parsed.get(BLOCK_KEY), dict)
    ]
    assert len(found) == 1, (
        f"{ONEPIPELINE_DOCS.crate}'s {CONTRACT} at {ONEPIPELINE_DOCS.ref} declares "
        f"{len(found)} `{BLOCK_KEY}` blocks, not one. This module reads that block as "
        "the producer's own statement of the label and pointer vocabulary; a release "
        "that moved or renamed it has moved the source, and every site listed in this "
        "module's docstring has to be re-read against wherever it went"
    )
    block = found[0]
    environment = block.get("environment")
    assert isinstance(environment, dict), (
        f"{ONEPIPELINE_DOCS.crate}'s `{BLOCK_KEY}` block at {ONEPIPELINE_DOCS.ref} gives "
        f"`environment` as {environment!r}; the three variables the engine sets are read "
        "out of it, so a shape change there is a shape change to all three"
    )
    return Declared(
        labels=_values(block, "labels"),
        label_prefix=_text(block, "label_prefix"),
        scopes=_values(block, "scopes"),
        pointer_file=_text(block, "pointer_file"),
        history_env=_text(environment, "history"),
        pointer_file_env=_text(environment, "pointer_file"),
        labels_env=_text(environment, "labels"),
        never_set_env=_text(block, "never_set"),
    )


def test_the_label_keys_this_host_names_are_the_ones_the_engine_stamps() -> None:
    """`orchestrator/labels.py`'s six keys, against the engine's own six.

    The whole set and in the engine's own order, rather than a containment: a key the
    engine added that this host does not name is one no reader here filters on, and a
    key this host names that the engine no longer stamps is a filter that silently
    matches nothing.
    """
    declared = _declared()

    assert declared.labels == labels.ENGINE_LABELS, (
        f"the engine stamps {declared.labels} and `orchestrator/labels.py` names "
        f"{labels.ENGINE_LABELS}. Those keys are what a reader selects a run's sessions "
        "by, so move the declaration and every comment the module's docstring lists in "
        "the same change"
    )
    assert declared.label_prefix == labels.ENGINE_LABEL_PREFIX, (
        f"the engine reserves the prefix {declared.label_prefix!r} and this host "
        f"names {labels.ENGINE_LABEL_PREFIX!r}. That prefix is the boundary between the "
        "labels a dispatch inherits and the ones this repository adds — get it wrong and "
        "this host's own `role` is either stripped by the engine or stops being stripped "
        "when it should be"
    )


def test_the_scope_words_this_host_names_are_the_ones_the_engine_stamps() -> None:
    """The three values `onepipeline.scope` takes, whole.

    Held as the set because each one is a filter somebody writes: the observer's
    sessions are found by `observer` and the drafter's by `pr-author`, and a word that
    moved leaves that reader with an empty answer rather than an error.
    """
    declared = _declared()

    assert declared.scopes == labels.SCOPES, (
        f"the engine stamps the scopes {declared.scopes} and this host names "
        f"{labels.SCOPES}. A scope word this build has never seen is one every reader "
        "filtering by it stops matching"
    )


def test_the_pointer_file_and_its_variables_are_the_ones_the_engine_sets() -> None:
    """The file a run's sessions are found through, and the three variables around it.

    `NEVER_SET_ENV` is held with them and is the one worth stating out loud: the engine
    setting `ONEHARNESS_HISTORY_DIR` is what would move a dispatch's transcripts out of
    the store this host reads with `oneharness history`, and every paragraph here that
    says they stay put is a claim about the engine *not* setting it.
    """
    declared = _declared()

    assert declared.pointer_file == labels.POINTER_FILE_NAME, (
        f"the engine writes its pointer file as {declared.pointer_file!r} and this "
        f"host names {labels.POINTER_FILE_NAME!r}. That file is how a run's sessions are "
        "found and there is no second way, so a rename leaves every agents read here "
        "pointed at nothing"
    )
    assert declared.history_env == labels.HISTORY_ENV, declared
    assert declared.pointer_file_env == labels.POINTER_FILE_ENV, declared
    assert declared.labels_env == labels.LABEL_ENV, declared
    assert declared.never_set_env == labels.NEVER_SET_ENV, (
        f"the engine names {declared.never_set_env!r} as the variable it never sets and "
        f"this host names {labels.NEVER_SET_ENV!r}. This repository's docs say a "
        "dispatch's transcripts stay in the host's default store *because* of that, so "
        "the two have to be the same name"
    )


def test_the_pointer_setting_is_still_spelled_three_ways_by_oneharness() -> None:
    """The setting that turns the pointer line on, at the CLI release this host pins.

    Three spellings because a host reaches it three ways, and this repository's prose
    tells a reader about the environment one while the engine sets exactly that. Read
    from `oneharness`'s own files rather than from the engine's block: the engine
    declares the variable it *sets*, and whether `oneharness` still honours it — under
    that name, and under the config key and flag beside it — is the producer's to say.
    """
    config = _source(ONEHARNESS, POINTER_CONFIG)
    readme = _source(ONEHARNESS._replace(source_root="."), "README.md")

    assert POINTER_CONFIG_KEY in config, (
        f"{ONEHARNESS.crate} {ONEHARNESS.ref} no longer declares `{POINTER_CONFIG_KEY}` "
        f"in {POINTER_CONFIG}, so the setting the engine writes through has moved. The "
        "pointer line is what makes a run's sessions findable; re-read what replaced it"
    )
    assert labels.POINTER_FILE_ENV in config, (
        f"{ONEHARNESS.crate} {ONEHARNESS.ref} no longer reads "
        f"`{labels.POINTER_FILE_ENV}` in {POINTER_CONFIG}, and that variable is the only "
        "one the engine sets — a dispatch would write no pointer line at all, silently"
    )
    assert POINTER_FLAG in readme, (
        f"{ONEHARNESS.crate} {ONEHARNESS.ref}'s README no longer documents "
        f"`{POINTER_FLAG}`, the flag half of the setting; this host's prose offers it as "
        "the by-hand way to write a pointer line and would be offering a flag that is gone"
    )


class RestatingSite(NamedTuple):
    """A tracked file that restates an engine name in prose, and the name it has to state."""

    relative_path: str
    name: str


#: Every tracked file that restates one of these names in prose, and what it has to
#: name. `orchestrator/labels.py` is deliberately absent: it *declares* these values
#: rather than quoting them, and the tests above hold its declaration to the producer's
#: directly — looking for the literal in its source would fail on the composition
#: (`RUN_LABEL` is the prefix plus a suffix) while proving nothing the first test does
#: not. Keeping this list here rather than in prose is what makes a new site an edit to
#: this tuple rather than a paragraph nothing reads.
RESTATING_SITES = (
    RestatingSite("docs/orchestration.md", labels.POINTER_FILE_NAME),
    RestatingSite("docs/orchestration.md", labels.RUN_LABEL),
    RestatingSite("docs/telemetry.md", labels.POINTER_FILE_NAME),
    RestatingSite("docs/telemetry.md", labels.NEVER_SET_ENV),
    # Every harness config's `history_labels` comment says what its own keys add beside
    # the engine's, which is a restatement of the engine's prefix in each of them.
    RestatingSite("oneharness.toml", labels.ENGINE_LABEL_PREFIX),
    RestatingSite("oneharness.judge.toml", labels.ENGINE_LABEL_PREFIX),
    RestatingSite("oneharness.check-in.toml", labels.ENGINE_LABEL_PREFIX),
    RestatingSite("oneharness.design-doc.toml", labels.ENGINE_LABEL_PREFIX),
    RestatingSite("oneharness.design-doc-judge.toml", labels.ENGINE_LABEL_PREFIX),
    RestatingSite("oneharness.follow-up.toml", labels.ENGINE_LABEL_PREFIX),
    RestatingSite("oneharness.llmlint.toml", labels.ENGINE_LABEL_PREFIX),
    RestatingSite("oneharness.orchestrator.toml", labels.ENGINE_LABEL_PREFIX),
    RestatingSite("oneharness.plan-review.toml", labels.ENGINE_LABEL_PREFIX),
    RestatingSite("oneharness.pr-author.toml", labels.ENGINE_LABEL_PREFIX),
)


def test_every_harness_config_is_a_restating_site() -> None:
    """The site list covers every ROLE config, not the ones somebody named.

    Each role config says in a comment what its own `history_labels` add beside the
    engine's, so each is a restatement, and a config added later without a row would be
    one the reconciliation above silently stops covering — which is how the list came to
    omit eight of ten the first time it was written.

    The subject set is the configs that declare `history_labels`, stated here rather
    than left to the glob. `oneharness*.toml` also matches the two SHARED parents —
    `oneharness.identities.toml` and `oneharness.dispatch.toml` — which state the six
    identities every role extends and declare no labels at all: a label names which
    SIDE of a conversation a session is, which is a fact about a role and not about an
    identity. They restate nothing because they say nothing about labels, which is the
    answer this test's own message asks a config to give.
    """
    configs = {
        path.name
        for path in REPO_ROOT.glob("oneharness*.toml")
        if "history_labels" in tomllib.loads(path.read_text(encoding="utf-8"))
    }
    listed = {path for path, _ in RESTATING_SITES if path.startswith("oneharness")}

    assert configs == listed, (
        f"the `oneharness*.toml` configs declaring `history_labels` are {sorted(configs)} "
        f"and RESTATING_SITES lists {sorted(listed)}. Add a row for each config that "
        "restates the engine's label keys, or say in the config why it restates nothing"
    )


# `reads_docs` is deliberately NOT added beside the module's `reads_checkouts`, even
# though this one reads only prose: `tests/conftest.py` says a test reconciling this
# repository's prose against an engine's own source needs exactly one of the two, and
# that the two together would put it back in a tier that memoizes a verdict depending on
# another repository's checkout. The tier partition holds the same rule from the other
# side, so carrying both is a hard failure rather than a preference.
@pytest.mark.parametrize(("relative_path", "name"), RESTATING_SITES, ids=lambda value: str(value))
def test_each_site_that_restates_one_of_these_names_still_carries_it(
    relative_path: str, name: str
) -> None:
    """The reconciliation above is only worth having if the sites still say it.

    Two failures this catches, and they arrive from opposite directions: a paragraph
    that dropped the name while the engine kept it — leaving a manager told how to find
    a run's sessions without the one string that finds them — and a paragraph reworded
    to a name the tests above have not been pointed at, which is how the reconciliation
    quietly stops covering the site it was written for.
    """
    written = (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    assert name in written, (
        f"{relative_path} no longer names {name!r}, which the adopted engine still "
        "stamps or sets. Either the site stopped telling a reader how a run's sessions "
        "are found, or it was reworded to something this module is not holding"
    )
