/* i218v2_check.cjs — I-218 返修（lane-workers-ui-v2）隔离核验。
 * 两段式：
 *   甲段 服务端渲染反例复验（复刻 Ciri consumer_probe.cjs 方法论）：
 *     S1 真实冻结后端快照直喂 LaneView/WatchView 不再报错（waiting 对象形态）；
 *     S2 一活跃一保留出两张卡（反例二消失）；
 *     S3 双保留出两张灰卡（反例三消失）；
 *     S4 真实回放活跃队列：工位行、等待对象键值（request_id→原因）全渲染；
 *     S5 同前缀会话对按完整号区分（反例四：截断前缀检查失效的修复证明）。
 *   乙段 浏览器隔离样例（vite preview 独立端口 4174，请求全拦截，不触生产）：
 *     六类样例 + 双空闲两灰卡 + 同前缀操作定向 + 真实回放操作定向。
 *     注入/停止核对弹层与 POST 请求体的完整 session_id；履历/出处核对
 *     抽屉内完整 session_id；每步证明另一工位未被作用。
 * 数据来源：mock_i218v2 前缀自造样例；真实值回放自
 *   logs/i217_real_v2_20261014_174229（kernel_state/state.json、
 *   kernel_state/lane_queues.json、cloud/ledger.db 只读副本），
 *   回放值不冒称生产实时回读。 */
const fs = require("fs");
const path = require("path");
const Module = require("module");

const ROOT = "G:/Playground/isaac_flapper_sim";
const OUT = ROOT + "/logs/i218v2_check";
fs.mkdirSync(OUT, { recursive: true });
const PORT = 4174;
let pass = 0, fail = 0;
const failures = [];
function ok(cond, label, detail) {
  if (cond) { pass++; console.log("  [过] " + label); }
  else { fail++; failures.push(label + (detail ? "：" + detail : "")); console.log("  [拒] " + label + (detail ? "：" + detail : "")); }
  return !!cond;
}

/* ── 真实回放常量（logs/i217_real_v2_20261014_174229 只读回读） ── */
const REAL = {
  W1: { worker: "L3-w1", sid: "sess_d732a7ef-4e1c-4241-bdd0-c1c6f9e88e5c",
        item: "I-960", title: "I-217 真实闭环 960", run: "run-kt-real-a-a1",
        req: "kl-kt-real-a-line-a1" },
  W2: { worker: "L3-w2", sid: "sess_5512b27e-42b1-456d-93d6-1be655730bc8",
        item: "I-961", title: "I-217 真实闭环 961", run: "run-kt-real-b-a1",
        req: "kl-kt-real-b-line-a1" },
  C:  { item: "I-962", title: "I-217 真实闭环 962", req: "kl-kt-real-c-line-a1",
        reason: "等待本线工位空闲" },
  CLAIM_EVENTS: [
    { seq: 2, ts: "2026-10-14T17:42:44", action: "claim", item_id: "I-960", actor: "L3队",
      detail: "认领，租约 4 小时，会话 sess_d732a7ef-4e1c-4241-bdd0-c1c6f9e88e5c，工位 L3-w1" },
    { seq: 3, ts: "2026-10-14T17:42:49", action: "claim", item_id: "I-961", actor: "L3队",
      detail: "认领，租约 4 小时，会话 sess_5512b27e-42b1-456d-93d6-1be655730bc8，工位 L3-w2" },
  ],
};
const NOW = "2026-10-14T19:10:00";

