/* i218v2_static_proxy.cjs — I-218 v2 真实联调静态代理（自建测试进程）。
 * 本地端口 4175 服务 board_ui/dist 静态文件（SPA 回退到 index.html），
 * /api/* 反向代理到隔离云 HTTPS 端口（自签证书不校验签发者，与
 * cloud_client 同口径）。不触碰生产云；进程由探针脚本负责回收。
 * 用法：node i218v2_static_proxy.cjs <cloud_port> [port] */
const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");

const CLOUD_PORT = parseInt(process.argv[2] || "9447", 10);
const PORT = parseInt(process.argv[3] || "4175", 10);
const DIST = path.join(__dirname, "..", "dist");
const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
               ".png": "image/png", ".svg": "image/svg+xml", ".json": "application/json",
               ".ico": "image/x-icon", ".woff2": "font/woff2" };

const server = http.createServer((req, res) => {
  if (req.url.startsWith("/api/")) {
    const opts = { hostname: "127.0.0.1", port: CLOUD_PORT, method: req.method,
      path: req.url, headers: { ...req.headers, host: "127.0.0.1:" + CLOUD_PORT },
      rejectUnauthorized: false };
    const up = https.request(opts, (ur) => {
      res.writeHead(ur.statusCode, ur.headers);
      ur.pipe(res);
    });
    up.on("error", () => { res.writeHead(502, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: false, error: "isolated cloud unreachable" })); });
    req.pipe(up);
    return;
  }
  const rel = req.url.split("?")[0].replace(/^\/+/, "").replace(/^app\/+/, "");
  let file = path.normalize(path.join(DIST, rel));
  if (!file.startsWith(DIST)) { res.writeHead(403); res.end("forbidden"); return; }
  if (!rel || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    file = path.join(DIST, "index.html");       // SPA 回退（hash 路由）
  }
  const ext = path.extname(file).toLowerCase();
  res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
});
server.listen(PORT, "127.0.0.1", () => {
  console.log("i218v2 static proxy on http://127.0.0.1:" + PORT
    + " -> https://127.0.0.1:" + CLOUD_PORT);
});
