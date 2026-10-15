/* i218v2_real_page_check.cjs — I-218 v2 真实联调页面检查（阶段一/二）。
 * 输入：page_check_input.json（探针写入：代理端口、代理签到令牌、
 * 隔离内核状态根、隔离云端口、各任务会话与工位真实值）。
 * 阶段一（A/B 双工位执行中）：
 *   P1 页面加载观察窗，L3 组头与真实快照一致（执行二比二等待一）；
 *   P2 两张活跃卡逐卡对应：data-session-id 全等于内核在册会话，
 *      工位芯片、事项号与内核 tasks 逐项一致；
 *   P3 真实回读三方一致：内核 lane_queues.json = 云端
 *      GET /api/agent/lane_queues = 页面显示（工位会话集、上限、
 *      活跃数、等待数、等待对象键）；
 *   P4 注入 L3-w1：弹层完整会话号、确认后云端 console_tasks 落行
 *      且只指向该会话（另一工位零行）；
 *   P5 停止 L3-w2：同上，请求级完整会话号；
 *   P6 履历抽屉：完整会话号与真实认领事件（会话+工位在 detail）；
 *   P7 出处抽屉：locus 为该会话真实采集指针。
 * 阶段二（D 活跃 + 另一工位驻留灰卡并存）：
 *   P8 活跃工位卡与驻留灰卡并存，灰卡完整会话号与最后事项。
 * 截图与结果写回 OUT/page_check_result.json。只读生产零接触。 */
const fs = require("fs");
const https = require("https");
const puppeteer = require("puppeteer-core");

const INPUT = process.argv[2];
const PHASE = process.argv[3] || "1";
const input = JSON.parse(fs.readFileSync(INPUT, "utf8"));
const OUT = input.out_dir;
let pass = 0, fail = 0;
const checks = [];
function ok(cond, name, detail) {
  checks.push({ name, ok: !!cond, detail: String(detail ?? "").slice(0, 300) });
  if (cond) { pass++; console.log("  [过] " + name); }
  else { fail++; console.log("  [拒] " + name + "：" + String(detail ?? "")); }
  return !!cond;
}
function cloudApi(method, path, body) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const req = https.request({ hostname: "127.0.0.1", port: input.cloud_port,
      method, path, rejectUnauthorized: false,
      headers: { "Content-Type": "application/json",
        ...(payload ? { "Content-Length": Buffer.byteLength(payload) } : {}),
        ...(input.token ? { Authorization: "Bearer " + input.token } : {}) } },
      (res) => { let buf = "";
        res.on("data", (c) => { buf += c; });
        res.on("end", () => { try { resolve(JSON.parse(buf)); }
          catch (e) { reject(new Error("bad json: " + buf.slice(0, 120))); } }); });
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function openConsole(browser) {
  const p = await browser.newPage();
  await p.setCacheEnabled(false);
  await p.setRequestInterception(true);
  p.on("request", (rq) => {
    const url = rq.url();
    if (url.includes("/api/")) {
      const headers = { ...rq.headers() };
      if (input.token) headers.Authorization = "Bearer " + input.token;
      rq.continue({ headers });
    } else rq.continue();
  });
  await p.goto("http://127.0.0.1:" + input.proxy_port + "/app/#/console",
    { waitUntil: "networkidle2", timeout: 30000 });
  await p.waitForSelector(".console-tabs");
  await p.evaluate(() => {
    const btn = [...document.querySelectorAll(".console-tab")]
      .find((b) => b.textContent.includes("观察窗"));
    if (btn) btn.click();
  });
  await p.waitForSelector(".ws-group", { timeout: 15000 });
  await sleep(800);
  return p;
}
const laneProbe = (p, lane) => p.evaluate((lane) => {
  const g = [...document.querySelectorAll(".ws-group")]
    .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith(lane));
  if (!g) return null;
  return {
    count: g.querySelector(".ws-group-count")?.textContent ?? "",
    cards: [...g.querySelectorAll(".ws-card:not(.retained)")].map((c) => ({
      worker: c.querySelector(".ws-worker-chip")?.textContent ?? "",
      item: c.querySelector(".ws-item-id")?.textContent ?? "",
      sid: c.getAttribute("data-session-id") ?? "",
      status: c.querySelector(".ws-status")?.textContent ?? "",
    })),
    retained: [...g.querySelectorAll(".ws-card.retained")].map((c) => ({
      sid: c.getAttribute("data-session-id") ?? "",
      note: c.querySelector(".ws-retained-note")?.textContent ?? "",
    })),
  };
}, lane);

