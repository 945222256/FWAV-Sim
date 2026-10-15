/* t5b.mjs — 定音实验（绕开服务）：直连进程发长文，5 秒杀驱动进程，旁观者核完成。 */
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";

const ZC = "C:/Users/Anni/AppData/Local/Programs/ZCode/resources/glm/zcode.cjs";
const OUT = "G:/Playground/isaac_flapper_sim/logs";
const WS = { workspace: { workspaceKey: "G:\\Playground", workspacePath: "G:\\Playground" } };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

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

const P1 = await startServer(); await P1.ready;
const sid = sidOf(await P1.call({ id: 20, method: "session/create", params: WS }, 60000));
console.log("测试会话子:", sid);
const MARK = "I126子M1", TAIL = "子文完";
const sent = await P1.call({ id: 21, method: "session/send", params: {
  sessionId: sid,
  content: `${MARK}：请勿调用任何工具，写一篇约三千字的关于航海天文钟历史的文章，完整写完，最后以"${TAIL}"三字收尾。` } });
console.log("长文已发:", !!sent?.result?.accepted, "T0");

// T0+5s：杀驱动进程 P1。
await sleep(5000);
P1.kill();
console.log("驱动进程已杀 T+5s");

// 旁观者 P2 轮询 180 秒。
const P2 = await startServer(); await P2.ready;
let done = false, at = null;
for (let i = 0; i < 36 && !done; i++) {
  await sleep(5000);
  const rb = await P2.call({ id: 900 + i, method: "session/messages", params: { sessionId: sid } });
  if (JSON.stringify(rb?.result?.messages || []).includes(TAIL)) { done = true; at = (i + 1) * 5; }
}
P2.kill();
writeFileSync(`${OUT}/i126_t5b_execution_host.json`, JSON.stringify({
  at: new Date().toISOString(), session: sid, killAtS: 5,
  completed: done, observedAtS: at,
  reading: done
    ? `长回合在驱动进程被杀后 ${at}s 处完成：回合执行不在驱动进程内（共享宿主执行，杀驱动≠停任务）`
    : "长回合随驱动进程死亡中断：执行在驱动进程内，杀驱动=有效停止通道" }, null, 2));
console.log("完成=" + done + (done ? `（${at}s 观测到）执行宿主=共享，杀驱动不能停任务` : "杀驱动=有效停止通道"));
process.exit(0);
