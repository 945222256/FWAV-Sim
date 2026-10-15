/* i234_real_check.cjs — I-234 真实联调页面核对（只读；零非 GET 触生产）。
 * 数据面：待领快照与事件来自隔离云真实端点（数据为生产来源真实只读构建）；
 * 观察窗实况来自生产云只读 state。核对判据一、三、五的真实数据面，
 * 截图随档。用法：node i234_real_check.cjs <proxy_port> */
const puppeteer = require("G:/Playground/isaac_flapper_sim/board_ui/node_modules/puppeteer-core");
const fs = require("fs");

const PORT = process.argv[2] || "4193";
const ROOT = "G:/Playground/isaac_flapper_sim";
const OUT = ROOT + "/logs/i234_pending_work_ui_20261015";
const SNAP = JSON.parse(fs.readFileSync(OUT + "/real_snapshot_v1.json", "utf8"));
const EVS = JSON.parse(fs.readFileSync(OUT + "/real_events.json", "utf8"));
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

(async () => {
  const browser = await puppeteer.launch({
    executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    headless: true, args: ["--no-sandbox"], defaultViewport: { width: 1600, height: 1000 } });
  try {
    const p = await browser.newPage();
    await p.setCacheEnabled(false);
    await p.setRequestInterception(true);
    const token = fs.readFileSync(ROOT + "/lp_sync/agent_token", "utf8").trim();
    p.on("request", (rq) => {
      const url = rq.url();
      if (rq.method() !== "GET" && url.includes("/api/")
          && !url.includes("/api/console/pending_work")) {
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
        .find((b) => b.textContent.includes("观察窗"))?.click();
    });
    await p.waitForSelector(".pw-summary", { timeout: 20000 });
    await sleep(2500);

    /* 判据一（真实）：总量、分类、明细逐项 */
    const total = await p.evaluate(() =>
      document.querySelector(".pw-total b")?.textContent);
    ok(total === String(SNAP.summary.total_pending),
      "R1 真实待领总量与快照一致（" + SNAP.summary.total_pending + "）", total);
    const cats = await p.evaluate(() => [...document.querySelectorAll(".pw-cat-btn")]
      .filter((b) => b.getAttribute("data-cat"))
      .map((b) => ({ cat: b.getAttribute("data-cat"), n: Number(b.getAttribute("data-count")) })));
    const expCats = Object.entries(SNAP.summary.by_category)
      .map(([cat, n]) => ({ cat, n }));
    ok(JSON.stringify(cats) === JSON.stringify(expCats),
      "R1b 四类计数与真实快照逐项一致", JSON.stringify(cats));
    await p.evaluate(() => document.querySelector(".pw-expand-all")?.click());
    await sleep(600);
    const allIds = await p.evaluate(() => [...document.querySelectorAll(
      ".pw-all-list .pw-task")].map((t) => t.getAttribute("data-item-id")));
    const expIds = SNAP.items.map((x) => x.item_id).sort();
    ok(allIds.length === SNAP.items.length
      && JSON.stringify([...allIds].sort()) === JSON.stringify(expIds),
      "R1c 展开明细与真实快照按事项号逐项一致（"
      + SNAP.items.length + " 项零重复零遗漏）",
      "page=" + allIds.length + " snap=" + expIds.length);
    const laneCounts = await p.evaluate(() => {
      const m = {};
      document.querySelectorAll(".ws-group[data-pending]").forEach((g) => {
        const lane = (g.querySelector(".ws-group-lane")?.textContent ?? "").match(/^L\d+/)?.[0] ?? "";
        m[lane] = Number(g.getAttribute("data-pending"));
      });
      return m;
    });
    const expLanes = {};
    for (const [ln, v] of Object.entries(SNAP.summary.by_lane ?? {})) {
      if (v && v.count) expLanes[ln] = v.count;
    }
    const laneOk = Object.keys(expLanes).every((ln) => laneCounts[ln] === expLanes[ln]);
    ok(laneOk, "R1d 各线待领计数与真实快照 by_lane 逐项一致",
      JSON.stringify({ got: laneCounts, exp: expLanes }));
    const un = await p.evaluate(() => ({
      n: document.querySelector(".pw-unassigned")?.textContent ?? "",
      group: document.querySelector(".pw-unassigned-group")?.textContent ?? "" }));
    ok(un.n.includes("未分配 " + (SNAP.summary.unassigned?.count ?? 0))
      && un.group.includes(SNAP.summary.unassigned.items[0] ?? ""),
      "R1e 未分配独立入口与真实快照一致（含 " + SNAP.summary.unassigned.items.join("、") + "）",
      un.n + "|" + un.group.slice(0, 60));
    await p.screenshot({ path: OUT + "/real_summary.png", fullPage: false });

    /* 判据三（真实）：打开真实事项原因 */
    const target = SNAP.items.find((x) => x.primary_reason?.code === "DEP_NOT_CLEARED")
      ?? SNAP.items[0];
    await p.evaluate((sid) => {
      [...document.querySelectorAll(".pw-all-list .pw-task")]
        .find((t) => t.getAttribute("data-item-id") === sid)
        ?.querySelector(".pw-task-line")?.click();
    }, target.item_id);
    await sleep(600);
    const dtxt = await p.evaluate(() =>
      document.querySelector(".pw-detail")?.textContent ?? "");
    ok(dtxt.includes(target.primary_reason?.code ?? "")
      && dtxt.includes(target.primary_reason?.provenance ?? "provenance"),
      "R3 真实事项原因展开：原因码与出处逐字一致（" + target.item_id + "）",
      dtxt.slice(0, 140));
    ok(dtxt.includes(target.evaluation?.evaluator ?? "evaluator"),
      "R3b 真实评估器标注（已入队=内核准入，未入队=静态同源）",
      target.evaluation?.evaluator ?? "");
    await p.screenshot({ path: OUT + "/real_reason_detail.png", fullPage: false });

    /* 真实过期样例：登记依赖快照采样期已过 → 页面如实标注待核对 */
    const metaNow = await p.evaluate(() =>
      document.querySelector(".pw-meta")?.textContent ?? "");
    const stale = metaNow.includes("采样已过期");
    ok(stale, "R2 真实过期快照如实标注（按待核对处理，不当作当前事实）",
      metaNow.slice(0, 140));
    /* 判据五（真实）：容量与预留 */
    const cap = await p.evaluate(() =>
      document.querySelector(".pw-cap")?.textContent ?? "");
    const g = SNAP.capacity.global;
    ok(cap.includes("上限 " + g.effective_limit)
      && cap.includes("已占用 " + g.occupied)
      && cap.includes("可派 " + g.dispatchable)
      && cap.includes("预留 未设置"),
      "R5 真实容量读数与快照逐项一致且预留如实未设置", cap);

    /* 事件面（真实端点）：基线读取无错 */
    const meta = await p.evaluate(() =>
      document.querySelector(".pw-meta")?.textContent ?? "");
    ok(!meta.includes("事件接口失败"),
      "R4 事件端点基线读取正常（真实事件 " + EVS.total_events + " 条入隔离云）",
      meta.slice(0, 120));

    /* 回归：历史入口与灰卡（生产真实 state） */
    const regress = await p.evaluate(() => ({
      hist: document.querySelectorAll(".ws-history-toggle").length,
      gray: document.querySelectorAll(".ws-card.retained").length,
      lanes: document.querySelectorAll(".ws-group").length }));
    ok(regress.hist >= 5 && regress.lanes >= 10,
      "回归：完整历史入口与十线常驻保留（生产实况）", JSON.stringify(regress));
    await p.screenshot({ path: OUT + "/real_watch_full.png", fullPage: false });

    ok(nonGet.length === 0, "防护：全程零非 GET 请求触达生产",
      JSON.stringify(nonGet));
  } finally {
    await browser.close().catch(() => {});
  }
  fs.writeFileSync(OUT + "/real_check_record.json",
    JSON.stringify({ generated_at: new Date().toISOString(), checks,
      non_get_attempts: nonGet,
      snapshot_id: SNAP.snapshot_id, total_pending: SNAP.summary.total_pending,
      events_total: EVS.total_events, summary: { pass, fail } }, null, 1));
  console.log("真实联调：" + pass + " 过 / " + fail + " 拒");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("真实联调异常：", e); process.exit(2); });
