"""The one way an e2e journey holds a dispatched agent turn in flight.

`fake_backend.py` parses the fragment `sentinels` renders, announces its arrival by
creating `ready`, and blocks until the test creates `release`. Holding on the test's
own signal keeps a node in flight exactly as long as the journey needs, where a
fixed sleep both costs that time unconditionally and races the assertion it was
meant to make observable.

There is deliberately one mechanism rather than one per journey: when the right
rendezvous is not obvious the next test reaches for `time.sleep`, which is how a
30-second sleep was once written into the double while two correct idioms already
sat beside it.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import NamedTuple

from waits import deadline


class Rendezvous(NamedTuple):
    """The pair of paths that hold one dispatched agent turn until a test lets go."""

    ready: Path
    release: Path

    @classmethod
    def at(cls, directory: Path, name: str) -> Rendezvous:
        """Name both paths of one rendezvous under `directory`."""
        return cls(directory / f"{name}.ready", directory / f"{name}.release")

    def sentinels(self, turn: int = 0) -> str:
        """Render the task fragment that holds the zero-based agent `turn` here."""
        return f" hold-{turn}-ready={self.ready} hold-{turn}-release={self.release}"

    def arrived(self) -> bool:
        """Whether the held turn has announced it is parked at this rendezvous."""
        return self.ready.is_file()

    def wait(self, seconds: float = 60.0) -> None:
        """Block until the held turn announces it arrived, or fail the hang guard."""
        guard = deadline(seconds)
        while not self.arrived():
            if time.monotonic() >= guard:
                raise AssertionError(f"the held agent turn never reached {self.ready}")
            time.sleep(0.02)

    def let_go(self) -> None:
        """Release the held turn."""
        self.release.write_text("release\n", encoding="utf-8")
