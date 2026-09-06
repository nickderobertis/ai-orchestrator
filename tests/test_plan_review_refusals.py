"""The refusal corpus this repository's plan-review detectors were widened against.

`docs/plan-review-refusals.json` holds every plan-review refusal this host had recorded
at its declared cutoff, each classified as one of three: already refused by
`orchestrator/criteria_guard.py` on the commit that widening started from, newly refused
by the widened detectors, or deliberately left to the judge with the false refusal a
detector for its shape would risk.

What this module holds is the part of that document a reader would otherwise have to
take on trust. Two of the three classifications are *claims about the detectors* rather
than notes beside a refusal, so they are re-decided here against the detectors as they
now stand — a widening that stopped catching its own evidence, or a corpus entry
promoted to `newly-caught` by editing the file, fails here. The third is held to
carrying the reason it was left, because "left to the judge" with nothing said about why
is indistinguishable from an entry nobody classified.

`docs/plan-review-refusals.md` is the prose beside it, and says where the corpus came
from and what the reviewer's own verdict contract now carries.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from criteria_examples import PUBLISHED_WITH_A_WORD_IN_THE_WAY, RED_BEFORE_GREEN

from orchestrator.criteria_guard import Bar, CriteriaError, check
from orchestrator.root import REPO_ROOT

# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] This marker deselects
# nothing and hides no cost: `orchestrator:test-docs` runs `-m reads_docs`, `orchestrator:test`
# runs the complement, both are targets of this same project, and `just check` runs both — so
# the marker chooses which Nx input key memoizes the verdict, `wholeWorkspace` rather than
# `codeWorkspace`, which is the opposite of skipping a tier. It is also not optional:
# `tests/conftest.py` fails an unmarked test that opens a file outside the code key and tells
# it to carry this marker. And the rule's own relevance clause is unmet — this module reads
# two files of this repository, reaching no external service and taking milliseconds.
pytestmark = pytest.mark.reads_docs
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]

CORPUS = REPO_ROOT / "docs" / "plan-review-refusals.json"

#: The prose beside it, which quotes the corpus's own numbers so a reader meets them in
#: the argument rather than in a data file. Every one of them is reconciled below, in the
#: three shapes it writes them: a labelled measure row, a classification row, and a count
#: in the heading of a shape's bullet.
PROSE = REPO_ROOT / "docs" / "plan-review-refusals.md"

MEASURE_ROW = re.compile(r"^ *\| (?P<measure>[a-z_]+) \| (?P<count>\d+) \|$", re.MULTILINE)
CLASSIFICATION_ROW = re.compile(r"^\| `(?P<name>[a-z-]+)` \|.*\| (?P<count>\d+) \|$", re.MULTILINE)
SHAPE_COUNT = re.compile(r"`(?P<shape>[a-z-]+)`[^(\n]*\((?P<count>\d+) (?P<of>left|newly caught)\)")

#: A bar demanding nothing, so a refusal below is attributable to a criterion rather
#: than to a demand some role makes of the node the criterion came from.
NOTHING_DEMANDED = Bar("a bar that demands nothing", "Accept it when it is done.")


def _corpus() -> dict[str, Any]:
    """The corpus, decoded. `Any` because its header, its shape map and its refusal list
    are three different shapes read at three depths; naming a type for them here would
    restate the document this module exists to read rather than checking it."""
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def _refused(criterion: str) -> str | None:
    """What the detectors say about a task whose criteria are ``criterion``, or ``None``."""
    task = (
        "## What\n\nDo the thing.\n\n## Why\n\nThe user asked for it.\n\n"
        f"## Acceptance criteria\n\n- {criterion}\n\n## Additional info\n\nWork it.\n"
    )
    try:
        check(task, "probe", NOTHING_DEMANDED)
    except CriteriaError as refusal:
        return str(refusal)
    return None


def test_every_refusal_in_the_corpus_carries_a_declared_classification_and_shape() -> None:
    """Whole rather than sampled, which is the only reading of it worth having.

    A classification covering a selection would report the shapes somebody chose to
    look at. So every entry is required to carry both fields, from the vocabularies the
    document declares itself, and the counts it publishes are required to be counts of
    what it actually holds rather than a summary that drifted from it.
    """
    corpus = _corpus()
    classifications, shapes = set(corpus["classifications"]), set(corpus["shapes"])
    counted: dict[str, int] = {}

    for entry in corpus["refusals"]:
        assert entry["classification"] in classifications, entry
        assert entry["shape"] in shapes, entry
        assert entry["reason"].strip(), entry
        counted[entry["classification"]] = counted.get(entry["classification"], 0) + 1

    assert corpus["counts"]["refusals"] == len(corpus["refusals"]), corpus["counts"]
    assert corpus["counts"]["by_classification"] == counted, corpus["counts"]
    assert corpus["captured_at"] and corpus["cutoff"] and corpus["source"], corpus


def test_every_newly_caught_refusal_is_one_the_detectors_now_refuse() -> None:
    """The claim `newly-caught` makes is about the detectors, so the detectors decide it.

    Each such entry stores the fragment the refusal quoted; a task whose criteria carry
    that fragment has to be refused, and refused *for it*. A widening narrowed later
    without the corpus moving with it fails here rather than leaving the document
    claiming a shape nothing catches.
    """
    newly = [e for e in _corpus()["refusals"] if e["classification"] == "newly-caught"]

    assert newly, "the corpus records no widening at all"
    for entry in newly:
        fragment = entry["criterion_fragment"]
        refusal = _refused(f"The finished tree is such that {fragment}.")
        assert refusal is not None, entry
        assert fragment in refusal, (fragment, refusal)


def test_every_refusal_left_to_the_judge_names_the_false_refusal_catching_it_would_risk() -> None:
    """An entry left uncaught for no stated reason is one nobody classified.

    This check refuses a plan outright, so the reason a shape is left alone is the whole
    of the argument for leaving it: a false refusal blocks correct work and gets worked
    around, which is worse than the gap. Requiring the risk to be *named* is what keeps
    "left to the judge" from meaning "not looked at".
    """
    corpus = _corpus()
    left = {e["shape"] for e in corpus["refusals"] if e["classification"] == "left-to-the-judge"}

    assert left, corpus["counts"]
    for shape in left:
        described = corpus["shapes"][shape]
        assert described["what"].strip(), shape
        assert len(described["false_refusal_risk"].split()) >= 20, shape


def test_every_count_the_prose_quotes_is_the_corpus_it_describes() -> None:
    """The document argues in numbers, so the numbers are read off the corpus, not typed.

    Three shapes, each reconciled against the header the corpus publishes: the selection
    table's labelled measures, the classification table's rows, and the count in each
    shape bullet's heading. Nothing here judges the prose — what it stops is the file
    going on quoting totals a later capture moved, which is how a document that reads
    like evidence stops being any.
    """
    counts = _corpus()["counts"]
    prose = PROSE.read_text(encoding="utf-8")

    measures = {row["measure"]: int(row["count"]) for row in MEASURE_ROW.finditer(prose)}
    assert measures, prose
    for measure, quoted in measures.items():
        assert counts[measure] == quoted, measure

    classified = {row["name"]: int(row["count"]) for row in CLASSIFICATION_ROW.finditer(prose)}
    assert classified == counts["by_classification"], classified

    shapes = {
        (found["shape"], found["of"]): int(found["count"]) for found in SHAPE_COUNT.finditer(prose)
    }
    assert shapes, prose
    for (shape, of), quoted in shapes.items():
        recorded = counts["by_shape_and_classification"][shape]
        assert recorded["left-to-the-judge" if of == "left" else "newly-caught"] == quoted, shape


def test_the_corpus_header_counts_what_the_corpus_holds() -> None:
    """Including per shape, which is the summary the classification test does not reach."""
    corpus = _corpus()
    by_shape: dict[str, int] = {}
    pairs: dict[str, dict[str, int]] = {}

    for entry in corpus["refusals"]:
        by_shape[entry["shape"]] = by_shape.get(entry["shape"], 0) + 1
        within = pairs.setdefault(entry["shape"], {})
        within[entry["classification"]] = within.get(entry["classification"], 0) + 1

    assert corpus["counts"]["by_shape"] == by_shape, corpus["counts"]["by_shape"]
    assert corpus["counts"]["by_shape_and_classification"] == pairs, pairs
    assert corpus["counts"]["distinct_tasks"] == len(
        {(entry["plan"], entry["task"]) for entry in corpus["refusals"]}
    )
    assert corpus["counts"]["plans"] == len({entry["plan"] for entry in corpus["refusals"]})


#: The fragment a refusal quotes, which is the same thing the corpus stores per entry —
#: so the two are comparable without either side restating the other's text.
QUOTED_FRAGMENT = re.compile(r"'([^']+)'")


def test_the_fixtures_the_widenings_are_driven_over_are_the_corpus_evidence_they_claim() -> None:
    """The drift gate over `tests/criteria_examples.py`'s two corpus-derived tuples.

    Those tuples are described as the corpus's own evidence, and a fixture that claims
    to be evidence has to be evidence: the reconciliation is on the **fragment each is
    refused on**, which is exactly what the corpus records per entry, rather than on the
    criterion's whole text — the corpus never stored that, so a containment check
    against it would be comparing a fixture with itself.

    It holds in both directions on purpose. A `newly-caught` fragment no fixture is
    refused on is a widening whose own evidence nothing drives; a fixture refused on a
    fragment the corpus does not record is a criterion somebody wrote rather than one a
    judged turn was paid for, and it should be sitting in `PUBLICATION_COPULAS` — which
    is coverage, is documented as coverage, and is deliberately outside this gate.
    """
    quoted = set()
    for criterion in (*RED_BEFORE_GREEN, *PUBLISHED_WITH_A_WORD_IN_THE_WAY):
        refusal = _refused(criterion.removeprefix("- "))
        assert refusal is not None, criterion
        found = QUOTED_FRAGMENT.search(refusal)
        assert found is not None, refusal
        quoted.add(found.group(1))

    recorded = {
        entry["criterion_fragment"]
        for entry in _corpus()["refusals"]
        if entry["classification"] == "newly-caught"
    }
    assert quoted == recorded, sorted(quoted.symmetric_difference(recorded))
