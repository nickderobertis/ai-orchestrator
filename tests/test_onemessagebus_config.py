"""`config/onemessagebus.yaml`, this host's messaging policy, held to the release and its sources.

The file is read by the installed `onemessagebus` release on every channel verb and by the
engine at every launch, so the first thing proven is that the release loads it: through
the command line's own `Config::load` and link resolution, beside a copy carrying one
unknown key that the same load refuses by name — without that control, a verb that
ignored `--config` would pass too. Then each value is held to the statement it comes from:
the monitor's author to `AGENTS.md` and to the reasons the bus gave before it stopped
naming the monitor, the reply window to the shim's default, the validator to the one
in-repo entry point, the frames link to the onejudge this host adopts, and the layout link
to the engine this host adopts — whose published planner-channel layout is where the
channel's queues now come from, since the adopted bus compiles no layout in.

The link is proven with no request leaving the host. Every copy here differs from the
committed file only in where its link points — at a bundle on disk, or at a loopback
server this module starts — and the frames bundle is derived from the installed onejudge
(`tests/onejudge_bundle.py`), never a copy somebody maintains. The layout document is the
one exception, because no installed artifact carries it: the suite reads the fixture
`tests/onejudge_bundle.py` names, and `tests/test_engine_contracts.py` holds it byte for
byte to the engine's own source at the adopted tag. The later comparison of the committed URLs
against what GitHub actually serves is session setup's warm step on a real host, which
reports each link with the version it stored.

The workspace installs no YAML library, so each value is read off the file's own lines,
with comments removed, the way this suite's other readers of YAML documents read them; the
release's load above is what holds the whole document well-formed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import monitor_conversation
import onejudge_bundle
import pytest
from onejudge_bundle import LoopbackOrigin

from orchestrator.root import REPO_ROOT

CONFIG = REPO_ROOT / "config" / "onemessagebus.yaml"
ONEJUDGE_VERSION = REPO_ROOT / "config" / "onejudge.version"
ONEPIPELINE_VERSION = REPO_ROOT / "config" / "onepipeline.version"

#: The four queues the engine's planner-channel layout declares and writes a run's channel
#: directory in (onepipeline's `docs/contract.md`, "The planner channel runs on
#: `onemessagebus`"), in the order the bus's `status` reports them.
ENGINE_QUEUES = ["command-outcomes", "commands", "replies", "surfaces"]

#: The bus this checkout pins, never one the ambient PATH offers first.
BUS = str(REPO_ROOT / ".venv" / "bin" / "onemessagebus")

#: A verdict alone: judged by no validator (it carries no commands), so a `validate` of it
#: answers what loading and resolving the configuration answered.
VERDICT = '{"version":3,"completion":true,"message":"main"}'

#: The binding the monitor's judge side serves, as graphs/dag-scope.yaml names it.
BINDING = "monitor"

#: The monitor's refused ops, each with the reason the bus's built-in profile gave it
#: before this host declared the monitor itself (onemessagebus 0.4.0's
#: `crates/onemessagebus-agent/src/channel.rs`), verbatim, so a refusal the monitor meets
#: reads exactly as it did.
REFUSED = {
    "complete": "whether the run is finished is the planner's verdict, not an observation",
    "attest": "a human action is attested by the person who took it, never by a watcher",
    "drop": "removing work from the graph is a decomposition decision the planner owns",
    "reparent": "rewiring dependencies is a decomposition decision the planner owns",
    "amend": "what a node is judged against is a decomposition decision the planner owns",
    "note": "a note may bind a criterion the node's judge decides against, which is the "
    "planner's decision rather than an observation",
    "settle": "settling a node from evidence declares an outcome this run never observed, "
    "which is the planner's decision rather than an observation",
}


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
        [BUS, *arguments, "--config", str(config), "--transport-dir", str(channel)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=REPO_ROOT,
    )


def test_the_release_loads_and_resolves_the_configuration(tmp_path: Path) -> None:
    """`validate` and `serve` both open the file, and its link, through the release."""
    validated = _bus("validate", "replies", config=CONFIG, channel=tmp_path, stdin=VERDICT)
    assert validated.returncode == 0, validated.stderr
    assert json.loads(validated.stdout)["verdict"] == "pass"

    served = _bus(
        "serve", "surfaces", "--codec", BINDING, config=CONFIG, channel=tmp_path, stdin=""
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
    granted = _value(r"^authors:\n  monitor:\n    capabilities: \[([^\]]*)\]$")
    assert [op.strip() for op in granted.split(",")] == named


def test_the_monitor_is_refused_every_other_op_for_the_reason_the_bus_once_gave(
    tmp_path: Path,
) -> None:
    """Each refused op reads back, through the release, with its reason in the channel's words.

    Beside the two controls that make the refusals mean something: a granted op by the
    same author passes the same load, and an author the file does not declare is refused
    for that — so the reasons below are the declaration's doing, not a channel refusing
    every monitor envelope.
    """
    refusals = re.search(r"^    refusals:\n((?:      .+\n)+)", _lines(), re.MULTILINE)
    assert refusals is not None, "config/onemessagebus.yaml declares no `authors.monitor.refusals`"
    assert [line.split(":", 1)[0].strip() for line in refusals.group(1).splitlines()] == list(
        REFUSED
    )
    for op, reason in REFUSED.items():
        envelope = {"version": 2, "author": "monitor", "commands": [{"op": op, "id": "research"}]}
        refused = _bus(
            "validate", "replies", config=CONFIG, channel=tmp_path, stdin=json.dumps(envelope)
        )
        assert refused.returncode == 1, refused.stdout
        assert (
            f"'{op}' is not an op the monitor may issue: {reason}. Surface it to the planner "
            "instead" in refused.stderr
        ), refused.stderr

    granted = {"version": 2, "author": "monitor", "commands": [{"op": "requeue", "id": "research"}]}
    passed = _bus("validate", "replies", config=CONFIG, channel=tmp_path, stdin=json.dumps(granted))
    assert passed.returncode == 0, passed.stderr

    stranger = {**granted, "author": "pacemaker"}
    unknown = _bus(
        "validate", "replies", config=CONFIG, channel=tmp_path, stdin=json.dumps(stranger)
    )
    assert unknown.returncode == 1, unknown.stdout
    assert "the envelope's author `pacemaker` is not declared" in unknown.stderr, unknown.stderr


def test_the_binding_opens_on_the_reply_window_and_the_engines_asker() -> None:
    """The binding's window is the one every question on `surfaces` waits.

    `onepipeline ask` — which `scripts/ask-manager.sh` runs — waits the
    `reply_window_seconds` the run's launch record carries for this queue when no
    `--timeout` names one, so this key is the one statement of the window a dispatched
    agent's question and the monitor binding's both wait.
    """
    binding = _value(rf"^codecs:\n  {BINDING}:\n((?:    .+\n)+)")
    lines = binding.splitlines()
    assert lines[:3] == [
        "    queue: surfaces",
        "    asker_env: ONEPIPELINE_CHANNEL_ASKER",
        "    session_env: ORCHESTRATOR_MONITOR_SESSION_SECONDS",
    ]
    window = re.fullmatch(r"    reply_window_seconds: (\d+)", lines[3])
    assert window is not None and int(window.group(1)) > 0, lines[3]
    assert lines[4:6] == ["    select: op", "    frames:"]


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


def _link() -> tuple[str, str]:
    """The committed frames link's location and its `@` pin."""
    location, pin = onejudge_bundle.frames_link().rsplit("@", 1)
    return location, pin


