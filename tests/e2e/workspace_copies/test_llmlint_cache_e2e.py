"""E2E proof that the judged tier replays a pass only for the tree, base and judge it judged.

`just lint-llm-diff` resolves its base, refuses one it cannot judge or one its own origin
ref has moved past, prints the commit, and runs the cached Nx target
`workspace:lint-llm-diff`, whose command is the llmlint CLI itself and whose cache key is
the tracked tree, `LLMLINT_DIFF_BASE_SHA` and `llmlint fingerprint`. These journeys drive
the real recipe, the real `scripts/nx.sh`, real Nx and the installed llmlint, which runs
the real `oneharness` under the copy's own `oneharness.llmlint.toml` — named by the copy's
`llmlint.yml`, as this repository's is. Only the paid model is substituted: every provider
binary on `PATH` is the `no-paid-provider` guard, which hands the turn to
`tests/e2e/fake_codex.py`, and that stand-in answers llmlint's batch and records each judge
call. A replay is read off the stand-in recording no new call and off Nx's own
`Nx read the output from the cache` line, never off one task annotation.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] Every journey here drives
real Nx, real git, the installed llmlint and the real oneharness over a copy of the whole
tree under the same whole-workspace `reads_docs` key; none is cheaper than the others, so
the placement is the module's rather than any one test's.

llmlint: ignore-file[shell_test_tiers_stay_split] Same placement, same reason. What could
be kept out of this tier was: `tests/e2e/publish_and_landing/test_base_freshness_e2e.py`
drives the same `scripts/base-freshness.sh` with no Nx and no judge at all.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] Same placement, same
reason. `reads_docs` here is not a deselecting tier either: it names the whole-workspace
key this module's own premise — copying the tracked tree, prose included — requires.

llmlint: ignore-file[e2e_not_mocked] The provider process behind oneharness is this
repository's paid model boundary, substituted here exactly as the rest of the e2e suite
substitutes it; the recipe, Nx, git, llmlint and oneharness are all real.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import pytest
import short_state
import yaml
from nx_workspace import copy_working_tree, share_this_checkouts_install
from project_fixtures import helper
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = [
    pytest.mark.skipif(
        shutil.which("llmlint") is None,
        reason="the judged tier runs the installed llmlint; run 'just setup-llmlint'",
    ),
    # Copying the whole tree is this journey's premise, and the tree includes its prose:
    # this belongs to the whole-workspace tier by construction.
    pytest.mark.reads_docs,
]

# llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
FAKE_CODEX = helper("fake_codex.py")
IDENTITY_STAND_IN = helper("identity_stand_in.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The origin every copy names, so `scripts/nx.sh` keys every copy's cache as one
#: repository's, as it keys two worktrees of this one.
ORIGIN = "https://example.invalid/nickderobertis/ai-orchestrator.git"
#: The two rules the copy's `llmlint.yml` holds, so a reply can answer some of them.
RULES = ("local_rule", "second_rule")
#: The tracked file each copy's change commit edits, and so the one file judged.
CHANGED = "scripts/base-freshness.sh"
#: A plugin rule, loaded from outside the tree.
PLUGIN = (
    "version: 1\nrules:\n"
    "  - name: plugin_rule\n"
    "    description: Every new operator entry point is documented.\n"
)
#: What Nx prints when it replayed the task instead of running it.
REPLAYED = "Nx read the output from the cache instead of running the command"
#: llmlint's one-line summary of a run.
SUMMARY = re.compile(r"^.*\b\d+ passed\b.*$", re.MULTILINE)
#: Every commit the copies make is dated here, so the same content is the same commit id
#: in every copy.
DATED = {
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


def _llmlint_config(plugin: Path) -> str:
    """The copy's `llmlint.yml`: two local rules, and this repository's harness routing.

    The repository's own plugins are fetched from the network, so the copy holds two local
    rules in their place; its `oneharness` block is read out of this repository's file, so
    the routing the copy judges under is the one this repository ships. It selects only
    the files the journeys edit, because every other file's ignore directives name this
    repository's rules rather than the copy's two, which `llmlint validate` refuses. One
    more rule comes from ``plugin``, a file outside the tree, which only the judge's
    fingerprint can see change.
    """
    routed = yaml.safe_load((REPO_ROOT / "llmlint.yml").read_text(encoding="utf-8"))
    return yaml.safe_dump(
        {
            "files": {"include": [CHANGED, "README.md", "llmlint.yml"]},
            "oneharness": routed["oneharness"],
            "plugins": [str(plugin)],
            "rules": [
                {"name": RULES[0], "description": "Every touched shell script stays POSIX-safe."},
                {"name": RULES[1], "description": "Every touched script names its purpose."},
            ],
        },
        sort_keys=False,
    )


@dataclass(frozen=True)
class Workspace:
    """One copy of this checkout, judged through the real tier with a recorded model."""

    root: Path
    env: dict[str, str]
    judge_log: Path

    def lint(self, base: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["just", "lint-llm-diff", base],
            cwd=self.root,
            env={**self.env, **overrides},
            stdin=subprocess.DEVNULL,
            check=False,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(300),
        )

    def gate_llmlint(self, remote: str, base: str) -> str:
        """Run `just gate` under the lifecycle's comparison base through its judged stage.

        The gate runs the whole deterministic suite after its judged stage, which is not
        this journey's subject, so the gate this test started is stopped — by the process
        group it was started in — once that stage has passed and the next one has opened
        its log. Returns the judged stage's log.
        """
        logs = self.root / ".logs"
        said = self.root.parent / f"{self.root.name}-gate.log"
        with said.open("w", encoding="utf-8") as output:
            gate = subprocess.Popen(
                ["just", "gate"],
                cwd=self.root,
                env={
                    **self.env,
                    "ONEVCS_COMPARISON_REMOTE": remote,
                    "ONEVCS_COMPARISON_BASE": base,
                },
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        try:
            deadline = time.monotonic() + e2e_timeout(300)
            while not (logs / "gate-check.log").exists():
                assert gate.poll() is None, said.read_text(encoding="utf-8")
                assert time.monotonic() < deadline, "the gate never reached its next stage"
                time.sleep(0.5)
        finally:
            if gate.poll() is None:
                os.killpg(gate.pid, signal.SIGTERM)
            gate.wait(timeout=e2e_timeout(60))
        return (logs / "gate-llmlint.log").read_text(encoding="utf-8")

    def judge_calls(self) -> int:
        if not self.judge_log.exists():
            return 0
        return len(self.judge_log.read_text(encoding="utf-8").splitlines())

    def fingerprint(self) -> str:
        """`llmlint fingerprint` as the target's runtime input runs it, from the copy's root."""
        ran = subprocess.run(
            ["llmlint", "fingerprint"],
            cwd=self.root,
            env=self.env,
            check=True,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
        )
        return ran.stdout.strip()

    def edit(self, relative: str, change: Callable[[str], str]) -> None:
        path = self.root / relative
        path.write_text(change(path.read_text(encoding="utf-8")), encoding="utf-8")

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@invalid", *args],
            cwd=self.root,
            env={**self.env, **DATED},
            check=True,
            text=True,
            capture_output=True,
        ).stdout


@dataclass(frozen=True)
class Copies:
    """A first copy holding a base commit and a change on it, and a way to make more."""

    first: Workspace
    base: str
    make: Callable[[str], Workspace]
    #: The plugin outside every copy's tree that each copy's `llmlint.yml` loads.
    plugin: Path


def _exclude_install(root: Path) -> None:
    """Keep the shared install's links out of every commit a copy makes.

    `.gitignore` names `node_modules/`, which matches a directory and not the symlink
    `share_this_checkouts_install` puts there.
    """
    (root / ".git" / "info" / "exclude").write_text("/node_modules\n/.venv\n", encoding="utf-8")


@pytest.fixture
def copies(tmp_path: Path) -> Copies:
    cache = tmp_path / "cache"
    judge_log = tmp_path / "judge-calls.jsonl"
    identities = tmp_path / "identities"
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(
            ("ONEHARNESS_", "LLMLINT_", "FAKE_CODEX_", "NX_", "ONEVCS_", "ORCHESTRATOR_")
        )
        # The session's own Claude Code and Codex variables name its provider binary and
        # identity, which no journey here may reach.
        and not name.startswith(("CLAUDE", "CODEX"))
        and name not in {"UV_PROJECT_ENVIRONMENT", "CI", "AI_AGENT"}
    }
    environment |= {
        "PATH": os.pathsep.join([str(PAID_PROVIDER_GUARD), environment["PATH"]]),
        # Every provider is named by absolute path: under an Nx task `node_modules/.bin`
        # and Node's own bin directory, where a host's npm-installed `codex` and `claude`
        # live, precede the caller's `PATH`, so a `PATH` guard alone is shadowed there.
        # Codex answers; every Claude candidate refuses, so no turn reaches a paid one.
        # llmlint: ignore[e2e_not_mocked] Only the paid provider processes are substituted.
        "ONEHARNESS_BIN_CODEX": str(FAKE_CODEX),
        "ONEHARNESS_BIN_CLAUDE_CODE": str(IDENTITY_STAND_IN),
        "FAKE_CODEX_LLMLINT_PROMPT_LOG": str(judge_log),
        # One Nx cache (scripts/nx.sh roots it here) and one plugin cache for every copy,
        # neither of them the developer's.
        "XDG_CACHE_HOME": str(cache),
        "XDG_STATE_HOME": str(short_state.state_home(tmp_path)),
        "LLMLINT_HISTORY_DIR": str(tmp_path / "llmlint-history"),
        "ONEHARNESS_HISTORY": "false",
        "ORCHESTRATOR_CODEX_ALT_HOME": str(identities / "codex-alt"),
        **{
            variable: str(identities / variable)
            for variable in (
                "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR",
                "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR",
                "ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR",
                "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR",
            )
        },
    }

    plugin = tmp_path / "plugin.llmlint.yml"
    plugin.write_text(PLUGIN, encoding="utf-8")
    first_root = tmp_path / "copies" / "first"
    copy_working_tree(first_root)
    (first_root / "llmlint.yml").write_text(_llmlint_config(plugin), encoding="utf-8")
    shared = share_this_checkouts_install(first_root)
    environment |= shared
    first = Workspace(first_root, {**environment, "PYTHONPATH": str(first_root)}, judge_log)
    first.git("init", "-q", "-b", "main")
    _exclude_install(first_root)
    first.git("remote", "add", "origin", ORIGIN)
    base = first.commit("base")
    first.edit(CHANGED, lambda text: text + "# changed under test\n")
    first.commit("change")

    def make(name: str) -> Workspace:
        # A worktree of the first copy, as a pool slot is of its checkout: Nx keeps its
        # cache index in the main worktree's workspace data, which every worktree of one
        # repository shares, while the outputs live in the cache `scripts/nx.sh` keys on
        # the origin.
        root = tmp_path / "copies" / name
        first.git("worktree", "add", "-q", "--detach", str(root), "HEAD")
        share_this_checkouts_install(root)
        return Workspace(root, {**environment, "PYTHONPATH": str(root)}, judge_log)

    return Copies(first, base, make, plugin)


def _summary(result: subprocess.CompletedProcess[str]) -> str:
    found = SUMMARY.findall(result.stdout + result.stderr)
    assert found, result.stdout + result.stderr
    return found[-1].strip()


def _judged(workspace: Workspace, base: str, commit: str | None = None) -> str:
    """Run the tier once, expecting a fresh judgement; returns llmlint's summary line.

    ``commit`` is the commit ``base`` resolves to, where ``base`` names a ref.
    """
    before = workspace.judge_calls()
    result = workspace.lint(base)
    report = result.stdout + result.stderr
    assert result.returncode == 0, report
    assert f"lint-llm-diff: judging this diff against base {commit or base}" in report, report
    assert workspace.judge_calls() > before, report
    assert REPLAYED not in report, report
    return _summary(result)


def _replayed(workspace: Workspace, base: str) -> str:
    """Run the tier once, expecting a replay; returns llmlint's summary line."""
    before = workspace.judge_calls()
    result = workspace.lint(base)
    report = result.stdout + result.stderr
    assert result.returncode == 0, report
    assert f"lint-llm-diff: judging this diff against base {base}" in report, report
    assert workspace.judge_calls() == before, report
    assert REPLAYED in report, report
    return _summary(result)


