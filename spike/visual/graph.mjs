// Stand-in for the unreleased `onetaskgraph project graph <ID> --format mermaid
// --direction auto`: the verb's agreed mermaid text, from a fixture plan. The real
// `design-doc-plan-dag` node replaces this with the real verb.
import { readFileSync } from "node:fs";

const esc = (s) => s.replace(/[\r\n]+/g, " ").replace(/#/g, "#35;").replace(/"/g, "#quot;")
  .replace(/</g, "#lt;").replace(/>/g, "#gt;");

export function graph(plan) {
  const byId = new Map(plan.tasks.map((t) => [t.id, t]));
  // Topological order, ties broken by title.
  const indeg = new Map(plan.tasks.map((t) => [t.id, t.depends_on.length]));
  const ready = plan.tasks.filter((t) => !t.depends_on.length);
  const order = [];
  while (ready.length) {
    ready.sort((a, b) => a.title.localeCompare(b.title));
    const t = ready.shift();
    order.push(t);
    for (const u of plan.tasks) if (u.depends_on.includes(t.id) && indeg.set(u.id, indeg.get(u.id) - 1).get(u.id) === 0) ready.push(u);
  }
  const rank = new Map();
  for (const t of order) rank.set(t.id, t.depends_on.length ? 1 + Math.max(...t.depends_on.map((d) => rank.get(d))) : 0);
  const widths = {};
  for (const r of rank.values()) widths[r] = (widths[r] || 0) + 1;
  const direction = Math.max(...Object.values(widths)) > Object.keys(widths).length ? "LR" : "TD";
  const node = new Map(order.map((t, k) => [t.id, `n${k}`]));
  const lines = [`flowchart ${direction}`];
  for (const t of order) lines.push(`  ${node.get(t.id)}["${esc(t.title)}"]`);
  for (const t of order) for (const d of t.depends_on) lines.push(`  ${node.get(d)} --> ${node.get(t.id)}`);
  return { text: lines.join("\n") + "\n", direction, byId };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.stdout.write(graph(JSON.parse(readFileSync(process.argv[2], "utf8"))).text);
}
