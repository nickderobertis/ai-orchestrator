"""A literal a document must spell, and the constant that owns it, reconciled.

A document has to spell an environment variable — or a commit trailer a maintainer
will grep `main` for — literally, so the name is unavoidably restated outside the
code that owns it. That restatement is what drifts: rename the constant's value and
the prose keeps confidently naming a string nothing writes or reads.

These tests take the name from the constant rather than repeating it, so a rename
fails here until the document is updated with it.
"""

from __future__ import annotations

import re

import pytest

from orchestrator import REPO_ROOT
from orchestrator.coordination import LOCK_TIMEOUT_ENV
from orchestrator.gitops import (
    DEFAULT_HOOK_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    GIT_HOOK_TIMEOUT_ENV,
    GIT_TIMEOUT_ENV,
    HOOK_RUNNING_COMMANDS,
)
from orchestrator.harnesses import JUDGE_HARNESS_ENV, WORKER_HARNESS_ENV
from orchestrator.provenance import INCOMPLETE_TRAILER, RECOVERY_TRAILER
from orchestrator.provider_health import PROBE_ENV
from orchestrator.scratch import MIN_FREE_BYTES_ENV
from orchestrator.verify import PRESERVED_GATE_LOG_ATTEMPTS
from orchestrator.workspace import RETAINED_INCOMPLETE_RUNS

#: Each variable the code names as a constant, and the document that tells an
#: operator to set it. A tunable documented without such a constant is not listed:
#: there would be nothing to derive from, and inventing one to satisfy this test
#: would restate the name a second time rather than fewer.
DOCUMENTED_TUNABLES = (
    (LOCK_TIMEOUT_ENV, "docs/repo-lifecycle.md"),
    (MIN_FREE_BYTES_ENV, "docs/repo-lifecycle.md"),
    (GIT_TIMEOUT_ENV, "docs/repo-lifecycle.md"),
    (GIT_HOOK_TIMEOUT_ENV, "docs/repo-lifecycle.md"),
    (WORKER_HARNESS_ENV, "docs/onejudge-integration.md"),
    (JUDGE_HARNESS_ENV, "docs/onejudge-integration.md"),
    (PROBE_ENV, "docs/telemetry.md"),
)


@pytest.mark.reads_docs
@pytest.mark.parametrize(("variable", "document"), DOCUMENTED_TUNABLES)
def test_documentation_names_the_variable_its_constant_declares(
    variable: str, document: str
) -> None:
    prose = (REPO_ROOT / document).read_text(encoding="utf-8")

    assert variable in prose, (
        f"{document} does not name {variable}; rename it there in the same change "
        "that renamed the constant, or stop documenting the tunable"
    )


def test_every_documented_tunable_is_reconciled_against_a_constant() -> None:
    """A tunable added to this table without a real constant would prove nothing."""
    assert all(variable and document for variable, document in DOCUMENTED_TUNABLES)
    assert len({variable for variable, _ in DOCUMENTED_TUNABLES}) == len(DOCUMENTED_TUNABLES)


#: Each commit trailer the code writes as a constant, and the document that tells a
#: maintainer to expect it in a published history. `AGENTS.md` states which of them
#: the base branch carries; `docs/repo-lifecycle.md` is its reference.
DOCUMENTED_TRAILERS = (
    (RECOVERY_TRAILER, "AGENTS.md"),
    (RECOVERY_TRAILER, "docs/repo-lifecycle.md"),
    (INCOMPLETE_TRAILER, "docs/repo-lifecycle.md"),
)


@pytest.mark.reads_docs
@pytest.mark.parametrize(("trailer", "document"), DOCUMENTED_TRAILERS)
def test_documentation_names_the_trailer_its_constant_declares(trailer: str, document: str) -> None:
    prose = (REPO_ROOT / document).read_text(encoding="utf-8")

    assert trailer in prose, (
        f"{document} does not name {trailer}; rename it there in the same change "
        "that renamed the constant, or stop documenting the trailer"
    )


#: Where the lifecycle document restates which git operations run a repository's own
#: hooks. Unlike a variable name, this one is a *set*, and a stale set is worse than a
#: stale name: it tells an operator a new operation is bounded by the gate timeout
#: when `_git` is giving it the ordinary one.
_HOOK_COMMAND_LINE = "Hook-running commands: "
_HOOK_COMMAND_DOCUMENT = "docs/repo-lifecycle.md"


@pytest.mark.reads_docs
def test_documentation_lists_exactly_the_hook_running_commands_the_code_classifies() -> None:
    prose = (REPO_ROOT / _HOOK_COMMAND_DOCUMENT).read_text(encoding="utf-8")
    listed = [line for line in prose.splitlines() if line.startswith(_HOOK_COMMAND_LINE)]

    assert len(listed) == 1, (
        f"{_HOOK_COMMAND_DOCUMENT} must carry exactly one line beginning "
        f"{_HOOK_COMMAND_LINE!r}; found {len(listed)}"
    )
    documented = {tuple(match.split()[1:]) for match in re.findall(r"`(git [^`]+)`", listed[0])}

    assert documented == set(HOOK_RUNNING_COMMANDS), (
        f"{_HOOK_COMMAND_DOCUMENT} lists {sorted(documented)} but gitops classifies "
        f"{sorted(HOOK_RUNNING_COMMANDS)}; update both in the same change"
    )


#: A documented default is a number an operator plans around, so a stale one is a
#: false claim rather than a stale label: it tells them a wedged push will be cut
#: loose at a moment the code has since moved. Each entry derives its literal from
#: the constant, so changing the constant alone fails here.
DOCUMENTED_DEFAULTS = (
    (GIT_TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS, "docs/repo-lifecycle.md"),
    (GIT_HOOK_TIMEOUT_ENV, DEFAULT_HOOK_TIMEOUT_SECONDS, "docs/repo-lifecycle.md"),
)


@pytest.mark.reads_docs
@pytest.mark.parametrize(("variable", "default", "document"), DOCUMENTED_DEFAULTS)
def test_documentation_states_the_default_its_constant_declares(
    variable: str, default: float, document: str
) -> None:
    prose = (REPO_ROOT / document).read_text(encoding="utf-8")
    stated = f"`{variable}` (default **{default:g}s**)"

    assert stated in prose, (
        f"{document} does not state {stated}; update it in the same change that "
        "moved the constant, or stop documenting the default"
    )


@pytest.mark.reads_docs
def test_documentation_states_the_preserved_gate_log_retention() -> None:
    """An operator reads this to know how many attempts are still on disk to read."""
    prose = (REPO_ROOT / "docs/repo-lifecycle.md").read_text(encoding="utf-8")

    assert re.search(rf"newest \*\*{PRESERVED_GATE_LOG_ATTEMPTS}\*\*", prose), (
        "docs/repo-lifecycle.md must derive its preserved-gate-log bound from "
        "PRESERVED_GATE_LOG_ATTEMPTS"
    )


@pytest.mark.reads_docs
@pytest.mark.parametrize("document", ("AGENTS.md", "docs/repo-lifecycle.md"))
def test_documentation_states_the_retained_incomplete_run_count(document: str) -> None:
    prose = (REPO_ROOT / document).read_text(encoding="utf-8")

    assert re.search(rf"newest\s+\*\*{RETAINED_INCOMPLETE_RUNS}\*\*", prose), (
        f"{document} must derive its retained-run count from RETAINED_INCOMPLETE_RUNS"
    )
