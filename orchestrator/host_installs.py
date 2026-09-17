"""What this host installs from each producer it depends on, and the pin each wheel governs.

Eight repositories publish the tools this harness configures, and each publishes
several artifacts — a crate, a wheel, an npm launcher, an SDK — of which this host
installs exactly one: the wheel `pyproject.toml` pins, or, for `onejudge` and
`onetaskgraph`, the CLI wheel the pinned SDK carries from `uv.lock` at its own version,
which is the one `config/onetaskgraph.version` names. A node waiting on a release
has to say *which* artifact it waits for, because "the crate is out" and "the wheel is
out" are different waits, and `config/onevcs.releases.yml` says it once per producer as
that producer's ``default_target``. This module is the one table the pieces of that
answer are held to: the producer, the short target name the override names, the
registry-qualified artifact the producer's own declaration gives that name, and the
``config/<pin>.version`` file the installed wheel governs. `tests/test_host_installs.py`
reconciles the table against `pyproject.toml`, `config/`, and the override; the
`reads_checkouts` tier reconciles each artifact against the producer's own declaration.

``governs_dispatch`` is true for one row and the distinction is the reason the table
exists. Every ``config/*.version`` names one adopted release, but they do not all answer
the same question: `config/onepipeline.version` decides what a dispatched node runs,
through the crates that release linked — the `onevcs` it publishes through, the
`oneagentgraph` that reads its persona, the `onejudge` it settles on — while every other
pin governs only the host's own commands. Reading the wrong pin has produced a wrong
diagnosis here twice, both times with every pin looking current while a real run came
out empty; `AGENTS.md`'s "Which pin governs a dispatch" is the account of it.

Two things this host runs are deliberately not rows. `llmlint` is installed by
`scripts/setup-llmlint.sh` from its own repository with no `config/` pin and no entry in
`pyproject.toml`, so there is no installed artifact here to name and no pin for a release
to move. `oneharness-core` is a *crate* the engine links on its own account, published
from the `oneharness` repository on a cadence of its own: the wheel this host installs
from that repository is the CLI, and the engine's SBOM — not this table — is what says
which core a dispatch runs.
"""

from __future__ import annotations

from typing import NamedTuple

__all__ = ["INSTALLED", "Installed", "by_artifact", "by_producer", "rendered"]


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
    #: The stem of the ``config/<pin>.version`` file this wheel governs.
    pin: str
    #: Whether a dispatched node runs this artifact: true for the engine wheel alone.
    governs_dispatch: bool


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
)


def by_artifact(artifact: str) -> Installed | None:
    """The row installing ``artifact`` (``pypi:onepipeline-cli``), or None for none."""
    return next((row for row in INSTALLED if row.artifact == artifact), None)


def by_producer(producer: str) -> Installed | None:
    """The row for ``producer`` (``github.com/nickderobertis/onepipeline``), or None."""
    return next((row for row in INSTALLED if row.producer == producer), None)


def rendered() -> str:
    """The table as prose, one line per row, for a prompt a judge reads.

    Each line names the producer, the target name a consumer waits on, the artifact
    that target is, and the pin it moves; the one row a dispatch runs says so, because
    that is the distinction a reader deciding which pin to move has to hold.
    """
    return "".join(
        f"- {row.producer} releases its `{row.target}` target as `{row.artifact}`, "
        f"which `config/{row.pin}.version` pins"
        + (" and which every dispatched node runs" if row.governs_dispatch else "")
        + ".\n"
        for row in INSTALLED
    )
