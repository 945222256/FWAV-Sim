/* i228_check.cjs — I-228 会话历史目录隔离核验（独立端口，不触生产）。
 * 数据面：私有 session_history 只读查询服务（真实服务代码，读私有
 * 夹具索引）+ 页面构建静态代理；观察窗状态用拦截注入。
 * 判据一：每线完整历史入口（观察窗按钮）、L3 列表与夹具逐项一致、
 *         十线常驻与灰态保留回归；
 * 判据二：工位/事项/类型/时间筛选、加载更多、完整/筛选/已加载计数
 *         一致、跨页无重无漏、保留不冒充显式归档；
 * 判据三：旧会话号可点击，对话/工具摘要、历次事项、交接卡与链；
 *         四类缺失样例（已删除/无对话源/无认领/接口失败）各显示原因；
 * 判据四：三次交接链 A→B→C→D 双向可查、交接后新会话运行中、
 *         详情只读无注入停止入口；
 * 判据五：卡片实况对账行（采样时刻+待核对后缀）、单卡注入定向回归。 */
const puppeteer = require("puppeteer-core");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const ROOT = "G:/Playground/isaac_flapper_sim";
const OUT = ROOT + "/logs/i228_session_history_ui_20261015";
const FIX = OUT + "/fixture_index";
const PORT = 4190;
const HISTORY_PORT = 58795;
let pass = 0, fail = 0;
const record = { generated_at: new Date().toISOString(), checks: [] };
const META = JSON.parse(fs.readFileSync(OUT + "/fixture_meta.json", "utf8"));
function ok(cond, label, detail) {
  record.checks.push({ label, ok: !!cond, detail: String(detail ?? "").slice(0, 300) });
  if (cond) { pass++; console.log("  [过] " + label); }
  else { fail++; console.log("  [拒] " + label + "：" + String(detail ?? "")); }
  return !!cond;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const NOW = "2026-10-15T21:05:00";

function mkCard(sid, lane, item, status, extra) {
  return Object.assign({ session_id: sid, lane, item_id: item, current_item_id: item,
    status, context_pct: 20, last_activity_at: NOW, updated_at: NOW,
    source: { kind: "表", locus: "console_sessions/" + sid,
              excerpt: "隔离样例", sampled_at: NOW } }, extra || {});
}
function lanesSkeleton() {
  const names = { HQ: "指挥部", L1: "攻关主线", L2: "架构与管线", L3: "数据资产与接驳预备",
    L4: "周期时变与混合飞行", L5: "接口与应用线", L6: "强化学习线", L7: "导航合龙线",
    L8: "形状保真对齐线", L9: "计算流体力学线（预登记）", L10: "架构提取与范用化线" };
  return Object.entries(names).map(([id, name]) => ({ id, name, items: [], active_sessions: [],
    source: { kind: "文件", locus: "items_registry/lanes.json#" + id, excerpt: name, sampled_at: NOW } }));
}
function stateWith(active, retained) {
  return { goals: [], lanes: lanesSkeleton(),
    sessions: { active, retained: retained || [], archived: [], owner: [] },
    footer: { parallel: { active: active.length, tracked: active.length, limit: 15 },
              sampled_at: NOW, source: { kind: "表", locus: "x", excerpt: "x", sampled_at: NOW } },
    lane_queues: { sampled_at: NOW, queues: { L3: { concurrency_limit: 2,
      workers: [], active_count: 0, waiting_count: 0, waiting: {} } } } };
}

async function withState(browser, stateBody, fn) {
  const p = await browser.newPage();
  await p.setCacheEnabled(false);
  await p.setRequestInterception(true);
  p.on("request", (rq) => {
    const url = rq.url();
    if (url.includes("/api/session_history/")) { rq.continue(); return; }  // → 代理 → 私有查询服务
    const reply = (result) => rq.respond({ status: 200, contentType: "application/json",
      headers: { Date: new Date().toUTCString() },
      body: JSON.stringify({ ok: true, result }) });
    if (url.includes("/api/console/state")) reply(stateBody);
    else if (url.includes("/api/console/config"))
      reply({ handoff_mode: "auto", gate_pct: 70, parallel_limit: 15 });
    else if (url.includes("/api/events")) reply([]);
    else if (url.includes("/api/auth/whoami")) reply({ user: "隔离样例" });
    else rq.continue();
  });
  try {
    await p.goto("http://localhost:" + PORT + "/app/#/console", { waitUntil: "networkidle2", timeout: 30000 });
    await p.waitForSelector(".console-tabs");
    return await fn(p);
  } finally {
    p.close().catch(() => {});
  }
}
async function openHistoryTab(p, lane) {
  await p.evaluate((lane) => {
    const btn = [...document.querySelectorAll(".console-tab")]
      .find((b) => b.textContent.includes("历史目录"));
    if (btn) btn.click();
  }, lane);
  await p.waitForSelector(".sh", { timeout: 15000 });
  await sleep(600);
}
async function applyFilter(p) {
  await p.evaluate(() => {
    const btn = [...document.querySelectorAll(".sh-filters .ws-btn.primary")]
      .find((b) => b.textContent.includes("应用筛选"));
    if (btn) btn.click();
  });
  await sleep(700);
}
async function loadMore(p) {
  await p.evaluate(() => {
    const btn = document.querySelector(".sh-foot .ws-btn.primary");
    if (btn && btn.textContent.includes("加载更多")) btn.click();
  });
  await sleep(900);
}
const listSids = (p) => p.evaluate(() =>
  [...document.querySelectorAll(".sh-row")].map((r) => r.getAttribute("data-session-id")));
const countsLine = (p) => p.evaluate(() =>
  document.querySelector(".sh-counts")?.textContent ?? "");

(async () => {
  // 私有查询服务（真实 session_history_server.py 读私有夹具索引）
  const hist = spawn("python", [ROOT + "/lp_sync/session_history_server.py"], {
    cwd: ROOT + "/lp_sync", shell: true, stdio: "ignore",
    env: { ...process.env, LP_SESSION_HISTORY_PORT: String(HISTORY_PORT),
           LP_SESSION_HISTORY_DIR: FIX } });
  const preview = spawn("node", [path.join(ROOT, "board_ui/scripts/i228_proxy.cjs"),
    String(HISTORY_PORT), String(PORT)], { shell: true, stdio: "ignore" });
  await sleep(3000);
  const browser = await puppeteer.launch({
    executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    headless: true, args: ["--no-sandbox"], defaultViewport: { width: 1600, height: 1000 },
  });
  let hist2 = null;
  try {
    /* ── 判据一：观察窗入口 + 线列表逐项一致 + 十线灰态回归 ── */
    console.log("判据一：完整历史入口与线列表一致");
    await withState(browser, stateWith([
      mkCard("sess_i228_live_w1_0001", "L3", "I-981", "在干活（长工具 100 秒：最早在途 tool 已超窗）"),
      mkCard("sess_i228_live_w2_0002", "L3", "I-982", "未知（待核对：会话库本周期不可读）"),
    ], [mkCard("sess_i228_gray_0003", "L3", "I-983", "空闲")]), async (p) => {
      await p.evaluate(() => {
        const btn = [...document.querySelectorAll(".console-tab")]
          .find((b) => b.textContent.includes("观察窗"));
        if (btn) btn.click();
      });
      await p.waitForSelector(".ws-group", { timeout: 15000 });
      const groups = await p.evaluate(() => [...document.querySelectorAll(".ws-group")]
        .map((g) => g.querySelector(".ws-group-lane")?.textContent?.slice(0, 3)));
      ok(groups.filter(Boolean).length >= 10, "十条业务线常驻（判据一回归）",
        JSON.stringify(groups));
      const gray = await p.evaluate(() => document.querySelectorAll(".ws-card.retained").length);
      ok(gray >= 1, "灰态保留卡在页（判据一回归）", String(gray));
      await p.screenshot({ path: OUT + "/c1_watch_entry.png", fullPage: false });
      // 从 L3 组头点开完整历史
      const clicked = await p.evaluate(() => {
        const g = [...document.querySelectorAll(".ws-group")]
          .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith("L3"));
        const btn = g?.querySelector(".ws-history-toggle");
        if (!btn) return false;
        btn.click();
        return true;
      });
      ok(clicked, "L3 组头存在完整历史入口并可点击", "");
      await p.waitForSelector(".sh", { timeout: 15000 });
      await sleep(800);
      const laneVal = await p.evaluate(() =>
        document.querySelector(".sh-filters select")?.value ?? "");
      ok(laneVal === "L3", "入口预筛业务线 L3", laneVal);
      const sids = await listSids(p);
      const errBanner = await p.evaluate(() =>
        document.querySelector(".sh-error")?.textContent ?? "");
      ok(sids.length === META.l3_sids.length
        && JSON.stringify([...sids].sort()) === JSON.stringify(META.l3_sids),
        "L3 历史列表与夹具索引逐项一致（" + sids.length + " 条，含交接与双工位旧会话）",
        "page=" + sids.length + " fixture=" + META.l3_sids.length + " err=" + errBanner.slice(0, 80));
      await p.screenshot({ path: OUT + "/c1_l3_list.png", fullPage: false });

      /* ── 判据二：筛选与分页 ── */
      console.log("判据二：筛选、加载更多与计数一致");
      await p.evaluate(() => {
        const inp = [...document.querySelectorAll(".sh-filters input")][0];
        const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(inp), "value").set;
        set.call(inp, "L3-w1");
        inp.dispatchEvent(new Event("input", { bubbles: true }));
      });
      await applyFilter(p);
      let sids2 = await listSids(p);
      let line = await countsLine(p);
      ok(sids2.length === META.l3_w1_sids.length
        && JSON.stringify([...sids2].sort()) === JSON.stringify(META.l3_w1_sids)
        && line.includes("已加载 " + META.l3_w1_sids.length),
        "工位筛选 L3-w1 与夹具逐项一致且计数行一致", line);
      await p.evaluate(() => {
        const sel = document.querySelector(".sh-filters select:nth-of-type(1)");
        const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(sel), "value").set;
        set.call(sel, "");
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      });
      await p.select ? null : null;
      // 类型筛选：显式归档
      await p.evaluate(() => {
        const selects = [...document.querySelectorAll(".sh-filters select")];
        const sel = selects[selects.length - 1];      // 类型下拉
        const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(sel), "value").set;
        set.call(sel, "explicit_archive");
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        const inp = [...document.querySelectorAll(".sh-filters input")][0];
        const setV = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(inp), "value").set;
        setV.call(inp, "");
        inp.dispatchEvent(new Event("input", { bubbles: true }));
      });
      await applyFilter(p);
      sids2 = await listSids(p);
      ok(sids2.length === 1 && sids2[0] === META.archived,
        "类型筛选显式归档只含归档样例一条", JSON.stringify(sids2));
      const badge = await p.evaluate(() =>
        document.querySelector(".sh-row .sh-badge")?.textContent ?? "");
      ok(badge.includes("显式归档"), "归档行徽标为显式归档", badge);
      // 保留不冒充归档
      await p.evaluate(() => {
        const selects = [...document.querySelectorAll(".sh-filters select")];
        const sel = selects[selects.length - 1];
        const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(sel), "value").set;
        set.call(sel, "retained_timeout");
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      });
      await applyFilter(p);
      const badges = await p.evaluate(() =>
        [...document.querySelectorAll(".sh-row .sh-badge")].map((b) => b.textContent));
      ok(badges.length > 3 && badges.every((b) => b.includes("超时保留")),
        "超时保留行徽标为超时保留（不冒充显式归档）",
        JSON.stringify([...new Set(badges)]));
      line = await countsLine(p);
      ok(line.includes("已加载") && /\d+/.test(line), "计数行格式（索引共/筛选/已加载）", line);
      // 日期筛选
      await p.evaluate(() => {
        const selects = [...document.querySelectorAll(".sh-filters select")];
        const sel = selects[selects.length - 1];
        const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(sel), "value").set;
        set.call(sel, "");
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        const dates = [...document.querySelectorAll(".sh-filters input[type=date]")];
        const setV = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(dates[0]), "value").set;
        setV.call(dates[0], "2026-10-15");
        dates[0].dispatchEvent(new Event("input", { bubbles: true }));
      });
      await applyFilter(p);
      for (let i = 0; i < 12; i++) {
        const c = await countsLine(p);
        const expN = 59;
        if (c.includes("当前筛选 " + expN)) break;
        await sleep(700);
      }
      while (await p.evaluate(() => !!document.querySelector(".sh-foot .ws-btn.primary"))) {
        await loadMore(p);
      }
      sids2 = await listSids(p);
      const expectAfter15 = META.all_sids.filter((s) => {
        const idx = JSON.parse(fs.readFileSync(FIX + "/index.json", "utf8")).sessions[s];
        return idx && idx.last_seen_at >= "2026-10-15";
      });
      ok(sids2.length === expectAfter15.length
        && JSON.stringify([...sids2].sort()) === JSON.stringify([...expectAfter15].sort()),
        "日期筛选（最后活动≥2026-10-15）全页遍历与夹具逐项一致",
        "page=" + sids2.length + " expect=" + expectAfter15.length);
      // 事项筛选
      await p.evaluate(() => {
        const dates = [...document.querySelectorAll(".sh-filters input[type=date]")];
        const setV = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(dates[0]), "value").set;
        setV.call(dates[0], "");
        dates[0].dispatchEvent(new Event("input", { bubbles: true }));
        const inp = [...document.querySelectorAll(".sh-filters input")][1];
        const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(inp), "value").set;
        set.call(inp, "I-999");
        inp.dispatchEvent(new Event("input", { bubbles: true }));
      });
      await applyFilter(p);
      for (let i = 0; i < 12; i++) {
        const c = await countsLine(p);
        if (c.includes("当前筛选 4")) break;
        await sleep(700);
      }
      sids2 = await listSids(p);
      ok(sids2.length === 4 && sids2.includes(META.chain.A) && sids2.includes(META.chain.D),
        "事项筛选 I-999 含交接链四会话", JSON.stringify(sids2.map((s) => s.slice(5, 13))));

      // 全量分页遍历（126 条 / 页 50 → 3 页）
      await p.evaluate(() => {
        const inp = [...document.querySelectorAll(".sh-filters input")][1];
        const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(inp), "value").set;
        set.call(inp, "");
        inp.dispatchEvent(new Event("input", { bubbles: true }));
      });
      await applyFilter(p);
      let all = await listSids(p);
      const total0 = all.length;
      while (true) {
        const more = await p.evaluate(() => {
          const btn = document.querySelector(".sh-foot .ws-btn.primary");
          return btn && btn.textContent.includes("加载更多") ? btn.textContent : null;
        });
        if (!more) break;
        await loadMore(p);
        all = await listSids(p);
      }
      const uniq = new Set(all);
      ok(all.length === META.all_sids.length && uniq.size === all.length
        && JSON.stringify([...uniq].sort()) === JSON.stringify(META.all_sids),
        "分页遍历全部 126 条：无重复无遗漏且与夹具全集一致",
        "loaded=" + all.length + " uniq=" + uniq.size + " fixture=" + META.all_sids.length);
      const doneText = await p.evaluate(() =>
        document.querySelector(".sh-foot")?.textContent ?? "");
      ok(doneText.includes("已全部加载"), "遍历完成标记（已全部加载）", doneText.slice(0, 80));
      line = await countsLine(p);
      ok(line.includes("已加载 " + all.length) && line.includes("当前筛选 " + all.length),
        "计数行与接口 total 一致（完整=筛选=已加载 126）", line);
      await p.screenshot({ path: OUT + "/c2_pagination_done.png", fullPage: false });

      /* ── 判据三：旧会话详情与四类缺失样例 ── */
      console.log("判据三：旧会话只读详情与缺失样例");
      const openBySid = async (sid) => {
        await p.evaluate((sid) => {
          const row = [...document.querySelectorAll(".sh-row")]
            .find((r) => r.getAttribute("data-session-id") === sid);
          (row?.querySelector(".sh-sid"))?.click();
        }, sid);
        await p.waitForSelector(".sh-drawer", { timeout: 15000 });
        await sleep(700);
      };
      const detailText = (p2) => p2.evaluate(() =>
        document.querySelector(".sh-drawer")?.textContent ?? "");
      const closeDetail = async (p2) => {
        await p2.evaluate(() => {
          const btn = [...document.querySelectorAll(".sh-drawer .lab-btn-minor")]
            .find((b) => b.textContent === "关闭");
          if (btn) btn.click();
        });
        await sleep(300);
      };

      // 已删除样例（原会话库来源在册后删除）
      await openBySid(META.deleted);
      let dtxt = await detailText(p);
      ok(dtxt.includes("会话行已删除") && dtxt.includes("对话摘要不可用"),
        "已删除样例：缺失说明（会话行已删除）与对话摘要不可用原因", dtxt.slice(0, 160));
      await p.screenshot({ path: OUT + "/c3_deleted_sample.png", fullPage: false });
      await closeDetail(p);
      // 无对话源样例（仅登记来源）
      await openBySid(META.nodialog);
      dtxt = await detailText(p);
      ok(dtxt.includes("对话摘要不可用") && dtxt.includes("对话源缺失"),
        "无对话源样例：对话摘要不可用且注明缺失原因", dtxt.slice(0, 160));
      await closeDetail(p);
      // 无认领记录样例（items 空）
      await openBySid(META.noclaim);
      dtxt = await detailText(p);
      ok(dtxt.includes("无认领记录"),
        "无认领记录样例：显示无认领原因（不编造事项履历）", dtxt.slice(0, 160));
      await p.screenshot({ path: OUT + "/c3_noclaim_sample.png", fullPage: false });
      await closeDetail(p);

      /* ── 判据四：三次交接链与只读 ── */
      console.log("判据四：交接链、交接后运行与只读");
      await openBySid(META.chain.A);
      dtxt = await detailText(p);
      const chainSids = await p.evaluate(() =>
        [...document.querySelectorAll(".sh-chain-sid")].map((b) => b.textContent));
      ok(chainSids.includes(META.chain.B) && chainSids.includes(META.chain.C)
        && chainSids.includes(META.chain.D),
        "链头 A 详情：前代/后代链含 B、C、D（三次交接全可达）",
        JSON.stringify(chainSids));
      ok(dtxt.includes("已取得定向停止和新会话回读回执") && dtxt.includes("交接卡 A→B"),
        "交接卡渲染含真实原因与卡文摘录", dtxt.slice(0, 200));
      const noActions = await p.evaluate(() =>
        [...document.querySelectorAll(".sh-drawer button")]
          .filter((b) => b.textContent.includes("注入") || b.textContent.includes("停止")
            || b.textContent.includes("恢复")).length);
      ok(noActions === 0 && dtxt.includes("只读"),
        "旧会话详情只读：无注入/停止/恢复入口", String(noActions));
      await p.screenshot({ path: OUT + "/c4_chain_head_detail.png", fullPage: false });
      // 经链点入 D（交接后新会话）
      await p.evaluate((sid) => {
        const btn = [...document.querySelectorAll(".sh-chain-sid")]
          .find((b) => b.textContent === sid);
        if (btn) btn.click();
      }, META.chain.D);
      await sleep(900);
      dtxt = await detailText(p);
      const dSid = await p.evaluate(() =>
        document.querySelector(".sh-drawer")?.getAttribute("data-session-id") ?? "");
      ok(dSid === META.chain.D && dtxt.includes("运行中")
        && dtxt.includes("run-i228f-d1"),
        "链尾 D：交接后新会话运行中（活跃运行在册）", dSid.slice(5, 13) + " " + dtxt.slice(0, 80));
      ok(dtxt.includes("前代链"), "D 详情可回溯前代链（双向可查）", "");
      await p.screenshot({ path: OUT + "/c4_chain_tail_running.png", fullPage: false });
      await closeDetail(p);
      await p.close().catch(() => {});
    });

    /* ── 判据五：实况对账行 + 单卡回归 ── */
    console.log("判据五：实况对账行与单卡定向回归");
    await withState(browser, stateWith([
      mkCard("sess_i228_live_w1_0001", "L3", "I-981", "在干活（长工具 100 秒：最早在途工具超窗待核对）"),
      mkCard("sess_i228_live_w2_0002", "L3", "I-982", "未知（待核对：会话库本周期不可读）"),
    ]), async (p) => {
      await p.evaluate(() => {
        const btn = [...document.querySelectorAll(".console-tab")]
          .find((b) => b.textContent.includes("观察窗"));
        if (btn) btn.click();
      });
      await p.waitForSelector(".ws-group", { timeout: 15000 });
      await sleep(600);
      const sample = await p.evaluate(() => [...document.querySelectorAll(".ws-sample-line")]
        .map((el) => ({ status: el.getAttribute("data-status"),
                        text: el.textContent })));
      ok(sample.length === 2
        && sample.some((s) => s.text.includes("采样") && s.status.includes("长工具"))
        && sample.some((s) => s.status.startsWith("未知")),
        "卡片实况行：显示采样时刻与后端状态（长工具>90 秒在干活、采集失联未知）",
        JSON.stringify(sample));
      await p.screenshot({ path: OUT + "/c5_live_fields.png", fullPage: false });
      // 单卡注入定向回归（完整会话号）
      const step = await p.evaluate(() => {
        const card = [...document.querySelectorAll(".ws-card:not(.retained)")]
          .find((c) => c.getAttribute("data-session-id") === "sess_i228_live_w2_0002");
        const btn = [...(card?.querySelectorAll(".ws-actions .ws-btn") ?? [])]
          .find((b) => b.textContent === "注入");
        if (!btn) return "no btn";
        btn.click();
        return "clicked";
      });
      await sleep(400);
      const modal = await p.evaluate(() => ({
        sid: document.querySelector(".ws-modal")?.getAttribute("data-session-id") ?? "" }));
      ok(step === "clicked" && modal.sid === "sess_i228_live_w2_0002",
        "单卡注入定向回归（完整会话号）", modal.sid);
      await p.evaluate(() => {
        const close = [...document.querySelectorAll(".ws-modal .ws-btn")]
          .find((b) => b.textContent === "取消");
        if (close) close.click();
      }).catch(() => {});
      await p.close().catch(() => {});
    });

    /* ── 判据三（续）：接口失败样例（停服务制造真实失败） ── */
    console.log("接口失败样例：停止私有查询服务");
    spawn("taskkill", ["/T", "/F", "/PID", String(hist.pid)], { shell: true });
    await sleep(1500);
    await withState(browser, stateWith([]), async (p) => {
      await openHistoryTab(p, null);
      await applyFilter(p);
      await sleep(600);
      const errText = await p.evaluate(() =>
        document.querySelector(".sh-error")?.textContent ?? "");
      ok(errText.includes("历史查询接口失败") || errText.includes("不可达"),
        "接口失败样例：页面显示明确失败原因", errText.slice(0, 120));
      await p.screenshot({ path: OUT + "/c3_api_failure_sample.png", fullPage: false });
      await p.close().catch(() => {});
    });
    // 复活服务（同一夹具）供后续复跑
    hist2 = spawn("python", [ROOT + "/lp_sync/session_history_server.py"], {
      cwd: ROOT + "/lp_sync", shell: true, stdio: "ignore",
      env: { ...process.env, LP_SESSION_HISTORY_PORT: String(HISTORY_PORT),
             LP_SESSION_HISTORY_DIR: FIX } });
    await sleep(1500);
  } finally {
    await browser.close().catch(() => {});
    for (const proc of [preview, hist, hist2]) {
      if (proc && proc.pid) {
        spawn("taskkill", ["/T", "/F", "/PID", String(proc.pid)], { shell: true });
      }
    }
  }
  record.pass = pass; record.fail = fail;
  fs.writeFileSync(OUT + "/isolated_check_record.json", JSON.stringify(record, null, 1));
  console.log("\n结果：" + pass + " 过 / " + fail + " 拒；记录 " + OUT + "/isolated_check_record.json");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("核验脚本异常：", e); process.exit(2); });
