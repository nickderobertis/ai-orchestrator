"""The paid writer's scripted action, through real template and store interfaces."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from orchestrator import plan_store
from orchestrator.root import REPO_ROOT


def write(scratch: Path) -> None:
    loader = scratch / "brief-loader.json"
    loader.write_text(
        subprocess.check_output(
            [
                str(REPO_ROOT / ".venv/bin/onepipeline"),
                "template",
                "resolve",
                "spike-brief",
                "--template-root",
                str(REPO_ROOT / "templates"),
                "--json",
            ],
            text=True,
            timeout=30,
        )
    )
    plan_store.sdk(
        plan_store.client().document_create(
            "authoring",
            "preview",
            "Preview brief",
            id="preview-brief",
            template_loader=str(loader),
            answers={
                "overview": "Measure the private interface before selecting its implementation.",
                "spikes": [
                    {
                        "spike": "measure",
                        "question": "Which supported interface preserves request behavior?",
                        "use": "Choose the implementation from measured behavior.",
                    }
                ],
            },
        )
    )


if __name__ == "__main__":
    write(Path(sys.argv[1]))
