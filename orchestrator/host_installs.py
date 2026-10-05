"""What this host installs from each producer it depends on, and the pin each wheel governs.

Nine repositories publish the tools this harness configures, and each publishes
several artifacts — a crate, a wheel, an npm launcher, an SDK — of which this host
installs exactly one: the wheel `pyproject.toml` pins, or, for `onejudge` and
`onetaskgraph`, the CLI wheel the pinned SDK carries from `uv.lock` at its own version,
which is the one `config/onetaskgraph.version` names, or, for `llmlint`, the wheel its
row's ``installer`` puts in place with no pin of its own. A node waiting on a release
has to say *which* artifact it waits for, because "the crate is out" and "the wheel is
out" are different waits, and `config/onevcs.releases.yml` says it once per producer as
that producer's ``default_target``. This module is the one table the pieces of that
answer are held to: the producer, the short target name the override names, the
registry-qualified artifact the producer's own declaration gives that name, and the
``config/<pin>.version`` file the installed wheel governs — or, where no pin governs it,
the script that installs it. `tests/test_host_installs.py`
reconciles the table against `pyproject.toml`, `config/`, and the override; its
`reads_checkouts` tier reads each producer's own declaration for a contradiction of a
row, which refuses a change that edits this registration and is reported for any other.

``governs_dispatch`` is true for one row and the distinction is the reason the table
exists. Every ``config/*.version`` names one adopted release, but they do not all answer
the same question: `config/onepipeline.version` decides what a dispatched node runs,
through the crates that release linked — the `onevcs` it publishes through, the
`oneagentgraph` that reads its persona, the `onejudge` it settles on — while every other
pin governs only the host's own commands. Reading the wrong pin has produced a wrong
diagnosis here twice, both times with every pin looking current while a real run came
out empty; `AGENTS.md`'s "Which pin governs a dispatch" is the account of it.

`llmlint` is the one row with no pin. `scripts/setup-llmlint.sh` installs its
`llmlint-cli` wheel with `uv tool`, outside `pyproject.toml`, taking the newest release
whose declared `oneharness-cli` requirement `config/oneharness.version` admits — so its
``pin`` is None and its ``installer`` names that script, and a release of it is adopted
by that script's next run rather than by moving a pin. It is still a row because a
`published` node of this repository waits on that wheel like any other: without it, the
override would name a ``default_target`` nothing here installs.

One thing this host runs is deliberately not a row. `oneharness-core` is a *crate* the
engine links on its own account, published from the `oneharness` repository on a
cadence of its own: the wheel this host installs from that repository is the CLI, and
the engine's SBOM — not this table — is what says which core a dispatch runs.
"""

from __future__ import annotations

from typing import NamedTuple

__all__ = [
    "AWAITING_FIRST_ADOPTION",
    "INSTALLED",
    "Installed",
    "by_artifact",
    "by_producer",
    "rendered",
]


# llmlint: ignore-block[modern_domain_modeling] The four names are `str` by the interface
# this module was specified to: its callers — the plan-check and planner-prompt nodes that
# follow — hand it the identity `onevcs` prints and the artifact id a declaration carries,
# both plain text at that boundary, and a `NewType` here would be one no caller mints.
# Block-scoped because the rule reads each field line, not the class line.
class Installed(NamedTuple):
    """One artifact this host installs, and where each of its four names is spelled."""

    #: The producing repository, as `onevcs` files it: ``github.com/<owner>/<name>``.
    producer: str
    #: The producer's short target name — the override's ``default_target`` for it.
    target: str
    #: The registry-qualified id the producer's declaration gives that target,
    #: ``<registry>:<distribution>``; its distribution is what `pyproject.toml` pins.
    artifact: str
    #: The stem of the ``config/<pin>.version`` file this wheel governs, or None for a
    #: wheel no pin governs, which names its ``installer`` instead.
    pin: str | None
    #: Whether a dispatched node runs this artifact: true for the engine wheel alone.
    governs_dispatch: bool
    #: The script that installs a wheel no pin governs, from this repository's root;
    #: None for every pinned wheel, which `pyproject.toml` and `uv.lock` install.
    installer: str | None = None


# llmlint: ignore-end[modern_domain_modeling]


