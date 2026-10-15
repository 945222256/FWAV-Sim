/* probe_i126c.mjs — I-126 定向收口：C 停止通道（带回合号）+ 居住权转移语义。 */
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";

const ZC = "C:/Users/Anni/AppData/Local/Programs/ZCode/resources/glm/zcode.cjs";
const OUT = "G:/Playground/isaac_flapper_sim/logs";
const WS = { workspace: { workspaceKey: "G:\\Playground", workspacePath: "G:\\Playground" } };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const now = () => new Date().toISOString();

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
    const t = setTimeout(() => { waiters.delete(msg.id); rej(new Error("超时")); }, ms);
    waiters.set(msg.id, (r) => { clearTimeout(t); res(r); });
    child.stdin.write(JSON.stringify(msg) + "\n");
  });
  return { child, call, events, ready: new Promise(r => setTimeout(r, 2500)), kill: () => child.kill() };
}
const sidOf = (r) => r?.result?.sessionId
  || (JSON.stringify(r).match(/sess_[0-9a-f-]{36}/g) || []).pop();
async function readback(proc, sid, marker, rounds = 24) {
  for (let i = 0; i < rounds; i++) {
    await sleep(5000);
    const rb = await proc.call({ id: 900 + i, method: "session/messages", params: { sessionId: sid } });
    if (JSON.stringify(rb?.result?.messages || []).includes(marker)) return true;
  }
  return false;
}

/* C：带 turnId 的 session/stop。 */
async function stopWithTurnId() {
  const P1 = await startServer(); await P1.ready;
  const sid = sidOf(await P1.call({ id: 20, method: "session/create", params: WS }, 60000));
  console.log("测试会话庚:", sid);
  const MARK = "I126庚M1数数", TAIL = "庚数完";
  await P1.call({ id: 21, method: "session/send", params: {
    sessionId: sid,
    content: `${MARK}：请勿调用任何工具，从一数到八十，每行十个数，数完另起一行写"${TAIL}"。` } });
  // 从事件流抓 turnId（turn-started 类推送）。
  let turnId = null;
  for (let i = 0; i < 20 && !turnId; i++) {
    await sleep(1000);
    for (const e of P1.events) {
      const s = JSON.stringify(e);
      const m = s.match(/turn_[0-9a-f-]{36}/);
      if (m) { turnId = m[0]; break; }
    }
  }
  console.log("捕获回合号:", turnId);
  const stopR = await P1.call({ id: 22, method: "session/stop",
    params: { sessionId: sid, turnId } }, 20000).catch(e => ({ error: { message: String(e) } }));
  const stopped = !(await readback(P1, sid, TAIL, 20));
  P1.kill();
  writeFileSync(`${OUT}/i126_c_stop_with_turnid.json`, JSON.stringify({
    at: now(), session: sid, turnId,
    stopReply: stopR?.error ? String(stopR.error.message).slice(0, 120) : "可用",
    completedAfterStop: !stopped, interrupted: stopped }, null, 2));
  console.log("stop(带turnId) 回合被停=" + stopped);
  return { sid, stopped, turnId, stopReply: stopR };
}

/* 居住权转移语义（空闲态小试）。 */
async function residencySemantics() {
  const P1 = await startServer(); await P1.ready;
  const sid = sidOf(await P1.call({ id: 30, method: "session/create", params: WS }, 60000));
  console.log("测试会话辛:", sid);
  const A1 = "I126辛A1";
  await P1.call({ id: 31, method: "session/send", params: {
    sessionId: sid, content: `${A1}：请直接回复收到，勿用工具。` } });
  const a1 = await readback(P1, sid, "收到", 24);
  // P2 打开（list+resume+read，与 T1 的客户端打开面一致）。
  const P2 = await startServer(); await P2.ready;
  await P2.call({ id: 40, method: "session/list", params: {} });
  await P2.call({ id: 41, method: "session/resume", params: { sessionId: sid } });
  await P2.call({ id: 42, method: "session/messages", params: { sessionId: sid } });
  // P1 不再 resume 直接发：执行还是搁浅？
  const A2 = "I126辛A2不resume直发";
  const s2 = await P1.call({ id: 32, method: "session/send", params: {
    sessionId: sid, content: `${A2}：请直接回复收到，勿用工具。` } });
  const a2 = await readback(P2, sid, A2, 18);
  // P1 先 resume 再发：恢复发言权？
  const r1 = await P1.call({ id: 33, method: "session/resume", params: { sessionId: sid } });
  const A3 = "I126辛A3恢复后发";
  const s3 = await P1.call({ id: 34, method: "session/send", params: {
    sessionId: sid, content: `${A3}：请直接回复收到，勿用工具。` } });
  const a3 = await readback(P2, sid, A3, 18);
  P1.kill(); P2.kill();
  writeFileSync(`${OUT}/i126_c_residency.json`, JSON.stringify({
    at: now(), session: sid,
    a1Baseline: a1,
    foreignOpenThenSendAccepted: !s2?.error && !!s2?.result?.accepted,
    a2Executed: a2,
    resumeAgainOk: !r1?.error,
    a3SendAccepted: !s3?.error && !!s3?.result?.accepted,
    a3Executed: a3,
    semantics: a1 && !a2 && a3
      ? "居住权转移成立：他人打开(resume)后，原驱动方不恢复直发则消息搁浅；先恢复再发即夺回"
      : (a2 ? "未转移：直发仍执行" : "其他，见字段") }, null, 2));
  console.log(`居住权小试: 基线=${a1} 开后直发=${a2} 恢复后再发=${a3}`);
  return { a1, a2, a3, sid };
}

const c1 = await stopWithTurnId();
const c2 = await residencySemantics();
writeFileSync(`${OUT}/i126_test_sessions_registry.json`, JSON.stringify({
  at: now(), item: "I-126",
  sessions: {
    甲: "sess_2cb44cfa-cfc9-4f04-acaf-e8113b90a856",
    乙: "sess_f086b204-b3aa-47e5-9ce8-80478dd86f70",
    戊: "sess_be05c1a6-e2b5-4448-ba1f-d036e17f0859",
    丙: "sess_9e6d2e66-e8ad-4add-be4d-029cb3473f0b",
    丁: "sess_5458ec1d-1507-4e94-9c86-7ff3fef2b680",
    庚: c1.sid, 辛: c2.sid },
  note: "全部为本事项新建测试会话，未触碰任何在办会话" }, null, 2));
console.log("收口完成");
process.exit(0);
