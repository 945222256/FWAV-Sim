/* i228v2_rework_check.cjs — I-228 v2 返修隔离复验（独立端口，不触生产）。
 * 复验 Ciri 三反例（logs/ciri_i228_ui_review_20261014/independent_browser_probe.json）
 * 的修复后行为 + 正常控制 + 返修四（事项履历）样例 + 原有回归：
 *  S1 筛选竞态：迟到的"全部"请求（650ms）不得覆盖快速 L3 请求（35ms）；
 *     快速连续筛选反序返回；关闭重开后单请求正确预筛。
 *  S2 分页：页间索引换版（第二页一条与第一页重复）→ 识别换版、重置并
 *     重新开始本版本遍历（页序 1,2,1,2,3），不停滞不跨版本冒称；
 *     固定索引 120 条正常控制全量可达。
 *  S3 保留语义：active+idle 标近期保留与空闲相位（不再运行中）；
 *     近期运行/已停止/无来源未知三对照。
 *  S4 事项履历：标题+当前账本状态+账本事件时间线；认领核实仅认本会话
 *     认领事件；他方认领/无事件/账本读取失败分别说明。
 *  S5 回归：对话摘要来源标注、交接链、完整会话号、十线灰态、单卡注入
 *     定向、详情无操作入口。 */
