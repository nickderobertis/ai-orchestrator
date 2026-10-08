"""The plan store this host locks carries a document's images through a copy.

A visual plan's spike report and design document reference before-and-after screenshots
as `![alt](./<name>)`, are written into the drafting source with each image as an
`--asset`, and are copied onward from there. This drives that sequence through the locked
`.venv/bin/onetaskgraph` every plan-store read here runs, run from this checkout as
`orchestrator/plan_store.py` runs it, over two scratch `local-md` sources declared the
way the store's own environment layer declares a source. The images are generated per
test from seeded pseudo-random pixels, so compression cannot shrink them below the size
a real screenshot has and no two are alike.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import struct
import subprocess
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

from published_tools import ONETASKGRAPH_BIN

from orchestrator.root import REPO_ROOT

#: The two scratch sources: the one a plan is drafted in, and the one it is copied to.
DRAFTED = "drafted"
COPIED = "copied"
PROJECT = "visual-plan"
DOCUMENT = "spike-report"


def _png(path: Path, seed: int, side: int = 200) -> bytes:
    """Write an RGB PNG of seeded random pixels to ``path`` and answer its bytes.

    At the default side that is 120,200 bytes of random scanlines, which zlib cannot shrink,
    so the file is about 120 KB: inside the 50 KB to 500 KB a real screenshot spans.
    """
    pixels = random.Random(seed)
    raw = b"".join(b"\x00" + pixels.randbytes(side * 3) for _ in range(side))

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + crc

    header = struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0)
    image = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(image)
    return image


def _environment(root: Path) -> dict[str, str]:
    """This process's environment with the two scratch sources declared under ``root``."""
    sources: dict[str, str] = {}
    for name in (DRAFTED, COPIED):
        (root / name).mkdir(parents=True)
        variable = f"ONETASKGRAPH_SOURCES__{name.upper()}__"
        sources[f"{variable}PLUGIN"] = "local-md"
        sources[f"{variable}CONFIG__ROOT"] = str(root / name)
    return {**os.environ, **sources}


def _store(environment: Mapping[str, str], *arguments: str) -> Any:
    """Run the locked plan-store CLI from this checkout and answer its JSON document."""
    ran = subprocess.run(
        [str(ONETASKGRAPH_BIN), *arguments, "--json"],
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert ran.returncode == 0, (arguments, ran.stdout, ran.stderr)
    return json.loads(ran.stdout)


class Asset(NamedTuple):
    """One asset as `document show --json` lists it."""

    name: str
    sha256: str
    content_type: str
    path: Path


class ShownDocument(NamedTuple):
    """What `document show --json` answers for one document: its content and its assets."""

    content: str
    assets: tuple[Asset, ...]


def _shown(environment: Mapping[str, str], qualified: str) -> ShownDocument:
    """The document `document show --json` answers for ``qualified``.

    The assets are a member of the answer beside its one item rather than of the item.
    """
    shown = _store(environment, "document", "show", qualified)
    (item,) = shown["items"]
    content = item["item"]["content"]
    assert isinstance(content, str), (qualified, item)
    assets = tuple(
        Asset(
            name=str(asset["name"]),
            sha256=str(asset["sha256"]),
            content_type=str(asset["content_type"]),
            path=Path(asset["path"]),
        )
        for asset in shown["assets"]
    )
    return ShownDocument(content=content, assets=assets)


def test_a_documents_images_survive_its_copy_into_a_second_source(tmp_path: Path) -> None:
    """Each image is listed on both copies with one sha256 and the original bytes on disk.

    The document is created with two `--asset` images its content references, copied into
    the second source, and read back from each with `document show --json`: both list the
    two assets in the order the content references them, each with the sha256 of the bytes
    generated here and a `path` holding exactly those bytes, and the copy's content is the
    content written.
    """
    images = tmp_path / "screenshots"
    images.mkdir()
    generated = {
        name: _png(images / name, seed) for seed, name in enumerate(("before.png", "after.png"))
    }
    assert len({hashlib.sha256(image).hexdigest() for image in generated.values()}) == 2
    content = (
        "# Spike report\n\n"
        "![The settings page before](./before.png)\n\n"
        "![The settings page after](./after.png)\n"
    )
    body = tmp_path / "report.md"
    body.write_text(content, encoding="utf-8")
    environment = _environment(tmp_path / "sources")

    _store(environment, "project", "create", DRAFTED, "--id", PROJECT, "--title", "Visual plan")
    _store(
        environment,
        *("document", "create", DRAFTED, "--project", PROJECT, "--title", "Spike report"),
        *("--id", DOCUMENT, "--body-file", str(body), "--no-interactive"),
        *(arg for name in generated for arg in ("--asset", str(images / name))),
    )
    copied = _store(environment, "document", "copy", f"{DRAFTED}:{DOCUMENT}", "--to", COPIED)
    (outcome,) = copied["items"]
    assert outcome["destination"] == f"{COPIED}:{DOCUMENT}", copied

    paths: set[Path] = set()
    for qualified in (f"{DRAFTED}:{DOCUMENT}", outcome["destination"]):
        shown = _shown(environment, qualified)
        assert shown.content == content, (qualified, shown.content)
        assert [asset.name for asset in shown.assets] == list(generated), (qualified, shown)
        for asset in shown.assets:
            image = generated[asset.name]
            assert asset.sha256 == hashlib.sha256(image).hexdigest(), (qualified, asset)
            assert asset.content_type == "image/png", (qualified, asset)
            assert asset.path.read_bytes() == image, (qualified, asset)
            paths.add(asset.path)
    assert len(paths) == 4, f"each source holds its own copy of each image: {paths}"
