/* i228_proxy.cjs — I-228 核验静态代理（自建测试进程）。
 * /api/session_history/* → 私有 session_history 只读查询服务（HTTP）；
 * 其余 /api/* 可选转发到实参目标（如生产云 HTTPS，用于真实端到端；
 * 未给目标时返回 502，由页面拦截层兜底）；静态服务 board_ui/dist。
 * 用法：node i228_proxy.cjs <history_port> [port] [api_target_https_port] */
const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");

const HISTORY_PORT = parseInt(process.argv[2] || "58795", 10);
const PORT = parseInt(process.argv[3] || "4190", 10);
const API_HTTPS_PORT = parseInt(process.argv[4] || "0", 10);   // 云端口（可选）
const API_HOST = process.argv[5] || "127.0.0.1";               // 云主机（可选）
const DIST = path.join(__dirname, "..", "dist");
const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
               ".png": "image/png", ".svg": "image/svg+xml", ".json": "application/json",
               ".ico": "image/x-icon", ".woff2": "font/woff2" };

const server = http.createServer((req, res) => {
  if (req.url.startsWith("/api/session_history/")) {
    const up = http.request({ hostname: "127.0.0.1", port: HISTORY_PORT,
      method: req.method, path: req.url,
      headers: { ...req.headers, host: "127.0.0.1:" + HISTORY_PORT } },
      (ur) => { res.writeHead(ur.statusCode, ur.headers); ur.pipe(res); });
    up.on("error", () => { res.writeHead(502, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: false, reason: "session_history 服务不可达" })); });
    req.pipe(up);
    return;
  }
  if (req.url.startsWith("/api/") && API_HTTPS_PORT) {
    const up = https.request({ hostname: API_HOST, port: API_HTTPS_PORT,
      method: req.method, path: req.url, rejectUnauthorized: false,
      headers: { ...req.headers, host: API_HOST + ":" + API_HTTPS_PORT } },
      (ur) => { res.writeHead(ur.statusCode, ur.headers); ur.pipe(res); });
    up.on("error", () => { res.writeHead(502, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: false, reason: "api 目标不可达" })); });
    req.pipe(up);
    return;
  }
  if (req.url.startsWith("/api/")) {
    res.writeHead(502, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: false, reason: "无 api 转发目标（仅历史服务）" }));
    return;
  }
  const rel = req.url.split("?")[0].replace(/^\/+/, "").replace(/^app\/+/, "");
  let file = path.normalize(path.join(DIST, rel));
  if (!file.startsWith(DIST)) { res.writeHead(403); res.end("forbidden"); return; }
  if (!rel || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    file = path.join(DIST, "index.html");
  }
  const ext = path.extname(file).toLowerCase();
  res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
});
server.listen(PORT, "127.0.0.1", () => {
  console.log("i228 proxy on http://127.0.0.1:" + PORT
    + " history->" + HISTORY_PORT
    + (API_HTTPS_PORT ? " api->https://" + API_HOST + ":" + API_HTTPS_PORT : ""));
});
