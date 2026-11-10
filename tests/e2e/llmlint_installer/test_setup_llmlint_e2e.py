"""E2E proof that `scripts/setup-llmlint.sh` installs the release the `oneharness` pin admits.

The ceiling is a consequence of `config/oneharness.version`, handed to `uv`'s resolver
rather than written as a version, so what is proven is the resolver's answer through
the real script: the highest release at or above the floor whose declared
`oneharness-cli` requirement admits the pin, never a newer one that does not; and, when
nothing admits it or the pin cannot be read, exit 0 with the installed `llmlint` left
alone and the reason logged.

llmlint: ignore-file[e2e_not_mocked] The one boundary replaced is the paid network
fetch: the index is a `file://` PEP 503 directory of real wheels this journey builds,
each declaring a `Requires-Dist` chosen against the pin the checkout really names.
Everything above it is real — the script, the `uv tool install` it runs and its
resolver, the tool directory and shim it installs — and that directory is a throwaway
one `uv`'s own `UV_TOOL_DIR` / `UV_TOOL_BIN_DIR` name, so the host's install is never
touched.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge, shell_test_tiers_stay_split] These
journeys sit behind `llmlint-installer:test`, a project of their own keyed on the
installer, the pin and this directory. Its one inherited edge, `support-conftest`, is the
suite's root `tests/conftest.py`, which pytest loads for every project under `tests/`, so
no project here can run without it.

"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from llmlint_install import (
    INSTALLER,
    LLMLINT_DISTRIBUTION,
    ONEHARNESS_DISTRIBUTION,
    declared_oneharness_requirement,
    installed_llmlint,
    installer_floor,
    pinned_oneharness,
)
from packaging.version import Version

#: The installer's floor, read off the script so a release below it can be offered and
#: refused for the floor the script really declares.
FLOOR = installer_floor()
#: A higher admissible release and a still newer incompatible release, relative to the floor.
ADMISSIBLE = Version(f"{FLOOR}.1")
INCOMPATIBLE = Version(f"{FLOOR.major}.{FLOOR.minor}.{FLOOR.micro + 1}")
#: The executable the fake `llmlint-cli` wheels provide, as the real one does: `uv
#: tool install` refuses a package that provides none, and the installer runs it.
ENTRY_POINT = "llmlint"


@dataclass(frozen=True)
class Release:
    """One release a fake index offers: its version and what it declares it needs."""

    distribution: str
    version: str
    requires: tuple[str, ...] = ()


def _wheel(directory: Path, release: Release) -> Path:
    """Build a minimal pure-Python wheel: metadata, and a console script if `llmlint`."""
    name = release.distribution.replace("-", "_")
    dist_info = f"{name}-{release.version}.dist-info"
    members: dict[str, str] = {}
    if release.distribution == LLMLINT_DISTRIBUTION:
        module = f"{name}_fake"
        members[f"{module}.py"] = (
            "import sys\n"
            "\n"
            "\n"
            "def main() -> None:\n"
            f"    print('{ENTRY_POINT} {release.version}' if '--version' in sys.argv "
            "else 'doctor: ok')\n"
        )
        members[f"{dist_info}/entry_points.txt"] = (
            f"[console_scripts]\n{ENTRY_POINT} = {module}:main\n"
        )
    members[f"{dist_info}/METADATA"] = "".join(
        [
            "Metadata-Version: 2.1\n",
            f"Name: {release.distribution}\n",
            f"Version: {release.version}\n",
            *(f"Requires-Dist: {requirement}\n" for requirement in release.requires),
        ]
    )
    members[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    path = directory / f"{name}-{release.version}-py3-none-any.whl"
    record: list[str] = []
    with zipfile.ZipFile(path, "w") as wheel:
        for member, text in members.items():
            data = text.encode("utf-8")
            wheel.writestr(member, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
            record.append(f"{member},sha256={digest.decode('ascii')},{len(data)}")
        record.append(f"{dist_info}/RECORD,,")
        wheel.writestr(f"{dist_info}/RECORD", "\n".join(record) + "\n")
    return path


def _index(root: Path, releases: tuple[Release, ...]) -> str:
    """A PEP 503 simple index under `root`, offering exactly `releases`; its URL."""
    wheels = root / "wheels"
    wheels.mkdir(parents=True)
    simple = root / "simple"
    simple.mkdir()
    by_project: dict[str, list[Path]] = {}
    for release in releases:
        by_project.setdefault(release.distribution, []).append(_wheel(wheels, release))
    (simple / "index.html").write_text(
        "<html><body>"
        + "".join(f'<a href="{project}/">{project}</a>' for project in by_project)
        + "</body></html>",
        encoding="utf-8",
    )
    for project, paths in by_project.items():
        page = simple / project
        page.mkdir()
        page.joinpath("index.html").write_text(
            "<html><body>"
            + "".join(f'<a href="../../wheels/{path.name}">{path.name}</a>' for path in paths)
            + "</body></html>",
            encoding="utf-8",
        )
    return simple.as_uri()


@dataclass(frozen=True)
class ToolHome:
    """A throwaway `uv tool` home: where installs go, and the environment naming it."""

    tool_dir: Path
    bin_dir: Path
    cache_dir: Path
    #: A real PATH holding the real `uv` and the system directories, and not the
    #: host's `~/.local/bin`: that is where the host's own `llmlint` shim lives, and
    #: the installer's closing lines run whichever `llmlint` PATH resolves — which
    #: has to be the one it just installed, or nothing.
    path: str

    def environment(self, index_url: str) -> dict[str, str]:
        """`uv`'s own indirections, over the host's environment.

        `UV_PYTHON` names this interpreter's base so the tool venv is built from a
        Python the host already has, with downloads off: the network is the boundary
        this journey replaces, and a `uv` reaching for one would be reaching past it.
        """
        return {
            **os.environ,
            "PATH": self.path,
            "UV_TOOL_DIR": str(self.tool_dir),
            "UV_TOOL_BIN_DIR": str(self.bin_dir),
            "UV_CACHE_DIR": str(self.cache_dir),
            "UV_DEFAULT_INDEX": index_url,
            "UV_PYTHON": str(Path(sys.base_prefix) / "bin" / "python3"),
            "UV_PYTHON_DOWNLOADS": "never",
            # A tool dir on another filesystem from the cache cannot be hardlinked, and
            # the warning `uv` prints about it is noise beside the installer's own log.
            "UV_LINK_MODE": "copy",
        }


@pytest.fixture
def tool_home(tmp_path: Path) -> ToolHome:
    uv = shutil.which("uv")
    assert uv is not None
    real_tools = tmp_path / "real-tools"
    real_tools.mkdir()
    (real_tools / "uv").symlink_to(Path(uv).resolve())
    home = ToolHome(
        tmp_path / "tools", tmp_path / "bin", tmp_path / "uv-cache", f"{real_tools}:/usr/bin:/bin"
    )
    for directory in (home.tool_dir, home.bin_dir, home.cache_dir):
        directory.mkdir()
    return home


def _run_installer(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(script)], text=True, capture_output=True, env=env)


def _above(pin: Version) -> Version:
    """A `oneharness-cli` release the pin does not reach: the next minor."""
    return Version(f"{pin.major}.{pin.minor + 1}.0")


def _below_floor() -> Version:
    """A `llmlint-cli` release under the installer's floor: the minor before it."""
    return Version(f"{FLOOR.major}.{FLOOR.minor - 1}.0")


