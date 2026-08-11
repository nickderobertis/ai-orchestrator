"""What is left of this repository's own Python: label rendering, and where it lives.

The deterministic mechanics this package used to hold — config merging, dispatch,
DAG scheduling, and the repository lifecycle — are the published CLIs now, and the
`just` recipes are thin wrappers over them. What stays here is the one thing no
recipe can do in shell (`orchestrator.labels`), the redaction its logs share, and
this checkout's own root, which the suite resolves its fixtures against.
"""

from pathlib import Path

__all__ = ["REPO_ROOT"]

REPO_ROOT = Path(__file__).resolve().parent.parent
