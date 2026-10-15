/* i228_real_check.cjs — I-228 真实端到端页面验证（只读）。
 * 数据面全部真实：私有端口运行真实 session_history_server 读生产索引
 * （lp_sync/session_history/index.json 只读）；/api/* 经代理转生产云
 * （只 GET：观察窗实况与入口来自生产 state）；页面为 board_ui/dist 构建。
 * 防护：拦截层放行 GET、显式拒绝任何非 GET（生产零写）。
 * 验证：真实线入口、真实总数与全量遍历、真实交接链双向、真实旧会话
 * 对话摘要、只读无操作入口；截图随档。 */
const puppeteer = require("puppeteer-core");
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");

const ROOT = "G:/Playground/isaac_flapper_sim";
const OUT = ROOT + "/logs/i228_session_history_ui_20261015";
const PORT = 4191;
const HISTORY_PORT = 58796;
const PROD = { host: "101.96.207.208", port: 443 };
let pass = 0, fail = 0;
const record = { generated_at: new Date().toISOString(), checks: [], non_get_attempts: [] };
function ok(cond, label, detail) {
  record.checks.push({ label, ok: !!cond, detail: String(detail ?? "").slice(0, 300) });
  if (cond) { pass++; console.log("  [过] " + label); }
  else { fail++; console.log("  [拒] " + label + "：" + String(detail ?? "")); }
  return !!cond;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function histApi(path) {
  return new Promise((resolve, reject) => {
    http.get({ host: "127.0.0.1", port: HISTORY_PORT, path }, (r) => {
      let b = ""; r.on("data", (c) => { b += c; });
      r.on("end", () => { try { resolve(JSON.parse(b)); } catch (e) { reject(e); } });
    }).on("error", reject);
  });
}

(async () => {
  const hist = spawn("python", [ROOT + "/lp_sync/session_history_server.py"], {
    cwd: ROOT + "/lp_sync", shell: true, stdio: "ignore",
    env: { ...process.env, LP_SESSION_HISTORY_PORT: String(HISTORY_PORT),
           LP_SESSION_HISTORY_DIR: ROOT + "/lp_sync/session_history" } });
  const proxy = spawn("node", [ROOT + "/board_ui/scripts/i228_proxy.cjs",
    String(HISTORY_PORT), String(PORT), String(PROD.port), PROD.host],
    { shell: true, stdio: "ignore" });
  await sleep(3000);
  const token = fs.readFileSync(ROOT + "/lp_sync/agent_token", "utf8").trim();
  const stats = (await histApi("/api/session_history/stats")).result;
  console.log("真实索引统计:", JSON.stringify(stats.retention), "sessions=", stats.sessions);
  let browser = null;
  try {
    browser = await puppeteer.launch({
      executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
      headless: true, args: ["--no-sandbox"], defaultViewport: { width: 1600, height: 1000 } });
    const p = await browser.newPage();
    await p.setCacheEnabled(false);
    await p.setRequestInterception(true);
    p.on("request", (rq) => {
      const url = rq.url();
      if (rq.method() !== "GET" && url.includes("/api/")
          && !url.includes("/api/session_history/")) {
        record.non_get_attempts.push(rq.method() + " " + url);
        rq.abort(); return;                       // 生产零写防线
      }
      if (url.includes("/api/session_history/")) { rq.continue(); return; }
      if (url.includes("/api/")) {
        rq.continue({ headers: { ...rq.headers(), Authorization: "Bearer " + token } });
        return;
      }
      rq.continue();
    });
    await p.goto("http://127.0.0.1:" + PORT + "/app/#/console",
      { waitUntil: "networkidle2", timeout: 40000 });
    await p.waitForSelector(".console-tabs", { timeout: 20000 });

    /* R1 真实观察窗 + L3 完整历史入口 */
    await p.evaluate(() => {
      [...document.querySelectorAll(".console-tab")]
        .find((b) => b.textContent.includes("观察窗"))?.click();
    });
    await p.waitForSelector(".ws-group", { timeout: 20000 });
    await sleep(1500);
    const watchInfo = await p.evaluate(() => ({
      lanes: [...document.querySelectorAll(".ws-group")].length,
      activeCards: document.querySelectorAll(".ws-card:not(.retained)").length,
      gray: document.querySelectorAll(".ws-card.retained").length }));
    ok(watchInfo.lanes >= 10, "R1a 生产观察窗十条业务线常驻（真实 state）",
      JSON.stringify(watchInfo));
    await p.screenshot({ path: OUT + "/real_watch.png", fullPage: false });
    const l3Count = (stats.by_lane || {}).L3 ?? 0;
    const clicked = await p.evaluate(() => {
      const g = [...document.querySelectorAll(".ws-group")]
        .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith("L3"));
      const btn = g?.querySelector(".ws-history-toggle");
      if (!btn) return false;
      btn.click();
      return true;
    });
    await p.waitForSelector(".sh", { timeout: 20000 });
    for (let i = 0; i < 15; i++) {
      const c = await p.evaluate(() => document.querySelector(".sh-counts")?.textContent ?? "");
      if (c.includes("当前筛选 " + l3Count)) break;
      await sleep(800);
    }
    const counts1 = await p.evaluate(() =>
      document.querySelector(".sh-counts")?.textContent ?? "");
    ok(clicked && counts1.includes("当前筛选 " + l3Count) && counts1.includes("索引共 " + stats.sessions),
      "R1b 真实 L3 完整历史入口：筛选数与真实索引 by_lane 逐项一致", counts1);
    const l3Sids = await p.evaluate(() =>
      [...document.querySelectorAll(".sh-row")].map((r) => r.getAttribute("data-session-id")));
    const l3Api = (await histApi("/api/session_history/sessions?lane=L3&page_size=200")).result;
    ok(l3Sids.length === l3Api.total
      && JSON.stringify([...l3Sids].sort()) === JSON.stringify([...l3Api.items.map((x) => x.session_id)].sort()),
      "R1c L3 列表与真实索引接口逐项一致（" + l3Sids.length + " 条）",
      "page=" + l3Sids.length + " api=" + l3Api.total);
    await p.screenshot({ path: OUT + "/real_l3_history.png", fullPage: false });

    /* R2 真实全量遍历（264 条 → 6 页） */
    await p.evaluate(() => {
      const sel = [...document.querySelectorAll(".sh-filters select")][0];
      const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(sel), "value").set;
      set.call(sel, "");
      sel.dispatchEvent(new Event("change", { bubbles: true }));
      [...document.querySelectorAll(".sh-filters .ws-btn")]
        .find((b) => b.textContent.includes("应用筛选"))?.click();
    });
    await sleep(1200);
    let all = [];
    for (let i = 0; i < 30; i++) {
      all = await p.evaluate(() =>
        [...document.querySelectorAll(".sh-row")].map((r) => r.getAttribute("data-session-id")));
      const more = await p.evaluate(() => {
        const btn = document.querySelector(".sh-foot .ws-btn.primary");
        return btn && btn.textContent.includes("加载更多");
      });
      if (!more) break;
      await p.evaluate(() => document.querySelector(".sh-foot .ws-btn.primary")?.click());
      await sleep(900);
    }
    const uniq = new Set(all);
    const fullApi = [];
    for (let pg = 1; pg <= Math.ceil(stats.sessions / 50); pg++) {
      const r = (await histApi("/api/session_history/sessions?page=" + pg + "&page_size=50")).result;
      fullApi.push(...r.items.map((x) => x.session_id));
    }
    ok(all.length === stats.sessions && uniq.size === all.length
      && JSON.stringify([...uniq].sort()) === JSON.stringify([...fullApi].sort()),
      "R2 真实全量遍历：" + all.length + " 条跨 " + Math.ceil(stats.sessions / 50)
      + " 页无重复无遗漏且与接口逐项一致",
      "loaded=" + all.length + " uniq=" + uniq.size + " api=" + fullApi.length);
    const counts2 = await p.evaluate(() =>
      document.querySelector(".sh-counts")?.textContent ?? "");
    ok(counts2.includes("已加载 " + stats.sessions), "R2b 计数行=真实 total", counts2);
    await p.screenshot({ path: OUT + "/real_traversal_done.png", fullPage: false });

    /* R3 真实交接链（L2，I-219 的 70% 交接对） */
    const CHAIN_A = "sess_5074d84c-2adc-43b6-aec6-33f46c3df739";
    const CHAIN_B = "sess_87092162-68e3-4f6b-a0d9-76ee4d3f13ab";
    await p.evaluate(() => {
      const sel = [...document.querySelectorAll(".sh-filters select")][0];
      const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(sel), "value").set;
      set.call(sel, "L2");
      sel.dispatchEvent(new Event("change", { bubbles: true }));
      [...document.querySelectorAll(".sh-filters .ws-btn")]
        .find((b) => b.textContent.includes("应用筛选"))?.click();
    });
    await sleep(1200);
    await p.evaluate((sid) => {
      const row = [...document.querySelectorAll(".sh-row")]
        .find((r) => r.getAttribute("data-session-id") === sid);
      row?.querySelector(".sh-sid")?.click();
    }, CHAIN_A);
    await p.waitForSelector(".sh-drawer", { timeout: 15000 });
    await sleep(1000);
    let dtxt = await p.evaluate(() => document.querySelector(".sh-drawer")?.textContent ?? "");
    const chainSids = await p.evaluate(() =>
      [...document.querySelectorAll(".sh-chain-sid")].map((b) => b.textContent));
    ok(dtxt.includes("已取得定向停止和新会话回读回执"),
      "R3a 真实交接卡渲染（I-219 70% 交接，真实原因）", dtxt.slice(0, 120));
    ok(chainSids.includes(CHAIN_B),
      "R3a2 真实后代链含接续会话（双向可查之一）", JSON.stringify(chainSids));
    await p.screenshot({ path: OUT + "/real_chain_head.png", fullPage: false });
    await p.evaluate((sid) => {
      [...document.querySelectorAll(".sh-chain-sid")]
        .find((b) => b.textContent === sid)?.click();
    }, CHAIN_B);
    await sleep(1200);
    const dSid = await p.evaluate(() =>
      document.querySelector(".sh-drawer")?.getAttribute("data-session-id") ?? "");
    dtxt = await p.evaluate(() => document.querySelector(".sh-drawer")?.textContent ?? "");
    ok(dSid === CHAIN_B && dtxt.includes("前代链"),
      "R3b 链上跳转到接续会话且可回溯（双向可查）", dSid.slice(5, 20));
    await p.screenshot({ path: OUT + "/real_chain_successor.png", fullPage: false });

    /* R4 真实旧会话（超时保留，真实对话摘要） */
    const OLD = "sess_89b73af3-2010-42e1-9e37-3b7a9642d847";   // L7 超时保留样例
    await p.evaluate(() => {
      document.querySelector(".sh-drawer .lab-btn-minor")?.click?.();
    });
    await sleep(400);
    await p.evaluate(() => {
      const sel = [...document.querySelectorAll(".sh-filters select")][0];
      const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(sel), "value").set;
      set.call(sel, "L7");
      sel.dispatchEvent(new Event("change", { bubbles: true }));
      [...document.querySelectorAll(".sh-filters .ws-btn")]
        .find((b) => b.textContent.includes("应用筛选"))?.click();
    });
    await sleep(1200);
    await p.evaluate((sid) => {
      const row = [...document.querySelectorAll(".sh-row")]
        .find((r) => r.getAttribute("data-session-id") === sid);
      row?.querySelector(".sh-sid")?.click();
    }, OLD);
    await p.waitForSelector(".sh-drawer", { timeout: 15000 });
    await sleep(1200);
    dtxt = await p.evaluate(() => document.querySelector(".sh-drawer")?.textContent ?? "");
    const dialogOk = await p.evaluate(() => {
      const d = document.querySelector(".sh-drawer");
      return { texts: d?.querySelectorAll(".sh-texts li").length ?? 0,
               tools: d?.querySelector(".sh-tools")?.textContent ?? "",
               reason: d?.querySelector(".sh-reason-block")?.textContent ?? "" };
    });
    ok(dtxt.includes("超时保留") && dtxt.includes("I-091"),
      "R4a 真实旧会话详情：保留原因与真实认领事项在页", dtxt.slice(0, 120));
    ok(dialogOk.texts > 0 || dialogOk.tools.length > 0 || dialogOk.reason.length > 0,
      "R4b 只读对话与工具摘要有真实内容或明确原因",
      JSON.stringify(dialogOk).slice(0, 160));
    const noActions = await p.evaluate(() =>
      [...document.querySelectorAll(".sh-drawer button")]
        .filter((b) => /注入|停止|恢复/.test(b.textContent)).length);
    ok(noActions === 0, "R5 真实详情只读：无注入/停止/恢复入口", String(noActions));
    await p.screenshot({ path: OUT + "/real_old_session_detail.png", fullPage: false });
    await p.close().catch(() => {});
    ok(record.non_get_attempts.length === 0,
      "防护核对：全程零非 GET 请求触达生产", JSON.stringify(record.non_get_attempts));
  } finally {
    if (browser) await browser.close().catch(() => {});
    for (const proc of [proxy, hist]) {
      if (proc && proc.pid) {
        spawn("taskkill", ["/T", "/F", "/PID", String(proc.pid)], { shell: true });
      }
    }
  }
  record.pass = pass; record.fail = fail;
  record.index_stats = stats;
  fs.writeFileSync(OUT + "/real_check_record.json", JSON.stringify(record, null, 1));
  console.log("\n结果：" + pass + " 过 / " + fail + " 拒；记录 " + OUT + "/real_check_record.json");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("真实端到端异常：", e); process.exit(2); });
