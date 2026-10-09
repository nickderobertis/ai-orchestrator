"""`just render-diagram` draws a Mermaid source to a PNG in the locked font, or writes nothing.

A design document's Architecture shows a diagram as an image asset, because a Linear board
does not render Mermaid code. The image has to be the same bytes on every provisioned host,
so that a budget over its label size can be cached on the files that decide it: the source,
the committed `config/mermaid/` files, `bun.lock` (mermaid-cli, Playwright and so the
Chromium revision, and the font) and the script.

Everything here is real: the recipe, `scripts/render-diagram.sh`, the locked mermaid-cli and
the Chromium the locked Playwright names. Two of the journeys run the same script out of a
scratch copy of the files it reads, whose `node_modules` links every locked package back to
this checkout's install except the one being varied, so the copy is what differs and nothing
in this checkout is changed.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.xdist_group("render-diagram")


def _declared_page_width() -> int:
    """The page width `scripts/render-diagram.sh` declares, which no PNG it writes exceeds."""
    script = (REPO_ROOT / "scripts" / "render-diagram.sh").read_text(encoding="utf-8")
    found = re.search(r"^PAGE_WIDTH=(\d+)$", script, re.MULTILINE)
    assert found is not None, "scripts/render-diagram.sh declares no PAGE_WIDTH"
    return int(found.group(1))


PAGE_WIDTH = _declared_page_width()

#: The body margin mermaid-cli's page keeps on each side, which a full-width graph loses.
BODY_MARGIN = 8

#: A source wider than the page, with a unit box, so wrapping, spacing and the title
#: margin the committed configuration sets all take part in the render.
SOURCE = """flowchart LR
  subgraph unit["Design document flow"]
    write["Write the design document from the plan"] --> render["Render the architecture diagram"]
  end
  render --> approve["Approve the document with every image it shows"]
  approve --> launch["Launch the plan once its document is approved"]
  launch --> settle["Settle every node and report where each change landed"]
"""

#: The PNG signature every file the recipe writes starts with.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: The locked font package, and the one of its files a regular-weight label is drawn in.
FONT_PACKAGE = Path("@expo-google-fonts") / "noto-sans"
REGULAR = Path("400Regular") / "NotoSans_400Regular.ttf"

#: A different typeface's file the locked install already holds, to swap in for it.
OTHER_FONT = (
    REPO_ROOT
    / "node_modules"
    / "@mermaid-js"
    / "mermaid-cli"
    / "dist"
    / "assets"
    / "KaTeX_Main-Regular-ypZvNtVU.ttf"
)


def _render(
    source: Path,
    output: Path,
    *,
    environment: dict[str, str] | None = None,
    root: Path = REPO_ROOT,
) -> subprocess.CompletedProcess[str]:
    """Render ``source`` to ``output`` through the recipe ``root``'s justfile declares."""
    return subprocess.run(
        ["just", "render-diagram", str(source), str(output)],
        cwd=root,
        env=environment if environment is not None else dict(os.environ),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _size(image: bytes) -> tuple[int, int]:
    """The width and height a PNG's IHDR chunk states."""
    width, height = struct.unpack(">II", image[16:24])
    return width, height


def _source(tmp_path: Path, text: str = SOURCE, name: str = "diagram.mmd") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _copied_workspace(
    tmp_path: Path,
    font: Path | None = None,
    *,
    without_mermaid_cli: bool = False,
    without_playwright: bool = False,
) -> Path:
    """A scratch copy of what the recipe reads, its packages linked back to this install.

    ``font``, when given, is the file the copy's locked Noto Sans regular is replaced by;
    every other package, and every other file of the font package, is this checkout's own.
    ``without_mermaid_cli`` leaves the copy's install without mermaid-cli's `mmdc`, as a
    workspace nobody provisioned since mermaid-cli was locked would be, and
    ``without_playwright`` leaves it without the locked Playwright package.
    """
    root = tmp_path / "workspace"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "justfile", root / "justfile")
    shutil.copy2(REPO_ROOT / "scripts" / "render-diagram.sh", root / "scripts")
    shutil.copytree(REPO_ROOT / "config" / "mermaid", root / "config" / "mermaid")
    installed = REPO_ROOT / "node_modules"
    linked = root / "node_modules"
    linked.mkdir()
    for entry in installed.iterdir():
        if entry.name not in (FONT_PACKAGE.parts[0], ".bin") and not (
            without_playwright and entry.name == "playwright"
        ):
            (linked / entry.name).symlink_to(entry)
    (linked / ".bin").mkdir()
    for entry in (installed / ".bin").iterdir():
        if not (without_mermaid_cli and entry.name == "mmdc"):
            (linked / ".bin" / entry.name).symlink_to(entry.resolve())
    scope = linked / FONT_PACKAGE.parts[0]
    scope.mkdir()
    shutil.copytree(installed / FONT_PACKAGE, scope / FONT_PACKAGE.name)
    if font is not None:
        shutil.copyfile(font, scope / FONT_PACKAGE.name / REGULAR)
    return root


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """One render of :data:`SOURCE` through this checkout's recipe."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("render-diagram")
    output = tmp_path / "diagram.png"
    done = _render(_source(tmp_path), output)
    assert done.returncode == 0, (done.stdout, done.stderr)
    return output.read_bytes()


def test_a_source_renders_to_a_png_no_wider_than_the_page(rendered: bytes) -> None:
    """The recipe writes a PNG laid out at the 880-px page a board column shows it at."""
    assert rendered.startswith(PNG_SIGNATURE), rendered[:16]
    width, height = _size(rendered)
    assert 0 < width <= PAGE_WIDTH, width
    # The source is wider than the page, so it fills it less mermaid-cli's two 8-px margins.
    assert width == PAGE_WIDTH - 2 * BODY_MARGIN, width
    assert height > 0, height


def test_two_renders_of_one_source_are_the_same_bytes(rendered: bytes, tmp_path: Path) -> None:
    """Nothing but the workspace's files decides a render, so a second one is identical."""
    output = tmp_path / "again.png"
    done = _render(_source(tmp_path), output)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert output.read_bytes() == rendered