def _offer(pin: Version) -> tuple[Release, ...]:
    """Several admissible releases, a newer one the pin does not admit, one below the floor.

    `ADMISSIBLE` admits exactly the pin, so it is the highest the rule allows.
    """
    above = _above(pin)
    return (
        Release(LLMLINT_DISTRIBUTION, str(_below_floor())),
        Release(LLMLINT_DISTRIBUTION, str(FLOOR), (f"{ONEHARNESS_DISTRIBUTION}>={pin}",)),
        Release(LLMLINT_DISTRIBUTION, str(ADMISSIBLE), (f"{ONEHARNESS_DISTRIBUTION}>={pin}",)),
        Release(LLMLINT_DISTRIBUTION, str(INCOMPATIBLE), (f"{ONEHARNESS_DISTRIBUTION}>={above}",)),
        Release(ONEHARNESS_DISTRIBUTION, str(pin)),
        Release(ONEHARNESS_DISTRIBUTION, str(above)),
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="the installer runs `uv tool install`")
def test_the_installer_selects_the_highest_release_the_pin_admits(
    tmp_path: Path, tool_home: ToolHome
) -> None:
    """Several releases admit the pin, a newer one does not: the highest admissible wins."""
    pin = pinned_oneharness()
    env = tool_home.environment(_index(tmp_path / "index", _offer(pin)))

    completed = _run_installer(INSTALLER, env)

    assert completed.returncode == 0, completed.stderr
    installed = installed_llmlint(tool_home.tool_dir)
    assert installed is not None, completed.stderr
    assert installed.version == str(ADMISSIBLE), (
        f"installed {installed.version} where {ADMISSIBLE} is the highest release at or above "
        f"{FLOOR} admitting {ONEHARNESS_DISTRIBUTION} {pin}:\n{completed.stderr}"
    )
    # The rule itself, read the way the host drift gate reads it: the installed
    # release's own `Requires-Dist` admits the pin.
    assert declared_oneharness_requirement(installed).specifier.contains(pin)
    assert str(pin) in completed.stderr, completed.stderr
    # The shim `uv` linked is the release that was installed, and the installer's
    # closing line reports it, so a session log says which llmlint the host carries.
    shim = subprocess.run(
        [str(tool_home.bin_dir / ENTRY_POINT), "--version"],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert shim.stdout.strip() == f"{ENTRY_POINT} {ADMISSIBLE}"
    assert f"ready ({ENTRY_POINT}: {ENTRY_POINT} {ADMISSIBLE})" in completed.stderr, (
        completed.stderr
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="the installer runs `uv tool install`")
def test_the_installer_leaves_the_host_alone_when_no_release_admits_the_pin(
    tmp_path: Path, tool_home: ToolHome
) -> None:
    """Nothing offered admits the pin: exit 0, say what was found, change nothing.

    The prior install is a `llmlint-cli` requiring an `oneharness-cli` above the pin —
    the state an uncapped installer leaves — so the run neither breaks it nor fails. The
    one release admitting the pin sits below the floor, so the floor is proven to hold
    at the same time: admitting the pin is not enough.
    """
    pin = pinned_oneharness()
    above = _above(pin)
    listing = (
        Release(LLMLINT_DISTRIBUTION, str(_below_floor())),
        Release(LLMLINT_DISTRIBUTION, str(INCOMPATIBLE), (f"{ONEHARNESS_DISTRIBUTION}>={above}",)),
        Release(ONEHARNESS_DISTRIBUTION, str(pin)),
        Release(ONEHARNESS_DISTRIBUTION, str(above)),
    )
    env = tool_home.environment(_index(tmp_path / "index", listing))
    subprocess.run(
        ["uv", "tool", "install", f"{LLMLINT_DISTRIBUTION}=={INCOMPATIBLE}"],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    receipt = tool_home.tool_dir / LLMLINT_DISTRIBUTION / "uv-receipt.toml"
    before = receipt.read_text(encoding="utf-8")

    completed = _run_installer(INSTALLER, env)

    assert completed.returncode == 0, completed.stderr
    assert (
        f"the release query for {LLMLINT_DISTRIBUTION} >= {FLOOR} under "
        f"{ONEHARNESS_DISTRIBUTION} {pin} failed or answered nothing; leaving the installed "
        f"{LLMLINT_DISTRIBUTION} {INCOMPATIBLE} as it is"
    ) in completed.stderr, completed.stderr
    # What was found: uv's own resolution names the requirement nothing offered met.
    assert f"{ONEHARNESS_DISTRIBUTION}>={above}" in completed.stderr, completed.stderr
    installed = installed_llmlint(tool_home.tool_dir)
    assert installed is not None and installed.version == str(INCOMPATIBLE), completed.stderr
    assert receipt.read_text(encoding="utf-8") == before
    assert f"ready ({ENTRY_POINT}: {ENTRY_POINT} {INCOMPATIBLE})" in completed.stderr, (
        completed.stderr
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="the installer runs `uv tool install`")
def test_the_installer_installs_nothing_when_the_pin_cannot_be_read(
    tmp_path: Path, tool_home: ToolHome
) -> None:
    """With no pin to derive a ceiling from there is no rule to install under.

    The script reads the pin beside itself, so a copy in a checkout carrying no
    `config/oneharness.version` names the file and installs nothing.
    """
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    script = checkout / "scripts" / INSTALLER.name
    shutil.copy2(INSTALLER, script)
    env = tool_home.environment(_index(tmp_path / "index", _offer(pinned_oneharness())))

    completed = _run_installer(script, env)

    assert completed.returncode == 0, completed.stderr
    assert (
        f"cannot read the oneharness pin at {checkout / 'config' / 'oneharness.version'}"
        in completed.stderr
    ), completed.stderr
    assert installed_llmlint(tool_home.tool_dir) is None
    assert not (tool_home.tool_dir / LLMLINT_DISTRIBUTION).exists()
    assert f"{ENTRY_POINT} not installed" in completed.stderr, completed.stderr


@pytest.mark.skipif(shutil.which("uv") is None, reason="the installer runs `uv tool install`")
def test_the_installer_installs_nothing_when_the_pin_is_not_a_release(
    tmp_path: Path, tool_home: ToolHome
) -> None:
    """A pin that reads but is not a version is reported as what it reads, and nothing runs."""
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "config").mkdir()
    script = checkout / "scripts" / INSTALLER.name
    shutil.copy2(INSTALLER, script)
    (checkout / "config" / "oneharness.version").write_text("v0.12.1-rc\n", encoding="utf-8")
    env = tool_home.environment(_index(tmp_path / "index", _offer(pinned_oneharness())))

    completed = _run_installer(script, env)

    assert completed.returncode == 0, completed.stderr
    assert (
        f"the oneharness pin at {checkout / 'config' / 'oneharness.version'} reads "
        "'v0.12.1-rc', which is not a release"
    ) in completed.stderr, completed.stderr
    assert installed_llmlint(tool_home.tool_dir) is None
    assert not (tool_home.tool_dir / LLMLINT_DISTRIBUTION).exists()
    assert f"{ENTRY_POINT} not installed" in completed.stderr, completed.stderr


@dataclass(frozen=True)
class PathStamp:
    """What a removal and recreation of one path changes: its inode and its mtime."""

    inode: int
    mtime_ns: int


def _install_fingerprint(home: ToolHome) -> dict[Path, PathStamp]:
    """Every path of the llmlint link and tool environment, stamped.

    `uv tool install --upgrade` removes and recreates both even when the release does
    not change, so a run that leaves them alone leaves every stamp here as it was.
    """
    environment = home.tool_dir / LLMLINT_DISTRIBUTION
    paths = [home.bin_dir / ENTRY_POINT, environment, *environment.rglob("*")]
    return {
        path: PathStamp(stat.st_ino, stat.st_mtime_ns)
        for path, stat in ((path, path.lstat()) for path in paths)
    }


@dataclass(frozen=True)
class WatchedRuns:
    """Installer runs started at once, and how often the link was polled and found missing."""

    completed: tuple[subprocess.CompletedProcess[str], ...]
    polls: int
    misses: int

    @property
    def logs(self) -> str:
        return "\n---\n".join(run.stderr for run in self.completed)


def _watch_runs(script: Path, env: dict[str, str], link: Path, count: int = 1) -> WatchedRuns:
    """Start `count` installer runs at once and poll `link` until every one has ended."""
    processes = [
        subprocess.Popen(
            ["bash", str(script)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        for _ in range(count)
    ]
    polls = misses = 0
    while any(process.poll() is None for process in processes):
        polls += 1
        if not os.path.lexists(link):
            misses += 1
    completed = []
    for process in processes:
        stdout, stderr = process.communicate()
        completed.append(
            subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        )
    return WatchedRuns(tuple(completed), polls, misses)


def _uv_tool_install(env: dict[str, str], *arguments: str) -> None:
    """Arrange the starting install through uv rather than by writing files.

    The installer judges an install current from the receipt line `uv tool list
    --show-with` prints, so only a receipt uv wrote is one the installer reads as a host
    would; a hand-built tool directory would test a layout uv never produces.
    """
    subprocess.run(
        ["uv", "tool", "install", *arguments], text=True, capture_output=True, check=True, env=env
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="the installer runs `uv tool install`")
def test_the_installer_replaces_a_stale_release_and_leaves_a_current_one_alone(
    tmp_path: Path, tool_home: ToolHome
) -> None:
    """A run over a stale install installs the selected release; a second leaves it be.

    The second run is the one every session start makes on a current host: it must not
    remove and recreate the `llmlint` link, because a gate running in another checkout
    at that moment finds no `llmlint` command.
    """
    pin = pinned_oneharness()
    env = tool_home.environment(_index(tmp_path / "index", _offer(pin)))
    _uv_tool_install(
        env, f"{LLMLINT_DISTRIBUTION}=={FLOOR}", "--with", f"{ONEHARNESS_DISTRIBUTION}=={pin}"
    )
    link = tool_home.bin_dir / ENTRY_POINT

    first = _run_installer(INSTALLER, env)

    assert first.returncode == 0, first.stderr
    installed = installed_llmlint(tool_home.tool_dir)
    assert installed is not None and installed.version == str(ADMISSIBLE), first.stderr
    assert "already installed" not in first.stderr, first.stderr
    before = _install_fingerprint(tool_home)

    second = _watch_runs(INSTALLER, env, link)

    log = second.logs
    assert second.completed[0].returncode == 0, log
    assert second.polls > 0
    assert second.misses == 0, (
        f"{link} was missing on {second.misses} of {second.polls} polls:\n{log}"
    )
    assert _install_fingerprint(tool_home) == before, log
    assert (
        f"{LLMLINT_DISTRIBUTION} {ADMISSIBLE} under {ONEHARNESS_DISTRIBUTION} {pin} is "
        "already installed; leaving it as it is"
    ) in log, log
    assert f"ready ({ENTRY_POINT}: {ENTRY_POINT} {ADMISSIBLE})" in log, log
    print(f"first run:\n{first.stderr}\nsecond run ({second.polls} polls):\n{log}")


@pytest.mark.skipif(shutil.which("uv") is None, reason="the installer runs `uv tool install`")
def test_the_installer_reinstalls_a_selected_release_whose_link_is_gone(
    tmp_path: Path, tool_home: ToolHome
) -> None:
    """The selected release is in place but its `llmlint` link is not: reinstall it.

    Leaving it alone would leave a host with no `llmlint` command.
    """
    pin = pinned_oneharness()
    env = tool_home.environment(_index(tmp_path / "index", _offer(pin)))
    link = tool_home.bin_dir / ENTRY_POINT
    _uv_tool_install(
        env, f"{LLMLINT_DISTRIBUTION}=={ADMISSIBLE}", "--with", f"{ONEHARNESS_DISTRIBUTION}=={pin}"
    )
    link.unlink()

    completed = _run_installer(INSTALLER, env)

    assert completed.returncode == 0, completed.stderr
    assert "already installed" not in completed.stderr, completed.stderr
    assert link.exists(), completed.stderr
    listed = subprocess.run(
        ["uv", "tool", "list", "--show-with"], text=True, capture_output=True, check=True, env=env
    )
    assert (
        f"{LLMLINT_DISTRIBUTION} v{ADMISSIBLE} [with: {ONEHARNESS_DISTRIBUTION}=={pin}]"
        in listed.stdout.splitlines()
    ), listed.stdout
    assert f"ready ({ENTRY_POINT}: {ENTRY_POINT} {ADMISSIBLE})" in completed.stderr, (
        completed.stderr
    )


def _uv_config(directory: Path, pip_settings: str) -> Path:
    """A `uv.toml` whose `[pip]` table reaches the release query and not the install.

    `uv pip compile` is the installer's release query and `[pip]` settings apply to the
    `uv pip` interface alone, so real `uv` answers the query as an unreachable index or an
    answer written elsewhere would, while `uv tool install` still reaches the index.
    """
    path = directory / "uv.toml"
    path.write_text(f"[pip]\n{pip_settings}\n", encoding="utf-8")
    return path


#: Each way the selected release can already be on a host while a run cannot read it as
#: its own receipt: installed by another repository's installer with no `--with`, a
#: release query that fails, one that answers nothing — and, for each, runs overlapping.
#: The two query cases start from an install that does carry the pin, so the failed or
#: empty query is the only thing a run could read as not current.
LEFT_ALONE = {
    "installed-without-the-pin": "",
    "release-query-fails": "no-index = true",
    "release-query-answers-nothing": 'output-file = "{directory}/compiled.txt"',
}


@pytest.mark.skipif(shutil.which("uv") is None, reason="the installer runs `uv tool install`")
@pytest.mark.parametrize("runs", [1, 4], ids=["one-run", "overlapping-runs"])
@pytest.mark.parametrize("case", sorted(LEFT_ALONE))
def test_the_installer_never_touches_an_installed_selected_release(
    tmp_path: Path, tool_home: ToolHome, case: str, runs: int
) -> None:
    """The selected release is installed: no run removes or replaces its link.

    The selected release is installed either the way another repository's installer on
    the host leaves it — `uv tool install --upgrade llmlint-cli` with no `--with`, so the
    receipt carries no pin — or, for a release query that fails or answers nothing, with
    the pin in the receipt as this installer writes it. The link is polled for as long as
    any run lasts, then every path of the install is compared to what it was. A run that
    reinstalled would have removed and recreated the link, which a gate in another
    checkout meets as no `llmlint` command at all.
    """
    pin = pinned_oneharness()
    env = tool_home.environment(_index(tmp_path / "index", _offer(pin)))
    with_pin: tuple[str, ...] = (
        ()
        if case == "installed-without-the-pin"
        else ("--with", f"{ONEHARNESS_DISTRIBUTION}=={pin}")
    )
    _uv_tool_install(env, f"{LLMLINT_DISTRIBUTION}=={ADMISSIBLE}", *with_pin)
    settings = LEFT_ALONE[case]
    if settings:
        env["UV_CONFIG_FILE"] = str(_uv_config(tmp_path, settings.format(directory=tmp_path)))
    link = tool_home.bin_dir / ENTRY_POINT
    # Run once first, as a host's llmlint has been: the first run of the fake console
    # script writes its bytecode cache into the tool environment, which is no reinstall.
    subprocess.run([str(link), "--version"], capture_output=True, check=True, env=env)
    before = _install_fingerprint(tool_home)

    watched = _watch_runs(INSTALLER, env, link, runs)

    logs = watched.logs
    assert all(run.returncode == 0 for run in watched.completed), logs
    assert watched.polls > 0
    assert watched.misses == 0, (
        f"{link} was missing on {watched.misses} of {watched.polls} polls:\n{logs}"
    )
    assert _install_fingerprint(tool_home) == before, logs
    expected = (
        f"{LLMLINT_DISTRIBUTION} {ADMISSIBLE} under {ONEHARNESS_DISTRIBUTION} {pin} is "
        "already installed; leaving it as it is"
        if case == "installed-without-the-pin"
        else f"failed or answered nothing; leaving the installed {LLMLINT_DISTRIBUTION} "
        f"{ADMISSIBLE} as it is"
    )
    for run in watched.completed:
        assert expected in run.stderr, run.stderr
        assert "installing the highest" not in run.stderr, run.stderr
        assert f"ready ({ENTRY_POINT}: {ENTRY_POINT} {ADMISSIBLE})" in run.stderr, run.stderr
    if case == "release-query-fails":
        # uv's own report says why the query failed, so a log names the cause.
        assert "index lookups were disabled" in logs, logs


@pytest.mark.skipif(shutil.which("uv") is None, reason="the installer runs `uv tool install`")
def test_overlapping_runs_over_a_stale_release_install_it_once(
    tmp_path: Path, tool_home: ToolHome
) -> None:
    """Runs from several session starts meet a stale install: one installs, the rest wait.

    Each waits on the lock the first takes on the bin directory, then finds the release
    that run installed current, rather than reinstalling over the link it just made.
    """
    pin = pinned_oneharness()
    env = tool_home.environment(_index(tmp_path / "index", _offer(pin)))
    _uv_tool_install(
        env, f"{LLMLINT_DISTRIBUTION}=={FLOOR}", "--with", f"{ONEHARNESS_DISTRIBUTION}=={pin}"
    )

    watched = _watch_runs(INSTALLER, env, tool_home.bin_dir / ENTRY_POINT, 4)

    logs = watched.logs
    assert all(run.returncode == 0 for run in watched.completed), logs
    installs = [run for run in watched.completed if "installing the highest" in run.stderr]
    assert len(installs) == 1, logs
    assert logs.count("is already installed; leaving it as it is") == 3, logs
    installed = installed_llmlint(tool_home.tool_dir)
    assert installed is not None and installed.version == str(ADMISSIBLE), logs
    for run in watched.completed:
        assert f"ready ({ENTRY_POINT}: {ENTRY_POINT} {ADMISSIBLE})" in run.stderr, run.stderr


@pytest.mark.skipif(shutil.which("uv") is None, reason="the installer runs `uv tool install`")
def test_a_tool_list_uv_cannot_read_leaves_a_runnable_llmlint_alone(
    tmp_path: Path, tool_home: ToolHome
) -> None:
    """uv cannot list its tools, so the installed release is unknown: touch nothing.

    `UV_TOOL_DIR` naming a file is a tool directory uv cannot list, while the link still
    runs the release installed before.
    """
    pin = pinned_oneharness()
    env = tool_home.environment(_index(tmp_path / "index", _offer(pin)))
    _uv_tool_install(env, f"{LLMLINT_DISTRIBUTION}=={ADMISSIBLE}")
    link = tool_home.bin_dir / ENTRY_POINT
    subprocess.run([str(link), "--version"], capture_output=True, check=True, env=env)
    before = _install_fingerprint(tool_home)
    unlistable = tmp_path / "not-a-tool-dir"
    unlistable.write_text("", encoding="utf-8")
    env["UV_TOOL_DIR"] = str(unlistable)

    watched = _watch_runs(INSTALLER, env, link)

    log = watched.logs
    assert watched.completed[0].returncode == 0, log
    assert watched.misses == 0, log
    assert _install_fingerprint(tool_home) == before, log
    assert (
        "uv could not list its tools, so the installed llmlint's release is unknown; "
        "leaving it as it is"
    ) in log, log
    assert "installing the highest" not in log, log
    assert f"ready ({ENTRY_POINT}: {ENTRY_POINT} {ADMISSIBLE})" in log, log


def _path_without_flock(directory: Path, home: ToolHome) -> str:
    """A PATH holding `uv` and every command the installer runs, and no `flock`."""
    directory.mkdir()
    for command in ("bash", "dirname", "mkdir", "sed", "tr"):
        found = shutil.which(command, path="/usr/bin:/bin")
        assert found is not None, command
        (directory / command).symlink_to(found)
    return f"{home.path.split(':')[0]}:{directory}"


@pytest.mark.skipif(shutil.which("uv") is None, reason="the installer runs `uv tool install`")
def test_a_host_without_flock_still_installs_the_selected_release(
    tmp_path: Path, tool_home: ToolHome
) -> None:
    """No `flock` on PATH, as on a stock macOS: the run says so and installs unserialized."""
    pin = pinned_oneharness()
    env = tool_home.environment(_index(tmp_path / "index", _offer(pin)))
    env["PATH"] = _path_without_flock(tmp_path / "no-flock", tool_home)
    _uv_tool_install(env, f"{LLMLINT_DISTRIBUTION}=={FLOOR}")

    completed = _run_installer(INSTALLER, env)

    assert completed.returncode == 0, completed.stderr
    assert "flock not found; going on unserialized" in completed.stderr, completed.stderr
    assert (
        f"{LLMLINT_DISTRIBUTION} {FLOOR} is installed and the pin selects {ADMISSIBLE}"
    ) in completed.stderr, completed.stderr
    installed = installed_llmlint(tool_home.tool_dir)
    assert installed is not None and installed.version == str(ADMISSIBLE), completed.stderr


@pytest.mark.skipif(shutil.which("uv") is None, reason="the installer runs `uv tool install`")
def test_a_lock_flock_refuses_is_reported_and_the_run_still_installs(
    tmp_path: Path, tool_home: ToolHome
) -> None:
    """`flock` refuses the lock, as on a filesystem without locks: say so, install anyway.

    The one stand-in is `flock` itself, the util-linux command at the installer's
    boundary, answering as `flock` does when the kernel refuses the lock: a non-zero exit.
    No real filesystem a journey can make refuses a lock on a directory just opened.
    """
    pin = pinned_oneharness()
    env = tool_home.environment(_index(tmp_path / "index", _offer(pin)))
    refusing = tmp_path / "refusing-flock"
    refusing.mkdir()
    flock = refusing / "flock"
    flock.write_text(
        "#!/bin/sh\necho 'flock: failed to get lock: Operation not supported' >&2\nexit 1\n",
        encoding="utf-8",
    )
    flock.chmod(0o755)
    env["PATH"] = f"{refusing}:{env['PATH']}"
    _uv_tool_install(env, f"{LLMLINT_DISTRIBUTION}=={FLOOR}")

    completed = _run_installer(INSTALLER, env)

    assert completed.returncode == 0, completed.stderr
    assert "flock: failed to get lock" in completed.stderr, completed.stderr
    assert (
        f"flock could not lock {tool_home.bin_dir}; going on unserialized"
    ) in completed.stderr, completed.stderr
    installed = installed_llmlint(tool_home.tool_dir)
    assert installed is not None and installed.version == str(ADMISSIBLE), completed.stderr
    assert f"ready ({ENTRY_POINT}: {ENTRY_POINT} {ADMISSIBLE})" in completed.stderr, (
        completed.stderr
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="the installer runs `uv tool install`")
@pytest.mark.parametrize("bin_dir_state", ["cannot-be-created", "cannot-be-opened"])
def test_a_bin_dir_that_cannot_be_locked_is_reported_and_the_run_goes_on(
    tmp_path: Path, tool_home: ToolHome, bin_dir_state: str
) -> None:
    """The bin directory cannot be made, or cannot be opened to lock: say so, go on.

    Made under a file, it can be neither locked nor linked into, so uv's own failure is
    what the log goes on to report; opened, it needs read permission, which a directory
    uv can still link into may lack.
    """
    pin = pinned_oneharness()
    env = tool_home.environment(_index(tmp_path / "index", _offer(pin)))
    if bin_dir_state == "cannot-be-created":
        parent = tmp_path / "a-file"
        parent.write_text("", encoding="utf-8")
        bin_dir = parent / "bin"
    else:
        bin_dir = tool_home.bin_dir
        bin_dir.chmod(0o300)
    env["UV_TOOL_BIN_DIR"] = str(bin_dir)

    try:
        completed = _run_installer(INSTALLER, env)
    finally:
        tool_home.bin_dir.chmod(0o755)

    assert completed.returncode == 0, completed.stderr
    assert f"cannot open {bin_dir} to lock it; going on unserialized" in completed.stderr, (
        completed.stderr
    )
    assert "installing the highest" in completed.stderr, completed.stderr
    if bin_dir_state == "cannot-be-created":
        assert "leaving the installed llmlint as it is" in completed.stderr, completed.stderr
    else:
        installed = installed_llmlint(tool_home.tool_dir)
        assert installed is not None and installed.version == str(ADMISSIBLE), completed.stderr
        assert (bin_dir / ENTRY_POINT).exists(), completed.stderr


@pytest.mark.skipif(shutil.which("uv") is None, reason="the installer runs `uv tool install`")
def test_an_llmlint_uv_did_not_install_is_handed_to_uv_which_keeps_it(
    tmp_path: Path, tool_home: ToolHome
) -> None:
    """A runnable `llmlint` no uv tool install owns is not current, and uv refuses it.

    The installer asks uv to install, and uv will not overwrite an executable it did
    not link, so the command already there is what the host keeps, and the log says so.
    """
    pin = pinned_oneharness()
    env = tool_home.environment(_index(tmp_path / "index", _offer(pin)))
    link = tool_home.bin_dir / ENTRY_POINT
    foreign = "#!/bin/sh\necho 'llmlint foreign'\n"
    link.write_text(foreign, encoding="utf-8")
    link.chmod(0o755)

    completed = _run_installer(INSTALLER, env)

    assert completed.returncode == 0, completed.stderr
    assert f"{link} is not a uv tool install of {LLMLINT_DISTRIBUTION}" in completed.stderr, (
        completed.stderr
    )
    assert "Executable already exists" in completed.stderr, completed.stderr
    assert "leaving the installed llmlint as it is" in completed.stderr, completed.stderr
    assert link.read_text(encoding="utf-8") == foreign
    assert f"ready ({ENTRY_POINT}: {ENTRY_POINT} foreign)" in completed.stderr, completed.stderr
