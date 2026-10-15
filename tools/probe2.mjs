/* probe2.mjs — 探测 session/new / session/send / conversationFrame 参数形状。
 * 用假参数触发服务端校验错误，从报错反推必填字段。 */
import { spawn } from "node:child_process";

const ZC = "C:/Users/Anni/AppData/Local/Programs/ZCode/resources/glm/zcode.cjs";
const child = spawn(process.execPath, [ZC, "app-server"], {
  stdio: ["pipe", "pipe", "pipe"], env: process.env,
});
child.stdout.setEncoding("utf8");
child.stderr.setEncoding("utf8");
let buf = "";
child.stdout.on("data", (d) => {
  buf += d;
  let idx;
  while ((idx = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    if (line) console.log("[IN] " + line.slice(0, 500));
  }
});
child.stderr.on("data", (d) => process.stdout.write("[ERR] " + d.slice(0, 300)));

const probes = [
  { id: 10, method: "session/new", params: {} },
  { id: 11, method: "session/send", params: {} },
  { id: 12, method: "conversationFrame", params: {} },
  { id: 13, method: "session/status", params: {} },
];

let i = 0;
const timer = setInterval(() => {
  if (i < probes.length) {
    const p = probes[i++];
    child.stdin.write(JSON.stringify(p) + "\n");
    console.log("[OUT] " + JSON.stringify(p));
  } else {
    clearInterval(timer);
    child.kill();
    process.exit(0);
  }
}, 1200);
