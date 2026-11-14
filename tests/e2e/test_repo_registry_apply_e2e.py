"""`just repos-apply` registers a checkout list and installs the rules it is handed.

The recipe is how a host brings its `onevcs` registry up to a configuration: it composes
a base rules file with the host's own overlay through `onevcs rules apply`, installs the
release override and the workspaces file beside it, registers every listed checkout it
has, and refuses a checkout whose `publication` or `approvals` neither a rule nor the
repository's own `onevcs.toml` declaration decides. These journeys hold the recipe to that
behaviour and to nothing about which repositories any host registers: every one runs the
real recipe and the installed `onevcs` on a synthetic checkout list and a synthetic rules
file, passed through `--checkouts` and `--rules`, under a sandboxed `HOME`,
`XDG_CONFIG_HOME` and `ONEVCS_HOME` — so what is asserted is what `onevcs` itself
resolved from what the journey wrote, never a reading of this host's routing.

A private repository's rule lives in the host's own overlay, outside every checkout, so
every journey about one installs a host overlay over synthetic identities under the
scratch XDG configuration home and reads the composed policy back through the installed
`onevcs rules check`.

llmlint: ignore-file[e2e_not_mocked] The scratch checkouts stand in for this host's
own clones, which a test may not register or publish from; the recipe, the script,
`onevcs`, and git are all real, and the policy asserted is the one `onevcs` resolved.

llmlint: ignore-file[shell_test_tiers_stay_split] This module is the one journey of `just
repos-apply`, and every journey of that recipe lives here, so they select the same tier
and read the same key. Moving the recipe's journeys to a project of their own is a change
to `nx.json`, `orchestrator/project.json` and `tests/nx_inputs.py` together, for every one
of them at once, and is not one more journey's to make.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple, NotRequired, TypedDict, cast

import pytest
from registered_checkouts import RepoIdentity
from scratch_identity import seeded
from sibling_facts import COMMITTER, declaration
from sibling_facts import origin_checkout as checkout

from orchestrator.gate_impact import UV_LOCK
from orchestrator.host_installs import INSTALLED, Installed
from orchestrator.root import REPO_ROOT

#: The tracked release override the recipe installs beside the rules file, which
#: decides the rung a node of each repository adopts a dependency's release on and
#: which of a producer's targets a consumer naming none waits for.
TRACKED_RELEASES = REPO_ROOT / "config" / "onevcs.releases.yml"
#: The tracked workspaces file the recipe installs as the fourth, which sizes each
#: identity's pool of warm worktree slots and what a return clears.
TRACKED_WORKSPACES = REPO_ROOT / "config" / "onevcs.workspaces.yml"
#: What that file says every identity no rule names gets, which is the shared default
#: for a pool every host starts from: one warm slot, and sessions past it admitted
#: without bound. A host raises it in its own overlay, never in the tracked file.
SHARED_POOL = 1
SHARED_OVERFLOW = "unlimited"
#: Where the recipe reads a host's overlay from under its XDG configuration home, the
#: directory the Claude identities file lives in. Every journey here points that home
#: at scratch, so the host this suite runs on says nothing into a registry a journey
#: is asserting its own files alone reached.
HOST_OVERLAY = Path("ai-orchestrator") / "workspaces.yml"
#: The identity `config/onevcs.releases.yml` gives the one non-default rung, and the
#: rung: a node of this repository waits for a producer's release rather than adopting
#: its branch.
THIS_REPOSITORY: RepoIdentity = "github.com/nickderobertis/ai-orchestrator"
THIS_REPOSITORY_RUNG = "published"
#: The rung every producer resolves — the global one, because the override names none
#: for them — and what `onevcs` answers for a repository the override never mentions.
GLOBAL_RUNG = "fast"

#: The schema a current rules file declares. At 3 a `gate:` anywhere is refused by
#: `deny_unknown_fields`; at 1 and 2 it is accepted, ignored, and reported once. Both
#: halves are driven below, because "the engine refuses a gate" and "the engine still
#: tolerates an unmigrated host" are separate claims.
RULES_SCHEMA_VERSION = 3

#: The synthetic public identities the base rules below route, one per publication kind
#: a base rule states, and the `(publication, approvals)` each one is written with. No
#: host registers them, and the rules naming them are this module's own.
LOCALLY: RepoIdentity = "github.com/scratchowner/locally"
SIBLING: RepoIdentity = "github.com/scratchowner/sibling"


class Policy(NamedTuple):
    """How a change to one identity publishes, as a rule states it."""

    publication: str
    approvals: str


#: Merged in place, with nobody else's approval.
LOCAL_DIRECT = Policy("local-direct", "none")
#: A change request that merges itself once its merge path passes.
CHANGE_AUTO = Policy("change-auto", "none")
BASE_POLICY: dict[RepoIdentity, Policy] = {LOCALLY: LOCAL_DIRECT, SIBLING: CHANGE_AUTO}

#: The synthetic team identity whose routing the host-overlay journey resolves. It is the
#: one whose rule a resolver cannot be taken on trust for: `default:` resolves the
#: reviewed pair too, so a policy read alone would answer identically whether the rule
#: matched or nothing did. Private: its rule exists only in the host overlay below.
QUIETHARBOR: RepoIdentity = "github.com/hiddenco/quietharbor"
#: A second private team identity, so the overlay states more than one rule.
STILLWATER: RepoIdentity = "github.com/hiddenco/stillwater"
#: A private personal repository that publishes locally, ruled by the overlay alone.
PEBBLECRAFT: RepoIdentity = "github.com/scratchowner/pebblecraft"
#: Where the recipe reads a host's rules overlay from under its XDG configuration home.
HOST_RULES = Path("ai-orchestrator") / "onevcs.rules.yml"
#: A host's own rules overlay over the synthetic private identities: the shape a host
#: that also works in private repositories keeps outside every checkout. It is at the
#: schema that admits `visibility`, so the host is never asked about them.
HOST_RULES_OVERLAY_TEXT = (
    "version: 4\n"
    "rules:\n"
    "  - match: {host: github.com, owner: hiddenco, name: quietharbor}\n"
    "    publication: change-open\n"
    "    approvals: required\n"
    "    visibility: private\n"
    "  - match: {host: github.com, owner: hiddenco, name: stillwater}\n"
    "    publication: change-open\n"
    "    approvals: required\n"
    "    visibility: private\n"
    "  - match: {host: github.com, owner: scratchowner, name: pebblecraft}\n"
    "    publication: local-direct\n"
    "    approvals: none\n"
    "    visibility: private\n"
)


class OverlayPolicy(NamedTuple):
    """What the overlay states for one identity, and the rule position it holds there."""

    publication: str
    approvals: str
    position: int

    @property
    def policy(self) -> Policy:
        """The publication and approvals alone, as a base rule would state them."""
        return Policy(self.publication, self.approvals)


#: What that overlay states for each identity, restated independently of its text; the
#: composed file keeps each rule's position ahead of every base rule.
OVERLAY_POLICY: dict[RepoIdentity, OverlayPolicy] = {
    QUIETHARBOR: OverlayPolicy("change-open", "required", 1),
    STILLWATER: OverlayPolicy("change-open", "required", 2),
    PEBBLECRAFT: OverlayPolicy("local-direct", "none", 3),
}


def rules_naming(policies: Mapping[RepoIdentity, Policy]) -> str:
    """A base rules file with one rule per identity of ``policies``, in order, and a default.

    Each rule names its repository outright and declares it `public`, so registering one
    never asks a host about a repository that does not exist; the default is the reviewed
    pair, which `just repos-apply` refuses a checkout for falling through to.
    """
    rules = "".join(
        "  - match: {{host: {}, owner: {}, name: {}}}\n"
        "    publication: {}\n"
        "    approvals: {}\n"
        "    visibility: public\n".format(
            *identity.split("/"), policy.publication, policy.approvals
        )
        for identity, policy in policies.items()
    )
    return (
        "version: 4\n"
        "trailer_prefix: Orchestrator-\n"
        f"rules:\n{rules or '  []\n'}"
        "default:\n"
        "  publication: change-open\n"
        "  approvals: required\n"
    )


def write_rules(path: Path, policies: Mapping[RepoIdentity, Policy] = BASE_POLICY) -> Path:
    """``path``, holding :func:`rules_naming` of ``policies``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rules_naming(policies), encoding="utf-8")
    return path


def sandbox(home: Path, config_home: Path) -> dict[str, str]:
    """The environment every journey runs the recipe in: no file of this host's reached.

    A scratch `HOME` beside the registry root, the scratch XDG configuration home, and the
    registry root itself, so the recipe finds neither this host's checkout list nor its
    rules or workspaces overlays, and writes no registry but ``home``.
    """
    user_home = home.parent / f"{home.name}-user"
    user_home.mkdir(parents=True, exist_ok=True)
    config_home.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "HOME": str(user_home),
        "XDG_CONFIG_HOME": str(config_home),
        "ONEVCS_HOME": str(home),
    }