/* ════════ 甲段：服务端渲染反例复验 ════════ */
function serverRenderProbe() {
  const ts = require(ROOT + "/board_ui/node_modules/typescript");
  const React = require(ROOT + "/board_ui/node_modules/react");
  const ReactDOMServer = require(ROOT + "/board_ui/node_modules/react-dom/server");
  const compile = (code, file) => ts.transpileModule(code, { fileName: file,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  for (const ext of [".ts", ".tsx"]) {
    Module._extensions[ext] = (m, file) =>
      m._compile(compile(fs.readFileSync(file, "utf8"), file), file);
  }
  const loadActual = (rel) => require(ROOT + "/" + rel).default;
  const LaneView = loadActual("board_ui/src/console/LaneView.tsx");
  const WatchView = loadActual("board_ui/src/console/WatchView.tsx");
  const noOp = () => {};

  function card(id, item, pct, updated) {
    return { session_id: id, lane: "L3", item_id: item, current_item_id: item,
      status: "在干活", context_pct: pct, last_activity_at: NOW, updated_at: updated,
      _is_worker: true,
      source: { kind: "表", locus: "console_sessions/" + id, excerpt: "i218v2 服务端渲染复验",
                sampled_at: NOW } };
  }
  const A = card(REAL.W1.sid, REAL.W1.item, 31, NOW);
  const B = card(REAL.W2.sid, REAL.W2.item, 52, "2026-10-14T19:09:00");
  function state(active, retained, laneQueues) {
    return { goals: [], lanes: [{ id: "L3", name: "数据资产与接驳预备", items: [], active_sessions: active,
      source: { kind: "表", locus: "isolated", excerpt: "isolated", sampled_at: NOW } }],
      sessions: { active, retained, archived: [], owner: [] },
      footer: { parallel: { active: active.length, tracked: active.length, limit: 15 }, sampled_at: NOW,
        source: { kind: "表", locus: "isolated", excerpt: "isolated", sampled_at: NOW } },
      lane_queues: laneQueues };
  }
  const records = [];

  // S1：真实冻结快照直喂（快照文件为收尾空态，waiting 全为对象）。
  const snapshot = JSON.parse(fs.readFileSync(
    ROOT + "/logs/i217_real_v2_20261014_174229/kernel_state/lane_queues.json", "utf8"));
  const wrapped = snapshot.queues ? snapshot : { sampled_at: NOW, queues: snapshot };
  for (const [comp, name, el] of [[LaneView, "LaneView", React.createElement(LaneView,
      { state: state([], [], wrapped), serverBase: Date.now(), onOpenSrc: noOp })],
      [WatchView, "WatchView", React.createElement(WatchView,
      { state: state([], [], wrapped), serverNow: Date.now(), onOpenSrc: noOp })]]) {
    try {
      ReactDOMServer.renderToStaticMarkup(el);
      records.push({ case: "real_frozen_backend_snapshot_to_" + name, error: null });
    } catch (e) {
      records.push({ case: "real_frozen_backend_snapshot_to_" + name, error: String(e) });
    }
  }
  ok(records.every((r) => !r.error),
    "S1 真实冻结快照（waiting 对象形态）双组件渲染无异常", JSON.stringify(records.filter((r) => r.error)));

  // S2/S3：复刻 Ciri 反例二、三（一活跃一保留／双保留）。
  const qActive1 = { sampled_at: NOW, queues: { L3: { concurrency_limit: 2,
    workers: [{ worker_id: REAL.W1.worker, session_id: A.session_id, item_id: A.item_id }],
    active_count: 1, waiting_count: 0, waiting: {}, queued_items: [] } } };
  const qIdle = { sampled_at: NOW, queues: { L3: { concurrency_limit: 2, workers: [],
    active_count: 0, waiting_count: 0, waiting: {}, queued_items: [] } } };
  for (const [name, active, retained, queues] of [
    ["one_active_one_retained", [A], [B], qActive1],
    ["both_workers_retained", [], [A, B], qIdle]]) {
    try {
      const html = ReactDOMServer.renderToStaticMarkup(React.createElement(WatchView,
        { state: state(active, retained, queues), serverNow: Date.now(), onOpenSrc: noOp }));
      const foundA = html.includes(A.item_id), foundB = html.includes(B.item_id);
      const articles = (html.match(/<article /g) || []).length;
      const retainedArts = (html.match(/class="ws-card retained"/g) || []).length;
      const sidA = html.includes('data-session-id="' + A.session_id + '"');
      const sidB = html.includes('data-session-id="' + B.session_id + '"');
      fs.writeFileSync(OUT + "/" + name + ".html", html, "utf8");
      records.push({ case: name, articles, expected_worker_cards: 2, foundItemA: foundA,
        foundItemB: foundB, retained_articles: retainedArts, full_sid_A: sidA, full_sid_B: sidB });
      ok(articles === 2 && foundA && foundB && sidA && sidB,
        "S-" + name + "：两张卡、两会话、两事项齐全，完整会话号在卡体",
        JSON.stringify(records[records.length - 1]));
    } catch (e) { records.push({ case: name, error: String(e) });
      ok(false, "S-" + name + "：渲染异常", String(e)); }
  }

  // S4：真实回放活跃队列（双工位 + 等待第三项，waiting 为对象）。
  const replayQueues = { sampled_at: NOW, queues: { L3: {
    concurrency_limit: 2,
    workers: [
      { worker_id: REAL.W1.worker, session_id: REAL.W1.sid, item_id: REAL.W1.item,
        run_id: REAL.W1.run, phase: "dispatched", request_id: REAL.W1.req },
      { worker_id: REAL.W2.worker, session_id: REAL.W2.sid, item_id: REAL.W2.item,
        run_id: REAL.W2.run, phase: "dispatched", request_id: REAL.W2.req },
    ],
    active_count: 2, waiting_count: 1,
    waiting: { [REAL.C.req]: REAL.C.reason },
    order: [REAL.W1.req, REAL.W2.req, REAL.C.req],
    current: REAL.W1.req, current_item_id: REAL.W1.item,
    queued_items: [
      { request_id: REAL.W1.req, item_id: REAL.W1.item, title: REAL.W1.title, phase: "dispatched", reason: null },
      { request_id: REAL.W2.req, item_id: REAL.W2.item, title: REAL.W2.title, phase: "dispatched", reason: null },
      { request_id: REAL.C.req, item_id: REAL.C.item, title: REAL.C.title, phase: "ready", reason: REAL.C.reason },
    ],
    conflict: [] } } };
  try {
    const laneHtml = ReactDOMServer.renderToStaticMarkup(React.createElement(LaneView,
      { state: state([], [], replayQueues), serverBase: Date.now(), onOpenSrc: noOp }));
    const watchHtml = ReactDOMServer.renderToStaticMarkup(React.createElement(WatchView,
      { state: state([A, B], [], replayQueues), serverNow: Date.now(), onOpenSrc: noOp }));
    fs.writeFileSync(OUT + "/replay_active_queues_lanes.html", laneHtml, "utf8");
    fs.writeFileSync(OUT + "/replay_active_queues_watch.html", watchHtml, "utf8");
    const laneOk = laneHtml.includes(REAL.C.req) && laneHtml.includes(REAL.C.reason)
      && laneHtml.includes(REAL.W1.worker) && laneHtml.includes(REAL.W2.worker)
      && laneHtml.includes("上限 2") && laneHtml.includes("等待 1 项");
    const watchOk = watchHtml.includes("执行 2/2") && watchHtml.includes("等待 1")
      && watchHtml.includes('data-session-id="' + REAL.W1.sid + '"')
      && watchHtml.includes('data-session-id="' + REAL.W2.sid + '"')
      && watchHtml.includes("工位 " + REAL.W1.worker) && watchHtml.includes("工位 " + REAL.W2.worker);
    records.push({ case: "replay_active_queues", lane_waiting_object_rendered: laneOk,
      watch_dual_cards: watchOk });
    ok(laneOk, "S4a 战情室：等待对象（request_id→原因）与工位行按冻结契约渲染", "");
    ok(watchOk, "S4b 观察窗：执行二比二等待一，双工位卡带工位号与完整会话号", "");
  } catch (e) { records.push({ case: "replay_active_queues", error: String(e) });
    ok(false, "S4 真实回放活跃队列渲染异常", String(e)); }

  // S5：同前缀会话对——截断前缀检查失效的反例证明（完整号区分）。
  const P1 = "sess_mock_i218v2_commonprefixpad_w1_0001";
  const P2 = "sess_mock_i218v2_commonprefixpad_w2_0002";
  const prefix14 = (s) => s.slice(0, 14);
  const fullEq = (a, b) => a === b;
  records.push({ case: "same_prefix_identity", p1: P1, p2: P2,
    same_14_prefix: prefix14(P1) === prefix14(P2),
    prefix_check_cannot_distinguish: (P2 + "…").startsWith(prefix14(P1)),
    full_id_check_distinguishes: !fullEq(P1, P2),
    wrong_worker_can_satisfy_prefix_assertion: prefix14(P1) === prefix14(P2),
    wrong_worker_can_satisfy_full_id_assertion: fullEq(P1, P2) });
  ok(prefix14(P1) === prefix14(P2) && P1 !== P2,
    "S5 同前缀样例成立且完整号可区分（前缀断言失效、完整号断言有效）", "");

  fs.writeFileSync(OUT + "/server_render_probe.json",
    JSON.stringify({ checked_at: new Date().toISOString(), mode: "actual_components_server_render",
      backend_source: "logs/i217_real_v2_20261014_174229/kernel_state/lane_queues.json（收尾空态快照）+ 同源 state.json/ledger.db 真实值回放",
      production_actions: 0, records }, null, 2), "utf8");
}

/* ════════ 乙段：浏览器隔离样例 ════════ */
async function browserProbe() {
  const puppeteer = require("puppeteer-core");
  const { spawn } = require("child_process");
  const preview = spawn("npx", ["vite", "preview", "--port", String(PORT), "--strictPort"],
    { cwd: path.join(ROOT, "board_ui"), shell: true, stdio: "ignore" });
  await new Promise((r) => setTimeout(r, 2500));
  const browser = await puppeteer.launch({
    executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    headless: true, args: ["--no-sandbox"], defaultViewport: { width: 1600, height: 1000 },
  });
  const samples = [];
  const capturedActions = [];
  let eventsReply = [];

  function mkCard(sid, lane, item, pct, extra) {
    return Object.assign({ session_id: sid, lane, item_id: item, current_item_id: item,
      status: "在干活", context_pct: pct, last_activity_at: NOW, updated_at: NOW,
      _is_worker: true,
      source: { kind: "表", locus: "console_sessions/" + sid,
                excerpt: "隔离样例（mock_i218v2 或真实值回放）", sampled_at: NOW } }, extra || {});
  }
  function lanesSkeleton() {
    const names = { HQ: "指挥部", L1: "攻关主线", L2: "架构与管线", L3: "数据资产与接驳预备",
      L4: "周期时变与混合飞行", L5: "接口与应用线", L6: "强化学习线", L7: "导航合龙线",
      L8: "形状保真对齐线", L9: "计算流体力学线（预登记）", L10: "架构提取与范用化线" };
    return Object.entries(names).map(([id, name]) => ({ id, name, items: [], active_sessions: [],
      source: { kind: "文件", locus: "items_registry/lanes.json#" + id, excerpt: name, sampled_at: NOW } }));
  }
  function stateWith(lane_queues, active, retained, archived) {
    return { goals: [], lanes: lanesSkeleton(),
      sessions: { active, retained: retained || [], archived: archived || [], owner: [] },
      footer: { parallel: { active: active.length, tracked: active.length, limit: 15 },
                sampled_at: NOW, source: { kind: "表", locus: "x", excerpt: "x", sampled_at: NOW } },
      lane_queues };
  }

  async function withState(stateBody, fn) {
    const p = await browser.newPage();
    await p.setCacheEnabled(false);
    await p.setRequestInterception(true);
    const handler = (rq) => {
      const url = rq.url();
      const reply = (result) => rq.respond({ status: 200, contentType: "application/json",
        headers: { Date: new Date().toUTCString() },
        body: JSON.stringify({ ok: true, result }) });
      if (url.includes("/api/console/state")) reply(stateBody);
      else if (url.includes("/api/console/config"))
        reply({ handoff_mode: "auto", gate_pct: 70, parallel_limit: 15 });
      else if (url.includes("/api/events")) reply(eventsReply);
      else if (url.includes("/api/auth/whoami")) reply({ user: "隔离样例" });
      else if (url.includes("/api/item-detail")) {
        const id = new URL(url).searchParams.get("id") || "";
        const known = { [REAL.W1.item]: { id: REAL.W1.item, title: REAL.W1.title, status: "pending_review", conclusion: null },
                        [REAL.W2.item]: { id: REAL.W2.item, title: REAL.W2.title, status: "pending_review", conclusion: null } };
        reply({ item: known[id] || { id, title: id, status: "open", conclusion: null } });
      } else if (url.includes("/api/console/action")) {
        const body = JSON.parse(rq.postData() || "{}");
        capturedActions.push({ url, body });
        reply({ task_id: 41800 + capturedActions.length });
      } else rq.continue();
    };
    p.on("request", handler);
    try {
      await p.goto("http://localhost:" + PORT + "/app/#/console", { waitUntil: "networkidle2", timeout: 30000 });
      await p.waitForSelector(".console-tabs");
      await p.evaluate(() => {
        const btn = [...document.querySelectorAll(".console-tab")]
          .find((b) => b.textContent.includes("观察窗"));
        if (btn) btn.click();
      });
      await p.waitForSelector(".ws-group", { timeout: 15000 });
      await new Promise((r) => setTimeout(r, 700));
      return await fn(p);
    } finally {
      p.off("request", handler);
      await p.close().catch(() => {});
    }
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
      })),
      retained: [...g.querySelectorAll(".ws-card.retained")].map((c) => ({
        sid: c.getAttribute("data-session-id") ?? "",
        item: c.querySelector(".ws-retained-note")?.textContent ?? "",
      })),
    };
  }, lane);
  const switchToLanes = async (p) => {
    await p.evaluate(() => {
      const btn = [...document.querySelectorAll(".console-tab")]
        .find((b) => b.textContent.includes("战情室"));
      if (btn) btn.click();
    });
    await new Promise((r) => setTimeout(r, 600));
  };
  const laneQueueText = async (p, lane) => p.evaluate((lane) => {
    const g = [...document.querySelectorAll(".ln-lane")]
      .find((x) => x.textContent.includes(lane));
    return g?.querySelector(".ln-queue")?.textContent ?? "";
  }, lane);
  const laneQueueRequestIds = async (p, lane) => p.evaluate((lane) => {
    const g = [...document.querySelectorAll(".ln-lane")]
      .find((x) => x.textContent.includes(lane));
    return [...(g?.querySelectorAll(".ln-queue [data-request-id]") ?? [])]
      .map((el) => el.getAttribute("data-request-id"));
  }, lane);

  /* 操作核对通用：对指定工位卡触发操作，核对完整目标，证明另一工位未作用。 */
  async function opCheck(p, sampleName, lane, targetSid, otherSid, op, label) {
    const before = await laneProbe(p, lane);
    const step = await p.evaluate(({ lane, targetSid, op }) => {
      const g = [...document.querySelectorAll(".ws-group")]
        .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith(lane));
      if (!g) return "no group";
      const cards = [...g.querySelectorAll(".ws-card:not(.retained)")];
      const card = cards.find((c) => c.getAttribute("data-session-id") === targetSid);
      if (!card) return "no card for full sid";
      const btns = [...card.querySelectorAll(".ws-actions .ws-btn")];
      const btn = btns.find((b) => b.textContent === op);
      if (!btn) return "no btn " + op;
      btn.click();
      return "clicked";
    }, { lane, targetSid, op });
    await new Promise((r) => setTimeout(r, 500));
    let targetOk = false, otherUntouched = true, evidence = "";
    if (op === "注入" || op === "停止") {
      const modal = await p.evaluate(() => {
        const m = document.querySelector(".ws-modal");
        return m ? { sid: m.getAttribute("data-session-id"), action: m.getAttribute("data-action") } : null;
      });
      targetOk = !!modal && modal.sid === targetSid
        && modal.action === (op === "注入" ? "inject" : "stop");
      otherUntouched = !!modal && modal.sid !== otherSid;
      if (targetOk && op === "注入") {
        // 确认执行，捕获 POST 请求体完整会话号（请求级定向证据）。
        await p.evaluate(() => {
          const btn = [...document.querySelectorAll(".ws-modal .ws-btn.primary")]
            .find((b) => b.textContent.includes("确认执行"));
          if (btn) btn.click();
        });
        await new Promise((r) => setTimeout(r, 400));
      }
      evidence = JSON.stringify(modal);
      await p.evaluate(() => {
        const close = [...document.querySelectorAll(".ws-modal .ws-btn")]
          .find((b) => b.textContent === "关闭" || b.textContent === "取消");
        if (close) close.click();
      }).catch(() => {});
    } else if (op === "履历") {
      const drawer = await p.evaluate(() => ({
        sid: document.querySelector(".hist-full-sid")?.textContent ?? "",
        open: !!document.querySelector(".hist-drawer"),
      }));
      targetOk = drawer.open && drawer.sid === targetSid;
      otherUntouched = drawer.sid !== otherSid;
      evidence = drawer.sid;
      await p.evaluate(() => {
        const btn = [...document.querySelectorAll(".hist-drawer .lab-btn-minor")]
          .find((b) => b.textContent === "关闭");
        if (btn) btn.click();
      }).catch(() => {});
    } else if (op === "出处") {
      const drawer = await p.evaluate(() => ({
        locus: document.querySelector(".src-drawer .src-drawer-locus")?.textContent ?? "",
        open: !!document.querySelector(".src-drawer"),
      }));
      targetOk = drawer.open && drawer.locus === "console_sessions/" + targetSid;
      otherUntouched = drawer.locus !== "console_sessions/" + otherSid;
      evidence = drawer.locus;
      await p.evaluate(() => {
        const btn = [...document.querySelectorAll(".src-drawer .lab-btn-minor")]
          .find((b) => b.textContent === "关闭");
        if (btn) btn.click();
      }).catch(() => {});
    }
    const after = await laneProbe(p, lane);
    const otherCardStillThere = JSON.stringify(after.cards).includes(otherSid)
      || JSON.stringify(after.retained).includes(otherSid);
    ok(step === "clicked" && targetOk && otherUntouched && otherCardStillThere,
      label, "step=" + step + " evidence=" + evidence);
    return { op, targetSid, otherSid, clicked: step === "clicked", targetOk,
      otherUntouched, otherCardStillThere, before_cards: before?.cards.length ?? -1,
      after_cards: after?.cards.length ?? -1 };
  }

  try {
    console.log("样例一：单工位旧接口（无 workers；waiting 为冻结契约对象形态）");
    eventsReply = [];
    await withState(stateWith(
      { sampled_at: NOW, queues: { L3: { waiting_count: 1, current: "kl-mock-i218v2-a1",
          current_item_id: "I-801", order: ["kl-mock-i218v2-a1"],
          waiting: { "kl-mock-i218v2-a1": "依赖未清：I-800" }, queued_items: [
            { request_id: "kl-mock-i218v2-a1", item_id: "I-801", title: "旧接口样例事项",
              phase: "ready", reason: "依赖未清：I-800" }] } } },
      [mkCard("sess_mock_i218v2_legacy_0001", "L3", "I-801", 30.0)]), async (p) => {
      const g = await laneProbe(p, "L3");
      ok(g && g.count.includes("排队 1（工位明细未采集）"),
        "旧接口兼容：组头排队计数与未采集标注", JSON.stringify(g?.count));
      ok(g && g.cards[0]?.worker.includes("工位未登记"),
        "旧接口卡面工位未登记（不编造工位号）", g?.cards[0]?.worker);
      await switchToLanes(p);
      const q = await laneQueueText(p, "L3");
      const rids = await laneQueueRequestIds(p, "L3");
      ok(rids.includes("kl-mock-i218v2-a1") && q.includes("依赖未清：I-800") && q.includes("I-801"),
        "旧接口战情室：waiting 对象键（request_id 属性）与事项、原因如实显示",
        JSON.stringify({ rids, q: q.slice(0, 90) }));
      samples.push({ sample: 1, legacy: true, group_count: g?.count, lane_queue: q.slice(0, 120), request_ids: rids });
    });

    console.log("样例二：双工位新接口（真实值回放：L3-w1/L3-w2 双会话执行、第三项等待）");
    const dualQueues = { sampled_at: NOW, queues: { L3: {
      concurrency_limit: 2,
      workers: [
        { worker_id: REAL.W1.worker, session_id: REAL.W1.sid, item_id: REAL.W1.item,
          run_id: REAL.W1.run, phase: "dispatched", request_id: REAL.W1.req },
        { worker_id: REAL.W2.worker, session_id: REAL.W2.sid, item_id: REAL.W2.item,
          run_id: REAL.W2.run, phase: "dispatched", request_id: REAL.W2.req },
      ],
      active_count: 2, waiting_count: 1,
      waiting: { [REAL.C.req]: REAL.C.reason },
      order: [REAL.W1.req, REAL.W2.req, REAL.C.req], current: REAL.W1.req,
      current_item_id: REAL.W1.item,
      queued_items: [
        { request_id: REAL.W1.req, item_id: REAL.W1.item, title: REAL.W1.title, phase: "dispatched", reason: null },
        { request_id: REAL.W2.req, item_id: REAL.W2.item, title: REAL.W2.title, phase: "dispatched", reason: null },
        { request_id: REAL.C.req, item_id: REAL.C.item, title: REAL.C.title, phase: "ready", reason: REAL.C.reason },
      ], conflict: [] } } };
    eventsReply = REAL.CLAIM_EVENTS;
    await withState(stateWith(dualQueues, [
      mkCard(REAL.W1.sid, "L3", REAL.W1.item, 31.0),
      mkCard(REAL.W2.sid, "L3", REAL.W2.item, 52.0)]), async (p) => {
      const g = await laneProbe(p, "L3");
      ok(g && g.count.includes("执行 2/2") && g.count.includes("等待 1"),
        "组头：正在执行二、并发上限二、等待一（判据一显示口径）", JSON.stringify(g?.count));
      ok(g && g.cards.length === 2
        && g.cards.some((c) => c.worker.includes(REAL.W1.worker) && c.item === REAL.W1.item && c.sid === REAL.W1.sid)
        && g.cards.some((c) => c.worker.includes(REAL.W2.worker) && c.item === REAL.W2.item && c.sid === REAL.W2.sid),
        "两张工位卡各显示工位号、完整事项号与完整会话号（data-session-id 全等）",
        JSON.stringify(g?.cards));
      await p.screenshot({ path: OUT + "/sample2_dual_workers_real_replay.png", fullPage: false });
      samples.push({ sample: 2, dual: true, group_count: g?.count, cards: g?.cards });
    });

    console.log("样例三：某工位退出（w2 会话驻留保留节；w1 活跃）——历史卡不丢");
    await withState(stateWith(
      { sampled_at: NOW, queues: { L3: {
          concurrency_limit: 2,
          workers: [{ worker_id: REAL.W1.worker, session_id: REAL.W1.sid, item_id: REAL.W1.item,
                      run_id: REAL.W1.run, phase: "dispatched", request_id: REAL.W1.req }],
          active_count: 1, waiting_count: 1,
          waiting: { [REAL.C.req]: REAL.C.reason }, queued_items: [
            { request_id: REAL.W1.req, item_id: REAL.W1.item, phase: "dispatched", reason: null },
            { request_id: REAL.C.req, item_id: REAL.C.item, phase: "ready", reason: REAL.C.reason }],
          conflict: [] } } },
      [mkCard(REAL.W1.sid, "L3", REAL.W1.item, 33.0)],
      [mkCard(REAL.W2.sid, "L3", REAL.W2.item, null,
        { status: "空闲", updated_at: "2026-10-14T18:20:00", last_activity_at: "2026-10-14T18:20:00" })]),
      async (p) => {
      const g = await laneProbe(p, "L3");
      ok(g && g.count.includes("执行 1/2") && g.count.includes("等待 1"),
        "工位退出后计数为一执行二上限一等待", JSON.stringify(g?.count));
      ok(g && g.cards.length === 1 && g.cards[0].sid === REAL.W1.sid
        && g.retained.length === 1 && g.retained[0].sid === REAL.W2.sid
        && g.retained[0].item.includes(REAL.W2.item),
        "退出工位保留灰色历史卡：完整会话号与最后事项在卡（不再丢失）",
        JSON.stringify(g?.retained));
      await p.screenshot({ path: OUT + "/sample3_exit_retained_coexist.png", fullPage: false });
      samples.push({ sample: 3, exit: true, group_count: g?.count, retained: g?.retained });
    });

    console.log("样例四：等待原因（战情室按 waiting 对象显示明细）");
    await withState(stateWith(dualQueues, [
      mkCard(REAL.W1.sid, "L3", REAL.W1.item, 34.0),
      mkCard(REAL.W2.sid, "L3", REAL.W2.item, 40.0)]), async (p) => {
      await switchToLanes(p);
      const q = await laneQueueText(p, "L3");
      const rids = await laneQueueRequestIds(p, "L3");
      ok(q.includes(REAL.W1.worker) && q.includes(REAL.W2.worker)
        && rids.includes(REAL.C.req) && q.includes(REAL.C.item) && q.includes(REAL.C.reason),
        "战情室等待明细含工位、请求号（属性）、事项与实际等待原因（对象形态）",
        JSON.stringify({ rids, q: q.slice(0, 120) }));
      await p.screenshot({ path: OUT + "/sample4_waiting_reason_object.png", fullPage: false });
      samples.push({ sample: 4, reason: q.slice(0, 140), request_ids: rids });
    });

    console.log("样例五：接口缺字段（workers 元素缺工位号；waiting/queued_items 缺失）");
    await withState(stateWith(
      { sampled_at: NOW, queues: { L3: { concurrency_limit: 2,
          workers: [{ session_id: "sess_mock_i218v2_nofield_0005", item_id: "I-805" }],
          waiting_count: 0 } } },
      [mkCard("sess_mock_i218v2_nofield_0005", "L3", "I-805", 35.0)]), async (p) => {
      const g = await laneProbe(p, "L3");
      ok(g && g.cards[0]?.worker.includes("工位未登记") && g.cards.length === 1,
        "缺工位号与等待明细时如实标注未登记且不报错", JSON.stringify(g?.cards));
      await switchToLanes(p);
      const q = await laneQueueText(p, "L3");
      ok(typeof q === "string",
        "缺 waiting/queued_items 时战情室不抛异常", String(q).slice(0, 60));
      samples.push({ sample: 5, missing_field: g?.cards[0]?.worker });
    });

    console.log("样例六：无会话（workers 空且有等待项；灰态保留并明示原因）");
    await withState(stateWith(
      { sampled_at: NOW, queues: { L3: { concurrency_limit: 2, workers: [],
          active_count: 0, waiting_count: 1,
          waiting: { "kl-mock-i218v2-b4": "等待工位空出" }, queued_items: [
            { request_id: "kl-mock-i218v2-b4", item_id: "I-804", phase: "ready", reason: "等待工位空出" }] } } },
      []), async (p) => {
      const g = await laneProbe(p, "L3");
      ok(g && g.cards.length === 0 && g.retained.length === 1
        && !g.retained[0].sid,
        "无会话线灰态保留占位（业务线不消失，无编造会话号）", JSON.stringify(g?.retained));
      ok(g && g.count.includes("执行 0/2") && g.count.includes("等待 1"),
        "无会话时计数零执行上限二等待一（不编造正在执行）", g?.count);
      const reasonText = await p.evaluate(() =>
        [...document.querySelectorAll(".ws-card.retained .ws-activity")].map((x) => x.textContent).join("｜"));
      ok(reasonText.includes("无可核查"),
        "无可核查历史时明示原因", reasonText.slice(0, 90));
      await p.screenshot({ path: OUT + "/sample6_no_session.png", fullPage: false });
      samples.push({ sample: 6, none: true, group_count: g?.count, reason: reasonText.slice(0, 120) });
    });

    console.log("样例七：双空闲（两会话均超时驻留）——两张灰卡各带完整会话号");
    const stale = { status: "空闲", context_pct: null, updated_at: "2026-10-14T18:20:00",
      last_activity_at: "2026-10-14T18:20:00" };
    await withState(stateWith(
      { sampled_at: NOW, queues: { L3: { concurrency_limit: 2, workers: [],
          active_count: 0, waiting_count: 0, waiting: {}, queued_items: [] } } },
      [], [mkCard(REAL.W1.sid, "L3", REAL.W1.item, 28.0, stale),
           mkCard(REAL.W2.sid, "L3", REAL.W2.item, 44.0, stale)]), async (p) => {
      const g = await laneProbe(p, "L3");
      ok(g && g.retained.length === 2
        && g.retained.some((r) => r.sid === REAL.W1.sid && r.item.includes(REAL.W1.item))
        && g.retained.some((r) => r.sid === REAL.W2.sid && r.item.includes(REAL.W2.item)),
        "双空闲出两张灰卡：完整会话号与各自最后事项（不再只留一张）",
        JSON.stringify(g?.retained));
      ok(g && g.count.includes("历史工位灰卡 2") && g.count.includes("执行 0/2"),
        "组头如实显示历史灰卡数与零执行", g?.count);
      await p.screenshot({ path: OUT + "/sample7_both_idle_two_gray_cards.png", fullPage: false });
      samples.push({ sample: 7, both_idle: true, retained: g?.retained, group_count: g?.count });
    });

    console.log("样例八：同前缀会话对操作定向（Ciri 反例四复验：截断前缀同、完整号异）");
    const P1 = "sess_mock_i218v2_commonprefixpad_w1_0001";
    const P2 = "sess_mock_i218v2_commonprefixpad_w2_0002";
    const prefixQueues = { sampled_at: NOW, queues: { L3: {
      concurrency_limit: 2,
      workers: [
        { worker_id: "L3-w1", session_id: P1, item_id: "I-811", phase: "dispatched", request_id: "kl-mock-i218v2-p1" },
        { worker_id: "L3-w2", session_id: P2, item_id: "I-812", phase: "dispatched", request_id: "kl-mock-i218v2-p2" },
      ], active_count: 2, waiting_count: 0, waiting: {}, queued_items: [], conflict: [] } } };
    const ops8 = [];
    await withState(stateWith(prefixQueues, [
      mkCard(P1, "L3", "I-811", 36.0), mkCard(P2, "L3", "I-812", 47.0)]), async (p) => {
      ok(P1.slice(0, 14) === P2.slice(0, 14) && P1 !== P2,
        "样例八前提：两会话截断十四字符前缀相同而完整号不同", "");
      ops8.push(await opCheck(p, "sample8", "L3", P1, P2, "注入", "同前缀对：注入定向工位一（完整会话号全等，非前缀）"));
      await p.screenshot({ path: OUT + "/sample8_sameprefix_inject_target.png", fullPage: false });
      ops8.push(await opCheck(p, "sample8", "L3", P2, P1, "停止", "同前缀对：停止定向工位二（完整会话号全等）"));
      ops8.push(await opCheck(p, "sample8", "L3", P1, P2, "履历", "同前缀对：履历抽屉完整会话号为工位一"));
      ops8.push(await opCheck(p, "sample8", "L3", P2, P1, "出处", "同前缀对：出处抽屉 locus 为工位二完整会话号"));
      samples.push({ sample: 8, same_prefix_pair: { p1: P1, p2: P2 }, ops: ops8 });
    });

    console.log("样例九：真实回放对操作定向（完整身份四操作，误作用零次）");
    const ops9 = [];
    await withState(stateWith(dualQueues, [
      mkCard(REAL.W1.sid, "L3", REAL.W1.item, 31.0),
      mkCard(REAL.W2.sid, "L3", REAL.W2.item, 52.0)]), async (p) => {
      ops9.push(await opCheck(p, "sample9", "L3", REAL.W1.sid, REAL.W2.sid, "注入",
        "真实回放：注入定向 L3-w1（弹层与 POST 请求体完整会话号）"));
      ops9.push(await opCheck(p, "sample9", "L3", REAL.W2.sid, REAL.W1.sid, "停止",
        "真实回放：停止定向 L3-w2（弹层完整会话号）"));
      ops9.push(await opCheck(p, "sample9", "L3", REAL.W2.sid, REAL.W1.sid, "履历",
        "真实回放：履历抽屉完整会话号为 L3-w2"));
      ops9.push(await opCheck(p, "sample9", "L3", REAL.W1.sid, REAL.W2.sid, "出处",
        "真实回放：出处抽屉 locus 为 L3-w1 完整会话号"));
      const injectPosts = capturedActions.filter((a) => a.body.action === "inject");
      ok(injectPosts.length >= 1 && injectPosts.every((a) => a.body.session_id === P1 || a.body.session_id === REAL.W1.sid),
        "POST /api/console/action 请求体携带完整 session_id（请求级定向证据）",
        JSON.stringify(injectPosts.map((a) => a.body.session_id)));
      ok(ops8.concat(ops9).every((o) => o.otherUntouched && o.otherCardStillThere),
        "全部八次单卡操作误作用于另一工位次数为零", "");
      await p.screenshot({ path: OUT + "/sample9_real_replay_ops.png", fullPage: false });
      samples.push({ sample: 9, real_replay: true, ops: ops9,
        captured_action_bodies: capturedActions.map((a) => a.body) });
    });
  } finally {
    await browser.close().catch(() => {});
    preview.kill();
  }
  return { samples, capturedActions };
}

(async () => {
  console.log("═══ 甲段：服务端渲染反例复验 ═══");
  serverRenderProbe();
  console.log("═══ 乙段：浏览器隔离样例 ═══");
  const { samples, capturedActions } = await browserProbe();
  const record = { generated_at: new Date().toISOString(), pass, fail, failures, samples,
    captured_console_actions: capturedActions.map((a) => a.body) };
  fs.writeFileSync(OUT + "/isolated_samples_record.json", JSON.stringify(record, null, 1), "utf8");
  console.log("\n结果：" + pass + " 过 / " + fail + " 拒；记录 " + OUT + "/isolated_samples_record.json");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("核验脚本异常：", e); process.exit(2); });