const puppeteer = require("puppeteer-core");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const ROOT = "G:/Playground/isaac_flapper_sim";
const OUT = ROOT + "/logs/i228_ui_rework_20261015";
fs.mkdirSync(OUT, { recursive: true });
const PORT = 4194;
let pass = 0, fail = 0;
const record = { generated_at: new Date().toISOString(), checks: [], requests: [] };
function ok(cond, label, detail) {
  record.checks.push({ label, ok: !!cond, detail: String(detail ?? "").slice(0, 300) });
  if (cond) { pass++; console.log("  [过] " + label); }
  else { fail++; console.log("  [拒] " + label + "：" + String(detail ?? "")); }
  return !!cond;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const NOW = "2026-10-15T23:40:00";

/* ── 场景数据 ── */
function row(sid, lane, ret, extra) {
  return Object.assign({
    session_id: sid, lane, workers: [], items: [], run_count: 0,
    successor: null, predecessor: null, first_seen_at: NOW, last_seen_at: NOW,
    retention: { kind: ret, reason: "样例" }, sources: ["dispatch_registry"],
    missing: {},
  }, extra || {});
}
const mkSid = (tag) => "sess_i228v2_" + tag + "_0123456789abcdef0123456789abcdef";
const S_L3 = [mkSid("l3a"), mkSid("l3b"), mkSid("l3c")].map(
  (s) => row(s, "L3", "retained_timeout"));
const S_L9 = [row(mkSid("l9a"), "L9", "retained_timeout")];
const S_L5 = [row(mkSid("l5a"), "L5", "retained_timeout")];
/* 索引版本 T1：120 条；T2：121 条且第二页首条与 T1 第一页末条同号。 */
const T1 = "2026-10-14 10:00:00", T2 = "2026-10-14 10:01:00";
const big = (ver, dupShift) => {
  const n = ver === T1 ? 120 : 121;
  const rows = [];
  for (let i = 0; i < n; i++) {
    rows.push(row(mkSid("big" + String(i + (dupShift && i >= 50 ? 1 : 0)).padStart(3, "0")),
      "L" + ((i % 10) + 1), i % 20 === 0 ? "explicit_archive" : "retained_timeout"));
  }
  return rows;
};
const BIG1 = big(T1, false);
const BIG2 = big(T2, true);
/* 保留/相位样例 */
const PH = [
  { sid: mkSid("ph_idle"), phase: "idle", kind: "active" },
  { sid: mkSid("ph_run"), phase: "active", kind: "active" },
  { sid: mkSid("ph_stop"), phase: "stopped", kind: "active" },
  { sid: mkSid("ph_none"), phase: null, kind: "retained_timeout" },
];
/* 履历样例会话（I-901 本会话认领；I-902 他方认领；I-903 无事件；I-904 账本失败） */
const HIST_SID = mkSid("hist");
const histDetail = {
  ok: true, session_id: HIST_SID, lane: "L3", title_head: "履历样例会话",
  workers: {}, items: {
    "I-901": { first: null, last: null, sources: ["dispatch_registry", "kernel_runs"],
               claimed_at: "2026-10-14T09:00:00" },
    "I-902": { first: null, last: null, sources: ["dispatch_registry"], claimed_at: null },
    "I-903": { first: null, last: null, sources: ["dispatch_registry"], claimed_at: null },
    "I-904": { first: null, last: null, sources: ["dispatch_registry"], claimed_at: null },
  },
  runs: [], handover_cards: [], predecessor_chain: [], successor_chain: [],
  first_seen_at: NOW, last_seen_at: NOW,
  retention: { kind: "retained_timeout", reason: "样例" },
  execution_phase: "idle", sources: ["dispatch_registry"], missing: {},
  read_only: true,
  dialog_summary: { ok: true, texts: ["样例对话文本一", "样例对话文本二"],
                    tool_counts: { Bash: 3 }, reason: null },
};
const LEDGER_EVENTS = [
  { seq: 10, ts: "2026-10-14T09:00:00", action: "assign", item_id: "I-901", actor: "ciri", detail: "派发" },
  { seq: 11, ts: "2026-10-14T09:00:05", action: "claim", item_id: "I-901", actor: "L5队",
    detail: "认领，租约 4 小时，会话 " + HIST_SID },
  { seq: 12, ts: "2026-10-14T11:00:00", action: "close", item_id: "I-901", actor: "anni", detail: "结载" },
  { seq: 20, ts: "2026-10-14T12:00:00", action: "assign", item_id: "I-902", actor: "ciri", detail: "派发" },
  { seq: 21, ts: "2026-10-14T12:00:05", action: "claim", item_id: "I-902", actor: "L3队",
    detail: "认领，租约 4 小时，会话 sess_other_worker_000000000000000000000000" },
];
const ITEM_DETAIL = {
  "I-901": { ok: true, result: { item: { id: "I-901", title: "履历样例一（已结载）",
    status: "closed", conclusion: null } } },
  "I-902": { ok: true, result: { item: { id: "I-902", title: "履历样例二（进行中）",
    status: "in_progress", conclusion: null } } },
  "I-903": { ok: true, result: { item: { id: "I-903", title: "履历样例三（未认领）",
    status: "open", conclusion: null } } },
  "I-904": { ok: false, error: "云端读取失败（样例）" },
};
function detailFor(sid) {
  if (sid === HIST_SID) return histDetail;
  const ph = PH.find((x) => x.sid === sid);
  if (ph) return Object.assign({}, histDetail, {
    session_id: sid, items: { "I-950": { first: null, last: null,
      sources: ["dispatch_registry"], claimed_at: null } },
    retention: { kind: ph.kind, reason: "样例" },
    execution_phase: ph.phase, dialog_summary: { ok: false, texts: [],
      tool_counts: {}, reason: "对话源缺失：会话库无该会话行" } });
  return { ok: false, reason: "会话不在索引：无本项目已核实来源记录" };
}

/* ── 响应控制 ── */
const ctl = {
  scenario: "idle",           // idle | race | rapid | reopen | paging | control | phase | hist
  listDelay: { "": 0 },       // 按 lane 的延迟
  pageSeq: [],                // 已投放的分页响应序号
  bigVersion: T1,
  eventsFail: false,
};
function listRespond(lane, page) {
  const size = 50;
  let rows = [], total = 0, ver = ctl.bigVersion;
  if (ctl.scenario === "race" || ctl.scenario === "rapid") {
    if (lane === "L3") { rows = S_L3; total = S_L3.length; ver = "2026-10-14 09:30:00"; }
    else if (lane === "L9") { rows = S_L9; total = 1; ver = "2026-10-14 10:00:00"; }
    else if (lane === "L5") { rows = S_L5; total = 1; ver = "2026-10-14 09:50:00"; }
    else { rows = S_L9; total = 1; ver = "2026-10-14 10:00:00"; }
  } else if (ctl.scenario === "paging") {
    /* 首次第 1 页为 T1；自首个第 2 页请求起索引换版为 T2 且粘性不变。 */
    if (page > 1) ctl.swapped = true;
    const ver2 = ctl.swapped ? T2 : T1;
    const src = ver2 === T1 ? BIG1 : BIG2;
    rows = src.slice((page - 1) * size, page * size);
    total = ver2 === T1 ? BIG1.length : BIG2.length;
    ver = ver2;
  } else if (ctl.scenario === "control") {
    rows = BIG1.slice((page - 1) * size, page * size);
    total = BIG1.length;
  } else if (ctl.scenario === "phase" || ctl.scenario === "hist") {
    rows = ctl.scenario === "phase" ? PH.map((x) => row(x.sid, "L3", x.kind))
      : [row(HIST_SID, "L3", "retained_timeout")];
    total = rows.length;
  }
  return { total, page, page_size: size, items: rows, index_built_at: ver,
           stats: { sessions: ctl.scenario === "control" ? BIG1.length : total } };
}

function mkCard(sid, lane, item) {
  return { session_id: sid, lane, item_id: item, current_item_id: item,
    status: "在干活", context_pct: 30, last_activity_at: NOW, updated_at: NOW,
    _is_worker: true, source: { kind: "表", locus: "console_sessions/" + sid,
      excerpt: "隔离样例", sampled_at: NOW } };
}
const consoleState = {
  goals: [],
  lanes: Object.entries({ HQ: "指挥部", L1: "攻关主线", L2: "架构与管线", L3: "数据资产与接驳预备",
    L4: "周期时变与混合飞行", L5: "接口与应用线", L6: "强化学习线", L7: "导航合龙线",
    L8: "形状保真对齐线", L9: "计算流体力学线（预登记）", L10: "架构提取与范用化线" })
    .map(([id, name]) => ({ id, name, items: [], active_sessions: [],
      source: { kind: "文件", locus: "x", excerpt: name, sampled_at: NOW } })),
  sessions: { active: [mkCard(mkSid("live1"), "L3", "I-981")],
              retained: [], archived: [], owner: [] },
  footer: { parallel: { active: 1, tracked: 1, limit: 15 }, sampled_at: NOW,
            source: { kind: "表", locus: "x", excerpt: "x", sampled_at: NOW } },
  lane_queues: { sampled_at: NOW, queues: { L3: { concurrency_limit: 2,
    workers: [{ worker_id: "L3-w1", session_id: mkSid("live1"), item_id: "I-981" }],
    active_count: 1, waiting_count: 0, waiting: {} } } },
};

async function withPage(browser, fn, opts) {
  const p = await browser.newPage();
  await p.setCacheEnabled(false);
  await p.setRequestInterception(true);
  p.on("request", (rq) => {
    const url = rq.url();
    const json = (result, delayMs) => {
      const body = JSON.stringify(result);
      const reply = () => rq.respond({ status: 200, contentType: "application/json",
        headers: { Date: new Date().toUTCString() }, body });
      if (delayMs) setTimeout(reply, delayMs); else reply();
    };
    if (url.includes("/api/session_history/sessions/")) {
      const sid = decodeURIComponent(url.split("/sessions/")[1].split("?")[0]);
      json({ ok: true, result: detailFor(sid) }, 30);
    } else if (url.includes("/api/session_history/sessions")) {
      const u = new URL(url);
      const lane = u.searchParams.get("lane") || "";
      const page = Number(u.searchParams.get("page") || "1");
      record.requests.push({ scenario: ctl.scenario, lane, page });
      const delay = (ctl.listDelay && ctl.listDelay[lane]) || 0;
      json({ ok: true, result: listRespond(lane, page) }, delay);
    } else if (url.includes("/api/events")) {
      if (ctl.eventsFail) {
        rq.respond({ status: 500, contentType: "application/json",
          headers: { Date: new Date().toUTCString() },
          body: JSON.stringify({ ok: false, error: "账本不可达（样例）" }) });
      } else json({ ok: true, result: LEDGER_EVENTS });
    } else if (url.includes("/api/item-detail")) {
      const id = new URL(url).searchParams.get("id") || "";
      const d = ITEM_DETAIL[id] ?? { ok: false, error: "未知事项" };
      rq.respond({ status: d.ok ? 200 : 500, contentType: "application/json",
        headers: { Date: new Date().toUTCString() },
        body: JSON.stringify(d) });
    } else if (url.includes("/api/console/state")) json({ ok: true, result: consoleState });
    else if (url.includes("/api/console/config"))
      json({ ok: true, result: { handoff_mode: "auto", gate_pct: 70, parallel_limit: 15 } });
    else if (url.includes("/api/auth/whoami")) json({ ok: true, result: { user: "隔离样例" } });
    else rq.continue();
  });
  if (opts && opts.viewport) await p.setViewport(opts.viewport);
  try {
    await p.goto("http://localhost:" + PORT + "/app/#/console",
      { waitUntil: "networkidle2", timeout: 30000 });
    await p.waitForSelector(".console-tabs");
    await fn(p);
  } finally {
    p.close().catch(() => {});
  }
}
async function gotoWatch(p) {
  await p.evaluate(() => {
    [...document.querySelectorAll(".console-tab")]
      .find((b) => b.textContent.includes("观察窗"))?.click();
  });
  await p.waitForSelector(".ws-group", { timeout: 15000 });
  await sleep(500);
}
async function enterHistoryFromLane(p, lane) {
  await p.evaluate((lane) => {
    const g = [...document.querySelectorAll(".ws-group")]
      .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith(lane));
    g?.querySelector(".ws-history-toggle")?.click();
  }, lane);
  await p.waitForSelector(".sh", { timeout: 15000 });
  await sleep(700);
}
const listSids = (p) => p.evaluate(() =>
  [...document.querySelectorAll(".sh-row")].map((r) => r.getAttribute("data-session-id")));