def apply_registry(
    manifest: Path,
    home: Path,
    rules: Path,
    *arguments: str,
    config_home: Path | None = None,
    temporary: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the real recipe on ``manifest`` and ``rules`` against a scratch registry root.

    ``config_home`` is the XDG configuration home the recipe resolves the host's
    workspaces and rules overlays under; a scratch sibling of ``home`` holding nothing
    when a journey names none, so the files the journey names are installed alone.
    ``temporary``, when named, is the recipe's ``TMPDIR``, for a journey that reads what
    a run left there.
    """
    if config_home is None:
        config_home = home.parent / f"{home.name}-config"
    scratch = {"TMPDIR": str(temporary)} if temporary is not None else {}
    return subprocess.run(
        ["just", "repos-apply", "--checkouts", str(manifest), "--rules", str(rules), *arguments],
        cwd=REPO_ROOT,
        env={**sandbox(home, config_home), **scratch},
        text=True,
        capture_output=True,
    )


def onevcs(
    home: Path, *arguments: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """The installed `onevcs`, reading the registry under ``home``."""
    return subprocess.run(
        ["uv", "run", "--project", str(REPO_ROOT), "onevcs", *arguments],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(home)},
        text=True,
        input=stdin,
        capture_output=True,
    )


def reported(output: str, key: str) -> str:
    """One field of `onevcs rules check`, without its `(from rule 1)` annotation."""
    for line in output.splitlines():
        name, separator, value = line.partition(": ")
        if separator and name.strip() == key:
            return value.split(" (from ")[0].strip()
    raise AssertionError(f"no {key!r} in:\n{output}")


class Row(NamedTuple):
    """One row of the resolved-policy table: each field and the source that decided it."""

    publication: str
    publication_source: str
    approvals: str
    approvals_source: str


def table_row(output: str, identity: RepoIdentity) -> Row:
    """The resolved-policy row `just repos-apply` printed for ``identity``."""
    table = output.split("apply-repo-registry: resolved policy", 1)[1]
    row = next(line for line in table.splitlines() if f" {identity} " in line)
    found = re.search(r"\s(\S+)\s+\(([^)]*)\)\s+(\S+)\s+\(([^)]*)\)$", row)
    assert found is not None, row
    return Row(*found.groups())


def listed(path: Path, *checkouts: Path | str) -> Path:
    """``path``, holding a checkout list naming each of ``checkouts``, one per line."""
    path.write_text("".join(f"{entry}\n" for entry in checkouts), encoding="utf-8")
    return path


def declaring_checkout(directory: Path, origin: str, targets: dict[str, str]) -> Path:
    """A checkout of `origin` whose base declares each ``name: id`` of ``targets``.

    Committed on `main` rather than left in the working tree, because `onevcs` reads a
    producer's declaration at the publication checkout's base and nowhere else. What it
    declares is what this repository decides of the producer — the target the override
    names, under the artifact the table installs — beside one target nothing here names,
    because a producer declares more than this host consumes; never a copy of the real
    producer's declaration, which is that repository's to move.
    """
    directory = checkout(directory, origin)
    (directory / "release-targets.toml").write_text(
        declaration({**targets, "crate": f"crate:{directory.name}"}), encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=directory, check=True)
    subprocess.run(
        ["git", *COMMITTER, "commit", "-qm", "chore: declare"], cwd=directory, check=True
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=directory, check=True
    )
    return directory


def host_config(config_home: Path, rules: str = HOST_RULES_OVERLAY_TEXT) -> Path:
    """An XDG configuration home holding a host rules overlay, and nothing else."""
    (config_home / HOST_RULES).parent.mkdir(parents=True, exist_ok=True)
    (config_home / HOST_RULES).write_text(rules, encoding="utf-8")
    return config_home


def visibility(home: Path, identity: RepoIdentity) -> str:
    """What `onevcs boundary inspect` answers for ``identity`` under ``home``."""
    asked = onevcs(
        home,
        "boundary",
        "inspect",
        "--input",
        "-",
        stdin=json.dumps({"repository": identity}),
    )
    assert asked.returncode == 0, asked.stdout + asked.stderr
    return cast(str, json.loads(asked.stdout)["visibility"])


def registered_paths(home: Path) -> set[str]:
    """Every checkout path the registry under ``home`` holds; none when it has no registry."""
    document = home / "registry.json"
    if not document.exists():
        return set()
    registry: RegistryDocument = json.loads(document.read_text(encoding="utf-8"))
    return {record["path"] for record in registry["checkouts"].values()}


class Applied(NamedTuple):
    """One scratch registry, brought up to a synthetic base and a host overlay."""

    home: Path
    manifest: Path
    rules: Path
    result: subprocess.CompletedProcess[str]


class RegisteredCheckout(TypedDict):
    identity: str
    path: str


class RegistryDocument(TypedDict):
    identities: dict[str, object]
    checkouts: dict[str, RegisteredCheckout]


@pytest.fixture(scope="module")
def applied(tmp_path_factory: pytest.TempPathFactory) -> Applied:
    """A checkout of every base and overlay identity, registered by the real recipe.

    The base identities are ruled by the synthetic base rules handed to `--rules`; the
    private ones by the host's own rules overlay, installed under a scratch XDG
    configuration home, so the composed policy is read from both sides.
    """
    root = tmp_path_factory.mktemp("repo-registry")
    paths = [
        checkout(root / "checkouts" / key.rpartition("/")[2], f"https://{key}.git")
        for key in (*BASE_POLICY, *OVERLAY_POLICY)
    ]
    manifest = listed(root / "checkouts.list", *paths)
    rules = write_rules(root / "rules.yml")
    home = root / "onevcs"
    result = apply_registry(manifest, home, rules, config_home=host_config(root / "xdg"))
    assert result.returncode == 0, result.stdout + result.stderr
    return Applied(home=home, manifest=manifest, rules=rules, result=result)


def test_every_listed_checkout_is_registered_under_its_origins_identity(applied: Applied) -> None:
    registry: RegistryDocument = json.loads(
        (applied.home / "registry.json").read_text(encoding="utf-8")
    )
    assert set(registry["identities"]) == {*BASE_POLICY, *OVERLAY_POLICY}
    actual = {(record["path"], record["identity"]) for record in registry["checkouts"].values()}
    expected = {
        (str(applied.manifest.parent / "checkouts" / key.rpartition("/")[2]), key)
        for key in (*BASE_POLICY, *OVERLAY_POLICY)
    }
    assert actual == expected


def test_apply_reports_the_resolved_policy_table(applied: Applied) -> None:
    """One row per registered checkout, with the policy `onevcs` resolved for it."""
    policies = {
        **BASE_POLICY,
        **{key: overlay.policy for key, overlay in OVERLAY_POLICY.items()},
    }
    for identity, policy in policies.items():
        row = table_row(applied.result.stdout, identity)
        assert (row.publication, row.approvals) == policy, row
        assert row.publication_source.startswith("rule "), row
        assert row.approvals_source == row.publication_source, row
    overlay = applied.manifest.parent / "xdg" / HOST_RULES
    assert (
        f"rules      the rules composed from 2 file(s) are at {applied.home / 'rules.yml'}"
        f"  (from {applied.rules} overlaid by {overlay})"
    ) in applied.result.stdout, applied.result.stdout


def test_apply_installs_the_tracked_release_override_and_says_so(applied: Applied) -> None:
    """The release override, installed whole beside the rules file and reported."""
    installed = applied.home / "releases.yml"
    assert installed.read_bytes() == TRACKED_RELEASES.read_bytes()
    assert f"releases   installed  {installed}" in applied.result.stdout


def test_apply_installs_the_tracked_workspaces_file_and_the_pool_resolves_from_it(
    applied: Applied,
) -> None:
    """The workspaces file, installed whole and read back through the verb that owns it.

    Installed bytes prove the copy; `onevcs pool status` proves the install is the one
    a session open resolves its placement out of, answering this host's default for an
    identity the file's rules do not name.
    """
    installed = applied.home / "workspaces.yml"
    assert installed.read_bytes() == TRACKED_WORKSPACES.read_bytes()
    assert (
        f"workspaces installed  {installed}  (from {TRACKED_WORKSPACES})" in applied.result.stdout
    )
    status = onevcs(applied.home, "pool", "status", "stillwater", "--json")
    assert status.returncode == 0, status.stdout + status.stderr
    capacity = json.loads(status.stdout)["capacity"]
    assert capacity["pool"] == SHARED_POOL, capacity
    assert capacity["overflow"] == SHARED_OVERFLOW, capacity


#: A host's overlay: a bigger default pool than the shared one, a rule of its own for an
#: identity the tracked file has none for, and then a rule that replaces the tracked rule
#: with the same `match` — `ai-orchestrator`'s, which deletes `.logs/` on every return —
#: so the host says what that rule is instead.
HOST_OVERLAY_TEXT = (
    "version: 1\n"
    "default:\n"
    "  pool: 3\n"
    "rules:\n"
    "  - match: {host: github.com, owner: nickderobertis, name: onevcs}\n"
    "    pool: 2\n"
    "  - match: {host: github.com, owner: nickderobertis, name: ai-orchestrator}\n"
    "    pool: 4\n"
    "    overflow: 2\n"
)


# llmlint: ignore[expensive_tests_stay_behind_their_own_edge] An existing journey of `just repos-apply`; every journey of that recipe lives in this module, and the module docstring's ignore of `shell_test_tiers_stay_split` says why moving them to a project of their own is a change for all of them at once rather than this edit's.  # noqa: E501
def test_the_hosts_overlay_is_composed_onto_the_tracked_file_and_installed(
    tmp_path: Path,
) -> None:
    """With a host file under the XDG home, the installed file is the tracked one overlaid.

    Read back through `onevcs pool status`, which resolves the installed document: the
    ruled identity answers the host's rule, an identity the host did not rule answers
    the host's default over the tracked one, and the report says both sources. The
    tracked file itself is untouched, which is the point of the overlay.
    """
    present = checkout(tmp_path / "ai-orchestrator", f"https://{THIS_REPOSITORY}.git")
    other = checkout(tmp_path / "onejudge", "https://github.com/nickderobertis/onejudge.git")
    manifest = listed(tmp_path / "checkouts", present, other)
    rules = write_rules(
        tmp_path / "rules.yml",
        {
            THIS_REPOSITORY: LOCAL_DIRECT,
            "github.com/nickderobertis/onejudge": CHANGE_AUTO,
        },
    )
    config_home = tmp_path / "xdg"
    (config_home / HOST_OVERLAY).parent.mkdir(parents=True)
    (config_home / HOST_OVERLAY).write_text(HOST_OVERLAY_TEXT, encoding="utf-8")
    home = tmp_path / "home"
    tracked_before = TRACKED_WORKSPACES.read_bytes()

    result = apply_registry(manifest, home, rules, config_home=config_home)

    assert result.returncode == 0, result.stdout + result.stderr
    installed = home / "workspaces.yml"
    assert (
        f"workspaces installed  {installed}  (from {TRACKED_WORKSPACES} overlaid by "
        f"{config_home / HOST_OVERLAY})"
    ) in result.stdout
    assert TRACKED_WORKSPACES.read_bytes() == tracked_before
    assert installed.read_bytes() != tracked_before
    ruled = json.loads(onevcs(home, "pool", "status", "ai-orchestrator", "--json").stdout)
    assert (ruled["capacity"]["pool"], ruled["capacity"]["overflow"]) == (4, 2), ruled
    unruled = json.loads(onevcs(home, "pool", "status", "onejudge", "--json").stdout)["capacity"]
    assert (unruled["pool"], unruled["overflow"]) == (3, SHARED_OVERFLOW), unruled
    # The rule replaced the tracked one in place rather than being appended after the
    # host's own new rule, so first-match-wins still reads the file in the tracked order.
    text = installed.read_text(encoding="utf-8")
    assert text.count("name: ai-orchestrator}") == 1, text
    assert text.index("name: ai-orchestrator}") < text.index("name: onevcs}"), text

    again = apply_registry(manifest, home, rules, config_home=config_home)

    assert again.returncode == 0, again.stdout + again.stderr
    assert "workspaces unchanged" in again.stdout


def test_a_host_overlay_the_composer_refuses_installs_nothing(tmp_path: Path) -> None:
    """A host file at another schema version is refused by name before validation."""
    present = checkout(tmp_path / "sibling", f"https://{SIBLING}.git")
    manifest = listed(tmp_path / "checkouts", present)
    config_home = tmp_path / "xdg"
    (config_home / HOST_OVERLAY).parent.mkdir(parents=True)
    (config_home / HOST_OVERLAY).write_text("version: 2\ndefault:\n  pool: 3\n", encoding="utf-8")
    home = tmp_path / "home"

    result = apply_registry(
        manifest, home, write_rules(tmp_path / "rules.yml"), config_home=config_home
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "declares version 2, and the tracked one 1" in result.stderr, result.stderr
    assert f"{config_home / HOST_OVERLAY} cannot be overlaid on" in result.stderr, result.stderr
    assert not (home / "workspaces.yml").exists()
    assert not (home / "rules.yml").exists()


class Producers(NamedTuple):
    """A scratch registry holding one declaring checkout per producer this host installs."""

    home: Path
    result: subprocess.CompletedProcess[str]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] A handful of `git
# init`s and one registry apply, in the module every journey of this recipe lives in; the
# project edge those would all sit behind is the module docstring's follow-up.
# Block-scoped because the rule reads the fixture's body, not its decorator line.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] The edge this fixture
# would sit behind is the one the module docstring's `shell_test_tiers_stay_split` note
# defers: a project of this recipe's own is a change to `nx.json`,
# `orchestrator/project.json` and `tests/nx_inputs.py` for every module fixture and
# every journey here at once, and one more fixture of the same cost class in the same
# module is not the change that makes it.
@pytest.fixture(scope="module")
def producers(tmp_path_factory: pytest.TempPathFactory) -> Producers:
    """Every producer identity, declaring the target this host installs, plus this repository.

    Registered from checkouts whose base carries a scratch declaration, because what
    `onevcs release targets` answers for a `default_target` depends on the producer
    declaring that target: a producer whose declaration cannot be read is *refused*
    for naming one, not answered without it, so a registry of empty checkouts would
    prove nothing about the override. Each is routed by a synthetic rule of its own.
    """
    root = tmp_path_factory.mktemp("producers")
    paths = [
        declaring_checkout(
            root / "checkouts" / row.producer.rpartition("/")[2],
            f"https://{row.producer}.git",
            {row.target: row.artifact},
        )
        for row in INSTALLED
    ]
    paths.append(checkout(root / "checkouts" / "ai-orchestrator", f"https://{THIS_REPOSITORY}.git"))
    rules = write_rules(
        root / "rules.yml",
        {
            **{row.producer: CHANGE_AUTO for row in INSTALLED},
            THIS_REPOSITORY: LOCAL_DIRECT,
        },
    )
    home = root / "onevcs"
    result = apply_registry(listed(root / "checkouts.list", *paths), home, rules)
    assert result.returncode == 0, result.stdout + result.stderr
    return Producers(home=home, result=result)


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


class Probe(TypedDict):
    """A declared target's probe: the declaration's script, given the target's id."""

    script: str
    args: list[str]


class Target(TypedDict):
    name: str
    style: str
    probe: Probe


class ReleaseTargets(TypedDict):
    """The `onevcs release targets --json` answer, as far as these journeys read it.

    `default_target` is present only where the override resolves one, which is itself
    asserted on: a repository the override gives no default must answer without the key.
    """

    identity: str
    adoption: str
    default_target: NotRequired[str]
    targets: list[Target]


def release_targets(home: Path, identity: str) -> ReleaseTargets:
    """What `onevcs release targets --json` answers for ``identity`` under ``home``.

    Cast rather than validated, because the installed `onevcs`'s own answer is the
    subject: a response that lost one of these keys fails the journey as the drift it
    is, with the read naming the key.
    """
    asked = onevcs(home, "release", "targets", identity, "--json")
    assert asked.returncode == 0, asked.stdout + asked.stderr
    return cast(ReleaseTargets, json.loads(asked.stdout))


@pytest.mark.parametrize("row", INSTALLED, ids=lambda row: row.producer)
def test_each_producer_resolves_the_wheel_this_host_installs_as_its_default(
    producers: Producers, row: Installed
) -> None:
    """The override's answer per producer, read through the verb a dispatch resolves it with.

    Three things per row: the default target is the row's, it is a target the
    declaration carries under the row's artifact id, and the producer sits on the
    global rung — the override names no rung for a producer, so one resolving anything
    else has been given a rule this file does not state.
    """
    answer = release_targets(producers.home, row.producer)
    assert answer["default_target"] == row.target, answer
    assert answer["adoption"] == GLOBAL_RUNG, answer
    declared = {target["name"]: target["probe"]["args"][0] for target in answer["targets"]}
    assert declared[row.target] == row.artifact, declared


def test_this_repository_resolves_the_published_rung(producers: Producers) -> None:
    """A node of this repository waits for a producer's release, by the override alone.

    It declares nothing and names no default target — only the rung, because its
    `local-direct` policy refuses the draft a `fast` node behind a release would open.
    """
    answer = release_targets(producers.home, THIS_REPOSITORY)
    assert answer["adoption"] == THIS_REPOSITORY_RUNG, answer
    assert "default_target" not in answer, answer
    assert answer["targets"] == [], answer


def test_apply_reports_what_each_installed_producer_resolves(producers: Producers) -> None:
    """Below the policy table, one line per producer this host installs, as read back.

    An operator applying the configuration reads what a consumer will get from it, and
    the lines are read back through `onevcs release targets` rather than composed from
    the file, so a producer the override describes wrongly reads wrongly here.
    """
    stdout = producers.result.stdout
    table = stdout.index("apply-repo-registry: release adoption")
    assert table > stdout.index("apply-repo-registry: resolved policy")
    reported_lines = stdout[table:]
    for row in INSTALLED:
        line = next(line for line in reported_lines.splitlines() if row.producer in line)
        assert re.search(rf"default target {re.escape(row.target)}\s", line), line
        assert line.rstrip().endswith(f"adoption {GLOBAL_RUNG}"), line


def test_a_producer_this_registry_does_not_hold_is_reported_not_failed(
    applied: Applied,
) -> None:
    """A host holding a subset of the producers reads which ones it lacks.

    The `applied` registry holds none of them — `onetaskgraph` among them — so the table
    below the policy one names it as not registered rather than the apply failing for a
    producer this host lacks.
    """
    reported_lines = applied.result.stdout[applied.result.stdout.index("release adoption") :]
    assert re.search(
        r"github\.com/nickderobertis/onetaskgraph\s+not registered here", reported_lines
    )
    assert applied.result.returncode == 0


def test_a_registered_producer_whose_base_declares_nothing_fails_the_apply(
    tmp_path: Path,
) -> None:
    """A producer the override gives a default its own base never declares is refused.

    The installed `onevcs rules check` reports that override as a release finding a
    release verb refuses when used, and the recipe fails on it naming the producer, so
    a host learns of the wait that could never complete when it applies the
    configuration rather than when a node is held on it.
    """
    producer = "github.com/nickderobertis/onepipeline"
    present = checkout(tmp_path / "onepipeline", f"https://{producer}.git")
    subprocess.run(["git", "rm", "-q", "release-targets.toml"], cwd=present, check=True)
    subprocess.run(
        ["git", *COMMITTER, "commit", "-qm", "chore: declare nothing"], cwd=present, check=True
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=present, check=True
    )
    rules = write_rules(tmp_path / "rules.yml", {producer: CHANGE_AUTO})

    result = apply_registry(listed(tmp_path / "checkouts", present), tmp_path / "home", rules)

    assert result.returncode != 0, result.stdout + result.stderr
    assert re.search(
        r"release finding: .*github\.com/nickderobertis/onepipeline naming default_target "
        r'"npm", which it does not declare as a target',
        result.stdout + result.stderr,
    ), result.stdout + result.stderr
    assert "resolving the policy for onepipeline failed" in result.stderr, result.stderr


def test_a_scratch_identity_seeds_beside_the_override_untouched_by_it(tmp_path: Path) -> None:
    """`tests/e2e/scratch_identity.py` registers through the same recipe, and still does.

    Every journey that publishes into a seeded identity registers it this way, so the
    override installed beside its rules has to leave a path identity exactly as it was:
    no rule matches it, so it resolves the global rung, no default target, no targets.
    """
    identity = seeded(tmp_path)

    assert (identity.home / "releases.yml").read_bytes() == TRACKED_RELEASES.read_bytes()
    answer = release_targets(identity.home, str(identity.publication))
    assert answer["adoption"] == GLOBAL_RUNG, answer
    assert "default_target" not in answer, answer
    assert answer["targets"] == [], answer


def _one_sibling(tmp_path: Path) -> tuple[Path, Path]:
    """A checkout list naming one checkout of :data:`SIBLING`, and the rules routing it."""
    present = checkout(tmp_path / "sibling", f"https://{SIBLING}.git")
    return listed(tmp_path / "checkouts", present), write_rules(tmp_path / "rules.yml")


def test_a_different_release_override_is_installed_in_place_of_the_tracked_one(
    tmp_path: Path,
) -> None:
    """`--releases FILE` installs FILE, observed as the installed bytes becoming its own."""
    manifest, rules = _one_sibling(tmp_path)
    candidate = tmp_path / "candidate.yml"
    candidate.write_text(
        "version: 1\ndefault:\n  adoption: published\nrepositories: []\n", encoding="utf-8"
    )
    home = tmp_path / "home"

    result = apply_registry(manifest, home, rules, "--releases", str(candidate))

    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / "releases.yml").read_bytes() == candidate.read_bytes()
    assert (home / "releases.yml").read_bytes() != TRACKED_RELEASES.read_bytes()
    answer = release_targets(home, SIBLING)
    assert answer["adoption"] == "published", answer