def test_a_pass_is_replayed_for_an_unchanged_tree_base_and_judge(copies: Copies) -> None:
    """The first run judges; the second replays the same summary with no judge call."""
    first = _judged(copies.first, copies.base)

    assert _replayed(copies.first, copies.base) == first


def test_the_judge_s_temporary_directory_is_its_own_and_is_removed(
    copies: Copies, tmp_path: Path
) -> None:
    """What the judge's harness leaves in its temporary directory goes when the tier exits.

    The stand-in leaves a sandbox mount-target directory in whatever temporary directory
    its turn was handed, as the real codex does on every turn; that directory sat inside
    the caller's, and the caller's is empty once the recipe returns. The caller's is a
    short one, as the gate's own root is, because Nx keeps its plugin sockets in it.
    """
    caller_tmp = short_state.state_home(tmp_path / "caller-tmp")
    left_log = tmp_path / "left-behind.txt"

    result = copies.first.lint(
        copies.base,
        TMPDIR=str(caller_tmp),
        FAKE_CODEX_LLMLINT_LEAVE_TEMPORARY=str(left_log),
    )

    report = result.stdout + result.stderr
    assert result.returncode == 0, report
    left = [Path(line) for line in left_log.read_text(encoding="utf-8").splitlines()]
    assert left, report
    for path in left:
        assert path.name.startswith("codex-bwrap-synthetic-mount-targets-"), left
        assert path.parent.parent == caller_tmp, left
    assert list(caller_tmp.iterdir()) == [], report


