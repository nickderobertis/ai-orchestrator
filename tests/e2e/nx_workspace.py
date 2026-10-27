"""Copies of this checkout for journeys that mutate its tracked files.

A journey that edits a tracked file, doubles a binary its recipe resolves, or writes
under the tree a recipe treats as its own runs in a copy rather than in this checkout,
which every other worker reads. `copy_working_tree` copies what git would commit, and
`share_this_checkouts_install` is the one way such a copy runs on this checkout's
installed toolchain: through symlinks, never a `node_modules` or `.venv` of its own. The
provisioning journeys alone build a toolchain of their own, through the setup or
bootstrap under test, in an `isolated_python_root`.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from orchestrator import npm_installs
from orchestrator.npm_installs import ENGINE, READ_API
from orchestrator.root import REPO_ROOT

#: The environment that keeps a copy's installers off every package index: uv answers
#: from its cache alone, and Bun's registry is an address nothing listens on, so an
#: install that would have to download fails rather than reaching the network. A copy's
#: lockfiles are this checkout's, whose own installs already filled both caches.
OFFLINE_INSTALLS = {"UV_OFFLINE": "1", "BUN_CONFIG_REGISTRY": "http://127.0.0.1:9/"}


def copy_working_tree(destination: Path) -> None:
    """Copy exactly the files Nx would hash: everything git would commit from here.

    A *fresh worktree* journey needs these files without the install: `git worktree
    add` carries committed content only, so the change under test reaches the new tree
    through this, and what the tree must not arrive with is the very toolchain it has to
    provision itself. The destination is expected to be empty of tracked content — a
    worktree added with `--no-checkout` — so that what lands there is this working tree
    exactly, with nothing left over from HEAD.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    for relative in filter(None, listing.split("\0")):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target, follow_symlinks=False)


def missing_plan_roots(checkout: Path) -> list[str]:
    """Each `local-md` root ``checkout``'s own `onetaskgraph.yaml` configures that is absent.

    Read from the document rather than from a list here, so a source added to it is held
    too, and resolved as the store resolves a relative root: against the document's own
    directory. Every one is gitignored, so `copy_working_tree` carries none, and the store
    refuses every read while any one is missing; so a provisioned checkout leaving one out
    is one whose plan-store budgets error.
    """
    return [root for root in _local_plan_roots(checkout) if not (checkout / root).is_dir()]


def _local_plan_roots(checkout: Path) -> list[str]:
    """Read local source roots from the checkout's own store configuration."""
    document = yaml.safe_load((checkout / "onetaskgraph.yaml").read_text(encoding="utf-8"))
    roots = [
        str(source["config"]["root"])
        for source in document["sources"].values()
        if source.get("plugin") == "local-md"
    ]
    assert roots, "onetaskgraph.yaml configures no local-md source"
    return roots