def _fetched_frames(report: str) -> dict[str, str]:
    """The one link of a `schemas fetch` report that is not the layout's."""
    (link,) = [
        link
        for link in json.loads(report)["links"]
        if not link["link"].rsplit("@", 1)[0].endswith(f"/{onejudge_bundle.LAYOUT_DOCUMENT}")
    ]
    return dict(link)


def test_the_link_names_the_adopted_onejudge_release_and_the_protocol_it_speaks() -> None:
    """The tag is `config/onejudge.version`'s and the pin is the installed release's protocol.

    Contract G's URL form, read off onejudge's own `docs/protocol.md`: the bundle a release
    publishes sits at its tag, and a consumer pins the protocol version that release
    speaks. A pin file moved without the link would leave the judge side validating
    frames against the release before it; a pin that disagreed with the protocol would be
    refused on the first frame of every run.
    """
    location, pin = _link()
    adopted = ONEJUDGE_VERSION.read_text(encoding="utf-8").strip()
    assert location == (
        "https://raw.githubusercontent.com/nickderobertis/onejudge/"
        f"v{adopted}/schemas/judge-seat-frames.json"
    ), location
    assert pin == onejudge_bundle.protocol(), (
        f"the link pins @{pin}, and the installed onejudge {adopted} speaks protocol "
        f"{onejudge_bundle.protocol()}"
    )