def test_a_callers_fontconfig_hiding_every_host_font_changes_no_byte(
    rendered: bytes, tmp_path: Path
) -> None:
    """A caller whose fontconfig names no font at all gets the very same render.

    The script hands Chromium its own fontconfig, naming the locked font alone, so what a
    caller's environment offers — every host font, or none — never reaches a label.
    """
    empty = tmp_path / "no-fonts.conf"
    empty.write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
        "<fontconfig></fontconfig>\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["FONTCONFIG_FILE"] = str(empty)
    environment.pop("FONTCONFIG_PATH", None)
    output = tmp_path / "hidden.png"
    done = _render(_source(tmp_path), output, environment=environment)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert output.read_bytes() == rendered


def test_swapping_the_locked_fonts_file_changes_the_render(rendered: bytes, tmp_path: Path) -> None:
    """The labels are drawn in the locked font's file: another typeface there is another image.

    The same copy with the locked file left in place renders the very same bytes, so what
    the swapped render differs by is the font file alone and not the copy.
    """
    kept = _copied_workspace(tmp_path / "kept")
    output = tmp_path / "kept.png"
    done = _render(_source(tmp_path), output, root=kept)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert output.read_bytes() == rendered

    swapped = _copied_workspace(tmp_path / "swapped", font=OTHER_FONT)
    output = tmp_path / "swapped.png"
    done = _render(_source(tmp_path), output, root=swapped)
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert output.read_bytes().startswith(PNG_SIGNATURE)
    assert output.read_bytes() != rendered


def test_an_absent_browser_is_named_with_its_provisioning_command(tmp_path: Path) -> None:
    """With no Chromium where the locked Playwright looks, nothing is drawn and the fix is named."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    browsers = tmp_path / "no-browsers"
    browsers.mkdir()
    environment = dict(os.environ)
    environment["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    output = tmp_path / "diagram.png"
    done = _render(_source(tmp_path), output, environment=environment)
    assert done.returncode == 3, (done.stdout, done.stderr)
    assert f"no Chromium at {browsers}/" in done.stderr, done.stderr
    assert "provision it with 'just bootstrap'" in done.stderr, done.stderr
    assert "the locked Playwright's 'install chromium'" in done.stderr, done.stderr
    assert not output.exists()


def test_a_source_mermaid_refuses_writes_nothing_and_shows_its_diagnostic(tmp_path: Path) -> None:
    """A source mermaid cannot parse ends in mermaid's own words, with no PNG left behind."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    source = _source(tmp_path, "flowchart LR\n  A[one] -->\n  }}}\n", "broken.mmd")
    output = tmp_path / "broken.png"
    done = _render(source, output)
    assert done.returncode == 1, (done.stdout, done.stderr)
    assert "Lexical error on line 3" in done.stderr, done.stderr
    assert f"could not render '{source}'" in done.stderr, done.stderr
    assert "where its diagnostic above points into the source, correct the source" in done.stderr
    assert not output.exists()


