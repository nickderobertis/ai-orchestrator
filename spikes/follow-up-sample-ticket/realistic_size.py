"""The longest rendered `owning_runs`, `basis` and `drafts` values among the followups tickets.

Reads `task list --source followups --json` (every page, through the store) from standard input,
and renders each ticket's `orchestrator.follow-up` record the way the proposed fields would show
it: one element per line for a list, one `KEY: VALUE` line per entry in key order for a map.
"""

import json
import sys

KEY = "orchestrator.follow-up"


def render(value: object) -> str:
    if isinstance(value, list):
        return "\n".join(str(one) for one in value)
    if isinstance(value, dict):
        return "\n".join(f"{k}: {value[k]}" for k in sorted(value))
    return str(value)


def main() -> None:
    items = json.load(sys.stdin)["items"]
    tickets = 0
    longest: dict[str, tuple[int, str]] = {}
    for entry in items:
        task = entry.get("item", entry)
        task = task.get("task", task)
        record = (task.get("metadata") or {}).get(KEY)
        if not isinstance(record, dict):
            continue
        tickets += 1
        for field in ("owning_runs", "basis", "drafts"):
            if field in record:
                size = len(render(record[field]))
                if size > longest.get(field, (0, ""))[0]:
                    longest[field] = (size, task.get("url") or str(task.get("id")))
    print(f"items read: {len(items)}; tickets carrying {KEY}: {tickets}")
    for field, (size, where) in sorted(longest.items()):
        print(f"longest rendered {field}: {size} characters, on {where}")


if __name__ == "__main__":
    main()