def _checked(frame: str, schema: str, config: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BUS, "schema", "check", schema, "--config", str(config)],
        input=frame,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=REPO_ROOT,
    )


def test_a_supervisor_frame_onejudge_writes_validates_through_the_pinned_link(
    tmp_path: Path,
) -> None:
    """The frame the binding branches on is one the pinned bundle admits, as onejudge writes it.

    The frame is recorded off a real conversation the installed onejudge held; the bundle
    is derived from the same release; and the bus resolves it through a `file://` link
    carrying the committed pin, so a pin the bundle does not admit is refused here before
    any frame reaches a run. A copy of that frame without its `turn` — the field a lost
    turn is told by — is refused by the same check, which is what makes the pass mean
    something.
    """
    _, pin = _link()
    bundle = onejudge_bundle.write_bundle(tmp_path / "judge-seat-frames.json")
    config = onejudge_bundle.relinked_copy(
        tmp_path / "onemessagebus.yaml", f"file://{bundle}@{pin}"
    )
    frame = monitor_conversation.recorded_frame(
        "supervisor", tmp_path / "recorded", dict(os.environ)
    )
    assert json.loads(frame)["turn"] == {"outcome": "taken"}, frame

    schema = f"agent.onejudge-frame.supervisor@{pin}"
    accepted = _checked(frame, schema, config)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    without_turn = {key: value for key, value in json.loads(frame).items() if key != "turn"}
    refused = _checked(json.dumps(without_turn), schema, config)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert '"turn" is a required property' in refused.stderr + refused.stdout


@pytest.fixture
def origin() -> Iterator[LoopbackOrigin]:
    served = LoopbackOrigin(json.dumps(onejudge_bundle.bundle()).encode())
    yield served
    served.server.shutdown()


