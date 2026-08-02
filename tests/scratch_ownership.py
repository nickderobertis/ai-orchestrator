"""Stand in for a live dispatcher's claim on a watchdog scratch directory.

`orchestrator.scratch.owned_scratch_directory` creates that directory and *holds*
its owner lock for a dispatch's whole scope, and `watchdog_has_a_live_owner` accepts
nothing weaker — a lock file that is merely present names a process that does not
hold it. So a test that wants the reader to believe its publication has to take the
real lock, and `flock` conflicts between separate open file descriptions even inside
one process, which is what makes that possible here.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

from orchestrator.scratch import OWNER_LOCK_NAME, _OwnerIdentity


def hold_owner_lock(watchdog: Path) -> int:
    """Take and hold this watchdog directory's owner lock; return its descriptor.

    The caller closes it, which releases the claim — so a test can also prove what a
    view does once a dispatch is over.
    """
    descriptor = os.open(watchdog / OWNER_LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    identity = _OwnerIdentity.current(os.getpid())
    assert identity is not None, "this process has no readable start identity"
    os.write(descriptor, identity.render().encode("utf-8"))
    return descriptor
