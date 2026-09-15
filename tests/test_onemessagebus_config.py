"""`config/onemessagebus.yaml`, this host's messaging policy, held to the release and its sources.

The file is read by the installed `onemessagebus` release on every channel verb and by the
engine at every launch, so the first thing proven is that the release loads it: through
the command line's own `Config::load` and `Config::resolve`, beside a copy carrying one
unknown key that the same load refuses by name — without that control, a verb that
ignored `--config` would pass too. Then each value is held to the statement it comes from:
the monitor's grants to `AGENTS.md`, the reply window to the shim's default, the session
variable to the installed engine that owns its name, and the validator to the one in-repo
entry point.

The workspace installs no YAML library, so each value is read off the file's own lines,
with comments removed, the way this suite's other readers of YAML documents read them; the
release's load above is what holds the whole document well-formed.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest
from test_linked_libraries import ENGINE_DISTRIBUTION

from orchestrator.root import REPO_ROOT

CONFIG = REPO_ROOT / "config" / "onemessagebus.yaml"

#: A verdict alone: judged by no validator (it carries no commands), so a `validate` of it
#: answers what loading and resolving the configuration answered.
VERDICT = '{"version":3,"completion":true,"message":"main"}'


def _lines() -> str:
    """The document with every comment and blank line removed."""
    kept = []
    for line in CONFIG.read_text(encoding="utf-8").splitlines():
        stripped = re.sub(r"\s+#.*$", "", line) if not line.lstrip().startswith("#") else ""
        if stripped.strip():
            kept.append(stripped.rstrip())
    return "\n".join(kept) + "\n"


def _value(pattern: str) -> str:
    found = re.search(pattern, _lines(), re.MULTILINE)
    assert found is not None, f"config/onemessagebus.yaml states nothing matching {pattern!r}"
    return found.group(1)


def _bus(
    *arguments: str, config: Path, channel: Path, stdin: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["onemessagebus", *arguments, "--config", str(config), "--transport-dir", str(channel)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=REPO_ROOT,
    )


def test_the_release_loads_and_resolves_the_configuration(tmp_path: Path) -> None:
    """`validate` and `serve` both open the file through the release's own loader."""
    validated = _bus("validate", "replies", config=CONFIG, channel=tmp_path, stdin=VERDICT)
    assert validated.returncode == 0, validated.stderr
    assert json.loads(validated.stdout)["verdict"] == "pass"

    served = _bus(
        "serve", "surfaces", "--codec", "onejudge", config=CONFIG, channel=tmp_path, stdin=""
    )
    assert served.returncode == 0, served.stderr


def test_an_unknown_key_is_refused_by_the_same_load(tmp_path: Path) -> None:
    """The control: the verbs above really read the file, since a stray key fails them."""
    broken = tmp_path / "onemessagebus.yaml"
    text = CONFIG.read_text(encoding="utf-8")
    assert "reply_window_seconds:" in text
    broken.write_text(text.replace("reply_window_seconds:", "reply_window:"), encoding="utf-8")

    refused = _bus("validate", "replies", config=broken, channel=tmp_path, stdin=VERDICT)
    assert refused.returncode == 2, refused.stdout
    assert "reply_window" in refused.stderr


def test_the_channel_is_the_planner_channel_layout_over_the_runs_own_directory() -> None:
    """The engine refuses any other profile, and a `transport.dir` or `queues` block."""
    assert _value(r"^version: (\S+)$") == "1"
    assert _value(r"^profile: (\S+)$") == "planner-channel"
    assert _value(r"^transport: (.+)$") == "{kind: local}"
    assert re.search(r"^queues:", _lines(), re.MULTILINE) is None


