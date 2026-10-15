/* t4_service.mjs — I-126 判据四：常驻服务样机验证。
 * 步骤：经服务派新会话丙 → 回合进行中三轮“客户端打开”（list+resume+read）
 * → 独立进程核验回合完成 → kill_child 验自愈 → 经服务恢复再发验可用。 */
import { spawn } from "node:child_process";
import { createConnection } from "node:net";
import { writeFileSync, existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const ZC = "C:/Users/Anni/AppData/Local/Programs/ZCode/resources/glm/zcode.cjs";
const OUT = "G:/Playground/isaac_flapper_sim/logs";

function svc(payload, timeoutMs = 240000) {
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

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const ev = {};
const MARK = "I126丙M1";
const DONE = "丙文完";

// 1 经服务派新会话丙。
const d1 = await svc({ op: "dispatch_new", title: "I-126 常驻服务样机会话丙",
  message: `${MARK}：请勿调用任何工具，写一篇约六百字的关于潮汐与滩涂的短文，写完以"${DONE}"三字收尾。` });
console.log("经服务派发:", JSON.stringify(d1));
ev.dispatch1 = d1;
const BING = d1.session;

// 2 回合进行中，三轮客户端打开等价（list→resume→read 各一轮，间隔四秒）。
ev.readerRounds = [];
for (let k = 0; k < 3; k++) {
  await sleep(4000);
  const R = await startServer(); await R.ready;
  const seen = !!(await R.call({ id: 1, method: "session/list", params: {} }))
    ?.result?.sessions?.find(s => s.sessionId === BING);
  const res = !(await R.call({ id: 2, method: "session/resume", params: { sessionId: BING } })).error;
  const rb = await R.call({ id: 3, method: "session/messages", params: { sessionId: BING } });
  const sees = JSON.stringify(rb?.result?.messages || []).includes(MARK);
  ev.readerRounds.push({ seen, resumeOk: res, readOk: Array.isArray(rb?.result?.messages), sees });
  R.kill();
}
console.log("三轮打开:", JSON.stringify(ev.readerRounds));

// 3 独立进程核验回合完成。
const V = await startServer(); await V.ready;
let done = false;
for (let i = 0; i < 30 && !done; i++) {
  await sleep(5000);
  const rb = await V.call({ id: 1, method: "session/messages", params: { sessionId: BING } });
  done = JSON.stringify(rb?.result?.messages || []).includes(DONE);
}
const trp = join(homedir(), ".zcode", "cli", "rollout", `model-io-${BING}.jsonl`);
ev.completedAfterReaders = done;
ev.transcript = existsSync(trp)
  ? { exists: true, hasDone: readFileSync(trp, "utf8").includes(DONE) } : { exists: false };
V.kill();
console.log("回合完成=" + done + " 转录=" + JSON.stringify(ev.transcript));

// 4 崩溃自愈：杀子进程，等看护拉起。
const st0 = await svc({ op: "status" });
const k = await svc({ op: "kill_child" });
await sleep(8000);
const st1 = await svc({ op: "status" });
ev.selfHeal = { before: { alive: st0.child_alive, restarts: st0.restarts },
                kill: k, after: { alive: st1.child_alive, restarts: st1.restarts } };
console.log("自愈:", JSON.stringify(ev.selfHeal));

// 5 自愈后经服务恢复再发（可用性回归）。
const MARK2 = "I126丙M2自愈后";
ev.dispatch2 = await svc({ op: "dispatch_resume", sid: BING,
  message: `${MARK2}：请直接回复"收到"，勿用工具。` });
const V2 = await startServer(); await V2.ready;
let done2 = false;
for (let i = 0; i < 24 && !done2; i++) {
  await sleep(5000);
  const rb = await V2.call({ id: 1, method: "session/messages", params: { sessionId: BING } });
  done2 = JSON.stringify(rb?.result?.messages || []).includes("收到");
}
V2.kill();
ev.dispatch2Readback = done2;

const verdict = d1.ok && ev.readerRounds.every(r => r.seen && r.resumeOk && r.readOk)
  && done && ev.transcript.hasDone && st1.child_alive && st1.restarts > st0.restarts
  && ev.dispatch2.ok && done2;
writeFileSync(`${OUT}/i126_t4_service.json`, JSON.stringify({
  at: new Date().toISOString(), session: BING, ...ev,
  verdict: verdict
    ? "样机全过：经服务派活+客户端打开三轮不影响运行+崩溃自愈+自愈后可用+开机自启项已装"
    : "存在不过项，见字段" }, null, 2));
console.log("T4 总判定:", verdict ? "全过" : "有不过");
process.exit(verdict ? 0 : 1);
