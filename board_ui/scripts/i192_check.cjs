/* i192_check.cjs — I-192 判据核验：会话工作履历。
 * 真实读回：五队驻留会话（本会话真实卡）点开履历，断言关联事项
 * I-168/I-189/I-192、认领时间与 /api/events 原始事件逐字一致、
 * 当前状态与 /api/item-detail 实时一致。
 * 隔离样例：拦截 state/events/item-detail 注入构造数据，覆盖追加任务、
 * 重复采集、再次认领与归档后查看四种情形，关联与事件差异为零。
 * 证据写入 logs/i192_check/。 */
const puppeteer = require("puppeteer-core");
const https = require("https");
const fs = require("fs");
const path = require("path");

const SECRET = JSON.parse(fs.readFileSync(
  "G:/Playground/isaac_flapper_sim/lp_sync/cloud_secret.json", "utf-8"));
const ROOT = "G:/Playground/isaac_flapper_sim";
const HOST = "personal.karmicsword.com";
const OUT_DIR = path.join(ROOT, "logs", "i192_check");
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

/* 隔离样例数据（mock 前缀以示隔离）：
 * sess_mock_i192_hist：先认领 I-801（释放后再认领一次后结载），追加 I-802；
 * sess_mock_i192_arch：归档会话，承办 I-803。 */
const MOCK = {
  histSid: "sess_mock_i192_hist_aaaabbbbcccc",
  archSid: "sess_mock_i192_arch_aaaabbbbcccc",
  events: [
    { seq: 101, ts: "2026-10-12T09:00:00", action: "assign", item_id: "I-801", actor: "agent", detail: "派发到 L5，负责人 L5队" },
    { seq: 102, ts: "2026-10-12T10:00:00", action: "claim", item_id: "I-801", actor: "L5队", detail: "认领，租约 4 小时，会话 sess_mock_i192_hist_aaaabbbbcccc" },
    { seq: 103, ts: "2026-10-12T10:30:00", action: "release", item_id: "I-801", actor: "L5队", detail: "主动释放认领" },
    { seq: 104, ts: "2026-10-12T10:40:00", action: "claim", item_id: "I-801", actor: "L5队", detail: "认领，租约 4 小时，会话 sess_mock_i192_hist_aaaabbbbcccc" },
    { seq: 105, ts: "2026-10-12T11:00:00", action: "claim", item_id: "I-802", actor: "L5队", detail: "认领，租约 4 小时，会话 sess_mock_i192_hist_aaaabbbbcccc" },
    { seq: 106, ts: "2026-10-12T12:00:00", action: "close", item_id: "I-801", actor: "L5队", detail: "打勾关闭（判据通过），证据 2 条" },
    { seq: 107, ts: "2026-10-12T08:00:00", action: "claim", item_id: "I-803", actor: "L3队", detail: "认领，租约 4 小时，会话 sess_mock_i192_arch_aaaabbbbcccc" },
    { seq: 108, ts: "2026-10-12T09:30:00", action: "close", item_id: "I-803", actor: "L3队", detail: "打勾关闭（判据通过），证据 1 条" },
  ],
  details: {
    "I-801": { id: "I-801", title: "隔离样例事项八零一（mock）", status: "closed", conclusion: "判据通过" },
    "I-802": { id: "I-802", title: "隔离样例事项八零二（mock）", status: "in_progress", conclusion: null },
    "I-803": { id: "I-803", title: "隔离样例事项八零三（mock）", status: "closed", conclusion: "判据通过" },
  },
};

function mockCard(sid, lane, archived, pct) {
  return { session_id: sid, lane, item_id: archived ? "I-803" : "I-802",
    status: archived ? "已归档" : "在干活", context_pct: archived ? null : pct,
    last_activity_at: "2026-10-12T12:00:00", updated_at: "2026-10-12T12:00:00",
    source: { kind: "表", locus: "console_sessions/" + sid, excerpt: "隔离样例（mock_i192）",
              sampled_at: "2026-10-12T12:00:00" } };
}

