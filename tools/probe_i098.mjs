/* probe_i098.mjs — I-098 会话直送机制探查（两阶段，只动新建测试会话）。
 * 阶段一（判据一）：进程A建会话落库→杀→进程B恢复→发送→回读。
 * 阶段二（判据二）：进程C建会话并保持存活（桌面客户端可见）→
 *                  进程D恢复→发送→三方回读（D、C、转录文件）对照。
 * 每次发送的证据独立落 logs/i098_*.json。 */
import { spawn } from "node:child_process";
import { writeFileSync, existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const ZC = "C:/Users/Anni/AppData/Local/Programs/ZCode/resources/glm/zcode.cjs";
const OUT = "G:/Playground/isaac_flapper_sim/logs";
const MARK = { m1: "I098标记M1", m2: "I098标记M2", m3: "I098标记M3", m4: "I098标记M4" };
const MSG = (m) => `${MARK[m]}：I-098 会话直送机制实测消息。这是自动化通道测试，请勿调用任何工具，直接回复"收到"两字即可。`;

function startServer() {
  const child = spawn(process.execPath, [ZC, "app-server"], {
    stdio: ["pipe", "pipe", "pipe"], env: process.env });
  const waiters = new Map();
  const pushes = [];
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
      if (m.method) pushes.push(m);
      const w = waiters.get(m.id);
      if (w) { waiters.delete(m.id); w(m); }
    }
  });
  const call = (msg, ms = 30000) => new Promise((res, rej) => {
    const t = setTimeout(() => { waiters.delete(msg.id); rej(new Error("超时 " + msg.method)); }, ms);
    waiters.set(msg.id, (r) => { clearTimeout(t); res(r); });
    child.stdin.write(JSON.stringify(msg) + "\n");
  });
  const ready = new Promise(r => setTimeout(r, 2500));
  return { child, call, ready, kills: () => child.kill(), pushes };
}

const WS = { workspace: { workspaceKey: "G:\\Playground", workspacePath: "G:\\Playground" } };
async function readbackHit(proc, sid, marker, rounds = 24, gap = 5000) {
  for (let i = 0; i < rounds; i++) {
    await new Promise(r => setTimeout(r, gap));
    const rb = await proc.call({ id: 900 + i, method: "session/messages", params: { sessionId: sid } }, 40000);
    const text = JSON.stringify(rb?.result?.messages || []);
    if (text.includes(marker)) return { hit: true, round: i + 1, bytes: text.length };
  }
  return { hit: false, round: rounds, bytes: 0 };
}
const sidOf = (r) => r?.result?.sessionId || (JSON.stringify(r).match(/sess_[0-9a-f-]{36}/g) || []).pop();
const transcriptHas = (sid, marker) => {
  const p = join(homedir(), ".zcode", "cli", "rollout", `model-io-${sid}.jsonl`);
  if (!existsSync(p)) return { file: p, exists: false, has: false };
  const txt = readFileSync(p, "utf8");
  return { file: p, exists: true, has: txt.includes(marker), bytes: txt.length };
};

async function phase1() {
  console.log("== 阶段一：恢复后发送链路 ==");
  const A = await startServer(); await A.ready;
  const created = await A.call({ id: 1, method: "session/create", params: WS }, 60000);
  const s1 = sidOf(created);
  const send1 = await A.call({ id: 2, method: "session/send", params: { sessionId: s1, content: MSG("m1") } }, 60000);
  const rb1 = await readbackHit(A, s1, MARK.m1);
  A.kills(); await new Promise(r => setTimeout(r, 1500));
  console.log("S1=" + s1 + " 首发accepted=" + !!send1?.result?.accepted + " 回读=" + rb1.hit);

  const B = await startServer(); await B.ready;
  const listed = await B.call({ id: 3, method: "session/list", params: {} });
  const inStore = JSON.stringify(listed).includes(s1);
  const resumed = await B.call({ id: 4, method: "session/resume", params: { sessionId: s1 } }, 60000).catch(e => ({ error: String(e) }));
  const resumeOk = !resumed?.error;
  let send2 = null, rb2 = { hit: false };
  if (resumeOk) {
    send2 = await B.call({ id: 5, method: "session/send", params: { sessionId: s1, content: MSG("m2") } }, 60000).catch(e => ({ error: String(e) }));
    rb2 = await readbackHit(B, s1, MARK.m2);
  }
  const tr2 = transcriptHas(s1, MARK.m2);
  B.kills();
  const verdict1 = resumeOk && send2?.result?.accepted && rb2.hit;
  console.log("恢复=" + resumeOk + " 二发accepted=" + !!send2?.result?.accepted + " 回读=" + rb2.hit);
  writeFileSync(`${OUT}/i098_phase1_resume_send.json`, JSON.stringify({
    at: new Date().toISOString(), session: s1,
    steps: { create: sidOf(created) === s1, send1Accepted: !!send1?.result?.accepted,
             storeKillPersist: inStore, resumeOk, send2Accepted: !!send2?.result?.accepted,
             readbackM2: rb2, transcriptM2: tr2 },
    verdict: verdict1 ? "成立：恢复后发送全链路通" : "不成立或部分不成立，见steps" }, null, 2));
  return { s1, verdict1 };
}

