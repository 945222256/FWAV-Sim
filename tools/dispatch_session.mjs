/* dispatch_session.mjs — I-106 会话直送一键派活工具。
 *
 * 按 I-098 定论通道 A 实现：按需拉起协议进程。
 * 【顺序写死】对既有会话：先 session/resume 再 session/send，
 * 该顺序是工具内置行为，调用方不可绕过；对新会话：session/create
 * + session/send（简报作首条消息，会话提升落库）。
 *
 * 用法：
 *   node tools/dispatch_session.mjs --new --title "标题" --message "简报正文"
 *   node tools/dispatch_session.mjs --resume sess_xxx --message "开工口令"
 *   node tools/dispatch_session.mjs --selftest        （I-106 判据实测，只动新建测试会话）
 *
 * 钉模型方法（I-160 定论，实测依据 logs/zcode_shadow/setModel路线_*）：
 *   建会话后发消息前调 session/setModel，模型对象三键齐带：
 *   {providerId, modelId, options:{reasoningLevel}}。
 *   注意：session/create 顶层的 model 参数会丢 options，回合以
 *   invalid_model_request 失败，不许用 create 钉模型；必须走 setModel。
 *   本账户现役可用模型唯一：zai-api/GLM-5.3，推理档 low/high/max。
 *
 * 纪律（I-098 事实清单落地）：
 *   单写者：同一时刻一个会话只允许一个驱动进程；
 *   每次派发证据落 logs/dispatch_session/；
 *   --selftest 只动本工具新建的测试会话，禁止对在办会话发任何内容。
 */
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync, existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const ZC = "C:/Users/Anni/AppData/Local/Programs/ZCode/resources/glm/zcode.cjs";
const OUT = "G:/Playground/isaac_flapper_sim/logs/dispatch_session";
const WS = { workspace: { workspaceKey: "G:\\Playground", workspacePath: "G:\\Playground" } };

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
    const t = setTimeout(() => { waiters.delete(msg.id); rej(new Error("超时 " + msg.method)); }, ms);
    waiters.set(msg.id, (r) => { clearTimeout(t); res(r); });
    child.stdin.write(JSON.stringify(msg) + "\n");
  });
  return { child, call, ready: new Promise(r => setTimeout(r, 2500)),
           kill: () => child.kill() };
}

const sidOf = (r) => r?.result?.sessionId
  || (JSON.stringify(r).match(/sess_[0-9a-f-]{36}/g) || []).pop();

async function readbackHit(proc, sid, marker, rounds = 24, gap = 5000) {
  for (let i = 0; i < rounds; i++) {
    await new Promise(r => setTimeout(r, gap));
    const rb = await proc.call({ id: 900 + i, method: "session/messages",
      params: { sessionId: sid } }, 40000);
    const text = JSON.stringify(rb?.result?.messages || []);
    if (text.includes(marker)) return { hit: true, round: i + 1, bytes: text.length };
  }
  return { hit: false, round: rounds, bytes: 0 };
}

function saveEvidence(name, payload) {
  mkdirSync(OUT, { recursive: true });
  const p = join(OUT, name);
  writeFileSync(p, JSON.stringify(payload, ensureAsciiAware(payload) ? { ...payload } : payload, 2));
  return p;
}
function ensureAsciiAware() { return false; }

/* 发送并回读：先恢复后发送的顺序在这里写死（resume 通道）。 */
async function sendToExisting(server, sid, content, tag) {
  // 【写死顺序·第一步】恢复：无论目标会话当前状态如何，一律先恢复。
  const resumed = await server.call({ id: 100, method: "session/resume",
    params: { sessionId: sid } }, 60000);
  if (resumed?.error) throw new Error("恢复被拒：" + JSON.stringify(resumed.error).slice(0, 200));
  // 【写死顺序·第二步】发送：恢复成功后才允许发送。
  const sent = await server.call({ id: 101, method: "session/send",
    params: { sessionId: sid, content } }, 60000);
  if (!sent?.result?.accepted) throw new Error("发送未接受：" + JSON.stringify(sent).slice(0, 200));
  const rb = await readbackHit(server, sid, markerOf(content));
  const ev = saveEvidence(`dispatch_${tag}.json`, {
    at: new Date().toISOString(), mode: "resume", sessionId: sid,
    resumeOk: true, sendAccepted: true, readback: rb,
    contentHead: content.slice(0, 120) });
  console.log(`[${tag}] 恢复→发送→回读命中=${rb.hit}（证据 ${ev}）`);
  return { ok: rb.hit, ev };
}

/* 新会话直送：create + send（首条消息即提升落库）。 */
async function sendToNew(server, title, content, tag) {
  const created = await server.call({ id: 110, method: "session/create", params: WS }, 60000);
  const sid = sidOf(created);
  if (!sid) throw new Error("建会话失败：" + JSON.stringify(created).slice(0, 200));
  const sent = await server.call({ id: 111, method: "session/send",
    params: { sessionId: sid, content: title + "\n\n" + content } }, 60000);
  if (!sent?.result?.accepted) throw new Error("发送未接受");
  const rb = await readbackHit(server, sid, markerOf(content));
  const ev = saveEvidence(`dispatch_${tag}.json`, {
    at: new Date().toISOString(), mode: "create", sessionId: sid,
    createOk: true, sendAccepted: true, readback: rb,
    contentHead: (title + "\n\n" + content).slice(0, 120) });
  console.log(`[${tag}] 新建→直送→回读命中=${rb.hit}（会话 ${sid}，证据 ${ev}）`);
  return { ok: rb.hit, sid, ev };
}

