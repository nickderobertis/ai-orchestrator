"""What this host installs from each producer it depends on, and the pin each artifact governs.

Eleven repositories publish the tools this harness configures, and each publishes
several artifacts — a crate, a wheel, an npm launcher, an SDK — of which this host
installs exactly one: the wheel `pyproject.toml` pins, or, for `onejudge` and
`onetaskgraph`, the CLI wheel the pinned SDK carries from `uv.lock` at its own version,
which is the one `config/onetaskgraph.version` names, or, for `llmlint`, the wheel its
row's ``installer`` puts in place with no pin of its own, or, for `onepipeline` and
`onepipeline-ui`, the npm package `package.json` pins and `bun.lock` installs from
npmjs.com — the engine's `onepipeline-cli` and the read API's `onepipeline-api-cli`,
whose ``npm:`` artifacts are the only ones here not from PyPI. A node waiting on a release
has to say *which* artifact it waits for, because "the crate is out" and "the wheel is
out" are different waits, and `config/onevcs.releases.yml` says it once per producer as
that producer's ``default_target``. This module is the one table the pieces of that
answer are held to: the producer, the short target name the override names, the
registry-qualified artifact the producer's own declaration gives that name, and the
``config/<pin>.version`` file the installed artifact governs — or, where no pin governs
it, the script that installs it. `tests/test_host_installs.py`
reconciles the table against `pyproject.toml` (or `package.json` for an ``npm:``
artifact), `config/`, and the override; its
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

Not every producer the override gives a ``default_target`` is one this host installs
from. ``CONSUMER_DEFAULTS`` names the producers whose default is instead the artifact a
consumer *outside* this repository waits on — `printobserver`'s `github-release`, which a
`local-direct` repository elsewhere adopts the program from, `privateterms`' `crate`,
which the tools that detect or rewrite private terms build against, and `onetaskgraph-ui`'s
`npm`, the component package `onepipeline-ui` installs — so a `published` node of such a
repository can wait on that release naming no ``consumes``, which `local-direct` refuses.
Such a producer has no row, because nothing here installs it, and every
producer the override gives a default is in exactly one of ``INSTALLED`` and
``CONSUMER_DEFAULTS``: `tests/test_host_installs.py` holds that, and
`tests/sibling_facts.py` holds each default to the producer's own declaration. The
difference matters to onevcs's `default-taken` rule, which refuses a node elsewhere taking
a default this host installs — the artifacts the override's own rule for this repository
lists as its `installs`, held equal to the rows here — and none of these.

One thing this host runs is deliberately not a row. `oneharness-core` is a *crate* the
engine links on its own account, published from the `oneharness` repository on a
cadence of its own: the wheel this host installs from that repository is the CLI, and
the engine's SBOM — not this table — is what says which core a dispatch runs.
"""

from __future__ import annotations

from typing import NamedTuple

__all__ = [
    "CONSUMER_DEFAULTS",
    "INSTALLED",
    "Installed",
    "by_producer",
    "rendered",
]


# llmlint: ignore-block[modern_domain_modeling] The four names are `str` because each is
# compared with text read at a boundary — the identity `onevcs` prints, the artifact id a
# declaration and the override's `installs` carry, a pin file's stem — and a `NewType` here
# would be one no reader mints.
# Block-scoped because the rule reads each field line, not the class line.
class Installed(NamedTuple):
    """One artifact this host installs, and where each of its four names is spelled."""

    #: The producing repository, as `onevcs` files it: ``github.com/<owner>/<name>``.
    producer: str
    #: The producer's short target name — the override's ``default_target`` for it.
    target: str
    #: The registry-qualified id the producer's declaration gives that target,
    #: ``<registry>:<distribution>``; its distribution is what `pyproject.toml` pins, or
    #: `package.json` for an ``npm:`` one.
    artifact: str
    #: The stem of the ``config/<pin>.version`` file this artifact governs, or None for a
    #: wheel no pin governs, which names its ``installer`` instead.
    pin: str | None
    #: Whether a dispatched node runs this artifact: true for the engine alone.
    governs_dispatch: bool
    #: The script that installs a wheel no pin governs, from this repository's root;
    #: None for every pinned artifact, which `uv.lock` or `bun.lock` installs.
    installer: str | None = None


# llmlint: ignore-end[modern_domain_modeling]


#: What this host installs, one row per producer, in the override's order.
INSTALLED: tuple[Installed, ...] = (
    Installed(
        producer="github.com/nickderobertis/onepipeline",
        target="npm",
        artifact="npm:onepipeline-cli",
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
        target="npm-cli",
        artifact="npm:onepipeline-api-cli",
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
        producer="github.com/nickderobertis/onebudgetspec",
        target="pypi",
        artifact="pypi:onebudgetspec-cli",
        pin="onebudgetspec",
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
    Installed(
        producer="github.com/nickderobertis/onefollowup",
        target="pypi",
        artifact="pypi:onefollowup-cli",
        pin="onefollowup",
        governs_dispatch=False,
    ),
)


#: Producers `config/onevcs.releases.yml` gives a ``default_target`` that this host
#: installs nothing of, each as ``github.com/<owner>/<name>``: each default names the
#: artifact a consumer outside this repository waits on, rather than a wheel here. Never
#: also a row, and never a producer the override names no default for;
#: `tests/test_host_installs.py` holds the override to both sets.
# llmlint: ignore-block[modern_domain_modeling] Each entry is compared against the identity
# text `onevcs` prints and against the override's rules, both plain `str` at that boundary,
# for the reason the block above `Installed` gives.
CONSUMER_DEFAULTS: frozenset[str] = frozenset(
    {
        "github.com/nickderobertis/onetaskgraph-ui",
        "github.com/nickderobertis/printobserver",
        "github.com/nickderobertis/privateterms",
    }
)
# llmlint: ignore-end[modern_domain_modeling]


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