#: A release override the adopted `onevcs` loads, standing in for whatever a host
#: already has installed when a replacement is refused or fails.
INSTALLED_RELEASES = "version: 1\ndefault:\n  adoption: fast\nrepositories: []\n"
#: A workspaces file the adopted `onevcs` loads, pooling nothing — the shipped default
#: written out — standing in for whatever a host already has installed.
INSTALLED_WORKSPACES = "version: 1\ndefault:\n  pool: 0\n  overflow: unlimited\nrules: []\n"


def test_a_different_workspaces_file_is_installed_in_place_of_the_tracked_one(
    tmp_path: Path,
) -> None:
    """`--workspaces FILE` installs FILE, observed through the pool the identity then resolves."""
    manifest, rules = _one_sibling(tmp_path)
    candidate = tmp_path / "candidate.yml"
    candidate.write_text(
        "version: 1\ndefault:\n  pool: 3\n  overflow: 1\nrules: []\n", encoding="utf-8"
    )
    home = tmp_path / "home"

    result = apply_registry(manifest, home, rules, "--workspaces", str(candidate))

    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / "workspaces.yml").read_bytes() == candidate.read_bytes()
    assert (home / "workspaces.yml").read_bytes() != TRACKED_WORKSPACES.read_bytes()
    status = onevcs(home, "pool", "status", "sibling", "--json")
    assert status.returncode == 0, status.stdout + status.stderr
    capacity = json.loads(status.stdout)["capacity"]
    assert (capacity["pool"], capacity["overflow"]) == (3, 1), capacity


