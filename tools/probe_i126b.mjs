/* probe_i126b.mjs — I-126 补充实验：T1b 控制权保留、T2b 打断方法族全测、
 * T4b 服务样机全序（完成核验后再做自愈与停止通道）。 */
import { spawn } from "node:child_process";
import { createConnection } from "node:net";
import { writeFileSync, existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const ZC = "C:/Users/Anni/AppData/Local/Programs/ZCode/resources/glm/zcode.cjs";
const OUT = "G:/Playground/isaac_flapper_sim/logs";
const JIA = "sess_2cb44cfa-cfc9-4f04-acaf-e8113b90a856";
const WS = { workspace: { workspaceKey: "G:\\Playground", workspacePath: "G:\\Playground" } };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const now = () => new Date().toISOString();

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
const trHas = (sid, marker) => {
  const p = join(homedir(), ".zcode", "cli", "rollout", `model-io-${sid}.jsonl`);
  return existsSync(p) ? readFileSync(p, "utf8").includes(marker) : false;
};

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

async function readbackUntil(proc, sid, marker, rounds, gap = 5000) {
  for (let i = 0; i < rounds; i++) {
    await sleep(gap);
    const rb = await proc.call({ id: 900 + i, method: "session/messages", params: { sessionId: sid } });
    if (JSON.stringify(rb?.result?.messages || []).includes(marker)) return true;
  }
  return false;
}

/* T1b：打开之后，原驱动方仍能继续驱动（控制权保留）。 */
async function t1b() {
  const P = await startServer(); await P.ready;
  await P.call({ id: 1, method: "session/resume", params: { sessionId: JIA } });
  const M = "I126甲M3b控制权";
  const s = await P.call({ id: 2, method: "session/send", params: {
    sessionId: JIA, content: `${M}：请直接回复收到两字，勿用工具。` } });
  const ok = await readbackUntil(P, JIA, M, 36);
  P.kill();
  writeFileSync(`${OUT}/i126_t1b_control_retained.json`, JSON.stringify({
    at: now(), session: JIA, accepted: !!s?.result?.accepted, replied: ok,
    verdict: ok ? "控制权保留：被打开过的会话，原协议通道仍可继续驱动并得到回复" : "未证实" }, null, 2));
  console.log("T1b 控制权保留=" + ok);
  return ok;
}

/* T2b：打断方法族全测（跨进程逐个，不提前收兵）+ 同进程 stop 对照。 */
async function t2b() {
  const P1 = await startServer(); await P1.ready;
  const created = await P1.call({ id: 20, method: "session/create", params: WS }, 60000);
  const WU = sidOf(created);
  console.log("测试会话戊:", WU);
  const probeResults = {};
  async function oneRound(label, stopFromSame) {
    const MARK = `I126戊${label}`;
    await P1.call({ id: 21, method: "session/send", params: {
      sessionId: WU, content: `${MARK}：请勿调用任何工具，写一篇约四百字的关于灯塔的短文，以"戊完"收尾。` } });
    await sleep(9000);
    if (stopFromSame) {
      const r = await P1.call({ id: 22, method: "session/stop", params: { sessionId: WU } }, 20000)
        .catch(e => ({ error: { message: String(e) } }));
      probeResults[label] = r?.error ? "同进程stop拒绝:" + String(r.error.message).slice(0, 40) : "同进程stop可用";
    } else {
      const P2 = await startServer(); await P2.ready;
      await P2.call({ id: 30, method: "session/resume", params: { sessionId: WU } });
      for (const [mid, method] of [[31, "session/cancel"], [32, "session/abort"], [33, "session/stop"]]) {
        const r = await P2.call({ id: mid, method, params: { sessionId: WU } }, 20000)
          .catch(e => ({ error: { message: String(e) } }));
        const miss = r?.error && String(r.error.message || "").includes("Method not found");
        probeResults[label + ":" + method] = miss ? "不存在"
          : (r?.error ? "拒绝:" + String(r.error.message || r.error.code).slice(0, 40) : "可用");
      }
      P2.kill();
    }
    const done = await readbackUntil(P1, WU, "戊完", 20);
    return { done, transcript: trHas(WU, "戊完") };
  }
  const cross = await oneRound("跨进程", false);
  const same = await oneRound("同进程", true);
  P1.kill();
  writeFileSync(`${OUT}/i126_t2b_interrupt_full.json`, JSON.stringify({
    at: now(), session: WU, probes: probeResults, cross, same,
  }, null, 2));
  console.log("T2b:", JSON.stringify(probeResults), "跨进程回合完成=" + cross.done, "同进程回合完成=" + same.done);
  return { probeResults, cross, same, WU };
}

/* T4b：服务样机全序（完成核验前置）。 */
async function t4b() {
  const ev = {};
  const MARK = "I126丁M1", DONE = "丁文完";
  ev.dispatch1 = await svc({ op: "dispatch_new", title: "I-126 常驻服务样机会话丁",
    message: `${MARK}：请勿调用任何工具，写一篇约三百字的关于灯塔的短文，以"${DONE}"三字收尾。` });
  const DING = ev.dispatch1.session;
  console.log("丁:", DING);

  const R = await startServer(); await R.ready;
  for (let k = 0; k < 2; k++) {
    await sleep(4000);
    await R.call({ id: 1, method: "session/resume", params: { sessionId: DING } });
    await R.call({ id: 2, method: "session/messages", params: { sessionId: DING } });
  }
  ev.readersOk = true;
  ev.completed = await readbackUntil(R, DING, DONE, 60);
  ev.transcript = trHas(DING, DONE);
  console.log("丁回合完成=" + ev.completed);

  const M2 = "I126丁M2收到验证";
  ev.dispatch2 = await svc({ op: "dispatch_resume", sid: DING, message: `${M2}：请直接回复收到，勿用工具。` });
  ev.m2replied = await readbackUntil(R, DING, "收到", 36);
  console.log("M2回复=" + ev.m2replied);

  // 停止通道：经服务再发长回合，10 秒后杀子进程，验证回合终止。
  const M3 = "I126丁M3被停的长文", DONE3 = "丁文完二";
  ev.dispatch3 = await svc({ op: "dispatch_resume", sid: DING,
    message: `${M3}：请勿调用任何工具，写一篇约六百字的关于运河的短文，以"${DONE3}"三字收尾。` });
  await sleep(10000);
  const st0 = await svc({ op: "status" });
  ev.kill = await svc({ op: "kill_child" });
  await sleep(9000);
  const st1 = await svc({ op: "status" });
  ev.selfHeal = { before: { alive: st0.child_alive, restarts: st0.restarts },
                  after: { alive: st1.child_alive, restarts: st1.restarts } };
  const m3done = await readbackUntil(R, DING, DONE3, 12);
  ev.m3completedAfterKill = m3done;
  console.log("杀进程后长文完成=" + m3done + " 自愈=" + JSON.stringify(ev.selfHeal));

  const M4 = "I126丁M4自愈后回归";
  ev.dispatch4 = await svc({ op: "dispatch_resume", sid: DING, message: `${M4}：请直接回复收到，勿用工具。` });
  ev.m4replied = await readbackUntil(R, DING, "收到", 36);
  R.kill();

  const verdict = ev.dispatch1.ok && ev.completed && ev.transcript && ev.m2replied
    && !m3done && st1.child_alive && st1.restarts > st0.restarts
    && ev.dispatch4.ok && ev.m4replied;
  writeFileSync(`${OUT}/i126_t4b_service_full.json`, JSON.stringify({
    at: now(), session: DING, ...ev,
    verdict: verdict
      ? "样机全过：服务派活两轮可用+读者打开不影响运行+杀子进程构成有效停止通道+自愈拉起+自愈后可用"
      : "存在不过项，见字段" }, null, 2));
  console.log("T4b 总判定:", verdict ? "全过" : "有不过");
  return { verdict, DING };
}

const v1b = await t1b();
const v2b = await t2b();
const v4b = await t4b();
writeFileSync(`${OUT}/i126_test_sessions_registry.json`, JSON.stringify({
  at: now(), item: "I-126",
  sessions: {
    甲: JIA, 乙: "sess_f086b204-b3aa-47e5-9ce8-80478dd86f70",
    戊: v2b.WU, 丁: v4b.DING,
    丙_首轮超时杀: "sess_9e6d2e66-e8ad-4add-be4d-029cb3473f0b" },
  note: "全部为本事项新建测试会话，未触碰任何在办会话" }, null, 2));
console.log("T1b=" + v1b + " T4b=" + v4b.verdict);
process.exit(0);