def test_a_pass_judged_in_one_copy_replays_in_another_at_a_different_path(
    copies: Copies,
) -> None:
    summary = _judged(copies.first, copies.base)
    second = copies.make("second")
    assert second.root != copies.first.root

    assert _replayed(second, copies.base) == summary


def test_each_base_commit_is_judged_and_then_replays_its_own_pass(copies: Copies) -> None:
    workspace = copies.first
    _judged(workspace, copies.base)
    # A second base: the base commit with one more commit on it, which the change is not on.
    workspace.git("branch", "other-base", copies.base)
    workspace.git("checkout", "-q", "other-base")
    workspace.edit("README.md", lambda text: text + "\nAnother base.\n")
    other = workspace.commit("another base")
    workspace.git("checkout", "-q", "main")

    _judged(workspace, other)
    _replayed(workspace, copies.base)
    _replayed(workspace, other)


def test_a_changed_rule_is_judged_again(copies: Copies) -> None:
    workspace = copies.first
    _judged(workspace, copies.base)
    before = workspace.fingerprint()
    workspace.edit("llmlint.yml", lambda text: text.replace("POSIX-safe", "portable"))
    workspace.commit("reword a rule")

    assert workspace.fingerprint() != before
    _judged(workspace, copies.base)
    _replayed(workspace, copies.base)


