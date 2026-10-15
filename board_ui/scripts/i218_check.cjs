/* i218_check.cjs — I-218 多工位观察窗隔离样例核验（独立端口，不触生产）。
 * 六种样例：单工位旧接口、双工位新接口、工位退出、等待原因、接口缺
 * 字段、无会话；另验判据一的组头计数（执行二等待一）、判据二的操作
 * 定向（注入目标为所点工位会话）。数据全部 mock_i218 前缀隔离。 */
const puppeteer = require("puppeteer-core");
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const path = require("path");

const ROOT = "G:/Playground/isaac_flapper_sim";
const OUT = ROOT + "/logs/i218_check";
fs.mkdirSync(OUT, { recursive: true });
const PORT = 4173;
let pass = 0, fail = 0;
const record = { generated_at: new Date().toISOString(), samples: [] };
function ok(cond, label, detail) {
  if (cond) { pass++; console.log("  [过] " + label); }
  else { fail++; console.log("  [拒] " + label + (detail ? "：" + detail : "")); }
  return !!cond;
}
const NOW = "2026-10-14T10:00:00";
function mkCard(sid, lane, item, pct) {
  return { session_id: sid, lane, item_id: item, current_item_id: item,
    status: "在干活", context_pct: pct, last_activity_at: NOW,
    updated_at: NOW,
    source: { kind: "表", locus: "console_sessions/" + sid,
              excerpt: "隔离样例（mock_i218）", sampled_at: NOW } };
}
function lanesSkeleton() {
  const names = { HQ: "指挥部", L1: "攻关主线", L2: "架构与管线", L3: "数据资产与接驳预备",
    L4: "周期时变与混合飞行", L5: "接口与应用线", L6: "强化学习线", L7: "导航合龙线",
    L8: "形状保真对齐线", L9: "计算流体力学线（预登记）", L10: "架构提取与范用化线" };
  return Object.entries(names).map(([id, name]) => ({ id, name, items: [], active_sessions: [],
    source: { kind: "文件", locus: "items_registry/lanes.json#" + id, excerpt: name, sampled_at: NOW } }));
}
function stateWith(lane_queues, active) {
  return { goals: [], lanes: lanesSkeleton(), sessions: { active, retained: [], archived: [], owner: [] },
    footer: { parallel: { active: active.length, tracked: active.length, limit: 15 },
              sampled_at: NOW, source: { kind: "表", locus: "x", excerpt: "x", sampled_at: NOW } },
    lane_queues };
}

async function withState(browser, stateBody, fn) {
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
    else if (url.includes("/api/events")) reply([]);
    else if (url.includes("/api/auth/whoami")) reply({ user: "隔离样例" });
    else rq.continue();
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
    await fn(p);
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
      sid: c.querySelector(".ws-sid-click")?.textContent ?? "",
    })),
    retained: g.querySelectorAll(".ws-card.retained").length,
  };
}, lane);