#: What this host installs, one row per producer, in the override's order.
INSTALLED: tuple[Installed, ...] = (
    Installed(
        producer="github.com/nickderobertis/onepipeline",
        target="pypi",
        artifact="pypi:onepipeline-cli",
        pin="onepipeline",
        governs_dispatch=True,
    ),
    Installed(
        producer="github.com/nickderobertis/onevcs",
        target="pypi",
        artifact="pypi:onevcs-cli",
        pin="onevcs",
        governs_dispatch=False,
    ),
    Installed(
        producer="github.com/nickderobertis/oneagentgraph",
        target="pypi",
        artifact="pypi:oneagentgraph-cli",
        pin="oneagentgraph",
        governs_dispatch=False,
    ),
    Installed(
        producer="github.com/nickderobertis/onejudge",
        target="cli",
        artifact="pypi:onejudge-cli",
        pin="onejudge",
        governs_dispatch=False,
    ),
    Installed(
        producer="github.com/nickderobertis/oneharness",
        target="cli-wheel",
        artifact="pypi:oneharness-cli",
        pin="oneharness",
        governs_dispatch=False,
    ),
    Installed(
        producer="github.com/nickderobertis/onetaskgraph",
        target="pypi",
        artifact="pypi:onetaskgraph-cli",
        pin="onetaskgraph",
        governs_dispatch=False,
    ),
    Installed(
        producer="github.com/nickderobertis/onepipeline-ui",
        target="pypi-cli",
        artifact="pypi:onepipeline-api-cli",
        pin="onepipeline-ui",
        governs_dispatch=False,
    ),
    Installed(
        producer="github.com/nickderobertis/onemessagebus",
        target="pypi",
        artifact="pypi:onemessagebus-cli",
        pin="onemessagebus",
        governs_dispatch=False,
    ),
    Installed(
        producer="github.com/nickderobertis/llmlint",
        target="cli",
        artifact="pypi:llmlint-cli",
        pin=None,
        governs_dispatch=False,
        installer="scripts/setup-llmlint.sh",
    ),
)


#: Producers `config/onevcs.releases.yml` names a ``default_target`` for ahead of their
#: first adoption here, each as ``github.com/<owner>/<name>``. A row needs a
#: ``config/<pin>.version`` and a `pyproject.toml` pin, and a producer that has released
#: nothing can have neither; yet a `published` node adopting its first release names no
#: ``consumes``, so without the override's rule its hold would wait on a target it never
#: resolves and never release. A producer stands here instead of in ``INSTALLED`` — never
#: in both — and the node that adopts its release moves it into a row, with the pin and
#: the dependency, emptying its entry: for `onebudgetspec`, the plan's ``aio-budgets``
#: node. `tests/test_host_installs.py` holds the override to the two together.
# llmlint: ignore-block[modern_domain_modeling] Each entry is an `Installed.producer` value
# waiting to become one, compared against that `str` field and against the identity text
# `onevcs` prints, for the reason the block above `Installed` gives.
AWAITING_FIRST_ADOPTION: frozenset[str] = frozenset({"github.com/nickderobertis/onebudgetspec"})
# llmlint: ignore-end[modern_domain_modeling]


def by_artifact(artifact: str) -> Installed | None:
    """The row installing ``artifact`` (``pypi:onepipeline-cli``), or None for none."""
    return next((row for row in INSTALLED if row.artifact == artifact), None)


def by_producer(producer: str) -> Installed | None:
    """The row for ``producer`` (``github.com/nickderobertis/onepipeline``), or None."""
    return next((row for row in INSTALLED if row.producer == producer), None)


def rendered() -> str:
    """The table as prose, one line per row, for a prompt a judge reads.

    Each line names the producer, the target name a consumer waits on, the artifact
    that target is, and the pin it moves — or, for a row no pin governs, the script
    that installs it and that no pin is moved; the one row a dispatch runs says so,
    because that is the distinction a reader deciding which pin to move has to hold.
    """
    return "".join(f"- {_governed(row)}.\n" for row in INSTALLED)


def _governed(row: Installed) -> str:
    """One row's line of :func:`rendered`, without its bullet and full stop."""
    released = f"{row.producer} releases its `{row.target}` target as `{row.artifact}`"
    if row.pin is None:
        return (
            f"{released}, which `{row.installer}` installs with no `config/` pin, so "
            f"adopting a release of it moves no pin and names that script instead"
        )
    return f"{released}, which `config/{row.pin}.version` pins" + (
        " and which every dispatched node runs" if row.governs_dispatch else ""
    )
