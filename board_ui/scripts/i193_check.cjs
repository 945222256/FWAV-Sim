/* i193_check.cjs — I-193 判据核验：会话编号与主体区块点击直达履历。
 * 真实读回：L2 与 L5 活跃卡的编号区与主体区点击均开同一履历、抽屉
 * 完整会话号、L2 履历与认领记录逐项对照（I-182/I-190/I-184 在列、
 * I-176 不混入）、按钮冒泡不误触、I-192 回归、点击前后截图。
 * 隔离样例：四态（活跃、归档灰块、无会话占位、接口失败）加
 * 无可核实记录与加载中说明。证据写 logs/i193_check/。 */
const puppeteer = require("puppeteer-core");
const https = require("https");
const fs = require("fs");
const path = require("path");

const SECRET = JSON.parse(fs.readFileSync(
  "G:/Playground/isaac_flapper_sim/lp_sync/cloud_secret.json", "utf-8"));
const ROOT = "G:/Playground/isaac_flapper_sim";
const HOST = "personal.karmicsword.com";
const OUT_DIR = path.join(ROOT, "logs", "i193_check");
fs.mkdirSync(OUT_DIR, { recursive: true });

let pass = 0, fail = 0;
const record = { generated_at: new Date().toISOString(), real: null, isolated: null };
function ok(cond, label, detail) {
  if (cond) { pass++; console.log("  [过] " + label); }
  else { fail++; console.log("  [拒] " + label + (detail ? "：" + detail : "")); }
  return !!cond;
}

function req(method, reqpath, body, cookie) {
  return new Promise((res, rej) => {
    const opts = { hostname: HOST, path: reqpath, method,
      headers: { Cookie: cookie || "" }, rejectUnauthorized: false };
    if (body) {
      const d = JSON.stringify(body);
      opts.headers["Content-Type"] = "application/json";
      opts.headers["Content-Length"] = Buffer.byteLength(d);
    }
    const r = https.request(opts, (x) => {
      let b = ""; x.on("data", (c) => (b += c));
      x.on("end", () => res({ status: x.statusCode, headers: x.headers, json: b ? JSON.parse(b) : null }));
    });
    r.on("error", rej);
    if (body) r.write(JSON.stringify(body));
    r.end();
  });
}