def _fetch(config: Path, cache: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BUS, "schemas", "fetch", "--config", str(config), "--format", "json"],
        env={**os.environ, onejudge_bundle.CACHE_DIR_ENV: str(cache)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_a_file_link_with_this_hosts_pin_reads_the_derived_bundle(tmp_path: Path) -> None:
    """A `file://` link is read on every resolution, its pin still asserted, nothing cached."""
    _, pin = _link()
    bundle = onejudge_bundle.write_bundle(tmp_path / "judge-seat-frames.json")
    config = onejudge_bundle.relinked_copy(
        tmp_path / "onemessagebus.yaml", f"file://{bundle}@{pin}"
    )
    cache = tmp_path / "cache"

    read = _fetch(config, cache)

    assert read.returncode == 0, read.stderr
    link = _fetched_frames(read.stdout)
    assert (link["outcome"], link["version"]) == ("read", onejudge_bundle.protocol()), link


def test_this_hosts_pin_fetches_into_an_empty_cache_and_revalidates_the_entry(
    tmp_path: Path, origin: LoopbackOrigin
) -> None:
    """The first fetch stores the bundle under its declared version; the second revalidates it."""
    _, pin = _link()
    config = onejudge_bundle.relinked_copy(tmp_path / "onemessagebus.yaml", f"{origin.url}@{pin}")
    cache = tmp_path / "cache"

    first = _fetch(config, cache)
    assert first.returncode == 0, first.stderr
    fetched = _fetched_frames(first.stdout)
    assert (fetched["outcome"], fetched["version"]) == ("fetched", onejudge_bundle.protocol())
    listed = subprocess.run(
        [BUS, "schemas", "--format", "json"],
        env={**os.environ, onejudge_bundle.CACHE_DIR_ENV: str(cache)},
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert [(entry["url"], entry["version"]) for entry in json.loads(listed.stdout)["entries"]] == [
        (origin.url, onejudge_bundle.protocol())
    ], listed.stdout

    second = _fetch(config, cache)
    assert second.returncode == 0, second.stderr
    confirmed = _fetched_frames(second.stdout)
    assert (confirmed["outcome"], confirmed["version"]) == ("confirmed", onejudge_bundle.protocol())
    assert origin.requests == [None, LoopbackOrigin.ETAG], (
        f"the second fetch was not a conditional request against the stored entry: "
        f"{origin.requests}"
    )


def test_a_bundle_declaring_a_version_this_hosts_pin_does_not_admit_is_refused(
    tmp_path: Path,
) -> None:
    """A bundle from the next protocol is a hard error naming the link, never a fallback."""
    _, pin = _link()
    later = str(int(pin) + 1)
    bundle = onejudge_bundle.write_bundle(tmp_path / "judge-seat-frames.json", version=later)
    link = f"file://{bundle}@{pin}"
    config = onejudge_bundle.relinked_copy(tmp_path / "onemessagebus.yaml", link)

    refused = _fetch(config, tmp_path / "cache")

    assert refused.returncode == 2, refused.stdout
    assert link in refused.stderr + refused.stdout, refused.stderr
    assert f"the bundle declares version {later}, which the pin @{pin} does not admit" in (
        refused.stderr + refused.stdout
    )


def test_the_layout_link_names_the_adopted_engine_release_and_the_version_it_declares() -> None:
    """The tag is `config/onepipeline.version`'s and the pin admits what the document declares.

    The URL form is the one onepipeline's `docs/contract.md` states for a host's bus. The
    engine keeps running the layout it compiles in, so a tag behind the engine pin would
    leave this host's channel verbs reading and writing the channel directory under an
    older engine's layout while every dispatch ran the newer one.
    """
    location, pin = onejudge_bundle.layout_link().rsplit("@", 1)
    adopted = ONEPIPELINE_VERSION.read_text(encoding="utf-8").strip()
    assert location == (
        "https://raw.githubusercontent.com/nickderobertis/onepipeline/"
        f"v{adopted}/schemas/planner-channel.json"
    ), location
    declared = json.loads(onejudge_bundle.ENGINE_LAYOUT.read_text(encoding="utf-8"))["version"]
    assert declared.split(".")[0] == pin, (
        f"the layout link pins @{pin}, and the document the engine publishes declares {declared}"
    )


def test_the_adopted_bus_resolves_the_configuration_to_the_four_queues_the_engine_writes(
    tmp_path: Path,
) -> None:
    """Through the linked layout, and through nothing else: without the link there is none.

    `status` over an empty channel directory reports every queue the resolved layout
    declares. The control is the same file with its layout link removed, which the adopted
    bus refuses by naming the profile — so the queues above come from the engine's document
    rather than from a layout the bus still compiled in.
    """
    resolved = subprocess.run(
        [BUS, "status", "--config", str(CONFIG), "--transport-dir", str(tmp_path / "channel")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=REPO_ROOT,
    )
    assert resolved.returncode == 0, resolved.stderr
    assert [queue["queue"] for queue in json.loads(resolved.stdout)] == ENGINE_QUEUES

    unlinked = tmp_path / "onemessagebus.yaml"
    layout = onejudge_bundle.layout_link()
    text = CONFIG.read_text(encoding="utf-8")
    assert text.count(f'  - "{layout}"\n') == 1
    unlinked.write_text(text.replace(f'  - "{layout}"\n', ""), encoding="utf-8")
    refused = subprocess.run(
        [BUS, "status", "--config", str(unlinked), "--transport-dir", str(tmp_path / "other")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=REPO_ROOT,
    )
    assert "`planner-channel` is not a layout this build links" in refused.stderr, (
        refused.stdout + refused.stderr
    )
    assert refused.stdout.strip() == "", refused.stdout