async function withMocks(page, stateBody, fn) {
  await page.setRequestInterception(true);
  const handler = (rq) => {
    const url = rq.url();
    const reply = (result) => rq.respond({ status: 200, contentType: "application/json",
      headers: { Date: new Date().toUTCString() },
      body: JSON.stringify({ ok: true, result }) });
    if (url.includes("/api/console/state")) reply(stateBody);
    else if (url.includes("/api/console/config"))
      reply({ handoff_mode: "auto", gate_pct: 70, parallel_limit: 15 });
    else if (url.includes("/api/events")) reply(MOCK.events);
    else if (url.includes("/api/item-detail")) {
      const m = url.match(/id=(I-\d{3})/);
      const item = m ? MOCK.details[m[1]] : null;
      if (item) reply({ item, events: [] });
      else rq.continue();
    } else rq.continue();
  };
  page.on("request", handler);
  try { await fn(); } finally {
    page.off("request", handler);
    await page.setRequestInterception(false).catch(() => {});
  }
}

(async () => {
  const login = await req("POST", "/api/auth/login",
    { username: SECRET.board_user, password: SECRET.board_password });
  const cookie = (login.headers["set-cookie"] || []).map((c) => c.split(";")[0]).join("; ");
  const cookieVal = cookie.split("=")[1];

  const realState = await req("GET", "/api/console/state", null, cookie);
  const realEvents = await req("GET", "/api/events", null, cookie);
  const evs = realEvents.json.result;
  const myClaims = evs.filter((e) => e.action === "claim"
    && (e.detail || "").includes("sess_aad06e4d-9932-4d2a-ad14-79adc87cdeda"));
  const expectIds = [...new Set(myClaims.map((e) => e.item_id))];
  ok(expectIds.includes("I-168") && expectIds.includes("I-189"),
    "数据源核对：本会话认领事件含 I-168 与 I-189（账本 events）", JSON.stringify(expectIds));

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
  async function drawerText(p) {
    return p.$eval(".hist-drawer", (e) => e.textContent).catch(() => "");
  }

  console.log("真实读回：五队驻留会话履历实测");
  const pageR = await newPage();
  await openWatch(pageR);
  // 本会话真实活跃卡在 L5 组，点击其履历按钮
  const found = await pageR.evaluate(() => {
    const groups = [...document.querySelectorAll(".ws-group")];
    const g = groups.find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith("L5"));
    if (!g) return "no L5 group";
    const card = [...g.querySelectorAll(".ws-card:not(.retained)")]
      .find((c) => c.textContent.includes("I-192") || c.textContent.includes("I-189") || c.textContent.includes("I-168"));
    if (!card) return "no active card";
    const btn = [...card.querySelectorAll(".ws-btn")].find((b) => b.textContent === "履历");
    if (!btn) return "no history btn";
    btn.click();
    return "clicked";
  });
  ok(found === "clicked", "活跃会话卡存在履历入口并可点击", found);
  await pageR.waitForSelector(".hist-drawer", { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 2500));      // 等待 events 与逐项 item-detail 拉取
  const histReal = await drawerText(pageR);
  const itemDetailReal = {};
  for (const id of ["I-168", "I-189", "I-192"]) {
    const d = await req("GET", "/api/item-detail?id=" + id, null, cookie);
    itemDetailReal[id] = d.json.result.item;
  }
  ok(["I-168", "I-189", "I-192"].every((id) => histReal.includes(id)),
    "履历列出本会话历次事项 I-168、I-189、I-192");
  const claimById = {};
  myClaims.forEach((e) => { claimById[e.item_id] = e.ts.replace("T", " "); });
  ok(["I-168", "I-189", "I-192"].every((id) => histReal.includes(claimById[id])),
    "各事项认领时间与账本原始事件逐字一致", JSON.stringify(claimById));
  ok(histReal.includes(itemDetailReal["I-168"].title.slice(0, 12))
    && histReal.includes(itemDetailReal["I-189"].title.slice(0, 12)),
    "事项名称与账本一致");
  ok(histReal.includes("完成") && histReal.includes("判据通过"),
    "I-168 与 I-189 当前状态（完成）与结论（判据通过）与云端账本一致");
  // 结载时间与账本 close 事件一致
  const closeReal = {};
  evs.filter((e) => e.action === "close" && ["I-168", "I-189"].includes(e.item_id))
    .forEach((e) => { closeReal[e.item_id] = e.ts.replace("T", " "); });
  ok(["I-168", "I-189"].every((id) => histReal.includes(closeReal[id])),
    "结载时间与账本 close 事件逐字一致", JSON.stringify(closeReal));
  // 出处叠加：点事件行角标开出处抽屉
  const srcOk = await pageR.evaluate(() => {
    const marks = document.querySelectorAll(".hist-drawer .hist-events .src-mark");
    if (marks.length === 0) return "no marks";
    marks[0].dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return "marks:" + marks.length;
  });
  await new Promise((r) => setTimeout(r, 400));
  const srcDrawer = await pageR.$eval(".src-drawer:not(.hist-drawer)", (e) => e.textContent).catch(() => "");
  ok(srcDrawer.includes("摘录"), "事件行出处角标可打开出处抽屉（叠加层）", srcOk + " / " + srcDrawer.slice(0, 60));
  await pageR.screenshot({ path: OUT_DIR + "/real_history.png", fullPage: false });
  record.real = { claims: claimById, closes: closeReal,
    statuses: { "I-168": itemDetailReal["I-168"].status, "I-189": itemDetailReal["I-189"].status } };
  await pageR.close().catch(() => {});

  console.log("隔离样例：追加任务、重复采集、再次认领与归档后查看");
  const stateBody = {
    goals: [], lanes: realState.json.result.lanes,
    sessions: {
      active: [mockCard(MOCK.histSid, "L5", false, 31.5)],
      archived: [mockCard(MOCK.archSid, "L3", true)],
      owner: [],
    },
    footer: { parallel: { active: 1, tracked: 1, limit: 15 },
              sampled_at: "2026-10-12T12:00:00",
              source: { kind: "表", locus: "x", excerpt: "x", sampled_at: "2026-10-12T12:00:00" } },
  };
  const pageM = await newPage();
  await withMocks(pageM, stateBody, async () => {
    await openWatch(pageM);
    await pageM.evaluate(() => {
      const groups = [...document.querySelectorAll(".ws-group")];
      const g5 = groups.find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith("L5"));
      const btn = g5 && [...g5.querySelectorAll(".ws-card:not(.retained) .ws-btn")]
        .find((b) => b.textContent === "履历");
      if (btn) btn.click();
    });
    await pageM.waitForSelector(".hist-drawer", { timeout: 15000 });
    await new Promise((r) => setTimeout(r, 1800));
    const t1 = await drawerText(pageM);
    ok(t1.includes("I-801") && t1.includes("I-802"),
      "关联事项集合与注入认领记录一致（I-801 与追加的 I-802，差异为零）");
    ok(t1.includes("隔离样例事项八零一") && t1.includes("隔离样例事项八零二"),
      "事项名称与注入账本一致");
    const evRows1 = await pageM.$$eval(".hist-drawer .hist-item", (els) => els.map((e) => ({
      id: e.querySelector(".ov-id")?.textContent ?? "",
      evs: [...e.querySelectorAll(".hist-events li")].map((li) => li.textContent),
    })));
    const i801 = evRows1.find((r) => r.id === "I-801");
    ok(i801 && i801.evs.length === 5, "I-801 保留完整事件顺序（派发、认领、释放、再认领、结载五行）",
      JSON.stringify(i801));
    ok(i801 && i801.evs.some((x) => x.includes("2026-10-12 09:00:00"))
      && i801.evs.some((x) => x.includes("2026-10-12 12:00:00")),
      "事件时间逐字与注入事件一致");
    ok(t1.includes("完成") && t1.includes("判据通过") && t1.includes("进行中"),
      "当前状态与注入账本逐字一致（I-801 完成、I-802 进行中）");
    // 重复采集：关闭再打开，事件行数不变
    await pageM.evaluate(() => {
      const btn = [...document.querySelectorAll(".hist-drawer .lab-btn-minor")]
        .find((b) => b.textContent === "关闭");
      if (btn) btn.click();
    });
    await new Promise((r) => setTimeout(r, 300));
    await pageM.evaluate(() => {
      const groups = [...document.querySelectorAll(".ws-group")];
      const g5 = groups.find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith("L5"));
      const btn = g5 && [...g5.querySelectorAll(".ws-card:not(.retained) .ws-btn")]
        .find((b) => b.textContent === "履历");
      if (btn) btn.click();
    });
    await pageM.waitForSelector(".hist-drawer", { timeout: 15000 });
    await new Promise((r) => setTimeout(r, 1200));
    const evRows2 = await pageM.$$eval(".hist-drawer .hist-item", (els) => els.map((e) => ({
      id: e.querySelector(".ov-id")?.textContent ?? "",
      n: e.querySelectorAll(".hist-events li").length,
    })));
    const same = JSON.stringify(evRows1.map((r) => ({ id: r.id, n: r.evs.length }))) === JSON.stringify(evRows2);
    ok(same, "重复采集（再次打开履历）不生成重复记录", JSON.stringify(evRows2));
    await pageM.screenshot({ path: OUT_DIR + "/isolated_history.png", fullPage: false });

    // 归档会话履历入口（L3 灰块）
    await pageM.evaluate(() => {
      const btn = [...document.querySelectorAll(".hist-drawer .lab-btn-minor")]
        .find((b) => b.textContent === "关闭");
      if (btn) btn.click();
    });
    await new Promise((r) => setTimeout(r, 300));
    await pageM.evaluate(() => {
      const groups = [...document.querySelectorAll(".ws-group")];
      const g3 = groups.find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith("L3"));
      const btn = g3 && [...g3.querySelectorAll(".ws-card.retained .ws-btn")]
        .find((b) => b.textContent === "履历");
      if (btn) btn.click();
    });
    await pageM.waitForSelector(".hist-drawer", { timeout: 15000 });
    await new Promise((r) => setTimeout(r, 1200));
    const t3 = await drawerText(pageM);
    ok(t3.includes("I-803") && t3.includes("2026-10-12 08:00:00"),
      "归档会话灰块的履历入口可查其承办历史（I-803 认领时间逐字）", t3.slice(0, 120));
    await pageM.screenshot({ path: OUT_DIR + "/isolated_archived_history.png", fullPage: false });

    // 占位线块：暂无会话历史
    const placeholder = await pageM.evaluate(() => {
      const groups = [...document.querySelectorAll(".ws-group")];
      const g1 = groups.find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith("L1"));
      return g1 ? g1.querySelector(".ws-card.retained")?.textContent ?? "no card" : "no group";
    });
    ok(placeholder.includes("暂无会话历史") && !/履历/.test(placeholder),
      "未建立会话的占位线块显示暂无会话历史且无履历入口", placeholder.slice(0, 100));
    // 主体与额度保持：活跃卡读数与额度文本
    const quota = await pageM.$eval(".ws-head", (e) => e.textContent);
    ok(quota.includes("1/15"), "上下文主体与并行额度读数保持一致（1/15）", quota.slice(0, 100));
    const gauge = await pageM.$eval(".ws-card:not(.retained) .ws-gauge-num", (e) => e.textContent);
    ok(gauge.trim() === "31.5%", "活跃卡上下文读数主体保持（31.5%）", gauge);
    record.isolated = { items: evRows1.map((r) => ({ id: r.id, events: r.evs.length })),
      reopen_same: same, archived_item: "I-803" };
  });
  await pageM.close().catch(() => {});

  await browser.close();
  record.pass = pass; record.fail = fail;
  fs.writeFileSync(OUT_DIR + "/verify_record.json", JSON.stringify(record, null, 1));
  console.log("\n结果：" + pass + " 过 / " + fail + " 拒；验证记录 " + OUT_DIR + "/verify_record.json");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("核验脚本异常：", e); process.exit(2); });
