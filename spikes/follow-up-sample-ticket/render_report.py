"""Print the board fields and the `<details>` blocks of the sample, from verify-render.sh's read."""

import json
import re
import sys

issue = json.load(sys.stdin)["data"]["repository"]["issue"]
print("issue:", issue["url"], issue["state"])
for item in issue["projectItems"]["nodes"]:
    project = item["project"]
    print(f"board item on project {project['number']} ({project['title']}):")
    for value in item["fieldValues"]["nodes"]:
        if "field" in value:
            print(f"  {value['field']['name']}: {json.dumps(value.get('text', value.get('name')))}")
print("body has an Owning runs section:", "## Owning runs" in issue["body"])


def blocks(where: str, html: str) -> None:
    for m in re.finditer(r"<details[^>]*>.*?</details>", html, re.S):
        tag = re.match(r"<details[^>]*>", m.group(0)).group(0)
        summary = re.search(r"<summary>(.*?)</summary>", m.group(0), re.S).group(1)
        rendered = [t for t in ("<ul", "<li", "<code", "<p") if t in m.group(0)]
        print(f"{where}: {tag} summary={summary!r} collapsed={'open' not in tag} rendered={rendered}")


blocks("body", issue["bodyHTML"])
for comment in issue["comments"]["nodes"]:
    blocks(comment["url"], comment["bodyHTML"])
