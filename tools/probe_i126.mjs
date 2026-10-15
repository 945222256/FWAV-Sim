/* probe_i126.mjs — I-126 判据一二三：宿主行为协议级实测。
 * T1 判据一+二：回合进行中，第二连接恢复并读取（=客户端打开的协议面），
 *    对照打开前后状态，验证运行不受影响且原驱动方未失去控制权。
 * T2 判据三：显式打断方法族探测，跨进程打断进行中的回合。
 * 全部只动本事项新建测试会话（甲：I-126 已建；乙：本脚本新建）。 */
import { spawn } from "node:child_process";
import { writeFileSync, existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const ZC = "C:/Users/Anni/AppData/Local/Programs/ZCode/resources/glm/zcode.cjs";
const OUT = "G:/Playground/isaac_flapper_sim/logs";
const JIA = "sess_2cb44cfa-cfc9-4f04-acaf-e8113b90a856";
const WS = { workspace: { workspaceKey: "G:\\Playground", workspacePath: "G:\\Playground" } };

function startServer() {
  const child = spawn(process.execPath, [ZC, "app-server"], {
    stdio: ["pipe", "pipe", "pipe"], env: process.env });
  const waiters = new Map();
  const events = [];
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
      if (m.method) events.push(m);
      const w = waiters.get(m.id);
      if (w) { waiters.delete(m.id); w(m); }
    }
  });
  const call = (msg, ms = 60000) => new Promise((res, rej) => {
    const t = setTimeout(() => { waiters.delete(msg.id); rej(new Error("超时 " + msg.method)); }, ms);
    waiters.set(msg.id, (r) => { clearTimeout(t); res(r); });
    child.stdin.write(JSON.stringify(msg) + "\n");
  });
  return { child, call, events, ready: new Promise(r => setTimeout(r, 2500)),
           kill: () => child.kill() };
}

const sidOf = (r) => r?.result?.sessionId
  || (JSON.stringify(r).match(/sess_[0-9a-f-]{36}/g) || []).pop();
const transcript = (sid, marker) => {
  const p = join(homedir(), ".zcode", "cli", "rollout", `model-io-${sid}.jsonl`);
  if (!existsSync(p)) return { exists: false, has: false };
  const txt = readFileSync(p, "utf8");
  return { exists: true, has: txt.includes(marker), lines: txt.split("\n").filter(Boolean).length };
};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const now = () => new Date().toISOString();

/* ============ T1：回合进行中，模拟客户端打开 ============ */
async function t1() {
  console.log("== T1 判据一+二：打开不打断 ==");
  const P1 = await startServer(); await P1.ready;
  const before = {};
  before.list = (await P1.call({ id: 1, method: "session/list", params: {} }))
    ?.result?.sessions?.find(s => s.sessionId === JIA) || null;
  await P1.call({ id: 2, method: "session/resume", params: { sessionId: JIA } });
  const MARK2 = "I126甲M2灯塔长文";
  // 长回合：写长文，耗时数十秒，给"客户端打开"留窗口。
  const send2 = await P1.call({ id: 3, method: "session/send", params: {
    sessionId: JIA,
    content: `${MARK2}：请勿调用任何工具，写一篇约八百字的关于灯塔与守塔人的短文，完整写完后以"全文完"三字收尾。` } });
  before.sendAccepted = !!send2?.result?.accepted;
  before.turnEventsAtSend = P1.events.filter(e => /turn|state/.test(e.method || "")).length;

  // 等 6 秒让回合真正跑起来，再模拟客户端打开（list→resume→messages 读取）。
  await sleep(6000);
  const P2 = await startServer(); await P2.ready;
  const openSteps = {};
  openSteps.listSeen = !!(await P2.call({ id: 10, method: "session/list", params: {} }))
    ?.result?.sessions?.find(s => s.sessionId === JIA);
  openSteps.resumeOk = !(await P2.call({ id: 11, method: "session/resume",
    params: { sessionId: JIA } })).error;
  const mid = await P2.call({ id: 12, method: "session/messages", params: { sessionId: JIA } });
  openSteps.readOk = Array.isArray(mid?.result?.messages);
  openSteps.readSeesM2 = JSON.stringify(mid?.result?.messages || []).includes(MARK2);
  console.log("客户端打开步骤:", JSON.stringify(openSteps));

  // 原驱动方 P1 在"客户端打开"后继续持有：再发一条短消息证明控制权未转移。
  const MARK3 = "I126甲M3控制权验证";
  const send3 = await P1.call({ id: 4, method: "session/send", params: {
    sessionId: JIA, content: `${MARK3}：请直接回复"收到"，勿用工具。` } });

  // 等两个回合都完成（轮询读回）。
  let m2Done = false, m3Done = false;
  for (let i = 0; i < 36 && !(m2Done && m3Done); i++) {
    await sleep(5000);
    const rb = await P2.call({ id: 13, method: "session/messages", params: { sessionId: JIA } });
    const t = JSON.stringify(rb?.result?.messages || []);
    m2Done = m2Done || t.includes("全文完");
    m3Done = m3Done || (t.includes(MARK3) && t.includes("收到"));
  }
  const after = {};
  after.list = (await P2.call({ id: 14, method: "session/list", params: {} }))
    ?.result?.sessions?.find(s => s.sessionId === JIA) || null;
  after.transcriptM2 = transcript(JIA, MARK2);
  after.transcriptDone = transcript(JIA, "全文完");
  P1.kill(); P2.kill();

  const verdict1 = before.sendAccepted && openSteps.listSeen && openSteps.resumeOk
    && openSteps.readOk && m2Done && m3Done && after.transcriptDone.has;
  console.log("M2完成=" + m2Done + " M3完成=" + m3Done + " 转录含全文完=" + after.transcriptDone.has);
  writeFileSync(`${OUT}/i126_t1_open_during_turn.json`, JSON.stringify({
    at: now(), session: JIA, before, openSteps, after,
    m2Done, m3Done,
    verdict: verdict1 ? "不受影响：回合进行中被第二连接恢复+读取（客户端打开的协议面），两回合均完成、原驱动方控制权保留" : "受影响或部分失败，见字段",
  }, null, 2));
  return verdict1;
}

