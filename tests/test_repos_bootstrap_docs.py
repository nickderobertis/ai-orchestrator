"""`docs/host-setup.md` names exactly the outcomes `just repos-bootstrap` reports.

The recipe's per-checkout line opens with one word — what happened to that checkout —
and the host-setup document tells an operator what each word means. The document is
prose and the script is what runs, so nothing but this test keeps the two vocabularies
the same: a word the script starts reporting that the document does not explain, or one
the document explains that the script no longer reports, fails here rather than in an
operator's reading of a session-start report.
"""

from __future__ import annotations

import re

import pytest

from orchestrator.root import REPO_ROOT

# llmlint: ignore[shell_test_tiers_stay_split] This module runs no shell and drives no
# script: it reads `scripts/repos-bootstrap.sh` as text and reconciles its outcome words
# against `docs/host-setup.md` — the offline drift gate over this repository's prose, which
# is what `reads_docs` selects, and whose `wholeWorkspace` key covers the script. There is
# no shell cost here for a project of its own to select or skip, and `reads_recipes` is for
# a tier that reads nothing of the prose, which `tests/conftest.py` enforces.
pytestmark = pytest.mark.reads_docs

SCRIPT = REPO_ROOT / "scripts" / "repos-bootstrap.sh"
DOCUMENT = REPO_ROOT / "docs" / "host-setup.md"
#: The heading under which the document explains the recipe's report.
SECTION = "### The sibling gates: `just repos-bootstrap`"
#: How the script reports one checkout: `report <outcome> <detail> <path>`.
REPORTED = re.compile(r"^\s*report (\w+) ", re.MULTILINE)
#: How the document explains one outcome: a bullet opening with the word in backticks.
EXPLAINED = re.compile(r"^- `(\w+)` — ", re.MULTILINE)


def _section() -> str:
    text = DOCUMENT.read_text(encoding="utf-8")
    start = text.index(SECTION)
    after = text.find("\n## ", start)
    return text[start : after if after != -1 else None]


def test_the_document_explains_every_outcome_the_script_reports() -> None:
    reported = set(REPORTED.findall(SCRIPT.read_text(encoding="utf-8")))
    assert reported, f"{SCRIPT.name} reports no outcome through report()"
    explained = set(EXPLAINED.findall(_section()))
    assert explained == reported, (
        f"{DOCUMENT.name} explains {sorted(explained)} but {SCRIPT.name} reports "
        f"{sorted(reported)}; say what each outcome means under {SECTION!r}"
    )


#: The journeys that read a job's `state` file, each declaring the statuses it expects.
STATE_READERS = (
    REPO_ROOT / "tests" / "e2e" / "test_repos_bootstrap_e2e.py",
    REPO_ROOT / "tests" / "session_setup" / "test_locked_plan_store_setup_e2e.py",
)
#: The script's one statement of the state file's schema, read key by key.
SCHEMA = re.compile(r"^valid_state_value\(\) \{\n(.*?)^\}$", re.MULTILINE | re.DOTALL)
SCHEMA_KEY = re.compile(r"^\s*([\w |]+)\) (.*) ;;$", re.MULTILINE)
STATUS_VALUES = re.compile(r"^\[\[ \$2 =~ \^\(([\w|]+)\)\$ \]\]$")
#: Every state the script writes, and how each journey reads one back.
STATE_WRITE = re.compile(r"^\s*(?:write_state \"\$memo_dir\"|record) (\".*\")", re.MULTILINE)
WRITTEN_FIELD = re.compile(r'"(\w+) ([^"]*)"')
READ_STATUSES = re.compile(r"^JobStatus = Literal\[([^\]]+)\]$", re.MULTILINE)
READ_ENDINGS = re.compile(
    r"^JOB_ENDINGS: frozenset\[JobStatus\] = frozenset\(\{([^}]+)\}\)$", re.MULTILINE
)
READ_KEYS = re.compile(r"""fields(?:\.get\(|\[)"(\w+)"|"(\w+)" in fields""")


def _schema() -> dict[str, str]:
    """Each key the script's state may carry, mapped to the test its value has to pass."""
    bodies = SCHEMA.findall(SCRIPT.read_text(encoding="utf-8"))
    assert len(bodies) == 1, f"{SCRIPT.name} defines valid_state_value {len(bodies)} times"
    return {
        key.strip(): test
        for labels, test in SCHEMA_KEY.findall(bodies[0])
        for key in labels.split("|")
        if key.strip() != "*"
    }


def _writes(*, pass_through: bool = False) -> list[dict[str, str]]:
    """Each state the script writes, as the fields it carries.

    The job's `record` helper adds `pid` and `head` to whatever its caller passes through
    with `"$@"`, so its definition is a write of those two keys and its calls carry the
    rest; `pass_through` includes the definition.
    """
    writes = [
        dict(WRITTEN_FIELD.findall(fields))
        for fields in STATE_WRITE.findall(SCRIPT.read_text(encoding="utf-8"))
        if pass_through or '"$@"' not in fields
    ]
    assert writes, f"{SCRIPT.name} writes no job state"
    return writes


def _literals(declared: str) -> set[str]:
    return {literal.strip().strip('"') for literal in declared.split(",")}


def test_every_state_the_script_writes_is_one_its_own_schema_reads_back() -> None:
    schema = _schema()
    statuses = STATUS_VALUES.match(schema["status"])
    assert statuses, f"{SCRIPT.name} no longer spells the status values as one alternation"
    for write in _writes():
        assert write.keys() <= schema.keys(), f"{SCRIPT.name} writes {write} outside its schema"
        assert "status" in write, f"{SCRIPT.name} writes a state with no status: {write}"
    written = {write["status"] for write in _writes()}
    assert written == set(statuses.group(1).split("|")), (
        f"{SCRIPT.name} writes statuses {sorted(written)} but its schema reads {statuses.group(1)}"
    )


def test_every_journey_reading_job_state_reads_the_schema_the_script_writes() -> None:
    schema = _schema()
    written = {write["status"] for write in _writes()}
    #: A job's ending is the state it writes as it finishes, which is what a journey
    #: waits on.
    endings = {write["status"] for write in _writes() if "finished" in write}
    for reader in STATE_READERS:
        text = reader.read_text(encoding="utf-8")
        declared = READ_STATUSES.findall(text)
        assert len(declared) == 1, f"{reader.name} declares JobStatus {len(declared)} times"
        read = _literals(declared[0])
        assert read == written, (
            f"{reader.name} expects job statuses {sorted(read)} but {SCRIPT.name} writes "
            f"{sorted(written)}"
        )
        declared_endings = READ_ENDINGS.findall(text)
        assert len(declared_endings) == 1, f"{reader.name} declares JOB_ENDINGS once"
        assert _literals(declared_endings[0]) == endings, (
            f"{reader.name} waits on endings {declared_endings[0]} but {SCRIPT.name} "
            f"finishes a job as {sorted(endings)}"
        )
        keys = {key for pair in READ_KEYS.findall(text) for key in pair if key}
        assert keys, f"{reader.name} reads no key of the state file"
        written_keys = {key for write in _writes(pass_through=True) for key in write}
        assert keys <= written_keys & schema.keys(), (
            f"{reader.name} reads state keys {sorted(keys - (written_keys & schema.keys()))} "
            f"that {SCRIPT.name} never writes; it writes {sorted(written_keys & schema.keys())}"
        )
