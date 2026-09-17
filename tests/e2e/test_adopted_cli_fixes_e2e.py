"""The CLIs this host adopted carry the fixes its plans landed in their producers.

`config/onepipeline.version`, `config/onevcs.version`, `config/onejudge.version` and
`config/onetaskgraph.version` moved onto the releases carrying those fixes, and a pin is
one number: it says which release is installed and nothing about what that release does.
So each fix is driven here through the binary this checkout installed, the way a manager
or a recipe reaches it, and each journey fails on the release before the fix:

* `onepipeline monitor` ends every pass with a `-- cursor` line, and `--cursor` resumes
  from it — https://github.com/nickderobertis/onepipeline/pull/274, first cut as 0.29.2;
* `onevcs recoverable --repo` scopes the listing to one identity —
  https://github.com/nickderobertis/onevcs/pull/145, first cut as 0.23.0;
* `onevcs release acknowledge` accepts an automated target whose landing captured no
  baseline — https://github.com/nickderobertis/onevcs/pull/143, first cut as 0.22.0;
* the `github-projects` source refuses a `status_mapping.unknown` naming a closed state —
  https://github.com/nickderobertis/onetaskgraph/pull/915, first cut as 0.2.29;
* `onejudge run` names each `user.artifacts` path in its judge side's prompt —
  https://github.com/nickderobertis/onejudge/pull/86, first cut as 0.11.0;
* a `local-md` source's relative root resolves against the directory of the
  configuration document that supplied it, rather than being left for each reading
  process to resolve against its own working directory —
  https://github.com/nickderobertis/onetaskgraph/issues/1144;
* a `local-md` listing that meets a malformed record refuses, naming the record's path
  and its parse diagnostic, instead of listing everything else as if the record were
  not there — https://github.com/nickderobertis/onetaskgraph/issues/1313, first cut as
  0.2.35.

The status-word half of that same plan-store adoption — the canonical `in-progress`
reading back as its own category rather than as `unknown`
(https://github.com/nickderobertis/onetaskgraph/issues/1140) — is driven where this
repository depends on it, over a real follow-up ticket, in
`tests/test_follow_up_tickets.py`.

The onetaskgraph refusal is read through `project list` rather than `config show`: the
released `config show` renders settings without constructing a source, so it accepts the
mapping on both releases, while building the source refuses it before any request.

What is doubled is the boundary each engine is proven at in its own repository and
nothing above it: the paid codex provider, answered by `fake_codex.py` at the path
oneharness spawns it from; the worker side, answered by `judge_protocol_double.py`; and
the GitHub endpoint, which is unreachable on purpose because the refusal happens before
the first request. Every `onevcs` contact runs under a scratch `ONEVCS_HOME`, so this
host's registry is never read or migrated.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import NotRequired, TypedDict

import pytest
from onejudge_sdk import OneJudge, RunConfig
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] The project edge this
# rule wants is the one the block below answers: this tier is deliberately uncached, so
# `nx affected` skipping it on a project edge is exactly the memo it must not have.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] The subject is the set of
# binaries this checkout installed, which live outside the workspace and so outside every
# `nx.json` key: the uncached `orchestrator:test-checkouts` tier exists for exactly that,
# selected by `reads_checkouts`, and a second Nx project would need its own key over the
# same nothing. `tests/test_nx_cache_scope.py` holds the marker to routing.
pytestmark = pytest.mark.reads_checkouts
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]

#: Where the project environment installed every pinned CLI, beside the interpreter running
#: this suite — never whichever copy another checkout put first on `PATH`.
INSTALLED = Path(sys.executable).parent

#: A run this repository recorded, copied per journey so a new event can be written to it.
RECORDED_RUN = REPO_ROOT / "tests" / "fixtures" / "timeline-runs" / "gate-parity-2"
#: The resume line `onepipeline monitor` ends a pass with, and one rendered event line.
CURSOR_LINE = "-- cursor "
EVENT_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}T\S+Z\s")

#: A registry rules file every scratch identity below publishes under.
RULES = (
    "version: 3\n"
    "trailer_prefix: Orchestrator-\n"
    "rules: []\n"
    "default:\n"
    "  publication: local-direct\n"
    "  approvals: none\n"
)
BASE = "main"
#: Who the scratch commits are by, so no host git identity is needed.
AUTHOR = {
    "GIT_AUTHOR_NAME": "Adopted CLI journey",
    "GIT_AUTHOR_EMAIL": "journey@example.invalid",
    "GIT_COMMITTER_NAME": "Adopted CLI journey",
    "GIT_COMMITTER_EMAIL": "journey@example.invalid",
}

#: A producer declaration naming one automated target whose probe never answers, so a
#: landing captures no release baseline — the state only an acknowledgement can release.
RELEASE_TARGETS = (
    "schema_version = 1\n"
    'probe = "probe.sh"\n'
    "\n"
    "[[target]]\n"
    'id = "pypi:scratch-lib"\n'
    'name = "wheel"\n'
    'what = "A scratch wheel nothing publishes."\n'
    'published_by = "Nothing: this journey only needs a landing with no baseline."\n'
)
SILENT_PROBE = '#!/bin/sh\necho "no registry answers here" >&2\nexit 3\n'


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] These are typed views
# of the public wire input this journey sends to the installed engine; its real reply
# validator is the drift gate, so a field removed or changed by the authority refuses
# the journey rather than letting a parallel local implementation accept it.
class SettleCommand(TypedDict):
    """A manager correction carrying the landing the run failed to observe."""

    op: str
    id: str
    outcome: str
    evidence: str
    landing: str
    release: NotRequired[Release]


class Release(TypedDict):
    """A released artifact the operator correlates with a stated landing."""

    target: str
    version: str


class SettleEnvelope(TypedDict):
    """The public reply envelope used by the stated-landing journey."""

    version: int
    completion: bool
    message: str
    reason: str
    commands: list[SettleCommand]


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


def _installed(binary: str, pin: str) -> Path:
    """The installed `binary`, failing unless it reports the release `config/<pin>.version` pins."""
    path = INSTALLED / binary
    adopted = (REPO_ROOT / "config" / f"{pin}.version").read_text(encoding="utf-8").strip()
    assert path.exists(), f"no {binary} at {path}; run `scripts/session-setup.sh`"
    reported = subprocess.run(
        [str(path), "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert reported == f"{binary} {adopted}", (
        f"{path} reports {reported!r}, not the {binary} {adopted} config/{pin}.version adopts"
    )
    return path


def _run(
    *arguments: str | Path, env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(argument) for argument in arguments],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _events(rendered: str) -> list[str]:
    return [line for line in rendered.splitlines() if EVENT_LINE.match(line)]


def test_a_monitor_resumed_from_its_cursor_renders_only_what_was_recorded_since(
    tmp_path: Path,
) -> None:
    onepipeline = _installed("onepipeline", "onepipeline")
    runs = tmp_path / "runs"
    shutil.copytree(RECORDED_RUN, runs / RECORDED_RUN.name)
    env = {**os.environ, "ONEPIPELINE_RUNS_DIR": str(runs)}

    first = _run(onepipeline, "monitor", RECORDED_RUN.name, env=env, cwd=tmp_path)
    assert first.returncode == 0, first.stderr
    lines = first.stdout.splitlines()
    cursors = [line for line in lines if line.startswith(CURSOR_LINE)]
    assert len(cursors) == 1 and lines[-1] == cursors[0], (
        f"a monitor pass has to end with exactly one resume line: {first.stdout}"
    )
    rendered = _events(first.stdout)
    assert rendered, first.stdout

    recorded_since = ("recorded after the cursor: first", "recorded after the cursor: second")
    for message in recorded_since:
        surfaced = _run(
            onepipeline,
            "surface",
            RECORDED_RUN.name,
            "--kind",
            "finding",
            "--message",
            message,
            env=env,
            cwd=tmp_path,
        )
        assert surfaced.returncode == 0, surfaced.stderr

    cursor = cursors[0].removeprefix(CURSOR_LINE)
    resumed = _run(
        onepipeline, "monitor", RECORDED_RUN.name, "--cursor", cursor, env=env, cwd=tmp_path
    )
    assert resumed.returncode == 0, resumed.stderr
    events = _events(resumed.stdout)
    assert not set(events) & set(rendered), (
        f"a pass resumed from {cursor} re-rendered events the first pass had: {resumed.stdout}"
    )
    for message in recorded_since:
        assert sum(message in event for event in events) == 1, (
            f"a pass resumed from {cursor} has to render {message!r} once: {resumed.stdout}"
        )
    assert resumed.stdout.splitlines()[-1] != cursors[0], (
        "the resumed pass has to hand back a cursor past the events it rendered"
    )


def test_a_stated_landing_is_authoritative_in_the_results_view(tmp_path: Path) -> None:
    """A manager correction is rendered from its evidence, not the superseded branch."""
    runs = tmp_path / "runs"
    env = {**os.environ, "ONEPIPELINE_RUNS_DIR": str(runs)}
    landings = (
        ("stated-change-request", "https://github.com/octo-org/example/pull/17"),
        ("stated-commit", "0123456789abcdef0123456789abcdef01234567"),
    )
    for tier, landing in landings:
        run = tier
        shutil.copytree(RECORDED_RUN, runs / run)
        envelope = _settle_envelope(landing)
        replied = _reply(run, envelope, env)
        assert replied.returncode == 0, replied.stdout + replied.stderr

        results = _run("just", "results", run, env=env, cwd=REPO_ROOT)
        assert results.returncode == 0, results.stderr
        line = next(line for line in results.stdout.splitlines() if "gate-parity-land" in line)
        assert "landed on its base" in line, line
        assert tier in line, line
        assert landing in line, line
        assert "NOT landed" not in line, line

    refused_run = "unusable-stated-landing"
    shutil.copytree(RECORDED_RUN, runs / refused_run)
    envelope = _settle_envelope("not-a-landing")
    refused = _reply(refused_run, envelope, env)
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert 'landing of "not-a-landing", which is neither' in refused.stderr, refused.stderr


def _settle_envelope(landing: str, *, release: Release | None = None) -> SettleEnvelope:
    command: SettleCommand = {
        "op": "settle",
        "id": "gate-parity-land",
        "outcome": "failed",
        "evidence": "the operator observed the landing",
        "landing": landing,
    }
    if release is not None:
        command["release"] = release
    return {
        "version": 3,
        "completion": False,
        "message": "Record the landing the run did not observe.",
        "reason": "The landing completed after the dispatch failed.",
        "commands": [command],
    }


def _reply(
    run: str, envelope: SettleEnvelope, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's real engine wrapper
        [str(REPO_ROOT / "scripts" / "onepipeline.sh"), "reply", run],
        cwd=REPO_ROOT,
        env=env,
        input=json.dumps(envelope),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _scratch_onevcs(tmp_path: Path) -> dict[str, str]:
    """An environment whose `ONEVCS_HOME` is a scratch state root with a rules file."""
    home = tmp_path / "onevcs-home"
    home.mkdir()
    (home / "rules.yml").write_text(RULES, encoding="utf-8")
    return {**os.environ, **AUTHOR, "ONEVCS_HOME": str(home)}


def _git(*arguments: str | Path, cwd: Path, env: dict[str, str]) -> None:
    done = _run("git", *arguments, env=env, cwd=cwd)
    assert done.returncode == 0, f"git {arguments}: {done.stderr or done.stdout}"


def _registered_checkout(
    tmp_path: Path,
    name: str,
    branch: str,
    env: dict[str, str],
    seeded: dict[str, str] | None = None,
) -> Path:
    """A registered checkout of a throwaway origin, holding one unpublished `branch`."""
    seed = tmp_path / f"{name}-seed"
    _git("init", "-q", "-b", BASE, seed, cwd=tmp_path, env=env)
    (seed / "README.md").write_text(f"{name}\n", encoding="utf-8")
    for relative, text in (seeded or {}).items():
        (seed / relative).write_text(text, encoding="utf-8")
        if text.startswith("#!"):
            (seed / relative).chmod(0o755)
    _git("add", "-A", cwd=seed, env=env)
    _git("commit", "-q", "-m", "init", cwd=seed, env=env)
    origin = tmp_path / f"{name}.git"
    _git("clone", "-q", "--bare", seed, origin, cwd=tmp_path, env=env)
    checkout = tmp_path / name
    _git("clone", "-q", origin, checkout, cwd=tmp_path, env=env)
    _git("checkout", "-q", "-b", branch, cwd=checkout, env=env)
    (checkout / f"{name}.txt").write_text("the work\n", encoding="utf-8")
    _git("add", "-A", cwd=checkout, env=env)
    _git("commit", "-q", "-m", f"feat: {name} work", cwd=checkout, env=env)
    _git("checkout", "-q", BASE, cwd=checkout, env=env)
    registered = _run(_installed("onevcs", "onevcs"), "register", checkout, env=env, cwd=tmp_path)
    assert registered.returncode == 0, registered.stderr
    return checkout


def test_recoverable_scoped_by_repo_lists_that_identity_and_no_other(tmp_path: Path) -> None:
    onevcs = _installed("onevcs", "onevcs")
    env = _scratch_onevcs(tmp_path)
    branches = {"alpha": "claude/alpha-work", "beta": "claude/beta-work"}
    checkouts = {
        name: _registered_checkout(tmp_path, name, branch, env) for name, branch in branches.items()
    }

    for name, checkout in checkouts.items():
        listed = _run(onevcs, "recoverable", "--repo", checkout, env=env, cwd=tmp_path)
        assert listed.returncode == 0, listed.stderr
        assert branches[name] in listed.stdout, listed.stdout
        others = [branch for other, branch in branches.items() if other != name]
        assert not [branch for branch in others if branch in listed.stdout], (
            f"`recoverable --repo {checkout}` listed another identity's branch: {listed.stdout}"
        )


def test_an_automated_landing_with_no_baseline_is_released_by_an_acknowledgement(
    tmp_path: Path,
) -> None:
    onevcs = _installed("onevcs", "onevcs")
    env = _scratch_onevcs(tmp_path)
    branch = "feat/work"
    checkout = _registered_checkout(
        tmp_path,
        "lib",
        branch,
        env,
        seeded={"release-targets.toml": RELEASE_TARGETS, "probe.sh": SILENT_PROBE},
    )
    landed = _run(onevcs, "publish-branch", branch, "--repo", checkout, env=env, cwd=tmp_path)
    assert landed.returncode == 0, landed.stderr

    held = _run(onevcs, "release", "status", branch, "--target", "wheel", env=env, cwd=checkout)
    assert held.returncode == 0, held.stderr
    assert held.stdout.startswith("not answered: no baseline was captured"), held.stdout

    acknowledged = _run(
        onevcs,
        "release",
        "acknowledge",
        branch,
        "--target",
        "wheel",
        "--version",
        "1.0.0",
        env=env,
        cwd=checkout,
    )
    assert acknowledged.returncode == 0, acknowledged.stderr

    released = _run(onevcs, "release", "status", branch, "--target", "wheel", env=env, cwd=checkout)
    assert released.returncode == 0, released.stderr
    assert released.stdout.strip() == "released: wheel 1.0.0 (automated, acknowledged)", (
        released.stdout
    )


def test_a_settle_correlates_its_stated_landing_with_the_release(tmp_path: Path) -> None:
    """The installed engine records a valid release and refuses an invalid one."""
    onevcs = _installed("onevcs", "onevcs")
    env = _scratch_onevcs(tmp_path)
    branch = "feat/work"
    checkout = _registered_checkout(
        tmp_path,
        "settled-lib",
        branch,
        env,
        seeded={"release-targets.toml": RELEASE_TARGETS, "probe.sh": SILENT_PROBE},
    )
    landed = _run(onevcs, "publish-branch", branch, "--repo", checkout, env=env, cwd=tmp_path)
    assert landed.returncode == 0, landed.stderr
    synced = _run(onevcs, "sync", env=env, cwd=checkout)
    assert synced.returncode == 0, synced.stderr
    landing_commit = _run("git", "rev-parse", branch, env=env, cwd=checkout).stdout.strip()

    runs = tmp_path / "runs"
    refused_run = "refused-release-correlation"
    shutil.copytree(RECORDED_RUN, runs / refused_run)
    run_env = {**env, "ONEPIPELINE_RUNS_DIR": str(runs)}
    refused = _reply(
        refused_run,
        _settle_envelope(landing_commit, release={"target": "wheel", "version": "the-nightly"}),
        run_env,
    )
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "is not a semantic version" in refused.stderr, refused.stderr

    run = "release-correlation"
    shutil.copytree(RECORDED_RUN, runs / run)
    replied = _reply(
        run,
        _settle_envelope(landing_commit, release={"target": "wheel", "version": "1.0.0"}),
        run_env,
    )
    assert replied.returncode == 0, replied.stdout + replied.stderr
    released = _run(onevcs, "release", "status", branch, "--target", "wheel", env=env, cwd=checkout)
    assert released.returncode == 0, released.stderr
    assert released.stdout.strip() == "released: wheel 1.0.0 (automated, acknowledged)", (
        released.stdout
    )


def _unreachable_endpoint() -> str:
    """A loopback URL nothing listens on, so any request the source makes is refused."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/graphql"


