/* t5_stop.mjs — I-126 末发：长任务 + 5 秒杀子进程，回合执行宿主定音。 */
import { spawn } from "node:child_process";
import { createConnection } from "node:net";
import { writeFileSync } from "node:fs";

const ZC = "C:/Users/Anni/AppData/Local/Programs/ZCode/resources/glm/zcode.cjs";
const OUT = "G:/Playground/isaac_flapper_sim/logs";
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function svc(payload, timeoutMs = 60000) {
  return new Promise((resolve) => {
    const sock = createConnection({ host: "127.0.0.1", port: 58771 });
    let buf = "";
    const t = setTimeout(() => { sock.destroy(); resolve(null); }, timeoutMs);
    sock.on("error", () => { clearTimeout(t); resolve(null); });
    sock.on("connect", () => sock.write(JSON.stringify(payload) + "\n"));
    sock.on("data", (d) => {
      buf += d.toString("utf8");
      if (buf.includes("\n")) {
        clearTimeout(t);
        try { resolve(JSON.parse(buf.trim())); } catch { resolve(null); }
        sock.destroy();
      }
    });
  });
}
function startServer() {
  const child = spawn(process.execPath, [ZC, "app-server"], {
    stdio: ["pipe", "pipe", "pipe"], env: process.env });
  const waiters = new Map();
  let buf = "";
  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (d) => {
    buf += d;
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (!line) continue;
      let m; try { m = JSON.parse(line); } catch { continue; }
      if (m.id && m.method && String(m.id).startsWith("server-")) {
        const r = m.method === "session/requestRuntimePreferences"
          ? { nativeSearchEnhancementsEnabled: false } : {};
        child.stdin.write(JSON.stringify({ id: m.id, result: r }) + "\n");
        continue;
      }
      const w = waiters.get(m.id);
      if (w) { waiters.delete(m.id); w(m); }
    }
  });
  const call = (msg, ms = 60000) => new Promise((res, rej) => {
    const t = setTimeout(() => { waiters.delete(msg.id); rej(new Error("超时")); }, ms);
    waiters.set(msg.id, (r) => { clearTimeout(t); res(r); });
    child.stdin.write(JSON.stringify(msg) + "\n");
  });
  return { child, call, ready: new Promise(r => setTimeout(r, 2500)), kill: () => child.kill() };
}
const sidOf = (r) => r?.result?.sessionId
  || (JSON.stringify(r).match(/sess_[0-9a-f-]{36}/g) || []).pop();

// 先经服务建癸并做一次已证快的基线（数到五十）。
const d1 = await svc({ op: "dispatch_new", title: "I-126 宿主定音会话癸",
  message: "I126癸M1：请勿调用任何工具，从一数到五十，每行十个。数完写癸一完。" });
const GUI = d1.session;
console.log("癸:", GUI, "基线核验:", d1.readback_verified);

// 长任务：三千字，verify:false 发射。
const M2 = "I126癸M2长文", TAIL = "癸二完";
const fire = await svc({ op: "dispatch_resume", sid: GUI, verify: false,
  message: `${M2}：请勿调用任何工具，写一篇约三千字的关于航海天文钟历史的文章，完整写完，最后以"${TAIL}"三字收尾。` }, 30000);
console.log("长文已发射:", JSON.stringify(fire));

// 5 秒后杀子进程。
await sleep(5000);
const st0 = await svc({ op: "status" });
const k = await svc({ op: "kill_child" }, 30000);
console.log("杀于 T+5s:", JSON.stringify(k));
await sleep(9000);
const st1 = await svc({ op: "status" });
console.log("自愈:", st0.restarts, "->", st1.restarts, "alive:", st1.child_alive);

// 观察者轮询完成标记（独立进程，120 秒）。
const R = await startServer(); await R.ready;
let done = false, firstHit = null;
for (let i = 0; i < 24 && !done; i++) {
  await sleep(5000);
  const rb = await R.call({ id: 900 + i, method: "session/messages", params: { sessionId: GUI } });
  if (JSON.stringify(rb?.result?.messages || []).includes(TAIL)) {
    done = true; firstHit = i * 5 + 5;
  }
}
R.kill();
writeFileSync(`${OUT}/i126_t5_execution_host.json`, JSON.stringify({
  at: new Date().toISOString(), session: GUI, baseline: d1,
  killedAtS: 5, heal: { restarts: [st0.restarts, st1.restarts], alive: st1.child_alive },
  longTaskCompleted: done, observedCompleteAtS: firstHit,
  reading: done
    ? "长回合在驱动子进程被杀后仍完成：回合执行不在派发子进程内（共享宿主执行）"
    : "长回合被杀中断：执行在派发子进程内" }, null, 2));
console.log("长文完成=" + done + (done ? "（" + firstHit + "s 处观测到）→ 执行宿主不在子进程" : " → 杀进程为有效停止通道"));
process.exit(0);
