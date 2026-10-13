#!/usr/bin/env bash
# How many dispatches are running on this host, read once from `just host --json`.
#
# The `dispatches` condition the root `budgets.yaml` declares, which onebudgetspec records
# beside every result measured from that file, and what the pre-push hook samples while
# the gate runs (`scripts/lock-timeout.sh`), so that a slow gate can be read against how
# busy the host was. A budget's verdict never depends on it: it is there for the manager's
# judgement of an over-budget result.
#
# Read the engine's version-1 JSON host reading through `just host --json`.
# Refuse failed or malformed readings, unreadable roots and unproven dispatches,
# printing only a diagnostic on stderr so onebudgetspec records `unknown`.
set -euo pipefail

if ! root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd); then
  echo "budget-dispatches: the checkout holding $0 cannot be entered, so how many dispatches are running is unknown; make that checkout readable, or run this from an intact one" >&2
  exit 1
fi

python="$root/.venv/bin/python"
if [[ ! -x "$python" ]]; then
  echo "budget-dispatches: the locked Python interpreter $python is missing or inaccessible; run just bootstrap from $root to repair the environment" >&2
  exit 1
fi

# No sync: a read of the engine's view needs the installed engine as it stands, and a
# sync would queue behind, or hold, the lock on this checkout's environment that every
# `uv run` of the gate this is sampled beside waits on. The view's own stderr is let
# through, since it is the account of why a failed read failed.
if ! view=$(cd "$root" && UV_NO_SYNC=1 just host --json); then
  echo "budget-dispatches: 'just host --json' failed (its report is above), so how many dispatches are running is unknown; run 'just host --json' from $root and repair what it reports" >&2
  exit 1
fi

if ! count=$("$python" -c '
import json
import sys


def refuse(reason):
    print(
        "budget-dispatches: " + reason + "; the count is unknown. "
        "Run just host --json and repair the reported root or the count consumer",
        file=sys.stderr,
    )
    sys.exit(1)


try:
    view = json.load(sys.stdin)
except ValueError as error:
    refuse("the host reading is invalid JSON: " + str(error))
if not isinstance(view, dict):
    refuse("the host reading must be an object")
if type(view.get("schema_version")) is not int or view["schema_version"] != 1:
    refuse("schema_version must be integer 1")
if "root_error" not in view or (
    view["root_error"] is not None and not isinstance(view["root_error"], str)
):
    refuse("root_error is missing or must be a string or null")
if not isinstance(view.get("dispatches"), list):
    refuse("dispatches is missing or must be an array")
for dispatch in view["dispatches"]:
    if (
        not isinstance(dispatch, dict)
        or not isinstance(dispatch.get("proof"), str)
        or dispatch["proof"] not in ("held", "unproven")
    ):
        refuse("dispatch proof is missing or must be held or unproven")
if view["root_error"] is not None:
    refuse("it could not read the runs root: " + view["root_error"])
if any(dispatch["proof"] == "unproven" for dispatch in view["dispatches"]):
    refuse("a dispatch could not be proven alive or gone")
print(len(view["dispatches"]))
' <<<"$view"); then
  echo "budget-dispatches: the JSON count could not be read (its report is above); repair the host reading, or run just bootstrap from $root if the locked Python interpreter failed" >&2
  exit 1
fi
printf '%s\n' "$count"
