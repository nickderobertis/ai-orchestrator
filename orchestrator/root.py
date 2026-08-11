"""This checkout's own root, which the suite resolves its fixtures against.

It lives in a module rather than in the package's ``__init__`` because this
package deliberately has no ``__init__``. Every worktree of this repository is a
*dispatch working directory*: the orchestrator that dispatches a subtask runs the
agent with its cwd set to the worktree, and anything it launches as ``python -m
<module>`` gets that worktree at the front of ``sys.path``. A regular package
named ``orchestrator`` sitting at the root therefore captures every
``orchestrator.*`` import made from inside a dispatch — including the dispatcher's
own helper modules, which this repository no longer carries.

Without an ``__init__``, the directory is a PEP 420 namespace portion instead: the
import machinery records it and keeps scanning, so a real ``orchestrator`` package
installed anywhere else on the path still wins and still imports. The name stays,
every documented path stays, and a dispatch into a worktree of this tree can start.

``tests/test_dispatch_cwd_does_not_shadow.py`` drives that resolution for real, so
re-adding an ``__init__.py`` here fails the suite rather than the next dispatch.
"""

from pathlib import Path

__all__ = ["REPO_ROOT"]

REPO_ROOT = Path(__file__).resolve().parent.parent