function markerOf(text) {
  const m = text.match(/I-\d{3}|I098|I106|派活|[A-Za-z]{3,}/);
  return m ? m[0] : text.slice(0, 12);
}

/* I-126：优先走常驻宿主服务（127.0.0.1:58771），服务不在时回退按需拉起。 */
import { createConnection } from "node:net";

async function tryService(payload, timeoutMs = 240000) {
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

function parseArgs(argv) {
  const a = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--new") a.new = true;
    else if (argv[i] === "--resume") a.resume = argv[++i];
    else if (argv[i] === "--title") a.title = argv[++i];
    else if (argv[i] === "--message") a.message = argv[++i];
    else if (argv[i] === "--selftest") a.selftest = true;
    // I-157 派发纪律：队名（一队一驻）、派发类别（task|test）、事项号
    else if (argv[i] === "--lane") a.lane = argv[++i];
    else if (argv[i] === "--kind") a.kind = argv[++i];
    else if (argv[i] === "--item") a.item = argv[++i];
    // I-174 会话归属：自报会话号（认领记录权威来源）
    else if (argv[i] === "--session") a.session = argv[++i];
    else a._.push(argv[i]);
  }
  return a;
}

/* 判据实测：只动本工具新建的测试会话。两步验证通道 A 全链。 */
async function selftest() {
  const results = { started_at: new Date().toISOString(), steps: [] };
  const step = (name, ok, ev) => { results.steps.push({ name, ok, ev }); console.log(`${ok ? "过" : "不过"} ${name} | ${ev}`); };
  const server = await startServer(); await server.ready;

  // 步骤一：新建测试会话直送。
  const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
  const title = `I-106 派活工具自检会话 ${stamp}`;
  const msg1 = `I106 自检第一步：一键直送通道测试。请直接回复"收到"两字，不要调用任何工具。`;
  const r1 = await sendToNew(server, title, msg1, `selftest_new_${stamp}`);
  step("新建测试会话直送", r1.ok, r1.ev);

  // 步骤二：对同一会话走【写死的恢复→发送】顺序再发一条。
  const msg2 = `I106 自检第二步：先恢复后发送顺序实测。请直接回复"收到"两字。`;
  const r2 = await sendToExisting(server, r1.sid, msg2, `selftest_resume_${stamp}`);
  step("既有会话恢复后发送", r2.ok, r2.ev);

  server.kill();
  const allOk = r1.ok && r2.ok;
  // 登记测试会话编号（判据：不碰在办会话的纪律留痕）。
  const reg = join(OUT, "selftest_sessions_registry.json");
  let rego = { sessions: [] };
  if (existsSync(reg)) { try { rego = JSON.parse(readFileSync(reg, "utf8")); } catch { } }
  rego.item = "I-106"; rego.updated_at = new Date().toISOString();
  rego.sessions = [...new Set([...(rego.sessions || []), r1.sid])];
  writeFileSync(reg, JSON.stringify(rego, null, 2));
  results.all_ok = allOk;
  results.registry = reg;
  writeFileSync(join(OUT, `selftest_report_${stamp}.json`), JSON.stringify(results, null, 2));
  console.log("自检总判定：%s", allOk ? "全部通过" : "存在不过项");
  process.exit(allOk ? 0 : 1);
}

const args = parseArgs(process.argv.slice(2));
if (args.selftest) {
  await selftest();
} else if (args.new && args.message) {
  // I-126：常驻服务优先；I-157：透传 lane/kind/item 走三通道判定。
  const viaSvc = await tryService({
    op: "dispatch_new", title: args.title || "指挥部派活", message: args.message,
    lane: args.lane, kind: args.kind || "task", item_id: args.item });
  if (viaSvc && viaSvc.ok) {
    console.log("经常驻宿主服务派发：", JSON.stringify(viaSvc));
    process.exit(0);
  }
  // 服务不在时的回退路径同样遵守 I-157：测试派发直接走服务化路径拒绝回退
  if ((args.kind || "task") === "test") {
    console.error("I-157：测试派发必须经常驻服务（回退路径无测试不登记保证），先启动 host_service。");
    process.exit(3);
  }
  const server = await startServer(); await server.ready;
  const r = await sendToNew(server, args.title || "指挥部派活", args.message,
    "new_" + new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14));
  server.kill(); process.exit(r.ok ? 0 : 1);
} else if (args.resume && args.message) {
  const viaSvc = await tryService({ op: "dispatch_resume", sid: args.resume, message: args.message });
  if (viaSvc && viaSvc.ok) {
    console.log("经常驻宿主服务派发：", JSON.stringify(viaSvc));
    process.exit(0);
  }
  const server = await startServer(); await server.ready;
  const r = await sendToExisting(server, args.resume, args.message,
    "resume_" + new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14));
  server.kill(); process.exit(r.ok ? 0 : 1);
} else {
  console.log("用法：\n" +
    "  node tools/dispatch_session.mjs --new --title 标题 --message 简报 --lane L3 [--kind task|test] [--item I-xxx]\n" +
    "  node tools/dispatch_session.mjs --resume sess_xxx --message 开工口令\n" +
    "  node tools/dispatch_session.mjs --selftest");
  process.exit(2);
}