def test_a_workspaces_file_onevcs_refuses_leaves_the_installed_one_intact(
    tmp_path: Path,
) -> None:
    """A candidate `onevcs` cannot load is refused by name, and replaces nothing.

    Validated in the scratch home the rules and the override already are, through
    `pool status` — the one read that loads the document whole — so a default that
    admits no session at all is refused before the live copy is touched.
    """
    manifest, rules = _one_sibling(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / "rules.yml").write_text(INSTALLED_RULES, encoding="utf-8")
    (home / "releases.yml").write_text(INSTALLED_RELEASES, encoding="utf-8")
    installed = home / "workspaces.yml"
    installed.write_text(INSTALLED_WORKSPACES, encoding="utf-8")
    invalid = tmp_path / "invalid.yml"
    invalid.write_text(
        "version: 1\ndefault:\n  pool: 0\n  overflow: 0\nrules: []\n", encoding="utf-8"
    )

    result = apply_registry(manifest, home, rules, "--workspaces", str(invalid))

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{invalid} is not a valid onevcs workspaces file" in result.stderr
    assert "admits no session at all" in result.stderr, result.stderr
    assert installed.read_text(encoding="utf-8") == INSTALLED_WORKSPACES
    assert "workspaces installed" not in result.stdout


def test_an_override_onevcs_refuses_leaves_the_installed_one_intact(tmp_path: Path) -> None:
    """A candidate `onevcs` cannot load is refused by name, and replaces nothing.

    Validated in the scratch home the rules already are, through `release targets` —
    the one verb that loads the document — so a malformed rung or a `default_target`
    naming no target is refused before the live copy is touched.
    """
    manifest, rules = _one_sibling(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / "rules.yml").write_text(INSTALLED_RULES, encoding="utf-8")
    installed = home / "releases.yml"
    installed.write_text(INSTALLED_RELEASES, encoding="utf-8")
    invalid = tmp_path / "invalid.yml"
    invalid.write_text(
        "version: 1\ndefault:\n  adoption: bogus\nrepositories: []\n", encoding="utf-8"
    )

    result = apply_registry(manifest, home, rules, "--releases", str(invalid))

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{invalid} is not a valid onevcs release override" in result.stderr
    assert "unknown variant `bogus`" in result.stderr, result.stderr
    assert installed.read_text(encoding="utf-8") == INSTALLED_RELEASES
    assert "releases   installed" not in result.stdout