(async () => {
  // 独立预览服务（自建进程，不触生产）
  const preview = spawn("npx", ["vite", "preview", "--port", String(PORT), "--strictPort"],
    { cwd: path.join(ROOT, "board_ui"), shell: true, stdio: "ignore" });
  await new Promise((r) => setTimeout(r, 2500));

  const browser = await puppeteer.launch({
    executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    headless: true, args: ["--no-sandbox"], defaultViewport: { width: 1600, height: 1000 },
  });

  try {
    console.log("样例一：单工位旧接口（无 workers，兼容形态）");
    await withState(browser, stateWith(
      { sampled_at: NOW, queues: { L3: { waiting_count: 0, current_item_id: "I-801",
          queued_items: [] } } },
      [mkCard("sess_mock_i218_a1_old1111222", "L3", "I-801", 30.0)]), async (p) => {
      const g = await laneProbe(p, "L3");
      ok(g && g.count.includes("排队 0（工位明细未采集）"),
        "旧接口兼容：组头排队计数与未采集标注", JSON.stringify(g?.count));
      ok(g && g.cards[0]?.worker.includes("工位未登记"),
        "旧接口卡面工位未登记（不编造工位号）", g?.cards[0]?.worker);
      record.samples.push({ sample: 1, legacy: true, group_count: g?.count });
    });

    console.log("样例二：双工位新接口（执行二等待一）");
    const dualState = stateWith(
      { sampled_at: NOW, queues: { L3: {
          concurrency_limit: 2,
          workers: [
            { worker_id: "W1", session_id: "sess_mock_i218_w1_1111222", item_id: "I-801", run_id: "run-a", phase: "执行中" },
            { worker_id: "W2", session_id: "sess_mock_i218_w2_1111222", item_id: "I-802", run_id: "run-b", phase: "执行中" },
          ],
          waiting_count: 1, current_item_id: null, queued_items: [], waiting: [],
        } } },
      [mkCard("sess_mock_i218_w1_1111222", "L3", "I-801", 31.0),
       mkCard("sess_mock_i218_w2_1111222", "L3", "I-802", 52.0)]);
    await withState(browser, dualState, async (p) => {
      const g = await laneProbe(p, "L3");
      ok(g && g.count.includes("执行 2/2") && g.count.includes("等待 1"),
        "组头：正在执行二、并发上限二、等待一（判据一显示口径）", JSON.stringify(g?.count));
      ok(g && g.cards.length === 2
        && g.cards.some((c) => c.worker.includes("W1") && c.item === "I-801")
        && g.cards.some((c) => c.worker.includes("W2") && c.item === "I-802"),
        "两张工位卡各显示工位号与完整事项号", JSON.stringify(g?.cards));
      await p.screenshot({ path: OUT + "/sample2_dual_workers.png", fullPage: false });
      // 操作定向：点 W1 卡的注入按钮，弹层目标是 W1 会话
      const target = await p.evaluate(() => {
        const g = [...document.querySelectorAll(".ws-group")]
          .find((x) => x.querySelector(".ws-group-lane")?.textContent.startsWith("L3"));
        const cards = [...(g?.querySelectorAll(".ws-card:not(.retained)") ?? [])];
        const w1 = cards.find((c) => c.textContent.includes("W1"));
        const inject = [...(w1?.querySelectorAll(".ws-actions .ws-btn") ?? [])]
          .find((b) => b.textContent === "注入");
        if (!inject) return "no btn";
        inject.click();
        return "clicked";
      });
      await new Promise((r) => setTimeout(r, 400));
      const modal = await p.evaluate(() => ({
        sid: document.querySelector(".ws-modal-sid")?.textContent ?? "",
        open: !!document.querySelector(".ws-modal") }));
      ok(target === "clicked" && modal.open && modal.sid.startsWith("sess_mock_i218_w1_".slice(0, 14)),
        "操作定向：注入弹层目标为工位一会话（不误指工位二）", modal.sid);
      await p.evaluate(() => {
        const cancel = [...document.querySelectorAll(".ws-modal .ws-btn")]
          .find((b) => b.textContent === "取消");
        if (cancel) cancel.click();
      }).catch(() => {});
      record.samples.push({ sample: 2, dual: true, group_count: g?.count,
        cards: g?.cards.length, inject_target_ok: modal.sid.startsWith("sess_mock_i218_w1_".slice(0, 14)) });
    });

    console.log("样例三：工位退出（一退出一保留，等待项仍在队）");
    await withState(browser, stateWith(
      { sampled_at: NOW, queues: { L3: {
          concurrency_limit: 2,
          workers: [{ worker_id: "W1", session_id: "sess_mock_i218_w1_1111222", item_id: "I-801" }],
          waiting_count: 1, waiting: [{ item_id: "I-803", reason: "工位二退出后排队" }],
        } } },
      [mkCard("sess_mock_i218_w1_1111222", "L3", "I-801", 33.0)]), async (p) => {
      const g = await laneProbe(p, "L3");
      ok(g && g.count.includes("执行 1/2") && g.count.includes("等待 1"),
        "工位退出后计数为一执行二上限一等待", JSON.stringify(g?.count));
      ok(g && g.cards.length === 1 && g.cards[0].worker.includes("W1"),
        "退出工位的卡不再出现，保留工位卡正常", JSON.stringify(g?.cards));
      record.samples.push({ sample: 3, exit: true, group_count: g?.count });
    });

    console.log("样例四：等待原因（战情室显示等待明细）");
    await withState(browser, stateWith(
      { sampled_at: NOW, queues: { L3: {
          concurrency_limit: 1,
          workers: [{ worker_id: "W1", session_id: "sess_mock_i218_w1_1111222", item_id: "I-801" }],
          waiting_count: 1, waiting: [{ item_id: "I-803", reason: "可写范围与工位一重叠，拒绝并行准入" }],
        } } },
      [mkCard("sess_mock_i218_w1_1111222", "L3", "I-801", 34.0)]), async (p) => {
      await p.evaluate(() => {
        const btn = [...document.querySelectorAll(".console-tab")]
          .find((b) => b.textContent.includes("战情室"));
        if (btn) btn.click();
      });
      await new Promise((r) => setTimeout(r, 600));
      const q = await p.evaluate(() => {
        const g = [...document.querySelectorAll(".ln-lane")]
          .find((x) => x.textContent.includes("L3"));
        return g?.querySelector(".ln-queue")?.textContent ?? "";
      });
      ok(q.includes("W1") && q.includes("I-803") && q.includes("重叠"),
        "战情室等待明细含工位、事项与实际准入原因", q.slice(0, 90));
      await p.screenshot({ path: OUT + "/sample4_wait_reason.png", fullPage: false });
      record.samples.push({ sample: 4, reason: q.slice(0, 80) });
    });

    console.log("样例五：接口缺字段（workers 元素缺工位号）");
    await withState(browser, stateWith(
      { sampled_at: NOW, queues: { L3: { concurrency_limit: 2,
          workers: [{ session_id: "sess_mock_i218_w1_1111222", item_id: "I-801" }],
          waiting_count: 0 } } },
      [mkCard("sess_mock_i218_w1_1111222", "L3", "I-801", 35.0)]), async (p) => {
      const g = await laneProbe(p, "L3");
      ok(g && g.cards[0]?.worker.includes("工位未登记"),
        "缺工位号时如实标注未登记（不编造）", g?.cards[0]?.worker);
      record.samples.push({ sample: 5, missing_field: g?.cards[0]?.worker });
    });

    console.log("样例六：无会话（灰态保留与排队共存）");
    await withState(browser, stateWith(
      { sampled_at: NOW, queues: { L3: { concurrency_limit: 2, workers: [],
          waiting_count: 1, waiting: [{ item_id: "I-804", reason: "等待工位空出" }] } } },
      []), async (p) => {
      const g = await laneProbe(p, "L3");
      ok(g && g.cards.length === 0 && g.retained >= 1,
        "无会话线灰态保留（业务线不消失）", JSON.stringify({ cards: g?.cards.length, retained: g?.retained }));
      ok(g && g.count.includes("执行 0/2") && g.count.includes("等待 1"),
        "无会话时计数零执行上限二等待一（不编造正在执行）", g?.count);
      await p.screenshot({ path: OUT + "/sample6_no_session.png", fullPage: false });
      record.samples.push({ sample: 6, none: true, group_count: g?.count });
    });
  } finally {
    await browser.close().catch(() => {});
    preview.kill();
  }
  record.pass = pass; record.fail = fail;
  fs.writeFileSync(OUT + "/isolated_samples_record.json", JSON.stringify(record, null, 1));
  console.log("\n结果：" + pass + " 过 / " + fail + " 拒；记录 " + OUT + "/isolated_samples_record.json");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("核验脚本异常：", e); process.exit(2); });