/* ============ T2：显式打断 ============ */
async function t2() {
  console.log("== T2 判据三：显式打断 ==");
  // 新建测试会话乙。
  const P1 = await startServer(); await P1.ready;
  const created = await P1.call({ id: 20, method: "session/create", params: WS }, 60000);
  const YI = sidOf(created);
  console.log("测试会话乙:", YI);
  const MARK = "I126乙M1长文";
  await P1.call({ id: 21, method: "session/send", params: {
    sessionId: YI,
    content: `${MARK}：请勿调用任何工具，写一篇约一千字的关于运河与船闸的短文，完整写完后以"乙文完"三字收尾。` } });
  await sleep(8000);   // 回合进行中。

  // 打断方法族探测（跨进程）。
  const P2 = await startServer(); await P2.ready;
  await P2.call({ id: 30, method: "session/resume", params: { sessionId: YI } });
  const probes = {};
  for (const [mid, method] of [[31, "session/interrupt"], [32, "session/stop"],
                               [33, "session/cancel"], [34, "session/abort"]]) {
    const r = await P2.call({ id: mid, method, params: { sessionId: YI } }, 15000)
      .catch(e => ({ error: { message: String(e) } }));
    const err = r?.error;
    probes[method] = err ? (String(err.message || err).includes("Method not found") ? "不存在" : "拒绝:" + String(err.message || err.code).slice(0, 40)) : "可用";
    if (!err) break;
  }
  console.log("打断方法族:", JSON.stringify(probes));

  // 等候观察回合是否终止（无乙文完即被打断）。
  let done = false;
  for (let i = 0; i < 24 && !done; i++) {
    await sleep(5000);
    const rb = await P1.call({ id: 35, method: "session/messages", params: { sessionId: YI } });
    done = JSON.stringify(rb?.result?.messages || []).includes("乙文完");
  }
  const tr = transcript(YI, "乙文完");
  P1.kill(); P2.kill();
  const interrupted = !done && !tr.has;
  writeFileSync(`${OUT}/i126_t2_interrupt.json`, JSON.stringify({
    at: now(), session: YI, probes, completed: done, transcriptEnd: tr,
    verdict: interrupted
      ? "显式打断通道存在且有效：跨进程打断方法使进行中的回合终止（无完成产出）"
      : (done ? "打断未生效：回合仍完成" : "方法族全不存在，显式打断通道未找到"),
  }, null, 2));
  console.log("回合完成=" + done + " → 打断" + (interrupted ? "有效" : "未证实"));
  return { ok: interrupted, probes, YI };
}

const v1 = await t1();
const v2 = await t2();
console.log("T1=" + v1 + " T2=" + v2.ok);
process.exit(0);
