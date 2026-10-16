"""A configuration home for every test process in which the plan store finds no user layer.

The pinned onetaskgraph reads a user-level document, `<configuration home>/onetaskgraph/
config.yaml`, beneath every project document it loads, the configuration home being
`XDG_CONFIG_HOME`, else `$HOME/.config` — and it has no variable of its own that moves that
document. So whatever the invoking user keeps there decides what every fixture's plan-store
read and write does. That is a host's own setting, not this checkout's: a host declaring
`sources.authoring.visibility: private` — a deliberate choice for plans of private
repositories — made every plan a fixture authored read as private, and every copy of one
into a fixture's `plans`, `followups` or `examples` source was refused, across every tier
that copies a plan.

`isolate()` is the answer, run by `tests/conftest.py` at import for the reason the `onevcs`
snapshot beside it is: a fixture of any scope is set up after launches that already load
the store. It builds a configuration home of this process's own under `TMPDIR` and exports
it as `XDG_CONFIG_HOME` for the whole process and every subprocess it spawns. That home
links every entry of the invoking user's configuration home — `gh`, `uv`,
`ai-orchestrator` and the rest — so every other tool reading its own directory there reads
exactly what it read before; the one entry it does not link is the plan store's own
directory, which it holds empty, so neither the user layer nor the store's `secrets.env`
reaches a test. A test that exercises a user layer writes one under a configuration home
it names itself, which overrides this one in the environment it composes; a test that
redirects `HOME` to sandbox a program deriving its configuration from it names
`XDG_CONFIG_HOME` beside it, because this export would otherwise win over that derivation.

Bun is the one tool that reads a file at the configuration home's root rather than a
directory in it: its `.npmrc` is `$XDG_CONFIG_HOME/.npmrc` once that variable is set, and
`~/.npmrc` only while it is not, so moving the variable hid a host's `cache=` line from
every Bun install a test ran. Bun then fell back to `$XDG_CACHE_HOME/.bun/install/cache`,
and a provisioning journey installing offline under a cache home of its own found every
package missing. So before the move `isolate()` states the cache directory Bun resolves
now as `BUN_INSTALL_CACHE_DIR`, which Bun reads before either file, unless the
environment already states one — as a pytest process this one started does.

The invoking user's configuration home is recorded in `HOST_CONFIG_HOME_ENV`, so a pytest
process this one starts — an xdist worker, or a suite a test runs — links the same host
entries rather than taking this process's sandbox for the host's.
`tests/plan_tooling/plan_copy/test_plan_store_user_layer_e2e.py` drives a fixture's copy
under a user layer that would refuse it, and the isolation's links.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

#: The variable the plan store, and every XDG-following tool, reads its configuration home
#: from before `$HOME/.config`.
CONFIG_HOME_ENV = "XDG_CONFIG_HOME"
#: The plan store's directory under a configuration home: its user layer and its secrets.
STORE_DIRECTORY = "onetaskgraph"
#: The user layer's name inside that directory.
USER_DOCUMENT = "config.yaml"
#: Where the invoking user's configuration home is, for a pytest process this one starts.
#: No tool reads it.
HOST_CONFIG_HOME_ENV = "ORCHESTRATOR_TEST_HOST_CONFIG_HOME"
#: The variable Bun reads its install cache from before any configuration file.
BUN_CACHE_ENV = "BUN_INSTALL_CACHE_DIR"
#: A directory holding a `package.json`, which `bun pm cache` needs to answer at all.
CHECKOUT = Path(__file__).resolve().parent.parent


def configuration_home(environment: Mapping[str, str]) -> Path | None:
    """The configuration home ``environment`` names: `XDG_CONFIG_HOME`, else `$HOME/.config`.

    The store's own rule (`onetaskgraph-core`'s `configuration_home`), an empty value read
    as unset, and none when neither is set.
    """
    if named := environment.get(CONFIG_HOME_ENV):
        return Path(named)
    if home := environment.get("HOME"):
        return Path(home) / ".config"
    return None


def host_config_home(environment: Mapping[str, str]) -> Path | None:
    """The invoking user's configuration home, as a suite that started this process found it."""
    if inherited := environment.get(HOST_CONFIG_HOME_ENV):
        return Path(inherited)
    return configuration_home(environment)


def bun_cache(environment: Mapping[str, str]) -> str | None:
    """The install cache Bun resolves under ``environment``, or `None` when it cannot say.

    Bun's own answer, from `bun pm cache`, so its precedence between its variables, its
    `bunfig.toml` and the `.npmrc` it reads is Bun's rather than restated here.
    """
    bun = shutil.which("bun", path=environment.get("PATH"))
    if bun is None:  # pragma: no cover - the suite cannot run without Bun on PATH
        return None
    answered = subprocess.run(
        [bun, "pm", "cache"],
        cwd=CHECKOUT,
        env=dict(environment),
        text=True,
        capture_output=True,
        check=False,
    )
    return answered.stdout.strip() or None if answered.returncode == 0 else None


def isolate() -> Path:
    """Export a configuration home linking the host's, with an empty plan-store directory.

    Bun's cache is stated first, where Bun finds it now, because the move would change it.
    Lives under `TMPDIR` rather than pytest's basetemp because it is taken at import, before
    any fixture exists, and is removed when the process ends; removing it unlinks the links
    and never follows one.
    """
    if not os.environ.get(BUN_CACHE_ENV) and (cache := bun_cache(os.environ)) is not None:
        os.environ[BUN_CACHE_ENV] = cache
    host = host_config_home(os.environ)
    sandbox = Path(tempfile.mkdtemp(prefix="plan-store-config-home-"))
    atexit.register(shutil.rmtree, sandbox, ignore_errors=True)
    if host is not None and host.is_dir():
        for entry in host.iterdir():
            if entry.name != STORE_DIRECTORY:
                (sandbox / entry.name).symlink_to(entry)
    (sandbox / STORE_DIRECTORY).mkdir()
    if host is not None:
        os.environ[HOST_CONFIG_HOME_ENV] = str(host)
    os.environ[CONFIG_HOME_ENV] = str(sandbox)
    return sandbox


def handed() -> dict[str, str]:
    """The configuration home this process hands every process it spawns, as one variable.

    For an environment a journey builds from scratch rather than over this process's own:
    stated beside its `HOME`, so a store it starts reads no user layer of the host's.
    """
    return {CONFIG_HOME_ENV: os.environ[CONFIG_HOME_ENV]}
