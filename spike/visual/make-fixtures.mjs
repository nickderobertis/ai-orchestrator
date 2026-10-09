// Writes the three fixture plans under fixtures/: deterministic, shaped like this host's
// real plans (titles of 66-79 characters, one or two prerequisites from the rank before).
import { writeFileSync } from "node:fs";

const verbs = ["Add", "Teach", "Hold", "Record", "Render", "Route", "Measure", "Refuse", "Carry", "Settle", "Expose", "Bound"];
const subjects = ["the plan store", "the design document", "the follow-up board", "the engine's channel",
  "a spike report", "the pre-push gate", "a lifecycle node", "the observer graph", "the manager's allowlist",
  "a dispatched worker", "the publication closeout", "the release hold"];
const tails = ["so a reader sees what changed", "behind the onevcs merge path", "with one source for its contract",
  "against the pinned engine release", "and prove it with a journey", "without a second copy of the rule",
  "on every dispatch this host makes", "so a failed node names its cause"];

let seed = 7;
const rand = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
const pick = (a) => a[Math.floor(rand() * a.length)];

function title(i) {
  for (;;) {
    const t = `${pick(verbs)} ${pick(subjects)} ${pick(tails)} (#${i})`;
    if (t.length >= 66 && t.length <= 79) return t;
  }
}

function plan(name, ranks) {
  const tasks = [];
  let prev = [];
  ranks.forEach((width, r) => {
    const cur = [];
    for (let j = 0; j < width; j++) {
      const id = `${name}-t${String(tasks.length + 1).padStart(3, "0")}`;
      const deps = [];
      if (prev.length) {
        deps.push(prev[Math.floor(rand() * prev.length)]);
        if (prev.length > 1 && rand() < 0.5) {
          const d = prev[Math.floor(rand() * prev.length)];
          if (!deps.includes(d)) deps.push(d);
        }
      }
      tasks.push({ id, title: title(tasks.length + 1), depends_on: deps });
      cur.push(id);
    }
    prev = cur;
  });
  writeFileSync(`fixtures/${name}.json`, JSON.stringify({ project: `authoring:${name}`, tasks }, null, 2) + "\n");
}

plan("plan-10", [1, 4, 2, 1, 2]);
plan("plan-48", [1, 28, 15, 4]);
plan("plan-100", [1, 56, 33, 10]);