def _board_source(tmp_path: Path, unknown: str) -> subprocess.CompletedProcess[str]:
    """`project list` over one `github-projects` source whose `unknown` maps as `unknown`."""
    (tmp_path / "onetaskgraph.yaml").write_text(
        "sources:\n"
        "  board:\n"
        "    plugin: github-projects\n"
        "    config:\n"
        "      owner: octo-org\n"
        "      project_number: 7\n"
        "      repository: octo-org/tasks\n"
        "      status_mapping:\n"
        f"        unknown: {unknown}\n",
        encoding="utf-8",
    )
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("ONETASKGRAPH_") and name != "GH_PROJECTS_TOKEN"
    }
    env.update(
        {
            "GH_PROJECTS_TOKEN": "journey-placeholder-not-a-credential",
            "ONETASKGRAPH_SOURCES__BOARD__CONFIG__ENDPOINT": _unreachable_endpoint(),
            "ONETASKGRAPH_SECRETS_FILE": str(tmp_path / "no-secrets.env"),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        }
    )
    installed = subprocess.run(
        [str(ONETASKGRAPH_BIN), "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    adopted = (REPO_ROOT / "config" / "onetaskgraph.version").read_text(encoding="utf-8").strip()
    assert installed == f"onetaskgraph {adopted}", installed
    return _run(ONETASKGRAPH_BIN, "project", "list", env=env, cwd=tmp_path)


@pytest.mark.parametrize(
    ("closed", "read_back"), [("completed", "done"), ("not-planned", "cancelled")]
)
def test_the_board_source_refuses_an_unknown_status_mapped_to_a_closed_state(
    tmp_path: Path, closed: str, read_back: str
) -> None:
    refused = _board_source(tmp_path, f"{{closed: {closed}}}")

    said = refused.stdout + refused.stderr
    assert refused.returncode != 0, said
    assert (
        f"status_mapping.unknown of source board cannot target the closed state {closed} "
        f"because a copy reads that item back as {read_back}"
    ) in said, said


def test_the_board_source_mapping_unknown_to_an_option_reaches_for_the_board(
    tmp_path: Path,
) -> None:
    """The control: the same source, mapped to an option, is refused only by the endpoint."""
    answered = _board_source(tmp_path, "Needs attention")

    said = answered.stdout + answered.stderr
    assert "status_mapping.unknown" not in said, said
    assert "could not be reached" in said, said


#: The stand-in for the paid codex provider, spawned by the installed oneharness.
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"
#: The worker side, answering onejudge's own `docs/protocol.md`.
WORKER_DOUBLE = Path(__file__).resolve().parent / "judge_protocol_double.py"
#: The heading onejudge 0.11.0 opens its artifact section with, in every judge-side prompt.
ARTIFACT_SECTION = "ARTIFACTS TO READ DIRECTLY"


def test_a_judge_side_prompt_names_every_configured_artifact_by_its_resolved_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    onejudge = _installed("onejudge", "onejudge")
    worktree = tmp_path / "worktree"
    (worktree / "design").mkdir(parents=True)
    (worktree / "design" / "notes.md").write_text("the design\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("named by an absolute path\n", encoding="utf-8")
    judge_config = tmp_path / "oneharness.judge.toml"
    judge_config.write_text(
        'run_mode = "fallback"\nharnesses = ["codex"]\n\n[harness.codex]\nmodel = "gpt-5.5"\n',
        encoding="utf-8",
    )
    prompts = tmp_path / "prompts.jsonl"
    config = RunConfig(
        provider={
            "kind": "split",
            "skill": {
                "kind": "command",
                # llmlint: ignore[e2e_not_mocked] The worker side answers onejudge's own
                # `docs/protocol.md`; what is under test is the judge side's prompt, which
                # this double never touches.
                "command": [sys.executable, str(WORKER_DOUBLE), "worker", "0"],
            },
            "judge": {
                "kind": "oneharness",
                "bin": str(_installed("oneharness", "oneharness")),
                "judge_config": str(judge_config),
            },
        },
        user={
            "persona": "A reviewer reading the design document.",
            "done_when": "the design is written",
            "max_turns": 2,
            "artifacts": ["design/notes.md", str(outside)],
        },
    )
    for name, value in {
        # llmlint: ignore[e2e_not_mocked] Only the paid codex provider is substituted, at the
        # path oneharness spawns it from; onejudge, the oneharness CLI it spawns, and the
        # prompt they compose between them are the installed releases.
        "ONEHARNESS_BIN_CODEX": str(FAKE_CODEX),
        "FAKE_CODEX_PROMPT_LOG": str(prompts),
        "FAKE_CODEX_ATTEMPT_LOG": str(tmp_path / "launches"),
        "FAKE_CODEX_ANSWERS": json.dumps(
            [
                json.dumps({"completion": True, "reason": "the design is written"}),
                json.dumps({"value": True, "reason": "the design is written"}),
            ]
        ),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }.items():
        monkeypatch.setenv(name, value)

    client = OneJudge(executable=str(onejudge))
    asyncio.run(client.run(config, "Write the design.", cwd=str(worktree), timeout=120))

    judged = [
        json.loads(line)["prompt"] for line in prompts.read_text(encoding="utf-8").splitlines()
    ]
    assert judged, "the judge side was never asked anything, so no prompt was recorded"
    for prompt in judged:
        section = prompt.partition(ARTIFACT_SECTION)[2]
        assert section, f"a judge-side prompt carries no {ARTIFACT_SECTION!r} section: {prompt}"
        listed = section.split("\n\n", 1)[0]
        assert "  - ./design/notes.md" in listed.splitlines(), listed
        assert f"  - {outside}" in listed.splitlines(), listed


#: A configuration document rooting one `local-md` source at a path relative to itself,
#: which is the shape `onetaskgraph.yaml` gives this repository's own `authoring` and
#: `drafts` sources.
RELATIVE_ROOT = (
    "default_sources: [work]\n"
    "sources:\n"
    "  work:\n"
    "    plugin: local-md\n"
    "    config:\n"
    "      root: .tasks\n"
)
#: The setting a reader asks `config show` for, and the one record the store finds under it.
ROOT_SETTING = "sources.work.config.root"
PROJECT = "demo"
TASK = "a-task"


def _rooted(directory: Path) -> Path:
    """A directory holding `RELATIVE_ROOT` and one task under the root it names."""
    records = directory / ".tasks"
    (records / "projects").mkdir(parents=True)
    (records / "tasks" / PROJECT).mkdir(parents=True)
    (directory / "onetaskgraph.yaml").write_text(RELATIVE_ROOT, encoding="utf-8")
    (records / "projects" / f"{PROJECT}.md").write_text(
        f'---\ntitle: "{PROJECT}"\nstatus: "todo"\n---\n\nA project.\n', encoding="utf-8"
    )
    (records / "tasks" / PROJECT / f"{TASK}.md").write_text(
        f'---\ntitle: "a task"\nproject: "{PROJECT}"\nstatus: "todo"\n---\n\nA task.\n',
        encoding="utf-8",
    )
    return records


def _store_environment(tmp_path: Path) -> dict[str, str]:
    """This process's environment with every plan-store setting of the launch removed.

    A launch exports absolute roots for this checkout's own sources at the store's
    environment layer, and this host's dispatches run under them. That layer beats a
    document, so leaving them in place would measure the launch rather than the release.
    """
    kept = {
        name: value for name, value in os.environ.items() if not name.startswith("ONETASKGRAPH_")
    }
    kept["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    return kept


def _resolved_root(directory: Path, environment: dict[str, str]) -> str:
    """What the installed store resolves `ROOT_SETTING` to, read from ``directory``."""
    shown = _run(ONETASKGRAPH_BIN, "config", "show", "--json", env=environment, cwd=directory)
    assert shown.returncode == 0, shown.stdout + shown.stderr
    settings = json.loads(shown.stdout)["settings"]
    named = [setting for setting in settings if setting["key"] == ROOT_SETTING]
    assert len(named) == 1, f"one `{ROOT_SETTING}` was expected, and {named} was reported"
    return str(named[0]["value"])


def test_a_relative_source_root_resolves_against_the_document_that_supplied_it(
    tmp_path: Path,
) -> None:
    """The root a document names is that document's directory's, whoever reads it from where.

    Read from a *subdirectory* of the checkout holding the document, which is what
    distinguishes the two answers: on the release before the fix the setting came back as
    the bare `.tasks`, which each reading process then resolved against its own working
    directory, so a read one directory down found no records at all.
    """
    checkout = tmp_path / "checkout"
    records = _rooted(checkout)
    below = checkout / "below"
    below.mkdir()
    environment = _store_environment(tmp_path)

    assert _resolved_root(below, environment) == str(records), (
        "a relative root has to resolve against the document that supplied it"
    )
    listed = _run(ONETASKGRAPH_BIN, "task", "list", "--json", env=environment, cwd=below)
    assert listed.returncode == 0, listed.stdout + listed.stderr
    found = [item["item"]["location"]["path"] for item in json.loads(listed.stdout)["items"]]
    assert found == [str(records / "tasks" / PROJECT / f"{TASK}.md")], found


#: How the store spells `ROOT_SETTING` at its environment layer, which is the layer
#: `scripts/plan-root-env.sh` and `scripts/follow-up-env.sh` export this checkout's own
#: absolute roots at.
ROOT_SETTING_ENV = "ONETASKGRAPH_SOURCES__WORK__CONFIG__ROOT"


def test_an_exported_absolute_root_still_beats_the_document_a_dispatch_reads(
    tmp_path: Path,
) -> None:
    """Why this host still exports an absolute root rather than relying on the fix.

    A dispatch works in a worktree carrying its own copy of `onetaskgraph.yaml`, so
    resolving against the document it reads puts its records under *that* copy — the
    directory nothing outside the worktree reads and which is reclaimed with it. The
    launch's exported root is what answers that, and it answers it at a layer above the
    document: read from the worktree, the store resolves the launching checkout's
    records, and the task the launch stored there is the one the dispatch finds.
    """
    launching = _rooted(tmp_path / "launching")
    worktree = _rooted(tmp_path / "worktree")
    environment = _store_environment(tmp_path)

    assert _resolved_root(worktree.parent, environment) == str(worktree), (
        "left to the document, a dispatch in a worktree roots its source in that worktree"
    )

    exported = {**environment, ROOT_SETTING_ENV: str(launching)}
    assert _resolved_root(worktree.parent, exported) == str(launching), (
        "an exported absolute root has to beat the document the dispatch reads"
    )
    listed = _run(ONETASKGRAPH_BIN, "task", "list", "--json", env=exported, cwd=worktree.parent)
    assert listed.returncode == 0, listed.stdout + listed.stderr
    found = [item["item"]["location"]["path"] for item in json.loads(listed.stdout)["items"]]
    assert found == [str(launching / "tasks" / PROJECT / f"{TASK}.md")], found


#: A task record whose front matter never closes its quoted title: the shape a planner's
#: interrupted write, or a hand edit, leaves behind.
MALFORMED_TASK = "broken"
MALFORMED_FRONT_MATTER = '---\ntitle: "broken\nstatus: [\n---\n\nA task nothing can parse.\n'
#: The exit the store reserves for a query some source could not answer, as its own
#: `--help` states it; `--allow-partial` is what turns that into an answer without them.
SOURCE_REFUSED = 4


def test_a_listing_that_meets_a_malformed_record_names_it_instead_of_omitting_it(
    tmp_path: Path,
) -> None:
    """One unparseable task fails the whole listing, with the path and the diagnostic.

    The release before the fix listed the parseable records and said nothing, exit 0,
    so a plan carrying a broken task read as a smaller plan — to a manager, and to the
    engine, which reads every plan through this CLI. Here the listing refuses with the
    record's own path, the parser's own words, and the store's partial-answer exit, and
    the JSON form classifies the refusal as `malformed` rather than folding it into an
    empty page.
    """
    checkout = tmp_path / "checkout"
    records = _rooted(checkout)
    broken = records / "tasks" / PROJECT / f"{MALFORMED_TASK}.md"
    broken.write_text(MALFORMED_FRONT_MATTER, encoding="utf-8")
    environment = _store_environment(tmp_path)

    listed = _run(ONETASKGRAPH_BIN, "task", "list", env=environment, cwd=checkout)
    assert listed.returncode == SOURCE_REFUSED, listed.stdout + listed.stderr
    assert str(broken) in listed.stderr, (
        f"a refused listing has to name the record it could not parse: {listed.stderr}"
    )
    assert "line 1" in listed.stderr, (
        f"a refused listing has to carry the parser's own diagnostic: {listed.stderr}"
    )
    assert TASK not in listed.stdout, (
        f"a listing that refused must not also print the records it could parse: {listed.stdout}"
    )

    as_json = _run(ONETASKGRAPH_BIN, "task", "list", "--json", env=environment, cwd=checkout)
    assert as_json.returncode == SOURCE_REFUSED, as_json.stdout + as_json.stderr
    answer = json.loads(as_json.stdout)
    assert answer["items"] == [], answer
    (error,) = answer["errors"]
    assert error["error"]["kind"] == "malformed", error
    assert str(broken) in error["error"]["message"], error