@pytest.mark.reads_docs
def test_the_monitors_grants_are_the_allowlist_agents_md_states() -> None:
    """Exactly the ops `AGENTS.md` names for the monitor's edit author, and no others."""
    agents = " ".join((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8").split())
    stated = re.search(
        r"allowlist its edit author is bounded to — ((?:`\w+`(?:, | and )?)+)", agents
    )
    assert stated is not None, "AGENTS.md no longer names the monitor's allowlist"
    named = re.findall(r"`(\w+)`", stated.group(1))
    assert re.search(
        r"^authors:\n  monitor: \{capabilities: \[([^\]]*)\]\}\n(?! )", _lines(), re.MULTILINE
    )
    granted = _value(r"^  monitor: \{capabilities: \[([^\]]*)\]\}$")
    assert [op.strip() for op in granted.split(",")] == named


def test_the_reply_window_is_the_shims_own_default() -> None:
    """The codec's window and the shim's `--timeout` default are one decision."""
    shim = (REPO_ROOT / "scripts" / "ask-manager.sh").read_text(encoding="utf-8")
    default = re.search(r"^DEFAULT_TIMEOUT_SECONDS=(\d+)$", shim, re.MULTILINE)
    assert default is not None
    codec = _value(r"^codecs:\n  onejudge:\n((?:    .+\n)+)")
    assert codec.splitlines() == [
        "    queue: surfaces",
        "    asker_env: ONEPIPELINE_CHANNEL_ASKER",
        "    session_env: ONEPIPELINE_SERVE_SESSION_SECONDS",
        f"    reply_window_seconds: {default.group(1)}",
    ]


def _engine_binary() -> Path:
    """The `onepipeline` executable the adopted engine wheel installed on this host."""
    distribution = importlib.metadata.distribution(ENGINE_DISTRIBUTION)
    installed = [
        Path(str(distribution.locate_file(entry)))
        for entry in distribution.files or []
        if Path(entry).parent.name == "bin" and Path(entry).name == "onepipeline"
    ]
    assert installed, f"the {ENGINE_DISTRIBUTION} wheel installed no bin/onepipeline"
    return installed[0]


def test_the_session_variable_is_the_one_the_installed_engine_names() -> None:
    """`session_env` names the engine's variable, read off the engine this host runs.

    The name is `onepipeline`'s — its `channel serve` bounds a per-turn session by it,
    and the bus's codec takes the same word so a host bounding one bounds the other — and
    the engine documents it only in its source repository, so the installed binary is the
    one copy on this host to reconcile against. A release that renamed it would leave
    this configuration naming a variable nothing reads, and the codec's session unbounded.
    """
    named = _value(r"^    session_env: (\S+)$")
    engine = _engine_binary().resolve()
    # A boolean first, so a failure names the variable rather than dumping the executable.
    carried = named.encode() in engine.read_bytes()
    assert carried, (
        f"config/onemessagebus.yaml bounds the codec's session by {named}, which the "
        f"installed engine at {engine} no longer names; re-measure the engine's variable "
        "and move the configuration with it"
    )


def _serve(channel: Path, environment: dict[str, str]) -> subprocess.Popen[str]:
    """A judge side as the graph spawns one, with its frame stream held open."""
    return subprocess.Popen(  # noqa: S603 - the installed bus, as graphs/dag-scope.yaml execs it
        [
            "onemessagebus",
            "serve",
            "surfaces",
            "--codec",
            "onejudge",
            "--config",
            str(CONFIG),
            "--transport-dir",
            str(channel),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        cwd=REPO_ROOT,
    )


def test_the_codec_bounds_its_session_by_the_variable_this_configuration_names(
    tmp_path: Path,
) -> None:
    """Setting the named variable ends a session whose frame stream never closes.

    Beside a control that sets nothing and is still serving after the same wait, so the
    ending is the variable's doing rather than a session that stops on its own.
    """
    named = _value(r"^    session_env: (\S+)$")
    unbounded = {key: value for key, value in os.environ.items() if key != named}

    bounded = _serve(tmp_path / "bounded", {**unbounded, named: "1"})
    try:
        # A wait rather than `communicate`, which closes stdin, and a session whose frame
        # stream closes ends for that reason whatever its bound.
        bounded.wait(timeout=60)
    finally:
        bounded.kill()
    assert bounded.stderr is not None
    stderr = bounded.stderr.read()
    assert bounded.returncode == 0, stderr
    assert "reached its 1-second bound" in stderr, stderr

    control = _serve(tmp_path / "control", unbounded)
    try:
        time.sleep(3)
        assert control.poll() is None, (
            f"a session with no {named} stopped on its own, so the bound above proves "
            f"nothing:\n{control.communicate()[1]}"
        )
    finally:
        control.kill()
        control.communicate()


def test_every_reply_carrying_commands_is_judged_by_the_envelope_review_with_a_cache() -> None:
    """One validator, over commands, through the in-repo entry point, keyed on its bar."""
    validators = _value(r"^validators:\n((?:  .+\n)+)")
    assert validators.splitlines() == [
        "  - on: replies",
        "    when: {carries: commands}",
        "    kind: command",
        "    command: [scripts/envelope-review.sh]",
        "    cache:",
        "      dir: .cache/envelope-passes",
        "      bar_fingerprint: "
        "[uv, run, python, -m, orchestrator.envelope_review, --bar-fingerprint]",
    ]
    assert (REPO_ROOT / "scripts" / "envelope-review.sh").stat().st_mode & 0o111


def test_the_pass_cache_the_configuration_names_is_the_one_git_ignores() -> None:
    """Moving `cache.dir` in the configuration without `.gitignore` would commit pass records."""
    cache_dir = _value(r"^      dir: (\S+)$")
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert f"/{cache_dir}/" in ignored, (
        f"config/onemessagebus.yaml keeps envelope passes under {cache_dir}, which "
        f".gitignore does not ignore as /{cache_dir}/"
    )
