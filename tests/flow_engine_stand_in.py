"""The engine a copied checkout enters its launch flow through, with only `flow run` doubled.

`scripts/plan.sh` and the other launchers re-execute themselves inside the engine's
`onepipeline flow run` before they write anything, through the real `scripts/onepipeline.sh`,
and refuse when the checkout has no engine at the platform package's binary its `node_modules`
holds (`orchestrator.npm_installs.ENGINE`). A journey that copies a launcher into a checkout
of its own, to drive a refusal the launcher makes *inside* its flow, needs that flow entered
first. So this gives the copy the real wrapper and the helpers
it sources, and puts a stand-in at the binary the wrapper execs — the published-CLI boundary
the suite doubles — rather than the real engine, which would register a flow owned by
whichever session the test process happens to run under.

The stand-in does what `flow run` does for the program it is given: runs it with
`ONEPIPELINE_FLOW` naming the flow (the one already entered, when there is one, as the
engine joins a live flow of the same session) and exits with its status. Any other verb is
refused, because a journey reaching one has driven past the refusal it is about.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from orchestrator import npm_installs
from orchestrator.root import REPO_ROOT

#: The real wrapper a launcher enters its flow through, and every helper it sources for the
#: `flow` verb.
WRAPPER_SCRIPTS = ("onepipeline.sh", "npm-binary.sh", "launcher-session.sh", "template-env.sh")

#: The status and the words the stand-in refuses every verb but `flow run` with, so a journey
#: that stops at the first other verb its launcher reaches can say where it stopped.
UNEXPECTED_VERB_EXIT = 97
UNDOUBLED = "flow engine stand-in: only 'flow run' is doubled here"

STAND_IN = f"""#!/usr/bin/env bash
set -euo pipefail
if [ "${{1:-}} ${{2:-}}" = "flow run" ]; then
  shift 2
  flow=""
  while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
    [ "$1" != "--name" ] || flow=${{2:-}}
    shift
  done
  shift
  ONEPIPELINE_FLOW=${{ONEPIPELINE_FLOW:-$flow}} exec "$@"
fi
echo "{UNDOUBLED}, not '$*'" >&2
exit {UNEXPECTED_VERB_EXIT}
"""


def give_flow_engine(checkout: Path) -> Path:
    """Give ``checkout`` the wrapper its launchers enter a flow through, and the stand-in.

    Returns the stand-in's path, the platform package's binary under the checkout's
    `node_modules`.
    """
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in WRAPPER_SCRIPTS:
        copied = scripts / name
        copied.write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
        copied.chmod(0o755)
    # The template root the wrapper names on every verb, refused when it carries no
    # registration.
    templates = checkout / "templates"
    templates.mkdir(exist_ok=True)
    shutil.copy(REPO_ROOT / "templates" / "templates.yaml", templates)
    engine = npm_installs.platform_binary(node_modules=checkout / "node_modules")
    engine.parent.mkdir(parents=True, exist_ok=True)
    engine.write_text(STAND_IN, encoding="utf-8")
    engine.chmod(0o755)
    return engine