def read_a_plan_graph(checkout: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """The plan-store read a gate budget makes, by ``checkout``'s own locked store CLI.

    `project graph` over a tracked example project, which is the read the
    design-document graph's budgets make and the one a missing configured root fails for
    the whole configuration, naming that root — where a listing answers regardless.
    """
    return subprocess.run(
        [str(checkout / ".venv" / "bin" / "onetaskgraph"), "project", "graph"]
        + ["examples:health-endpoint", "--format", "json"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def share_this_checkouts_install(copy: Path) -> dict[str, str]:
    """Let a copy of this checkout run its recipes on this checkout's install, unwritten.

    `copy/node_modules` is an overlay: one symlink per entry of this checkout's, but for
    `.bin` and the platform packages of the engine and the read API, whose `bin/` is a real
    directory of one symlink per binary, so a test doubling the engine replaces that link and
    never writes this checkout's install. `copy/.venv` is an overlay:
    `pyvenv.cfg`, `lib/` (and `lib64/`, `include/` where present) symlink into this
    checkout's `.venv`, and `bin/` is a real directory of one symlink per entry of this
    checkout's `.venv/bin`, so a test doubling one binary replaces that link and never
    writes this checkout's environment. The interpreter reports the copy's `.venv` as
    `sys.prefix`, so the copy passes `scripts/require-provisioned.sh` on its own.

    The copy also holds the plan-store folders the tracked `onetaskgraph.yaml` declares
    private, which session setup creates in every checkout it provisions
    (`scripts/session-setup.sh`'s `create_plan_root`): the store walks each private source
    on every dependency read, and an absent root fails that read.

    The returned variables go into every command run in the copy: `PYTHONPATH=<copy>`
    precedes the editable install's `.pth`, which names this checkout, so `orchestrator`
    — and the `REPO_ROOT` it resolves — is the copy's; `UV_NO_SYNC=1` keeps a `uv run`
    there from syncing the overlay, which would write through its links.
    """
    source = REPO_ROOT / ".venv"
    modules = REPO_ROOT / "node_modules"
    platform_packages = {binary.parent.parent.name for binary in (ENGINE, READ_API)}
    (copy / "node_modules" / ".bin").mkdir(parents=True)
    for entry in modules.iterdir():
        if entry.name in platform_packages:
            (copy / "node_modules" / entry.name / "bin").mkdir(parents=True)
            for inner in entry.iterdir():
                if inner.name != "bin":
                    (copy / "node_modules" / entry.name / inner.name).symlink_to(inner)
            for binary in (entry / "bin").iterdir():
                (copy / "node_modules" / entry.name / "bin" / binary.name).symlink_to(binary)
        elif entry.name != ".bin":
            (copy / "node_modules" / entry.name).symlink_to(entry)
    for entry in (modules / ".bin").iterdir():
        (copy / "node_modules" / ".bin" / entry.name).symlink_to(entry)
    overlay = copy / ".venv"
    (overlay / "bin").mkdir(parents=True)
    for name in ("pyvenv.cfg", "lib", "lib64", "include"):
        if (source / name).exists():
            (overlay / name).symlink_to(source / name)
    for entry in (source / "bin").iterdir():
        (overlay / "bin" / entry.name).symlink_to(entry)
    if (copy / "onetaskgraph.yaml").is_file():
        for root in _local_plan_roots(copy):
            for records in ("tasks", "projects"):
                (copy / root / records).mkdir(parents=True, exist_ok=True)
    return {"PYTHONPATH": str(copy), "UV_NO_SYNC": "1"}


def double_the_engine(copy: Path, stand_in: Path) -> None:
    """Replace a shared copy's engine with a wrapper running ``stand_in``.

    The engine is the platform package's binary every caller runs
    (`orchestrator.npm_installs.ENGINE`), whose link in the copy
    `share_this_checkouts_install` made, so the double replaces the link and this checkout's
    install stays unwritten. The wrapper hands every call to the stand-in on its second
    line, followed by the real binary's bytes, which bash never reads: `just check-plan`
    reads the roles a persona name resolves to out of the installed engine's own bytes.
    """
    engine = npm_installs.platform_binary(node_modules=copy / "node_modules")
    engine.unlink()
    # llmlint: ignore[e2e_not_mocked] The engine is the published CLI this suite doubles at its binary and nowhere above it, as tests/AGENTS.md permits: a real `start` dispatches the paid model, and each stand-in hands every verb it does not double to the real binary, so the recipe, wrappers and store around it run unchanged.  # noqa: E501 - a directive is one line
    engine.write_bytes(
        f"#!/usr/bin/env bash\nexec {shlex.quote(sys.executable)} "
        f'{shlex.quote(str(stand_in))} "$@"\n'.encode()
        + REAL_ENGINE.read_bytes()
    )
    engine.chmod(0o755)


#: The engine binary a stand-in hands every verb it does not double to: this checkout's,
#: which no copy doubles. Never a copy's engine or any launcher, which would reach the
#: stand-in again and recurse.
REAL_ENGINE = ENGINE


def isolated_python_root(destination: Path) -> Path:
    """A copy of this checkout, as a repository, with no toolchain at all.

    For a provisioning journey, whose subject is what writes the toolchain: the copy is
    a repository (`git init`, no history), because the scripts under test resolve their
    checkout through git, and it holds no `node_modules` or `.venv`, so whatever the
    journey provisions there is its own and never this checkout's.
    """
    copy_working_tree(destination)
    subprocess.run(["git", "init", "-q"], cwd=destination, check=True, capture_output=True)
    return destination


def answering_this_checkouts_origin(destination: Path) -> None:
    """Give a copied tree the one thing of `.git` a review reads: this checkout's `origin`.

    `copy_working_tree` copies what git would commit and no `.git`, so a copy answers
    `git remote get-url origin` with nothing — and `orchestrator/plan_review.py` reads
    this host's own repository off that remote to decide which nodes of a plan are its
    own. A copy that could not answer would refuse every review and check run in it for
    want of the review configuration rather than for anything about the plan, and a
    copy answering a *different* origin would be a different bar. So the copy is made a
    repository with no history at all and exactly this checkout's remote: what it then
    answers is what this checkout answers, and nothing else of git is there to read.
    """
    url = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    subprocess.run(["git", "init", "-q"], cwd=destination, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", url], cwd=destination, check=True, capture_output=True
    )
