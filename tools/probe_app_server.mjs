/* probe_app_server.mjs — 探测 ZCode app-server stdio 协议。
 * 启动进程，捕获启动输出，依次试探协议帧，打印全部收发。 */
import { spawn } from "node:child_process";

const ZC = "C:/Users/Anni/AppData/Local/Programs/ZCode/resources/glm/zcode.cjs";
const child = spawn(process.execPath, [ZC, "app-server"], {
  stdio: ["pipe", "pipe", "pipe"],
  env: process.env,
});

const log = (tag, obj) => console.log("[" + tag + "] " + JSON.stringify(obj).slice(0, 600));

child.stdout.setEncoding("utf8");
child.stderr.setEncoding("utf8");
let buf = "";
child.stdout.on("data", (d) => {
  buf += d;
  let idx;
  while ((idx = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    if (line) log("IN ", safe(line));
  }
});
child.stderr.on("data", (d) => process.stdout.write("[ERR] " + d.slice(0, 400)));

function safe(line) {
  try { return JSON.parse(line); } catch { return line.slice(0, 200); }
}

const probes = [
  { id: 1, method: "initialize", params: { protocolVersion: 1, client: "probe" } },
  { id: 2, method: "session/list", params: {} },
  { id: 3, method: "ping", params: {} },
];

let i = 0;
const timer = setInterval(() => {
  if (i < probes.length) {
    const p = probes[i++];
    child.stdin.write(JSON.stringify(p) + "\n");
    log("OUT", p);
  } else {
    clearInterval(timer);
    child.kill();
    process.exit(0);
  }
}, 1500);
