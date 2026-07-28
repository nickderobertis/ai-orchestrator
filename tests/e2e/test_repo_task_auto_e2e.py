"""Command-surface coverage for the automatic repo-task wrapper."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT


def _uv_free_mirror(source: Path, destination: Path) -> Path:
    """Symlink rather than copy: an entry a launcher follows still resolves to its
    real path, where the siblings it reaches for live."""
    destination.mkdir(parents=True)
    for entry in source.iterdir():
        if entry.name != "uv":
            (destination / entry.name).symlink_to(entry)
    return destination


def test_repo_task_auto_prepends_uv_tool_bin_to_dispatch_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    uv = shutil.which("uv")
    just = shutil.which("just")
    assert uv is not None
    assert just is not None
    (bin_dir / "uv").symlink_to(uv)
    # The wrapper must find `uv` only through the tool bin it prepends, so the PATH
    # it inherits carries `just` and the interpreter a wrapper-script install of it
    # needs (a node-shim `just` is otherwise unrunnable here).
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "just").symlink_to(just)
    node = shutil.which("node")
    if node is not None:
        (tools / "node").symlink_to(node)
    # `just` may also be a launcher that reaches for a sibling of its own real path
    # rather than for a bare interpreter name, so keep its directory reachable too,
    # along with the system directories the recipe's own tools come from. Every one
    # of those goes on the PATH as an isolated mirror of symlinks with `uv` omitted,
    # never directly: `uv` is installed beside `just` on any host that puts both in
    # ~/.local/bin, and beside the system tools wherever a distro packages it. The
    # mirror is what makes the uv-free premise — the recipe having to derive the
    # tool bin from HOME — hold by construction instead of by install layout.
    mirror_root = tmp_path / "mirrors"
    inherited_dirs = [str(tools)]
    mirrored: set[Path] = set()
    for index, source in enumerate([Path(just).parent, Path("/usr/bin"), Path("/bin")]):
        resolved = source.resolve()
        if not resolved.is_dir() or resolved in mirrored:
            continue
        mirrored.add(resolved)
        inherited_dirs.append(str(_uv_free_mirror(resolved, mirror_root / str(index))))
    inherited_path = os.pathsep.join(inherited_dirs)
    assert shutil.which("uv", path=inherited_path) is None

    proc = subprocess.run(
        [str(tools / "just"), "repo-task-auto", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env={**os.environ, "HOME": str(home), "PATH": inherited_path},
    )

    assert proc.returncode == 0, proc.stderr
    assert "orchestrator-repo-task" in proc.stdout
