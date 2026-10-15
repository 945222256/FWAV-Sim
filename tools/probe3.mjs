/* probe3.mjs — Q-040 跨进程激活探测。
 * 靶子：sess_e3b4742e（先前 app-server 进程创建、进程已死、库中仍在）。
 * 步骤：list 确认在库 → 试激活方法族 → 激活成功则 send+回读。 */
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";

const ZC = "C:/Users/Anni/AppData/Local/Programs/ZCode/resources/glm/zcode.cjs";
const TARGET = "sess_e3b4742e-d386-4c7d-b24b-0c64ff43150a";
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
      child.stdin.write(JSON.stringify({ id: msg.id,
        result: { nativeSearchEnhancementsEnabled: false } }) + "\n");
      console.log("[SRV-REQ] " + msg.method + " -> 已应答");
      continue;
    }
    if (!String(msg.id || "").startsWith("server-") && (msg.result !== undefined || msg.error)) {
      console.log("[IN] " + JSON.stringify(msg).slice(0, 300));
    }
    const w = waiters.get(msg.id);
    if (w) { waiters.delete(msg.id); w(msg); }
  }
});
const waiters = new Map();
function call(msg, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => { waiters.delete(msg.id); reject(new Error("超时 " + msg.method)); }, timeoutMs);
    waiters.set(msg.id, (r) => { clearTimeout(t); resolve(r); });
    child.stdin.write(JSON.stringify(msg) + "\n");
    transcript.push({ _sent: msg });
  });
}

const TEST_TEXT = "Q-040 激活验证：本条经 session/resume 激活既有会话后由 session/send 定向发送，用于跨进程激活路径定论。";

async function main() {
  await new Promise(r => setTimeout(r, 2000));
  console.log("== 1 在库确认 ==");
  const listed = await call({ id: 60, method: "session/list", params: {} });
  const hit = (listed?.result?.sessions || []).find(s => s.sessionId === TARGET);
  console.log("靶会话在库: " + !!hit + (hit ? "（status=" + hit.status + "）" : ""));

  console.log("== 2 激活方法族探测 ==");
  const methods = ["session/resume", "session/activate", "session/attach"];
  let activated = null;
  for (let k = 0; k < methods.length; k++) {
    const r = await call({ id: 61 + k, method: methods[k],
      params: { sessionId: TARGET } });
    const err = r?.error?.code;
    console.log(methods[k] + " -> " + (err ? "拒绝 " + err + " " + (r.error.message || "").slice(0, 60)
                                          : "可用"));
    if (!err) { activated = methods[k]; break; }
  }
  if (!activated) {
    console.log("全部激活方法不存在或拒绝。定论方向：无常驻宿主则无协议级激活。");
    writeFileSync("logs/q040_probe_evidence.json", JSON.stringify({ at: new Date().toISOString(), target: TARGET, verdict: "no-activation-method", transcript }, null, 2));
    child.kill(); process.exit(0);
  }

  console.log("== 3 激活后定向发送 ==");
  const sent = await call({ id: 70, method: "session/send",
    params: { sessionId: TARGET, content: TEST_TEXT } }, 60000);
  console.log("发送: " + JSON.stringify(sent).slice(0, 200));

  console.log("== 4 回读 ==");
  let hitText = false, sample = "";
  for (let round = 0; round < 12; round++) {
    await new Promise(r => setTimeout(r, 5000));
    const rb = await call({ id: 71, method: "session/messages", params: { sessionId: TARGET } });
    sample = JSON.stringify(rb?.result?.messages || []);
    hitText = sample.includes("Q-040 激活验证");
    console.log("  轮次 " + (round + 1) + " 命中=" + hitText + " 字节=" + sample.length);
    if (hitText) break;
  }
  writeFileSync("logs/q040_probe_evidence.json", JSON.stringify({
    at: new Date().toISOString(), target: TARGET, activatedBy: activated,
    sentAccepted: !!sent, readbackHit: hitText, sample: sample.slice(0, 1500),
  }, null, 2));
  console.log("Q-040 定论材料已写 logs/q040_probe_evidence.json");
  child.kill(); process.exit(0);
}

main().catch((e) => { console.error("失败: " + e.message); child.kill(); process.exit(1); });