def test_a_changed_rule_in_a_plugin_outside_the_tree_is_judged_again(copies: Copies) -> None:
    """No tracked file moves, so only the `llmlint fingerprint` input can miss the cache."""
    workspace = copies.first
    _judged(workspace, copies.base)
    before = workspace.fingerprint()
    copies.plugin.write_text(PLUGIN.replace("documented", "named in the README"), "utf-8")

    assert workspace.fingerprint() != before
    _judged(workspace, copies.base)
    _replayed(workspace, copies.base)


@pytest.mark.parametrize("config", ["oneharness.llmlint.toml", "oneharness.identities.toml"])
def test_a_changed_judge_model_is_judged_again(copies: Copies, config: str) -> None:
    """A model moved in the role file, or in the parent it `extends`, keys a new judge."""
    workspace = copies.first
    _judged(workspace, copies.base)
    before = workspace.fingerprint()
    if config == "oneharness.llmlint.toml":
        # The role's own codex model, declared beside its arguments.
        workspace.edit(
            config,
            lambda text: text.replace(
                "[harness.codex]\n", '[harness.codex]\nmodel = "a-new-model"\n', 1
            ),
        )
    else:
        workspace.edit(
            config,
            lambda text: re.sub(r'(?m)^model = "[^"]*"', 'model = "a-new-model"', text, count=1),
        )
    workspace.commit(f"move a model in {config}")

    assert workspace.fingerprint() != before
    _judged(workspace, copies.base)


