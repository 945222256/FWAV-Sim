/* i234_real_proxy.cjs — I-234 真实联调静态代理（自建测试进程）。
 * /api/console/pending_work* → 隔离云（真实端点代码，HTTPS）；
 * 其余 /api/* → 生产云（只读 GET，由页面拦截层再加零非 GET 防线）；
 * 静态服务 board_ui/dist。用法：node i234_real_proxy.cjs <iso> <port> <prod_host> <prod_port> */
const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");

const ISO_PORT = parseInt(process.argv[2] || "9465", 10);
const PORT = parseInt(process.argv[3] || "4193", 10);
const PROD_HOST = process.argv[4] || "101.96.207.208";
const PROD_PORT = parseInt(process.argv[5] || "443", 10);
const DIST = path.join(__dirname, "..", "dist");
const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
               ".png": "image/png", ".svg": "image/svg+xml", ".json": "application/json",
               ".ico": "image/x-icon", ".woff2": "font/woff2" };

const server = http.createServer((req, res) => {
  const upHttps = (port, host) => {
    const up = https.request({ hostname: host, port, method: req.method,
      path: req.url, rejectUnauthorized: false,
      headers: { ...req.headers, host: host + ":" + port } },
      (ur) => { res.writeHead(ur.statusCode, ur.headers); ur.pipe(res); });
    up.on("error", () => { res.writeHead(502, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: false, reason: "上游不可达 " + host + ":" + port })); });
    req.pipe(up);
  };
  if (req.url.startsWith("/api/console/pending_work")) {
    upHttps(ISO_PORT, "127.0.0.1");
    return;
  }
  if (req.url.startsWith("/api/")) {
    upHttps(PROD_PORT, PROD_HOST);
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
  console.log("i234 real proxy " + PORT + " pending_work->127.0.0.1:" + ISO_PORT
    + " api->https://" + PROD_HOST + ":" + PROD_PORT);
});