def test_an_output_that_is_not_a_png_is_refused(tmp_path: Path) -> None:
    """A design document references its diagrams as PNG images, so no other path is written."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    output = tmp_path / "diagram.svg"
    done = _render(_source(tmp_path), output)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert f"'{output}' does not end in .png" in done.stderr, done.stderr
    assert not output.exists()


@pytest.mark.parametrize("arguments", [[], ["only.mmd"], ["a.mmd", "a.png", "extra"]])
def test_a_wrong_number_of_arguments_is_refused_naming_the_usage(
    arguments: list[str], tmp_path: Path
) -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    done = subprocess.run(
        ["just", "render-diagram", *arguments],
        cwd=tmp_path,
        env=dict(os.environ) | {"JUST_JUSTFILE": str(REPO_ROOT / "justfile")},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert f"got {len(arguments)} argument(s)" in done.stderr, done.stderr
    assert "just render-diagram <source.mmd> <output.png>" in done.stderr, done.stderr
    assert list(tmp_path.iterdir()) == []


def test_a_source_that_is_not_a_readable_file_is_refused(tmp_path: Path) -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    output = tmp_path / "diagram.png"
    done = _render(tmp_path / "absent.mmd", output)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert "is not a readable file; name an existing .mmd file" in done.stderr, done.stderr
    assert not output.exists()


def test_an_absent_mermaid_cli_is_named_with_its_provisioning_command(tmp_path: Path) -> None:
    """A workspace never provisioned since mermaid-cli was locked is told to bootstrap."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    unprovisioned = _copied_workspace(tmp_path, without_mermaid_cli=True)
    output = tmp_path / "diagram.png"
    done = _render(_source(tmp_path), output, root=unprovisioned)
    assert done.returncode == 3, (done.stdout, done.stderr)
    assert f"not installed at {unprovisioned}/node_modules/.bin/mmdc" in done.stderr, done.stderr
    assert "'just bootstrap'" in done.stderr, done.stderr
    assert not output.exists()


def test_a_failed_render_leaves_an_existing_output_and_a_good_one_replaces_it(
    rendered: bytes, tmp_path: Path
) -> None:
    """The output is only ever replaced whole, by a render that succeeded."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    output = tmp_path / "diagram.png"
    output.write_bytes(b"the diagram a document already shows")
    broken = _source(tmp_path, "flowchart LR\n  A[one] -->\n  }}}\n", "broken.mmd")
    failed = _render(broken, output)
    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    assert output.read_bytes() == b"the diagram a document already shows"
    replaced = _render(_source(tmp_path), output)
    assert replaced.returncode == 0, (replaced.stdout, replaced.stderr)
    assert output.read_bytes() == rendered
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "broken.mmd",
        "diagram.mmd",
        "diagram.png",
    ]


def test_an_output_in_a_directory_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    output = tmp_path / "absent" / "diagram.png"
    done = _render(_source(tmp_path), output)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert "is not a writable directory" in done.stderr, done.stderr
    assert not output.parent.exists()


def test_a_playwright_that_cannot_name_its_browser_is_told_to_bootstrap(tmp_path: Path) -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    unprovisioned = _copied_workspace(tmp_path, without_playwright=True)
    output = tmp_path / "diagram.png"
    done = _render(_source(tmp_path), output, root=unprovisioned)
    assert done.returncode == 3, (done.stdout, done.stderr)
    assert "could not name its Chromium" in done.stderr, done.stderr
    assert "'just bootstrap'" in done.stderr, done.stderr
    assert not output.exists()


def test_an_output_that_is_a_directory_is_refused(tmp_path: Path) -> None:
    """A directory named like a PNG would otherwise receive the render inside it."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    output = tmp_path / "diagram.png"
    output.mkdir()
    done = _render(_source(tmp_path), output)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert f"'{output}' is a directory" in done.stderr, done.stderr
    assert list(output.iterdir()) == []


def test_a_source_this_user_cannot_read_is_refused(tmp_path: Path) -> None:
    """An existing source without read permission is refused as unreadable, not rendered."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    source = _source(tmp_path)
    source.chmod(0)
    if os.access(source, os.R_OK):
        pytest.skip("this user reads a file whatever its mode")
    output = tmp_path / "diagram.png"
    try:
        done = _render(source, output)
    finally:
        source.chmod(0o644)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert f"'{source}' is not a readable file" in done.stderr, done.stderr
    assert not output.exists()


def test_an_output_in_a_directory_this_user_cannot_write_is_refused(tmp_path: Path) -> None:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    closed = tmp_path / "closed"
    closed.mkdir()
    closed.chmod(0o555)
    if os.access(closed, os.W_OK):
        pytest.skip("this user writes a directory whatever its mode")
    output = closed / "diagram.png"
    try:
        done = _render(_source(tmp_path), output)
    finally:
        closed.chmod(0o755)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert f"'{closed}' is not a writable directory" in done.stderr, done.stderr
    assert list(closed.iterdir()) == []
