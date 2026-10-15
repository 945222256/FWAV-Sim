/* i207_check.cjs — I-207 判据核验：编号显示与超时会话身份保留。
 * 六条判据：编号四样例（当前认领、空闲最近承办、无事项、旧派发冲突）、
 * 认领核实与真实 L3 对照、保留五样例（活跃、空闲超时、归档、无记录、
 * 接口失败）、驻留来源与恢复、额度口径三混合样例、真实读回截图与
 * I-189/I-192/I-193 回归。证据写 logs/i207_check/。 */
const puppeteer = require("puppeteer-core");
const https = require("https");
const fs = require("fs");
const path = require("path");

const SECRET = JSON.parse(fs.readFileSync(
  "G:/Playground/isaac_flapper_sim/lp_sync/cloud_secret.json", "utf-8"));
const ROOT = "G:/Playground/isaac_flapper_sim";
const HOST = "personal.karmicsword.com";
const OUT_DIR = path.join(ROOT, "logs", "i207_check");
fs.mkdirSync(OUT_DIR, { recursive: true });

let pass = 0, fail = 0;
const record = { generated_at: new Date().toISOString() };
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
const NOW = "2026-10-13T11:40:00";
function mockCard(sid, lane, cur, reg, pct, updated) {
  return { session_id: sid, lane, item_id: reg, current_item_id: cur,
    status: "在干活", context_pct: pct, last_activity_at: updated,
    updated_at: updated,
    source: { kind: "表", locus: "console_sessions/" + sid,
              excerpt: "隔离样例（mock_i207）", sampled_at: updated } };
}
function mockState(realLanes, active, retained, archived, parallel) {
  return { goals: [], lanes: realLanes,
    sessions: { active, retained, archived, owner: [] },
    footer: { parallel, sampled_at: NOW,
              source: { kind: "表", locus: "x", excerpt: "x", sampled_at: NOW } } };
}
async function withMocks(page, stateBody, mode, fn) {
  await page.setRequestInterception(true);
  const handler = (rq) => {
    const url = rq.url();
    const reply = (result) => rq.respond({ status: 200, contentType: "application/json",
      headers: { Date: new Date().toUTCString() },
      body: JSON.stringify({ ok: true, result }) });
    if (url.includes("/api/console/state")) {
      if (mode === "statefail") rq.respond({ status: 500, contentType: "application/json",
        headers: { Date: new Date().toUTCString() }, body: '{"ok":false}' });
      else reply(stateBody);
    } else if (url.includes("/api/console/config"))
      reply({ handoff_mode: "auto", gate_pct: 70, parallel_limit: 15 });
    else if (url.includes("/api/events")) {
      if (mode === "eventsfail") rq.respond({ status: 500, contentType: "application/json",
        headers: { Date: new Date().toUTCString() }, body: '{"ok":false}' });
      else reply([
        { seq: 301, ts: "2026-10-12T09:00:00", action: "claim", item_id: "I-801", actor: "L9队",
          detail: "认领，租约 4 小时，会话 sess_mock_i207_r9_aaaabbbbcccc" },
        { seq: 302, ts: "2026-10-13T10:00:00", action: "claim", item_id: "I-802", actor: "L9队",
          detail: "认领，租约 4 小时，会话 sess_mock_i207_r9_aaaabbbbcccc" },
      ]);
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
  const st = realState.json.result;
  const events = await req("GET", "/api/events", null, cookie);
  const evs = events.json.result;

  console.log("接口面（判据二、五前置）");
  const l3 = st.sessions.active.find((s) => s.session_id.startsWith("sess_bd527dd6"));
  const l3claims = evs.filter((e) => e.action === "claim"
    && (e.detail || "").includes("sess_bd527dd6"));
  const l3Latest = l3claims[l3claims.length - 1];
  ok(l3 && l3.current_item_id === l3Latest.item_id,
    "L3 会话 current_item_id 与认领事件最近一条逐字一致（" + l3Latest.item_id + "）",
    JSON.stringify({ api: l3?.current_item_id, event: l3Latest.item_id }));
  ok(l3.item_id !== l3.current_item_id,
    "旧派发登记（" + l3.item_id + "）未覆盖新认领（真实冲突在案）");
  const hasRetained = st.sessions.retained.some((s) => s.lane === "L7" && s.context_pct != null);
  ok(hasRetained, "retained 节含真实超时未归档行（L7 保留读数在册）",
    JSON.stringify(st.sessions.retained.filter((s) => s.lane === "L7").map((s) => [s.context_pct, s.updated_at])));
  ok(st.footer.parallel.active <= st.sessions.active.length,
    "额度实时口径不含 retained（active=" + st.footer.parallel.active + " retained=" + st.sessions.retained.length + "）");

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

  console.log("真实读回：编号显示与冲突表达（判据一、二）");
  const pageR = await newPage();
  await openWatch(pageR);
  const l3Card = await pageR.evaluate(() => {
    const g = [...document.querySelectorAll(".ws-group")]
      .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith("L3"));
    const card = g?.querySelector(".ws-card:not(.retained)");
    if (!card) return null;
    return {
      id: card.querySelector(".ws-item-id")?.textContent ?? null,
      title: card.querySelector(".ws-item")?.textContent ?? "",
      conflict: card.querySelector(".ws-conflict-note")?.textContent ?? null,
      status: card.querySelector(".ws-status")?.textContent ?? "",
    };
  });
  ok(l3Card && l3Card.id === l3Latest.item_id,
    "L3 活跃卡头固定显示当前认领编号（" + l3Latest.item_id + "，编号独立元素不随名称截断）",
    JSON.stringify(l3Card));
  ok(l3Card && l3Card.conflict && l3Card.conflict.includes(l3.item_id),
    "旧派发冲突显式说明（派发登记 " + l3.item_id + " 标为历史挂接）", l3Card?.conflict ?? "");
  await pageR.screenshot({ path: OUT_DIR + "/real_l3_card.png", fullPage: false });

  console.log("真实读回：L7 超时保留灰态（判据三、四）");
  const l7retained = st.sessions.retained.find((s) => s.lane === "L7" && s.context_pct != null);
  const l7Tile = await pageR.evaluate(() => {
    const g = [...document.querySelectorAll(".ws-group")]
      .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith("L7"));
    const card = g?.querySelector(".ws-card.retained");
    if (!card) return null;
    return {
      text: card.textContent,
      sid: card.querySelector(".ws-sid-click")?.textContent ?? "",
      gauge: card.querySelector(".ws-gauge-num")?.textContent ?? "",
      hasHistory: [...card.querySelectorAll(".ws-btn")].some((b) => b.textContent === "履历"),
    };
  });
  ok(l7Tile && l7Tile.text.includes("空闲超时保留"),
    "L7 超时灰块表达为空闲超时保留（不混同归档或从未建立）", l7Tile?.text.slice(0, 80));
  ok(l7Tile && l7Tile.sid.startsWith(l7retained.session_id.slice(0, 14)),
    "灰块保留原 session_id（" + l7retained.session_id.slice(0, 14) + "…）", l7Tile?.sid);
  ok(l7Tile && l7Tile.gauge.includes(l7retained.context_pct.toFixed(1)) && l7Tile.gauge.includes("过期"),
    "灰块显示最近有效读数并标明过期（" + l7retained.context_pct + "%）", l7Tile?.gauge);
  ok(l7Tile && l7Tile.text.includes("采样"),
    "灰块显示采样时刻（" + (l7retained.source?.sampled_at || "").slice(0, 19) + "）");
  ok(l7Tile && l7Tile.hasHistory, "灰块履历点击入口保留");
  await pageR.screenshot({ path: OUT_DIR + "/real_l7_retained.png", fullPage: false });

  console.log("真实读回：I-189/I-192/I-193 回归（判据六）");
  const groups = await pageR.evaluate(() =>
    [...document.querySelectorAll(".ws-group")].map((g) => ({
      head: g.querySelector(".ws-group-lane")?.textContent ?? "",
      cards: g.querySelectorAll(".ws-card").length })));
  ok(groups.filter((g) => /^L\d+ /.test(g.head)).length === 10,
    "I-189 回归：十条业务线常驻", String(groups.length));
  const histProbe = await pageR.evaluate(() => {
    const sid = document.querySelector(".ws-card.retained .ws-sid-click")
      || document.querySelector(".ws-card:not(.retained) .ws-sid-click");
    if (!sid) return "none";
    sid.click();
    return "clicked";
  });
  await pageR.waitForSelector(".hist-drawer", { timeout: 15000 }).catch(() => {});
  const histOk = await pageR.$(".hist-drawer") !== null;
  ok(histProbe === "clicked" && histOk, "I-192/I-193 回归：编号区点击直达履历");
  await pageR.close().catch(() => {});

  console.log("隔离样例一：编号四样例 + 保留三态（判据一、三）");
  const lanes = st.lanes;
  const pageM = await newPage();
  const s1 = mockState(lanes,
    [mockCard("sess_mock_i207_a1_x1a2b3c4d5e6", "L3", "I-901", "I-901", 31.0, NOW),       // 当前认领
      mockCard("sess_mock_i207_a2_x1a2b3c4d5e6", "L2", "I-902", "I-888", 44.0, NOW)],      // 旧派发冲突
    [mockCard("sess_mock_i207_r9_aaaabbbbcccc", "L9", "I-802", "I-091", 15.2, "2026-10-13T10:00:00")], // 空闲超时+冲突
    [],
    { active: 2, tracked: 2, limit: 15 });
  await withMocks(pageM, s1, "ok", async () => {
    await openWatch(pageM);
    const probe = await pageM.evaluate(() => {
      const cardOf = (lane) => {
        const g = [...document.querySelectorAll(".ws-group")]
          .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith(lane));
        const c = g?.querySelector(".ws-card:not(.retained)");
        return c ? {
          id: c.querySelector(".ws-item-id")?.textContent ?? null,
          conflict: c.querySelector(".ws-conflict-note")?.textContent ?? null } : null;
      };
      const retainedOf = (lane) => {
        const g = [...document.querySelectorAll(".ws-group")]
          .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith(lane));
        const c = g?.querySelector(".ws-card.retained");
        return c ? c.textContent : null;
      };
      return { l3: cardOf("L3"), l2: cardOf("L2"), l9: retainedOf("L9"),
               l1: retainedOf("L1"), quota: document.querySelector(".ws-head")?.textContent ?? "" };
    });
    ok(probe.l3 && probe.l3.id === "I-901" && !probe.l3.conflict,
      "样例一（当前认领）：编号固定显示且无冲突说明", JSON.stringify(probe.l3));
    ok(probe.l2 && probe.l2.id === "I-902" && probe.l2.conflict && probe.l2.conflict.includes("I-888"),
      "样例四（旧派发冲突）：显示当前认领 I-902 并注明历史挂接 I-888", JSON.stringify(probe.l2));
    ok(probe.l9 && probe.l9.includes("I-802") && probe.l9.includes("空闲超时保留")
       && probe.l9.includes("15.2%") && probe.l9.includes("过期"),
      "样例二（空闲最近承办）：灰块显示最近承办编号与过期读数", (probe.l9 || "").slice(0, 130));
    ok(probe.l1 && probe.l1.includes("暂无可核查的驻留记录"),
      "样例三之一（无记录）：占位明确表达", (probe.l1 || "").slice(0, 60));
    ok(probe.quota.includes("2/15"), "混合样例一额度只计二活跃（retained 不增数）", probe.quota.slice(0, 90));
    await pageM.screenshot({ path: OUT_DIR + "/isolated_sample1.png", fullPage: true });
    // 恢复：同 session 从 retained 变 active（重新工作）
  });
  const s2 = mockState(lanes,
    [mockCard("sess_mock_i207_r9_aaaabbbbcccc", "L9", "I-802", "I-091", 18.0, NOW)],
    [], [], { active: 1, tracked: 1, limit: 15 });
  await withMocks(pageM, s2, "ok", async () => {
    await openWatch(pageM);
    const recovered = await pageM.evaluate(() => {
      const g = [...document.querySelectorAll(".ws-group")]
        .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith("L9"));
      const c = g?.querySelector(".ws-card:not(.retained)");
      return c ? { id: c.querySelector(".ws-item-id")?.textContent, sid: c.querySelector(".ws-sid-click")?.textContent } : null;
    });
    ok(recovered && recovered.sid.startsWith("sess_mock_i207_r9_".slice(0, 14)),
      "判据四：同一 session 恢复活跃表达（原 L9 会话原编号回归活跃卡）", JSON.stringify(recovered));
    // 旧事项履历仍可查（I-801 旧认领在履历）
    await pageM.evaluate(() => {
      const sid = document.querySelector(".ws-card:not(.retained) .ws-sid-click");
      if (sid) sid.click();
    });
    await pageM.waitForSelector(".hist-drawer", { timeout: 15000 });
    await new Promise((r) => setTimeout(r, 800));
    const hist = await pageM.$eval(".hist-drawer", (e) => e.textContent);
    ok(hist.includes("I-801") && hist.includes("I-802"),
      "判据四：旧事项 I-801 履历仍可查（与新认领 I-802 并列）", hist.slice(0, 100));
    await pageM.screenshot({ path: OUT_DIR + "/isolated_recovered.png", fullPage: false });
  });
  await pageM.close().catch(() => {});

  console.log("隔离样例二、三：混合额度与失败态（判据三、五）");
  const pageF = await newPage();
  const s3 = mockState(lanes,
    [mockCard("sess_mock_i207_b1_x1a2b3c4d5e6", "L4", "I-903", null, 20.0, NOW)],
    [mockCard("sess_mock_i207_b2_x1a2b3c4d5e6", "L6", "I-904", "I-904", 66.0, "2026-10-13T10:00:00"),
      mockCard("sess_mock_i207_b3_x1a2b3c4d5e6", "L8", "I-905", "I-905", 12.0, "2026-10-13T09:00:00")],
    [mockCard("sess_mock_i207_b4_x1a2b3c4d5e6", "L7", "I-906", "I-906", null, "2026-10-12T09:00:00")],
    { active: 1, tracked: 1, limit: 15 });
  await withMocks(pageF, s3, "ok", async () => {
    await openWatch(pageF);
    const q = await pageF.evaluate(() => ({
      quota: document.querySelector(".ws-head")?.textContent ?? "",
      l7: [...document.querySelectorAll(".ws-group")].find((g) =>
        g.querySelector(".ws-group-lane")?.textContent.startsWith("L7"))
        ?.querySelector(".ws-card.retained")?.textContent ?? "none",
    }));
    ok(q.quota.includes("1/15"), "混合样例二（一活跃两超时一归档）额度只计一", q.quota.slice(0, 80));
    ok(q.l7.includes("已归档") && !q.l7.includes("空闲超时保留"),
      "显式归档样例：归档表达与超时保留区分", q.l7.slice(0, 90));
    await pageF.screenshot({ path: OUT_DIR + "/isolated_sample3.png", fullPage: true });
  });
  // 接口失败样例：全新页面验证首次加载失败路径（横幅不混同无会话）
  const pageS = await newPage();
  await withMocks(pageS, s3, "statefail", async () => {
    await pageS.goto("https://" + HOST + "/app/#/console", { waitUntil: "networkidle2", timeout: 60000 });
    await new Promise((r) => setTimeout(r, 1500));
    const failState = await pageS.evaluate(() => ({
      banner: !!document.querySelector(".console-error"),
      text: document.querySelector(".console-error")?.textContent ?? "",
    }));
    ok(failState.banner && failState.text.includes("不可达"),
      "接口失败样例：断连横幅明确表达通道不可达（不混同无会话）", JSON.stringify(failState));
    await pageS.screenshot({ path: OUT_DIR + "/isolated_statefail.png", fullPage: false });
  });
  await pageS.close().catch(() => {});
  await pageF.close().catch(() => {});

  record.real = {
    l3: { session: l3.session_id, registry_item: l3.item_id,
          current_item: l3.current_item_id, latest_claim: [l3Latest.item_id, l3Latest.ts],
          claims_count: l3claims.length },
    l7_retained: { session: l7retained.session_id, context_pct: l7retained.context_pct,
                   updated_at: l7retained.updated_at,
                   sampled_at: l7retained.source?.sampled_at },
    footer: st.footer.parallel, retained_count: st.sessions.retained.length,
  };
  record.pass = pass; record.fail = fail;
  fs.writeFileSync(OUT_DIR + "/verify_record.json", JSON.stringify(record, null, 1));
  await browser.close();
  console.log("\n结果：" + pass + " 过 / " + fail + " 拒；验证记录 " + OUT_DIR + "/verify_record.json");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("核验脚本异常：", e); process.exit(2); });
