import { expect, test } from "bun:test";

import { layoutDag } from "../src/index.js";

test("a package consumer cannot render an unsupported runtime node state", () => {
  const payload = JSON.parse(
    '{"nodes":[{"id":"agent","label":"Agent","kind":"agent","state":"paused"}],"edges":[]}',
  );

  expect(() => layoutDag(payload)).toThrow(
    "DAG node agent has unsupported state paused",
  );
});