def test_a_changed_tracked_file_is_judged_again(copies: Copies) -> None:
    workspace = copies.first
    _judged(workspace, copies.base)
    workspace.edit("README.md", lambda text: text + "\nA tracked change.\n")
    workspace.commit("a tracked change")

    _judged(workspace, copies.base)


def test_a_failing_verdict_fails_with_its_finding_and_is_judged_again(copies: Copies) -> None:
    """No terminal, `CI` and an agent's marker set: the finding still reaches the caller."""
    workspace = copies.first
    failing = {RULES[0]: {"file": CHANGED, "line": 1, "message": "the stand-in's scripted finding"}}
    overrides = {"FAKE_CODEX_LLMLINT_FAIL": json.dumps(failing), "CI": "true", "CLAUDECODE": "1"}

    for attempt in (1, 2):
        result = workspace.lint(copies.base, **overrides)
        report = result.stdout + result.stderr
        assert result.returncode != 0, report
        assert RULES[0] in report, report
        assert CHANGED in report, report
        assert "the stand-in's scripted finding" in report, report
        assert workspace.judge_calls() == attempt, report
        assert REPLAYED not in report, report


@pytest.mark.parametrize("omitted", [["*"], [RULES[1]]], ids=["none-answered", "some-answered"])
def test_a_reply_leaving_rules_unanswered_fails_naming_them_and_is_judged_again(
    copies: Copies, omitted: list[str]
) -> None:
    workspace = copies.first
    unanswered = RULES if omitted == ["*"] else tuple(omitted)
    overrides = {"FAKE_CODEX_LLMLINT_OMIT": json.dumps(omitted)}

    for _ in (1, 2):
        before = workspace.judge_calls()
        result = workspace.lint(copies.base, **overrides)
        report = result.stdout + result.stderr
        assert result.returncode != 0, report
        for rule in unanswered:
            assert rule in report, report
        assert workspace.judge_calls() > before, report
        assert REPLAYED not in report, report


def test_a_base_that_does_not_resolve_is_refused_before_any_judge_call(copies: Copies) -> None:
    result = copies.first.lint("no-such-base")

    assert result.returncode != 0
    refusal = "lint-llm-diff: 'no-such-base' does not resolve to a commit"
    assert refusal in result.stderr.splitlines(), result.stderr
    assert copies.first.judge_calls() == 0


def test_a_base_its_origin_ref_has_moved_past_is_refused_before_any_judge_call(
    copies: Copies,
) -> None:
    workspace = copies.first
    head = workspace.git("rev-parse", "HEAD").strip()
    workspace.git("branch", "stale", copies.base)
    workspace.git("update-ref", "refs/remotes/origin/stale", head)

    result = workspace.lint("stale")

    assert result.returncode != 0
    assert "so judging it would judge commits this branch does not carry" in result.stderr, (
        result.stderr
    )
    assert workspace.judge_calls() == 0


def test_the_gate_judges_against_the_comparison_base_and_replays_another_copys_pass(
    copies: Copies,
) -> None:
    """`ONEVCS_COMPARISON_*` names the base; a pass recorded against it in another copy replays."""
    recorder = copies.make("recorder")
    gate = copies.make("gate")
    # The branch the gate compares against, as the origin's, at the base commit.
    copies.first.git("update-ref", "refs/remotes/origin/release", copies.base)

    _judged(recorder, "origin/release", copies.base)
    before = gate.judge_calls()

    log = gate.gate_llmlint("origin", "release")

    assert f"lint-llm-diff: judging this diff against base {copies.base}" in log, log
    assert REPLAYED in log, log
    assert gate.judge_calls() == before, log


class Identity(NamedTuple):
    """One alternate identity, as a host exporting no identity variable resolves it."""

    #: The provider binary its variant spawns.
    provider: str
    #: The indirection its variant maps into that child.
    mapped: str
    #: Its directory under `HOME`, as the shared resolvers derive it.
    leaf: str


