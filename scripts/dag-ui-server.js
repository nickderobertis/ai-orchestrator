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
// Every environment value below is checked before it is used, because each one
// becomes something whose own failure blames the wrong thing: a served directory,
// a proxy target, and a listening address. Refusing at startup names the variable
// that is wrong; the alternative is a page of 404s, a 502 attributed to the read
// API, or a server listening somewhere nobody asked for.
const refuse = (why) => {
  console.error(`dag-ui: ${why}`);
  process.exit(2);
};

const here = import.meta.dir;
const dist = process.env.DAG_UI_DIST ?? `${here}/../node_modules/onepipeline-ui/dist`;
if (!(await Bun.file(`${dist}/index.html`).exists())) {
  refuse(
    process.env.DAG_UI_DIST
      ? `DAG_UI_DIST must name a directory holding a published bundle; ${dist} has no index.html`
      : `no published bundle at ${dist}; run 'just bootstrap' to install it, then retry`,
  );
}
const index = Bun.file(`${dist}/index.html`);

// Read only when it is needed: an invocation that names its own API address never
// consults the file, and validated when it is, because an empty or misshapen one
// would otherwise become a proxy target that fails later as a 502 blaming the read
// API for a file this repository got wrong.
const defaultApi = async () => {
  const source = `${here}/../config/read-api.address`;
  const address = (await Bun.file(source).text().catch(() => "")).trim();
  if (!/^[^\s:]+:\d{1,5}$/.test(address)) {
    refuse(`${source} must hold one HOST:PORT, not ${address || "nothing"}`);
  }
  return `http://${address}`;
};

// A named address is held to the same shape the file is: it is the origin every
// proxied request is prefixed with, so anything but an absolute http(s) origin
// produces a fetch that throws and is reported as the read API refusing.
const named = process.env.DAG_UI_API_URL;
if (named !== undefined) {
  const parsed = URL.parse(named);
  if (parsed === null || !["http:", "https:"].includes(parsed.protocol)) {
    refuse(`DAG_UI_API_URL must be an http(s) URL, not ${named || "nothing"}`);
  }
}
const api = (named ?? (await defaultApi())).replace(/\/+$/, "");

const requested = process.env.DAG_UI_PORT ?? "4173";
const port = Number(requested);
if (!Number.isInteger(port) || port < 0 || port > 65535) {
  refuse(`DAG_UI_PORT must be a port number, not ${requested}`);
}

// Bun takes an unresolvable hostname as a reason to throw from `serve`, which reads
// as the server crashing rather than as one variable being wrong.
const hostname = process.env.DAG_UI_HOST ?? "127.0.0.1";
if (!/^([A-Za-z0-9._-]+|\[[0-9A-Fa-f:.]+\])$/.test(hostname)) {
  refuse(`DAG_UI_HOST must be a hostname, an IPv4 address, or a bracketed IPv6 address, not ${hostname || "nothing"}`);
}

const server = Bun.serve({
  port,
  hostname,
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