def test_a_replacement_that_fails_after_validation_leaves_the_installed_copy_intact(
    tmp_path: Path,
) -> None:
    """The install is a rename of a staged copy, so a failure part-way truncates nothing.

    The destination is made unwritable after the candidates have been validated — the
    registry root itself, since every staged copy and its rename live in it — and what
    is asserted is each previous install byte for byte, not merely present. The rules
    are the first install, by `onevcs rules apply`, so that is where the run stops,
    naming it, with the release override behind it never touched.
    """
    manifest = listed(tmp_path / "checkouts")
    rules = write_rules(tmp_path / "rules.yml")
    home = tmp_path / "home"
    home.mkdir()
    installed_rules = home / "rules.yml"
    installed_rules.write_text(INSTALLED_RULES, encoding="utf-8")
    installed = home / "releases.yml"
    installed.write_text(INSTALLED_RELEASES, encoding="utf-8")
    home.chmod(0o555)
    try:
        result = apply_registry(manifest, home, rules)
    finally:
        home.chmod(0o755)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"installing the rules from {rules} failed" in result.stderr, result.stderr
    assert f"cannot write {home}" in result.stderr, result.stderr
    assert installed_rules.read_text(encoding="utf-8") == INSTALLED_RULES
    assert installed.read_text(encoding="utf-8") == INSTALLED_RELEASES
    assert "releases   installed" not in result.stdout
    assert sorted(path.name for path in home.iterdir()) == ["releases.yml", "rules.yml"]


def test_resolve_accepts_registered_spellings_and_refuses_bare_owner_name(
    applied: Applied,
) -> None:
    """Plans may name registry identities, aliases, origins, or checkout paths."""
    registry: RegistryDocument = json.loads(
        (applied.home / "registry.json").read_text(encoding="utf-8")
    )
    alias, checkout_record = next(
        (alias, record)
        for alias, record in registry["checkouts"].items()
        if record["identity"] == SIBLING
    )
    for spelling in (SIBLING, alias, f"https://{SIBLING}.git", checkout_record["path"]):
        resolved = onevcs(applied.home, "resolve", spelling)
        assert resolved.returncode == 0, resolved.stdout + resolved.stderr
        assert SIBLING in resolved.stdout

    owner_name = SIBLING.removeprefix("github.com/")
    refused = onevcs(applied.home, "resolve", owner_name)
    assert refused.returncode != 0, refused.stdout
    assert owner_name in refused.stderr


@pytest.mark.parametrize("identity", sorted(OVERLAY_POLICY))
def test_each_overlay_identity_resolves_the_overlays_rule_and_is_private(
    applied: Applied, identity: RepoIdentity
) -> None:
    """A private identity is routed by the host overlay alone, and the host is never asked.

    Read back through the installed `onevcs rules check`, so what is asserted is the
    composed file the recipe installed: the overlay's rule decides, at its own position
    ahead of the base rules, with the publication and approvals it declares — a
    local-first private repository included — and `onevcs boundary inspect` answers
    `private` off the overlay's `visibility` rather than off a host it would have to ask.
    """
    publication, approvals, position = OVERLAY_POLICY[identity]
    checked = onevcs(applied.home, "rules", "check", identity)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert reported(checked.stdout, "publication") == publication, checked.stdout
    assert reported(checked.stdout, "approvals") == approvals, checked.stdout
    host, owner, name = identity.split("/")
    expected = f"rule {position} {{host: {host}, owner: {owner}, name: {name}}}"
    assert reported(checked.stdout, "matched") == expected, checked.stdout
    assert reported(checked.stdout, "rules") == str(applied.home / "rules.yml"), checked.stdout
    assert visibility(applied.home, identity) == "private"


def test_the_overlay_leaves_each_base_rule_as_the_base_states_it(applied: Applied) -> None:
    """Composition adds the host's rules and moves neither base policy kind.

    One identity of each policy the base states — a local one and a change-request one —
    keeps that policy, matched from its own base rule shifted by exactly the overlay's
    rules.
    """
    for index, (identity, policy) in enumerate(BASE_POLICY.items()):
        checked = onevcs(applied.home, "rules", "check", identity)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        resolved = Policy(
            reported(checked.stdout, "publication"), reported(checked.stdout, "approvals")
        )
        assert resolved == policy, checked.stdout
        position = index + 1 + len(OVERLAY_POLICY)
        assert reported(checked.stdout, "matched").startswith(f"rule {position} "), checked.stdout


def test_the_optional_base_merge_and_wait_keys_are_accepted_and_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rule's optional keys work through the CLI, and malformed policy is refused.

    Each candidate goes through the real recipe as its base rules file, because the
    verb it installs with is the one that validates it: a malformed `base_merge` is
    refused by name and leaves the configured install resolving exactly as before.
    """
    monkeypatch.delenv("ONEVCS_CHECKS_TIMEOUT_SECONDS", raising=False)
    manifest, _ = _one_sibling(tmp_path)
    home = tmp_path / "onevcs"
    configured = tmp_path / "configured.yml"
    configured.write_text(
        rules_naming(BASE_POLICY).replace(
            "    publication: change-auto\n",
            "    publication: change-auto\n"
            "    base_merge: always\n"
            "    checks: {wait_seconds: 7200}\n",
        ),
        encoding="utf-8",
    )
    applied_once = apply_registry(manifest, home, configured)
    assert applied_once.returncode == 0, applied_once.stdout + applied_once.stderr
    resolved = onevcs(home, "rules", "check", SIBLING)
    assert resolved.returncode == 0, resolved.stderr
    assert reported(resolved.stdout, "base merge") == "always"
    assert reported(resolved.stdout, "checks wait") == "7200 s"
    installed = (home / "rules.yml").read_bytes()
    invalid = tmp_path / "invalid.yml"
    invalid.write_text(
        configured.read_text(encoding="utf-8").replace("base_merge: always", "base_merge: invalid"),
        encoding="utf-8",
    )
    refused = apply_registry(manifest, home, invalid)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "invalid" in refused.stderr
    assert f"{invalid} is not a valid onevcs rules composition" in refused.stderr
    assert (home / "rules.yml").read_bytes() == installed
    recovered = onevcs(home, "rules", "check", SIBLING)
    assert recovered.returncode == 0, recovered.stderr
    assert reported(recovered.stdout, "checks wait") == "7200 s"


def test_a_gate_at_the_migrated_schema_version_is_refused_by_name(tmp_path: Path) -> None:
    """At `version: 3` a rule is `{publication, approvals}`, and `gate:` an unknown field.

    So a rules file carrying one does not load. Driven through the real recipe because
    that is where an operator meets it: an apply that installed a file the engine cannot
    read would leave every publication resolving the previous rules.
    """
    manifest, _ = _one_sibling(tmp_path)
    gated = tmp_path / "gated.yml"
    gated.write_text(
        f"version: {RULES_SCHEMA_VERSION}\n"
        "rules:\n"
        "  - match: {host: github.com, owner: scratchowner, name: sibling}\n"
        "    publication: change-auto\n"
        "    approvals: none\n"
        '    gate: {command: ["just", "gate"]}\n'
        "default:\n"
        "  publication: change-open\n"
        "  approvals: required\n",
        encoding="utf-8",
    )

    result = apply_registry(manifest, tmp_path / "home", gated)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{gated} is not a valid onevcs rules composition" in result.stderr
    assert "gate" in result.stderr, result.stderr
    assert not (tmp_path / "home" / "rules.yml").exists()
    assert registered_paths(tmp_path / "home") == set()


# llmlint: ignore[tests_mirror_real_usage] An unmigrated host's rules must be seeded directly.
def test_a_gate_at_an_older_schema_version_is_accepted_ignored_and_reported(
    tmp_path: Path,
) -> None:
    """An unmigrated host keeps publishing, and is told why once.

    The same `gate: ["false"]` that an `onevcs` before 0.11.0 would have run and been
    refused by is now read, dropped, and named on stderr — so the *engine* is what
    stopped running a tier of its own.

    The unmigrated file is the one a host already has installed, which is where the
    engine meets it: written at the registry's own rules reference after a real
    registration. Applying it afresh is a different question, and `onevcs rules apply`
    answers it by refusing the gate by name and installing nothing, so the recipe
    never installs a gate even at a schema the engine still reads.
    """
    manifest, rules = _one_sibling(tmp_path)
    legacy = tmp_path / "legacy.yml"
    legacy.write_text(
        "version: 2\n"
        "rules:\n"
        "  - match: {host: github.com, owner: scratchowner, name: sibling}\n"
        "    publication: change-auto\n"
        "    approvals: none\n"
        '    gate: {command: ["false"]}\n'
        "default:\n"
        "  publication: change-open\n"
        "  approvals: required\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    result = apply_registry(manifest, home, rules)
    assert result.returncode == 0, result.stdout + result.stderr

    refused = apply_registry(manifest, home, legacy)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "gate" in refused.stderr, refused.stderr
    assert f"{legacy} is not a valid onevcs rules composition" in refused.stderr
    assert (home / "rules.yml").read_text(encoding="utf-8") != legacy.read_text(encoding="utf-8")

    # An unmigrated host: the installed file is the older schema, gate and all.
    shutil.copyfile(legacy, home / "rules.yml")
    checked = onevcs(home, "rules", "check", SIBLING)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "gate" not in {
        line.partition(": ")[0].strip() for line in checked.stdout.splitlines() if ": " in line
    }, checked.stdout
    assert reported(checked.stdout, "publication") == "change-auto"
    assert "version 3 removed" in checked.stderr, checked.stderr
    assert str(home / "rules.yml") in checked.stderr, checked.stderr


#: The fields `onevcs` stamps on an identity's record when it observes the identity's
#: visibility — off a rule's `visibility`, which every rule here declares.
OBSERVATION_FIELDS = ("visibility", "observation", "observed_at")


def registry_without(document: bytes, fields: tuple[str, ...]) -> dict[str, object]:
    """The registry ``document`` with ``fields`` dropped from every identity's record."""
    registry = json.loads(document)
    for record in registry["identities"].values():
        for field in fields:
            record.pop(field, None)
    return cast(dict[str, object], registry)


