/* i189_check.cjs — I-189 判据核验：观察窗十条业务线常驻与灰态保留。
 * 两类验证：
 *   隔离样例——puppeteer 请求拦截注入 /api/console/state 三种场景
 *   （无活跃、部分活跃、全部活跃），断言页面结构、灰块、读数、额度；
 *   真实读回——放行真实接口，断言常驻结构、额度一致、归档与出处能力。
 * 验证记录与截图写入 logs/i189_check/。不伪造：隔离样例数据全部标注
 * mock 前缀，真实场景只断言接口真实值。 */
const puppeteer = require("puppeteer-core");
const https = require("https");
const fs = require("fs");
const path = require("path");

const SECRET = JSON.parse(fs.readFileSync(
  "G:/Playground/isaac_flapper_sim/lp_sync/cloud_secret.json", "utf-8"));
const ROOT = "G:/Playground/isaac_flapper_sim";
const HOST = "personal.karmicsword.com";
const OUT_DIR = path.join(ROOT, "logs", "i189_check");
fs.mkdirSync(OUT_DIR, { recursive: true });

let pass = 0, fail = 0;
const record = { generated_at: new Date().toISOString(), scenarios: [], real: null };
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

/* ── 样例构造：lanes 取真实接口（十条业务线名称真实），sessions 与
 * footer 按场景注入，mock 会话号全部带 mock_i189 前缀以示隔离。 ── */
const NOW = new Date().toISOString().slice(0, 19);
function mockSource(sid) {
  return { kind: "表", locus: "console_sessions/" + sid,
           excerpt: "隔离样例归档原因（mock_i189）", sampled_at: NOW };
}
function mockCard(lane, pct, sid, archived) {
  return {
    session_id: sid || ("sess_mock_i189_" + lane.replace("L", "l") + "x1a2b3c4d5e"),
    lane, item_id: archived ? "I-166" : "I-189",
    status: archived ? "已归档" : "在干活",
    context_pct: archived ? null : pct,
    last_activity_at: NOW,
    updated_at: NOW,
    source: mockSource(sid || ("sess_mock_i189_" + lane.replace("L", "l") + "x1a2b3c4d5e")),
  };
}
function scenarioState(realLanes, activeByLane, archivedByLane, parallel) {
  const active = [], archived = [];
  for (const [lane, pct] of Object.entries(activeByLane)) if (pct != null) active.push(mockCard(lane, pct));
  for (const [lane, n] of Object.entries(archivedByLane || {})) if (n) archived.push(mockCard(lane, null, null, true));
  return { goals: [], lanes: realLanes, sessions: { active, archived, owner: [] },
           footer: { parallel, sampled_at: NOW,
                     source: mockSource("footer") } };
}

