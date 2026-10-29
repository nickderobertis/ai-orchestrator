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
        f"no {LLMLINT_DISTRIBUTION} >= {FLOOR} resolved under {ONEHARNESS_DISTRIBUTION} {pin}"
        in completed.stderr
    ), completed.stderr
    assert "leaving the installed llmlint as it is" in completed.stderr, completed.stderr
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
class WatchedRun:
    """One installer run, and how often the link was polled and found missing during it."""

    completed: subprocess.CompletedProcess[str]
    polls: int
    misses: int


def _run_watching_the_link(script: Path, env: dict[str, str], link: Path) -> WatchedRun:
    """Run the installer while polling `link` for as long as the run lasts."""
    process = subprocess.Popen(
        ["bash", str(script)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )
    polls = misses = 0
    while process.poll() is None:
        polls += 1
        if not os.path.lexists(link):
            misses += 1
    stdout, stderr = process.communicate()
    return WatchedRun(
        subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr),
        polls,
        misses,
    )


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

    second = _run_watching_the_link(INSTALLER, env, link)

    log = second.completed.stderr
    assert second.completed.returncode == 0, log
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
@pytest.mark.parametrize("broken", ["link-missing", "pin-not-constrained"])
def test_the_installer_repairs_a_selected_release_that_is_not_installed_as_the_rule_says(
    tmp_path: Path, tool_home: ToolHome, broken: str
) -> None:
    """The selected release is in place but its link is gone, or it carries no pin: reinstall.

    Either is an install the rule did not produce, so leaving it alone would leave a host
    with no `llmlint` command, or one whose `oneharness` is not the pinned release.
    """
    pin = pinned_oneharness()
    env = tool_home.environment(_index(tmp_path / "index", _offer(pin)))
    link = tool_home.bin_dir / ENTRY_POINT
    if broken == "link-missing":
        _uv_tool_install(
            env,
            f"{LLMLINT_DISTRIBUTION}=={ADMISSIBLE}",
            "--with",
            f"{ONEHARNESS_DISTRIBUTION}=={pin}",
        )
        link.unlink()
    else:
        _uv_tool_install(env, f"{LLMLINT_DISTRIBUTION}=={ADMISSIBLE}")

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