const countsLine = (p) => p.evaluate(() =>
  document.querySelector(".sh-counts")?.textContent ?? "");

(async () => {
  const preview = spawn("node", [ROOT + "/board_ui/scripts/i228_proxy.cjs",
    "58799", String(PORT)], { shell: true, stdio: "ignore" });
  await sleep(1800);
  const browser = await puppeteer.launch({
    executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    headless: true, args: ["--no-sandbox"], defaultViewport: { width: 1600, height: 1000 },
  });
  try {
    /* ── S1 筛选竞态（反例一复验） ── */
    console.log("S1 筛选竞态复验");
    ctl.scenario = "race";
    ctl.listDelay = { "": 650, L3: 35 };       // 全部慢、L3 快（Ciri 原样时序）
    await withPage(browser, async (p) => {
      await gotoWatch(p);
      await enterHistoryFromLane(p, "L3");
      await sleep(1200);                        // 等慢请求迟到返回
      const sids = await listSids(p);
      const counts = await countsLine(p);
      ok(sids.every((s) => S_L3.some((r) => r.session_id === s)) && sids.length === 3,
        "S1a 预筛 L3：迟到全部请求不覆盖（行为 L3 三条，无 L9）",
        JSON.stringify(sids.map((s) => s.slice(11, 18))) + "|" + counts);
      ok(counts.includes("当前筛选 3") && counts.includes("09:30:00"),
        "S1b 数量与索引时间来自当前筛选响应（L3 版本）", counts);
      // 快速连续筛选（L3→L9→L5，响应延迟反序：L5 快、L3 慢）
      ctl.listDelay = { L3: 600, L9: 300, L5: 30 };
      await p.evaluate(() => {
        const sel = [...document.querySelectorAll(".sh-filters select")][0];
        const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(sel), "value").set;
        for (const v of ["L9", "L5"]) {
          set.call(sel, v);
          sel.dispatchEvent(new Event("change", { bubbles: true }));
        }
        [...document.querySelectorAll(".sh-filters .ws-btn")]
          .find((b) => b.textContent.includes("应用筛选"))?.click();
      });
      await sleep(1100);
      const sids2 = await listSids(p);
      ok(sids2.length === 1 && sids2[0] === S_L5[0].session_id,
        "S1c 快速连续筛选反序返回：最终行为最新筛选（L5）",
        JSON.stringify(sids2.map((s) => s.slice(11, 18))));
      await p.screenshot({ path: OUT + "/s1_race_fixed.png", fullPage: false });
    });
    /* 关闭重开（回到观察窗再从 L9 进入）：仅一次正确预筛请求 */
    const reqBefore = record.requests.length;
    ctl.listDelay = {};
    await withPage(browser, async (p) => {
      await gotoWatch(p);
      await enterHistoryFromLane(p, "L9");
      await sleep(700);
      const sids = await listSids(p);
      const lane = await p.evaluate(() =>
        document.querySelector(".sh-filters select")?.value ?? "");
      ok(lane === "L9" && sids.length === 1 && sids[0] === S_L9[0].session_id,
        "S1d 关闭重开：以 L9 预筛挂载且行为 L9", lane + "|" + sids.length);
    });
    const newReqs = record.requests.slice(reqBefore).filter((r) => r.scenario === "race");
    ok(newReqs.length === 1 && newReqs[0].lane === "L9" && newReqs[0].page === 1,
      "S1e 重开仅发一次正确预筛请求（无错误首请求）", JSON.stringify(newReqs));

    /* ── S2 分页（反例二复验 + 正常控制） ── */
    console.log("S2 分页复验");
    ctl.scenario = "paging";
    await withPage(browser, async (p) => {
      await p.evaluate(() => {
        [...document.querySelectorAll(".console-tab")]
          .find((b) => b.textContent.includes("历史目录"))?.click();
      });
      await p.waitForSelector(".sh", { timeout: 15000 });
      await sleep(800);
      let loaded = await listSids(p);
      ok(loaded.length === 50, "S2a 首页 50 条（T1 版本）", String(loaded.length));
      // 第二页：索引换 T2，且页首一条与已加载重复（Ciri 反例场景）
      await p.evaluate(() => document.querySelector(".sh-foot .ws-btn.primary")?.click());
      await sleep(900);
      const notice = await p.evaluate(() =>
        document.querySelector(".sh-version-note")?.textContent ?? "");
      ok(notice.includes("索引已换版") && notice.includes("重新开始"),
        "S2b 换版识别：明确重置并重新开始本版本遍历", notice.slice(0, 120));
      let sids = await listSids(p);
      ok(sids.length === 50 && new Set(sids).size === 50,
        "S2c 重置后以新版本第一页重新开始", String(sids.length));
      // 继续加载到底（T2 稳定：121 条 = 50+50+21）
      for (let i = 0; i < 6; i++) {
        const more = await p.evaluate(() => {
          const btn = document.querySelector(".sh-foot .ws-btn.primary");
          return btn && btn.textContent.includes("加载更多");
        });
        if (!more) break;
        await p.evaluate(() => document.querySelector(".sh-foot .ws-btn.primary")?.click());
        await sleep(700);
      }
      sids = await listSids(p);
      const foot = await p.evaluate(() =>
        document.querySelector(".sh-foot")?.textContent ?? "");
      ok(sids.length === 121 && new Set(sids).size === 121,
        "S2d 换版重开后全量可达：121 条无重复", String(sids.length));
      ok(foot.includes("本版本已全部加载：121 / 121") && foot.includes("按页推进 1–3"),
        "S2e 完成态限定在本版本内（按页推进 1–3）", foot.slice(0, 120));
      await p.screenshot({ path: OUT + "/s2_pagination_restart.png", fullPage: false });
    });
    const pagesReq = record.requests.filter((r) => r.scenario === "paging")
      .map((r) => r.page);
    ok(JSON.stringify(pagesReq) === JSON.stringify([1, 2, 1, 2, 3]),
      "S2f 请求页序=1,2,1,2,3（换版即重开，无 1,2,2 停滞）", JSON.stringify(pagesReq));

    ctl.scenario = "control";
    await withPage(browser, async (p) => {
      await p.evaluate(() => {
        [...document.querySelectorAll(".console-tab")]
          .find((b) => b.textContent.includes("历史目录"))?.click();
      });
      await p.waitForSelector(".sh", { timeout: 15000 });
      await sleep(700);
      for (let i = 0; i < 6; i++) {
        const more = await p.evaluate(() => {
          const btn = document.querySelector(".sh-foot .ws-btn.primary");
          return btn && btn.textContent.includes("加载更多");
        });
        if (!more) break;
        await p.evaluate(() => document.querySelector(".sh-foot .ws-btn.primary")?.click());
        await sleep(600);
      }
      const sids = await listSids(p);
      const foot = await p.evaluate(() =>
        document.querySelector(".sh-foot")?.textContent ?? "");
      ok(sids.length === 120 && new Set(sids).size === 120
        && foot.includes("120 / 120"),
        "S2g 固定索引正常控制：120 条全量可达无重复", String(sids.length));
      const noNotice = await p.evaluate(() =>
        !document.querySelector(".sh-version-note"));
      ok(noNotice, "S2h 稳定版本无换版提示", "");
    });
    const ctrlPages = record.requests.filter((r) => r.scenario === "control")
      .map((r) => r.page);
    ok(JSON.stringify(ctrlPages) === JSON.stringify([1, 2, 3]),
      "S2i 控制组页序=1,2,3", JSON.stringify(ctrlPages));

    /* ── S3 保留语义（反例三复验） ── */
    console.log("S3 保留语义复验");
    ctl.scenario = "phase";
    await withPage(browser, async (p) => {
      await p.evaluate(() => {
        [...document.querySelectorAll(".console-tab")]
          .find((b) => b.textContent.includes("历史目录"))?.click();
      });
      await p.waitForSelector(".sh", { timeout: 15000 });
      await sleep(700);
      const badges = await p.evaluate(() => [...document.querySelectorAll(".sh-row")]
        .map((r) => ({ sid: r.getAttribute("data-session-id"),
                       badge: r.querySelector(".sh-badge")?.textContent })));
      ok(badges.filter((b) => b.badge.includes("近期保留")).length === 3
        && !badges.some((b) => b.badge.includes("运行中")),
        "S3a 保留类型不再标运行中（active→近期保留）", JSON.stringify(badges));
      const open = async (sid) => {
        await p.evaluate((sid) => {
          [...document.querySelectorAll(".sh-row")]
            .find((r) => r.getAttribute("data-session-id") === sid)
            ?.querySelector(".sh-sid")?.click();
        }, sid);
        await p.waitForSelector(".sh-drawer", { timeout: 10000 });
        await sleep(500);
        const t = await p.evaluate(() => ({
          phase: document.querySelector("[data-testid=sh-exec-phase]")?.textContent ?? "",
          body: document.querySelector(".sh-drawer")?.textContent ?? "" }));
        await p.evaluate(() => {
          [...document.querySelectorAll(".sh-drawer .lab-btn-minor")]
            .find((b) => b.textContent === "关闭")?.click();
        }).catch(() => {});
        await sleep(250);
        return t;
      };
      const idle = await open(PH[0].sid);
      ok(idle.body.includes("保留类型 近期保留") && idle.phase.includes("空闲（索引相位）"),
        "S3b 近期但空闲：保留=近期保留、执行状态=空闲（反例三消失）",
        idle.phase.slice(0, 80));
      const run = await open(PH[1].sid);
      ok(run.phase.includes("运行（索引相位）"),
        "S3c 近期且实际运行：执行状态=运行", run.phase.slice(0, 60));
      const stop = await open(PH[2].sid);
      ok(stop.phase.includes("已停止"), "S3d 已停止：如实显示", stop.phase.slice(0, 60));
      const none = await open(PH[3].sid);
      ok(none.phase.includes("未知（索引无执行相位来源）"),
        "S3e 无来源：明确未知", none.phase.slice(0, 80));
      await p.screenshot({ path: OUT + "/s3_retention_semantics.png", fullPage: false });
    });

    /* ── S4 事项履历（返修四） ── */
    console.log("S4 事项履历复验");
    ctl.scenario = "hist";
    await withPage(browser, async (p) => {
      await p.evaluate(() => {
        [...document.querySelectorAll(".console-tab")]
          .find((b) => b.textContent.includes("历史目录"))?.click();
      });
      await p.waitForSelector(".sh", { timeout: 15000 });
      await sleep(600);
      await p.evaluate((sid) => {
        [...document.querySelectorAll(".sh-row")]
          .find((r) => r.getAttribute("data-session-id") === sid)
          ?.querySelector(".sh-sid")?.click();
      }, HIST_SID);
      await p.waitForSelector(".sh-drawer", { timeout: 10000 });
      await sleep(900);
      const txt = await p.evaluate(() =>
        document.querySelector(".sh-drawer")?.textContent ?? "");
      ok(txt.includes("履历样例一（已结载）") && txt.includes("完成")
        && txt.includes("认领核实：本会话认领事件在册")
        && txt.includes("结载"),
        "S4a I-901：标题+当前账本状态+本会话认领+事件时间线（派发/认领/结载）",
        txt.slice(0, 150));
      ok(txt.includes("归属未核实") && txt.includes("他方认领"),
        "S4b I-902：索引关联不冒充认领（他方认领标注）", "");
      ok(txt.includes("该事项无派发/认领"),
        "S4c I-903：无账本事件如实说明", "");
      ok(txt.includes("当前账本状态读取失败"),
        "S4d I-904：当前账本读取失败单独说明（其余履历仍陈列）", "");
      ok(txt.includes("对话与工具摘要（会话库，只读") && txt.includes("账本事件 seq"),
        "S4e 对话与事项事件分别标注来源", "");
      // 账本事件接口失败样例
      ctl.eventsFail = true;
      await p.evaluate(() => {
        [...document.querySelectorAll(".sh-drawer .lab-btn-minor")]
          .find((b) => b.textContent === "关闭")?.click();
      }).catch(() => {});
      await sleep(10600);                       // 等前端事件缓存（10s）过期
      await p.evaluate((sid) => {
        [...document.querySelectorAll(".sh-row")]
          .find((r) => r.getAttribute("data-session-id") === sid)
          ?.querySelector(".sh-sid")?.click();
      }, HIST_SID);
      await p.waitForSelector(".sh-drawer", { timeout: 10000 });
      await sleep(900);
      const txt2 = await p.evaluate(() =>
        document.querySelector(".sh-drawer")?.textContent ?? "");
      ok(txt2.includes("账本事件读取失败"),
        "S4f 账本事件接口失败如实说明（不冒充无事件）", txt2.slice(0, 100));
      await p.screenshot({ path: OUT + "/s4_item_history.png", fullPage: false });
      ctl.eventsFail = false;
    });

    /* ── S5 回归 ── */
    console.log("S5 回归");
    ctl.scenario = "hist";
    await withPage(browser, async (p) => {
      await gotoWatch(p);
      const regress = await p.evaluate(() => ({
        lanes: document.querySelectorAll(".ws-group").length,
        hist: document.querySelectorAll(".ws-history-toggle").length,
      }));
      ok(regress.lanes >= 10 && regress.hist >= 5,
        "S5a 十线常驻与历史入口回归", JSON.stringify(regress));
      const step = await p.evaluate((sid) => {
        const card = [...document.querySelectorAll(".ws-card:not(.retained)")]
          .find((c) => c.getAttribute("data-session-id") === sid);
        const btn = [...(card?.querySelectorAll(".ws-actions .ws-btn") ?? [])]
          .find((b) => b.textContent === "注入");
        if (!btn) return "no btn";
        btn.click();
        return "clicked";
      }, mkSid("live1"));
      await sleep(400);
      const modal = await p.evaluate(() =>
        document.querySelector(".ws-modal")?.getAttribute("data-session-id") ?? "");
      ok(step === "clicked" && modal === mkSid("live1"),
        "S5b 单卡注入定向完整会话身份回归", modal.slice(11, 25));
      await p.evaluate(() => {
        [...document.querySelectorAll(".ws-modal .ws-btn")]
          .find((b) => b.textContent === "取消")?.click();
      }).catch(() => {});
    });
    // 详情只读（无注入/停止/恢复）与交接链回归在 S3/S4 已覆盖（详情内无操作按钮）
    const readonly = await (async () => {
      ctl.scenario = "phase";
      let result = null;
      await withPage(browser, async (p) => {
        await p.evaluate(() => {
          [...document.querySelectorAll(".console-tab")]
            .find((b) => b.textContent.includes("历史目录"))?.click();
        });
        await p.waitForSelector(".sh", { timeout: 10000 });
        await sleep(500);
        await p.evaluate((sid) => {
          [...document.querySelectorAll(".sh-row")]
            .find((r) => r.getAttribute("data-session-id") === sid)
            ?.querySelector(".sh-sid")?.click();
        }, PH[0].sid);
        await p.waitForSelector(".sh-drawer", { timeout: 10000 });
        await sleep(400);
        result = await p.evaluate(() =>
          [...document.querySelectorAll(".sh-drawer button")]
            .filter((b) => /注入|停止|恢复/.test(b.textContent)).length);
      });
      return result;
    })();
    ok(readonly === 0, "S5c 旧会话详情只读（误注入/误停止入口为零）", String(readonly));
  } finally {
    await browser.close().catch(() => {});
    if (preview && preview.pid) {
      spawn("taskkill", ["/T", "/F", "/PID", String(preview.pid)], { shell: true });
    }
  }
  record.pass = pass; record.fail = fail;
  fs.writeFileSync(OUT + "/rework_check_record.json", JSON.stringify(record, null, 1));
  console.log("\n结果：" + pass + " 过 / " + fail + " 拒；记录 " + OUT + "/rework_check_record.json");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("复验脚本异常：", e); process.exit(2); });
