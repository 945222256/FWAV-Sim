/* t4c_final.mjs — I-126 判据四终验（升级版服务）。
 * 1 经服务派短文（含回读核验）→ 2 回合中两轮打开 → 3 恢复再发收到（核验）
 * → 4 verify:false 发数数任务，4 秒后杀子进程=停止通道 → 无完成
 * → 5 自愈与自愈后可用。 */
import { spawn } from "node:child_process";
import { createConnection } from "node:net";
import { writeFileSync } from "node:fs";

const ZC = "C:/Users/Anni/AppData/Local/Programs/ZCode/resources/glm/zcode.cjs";
const OUT = "G:/Playground/isaac_flapper_sim/logs";
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const now = () => new Date().toISOString();

function svc(payload, timeoutMs = 420000) {
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
async function readback(proc, sid, marker, rounds) {
  for (let i = 0; i < rounds; i++) {
    await sleep(5000);
    const rb = await proc.call({ id: 900 + i, method: "session/messages", params: { sessionId: sid } });
    if (JSON.stringify(rb?.result?.messages || []).includes(marker)) return true;
  }
  return false;
}

const ev = {};
const MARK = "I126壬M1", DONE = "壬文完";
ev.d1 = await svc({ op: "dispatch_new", title: "I-126 终验会话壬",
  message: `${MARK}：请勿调用任何工具，写一篇约三百字的灯塔短文，以"${DONE}"三字收尾。` });
const REN = ev.d1.session;
console.log("壬:", REN, "回读核验:", ev.d1.readback_verified);
ev.d1Verified = !!ev.d1.readback_verified;

const R = await startServer(); await R.ready;
for (let k = 0; k < 2; k++) {
  await sleep(3000);
  await R.call({ id: 1, method: "session/resume", params: { sessionId: REN } });
  await R.call({ id: 2, method: "session/messages", params: { sessionId: REN } });
}
ev.readersOk = true;

const M2 = "I126壬M2";
ev.d2 = await svc({ op: "dispatch_resume", sid: REN,
  message: `${M2}：请直接回复收到，勿用工具。` });
console.log("M2 派发核验:", ev.d2.readback_verified);

// 停止通道：不核验发射，4 秒后杀子进程。
const M3 = "I126壬M3数数", TAIL = "壬数完";
ev.d3fired = await svc({ op: "dispatch_resume", sid: REN, verify: false,
  message: `${M3}：请勿调用任何工具，从一数到一百，每行十个数，数完另起一行写"${TAIL}"。` }, 30000);
await sleep(4000);
const st0 = await svc({ op: "status" });
ev.kill = await svc({ op: "kill_child" }, 30000);
await sleep(9000);
const st1 = await svc({ op: "status" });
ev.selfHeal = { before: { alive: st0.child_alive, restarts: st0.restarts },
                after: { alive: st1.child_alive, restarts: st1.restarts } };
ev.m3Done = await readback(R, REN, TAIL, 18);
console.log("杀后数数完成=" + ev.m3Done + "（False=停止通道有效）");

const M4 = "I126壬M4";
ev.d4 = await svc({ op: "dispatch_resume", sid: REN,
  message: `${M4}：请直接回复收到，勿用工具。` });
console.log("M4 派发核验:", ev.d4.readback_verified);
R.kill();

const verdict = ev.d1Verified && ev.d2.readback_verified && !ev.m3Done
  && st1.child_alive && st1.restarts > st0.restarts && ev.d4.readback_verified;
writeFileSync(`${OUT}/i126_t4c_service_final.json`, JSON.stringify({
  at: now(), session: REN, ...ev, verdict: verdict
    ? "样机全过：派发两轮回读核验+打开不影响+杀子进程构成有效停止通道+自愈+自愈后可用"
    : "存在不过项，见字段" }, null, 2));
console.log("T4c 总判定:", verdict ? "全过" : "有不过");
process.exit(verdict ? 0 : 1);