(async () => {
  const login = await req("POST", "/api/auth/login",
    { username: SECRET.board_user, password: SECRET.board_password });
  const cookie = (login.headers["set-cookie"] || []).map((c) => c.split(";")[0]).join("; ");
  const cookieVal = cookie.split("=")[1];
  const realEvents = await req("GET", "/api/events", null, cookie);
  const evs = realEvents.json.result;
  const claimsOf = (sid) => evs.filter((e) => e.action === "claim"
    && (e.detail || "").includes(sid))
    .map((e) => ({ item: e.item_id, ts: e.ts.replace("T", " ") }));
  const l2Claims = claimsOf("sess_5074d84c-2adc-43b6-aec6-33f46c3df739");
  const f0Claims = claimsOf("sess_f0a86a11-92bd-44f0-8139-db08efec23e2");
  ok(l2Claims.map((c) => c.item).join() === "I-182,I-190,I-184"
    && f0Claims.map((c) => c.item).join() === "I-176",
    "来源核对：sess_5074d84c 认领 I-182/I-190/I-184，sess_f0a86a11 认领 I-176（账本事件）",
    JSON.stringify({ l2Claims, f0Claims }));

  const browser = await puppeteer.launch({
    executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    headless: true, args: ["--no-sandbox"], defaultViewport: { width: 1600, height: 1000 },
  });
  async function newPage() {
    const p = await browser.newPage();
    await p.setCacheEnabled(false);
    await p.setCookie({ name: "lp_session", value: cookieVal, domain: HOST, path: "/" });
    return p;
  }
  async function openWatch(p) {
    await p.goto("https://" + HOST + "/app/#/console", { waitUntil: "networkidle2", timeout: 60000 });
    await p.waitForSelector(".console-tabs", { timeout: 30000 });
    await p.evaluate(() => {
      const btn = [...document.querySelectorAll(".console-tab")]
        .find((b) => b.textContent.includes("观察窗"));
      if (btn) btn.click();
    });
    await p.waitForSelector(".ws-group", { timeout: 15000 });
    await new Promise((r) => setTimeout(r, 1500));
  }
  async function closeDrawer(p) {
    await p.evaluate(() => {
      const btn = [...document.querySelectorAll(".hist-drawer .lab-btn-minor")]
        .find((b) => b.textContent === "关闭");
      if (btn) btn.click();
    }).catch(() => {});
    await new Promise((r) => setTimeout(r, 250));
  }
  const clickSid = (p, lane) => p.evaluate((lane) => {
    const g = [...document.querySelectorAll(".ws-group")]
      .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith(lane));
    if (!g) return "no group";
    const card = g.querySelector(".ws-card:not(.retained)");
    if (!card) return "no card";
    const sid = card.querySelector(".ws-sid-click");
    if (!sid) return "no sid entry";
    sid.click();
    return sid.textContent;
  }, lane);
  const clickGauge = (p, lane) => p.evaluate((lane) => {
    const g = [...document.querySelectorAll(".ws-group")]
      .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith(lane));
    if (!g) return "no group";
    const card = g.querySelector(".ws-card:not(.retained)");
    if (!card) return "no card";
    const gauge = card.querySelector(".ws-gauge-click");
    if (!gauge) return "no gauge entry";
    gauge.click();
    return "clicked";
  }, lane);

  console.log("真实读回一：点击入口与完整会话号（判据一）");
  const pageR = await newPage();
  await openWatch(pageR);
  await pageR.screenshot({ path: OUT_DIR + "/real_before_click.png", fullPage: false });
  const sidText = await clickSid(pageR, "L5");
  ok(sidText.startsWith("sess_"), "L5 编号区点击直达（编号区为按钮）", String(sidText));
  await pageR.waitForSelector(".hist-drawer", { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 2000));
  const sidShown = await pageR.$eval(".hist-full-sid", (e) => e.textContent.trim());
  ok(sidShown === "sess_aad06e4d-9932-4d2a-ad14-79adc87cdeda",
    "抽屉显示完整会话标识（36 字符无截断）", sidShown);
  await pageR.screenshot({ path: OUT_DIR + "/real_after_click_sid.png", fullPage: false });
  await closeDrawer(pageR);
  const gClick = await clickGauge(pageR, "L5");
  ok(gClick === "clicked", "上下文主体区点击直达", gClick);
  await pageR.waitForSelector(".hist-drawer", { timeout: 15000 });
  const sidShown2 = await pageR.$eval(".hist-full-sid", (e) => e.textContent.trim());
  ok(sidShown2 === sidShown, "两入口打开同一会话履历（会话号一致）", sidShown2);
  await closeDrawer(pageR);

  console.log("真实读回二：按钮冒泡不误触（判据一）");
  const modalProbe = await pageR.evaluate(() => {
    const g = [...document.querySelectorAll(".ws-group")]
      .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith("L5"));
    const card = g?.querySelector(".ws-card:not(.retained)");
    if (!card) return "no card";
    const inject = [...card.querySelectorAll(".ws-actions .ws-btn")]
      .find((b) => b.textContent === "注入");
    if (!inject) return "no inject btn";
    inject.click();
    return "inject clicked";
  });
  await new Promise((r) => setTimeout(r, 400));
  const modalOpen = await pageR.$(".ws-modal") !== null;
  const histOpen = await pageR.$(".hist-drawer") !== null;
  ok(modalOpen && !histOpen, "注入按钮只开确认弹层、不开履历（不误触）",
    modalProbe + " modal=" + modalOpen + " hist=" + histOpen);
  await pageR.evaluate(() => {
    const cancel = [...document.querySelectorAll(".ws-modal .ws-btn")]
      .find((b) => b.textContent === "取消");
    if (cancel) cancel.click();
  }).catch(() => {});
  await new Promise((r) => setTimeout(r, 250));

  console.log("真实读回三：L2 真实来源对照（判据四）");
  const l2Sid = await clickSid(pageR, "L2");
  await pageR.waitForSelector(".hist-drawer", { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 2200));
  const l2Hist = await pageR.$eval(".hist-drawer", (e) => e.textContent);
  const l2SidShown = await pageR.$eval(".hist-full-sid", (e) => e.textContent.trim());
  ok(l2SidShown === "sess_5074d84c-2adc-43b6-aec6-33f46c3df739",
    "L2 入口绑定当前会话号（与卡片数据一致）", l2SidShown + " / " + l2Sid);
  ok(["I-182", "I-190", "I-184"].every((id) => l2Hist.includes(id)),
    "有来源的历次事项均可查（I-182、I-190、I-184）", l2Hist.slice(0, 150));
  ok(!l2Hist.includes("I-176"),
    "I-176 不混入本会话履历（不从旧显示挂接猜造历史）", "");
  ok(l2Claims.every((c) => l2Hist.includes(c.ts)),
    "认领时间与账本逐字一致（差异为零）", JSON.stringify(l2Claims));
  await pageR.screenshot({ path: OUT_DIR + "/real_l2_history.png", fullPage: false });
  record.real = {
    l2_session: l2SidShown, l2_claims: l2Claims, f0a_claims: f0Claims,
    l2_contains_I176: l2Hist.includes("I-176"),
    full_sid_length: sidShown.length,
  };
  await closeDrawer(pageR);

  console.log("真实读回四：I-192 回归（判据五）");
  const reg = await pageR.evaluate(() => {
    const g = [...document.querySelectorAll(".ws-group")]
      .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith("L5"));
    const card = g?.querySelector(".ws-card:not(.retained)");
    if (!card) return "no card";
    const btn = [...card.querySelectorAll(".ws-actions .ws-btn")]
      .find((b) => b.textContent === "履历");
    if (!btn) return "no btn";
    btn.click();
    return "btn ok";
  });
  await pageR.waitForSelector(".hist-drawer", { timeout: 15000 });
  ok(reg === "btn ok", "I-192 履历按钮回归可用", reg);
  await closeDrawer(pageR);
  const gaugeNum = await pageR.$eval(".ws-card:not(.retained) .ws-gauge-num", (e) => e.textContent);
  const ctrlBtns = await pageR.$$eval(".ws-card:not(.retained) .ws-actions .ws-btn",
    (els) => els.map((e) => e.textContent));
  ok(/%|无读数/.test(gaugeNum) && ctrlBtns.includes("注入") && ctrlBtns.includes("停止")
    && ctrlBtns.includes("出处"),
    "原上下文读数与控制按钮保持一致", gaugeNum + " / " + JSON.stringify(ctrlBtns));
  // 灰态会话查看回归（若真实归档行存在则点其入口）
  const retainedEntry = await pageR.evaluate(() => {
    const card = document.querySelector(".ws-card.retained .ws-sid-click");
    if (!card) return "none";
    card.click();
    return "clicked";
  });
  if (retainedEntry === "clicked") {
    await pageR.waitForSelector(".hist-drawer", { timeout: 15000 });
    ok(true, "灰态会话入口回归可用（真实归档行）");
    await pageR.screenshot({ path: OUT_DIR + "/real_retained_history.png", fullPage: false });
    await closeDrawer(pageR);
  } else {
    ok(true, "真实归档行为空，灰态入口回归转隔离样例验证");
  }
  await pageR.close().catch(() => {});

  console.log("隔离样例：四态与说明文案（判据二）");
  const mkSource = (sid) => ({ kind: "表", locus: "console_sessions/" + sid,
    excerpt: "隔离样例（mock_i193）", sampled_at: "2026-10-12T17:00:00" });
  const card = (sid, lane, archived, pct) => ({
    session_id: sid, lane, item_id: archived ? "I-802" : "I-801",
    status: archived ? "已归档" : "在干活", context_pct: archived ? null : pct,
    last_activity_at: "2026-10-12T17:00:00", updated_at: "2026-10-12T17:00:00",
    source: mkSource(sid),
  });
  const mockEvents = [
    { seq: 201, ts: "2026-10-12T15:00:00", action: "claim", item_id: "I-801", actor: "L5队",
      detail: "认领，租约 4 小时，会话 sess_mock_i193_live_aaaabbbbcccc" },
  ];
  const details = { "I-801": { id: "I-801", title: "隔离样例八零一（mock）",
    status: "in_progress", conclusion: null } };
  const stateBody = {
    goals: [], lanes: (await req("GET", "/api/console/state", null, cookie)).json.result.lanes,
    sessions: {
      active: [card("sess_mock_i193_live_aaaabbbbcccc", "L5", false, 23.4)],
      archived: [card("sess_mock_i193_arch_aaaabbbbcccc", "L3", true)],
      owner: [],
    },
    footer: { parallel: { active: 1, tracked: 1, limit: 15 },
              sampled_at: "2026-10-12T17:00:00", source: mkSource("f") },
  };
  const pageM = await newPage();
  await pageM.setRequestInterception(true);
  let eventsMode = "ok";           // ok | fail | empty | slow
  let slowTimer = null;
  const handler = (rq) => {
    const url = rq.url();
    const reply = (result, delay) => {
      const doReply = () => rq.respond({ status: 200, contentType: "application/json",
        headers: { Date: new Date().toUTCString() },
        body: JSON.stringify({ ok: true, result }) });
      if (delay) slowTimer = setTimeout(doReply, delay);
      else doReply();
    };
    if (url.includes("/api/console/state")) reply(stateBody);
    else if (url.includes("/api/console/config"))
      reply({ handoff_mode: "auto", gate_pct: 70, parallel_limit: 15 });
    else if (url.includes("/api/events")) {
      if (eventsMode === "fail") {
        rq.respond({ status: 500, contentType: "application/json",
          headers: { Date: new Date().toUTCString() }, body: '{"ok":false}' });
      } else if (eventsMode === "slow") reply([], 5000);
      else reply(eventsMode === "empty" ? [] : mockEvents);
    } else if (url.includes("/api/item-detail")) {
      const m = url.match(/id=(I-\d{3})/);
      const item = m ? details[m[1]] : null;
      if (item) reply({ item, events: [] });
      else rq.continue();
    } else rq.continue();
  };
  pageM.on("request", handler);
  await openWatch(pageM);

  // 活跃卡直接入口
  await clickSid(pageM, "L5");
  await pageM.waitForSelector(".hist-drawer", { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 800));
  const liveHist = await pageM.$eval(".hist-drawer", (e) => e.textContent);
  ok(liveHist.includes("I-801") && liveHist.includes("sess_mock_i193_live_aaaabbbbcccc"),
    "活跃卡编号区直达履历（隔离）", liveHist.slice(0, 80));
  await closeDrawer(pageM);
  // 归档灰块入口
  const arch = await pageM.evaluate(() => {
    const sid = document.querySelector(".ws-card.retained .ws-sid-click");
    if (!sid) return "none";
    sid.click();
    return "clicked";
  });
  await pageM.waitForSelector(".hist-drawer", { timeout: 15000 });
  const archHist = await pageM.$eval(".hist-drawer", (e) => e.textContent);
  ok(arch === "clicked" && archHist.includes("未登记本会话的认领事件"),
    "归档灰块入口可用且无可核实记录时显示明确说明", archHist.slice(0, 100));
  await pageM.screenshot({ path: OUT_DIR + "/isolated_no_records.png", fullPage: false });
  await closeDrawer(pageM);
  // 无会话占位块
  const placeholder = await pageM.evaluate(() => {
    const g = [...document.querySelectorAll(".ws-group")]
      .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith("L1"));
    const card = g?.querySelector(".ws-card.retained");
    return card ? { text: card.textContent, hasEntry: !!card.querySelector(".ws-sid-click") } : null;
  });
  ok(placeholder && placeholder.text.includes("暂无会话历史") && !placeholder.hasEntry,
    "无会话占位块显示暂无会话历史且无点击入口", JSON.stringify(placeholder).slice(0, 120));
  // 加载中与失败说明：HistoryDrawer 有十秒模块级事件缓存，这两种态
  // 验证首次加载路径，各用全新页面（缓存随页面生命周期归零）。
  async function freshProbe(mode, wantText, shotName, label) {
    const p = await newPage();
    await p.setRequestInterception(true);
    let slowTimer = null;
    const h = (rq) => {
      const url = rq.url();
      const reply = (result, delay) => {
        const doReply = () => rq.respond({ status: 200, contentType: "application/json",
          headers: { Date: new Date().toUTCString() },
          body: JSON.stringify({ ok: true, result }) });
        if (delay) slowTimer = setTimeout(doReply, delay);
        else doReply();
      };
      if (url.includes("/api/console/state")) reply(stateBody);
      else if (url.includes("/api/console/config"))
        reply({ handoff_mode: "auto", gate_pct: 70, parallel_limit: 15 });
      else if (url.includes("/api/events")) {
        if (mode === "fail") {
          rq.respond({ status: 500, contentType: "application/json",
            headers: { Date: new Date().toUTCString() }, body: '{"ok":false}' });
        } else if (mode === "slow") reply([], 5000);
        else reply(mockEvents);
      } else if (url.includes("/api/item-detail")) {
        const m = url.match(/id=(I-\d{3})/);
        const item = m ? details[m[1]] : null;
        if (item) reply({ item, events: [] });
        else rq.continue();
      } else rq.continue();
    };
    p.on("request", h);
    await openWatch(p);
    await clickSid(p, "L5");
    await new Promise((r) => setTimeout(r, mode === "slow" ? 700 : 900));
    const text = await p.$eval(".hist-drawer", (e) => e.textContent).catch(() => "");
    ok(text.includes(wantText), label, text.slice(0, 90));
    await p.screenshot({ path: OUT_DIR + "/" + shotName, fullPage: false }).catch(() => {});
    clearTimeout(slowTimer);
    p.off("request", h);
    await p.setRequestInterception(false).catch(() => {});
    await p.close().catch(() => {});
  }
  await freshProbe("slow", "账本事件读取中", "isolated_loading.png", "加载中显示读取中说明");
  await freshProbe("fail", "账本事件接口不可达", "isolated_failed.png",
    "接口失败显示失败说明（不显示旧数据）");
  record.isolated = { live: "I-801", archived_note: true, placeholder: true,
    loading: true, failed: true };
  pageM.off("request", handler);
  await pageM.setRequestInterception(false).catch(() => {});
  await pageM.close().catch(() => {});

  await browser.close();
  record.pass = pass; record.fail = fail;
  fs.writeFileSync(OUT_DIR + "/verify_record.json", JSON.stringify(record, null, 1));
  console.log("\n结果：" + pass + " 过 / " + fail + " 拒；验证记录 " + OUT_DIR + "/verify_record.json");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("核验脚本异常：", e); process.exit(2); });