#: Each identity, keyed by the variable the shared resolvers export for it.
IDENTITY_DIRECTORIES = {
    "ORCHESTRATOR_CODEX_ALT_HOME": Identity("codex", "CODEX_HOME", ".codex-alt"),
    "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": Identity("claude", "CLAUDE_CONFIG_DIR", ".claude-alt"),
    "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR": Identity("claude", "CLAUDE_CONFIG_DIR", ".claude-alt2"),
    "ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR": Identity(
        "claude", "CLAUDE_CONFIG_DIR", ".claude-primary-backup"
    ),
    "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR": Identity("claude", "CLAUDE_CONFIG_DIR", ".claude"),
}
#: The labels a dispatch's caller hands down, which the judge's history has to keep.
INHERITED_LABELS = {"onepipeline.run_id": "caller-run", "onepipeline.node": "caller-node"}


def test_every_identity_directory_reaches_its_child_and_the_judge_session_is_labelled(
    copies: Copies, tmp_path: Path
) -> None:
    """The real oneharness walks the whole chain under the identities the recipe exported.

    The host exports no identity variable, so the shared resolvers derive each directory
    from `HOME`; every provider refuses as a never-logged-in identity except the primary
    Claude one, which answers. Each child records what it was handed, and the judge
    session's history keeps `role=llmlint` beside the labels its caller set.
    """
    home = tmp_path / "home"
    for identity in IDENTITY_DIRECTORIES.values():
        (home / identity.leaf).mkdir(parents=True, exist_ok=True)
    recordings = tmp_path / "identity-recordings.jsonl"
    pointers = tmp_path / "oneharness-pointers.jsonl"
    environment = {
        name: value
        for name, value in copies.first.env.items()
        if not name.startswith("ORCHESTRATOR_")
    }
    environment |= {
        "HOME": str(home),
        # The host's own identities file must not reach the resolvers.
        "XDG_CONFIG_HOME": str(home / ".config"),
        # The recording stand-in is every provider, by the absolute seam every variant of
        # each family reaches.
        "ONEHARNESS_BIN_CODEX": str(IDENTITY_STAND_IN),
        "ONEHARNESS_BIN_CLAUDE_CODE": str(IDENTITY_STAND_IN),
        "IDENTITY_RECORDINGS": str(recordings),
        "IDENTITY_ANSWERING_DIR": str(home / ".claude"),
        "IDENTITY_FAKE_CODEX": str(FAKE_CODEX),
        "ONEHARNESS_HISTORY": "1",
        "ONEHARNESS_HISTORY_POINTER_FILE": str(pointers),
        "ONEHARNESS_HISTORY_LABELS": ",".join(f"{k}={v}" for k, v in INHERITED_LABELS.items()),
    }
    workspace = Workspace(copies.first.root, environment, copies.first.judge_log)

    result = workspace.lint(copies.base)

    report = result.stdout + result.stderr
    handed = recordings.read_text("utf-8") if recordings.exists() else "no provider was spawned"
    assert result.returncode == 0, f"{report}\nproviders were handed:\n{handed}"
    recorded = [json.loads(line) for line in recordings.read_text("utf-8").splitlines()]
    resolved = {name: str(home / each.leaf) for name, each in IDENTITY_DIRECTORIES.items()}
    for name, identity in IDENTITY_DIRECTORIES.items():
        assert any(
            entry["binary"] == identity.provider
            and entry["environment"].get(identity.mapped) == resolved[name]
            for entry in recorded
        ), f"no {identity.provider} child was handed {identity.mapped}={resolved[name]}: {recorded}"
    for entry in recorded:
        handed = {key: entry["environment"].get(key) for key in resolved}
        assert handed == resolved, entry
    _assert_judge_history_labelled(pointers)


def _assert_judge_history_labelled(pointers: Path) -> None:
    """The judge session's history record carries `role=llmlint` and the caller's labels."""
    answered = []
    for line in pointers.read_text("utf-8").splitlines():
        pointer = json.loads(line)
        history = Path(pointer["history_file"])
        for text in history.read_text("utf-8").splitlines():
            entry = json.loads(text)
            if entry.get("type") == "run" and entry.get("status") == "ok":
                answered.append(entry["labels"])
    assert answered, f"no judge session ran to a verdict: {pointers.read_text('utf-8')}"
    for labels in answered:
        assert labels.get("role") == "llmlint", labels
        assert {key: labels.get(key) for key in INHERITED_LABELS} == INHERITED_LABELS, labels
