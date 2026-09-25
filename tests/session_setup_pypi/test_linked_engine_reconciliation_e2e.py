"""What a provisioned worktree's engine binary actually links, end to end.

`config/onepipeline.version` is the pin that decides what a *dispatch* runs, because
`onepipeline` links `oneagentgraph`, `onevcs`, and `onejudge` as Rust libraries — and,
through `oneagentgraph` and `onejudge`, `oneharness-core`, the crate
that runs the turn itself and the one no `config/*.version` here can name. Every
other reading of that question is a claim about it: a `Cargo.toml` requirement permits
versions a build never resolved, a `Cargo.lock` describes what a release *would* link,
and even the wheel's own SBOM is a statement the wheel makes about itself. The binary
is the artifact that runs.

So this journey provisions a worktree the way a session really does — the real
`scripts/session-setup.sh`, a real `uv sync` from PyPI into a fresh venv — and then
reads the engine it installed:

* the executable's own bytes, which carry the registry path of every crate it was
  compiled against, exactly as `AGENTS.md`'s `strings | grep` procedure reads them;
* the CycloneDX SBOM that wheel ships, which `tests/test_linked_libraries.py` gates the
  pins against without a network or a clone;
* the `config/*.version` pins the provisioning was driven from.

All three have to agree on what is linked, crate for crate — including
`oneharness-core`, which is checked as the whole set its dependents resolve rather than
as a single value, so this journey says the same thing whether they agree on one
release or split over several. The
unit gate holds the last two together on this checkout; what only a journey can prove
is that the wheel's declaration is true of the binary it shipped — which is the one
step where a rebuild, a repair, or an install from somewhere else parts a host from
every lockfile describing it, and the step no document can check itself.

llmlint: ignore-file[tests_mirror_real_usage] The subject here is what the installed engine
binary links, and no command it offers reports that: the executable's own bytes and the
SBOM its wheel ships are the only interface to the answer, and AGENTS.md's "The
installed binary decides" names that SBOM as where the answer is measured. What this
journey drives for real is the provisioning a session runs — the real setup script and a
real `uv sync` from PyPI.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from provisioning import run_setup, setup_repo
from test_linked_libraries import (
    DECLARED_DIVERGENCES,
    LINKED_HARNESS_CORES,
    LINKED_IN_BINARY,
    RECONCILED_PINS,
    UNRECONCILABLE_PIN,
)

from orchestrator.root import REPO_ROOT

#: Where PEP 770 puts a wheel's SBOMs, and the engine distribution that ships one.
ENGINE_DISTRIBUTION = "onepipeline_cli"
SBOM_DIRECTORY = "sboms"


def _linked_in_binary(executable: Path) -> dict[str, set[str]]:
    """Every crate release the compiled engine carries a registry path for."""
    carried: dict[str, set[str]] = {}
    for crate, version in LINKED_IN_BINARY.findall(executable.read_bytes()):
        carried.setdefault(crate.decode(), set()).add(version.decode())
    return carried


def _declared_in_sbom(venv: Path) -> dict[str, set[str]]:
    """Every crate release the installed engine wheel *declares* it links, as a set.

    Sets rather than one version each, because one crate here is legitimately linked
    twice and a reader that collapsed it would drop it silently — which is how
    `oneharness-core` stayed outside every reconciliation on this host.
    """
    sboms = sorted(
        venv.glob(f"lib/*/site-packages/{ENGINE_DISTRIBUTION}-*.dist-info/{SBOM_DIRECTORY}/*.json")
    )
    assert len(sboms) == 1, f"the provisioned engine must ship exactly one SBOM; found {sboms}"
    document = json.loads(sboms[0].read_text("utf-8"))
    declared: dict[str, set[str]] = {}
    for component in document["components"]:
        declared.setdefault(component["name"], set()).add(component["version"])
    return declared


def test_the_engine_a_session_provisions_reconciles_against_this_hosts_pins(
    tmp_path: Path,
) -> None:
    """The binary and the wheel agree on every linked crate, reconciled against `config/`.

    The binary and its own SBOM must agree outright: a wheel describing something the
    executable it shipped does not carry is the case no lockfile can report. A pin is
    held to that same release *or* to a divergence already declared with both versions
    and its reason, which is why this reconciles rather than equates.

    The failure this catches is the one that has cost this host two release cycles
    and, both times, showed nothing: a pin reading one version while the engine a
    dispatch runs carries another, with every version file on the host looking
    current and a real run coming out empty. Reading the binary is what makes the
    answer independent of everything that merely describes it.
    """
    repo = setup_repo(tmp_path)

    installed = run_setup(repo, tmp_path)

    assert installed.returncode == 0, installed.stderr
    engine = repo / ".venv" / "bin" / "onepipeline"
    adopted = (REPO_ROOT / "config" / "onepipeline.version").read_text("utf-8").strip()
    reported = subprocess.run([engine, "--version"], text=True, capture_output=True, check=True)
    assert reported.stdout.strip() == f"onepipeline {adopted}"

    carried = _linked_in_binary(engine.resolve())
    all_declared = _declared_in_sbom(repo / ".venv")
    declared = {
        crate: next(iter(versions))
        for crate, versions in all_declared.items()
        if len(versions) == 1
    }

    # The crate no pin can name, checked first because it is the one this reconciliation
    # used to drop: whatever set of releases the wheel declares, the binary has to
    # carry exactly that set. `tests/test_linked_libraries.py` says which dependent
    # brings which; what only a provisioned binary can say is that they are really in
    # it. Compared as sets rather than as a count, which is what let this survive the
    # two linked cores collapsing into one without the assertion needing a word changed.
    core = UNRECONCILABLE_PIN.crate
    expected = {resolved.core for resolved in LINKED_HARNESS_CORES}
    assert carried.get(core) == expected == all_declared.get(core), (
        f"the provisioned onepipeline {adopted} carries {core} {sorted(carried.get(core, ()))}, "
        f"its own SBOM declares {sorted(all_declared.get(core, ()))}, and this repository is "
        f"written against {sorted(expected)}. A dispatched turn runs one of these and "
        f"`config/{UNRECONCILABLE_PIN.pin}.version` names none of them"
    )

    for crate, version_file in sorted(RECONCILED_PINS.items()):
        assert carried.get(crate) == {declared[crate]}, (
            f"the provisioned onepipeline {adopted} carries {crate} "
            f"{sorted(carried.get(crate, ()))} and its own SBOM declares {declared[crate]}. "
            "The binary is what a dispatch runs, so the wheel's declaration — and every "
            "gate reading it — is describing something this host does not have"
        )
        pinned = (REPO_ROOT / "config" / version_file).read_text("utf-8").strip()
        if pinned == declared[crate]:
            continue
        # The one permitted gap, and it is permitted *here* only because
        # `tests/test_linked_libraries.py` holds it to a declaration naming both
        # versions and the reason. This journey re-states neither: it asserts the
        # gap is one that gate knows about, so a second, undeclared one fails.
        assert crate in DECLARED_DIVERGENCES, (
            f"config/{version_file} adopts {crate} {pinned} while the engine this "
            f"session provisioned links {declared[crate]}; reconcile the pin or declare "
            "the divergence where every other one is declared"
        )