def test_reapplying_the_configuration_changes_nothing(applied: Applied) -> None:
    """Idempotence, which is what makes this a recipe to re-run rather than a migration.

    Every identity and checkout the first run registered is registered again exactly as
    it was. The one movement is `onevcs`'s own: a re-registration of an identity whose
    rule declares its `visibility` records that observation, stamped with when it was
    made, and re-stamps only its time on every run after; everything else is byte for
    byte.
    """
    document = applied.home / "registry.json"
    before = document.read_bytes()
    override = applied.home / "releases.yml"
    override_before = override.read_bytes()
    override_installed_at = override.stat().st_mtime_ns
    rules_before = (applied.home / "rules.yml").read_bytes()
    config_home = applied.manifest.parent / "xdg"
    again = apply_registry(applied.manifest, applied.home, applied.rules, config_home=config_home)
    assert again.returncode == 0, again.stdout + again.stderr
    second = document.read_bytes()
    assert registry_without(second, OBSERVATION_FIELDS) == registry_without(
        before, OBSERVATION_FIELDS
    )
    observed = json.loads(second)["identities"]
    expected = {
        **dict.fromkeys(BASE_POLICY, "public"),
        **dict.fromkeys(OVERLAY_POLICY, "private"),
    }
    assert {key: record.get("visibility") for key, record in observed.items()} == expected
    third = apply_registry(applied.manifest, applied.home, applied.rules, config_home=config_home)
    assert third.returncode == 0, third.stdout + third.stderr
    assert registry_without(document.read_bytes(), ("observed_at",)) == registry_without(
        second, ("observed_at",)
    )
    # The rules are re-composed and re-installed by `onevcs rules apply` on every run,
    # so what stays the same is the installed document, not a line saying so.
    installed_rules = applied.home / "rules.yml"
    assert (
        f"rules      the rules composed from 2 file(s) are at {installed_rules}" in again.stdout
    ), again.stdout
    assert installed_rules.read_bytes() == rules_before
    assert "releases   unchanged" in again.stdout
    assert "workspaces unchanged" in again.stdout
    assert override.read_bytes() == override_before
    assert override.stat().st_mtime_ns == override_installed_at, "the override was rewritten"
    assert (applied.home / "workspaces.yml").read_bytes() == TRACKED_WORKSPACES.read_bytes()


def test_dry_run_parses_comments_whitespace_and_tilde_without_writing(tmp_path: Path) -> None:
    registry_home = tmp_path / "registry"
    # The sandboxed `HOME` the recipe expands `~` against, as `sandbox` names it.
    user_home = tmp_path / "registry-user"
    present = checkout(user_home / "checkout", f"https://{SIBLING}.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text("  # comment\n  ~/checkout   # local clone\n", encoding="utf-8")

    result = apply_registry(
        manifest, registry_home, write_rules(tmp_path / "rules.yml"), "--dry-run"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"would register  {present}" in result.stdout
    assert "rules      would install" in result.stdout
    assert "releases   would install" in result.stdout
    assert "workspaces would install" in result.stdout
    assert "dry run — 1 checkout(s) would be registered, 0 skipped" in result.stdout
    assert not registry_home.exists()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--checkouts",), "usage: apply-repo-registry.sh"),
        (("--rules",), "usage: apply-repo-registry.sh"),
        (("--releases",), "usage: apply-repo-registry.sh"),
        (("--workspaces",), "usage: apply-repo-registry.sh"),
        (("--workspaces", "/does/not/exist"), "does not exist"),
        (("--unknown",), "unknown argument"),
        (("--checkouts", "/does/not/exist"), "does not exist"),
        (("--rules", "/does/not/exist"), "does not exist"),
        (("--releases", "/does/not/exist"), "does not exist"),
    ],
)
def test_invalid_cli_input_is_rejected(
    tmp_path: Path, arguments: tuple[str, ...], message: str
) -> None:
    manifest = listed(tmp_path / "checkouts")
    result = apply_registry(
        manifest, tmp_path / "home", write_rules(tmp_path / "rules.yml"), *arguments
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert message in result.stderr


def test_help_is_successful(tmp_path: Path) -> None:
    manifest = listed(tmp_path / "checkouts")
    result = apply_registry(
        manifest, tmp_path / "home", write_rules(tmp_path / "rules.yml"), "--help"
    )
    assert result.returncode == 0
    assert "usage: apply-repo-registry.sh" in result.stderr


# llmlint: ignore[tests_mirror_real_usage] Corrupt JSON must be seeded directly.
def test_malformed_existing_registry_is_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "registry.json").write_text("not json", encoding="utf-8")

    result = apply_registry(listed(tmp_path / "checkouts"), home, write_rules(tmp_path / "r.yml"))

    assert result.returncode == 1
    assert f"the registry at {home / 'registry.json'}" in result.stderr, result.stderr
    assert (home / "registry.json").read_text(encoding="utf-8") == "not json"
    assert not (home / "rules.yml").exists()


#: A rules file the adopted `onevcs` loads, standing in for whatever a host already has
#: installed when an invalid replacement is refused.
INSTALLED_RULES = (
    "version: 3\ntrailer_prefix: Orchestrator-\nrules: []\n"
    "default:\n  publication: change-open\n  approvals: required\n"
)


