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
// The read API's own address has one source, `config/read-api.address`, because
// `just telemetry-server` binds it and this proxies to it: two literals would let
// the two recipes stop finding each other. The published bundle's location has one
// source for the same reason — both `just dag-ui` and `just dag-ui-screens` start
// this server, and a bundle layout corrected in one of them only would leave the
// other serving nothing.
const here = import.meta.dir;
const dist = process.env.DAG_UI_DIST ?? `${here}/../node_modules/onepipeline-ui/dist`;

// Read only when it is needed: an invocation that names its own API address never
// consults the file, and validated when it is, because an empty or misshapen one
// would otherwise become a proxy target that fails later as a 502 blaming the read
// API for a file this repository got wrong.
const defaultApi = async () => {
  const source = `${here}/../config/read-api.address`;
  const address = (await Bun.file(source).text().catch(() => "")).trim();
  if (!/^[^\s:]+:\d{1,5}$/.test(address)) {
    console.error(`dag-ui: ${source} must hold one HOST:PORT, not ${address || "nothing"}`);
    process.exit(2);
  }
  return `http://${address}`;
};

const api = (process.env.DAG_UI_API_URL ?? (await defaultApi())).replace(/\/+$/, "");

const requested = process.env.DAG_UI_PORT ?? "4173";
const port = Number(requested);
if (!Number.isInteger(port) || port < 0 || port > 65535) {
  console.error(`dag-ui: DAG_UI_PORT must be a port number, not ${requested}`);
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
      try {
        return await fetch(`${api}${url.pathname}${url.search}`, {
          method: request.method,
          headers: request.headers,
          body: request.body,
          signal: request.signal,
          // Bun requires this for a request that streams a body; a GET has none.
          duplex: "half",
        });
      } catch (reason) {
        // The read API not being up is the ordinary case here — an operator starts
        // the two in two shells — so it is answered in the shape the view already
        // knows how to read, naming the address that refused. A thrown fetch would
        // otherwise render a runtime error page into an XHR.
        return Response.json(
          {
            error: {
              code: "read_api_unreachable",
              message: `${api} did not answer (${reason}); start it with 'just telemetry-server', or point DAG_UI_API_URL elsewhere`,
            },
          },
          { status: 502 },
        );
      }
    }
    if (url.pathname.includes("..")) {
      return new Response("not found", { status: 404 });
    }
    const file = Bun.file(`${dist}${url.pathname}`);
    return (await file.exists()) ? new Response(file) : new Response(index);
  },
});

console.log(`dag-ui: serving ${dist} on http://${server.hostname}:${server.port} against ${api}`);
