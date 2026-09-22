// Minimal same-origin front for OpenMuse: serves the static Expo web build and
// reverse-proxies /api/* to the loopback-bound API server (streaming-safe for SSE).
import http from "node:http";
import { createReadStream, existsSync, statSync } from "node:fs";
import { extname, join, normalize } from "node:path";

const PORT = Number(process.env.WEB_PORT ?? 8080);
const WEB_ROOT = process.env.WEB_ROOT ?? "/app/apps/mobile/dist/web";
const API_HOST = process.env.API_HOST ?? "127.0.0.1";
const API_PORT = Number(process.env.API_PORT ?? 8787);

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".otf": "font/otf",
  ".wasm": "application/wasm",
  ".mp4": "video/mp4",
  ".pdf": "application/pdf",
};

const REQUEST_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "proxy-connection",
  "te",
  "trailer",
  "host",
]);
const RESPONSE_HOP_HEADERS = new Set(["connection", "keep-alive", "transfer-encoding", "upgrade"]);

function proxyApi(req, res) {
  const headers = {};
  for (const [key, value] of Object.entries(req.headers))
    if (!REQUEST_HOP_HEADERS.has(key.toLowerCase())) headers[key] = value;
  headers.host = `${API_HOST}:${API_PORT}`;
  const upstream = http.request(
    { host: API_HOST, port: API_PORT, method: req.method, path: req.url, headers },
    (up) => {
      const out = {};
      for (const [key, value] of Object.entries(up.headers))
        if (!RESPONSE_HOP_HEADERS.has(key.toLowerCase())) out[key] = value;
      res.writeHead(up.statusCode ?? 502, out);
      up.pipe(res);
    },
  );
  upstream.on("error", (error) => {
    if (!res.headersSent)
      res.writeHead(502, { "content-type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ error: `OpenMuse API is unavailable (${error.code ?? error.message})` }));
  });
  res.on("close", () => upstream.destroy());
  req.pipe(upstream);
}

function resolveStatic(pathname) {
  const normalized = normalize(pathname).replace(/^[/\\]+/, "");
  if (normalized.split(/[/\\]/).includes("..")) return null;
  let filePath = join(WEB_ROOT, normalized);
  try {
    if (existsSync(filePath) && statSync(filePath).isDirectory())
      filePath = join(filePath, "index.html");
    if (!existsSync(filePath) || !statSync(filePath).isFile())
      filePath = join(WEB_ROOT, "index.html");
  } catch {
    filePath = join(WEB_ROOT, "index.html");
  }
  return existsSync(filePath) ? filePath : null;
}

function serveStatic(req, res) {
  let pathname;
  try {
    pathname = decodeURIComponent(new URL(req.url ?? "/", "http://localhost").pathname);
  } catch {
    res.writeHead(400);
    return res.end("Bad request");
  }
  const filePath = resolveStatic(pathname);
  if (!filePath) {
    res.writeHead(404);
    return res.end("Not found");
  }
  const headers = {
    "content-type": MIME[extname(filePath).toLowerCase()] ?? "application/octet-stream",
  };
  if (filePath.endsWith("index.html")) headers["cache-control"] = "no-store";
  res.writeHead(200, headers);
  if (req.method === "HEAD") return res.end();
  createReadStream(filePath).pipe(res);
}

const server = http.createServer((req, res) => {
  const url = req.url ?? "/";
  if (url === "/api" || url.startsWith("/api/") || url.startsWith("/api?"))
    return proxyApi(req, res);
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.writeHead(405);
    return res.end("Method not allowed");
  }
  serveStatic(req, res);
});
server.listen(PORT, "0.0.0.0", () => console.log(`OpenMuse web+api proxy listening on :${PORT}`));