async function cardButton(p, sid, label) {
  return p.evaluate(({ sid, label }) => {
    const card = [...document.querySelectorAll(".ws-card:not(.retained)")]
      .find((c) => c.getAttribute("data-session-id") === sid);
    if (!card) return "no card";
    const btn = [...card.querySelectorAll(".ws-actions .ws-btn")]
      .find((b) => b.textContent === label);
    if (!btn) return "no btn";
    btn.click();
    return "clicked";
  }, { sid, label });
}

(async () => {
  const result = { phase: PHASE, started_at: new Date().toISOString(), checks };
  const browser = await puppeteer.launch({
    executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    headless: true, args: ["--no-sandbox"], defaultViewport: { width: 1600, height: 1000 },
  });
  try {
    if (PHASE === "1") {
      const A = input.tasks.A, B = input.tasks.B, C = input.tasks.C;
      // 等云端镜像快照追上内核（心跳周期三秒）。
      let cloudQ = null;
      for (let i = 0; i < 20; i++) {
        const r = await cloudApi("GET", "/api/agent/lane_queues");
        cloudQ = r?.result?.queues?.L3;
        if (cloudQ && (cloudQ.workers?.length ?? 0) === 2) break;
        await sleep(3000);
      }
      const kernelQ = JSON.parse(fs.readFileSync(
        input.ks_dir + "/lane_queues.json", "utf8")).L3;
      ok(!!cloudQ && (cloudQ.workers?.length ?? 0) === 2,
        "P0 云端镜像快照已追到双工位执行态", JSON.stringify(cloudQ?.workers?.length));

      const p = await openConsole(browser);
      // 防御：双卡到位再核对（采集五秒周期 + 页面三秒轮询）。
      let g = null;
      for (let i = 0; i < 15; i++) {
        g = await laneProbe(p, "L3");
        if (g && g.cards.length === 2) break;
        await sleep(3000);
      }
      // P1 组头计数与真实快照一致
      ok(g && g.count.includes("执行 2/2") && g.count.includes("等待 " + kernelQ.waiting_count),
        "P1 组头与真实快照一致（执行二比二、等待" + kernelQ.waiting_count + "）",
        JSON.stringify({ count: g?.count, kernel: { limit: kernelQ.concurrency_limit,
          wait: kernelQ.waiting_count } }));
      // P2 逐卡对应
      const bySid = new Map((kernelQ.workers || []).map((w) => [w.session_id, w]));
      const cardSids = new Set(g?.cards.map((c) => c.sid));
      ok(g && g.cards.length === 2
        && cardSids.size === 2
        && [...cardSids].every((s) => bySid.has(s))
        && g.cards.every((c) => c.worker.includes(bySid.get(c.sid)?.worker_id ?? "?")
          && c.item === bySid.get(c.sid)?.item_id),
        "P2 两张活跃卡逐卡对应内核在册（完整会话号集合相等、工位号与事项号逐项一致）",
        JSON.stringify({ kernel_workers: kernelQ.workers, page_cards: g?.cards }));
      // P3 三方一致
      const cloudSids = new Set((cloudQ?.workers || []).map((w) => w.session_id));
      const kernelSids = new Set((kernelQ.workers || []).map((w) => w.session_id));
      ok(kernelSids.size === 2 && cloudSids.size === 2
        && [...kernelSids].every((s) => cloudSids.has(s))
        && [...kernelSids].every((s) => cardSids.has(s))
        && cloudQ.concurrency_limit === kernelQ.concurrency_limit
        && cloudQ.active_count === kernelQ.active_count
        && cloudQ.waiting_count === kernelQ.waiting_count
        && Object.keys(cloudQ.waiting || {}).join(",") === Object.keys(kernelQ.waiting || {}).join(","),
        "P3 真实回读三方一致（内核快照=云端接口=页面卡集；上限/活跃/等待数/等待键逐项相等）",
        JSON.stringify({ kernel: { limit: kernelQ.concurrency_limit, act: kernelQ.active_count,
          wait: kernelQ.waiting_count, keys: Object.keys(kernelQ.waiting || {}) },
          cloud: { limit: cloudQ?.concurrency_limit, act: cloudQ?.active_count,
            wait: cloudQ?.waiting_count, keys: Object.keys(cloudQ?.waiting || {}) } }));
      await p.screenshot({ path: OUT + "/real_phase1_dual_cards.png", fullPage: false });

      // P4 注入 A（该工位会话）
      const inj = await cardButton(p, A.sid, "注入");
      await sleep(500);
      const modalA = await p.evaluate(() => {
        const m = document.querySelector(".ws-modal");
        return m ? { sid: m.getAttribute("data-session-id"), action: m.getAttribute("data-action") } : null;
      });
      ok(inj === "clicked" && modalA?.sid === A.sid && modalA?.action === "inject"
        && modalA.sid !== B.sid,
        "P4a 注入弹层定向 A 工位会话（完整会话号全等，非 B）", JSON.stringify(modalA));
      await p.screenshot({ path: OUT + "/real_phase1_inject_modal.png", fullPage: false });
      await p.evaluate(() => {
        const btn = [...document.querySelectorAll(".ws-modal .ws-btn.primary")]
          .find((b) => b.textContent.includes("确认执行"));
        if (btn) btn.click();
      });
      await sleep(800);
      let tasks = (await cloudApi("GET", "/api/console/tasks")).result;
      let injRows = tasks.filter((t) => t.query_kind === "action" && t.session_id === A.sid);
      ok(injRows.length === 1 && injRows[0].status === "pending"
        && !tasks.some((t) => t.query_kind === "action" && t.session_id === B.sid),
        "P4b 云端 console_tasks 落行只指向 A 会话（另一工位零行）",
        JSON.stringify(tasks.filter((t) => t.query_kind === "action").map((t) => t.session_id)));
      await p.evaluate(() => {
        const close = [...document.querySelectorAll(".ws-modal .ws-btn")]
          .find((b) => b.textContent === "关闭");
        if (close) close.click();
      }).catch(() => {});

      // P5 停止 B（该工位会话；只核对落点，确认执行形成真实任务行）
      const stp = await cardButton(p, B.sid, "停止");
      await sleep(500);
      const modalB = await p.evaluate(() => {
        const m = document.querySelector(".ws-modal");
        return m ? { sid: m.getAttribute("data-session-id"), action: m.getAttribute("data-action") } : null;
      });
      ok(stp === "clicked" && modalB?.sid === B.sid && modalB?.action === "stop"
        && modalB.sid !== A.sid,
        "P5a 停止弹层定向 B 工位会话（完整会话号全等）", JSON.stringify(modalB));
      await p.evaluate(() => {
        const btn = [...document.querySelectorAll(".ws-modal .ws-btn.primary")]
          .find((b) => b.textContent.includes("确认执行"));
        if (btn) btn.click();
      });
      await sleep(800);
      tasks = (await cloudApi("GET", "/api/console/tasks")).result;
      const stopRows = tasks.filter((t) => t.query_kind === "action" && t.session_id === B.sid);
      const actRows = tasks.filter((t) => t.query_kind === "action");
      ok(stopRows.length === 1 && actRows.length === 2
        && actRows.every((t) => [A.sid, B.sid].includes(t.session_id)),
        "P5b 云端动作任务恰两行且只各指向本工位会话（误作用零）",
        JSON.stringify(actRows.map((t) => ({ sid: t.session_id, st: t.status }))));
      await p.evaluate(() => {
        const close = [...document.querySelectorAll(".ws-modal .ws-btn")]
          .find((b) => b.textContent === "关闭");
        if (close) close.click();
      }).catch(() => {});

      // P6 履历抽屉（A 会话：真实认领事件含会话+工位）
      const hist = await cardButton(p, A.sid, "履历");
      await sleep(900);
      const drawer = await p.evaluate(() => ({
        sid: document.querySelector(".hist-full-sid")?.textContent ?? "",
        items: [...document.querySelectorAll(".hist-item .ov-id")].map((x) => x.textContent),
      }));
      ok(hist === "clicked" && drawer.sid === A.sid && drawer.items.includes(A.item),
        "P6 履历抽屉完整会话号为 A，真实认领事项在列", JSON.stringify(drawer));
      await p.screenshot({ path: OUT + "/real_phase1_history_drawer.png", fullPage: false });
      await p.evaluate(() => {
        const btn = [...document.querySelectorAll(".hist-drawer .lab-btn-minor")]
          .find((b) => b.textContent === "关闭");
        if (btn) btn.click();
      }).catch(() => {});

      // P7 出处抽屉（B 会话：真实采集指针 locus）
      const src = await cardButton(p, B.sid, "出处");
      await sleep(600);
      const srcDrawer = await p.evaluate(() => ({
        locus: document.querySelector(".src-drawer .src-drawer-locus")?.textContent ?? "",
      }));
      ok(src === "clicked" && srcDrawer.locus === "console_sessions/" + B.sid,
        "P7 出处抽屉 locus 为 B 会话真实行指针（state 接口统一改写为 console_sessions/<会话号>）",
        srcDrawer.locus);
      await p.screenshot({ path: OUT + "/real_phase1_source_drawer.png", fullPage: false });
      await p.evaluate(() => {
        const btn = [...document.querySelectorAll(".src-drawer .lab-btn-minor")]
          .find((b) => b.textContent === "关闭");
        if (btn) btn.click();
      }).catch(() => {});
      await p.close().catch(() => {});
    } else {
      // 阶段二：D 活跃 + 另一工位驻留灰卡并存
      const D = input.tasks.D, S = input.tasks.stale;
      const p = await openConsole(browser);
      const g = await laneProbe(p, "L3");
      ok(g && g.cards.some((c) => c.sid === D.sid && c.worker.includes(D.worker)),
        "P8a 活跃工位 D 卡在页（完整会话号与工位号）", JSON.stringify(g?.cards));
      ok(g && g.retained.some((r) => r.sid === S.sid && r.note.includes(S.item)),
        "P8b 另一工位驻留灰卡并存（完整会话号与最后事项，不再丢失）",
        JSON.stringify(g?.retained));
      ok(g && g.count.includes("历史工位灰卡 1"),
        "P8c 组头如实计入历史灰卡", g?.count);
      await p.screenshot({ path: OUT + "/real_phase2_active_plus_retained.png", fullPage: false });
      await p.close().catch(() => {});
    }
  } finally {
    await browser.close().catch(() => {});
  }
  result.finished_at = new Date().toISOString();
  result.summary = { pass, fail };
  fs.writeFileSync(OUT + "/page_check_result_phase" + PHASE + ".json",
    JSON.stringify(result, null, 1), "utf8");
  console.log("阶段" + PHASE + "：" + pass + " 过 / " + fail + " 拒");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => {
  console.error("页面检查异常：", e);
  fs.writeFileSync(OUT + "/page_check_result_phase" + PHASE + ".json",
    JSON.stringify({ phase: PHASE, error: String(e), summary: { pass, fail } }, null, 1), "utf8");
  process.exit(2);
});
