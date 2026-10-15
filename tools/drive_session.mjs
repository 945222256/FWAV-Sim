/* drive_session.mjs — 会话直送通道完整驱动（任务二）。
 * 流程：session/create → 应答服务端请求 → session/send → session/messages 回读。
 * 全部收发留痕到 stdout 与 logs/session_direct_send_evidence.json。 */
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";

const ZC = "C:/Users/Anni/AppData/Local/Programs/ZCode/resources/glm/zcode.cjs";
const child = spawn(process.execPath, [ZC, "app-server"], {
  stdio: ["pipe", "pipe", "pipe"], env: process.env,
});
child.stderr.setEncoding("utf8");
child.stderr.on("data", (d) => process.stdout.write("[SRV-ERR] " + d.slice(0, 200)));

const transcript = [];
let buf = "";
child.stdout.setEncoding("utf8");
child.stdout.on("data", (d) => {
  buf += d;
  let idx;
  while ((idx = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    if (!line) continue;
    let msg;
    try { msg = JSON.parse(line); } catch { continue; }
    transcript.push(msg);
    // 服务端到客户端的请求：按需应答运行时偏好，避免流程卡死。
    if (msg.id && msg.method && String(msg.id).startsWith("server-")) {
      const reply = { id: msg.id, result: { nativeSearchEnhancementsEnabled: false } };
      if (msg.method === "session/requestRuntimePreferences") {
        reply.result = { nativeSearchEnhancementsEnabled: false };
      }
      child.stdin.write(JSON.stringify(reply) + "\n");
      transcript.push({ _sent: reply });
      console.log("[SRV-REQ] " + msg.method + " -> 已应答");
      continue;
    }
    console.log("[IN] " + JSON.stringify(msg).slice(0, 400));
    const pending = waiters.get(msg.id);
    if (pending) { waiters.delete(msg.id); pending(msg); }
    handlePush(msg);
  }
});

const waiters = new Map();
function call(msg, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => { waiters.delete(msg.id); reject(new Error("超时: " + msg.method)); }, timeoutMs);
    waiters.set(msg.id, (resp) => { clearTimeout(t); resolve(resp); });
    child.stdin.write(JSON.stringify(msg) + "\n");
    transcript.push({ _sent: msg });
  });
}

let pushFrame = null;
function handlePush(msg) {
  if (msg.method && String(msg.method).startsWith("session/")) {
    pushFrame = msg;                       // 记录最近一条会话推送（如流式更新）。
  }
}

const TEST_TEXT = "L5 会话直送通道自检：本条由 app-server 协议 session/send 注入，"
  + "用于验证桌面界面可见性。见事项 I-064 配套验证，无需处理。";

async function main() {
  await new Promise(r => setTimeout(r, 2000));      // 等启动推送结束。

  console.log("== 步骤1 建测试会话 ==");
  const created = await call({
    id: 100, method: "session/create",
    params: { workspace: { workspaceKey: "G:\\Playground", workspacePath: "G:\\Playground" } },
  }, 40000);
  const fullJson = JSON.stringify(created);
  const idMatches = fullJson.match(/sess_[0-9a-f-]{36}/g) || [];
  const sessionId = created?.result?.sessionId || [...new Set(idMatches)].pop();
  console.log("新会话编号：" + sessionId);
  if (!sessionId) {
    writeFileSync("logs/_create_result_dump.json", fullJson);
    throw new Error("建会话失败，返回体已存 logs/_create_result_dump.json");
  }

  console.log("== 步骤2 直送消息 ==");
  const sent = await call({ id: 101, method: "session/send",
    params: { sessionId, content: TEST_TEXT } }, 60000);
  console.log("发送结果：" + JSON.stringify(sent).slice(0, 400));

  console.log("== 步骤3 轮询回读验证（等回合落库） ==");
  let readback = null, hit = false, framesText = "";
  for (let round = 0; round < 18; round++) {
    await new Promise(r => setTimeout(r, 5000));
    readback = await call({ id: 102 + round, method: "session/messages",
      params: { sessionId } }, 40000);
    framesText = JSON.stringify(readback?.result?.messages || readback?.result || []);
    hit = framesText.includes("L5 会话直送通道自检");
    console.log("  第 %d 轮：回读 %d 字节，命中=%s", round + 1, framesText.length, hit);
    if (hit) break;
  }
  console.log("回读含测试消息: " + hit);
  console.log("回读片段: " + framesText.slice(0, 600));

  writeFileSync("logs/session_direct_send_evidence.json", JSON.stringify({
    at: new Date().toISOString(), sessionId, sentOk: !!sent,
    contentInjected: TEST_TEXT, readbackHit: hit,
    readbackSample: framesText.slice(0, 2500), transcriptCount: transcript.length,
  }, null, 2));
  console.log("证据已写 logs/session_direct_send_evidence.json");

  child.kill();
  process.exit(0);
}

main().catch((e) => { console.error("失败: " + e.message); child.kill(); process.exit(1); });
