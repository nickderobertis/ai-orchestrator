// Serve the published DAG Observatory bundle with the read API on the same origin.
//
// `onepipeline-ui` ships a built static bundle and nothing else, and that bundle
// asks for `/api/v2/...` and `/healthz` relative to wherever it was served from —
// it declares no API host. The published read API (`onepipeline-api serve`) serves
// the data and not the bundle. So something has to put the two behind one origin,
// and a same-origin proxy is the only thing that works in a browser: a second port
// would make every request cross-origin, and the read API sends no CORS headers.
//
// That is this file's whole job. It holds no knowledge of the API's routes beyond
// the two prefixes above, and none at all of the view.
const dist = process.env.DAG_UI_DIST;
const api = (process.env.DAG_UI_API_URL ?? "http://127.0.0.1:8765").replace(/\/+$/, "");
const port = Number(process.env.DAG_UI_PORT ?? 4173);

if (!dist) {
  console.error("dag-ui: DAG_UI_DIST must name the published bundle; run this through 'just dag-ui'");
  process.exit(2);
}

const index = Bun.file(`${dist}/index.html`);
if (!(await index.exists())) {
  console.error(`dag-ui: no published bundle at ${dist}; run 'just bootstrap' to install it, then retry`);
  process.exit(2);
}

const server = Bun.serve({
  port,
  hostname: process.env.DAG_UI_HOST ?? "127.0.0.1",
  // The bundle is a single-page app, so an unknown path is a client route rather
  // than a missing file. `..` is refused outright: this serves one directory, and
  // a path that climbs out of it is never a route the app asked for.
  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api") || url.pathname === "/healthz") {
      return fetch(`${api}${url.pathname}${url.search}`, {
        method: request.method,
        headers: request.headers,
        body: request.body,
        signal: request.signal,
        // Bun requires this for a request that streams a body; a GET has none.
        duplex: "half",
      });
    }
    if (url.pathname.includes("..")) {
      return new Response("not found", { status: 404 });
    }
    const file = Bun.file(`${dist}${url.pathname}`);
    return (await file.exists()) ? new Response(file) : new Response(index);
  },
});

console.log(`dag-ui: serving ${dist} on http://${server.hostname}:${server.port} against ${api}`);
