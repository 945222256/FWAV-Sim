/* send_dispatch_once.mjs — 向指定既有会话直送一条消息（一次性派发通道）。
 * 用法：node tools/send_dispatch_once.mjs <sessionId> <消息文本文件路径> <证据输出路径>
 * 协议：app-server NDJSON；session/send 注入后 session/messages 回读验证。
 * 纪律：发送只调用一次；失败即退出并留证据，不做任何重试。 */
import { spawn } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const [sessionId, contentPath, evidencePath] = process.argv.slice(2);
if (!sessionId || !contentPath || !evidencePath) {
  console.error("用法：node tools/send_dispatch_once.mjs <sessionId> <文本文件> <证据输出>");
  process.exit(2);
}
const content = readFileSync(contentPath, "utf8");
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
    if (msg.id && msg.method && String(msg.id).startsWith("server-")) {
      child.stdin.write(JSON.stringify({ id: msg.id, result: { nativeSearchEnhancementsEnabled: false } }) + "\n");
      transcript.push({ _sent: { id: msg.id, answered: msg.method } });
      continue;
    }
    console.log("[IN] " + JSON.stringify(msg).slice(0, 300));
    const pending = waiters.get(msg.id);
    if (pending) { waiters.delete(msg.id); pending(msg); }
  }
});

const waiters = new Map();
function call(msg, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => { waiters.delete(msg.id); reject(new Error("超时: " + msg.method)); }, timeoutMs);
    waiters.set(msg.id, (resp) => { clearTimeout(t); resolve(resp); });
    child.stdin.write(JSON.stringify(msg) + "\n");
    transcript.push({ _sent: { id: msg.id, method: msg.method } });
  });
}

function finish(code, evidence) {
  writeFileSync(evidencePath, JSON.stringify({
    at: new Date().toISOString(), sessionId, ...evidence,
    transcriptCount: transcript.length,
  }, null, 2));
  console.log("证据已写 " + evidencePath);
  child.kill();
  process.exit(code);
}

async function main() {
  await new Promise((r) => setTimeout(r, 2000));
  console.log("== 唯一一次 session/send ==");
  let sent;
  try {
    sent = await call({ id: 1, method: "session/send", params: { sessionId, content } }, 60000);
  } catch (e) {
    finish(1, { stage: "send", ok: false, error: e.message });
    return;
  }
  const sentText = JSON.stringify(sent);
  console.log("发送返回：" + sentText.slice(0, 400));
  if (sent?.error || sent?.result?.accepted !== true) {
    finish(1, { stage: "send", ok: false, response: sentText.slice(0, 1500) });
    return;
  }
  console.log("== 回读验证 ==");
  let hit = false, framesText = "";
  for (let round = 0; round < 6; round++) {
    await new Promise((r) => setTimeout(r, 5000));
    const rb = await call({ id: 100 + round, method: "session/messages", params: { sessionId } }, 40000);
    framesText = JSON.stringify(rb?.result?.messages || rb?.result || []);
    hit = framesText.includes("L5 施工便签");
    console.log("  第 %d 轮回读 %d 字节 命中=%s", round + 1, framesText.length, hit);
    if (hit) break;
  }
  finish(hit ? 0 : 1, { stage: "readback", ok: hit, readbackSample: framesText.slice(0, 2000) });
}

main().catch((e) => finish(1, { stage: "exception", ok: false, error: e.message }));
