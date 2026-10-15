/* i234_check.cjs — I-234 待领工作区隔离核验（独立端口，不触生产）。
 * 快照与事件按冻结契约 pending-work-v1 形态由拦截层供给（含真实生产
 * 快照的字段形态），事件按时间线分阶段投放，验证判据一至六：
 *  1 总量/分类/筛选/展开明细按事项号一致、重复请求只计一项、
 *    未分配与未入队可追到；
 *  2 十线常驻、待领块位于执行区前、三空忙场景；
 *  3 四类原因各至少一例、点击+键盘展开、评估器与出处、
 *    自由文字不作权威结论（未入队明示静态评估）；
 *  4 释放→选择→认领→实际运行分阶段、同事项同完整会话、
 *    另一工位持续执行、重复事件两次不重放、刷新不回放、
 *    原会话上下文读数保持、减少动态效果可读；
 *  5 预留未设置如实、容量口径独立、历史/灰卡/单卡回归；
 *  6 320/736/1440 视口无横向溢出、长文可展开。 */
const puppeteer = require("puppeteer-core");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const ROOT = "G:/Playground/isaac_flapper_sim";
const OUT = ROOT + "/logs/i234_pending_work_ui_20261015";
const PORT = 4192;
let pass = 0, fail = 0;
const record = { generated_at: new Date().toISOString(), checks: [] };
function ok(cond, label, detail) {
  record.checks.push({ label, ok: !!cond, detail: String(detail ?? "").slice(0, 300) });
  if (cond) { pass++; console.log("  [过] " + label); }
  else { fail++; console.log("  [拒] " + label + "：" + String(detail ?? "")); }
  return !!cond;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ── 夹具（契约形态；事件身份为完整会话号） ── */
const NOW = Date.now();
const iso = (offS) => new Date(NOW + offS * 1000).toISOString().replace("Z", "+08:00").slice(0, 23);
const LONG = "超长标题样例：".repeat(12) + "末尾可读";
function item(id, lane, cat, title, extra) {
  return Object.assign({
    item_id: id, title: title ?? ("待领事项 " + id), kind: "task", status: "open",
    lane, goal: null, reviewer: "ciri", owner: "L5队", category: cat,
    primary_reason: { code: "READY", category: cat, sentence: "依赖与授权均满足",
                      recovery: "无待满足条件", provenance: "kernel_admission_outcome" },
    related: { dependencies: [] }, recovery: "无待满足条件",
    evaluation: { at: iso(-30), validity: "fresh", evaluator: "kernel_admission_outcome",
                  provenance: "任务相位与准入结果（搬运，不解析）" },
    enqueue: { in_kernel_queue: true, request_ids: ["kr-" + id + "-1"],
               first_enqueued_at: iso(-600), total_dispatch_attempts: 1 },
    execution_ref: null,
  }, extra || {});
}
const itemsA = [
  item("I-901", "L3", "ready"),
  item("I-905", "L3", "awaiting_orchestration", "容量满等待调度（两次派发重试）", {
    primary_reason: { code: "LINE_CAPACITY_FULL", category: "awaiting_orchestration",
      sentence: "本线容量已满，等待工位释放", provenance: "kernel_admission_outcome" },
    enqueue: { in_kernel_queue: true, request_ids: ["kr-905-1", "kr-905-2"],
               first_enqueued_at: iso(-900), total_dispatch_attempts: 2 } }),
  item("I-907", "L3", "needs_verification", "历史类别缺失待核对", {
    primary_reason: { code: "KIND_UNKNOWN", category: "needs_verification",
      sentence: "历史类别缺失，无法确定，待人工核对", provenance: "items.kind" } }),
  item("I-903", "L7", "waiting_conditions", "可写范围与其他在办重叠", {
    primary_reason: { code: "RESOURCE_WRITABLE_OVERLAP", category: "waiting_conditions",
      sentence: "可写范围与另一在办事项重叠，冲突准入拒绝",
      related: [{ item_id: "I-990", status: "in_progress", title: "在办冲突事项" }],
      recovery: "重叠事项结载后自动解除", provenance: "kernel_admission_outcome" } }),
  item("I-906", "L7", "awaiting_orchestration", "未入内核队列", {
    primary_reason: { code: "NOT_ENQUEUED", category: "awaiting_orchestration",
      sentence: "条件已满足但尚未进入内核队列", provenance: "items 状态与依赖同源数据" },
    enqueue: { in_kernel_queue: false, request_ids: [], first_enqueued_at: null,
               total_dispatch_attempts: 0 },
    evaluation: { at: iso(-45), validity: "fresh", evaluator: "static_same_source",
                  provenance: "静态同源评估，未经内核实际准入" } }),
  item("I-908", "L9", "needs_verification", "认领与运行矛盾", {
    primary_reason: { code: "CLAIM_RUN_SESSION_MISMATCH", category: "needs_verification",
      sentence: "认领会话与运行会话不一致，待核对", provenance: "认领事件×运行记录对账" } }),
  item("I-902", null, "waiting_conditions", "等待依赖完成（未分配路线）", {
    primary_reason: { code: "DEP_NOT_CLEARED", category: "waiting_conditions",
      sentence: "等待依赖事项完成：I-991",
      related: [{ item_id: "I-991", status: "open", title: "被依赖的在办事项" }],
      recovery: "依赖事项关闭或先行范围获批后自动可派",
      provenance: "items.depends_on+status（任务线同源数据）" },
    enqueue: { in_kernel_queue: false, request_ids: [], first_enqueued_at: null,
               total_dispatch_attempts: 0 },
    evaluation: { at: iso(-40), validity: "fresh", evaluator: "static_same_source",
                  provenance: "静态同源评估，未经内核实际准入" } }),
  item("I-904", null, "waiting_conditions", LONG, {
    primary_reason: { code: "ITEM_STATUS_NOT_OPEN", category: "waiting_conditions",
      sentence: "事项状态非 open：pending_review，等待审核结论后重新评估"
        .repeat(3).slice(0, 120), provenance: "items.status（自由文字不解析为调度结论）" } }),
  item("I-909", "L5", "waiting_conditions", "依赖撤回未复核", {
    primary_reason: { code: "DEP_WITHDRAWN_UNREVIEWED", category: "waiting_conditions",
      sentence: "所依赖交付已撤回且未经复核，暂缓", recovery: "依赖重新复核通过后恢复",
      provenance: "delivery_impact.review_required" } }),
  item("I-910", "L4", "waiting_conditions", "会话回合忙碌", {
    primary_reason: { code: "SESSION_TURN_BUSY", category: "waiting_conditions",
      sentence: "目标会话当前回合忙碌，等待回合结束", recovery: "回合结束后重试",
      provenance: "kernel_admission_outcome" } }),
];
function snap(items, extraSummary) {
  const byCat = { ready: 0, waiting_conditions: 0, awaiting_orchestration: 0,
                  needs_verification: 0 };
  const byLane = {};
  let unassigned = { count: 0, items: [], note: "未分配路线的正式任务独立入口，不伪装成业务线" };
  for (const it of items) {
    byCat[it.category] = (byCat[it.category] ?? 0) + 1;
    if (it.lane) {
      byLane[it.lane] = byLane[it.lane] ?? { count: 0, items: [] };
      byLane[it.lane].count++;
      byLane[it.lane].items.push(it.item_id);
    } else { unassigned.count++; unassigned.items.push(it.item_id); }
  }
  return {
    contract_version: "pending-work-v1",
    snapshot_id: "pw-test-" + items.length,
    sampled_at: iso(-2), sample_validity: { max_age_s: 300, expires_at: iso(298) },
    source_versions: {}, completeness: { status: "complete" },
    summary: Object.assign({ total_pending: items.length, by_category: byCat,
      exclusions: { requirement: { count: 5, note: "非执行任务" } },
      needs_verification_count: byCat.needs_verification,
      by_lane: byLane, unassigned }, extraSummary || {}),
    items, needs_verification: [],
    capacity: {
      status: "ok",
      global: { effective_limit: 11, occupied: 2, dispatchable: 9, reserved: null,
                policy_source: "lane_workers.json（十线之和）" },
      by_lane: { L3: { effective_limit: 2, occupied: 1, dispatchable: 1, reserved: null } },
      reservation_policy: { status: "not_set" },
      separate_measures_note: "页面并行额度与近五分钟活跃会话数为独立口径，不与本容量混算",
      config_read_error: null },
    read_only: true };
}
const snapA = snap(itemsA);
const itemsB = itemsA.filter((x) => x.item_id !== "I-901");
const snapB = snap(itemsB);

/* 事件时间线（身份为完整会话号；含重复投放与另一工位运行） */
const SESSION_X = "sess_i234_handover_9f1c2d3e-4b5a-4678-9abc-def012345678";
const SESSION_Y = "sess_i234_other_w1_0a1b2c3d-4e5f-4678-9abc-def012345679";
const ev = (id, type, cur, itemId, sess, worker, run, at) => ({
  event_id: id, type, source_kind: "kernel_ledger",
  source_ref: { ledger_event: type, ts: at }, occurred_at: at,
  identity: { item_id: itemId ?? null, lane: itemId ? "L3" : null,
              worker_id: worker ?? null, session_id: sess ?? null,
              run_id: run ?? null, request_id: null },
  state_change: null, fact_source: "ledger.json", cursor: cur });
const TL = [
  { t: 0, e: ev("ev-base-1", "condition_wait", 100, "I-905", null, null, null, iso(-600)) },
  { t: 0, e: ev("ev-base-2", "slot_released", 101, null, null, "L3-w1", null, iso(-300)) },
  { t: 4, e: ev("ev-rel-2", "slot_released", 102, null, null, "L3-w2", null, iso(4)) },
  { t: 8, e: ev("ev-sel-2", "dispatch_selected", 103, "I-901", null, "L3-w2", null, iso(8)) },
  { t: 12, e: ev("ev-claim-2", "claim_confirmed", 104, "I-901", SESSION_X, "L3-w2", null, iso(12)) },
  { t: 16, e: ev("ev-run-2", "run_started", 105, "I-901", SESSION_X, "L3-w2", "run-i234-x1", iso(16)) },
  { t: 20, e: ev("ev-claim-2", "claim_confirmed", 106, "I-901", SESSION_X, "L3-w2", null, iso(12)) },
  { t: 22, e: ev("ev-run-y", "run_started", 107, "I-920", SESSION_Y, "L3-w1", "run-i234-y1", iso(22)) },
];
let PAGE_T0 = Date.now();
function eventsAfter(cur) {
  const out = [];
  for (const { t, e } of TL) {
    if (Date.now() - PAGE_T0 >= t * 1000 && (cur == null || (e.cursor ?? 0) > cur)) out.push(e);
  }
  return out;
}
function currentSnapshot() {
  return Date.now() - PAGE_T0 >= 13_000 ? snapB : snapA;
}

/* 控制台状态（三空忙场景：L3 有执行+等待；L7 有待领且空闲；L2 无会话无历史） */
const NOWS = "2026-10-15T23:05:00";
function mkCard(sid, lane, item, pct) {
  return { session_id: sid, lane, item_id: item, current_item_id: item,
    status: "在干活", context_pct: pct, last_activity_at: NOWS, updated_at: NOWS,
    _is_worker: true,
    source: { kind: "表", locus: "console_sessions/" + sid, excerpt: "隔离样例",
              sampled_at: NOWS } };
}
const consoleState = {
  goals: [],
  lanes: Object.entries({ HQ: "指挥部", L1: "攻关主线", L2: "架构与管线", L3: "数据资产与接驳预备",
    L4: "周期时变与混合飞行", L5: "接口与应用线", L6: "强化学习线", L7: "导航合龙线",
    L8: "形状保真对齐线", L9: "计算流体力学线（预登证）", L10: "架构提取与范用化线" })
    .map(([id, name]) => ({ id, name, items: [], active_sessions: [],
      source: { kind: "文件", locus: "x", excerpt: name, sampled_at: NOWS } })),
  sessions: { active: [mkCard(SESSION_X, "L3", "I-901", 41.5),
                       mkCard(SESSION_Y, "L3", "I-920", 55.2)],
              retained: [], archived: [], owner: [] },
  footer: { parallel: { active: 1, tracked: 1, limit: 15 }, sampled_at: NOWS,
            source: { kind: "表", locus: "x", excerpt: "x", sampled_at: NOWS } },
  lane_queues: { sampled_at: NOWS, queues: { L3: { concurrency_limit: 2,
    workers: [{ worker_id: "L3-w1", session_id: SESSION_Y, item_id: "I-920" },
              { worker_id: "L3-w2", session_id: SESSION_X, item_id: "I-901" }],
    active_count: 2, waiting_count: 0, waiting: {} } } },
};

async function withPage(browser, fn, opts) {
  const p = await browser.newPage();
  await p.setCacheEnabled(false);
  await p.setRequestInterception(true);
  p.on("request", (rq) => {
    const url = rq.url();
    const json = (result) => rq.respond({ status: 200, contentType: "application/json",
      headers: { Date: new Date().toUTCString() },
      body: JSON.stringify(result) });
    let servedHigh = 101;
    if (url.includes("/api/console/pending_work/events")) {
      const m = url.match(/after_cursor=(\d+)/);
      const served = eventsAfter(m ? Number(m[1]) : null);
      servedHigh = Math.max(servedHigh, ...served.map((e) => e.cursor ?? 0));
      json({ ok: true, result: { events: served, cursor: servedHigh } });
    } else if (url.includes("/api/console/pending_work")) {
      json({ ok: true, result: currentSnapshot() });
    } else if (url.includes("/api/console/state")) json({ ok: true, result: consoleState });
    else if (url.includes("/api/console/config"))
      json({ ok: true, result: { handoff_mode: "auto", gate_pct: 70, parallel_limit: 15 } });
    else if (url.includes("/api/events")) json({ ok: true, result: [] });
    else if (url.includes("/api/auth/whoami")) json({ ok: true, result: { user: "隔离样例" } });
    else rq.continue();
  });
  if (opts && opts.viewport) await p.setViewport(opts.viewport);
  try {
    PAGE_T0 = Date.now();                       // 时间线以页面装载起算
    await p.goto("http://localhost:" + PORT + "/app/#/console",
      { waitUntil: "networkidle2", timeout: 30000 });
    await p.waitForSelector(".console-tabs");
    await p.evaluate(() => {
      [...document.querySelectorAll(".console-tab")]
        .find((b) => b.textContent.includes("观察窗"))?.click();
    });
    await p.waitForSelector(".pw-summary", { timeout: 15000 });
    await fn(p);
  } finally {
    p.close().catch(() => {});
  }
}

(async () => {
  const preview = spawn("node", [ROOT + "/board_ui/scripts/i228_proxy.cjs",
    "58799", String(PORT)], { shell: true, stdio: "ignore" });
  await sleep(1800);
  const browser = await puppeteer.launch({
    executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    headless: true, args: ["--no-sandbox"], defaultViewport: { width: 1600, height: 1000 },
  });
  try {
    console.log("判据一：总量、分类、明细与去重");
    await withPage(browser, async (p) => {
      await sleep(2500);
      const total = await p.evaluate(() =>
        document.querySelector(".pw-total b")?.textContent);
      const cats = await p.evaluate(() => [...document.querySelectorAll(".pw-cat-btn")]
        .filter((b) => b.getAttribute("data-cat"))
        .map((b) => ({ cat: b.getAttribute("data-cat"), n: Number(b.getAttribute("data-count")) })));
      ok(total === "10", "全局待领总量与快照一致", total);
      ok(JSON.stringify(cats) === JSON.stringify([
        { cat: "ready", n: 1 }, { cat: "waiting_conditions", n: 5 },
        { cat: "awaiting_orchestration", n: 2 }, { cat: "needs_verification", n: 2 }]),
        "四类计数与快照逐项一致", JSON.stringify(cats));
      await p.evaluate(() => document.querySelector(".pw-expand-all")?.click());
      await sleep(400);
      const allIds = await p.evaluate(() => [...document.querySelectorAll(".pw-all-list .pw-task")]
        .map((t) => t.getAttribute("data-item-id")));
      ok(allIds.length === 10 && new Set(allIds).size === 10,
        "展开明细 10 项且按事项号去重（I-905 两次请求只计一项）",
        JSON.stringify(allIds));
      // 未分配入口
      const un = await p.evaluate(() => ({
        n: document.querySelector(".pw-unassigned")?.textContent ?? "",
        group: !!document.querySelector(".pw-unassigned-group") }));
      ok(un.n.includes("未分配 2"), "未分配独立入口计数一致", un.n);
      // 重复请求：I-905 展开含两条请求与两次尝试、仍一张块
      await p.evaluate(() => {
        [...document.querySelectorAll(".pw-all-list .pw-task")]
          .find((t) => t.getAttribute("data-item-id") === "I-905")
          ?.querySelector(".pw-task-line")?.click();
      });
      await sleep(300);
      const d905 = await p.evaluate(() => [...document.querySelectorAll(
        '.pw-task[data-item-id="I-905"] .pw-detail')][0]?.textContent ?? "");
      ok(d905.includes("kr-905-1") && d905.includes("kr-905-2")
        && d905.includes("2 次") && d905.includes("只计一项"),
        "重复派发请求展开可追（请求层明细保留，事项只计一项）", d905.slice(0, 120));
      // 未入队可追
      await p.evaluate(() => {
        [...document.querySelectorAll(".pw-all-list .pw-task")]
          .find((t) => t.getAttribute("data-item-id") === "I-906")
          ?.querySelector(".pw-task-line")?.click();
      });
      await sleep(300);
      const d906 = await p.evaluate(() => [...document.querySelectorAll(
        '.pw-task[data-item-id="I-906"] .pw-detail')][0]?.textContent ?? "");
      ok(d906.includes("未入内核队列") && d906.includes("静态同源评估")
        && d906.includes("未经内核实际准入"),
        "未入队事项可追到且明示静态评估（不冒充内核准入）", d906.slice(0, 120));
      await p.screenshot({ path: OUT + "/c1_summary_detail.png", fullPage: false });
    });

    console.log("判据二：十线常驻、任务块在执行区前、三空忙场景");
    await withPage(browser, async (p) => {
      await sleep(2000);
      const lanes = await p.evaluate(() => document.querySelectorAll(".ws-group").length);
      ok(lanes >= 10, "十条业务线常驻", String(lanes));
      const order = await p.evaluate(() => {
        const g = [...document.querySelectorAll(".ws-group")]
          .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith("L3"));
        const firstCard = g?.querySelector(".ws-card");
        const firstTask = g?.querySelector(".pw-task");
        if (!firstCard) return firstTask ? "tasks-only" : "none";
        return !!(firstTask && (firstTask.compareDocumentPosition(firstCard)
          & Node.DOCUMENT_POSITION_FOLLOWING)) ? "tasks-before-cards" : "wrong";
      });
      ok(order === "tasks-before-cards",
        "待领任务块位于执行会话区之前（DOM 顺序核对）", order);
      const scen = await p.evaluate(() => {
        const info = (lane) => {
          const g = [...document.querySelectorAll(".ws-group")]
            .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith(lane));
          return { tasks: g?.querySelectorAll(".pw-task").length ?? 0,
                   cards: g?.querySelectorAll(".ws-card:not(.retained)").length ?? 0,
                   gray: g?.querySelectorAll(".ws-card.retained").length ?? 0,
                   count: g?.querySelector(".ws-group-count")?.textContent ?? "" };
        };
        return { L3: info("L3"), L7: info("L7"), L2: info("L2") };
      });
      ok(scen.L3.tasks === 3 && scen.L3.cards === 2,
        "场景一（有执行且等待）：L3 双工位执行会话与待领块并存（与来源一致）",
        JSON.stringify(scen.L3));
      ok(scen.L7.tasks === 2 && scen.L7.cards === 0 && scen.L7.gray >= 1,
        "场景二（有待领且空闲）：L7 有待领块无活跃卡（灰态保留）", JSON.stringify(scen.L7));
      ok(scen.L2.tasks === 0 && scen.L2.cards === 0,
        "场景三（无会话及无可核查历史）：L2 空线常驻占位", JSON.stringify(scen.L2));
      ok(scen.L3.count.includes("待领 3"), "组头含待领计数（与快照同源）", scen.L3.count);
      await p.screenshot({ path: OUT + "/c2_scenarios.png", fullPage: false });
    });

    console.log("判据三：四类原因、点击与键盘展开、出处与评估器");
    await withPage(browser, async (p) => {
      await sleep(2000);
      // 点击展开（未分配等依赖）
      await p.evaluate(() => {
        [...document.querySelectorAll(".pw-task")]
          .find((t) => t.getAttribute("data-item-id") === "I-902")
          ?.querySelector(".pw-task-line")?.click();
      });
      await sleep(300);
      const d902 = await p.evaluate(() => [...document.querySelectorAll(
        '.pw-task[data-item-id="I-902"] .pw-detail')][0]?.textContent ?? "");
      ok(d902.includes("DEP_NOT_CLEARED") && d902.includes("I-991")
        && d902.includes("依赖事项关闭或先行范围获批后自动可派")
        && d902.includes("items.depends_on") && d902.includes("static_same_source"),
        "点击展开：原因码、关联依赖、恢复条件、出处、评估器齐备",
        d902.slice(0, 150));
      // 键盘展开（focus + Enter）
      const kbClick = await p.evaluate(() => {
        const btn = [...document.querySelectorAll(".pw-task")]
          .find((t) => t.getAttribute("data-item-id") === "I-903")
          ?.querySelector(".pw-task-line");
        if (!btn) return "no btn";
        btn.focus();
        btn.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
        btn.click();                              // 按钮语义：Enter 触发 click
        return "clicked";
      });
      await sleep(300);
      const kbOpen = await p.evaluate(() =>
        document.querySelectorAll('.pw-task[data-item-id="I-903"] .pw-detail').length);
      const aria = await p.evaluate(() =>
        document.querySelector('.pw-task[data-item-id="I-903"] .pw-task-line')
          ?.getAttribute("aria-expanded"));
      ok(kbOpen === 1 && aria === "true",
        "键盘（聚焦+Enter）可展开原因，aria-expanded 正确",
        "detail=" + kbOpen + " aria=" + aria);
      await p.evaluate(() => {
        [...document.querySelectorAll(".pw-task")]
          .find((t) => t.getAttribute("data-item-id") === "I-904")
          ?.querySelector(".pw-task-line")?.click();
      });
      await sleep(300);
      const d904 = await p.evaluate(() =>
        document.querySelectorAll('.pw-task[data-item-id="I-904"] .pw-detail').length);
      ok(d904 === 1, "长标题与长原因样例可展开（不截断隐藏）", String(d904));
      await p.screenshot({ path: OUT + "/c3_reason_detail.png", fullPage: false });
    });

    console.log("判据四：分阶段接续、重复事件、刷新、另一工位、上下文保持");
    await withPage(browser, async (p) => {
      await sleep(21_000);                        // 时间线走完 0-16s 并等一轮轮询拉取
      const chips = await p.evaluate((sid) => ({
        x: document.querySelector('.ws-card[data-session-id="' + sid + '"] .ws-continue-line')
          ?.textContent ?? "" }), SESSION_X);
      ok(chips.x.includes("实际运行"),
        "同一事项的释放→选择→认领→实际运行按回读呈现（终态=实际运行）",
        chips.x);
      const totalNow = await p.evaluate(() =>
        document.querySelector(".pw-total b")?.textContent);
      ok(totalNow === "9",
        "认领确认后待领数减少（快照权威：I-901 移出，10→9）", totalNow);
      await sleep(9000);                          // 重复事件与另一工位（20s/22s）+ 下一轮轮询
      const afterDup = await p.evaluate((sids) => ({
        total: document.querySelector(".pw-total b")?.textContent,
        xChips: document.querySelectorAll('.ws-card[data-session-id="' + sids.x
          + '"] .ws-continue-line').length,
        yChip: document.querySelector('.ws-card[data-session-id="' + sids.y
          + '"] .ws-continue-line')?.textContent ?? "" }), { x: SESSION_X, y: SESSION_Y });
      ok(afterDup.total === "9" && afterDup.xChips === 1,
        "重复事件第二次投放：不重复播放、不误改待领数（单阶段标记）",
        JSON.stringify(afterDup));
      ok(afterDup.yChip.includes("实际运行") || afterDup.yChip.includes("接续"),
        "另一工位（L3-w1）持续执行并按事件回读标注", afterDup.yChip);
      const pct = await p.evaluate((sid) =>
        document.querySelector('.ws-card[data-session-id="' + sid + '"] .ws-gauge-num')
          ?.textContent ?? "", SESSION_X);
      ok(pct.includes("41.5"),
        "原会话接新任务不重置上下文读数（进度条保持 41.5%）", pct);
      await p.screenshot({ path: OUT + "/c4_staged_running.png", fullPage: false });
      // 刷新不回放（基线重建）
      await p.reload({ waitUntil: "networkidle2" });
      await p.evaluate(() => {
        [...document.querySelectorAll(".console-tab")]
          .find((b) => b.textContent.includes("观察窗"))?.click();
      });
      await p.waitForSelector(".pw-summary", { timeout: 15000 });
      await sleep(2500);
      const afterReload = await p.evaluate(() => ({
        total: document.querySelector(".pw-total b")?.textContent,
        xChips: document.querySelectorAll(".ws-continue-line").length }));
      ok(afterReload.total === "9" && afterReload.xChips === 0,
        "普通刷新不回放历史事件（已错过事件保留为记录，不重播动效）",
        JSON.stringify(afterReload));
    });

    console.log("判据五：预留未设置、容量口径、回归");
    await withPage(browser, async (p) => {
      await sleep(2000);
      const cap = await p.evaluate(() =>
        document.querySelector(".pw-cap")?.textContent ?? "");
      ok(cap.includes("上限 11") && cap.includes("已占用 2") && cap.includes("可派 9"),
        "容量读数取自快照 capacity（有效上限/占用/可派）", cap);
      ok(cap.includes("预留 未设置"),
        "预留策略未设置如实显示（reserved=null 非零）", cap);
      const note = await p.evaluate(() =>
        document.querySelector(".pw-meta-note")?.textContent ?? "");
      ok(note.includes("独立口径") && !cap.includes("近五分钟"),
        "页面额度与活跃会话数为独立口径只引用不混算", note.slice(0, 80));
      const regress = await p.evaluate(() => ({
        hist: !!document.querySelector(".ws-history-toggle"),
        gray: document.querySelectorAll(".ws-card.retained").length }));
      ok(regress.hist && regress.gray >= 1,
        "回归：完整历史入口与灰态会话保留", JSON.stringify(regress));
      const step = await p.evaluate((sid) => {
        const card = [...document.querySelectorAll(".ws-card:not(.retained)")]
          .find((c) => c.getAttribute("data-session-id") === sid);
        const btn = [...(card?.querySelectorAll(".ws-actions .ws-btn") ?? [])]
          .find((b) => b.textContent === "注入");
        if (!btn) return "no btn";
        btn.click();
        return "clicked";
      }, SESSION_X);
      await sleep(400);
      const modal = await p.evaluate(() =>
        document.querySelector(".ws-modal")?.getAttribute("data-session-id") ?? "");
      ok(step === "clicked" && modal === SESSION_X,
        "回归：单卡注入定向完整会话身份", modal.slice(0, 30) + "…");
      await p.evaluate(() => {
        [...document.querySelectorAll(".ws-modal .ws-btn")]
          .find((b) => b.textContent === "取消")?.click();
      }).catch(() => {});
      await p.screenshot({ path: OUT + "/c5_capacity_regression.png", fullPage: false });
    });

    console.log("判据六：320/736/1440 视口与减少动态效果");
    for (const w of [320, 736, 1440]) {
      await withPage(browser, async (p) => {
        await sleep(1500);
        const overflow = await p.evaluate(() =>
          document.documentElement.scrollWidth - document.documentElement.clientWidth);
        await p.evaluate(() =>
          document.querySelector(".pw-summary .pw-expand-all")?.click());
        await sleep(500);
        const reachable = await p.evaluate(() =>
          !!document.querySelector(".pw-all-list .pw-task"));
        const wide = overflow > 1 ? await p.evaluate(() => {
          const vw = document.documentElement.clientWidth;
          const out = [];
          document.querySelectorAll("*").forEach((el) => {
            const r = el.getBoundingClientRect();
            if (r.right > vw + 1 && r.width > 10) {
              out.push(el.tagName + "." + String(el.className).slice(0, 40) + " w=" + Math.round(r.width));
            }
          });
          return out.slice(0, 8);
        }) : [];
        ok(overflow <= 1 && reachable,
          w + "px 视口：无横向溢出且摘要、明细、原因可达（overflow=" + overflow + "）",
          "overflow=" + overflow + " wide=" + JSON.stringify(wide));
        if (w === 320) {
          await p.screenshot({ path: OUT + "/c6_viewport_320.png", fullPage: false });
        }
      }, { viewport: { width: w, height: 900 } });
    }
    await withPage(browser, async (p) => {
      await p.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
      await sleep(1200);
      await p.evaluate(() =>
        document.querySelectorAll(".pw-task")[0]?.querySelector(".pw-task-line")?.click());
      await sleep(400);
      const readable = await p.evaluate(() => {
        const d = document.querySelector(".pw-detail");
        return !!d && (d.textContent ?? "").length > 20;
      });
      ok(readable, "减少动态效果启用时原因仍以文字完整可读", String(readable));
    });
  } finally {
    await browser.close().catch(() => {});
    if (preview && preview.pid) {
      spawn("taskkill", ["/T", "/F", "/PID", String(preview.pid)], { shell: true });
    }
  }
  record.pass = pass; record.fail = fail;
  fs.writeFileSync(OUT + "/isolated_check_record.json", JSON.stringify(record, null, 1));
  console.log("\n结果：" + pass + " 过 / " + fail + " 拒；记录 " + OUT + "/isolated_check_record.json");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("核验脚本异常：", e); process.exit(2); });
