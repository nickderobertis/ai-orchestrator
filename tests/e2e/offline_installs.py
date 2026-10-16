"""The environment a provisioning journey installs a checkout under, reaching no network.

A test-support unit of its own, `tests/support/offline_installs/`, separate from
`tests/e2e/provisioning.py`: a journey that only needs to install offline then takes no
edge to the provisioning fixture's files and the session setup scripts it copies.
"""

from __future__ import annotations

#: An address nothing listens on, standing for every network crossing a provisioning run
#: could make.
UNREACHABLE = "http://127.0.0.1:9"
#: The environment that provisions a whole checkout from this host's caches and reaches no
#: index, registry or download host: uv answers offline, and every HTTP client — Bun's
#: installs, Playwright's — is routed through a proxy that refuses, so an install that
#: would have to download fails instead. Bun keeps its own registry rather than being
#: pointed at a dead one, because its cache is keyed by registry and a different registry
#: misses every entry.
NO_NETWORK = {
    "UV_OFFLINE": "1",
    "PLAYWRIGHT_DOWNLOAD_HOST": UNREACHABLE,
    **{
        name: UNREACHABLE
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy")
    },
    "NO_PROXY": "",
    "no_proxy": "",
}