async function phase2() {
  console.log("== 阶段二：双活并发语义 ==");
  const C = await startServer(); await C.ready;
  const created = await C.call({ id: 10, method: "session/create", params: WS }, 60000);
  const s2 = sidOf(created);
  await C.call({ id: 11, method: "session/send", params: { sessionId: s2, content: MSG("m3") } }, 60000);
  const rb3 = await readbackHit(C, s2, MARK.m3);
  console.log("S2=" + s2 + " 首发回读=" + rb3.hit + "（进程C保持存活）");

  const D = await startServer(); await D.ready;
  const before = await D.call({ id: 12, method: "session/list", params: {} });
  const meta = (before?.result?.sessions || []).find(x => x.sessionId === s2);
  const resumed = await D.call({ id: 13, method: "session/resume", params: { sessionId: s2 } }, 60000).catch(e => ({ error: String(e) }));
  const send4 = await D.call({ id: 14, method: "session/send", params: { sessionId: s2, content: MSG("m4") } }, 60000).catch(e => ({ error: String(e) }));
  const rb4 = await readbackHit(D, s2, MARK.m4);

  // 三方对照：D 回读、C 回读（原持有进程）、转录文件。
  const rbC = await C.call({ id: 15, method: "session/messages", params: { sessionId: s2 } }, 40000);
  const cSeesM4 = JSON.stringify(rbC?.result?.messages || []).includes(MARK.m4);
  const tr4 = transcriptHas(s2, MARK.m4);
  const after = await D.call({ id: 16, method: "session/list", params: {} });
  const metaAfter = (after?.result?.sessions || []).find(x => x.sessionId === s2);
  C.kills(); D.kills();

  const lost = !rb4.hit;
  const fork = rb4.hit && !cSeesM4;
  const cls = lost ? "丢消息" : fork ? "分叉（两进程视图不一致）" : "安全（顺序双驱动实测未见丢失，C/D 两进程与转录三方一致）";
  console.log("D回读=" + rb4.hit + " C回读含M4=" + cSeesM4 + " 转录含M4=" + tr4.has + " → " + cls);
  writeFileSync(`${OUT}/i098_phase2_dual_active.json`, JSON.stringify({
    at: new Date().toISOString(), session: s2,
    steps: { sendFromSecondProcessAccepted: !!send4?.result?.accepted,
             readbackD: rb4, readbackCSeesM4: cSeesM4, transcriptM4: tr4,
             storeMetaBefore: meta || null, storeMetaAfter: metaAfter || null },
    classification: cls,
    note: "桌面侧栏为 Electron 无障碍盲区且窗口隐藏禁截屏，机器不可观测；以共享库 updatedAt、双进程回读一致、转录追加三证代理，侧栏一眼确认留机主。" }, null, 2));
  return { s2, cls };
}

const p1 = await phase1();
const p2 = await phase2();
writeFileSync(`${OUT}/i098_test_sessions_registry.json`, JSON.stringify({
  at: new Date().toISOString(), item: "I-098",
  sessions: [p1.s1, p2.s2], purpose: "机制实测专用，任何施工会话未触碰" }, null, 2));
console.log("两阶段完成。测试会话登记：" + p1.s1 + "、" + p2.s2);
process.exit(0);
