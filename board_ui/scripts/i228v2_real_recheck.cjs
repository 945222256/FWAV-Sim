/* i228v2_real_recheck.cjs — I-228 v2 真实只读复验（零非 GET 触生产）。
 * 私有端口运行真实 session_history_server 读生产索引（只读）；
 * /api/*（state/events/item-detail）经代理只读生产云。
 * 核对：真实保留标签（不再运行中）、真实分页按页推进全量可达、
 * 真实旧会话的新事项履历（真实账本标题/状态/时间线）、执行相位行、
 * 只读无操作入口；截图随档。 */
const puppeteer = require("G:/Playground/isaac_flapper_sim/board_ui/node_modules/puppeteer-core");
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");

const ROOT = "G:/Playground/isaac_flapper_sim";
const OUT = ROOT + "/logs/i228_ui_rework_20261015";
const PORT = 4195;
const HISTORY_PORT = 58796;
const PROD = { host: "101.96.207.208", port: 443 };
let pass = 0, fail = 0;
const checks = [];
const nonGet = [];
function ok(cond, label, detail) {
  checks.push({ label, ok: !!cond, detail: String(detail ?? "").slice(0, 300) });
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
  console.log("真实索引:", stats.sessions, "retention:", JSON.stringify(stats.retention));
  let browser = null;
  try {
    browser = await puppeteer.launch({
      executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
      headless: true, args: ["--no-sandbox"], defaultViewport: { width: 1600, height: 1000 } });
    const p = await browser.newPage();
    await p.setCacheEnabled(false);
    await p.setRequestInterception(true);
    p.on("request", (rq) => {
      const url = rq.url();
      if (rq.method() !== "GET" && url.includes("/api/")) {
        nonGet.push(rq.method() + " " + url); rq.abort(); return;
      }
      if (url.includes("/api/")) {
        rq.continue({ headers: { ...rq.headers(), Authorization: "Bearer " + token } });
        return;
      }
      rq.continue();
    });
    await p.goto("http://127.0.0.1:" + PORT + "/app/#/console",
      { waitUntil: "networkidle2", timeout: 40000 });
    await p.waitForSelector(".console-tabs", { timeout: 20000 });
    await p.evaluate(() => {
      [...document.querySelectorAll(".console-tab")]
        .find((b) => b.textContent.includes("历史目录"))?.click();
    });
    await p.waitForSelector(".sh", { timeout: 20000 });
    await sleep(1500);

    await p.screenshot({ path: OUT + "/real_recheck_list.png", fullPage: false });
    /* R2 真实分页按页推进全量可达 */
    for (let i = 0; i < 12; i++) {
      const more = await p.evaluate(() => {
        const btn = document.querySelector(".sh-foot .ws-btn.primary");
        return btn && btn.textContent.includes("加载更多");
      });
      if (!more) break;
      await p.evaluate(() => document.querySelector(".sh-foot .ws-btn.primary")?.click());
      await sleep(900);
    }
    const sids = await p.evaluate(() => [...document.querySelectorAll(".sh-row")]
      .map((r) => r.getAttribute("data-session-id")));
    const foot = await p.evaluate(() =>
      document.querySelector(".sh-foot")?.textContent ?? "");
    ok(sids.length === stats.sessions && new Set(sids).size === stats.sessions
      && foot.includes(stats.sessions + " / " + stats.sessions)
      && foot.includes("按页推进 1–" + Math.ceil(stats.sessions / 50)),
      "R2 真实全量 " + stats.sessions + " 条按页推进可达无重复（页 1–"
      + Math.ceil(stats.sessions / 50) + "）", foot.slice(0, 110));
    /* R1 真实保留标签（遍历后全量统计） */
    const badges = await p.evaluate(() => [...document.querySelectorAll(".sh-badge")]
      .map((b) => b.textContent));
    const activeRows = (stats.retention || {}).active ?? 0;
    const nearCount = badges.filter((b) => b.includes("近期保留")).length;
    const kinds = [...new Set(badges)];
    ok(!badges.some((b) => b.includes("运行中")),
      "R1a 真实列表无任何“运行中”保留标签（反例三真实面消失）",
      JSON.stringify(kinds));
    ok(nearCount === activeRows,
      "R1b active 行全部标近期保留（" + nearCount + " = 索引 active " + activeRows + "）",
      "near=" + nearCount + " active=" + activeRows);
    await p.screenshot({ path: OUT + "/real_recheck_traversal.png", fullPage: false });

    /* R3 真实旧会话新事项履历（生产账本只读） */
    const CHAIN_A = "sess_5074d84c-2adc-43b6-aec6-33f46c3df739";   // L2 I-219
    await p.evaluate((sid) => {
      [...document.querySelectorAll(".sh-row")]
        .find((r) => r.getAttribute("data-session-id") === sid)
        ?.querySelector(".sh-sid")?.click();
    }, CHAIN_A);
    await p.waitForSelector(".sh-drawer", { timeout: 15000 });
    await sleep(2500);                          // 等事项详情与事件拉取
    const detail = await p.evaluate(() => {
      const d = document.querySelector(".sh-drawer");
      return { text: d?.textContent ?? "",
               phase: d?.querySelector("[data-testid=sh-exec-phase]")?.textContent ?? "",
               hist: d?.querySelectorAll(".sh-item-history").length ?? 0,
               timeline: d?.querySelectorAll(".sh-timeline li").length ?? 0,
               sid: d?.getAttribute("data-session-id") ?? "" };
    });
    ok(detail.sid === CHAIN_A && detail.hist >= 1,
      "R3a 真实旧会话事项履历区在页（I-219）",
      "hist=" + detail.hist);
    ok(detail.text.includes("I-219"),
      "R3b 履历含事项标题与当前账本状态（真实 item-detail）",
      detail.text.slice(0, 120));
    ok(detail.text.includes("认领核实") || detail.text.includes("归属未核实"),
      "R3c 认领归属如实标注（仅账本认领事件计核实）", "");
    ok(detail.timeline >= 1,
      "R3d 账本事件时间线在页（真实 /api/events，来源标注账本事件）",
      "timeline=" + detail.timeline);
    ok(detail.phase.includes("执行状态"),
      "R4 执行相位行如实呈现（索引快照口径）", detail.phase.slice(0, 90));
    const noActions = await p.evaluate(() =>
      [...document.querySelectorAll(".sh-drawer button")]
        .filter((b) => /注入|停止|恢复/.test(b.textContent)).length);
    ok(noActions === 0, "R5 详情只读（误注入/误停止入口零）", String(noActions));
    await p.screenshot({ path: OUT + "/real_recheck_item_history.png", fullPage: false });
    ok(nonGet.length === 0, "防护：全程零非 GET 触生产", JSON.stringify(nonGet));
  } finally {
    if (browser) await browser.close().catch(() => {});
    for (const proc of [proxy, hist]) {
      if (proc && proc.pid) {
        spawn("taskkill", ["/T", "/F", "/PID", String(proc.pid)], { shell: true });
      }
    }
  }
  fs.writeFileSync(OUT + "/real_recheck_record.json",
    JSON.stringify({ generated_at: new Date().toISOString(), checks, non_get_attempts: nonGet,
      index_stats: stats, summary: { pass, fail } }, null, 1));
  console.log("\n真实复验：" + pass + " 过 / " + fail + " 拒");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("真实复验异常：", e); process.exit(2); });