def test_invalid_rules_do_not_replace_the_installed_rules(tmp_path: Path) -> None:
    """A refused rules file leaves the one already installed exactly as it was.

    The installed stand-in has to be a rules file the pinned `onevcs` itself accepts,
    because registration reads it before the supplied file is ever validated: onevcs
    0.20.0 resolves an identity's publication policy from the rules at `register`, and
    a file with no `default:` is refused there as malformed — so a stand-in written as
    the bare `version: 2` / `rules: []` of earlier releases fails the recipe one step
    before the refusal this journey is about, and proves nothing about replacement.
    """
    manifest, _ = _one_sibling(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    installed = home / "rules.yml"
    # A file `onevcs` can load, because the adopted release's `register` resolves a
    # checkout's policy from the installed rules on the way in — an installed file it
    # cannot read fails the registration before the replacement is ever validated.
    installed.write_text(INSTALLED_RULES, encoding="utf-8")
    invalid = tmp_path / "invalid.yml"
    invalid.write_text("not: [valid", encoding="utf-8")

    result = apply_registry(manifest, home, invalid)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{invalid} is not a valid onevcs rules composition" in result.stderr
    assert installed.read_text(encoding="utf-8") == INSTALLED_RULES
    assert registered_paths(home) == set()


def test_an_invalid_host_rules_overlay_installs_nothing_and_registers_nothing(
    tmp_path: Path,
) -> None:
    """A host overlay the verb refuses is refused by name before anything moves.

    The overlay is composed and validated with the base by `onevcs rules apply
    --dry-run` before the first registration, so an overlay that cannot be read — or
    one naming a field its schema does not admit — leaves the installed rules exactly as
    they were and the registry holding no checkout at all. The host's workspaces overlay
    beside it has been composed into a scratch file by then, and the refusal leaves
    nothing in the temporary directory it was handed.
    """
    manifest, rules = _one_sibling(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    installed = home / "rules.yml"
    installed.write_text(INSTALLED_RULES, encoding="utf-8")
    for overlay in (
        "not: [valid",
        # `visibility` is a version-4 field, so a version-3 overlay naming it is refused.
        HOST_RULES_OVERLAY_TEXT.replace("version: 4", "version: 3"),
    ):
        config_home = host_config(tmp_path / "xdg", overlay)
        (config_home / HOST_OVERLAY).write_text(HOST_OVERLAY_TEXT, encoding="utf-8")
        temporary = tmp_path / "temporary"
        temporary.mkdir(exist_ok=True)

        result = apply_registry(manifest, home, rules, config_home=config_home, temporary=temporary)

        assert result.returncode == 1, result.stdout + result.stderr
        assert (
            f"{rules} overlaid by {config_home / HOST_RULES} is not a valid onevcs "
            "rules composition"
        ) in result.stderr, result.stderr
        assert installed.read_text(encoding="utf-8") == INSTALLED_RULES
        assert registered_paths(home) == set()
        assert "rules      " not in result.stdout
        # uv's environment locks are left by every `uv run`, and the push gate's leftover
        # budget does not count them either.
        left = sorted(p.name for p in temporary.iterdir() if not UV_LOCK.fullmatch(p.name))
        assert left == [], f"a refusal left scratch behind: {left}"


def test_invalid_rules_are_rejected_with_no_present_checkout(tmp_path: Path) -> None:
    manifest = listed(tmp_path / "checkouts", tmp_path / "not-cloned")
    invalid = tmp_path / "invalid.yml"
    invalid.write_text("not: [valid", encoding="utf-8")

    result = apply_registry(manifest, tmp_path / "home", invalid)

    assert result.returncode == 1
    assert f"{invalid} is not a valid onevcs rules composition" in result.stderr
    assert not (tmp_path / "home" / "rules.yml").exists()


def _recipe_with_onevcs_home(tmp_path: Path, onevcs_home: str) -> subprocess.CompletedProcess[str]:
    """The real recipe on scratch inputs, under a sandbox whose `ONEVCS_HOME` is ``onevcs_home``."""
    manifest = listed(tmp_path / "checkouts")
    rules = write_rules(tmp_path / "rules.yml")
    environment = {**sandbox(tmp_path / "home", tmp_path / "xdg"), "ONEVCS_HOME": onevcs_home}
    return subprocess.run(
        ["just", "repos-apply", "--checkouts", str(manifest), "--rules", str(rules)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
    )


def test_empty_onevcs_home_is_rejected(tmp_path: Path) -> None:
    result = _recipe_with_onevcs_home(tmp_path, "")
    assert result.returncode == 2
    assert "ONEVCS_HOME is set but empty" in result.stderr


@pytest.mark.parametrize("onevcs_home", ["relative/registry", "/"])
def test_unsafe_onevcs_home_is_rejected(tmp_path: Path, onevcs_home: str) -> None:
    result = _recipe_with_onevcs_home(tmp_path, onevcs_home)
    assert result.returncode == 2
    assert "must name an absolute directory other than /" in result.stderr


# llmlint: ignore[tests_mirror_real_usage] A registry's rules reference must be seeded directly.
def test_rules_are_installed_at_the_rules_file_the_registry_references(tmp_path: Path) -> None:
    """`onevcs rules apply` installs where the registry looks for rules, wherever that is.

    A registry whose `rules` names another file than `rules.yml` beside it is honoured
    rather than refused: the composed rules land at that reference, the report names
    it, and `onevcs rules check` reads the policy out of it — so the recipe can never
    install a file the registry then ignores.
    """
    manifest, rules = _one_sibling(tmp_path)
    home = tmp_path / "home"
    first = apply_registry(manifest, home, rules)
    assert first.returncode == 0, first.stdout + first.stderr
    document = home / "registry.json"
    registry = json.loads(document.read_text(encoding="utf-8"))
    # The file it references already exists, as an earlier install there would have left
    # it: registration reads the referenced rules before this run installs any.
    elsewhere = tmp_path / "elsewhere.yml"
    elsewhere.write_text(INSTALLED_RULES, encoding="utf-8")
    registry["rules"] = str(elsewhere)
    document.write_text(json.dumps(registry), encoding="utf-8")
    beside = (home / "rules.yml").read_bytes()

    result = apply_registry(manifest, home, rules)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"rules      the rules composed from 1 file(s) are at {elsewhere}" in result.stdout
    assert "trailer_prefix: Orchestrator-" in elsewhere.read_text(encoding="utf-8")
    assert (home / "rules.yml").read_bytes() == beside
    checked = onevcs(home, "rules", "check", SIBLING)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert reported(checked.stdout, "rules") == str(elsewhere), checked.stdout
    assert reported(checked.stdout, "publication") == "change-auto", checked.stdout


def test_checkout_registration_failure_is_reported(tmp_path: Path) -> None:
    not_a_checkout = tmp_path / "not-a-checkout"
    not_a_checkout.mkdir()

    result = apply_registry(
        listed(tmp_path / "checkouts", not_a_checkout),
        tmp_path / "home",
        write_rules(tmp_path / "rules.yml"),
    )

    assert result.returncode == 1
    assert "registering" in result.stderr
    assert "failed" in result.stderr


def test_work_preserved_under_the_configured_trailer_prefix_is_recognized(tmp_path: Path) -> None:
    """The provenance prefix is part of the rules' data, and stranding it is silent.

    `onevcs` spells its trailer keys `Onevcs-` when nothing configures one, and a branch
    preserved under another prefix is neither read nor ignored: it is listed as
    unrecoverable and refused publication. So the `trailer_prefix` a rules file declares
    has to reach the installed rules, and a marker under it read as the provenance it is.
    """
    preserved = checkout(tmp_path / "preserved", f"https://{SIBLING}.git")

    def commit(message: str) -> None:
        subprocess.run(["git", "add", "-A"], cwd=preserved, check=True)
        subprocess.run(["git", *COMMITTER, "commit", "-qm", message], cwd=preserved, check=True)

    (preserved / "file").write_text("base\n", encoding="utf-8")
    commit("chore: base")
    subprocess.run(["git", "checkout", "-qb", "preserved/work"], cwd=preserved, check=True)
    (preserved / "file").write_text("base\nwork\n", encoding="utf-8")
    commit("chore: orchestrated change (incomplete step)\n\nOrchestrator-Status: incomplete")
    subprocess.run(["git", "checkout", "-q", "main"], cwd=preserved, check=True)

    home = tmp_path / "home"
    applied_once = apply_registry(
        listed(tmp_path / "checkouts", preserved), home, write_rules(tmp_path / "rules.yml")
    )
    assert applied_once.returncode == 0, applied_once.stdout + applied_once.stderr

    found = onevcs(home, "recoverable")
    assert "preserved/work" in found.stdout
    assert "incomplete step (provenance marker)" in found.stdout
    assert "not configured to read" not in found.stdout, found.stdout
    checked = onevcs(home, "rules", "check", SIBLING)
    assert reported(checked.stdout, "trailer_prefix") == "Orchestrator-"


def test_a_publication_checkout_and_its_safety_clone_register_as_one_identity(
    tmp_path: Path,
) -> None:
    """Two checkouts of one repository, through the real recipe, under one policy.

    That the pair collapses to one identity is `onevcs`'s doing rather than the list's —
    it resolves an identity from a checkout's own `origin`, not from its path — so it is
    driven rather than read: neither checkout can end up a notch wider than the other.
    """
    origin = f"https://{SIBLING}.git"
    publication = checkout(tmp_path / "checkouts" / "scratchowner__sibling", origin)
    execution = checkout(tmp_path / "checkouts" / "scratchowner__sibling-isolated", origin)
    home = tmp_path / "home"

    result = apply_registry(
        listed(tmp_path / "checkouts.list", publication, execution),
        home,
        write_rules(tmp_path / "rules.yml"),
    )
    assert result.returncode == 0, result.stdout + result.stderr

    registry: RegistryDocument = json.loads((home / "registry.json").read_text(encoding="utf-8"))
    assert set(registry["identities"]) == {SIBLING}
    assert {record["path"] for record in registry["checkouts"].values()} == {
        str(publication),
        str(execution),
    }

    # Either alias `onevcs` derived from a directory name is a spelling a plan node may
    # name, and both have to land on one identity and one policy: a safety clone resolving
    # a notch wider is how work executed in one checkout comes to publish differently from
    # work executed in the other.
    for alias in ("scratchowner__sibling", "scratchowner__sibling-isolated"):
        resolved = onevcs(home, "resolve", alias)
        assert resolved.returncode == 0, resolved.stdout + resolved.stderr
        assert SIBLING in resolved.stdout

        checked = onevcs(home, "rules", "check", alias)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert reported(checked.stdout, "publication") == "change-auto"
        assert reported(checked.stdout, "approvals") == "none"
        assert reported(checked.stdout, "matched").startswith("rule ")


def test_a_host_overlay_routes_a_private_team_identity_through_its_own_rule(
    tmp_path: Path,
) -> None:
    """A private team identity, and the overlay rule it reaches.

    A team identity is where reading the resolved policy alone proves the least. Its
    rule and `default:` resolve the same `change-open` / `approvals: required` pair, so
    a policy read answers identically whether the rule matched or the checkout fell
    through — and a checkout that falls through is one `just repos-apply` refuses rather
    than registers. So what is asserted here is the rule that decided, by its position in
    the installed composition. Without the overlay the same registration falls through
    and is refused, which is the proof that the overlay's rule, and nothing in the base,
    routes it.
    """
    path = checkout(tmp_path / "checkouts" / "quietharbor", f"https://{QUIETHARBOR}.git")
    manifest = listed(tmp_path / "checkouts.list", path)
    rules = write_rules(tmp_path / "rules.yml")

    unruled = apply_registry(manifest, tmp_path / "bare", rules)
    assert unruled.returncode == 1, unruled.stdout + unruled.stderr
    assert "quietharbor: publication, approvals from the default" in unruled.stderr, unruled.stderr

    home = tmp_path / "onevcs"
    config_home = host_config(tmp_path / "xdg")
    applied_once = apply_registry(manifest, home, rules, config_home=config_home)
    assert applied_once.returncode == 0, applied_once.stdout + applied_once.stderr
    assert f"(from {rules} overlaid by {config_home / HOST_RULES})" in applied_once.stdout

    registry: RegistryDocument = json.loads((home / "registry.json").read_text(encoding="utf-8"))
    assert set(registry["identities"]) == {QUIETHARBOR}

    host, owner, name = QUIETHARBOR.split("/")
    position = OVERLAY_POLICY[QUIETHARBOR].position
    expected_rule = f"rule {position} {{host: {host}, owner: {owner}, name: {name}}}"
    for argument in (QUIETHARBOR, "quietharbor"):
        checked = onevcs(home, "rules", "check", argument)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert reported(checked.stdout, "publication") == "change-open", checked.stdout
        assert reported(checked.stdout, "approvals") == "required", checked.stdout
        assert reported(checked.stdout, "matched") == expected_rule, checked.stdout
        assert reported(checked.stdout, "green draft") == "keep for review", checked.stdout
    assert visibility(home, QUIETHARBOR) == "private"


def test_a_checkout_this_host_does_not_have_is_skipped(tmp_path: Path) -> None:
    """A second machine holds a subset of a list's checkouts, and still registers it."""
    present = checkout(tmp_path / "sibling", f"https://{SIBLING}.git")
    manifest = listed(tmp_path / "checkouts", present, tmp_path / "never-cloned")

    result = apply_registry(manifest, tmp_path / "home", write_rules(tmp_path / "rules.yml"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skip       not on this host" in result.stdout
    assert "1 checkout(s) registered, 1 skipped" in result.stdout


def test_a_repository_neither_a_rule_nor_a_declaration_names_fails_the_apply(
    tmp_path: Path,
) -> None:
    """Falling through to the reviewed default is safe, and is nobody's configured policy.

    Reporting it as a pass is how a mistyped owner or a rule nobody wrote reaches a
    publication as a change request against a repository that wanted a local merge.
    """
    unlisted = checkout(tmp_path / "unlisted", "https://github.com/someone/unlisted.git")
    manifest = listed(tmp_path / "checkouts", unlisted)

    result = apply_registry(manifest, tmp_path / "home", write_rules(tmp_path / "rules.yml"))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "unlisted: publication, approvals from the default" in result.stderr, result.stderr
    assert "nor the repository's own onevcs.toml" in result.stderr, result.stderr


#: A synthetic identity no rule names and whose checkout declares its own policy.
DECLARED: RepoIdentity = "github.com/scratchowner/declared"
#: The policy that checkout's `onevcs.toml` declares, at the contract's schema version.
DECLARED_POLICY = Policy("change-auto", "none")
DECLARATION = 'schema_version = 2\n\n[policy]\npublication = "change-auto"\napprovals = "none"\n'


def declared_checkout(directory: Path, identity: RepoIdentity, document: str) -> Path:
    """A checkout of ``identity`` whose base carries ``document`` as its `onevcs.toml`."""
    directory = checkout(directory, f"https://{identity}.git")
    (directory / "onevcs.toml").write_text(document, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=directory, check=True)
    subprocess.run(
        ["git", *COMMITTER, "commit", "-qm", "chore: declare the policy"], cwd=directory, check=True
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=directory, check=True
    )
    return directory


def overlay_rule(identity: RepoIdentity, **fields: str) -> str:
    """A host rules overlay holding one rule for ``identity`` that sets only ``fields``."""
    host, owner, name = identity.split("/")
    body = "".join(f"    {key}: {value}\n" for key, value in fields.items())
    return (
        "version: 4\n"
        "rules:\n"
        f"  - match: {{host: {host}, owner: {owner}, name: {name}}}\n"
        f"{body}"
        "    visibility: public\n"
    )


def apply_declared(
    tmp_path: Path, document: str, overlay: str | None = None
) -> tuple[Path, subprocess.CompletedProcess[str]]:
    """The real recipe over one checkout declaring ``document``, under no base rule.

    The base handed to `--rules` is a rules file with no rule and the reviewed default,
    as `config/onevcs.rules.yml` is; ``overlay``, when named, is the host's rules overlay.
    """
    path = declared_checkout(tmp_path / "checkouts" / "declared", DECLARED, document)
    home = tmp_path / "onevcs"
    config_home = tmp_path / "xdg"
    if overlay is not None:
        host_config(config_home, overlay)
    result = apply_registry(
        listed(tmp_path / "checkouts.list", path),
        home,
        write_rules(tmp_path / "rules.yml", {}),
        config_home=config_home,
    )
    return home, result


def test_a_checkout_declaring_its_policy_is_applied_with_no_rule(tmp_path: Path) -> None:
    """Its `onevcs.toml` decides both fields, and the table says so for each."""
    home, result = apply_declared(tmp_path, DECLARATION)

    assert result.returncode == 0, result.stdout + result.stderr
    row = table_row(result.stdout, DECLARED)
    assert row == Row("change-auto", "declaration", "none", "declaration"), row
    assert "every one decided by a rule or its declaration" in result.stdout
    checked = onevcs(home, "rules", "check", DECLARED)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    declared_at = tmp_path / "checkouts" / "declared" / "onevcs.toml"
    assert reported(checked.stdout, "declaration") == f"declared in {declared_at}"
    assert f"publication: change-auto (from the repository declaration {declared_at})" in (
        checked.stdout
    )


def test_a_host_overlay_rule_overrides_the_declaration_for_its_field_alone(
    tmp_path: Path,
) -> None:
    """A rule setting `publication` wins for it; `approvals` is still the declaration's."""
    home, result = apply_declared(
        tmp_path, DECLARATION, overlay_rule(DECLARED, publication="change-open")
    )

    assert result.returncode == 0, result.stdout + result.stderr
    row = table_row(result.stdout, DECLARED)
    assert row == Row("change-open", "rule 1", "none", "declaration"), row
    checked = onevcs(home, "rules", "check", DECLARED)
    assert "publication: change-open (from rule 1)" in checked.stdout, checked.stdout
    assert reported(checked.stdout, "approvals") == "none", checked.stdout


@pytest.mark.parametrize(
    ("declared", "ruled", "expected"),
    [
        (
            {"publication": "change-auto"},
            {"approvals": "none"},
            Row("change-auto", "declaration", "none", "rule 1"),
        ),
        (
            {"approvals": "none"},
            {"publication": "change-open"},
            Row("change-open", "rule 1", "none", "declaration"),
        ),
    ],
    ids=["declared-publication-ruled-approvals", "declared-approvals-ruled-publication"],
)
def test_a_declaration_and_a_rule_may_each_supply_one_field(
    tmp_path: Path, declared: dict[str, str], ruled: dict[str, str], expected: Row
) -> None:
    """Neither falls to the default, so the checkout is applied with each field's source."""
    document = "schema_version = 2\n\n[policy]\n" + "".join(
        f'{key} = "{value}"\n' for key, value in declared.items()
    )
    _, result = apply_declared(tmp_path, document, overlay_rule(DECLARED, **ruled))

    assert result.returncode == 0, result.stdout + result.stderr
    assert table_row(result.stdout, DECLARED) == expected


@pytest.mark.parametrize(
    ("document", "fallen"),
    [
        ("schema_version = 2\n\n[policy]\n", "publication, approvals"),
        ("schema_version = 2\n\n[policy.checks]\nwait_seconds = 3900\n", "publication, approvals"),
        ('schema_version = 2\n\n[policy]\npublication = "change-auto"\n', "approvals"),
    ],
    ids=["empty-policy", "checks-only", "publication-only"],
)
def test_a_declaration_leaving_a_field_to_the_default_fails_the_apply(
    tmp_path: Path, document: str, fallen: str
) -> None:
    """A declared document is not coverage: each field's own source is what is read.

    `onevcs rules check` reads such a checkout's `declaration:` as declared, and the
    field it leaves unset still resolves from the reviewed default.
    """
    home, result = apply_declared(tmp_path, document)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"declared: {fallen} from the default" in result.stderr, result.stderr
    checked = onevcs(home, "rules", "check", DECLARED)
    assert reported(checked.stdout, "declaration").startswith("declared in "), checked.stdout


def test_an_unreadable_declaration_fails_the_apply_naming_the_document(tmp_path: Path) -> None:
    document = 'schema_version = 2\n\n[policy]\npublication = "sometimes"\n'
    _, result = apply_declared(tmp_path, document)

    assert result.returncode == 1, result.stdout + result.stderr
    declared_at = tmp_path / "checkouts" / "declared" / "onevcs.toml"
    assert str(declared_at) in result.stderr, result.stderr
    assert f"registering {declared_at.parent} failed" in result.stderr, result.stderr