async function withMockState(page, stateBody, fn) {
  await page.setRequestInterception(true);
  const handler = (rq) => {
    if (rq.url().includes("/api/console/state")) {
      rq.respond({ status: 200, contentType: "application/json",
        headers: { Date: new Date().toUTCString() },
        body: JSON.stringify({ ok: true, result: stateBody }) });
    } else if (rq.url().includes("/api/console/config")) {
      rq.respond({ status: 200, contentType: "application/json",
        headers: { Date: new Date().toUTCString() },
        body: JSON.stringify({ ok: true, result: { handoff_mode: "auto", gate_pct: 70, parallel_limit: 15 } }) });
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

  const browser = await puppeteer.launch({
    executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    headless: true, args: ["--no-sandbox"],
    defaultViewport: { width: 1600, height: 1000 },
  });
  async function newPage() {
    const p = await browser.newPage();
    await p.setCacheEnabled(false);   // 破除同 URL fetch 磁盘缓存（防 mock 被缓存绕过）
    await p.setCookie({ name: "lp_session", value: cookieVal, domain: HOST, path: "/" });
    return p;
  }
  async function openWatch(page) {
    await page.goto("https://" + HOST + "/app/#/console", { waitUntil: "networkidle2", timeout: 60000 });
    await page.waitForSelector(".console-tabs", { timeout: 30000 });
    await page.evaluate(() => {
      const btn = [...document.querySelectorAll(".console-tab")]
        .find((b) => b.textContent.includes("观察窗"));
      if (btn) btn.click();
    });
    await page.waitForSelector(".ws-group", { timeout: 15000 });
    await new Promise((r) => setTimeout(r, 1500));
  }

  // 真实 lanes 骨架（十条业务线名称取真实接口，样例只动 sessions 与额度）
  const realState = await req("GET", "/api/console/state", null, cookie);
  const realLanes = realState.json.result.lanes;
  record.real_state_footer = realState.json.result.footer;
  record.real_lanes = realLanes.map((l) => ({ id: l.id, name: l.name }));
  const biz = realLanes.filter((l) => /^L\d+$/.test(l.id));
  ok(biz.length === 10, "业务线骨架 L1 至 L10 共 10 条（接口 lanes）", String(biz.length));

  async function structure(page) {
    return page.evaluate(() => {
      const groups = [...document.querySelectorAll(".ws-group")].map((g) => ({
        head: g.querySelector(".ws-group-lane")?.textContent ?? "",
        count: g.querySelector(".ws-group-count")?.textContent ?? "",
        retained: g.querySelectorAll(".ws-card.retained").length,
        active: g.querySelectorAll(".ws-card:not(.retained)").length,
        gauge: g.querySelectorAll(".ws-gauge").length,
        name: g.querySelector(".ws-card .ws-item")?.textContent ?? "",
      }));
      return { groups,
        quota: document.querySelector(".ws-head")?.textContent ?? "",
        archive: document.querySelector(".ws-archive")?.textContent ?? "" };
    });
  }

  console.log("场景 A：隔离样例——全部业务线无活跃会话（无归档记录）");
  const pageA = await newPage();
  await withMockState(pageA, scenarioState(realLanes, {}, {}, { active: 0, tracked: 0, limit: 15 }), async () => {
    await openWatch(pageA);
    const s = await structure(pageA);
    ok(s.groups.length === 10, "全部路线视图包含 10 个业务线区域", String(s.groups.length));
    const allNamed = s.groups.every((g) => /^L\d+ .+/.test(g.head));
    ok(allNamed, "每条业务线名称可见（组头含编号与名称）", JSON.stringify(s.groups.map((g) => g.head)));
    const allRetained = s.groups.every((g) => g.retained === 1 && g.active === 0 && g.gauge === 1);
    ok(allRetained, "每条线一个灰色会话线块带上下文进度条（无活跃卡）");
    const missing = await pageA.$$eval(".ws-card.retained", (els) => els.map((e) => e.textContent));
    ok(missing.every((t) => t.includes("暂无读数") && t.includes("无活跃会话")),
      "无记录线显示明确缺失态（暂无读数、无活跃会话），不编造标识");
    ok(missing.every((t) => !/注入|停止/.test(t)), "占位线块不具备可执行的会话动作");
    ok(s.quota.includes("0/15"), "并行额度显示 0/15（灰块不增加活跃数）", s.quota.slice(0, 120));
    await pageA.screenshot({ path: OUT_DIR + "/scene_a_none_active.png", fullPage: true });
    record.scenarios.push({ scene: "A_none_active", groups: s.groups.length,
      retained: s.groups.reduce((a, g) => a + g.retained, 0), quota: "0/15" });
  });
  await pageA.close().catch(() => {});

  console.log("场景 B：隔离样例——部分路线活跃（L5 活跃带读数，L1 有归档驻留，其余无）");
  const pageB = await newPage();
  await withMockState(pageB, scenarioState(realLanes,
    { L5: 42.3 }, { L1: 1 }, { active: 1, tracked: 1, limit: 15 }), async () => {
    await openWatch(pageB);
    const s = await structure(pageB);
    ok(s.groups.length === 10, "全部路线视图仍含 10 个业务线区域", String(s.groups.length));
    const l5 = s.groups.find((g) => g.head.startsWith("L5"));
    ok(l5 && l5.active === 1 && l5.retained === 0, "L5 活跃卡在线（无灰块）", JSON.stringify(l5));
    const pctText = await pageB.$eval(".ws-card:not(.retained) .ws-gauge-num", (e) => e.textContent);
    ok(pctText.trim() === "42.3%", "活跃会话显示真实读数 42.3%", pctText);
    const l1 = s.groups.find((g) => g.head.startsWith("L1"));
    ok(l1 && l1.retained === 1, "L1 无活跃仍有灰块（有归档驻留记录）", JSON.stringify(l1));
    const l1Text = await pageB.$eval(".ws-group:nth-child(1) .ws-card.retained", (e) => e.textContent);
    ok(l1Text.includes("sess_mock_i189") && l1Text.includes("采样") && l1Text.includes("暂无读数"),
      "归档灰块显示真实会话标识、采样时刻、读数缺失态", l1Text.slice(0, 140));
    const l1Btns = await pageB.$$eval(".ws-group:nth-child(1) .ws-card.retained .ws-actions .ws-btn", (els) => els.map((e) => e.textContent));
    ok(l1Btns.length === 1 && l1Btns[0] === "出处", "归档灰块仅保留出处能力（无注入停止）", JSON.stringify(l1Btns));
    const others = s.groups.filter((g) => !["L1", "L5"].includes(g.head.split(" ")[0]));
    ok(others.length === 8 && others.every((g) => g.retained === 1),
      "其余 8 条线灰块常驻", JSON.stringify(others.map((g) => g.head)));
    ok(s.quota.includes("1/15"), "并行额度 1/15（仅活跃会话计数）", s.quota.slice(0, 120));
    // 出处抽屉可用（归档灰块）
    await pageB.click(".ws-group:nth-child(1) .ws-card.retained .ws-actions .ws-btn");
    await new Promise((r) => setTimeout(r, 350));
    const drawer = await pageB.$eval(".src-drawer", (e) => e.textContent).catch(() => "");
    ok(drawer.includes("console_sessions/") && drawer.includes("摘录"), "灰块出处抽屉可开（归档出处能力继续可用）", drawer.slice(0, 80));
    await pageB.click(".src-drawer .lab-btn-minor").catch(() => {});
    await new Promise((r) => setTimeout(r, 250));
    // 判据五：单路线筛选后非活跃线块可见
    await pageB.select(".ws-filter select", "L9");
    await new Promise((r) => setTimeout(r, 500));
    const s9 = await structure(pageB);
    ok(s9.groups.length === 1 && s9.groups[0].head.startsWith("L9") && s9.groups[0].retained === 1,
      "选择 L9 后该线区域与灰块可见", JSON.stringify(s9.groups));
    await pageB.select(".ws-filter select", "全部");
    await new Promise((r) => setTimeout(r, 400));
    await pageB.screenshot({ path: OUT_DIR + "/scene_b_partial.png", fullPage: true });
    record.scenarios.push({ scene: "B_partial", groups: s.groups.length,
      active_cards: 1, retained: s.groups.reduce((a, g) => a + g.retained, 0), quota: "1/15" });
  });

  console.log("场景 C：隔离样例——全部路线活跃");
  const allActive = {}; biz.forEach((l, i) => { allActive[l.id] = 5 + i * 8; });
  const pageC = await newPage();
  await withMockState(pageC, scenarioState(realLanes, allActive, {},
    { active: 10, tracked: 10, limit: 15 }), async () => {
    await openWatch(pageC);
    const s = await structure(pageC);
    ok(s.groups.length === 10, "全部路线视图含 10 个业务线区域", String(s.groups.length));
    ok(s.groups.every((g) => g.active >= 1 && g.retained === 0),
      "十条线全部活跃卡、无灰块", JSON.stringify(s.groups.map((g) => g.active)));
    ok(s.quota.includes("10/15"), "并行额度 10/15", s.quota.slice(0, 120));
    await pageC.screenshot({ path: OUT_DIR + "/scene_c_all_active.png", fullPage: true });
    record.scenarios.push({ scene: "C_all_active", groups: s.groups.length, quota: "10/15" });
  });

  await pageC.close().catch(() => {});

  console.log("真实读回：云端实测（不拦截，全部真实接口）");
  const pageR = await newPage();
  await openWatch(pageR);
  const rs = await structure(pageR);
  ok(rs.groups.length >= 10, "真实全部路线视图含至少 10 个业务线区域", String(rs.groups.length));
  const bizHeads = rs.groups.filter((g) => /^L\d+ /.test(g.head));
  ok(bizHeads.length === 10, "真实十条业务线全部可见且名称可见", String(bizHeads.length));
  const eachTile = bizHeads.every((g) => g.active + g.retained > 0 && g.gauge >= 1);
  ok(eachTile, "每条线有会话卡或灰色线块，且含上下文进度条");
  const realFooter = realState.json.result.footer.parallel;
  ok(rs.quota.includes(realFooter.active + "/" + realFooter.limit),
    "页面额度读数与真实状态接口一致（" + realFooter.active + "/" + realFooter.limit + "）",
    rs.quota.slice(0, 120));
  const realActiveLanes = new Set(realState.json.result.sessions.active.map((s) => s.lane));
  const retainedLanes = bizHeads.filter((g) => g.retained > 0).map((g) => g.head.split(" ")[0]);
  ok(retainedLanes.every((l) => !realActiveLanes.has(l)),
    "灰块只出现在无活跃会话的线（不与活跃卡并存）", JSON.stringify(retainedLanes));
  ok(rs.archive.includes("归档抽屉"), "归档抽屉继续可用", rs.archive.slice(0, 60));
  await pageR.screenshot({ path: OUT_DIR + "/real_readback.png", fullPage: true });
  record.real = {
    groups: rs.groups.length, biz_groups: bizHeads.length,
    retained_lanes: retainedLanes, active_lanes: [...realActiveLanes],
    footer_parallel: realFooter,
    retained_sources: realState.json.result.sessions.archived.map((s) => ({
      session_id: s.session_id, lane: s.lane, context_pct: s.context_pct,
      has_source: !!s.source })),
  };
  ok(record.real.retained_sources.every((s) => s.context_pct == null),
    "灰块会话来源核对：归档行接口不带上下文读数，页面按缺失态显示（不编造）");

  await browser.close();
  record.pass = pass; record.fail = fail;
  fs.writeFileSync(OUT_DIR + "/verify_record.json", JSON.stringify(record, null, 1));
  console.log("\n结果：" + pass + " 过 / " + fail + " 拒；验证记录 " + OUT_DIR + "/verify_record.json");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("核验脚本异常：", e); process.exit(2); });
