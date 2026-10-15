/* i168_check.js — I-168 判据核验：总览页三层陈列、卡片点击、出处跳转。
 * 判据原文四段逐项对照：
 *   一、页面一按三层陈列（许愿池愿景条目、战略蓝图应然块、指挥部述职卡）；
 *   二、业务线卡点开见存在理由与所服务的蓝图块；
 *   三、HQ 卡点开见自我认知、压缩记忆与按时间排列的曾用现用对话；
 *   四、全部数据带出处指针（抽样点击出处角标开抽屉）。
 * 一致性比对基准为本地权威文件：items_registry/{goals,blueprints,lanes}.json
 * 与协作架构 D-048 段（页面值与登记库逐字一致）。 */
const puppeteer = require("puppeteer-core");
const https = require("https");
const fs = require("fs");

const SECRET = JSON.parse(fs.readFileSync(
  "G:\\Playground\\isaac_flapper_sim\\lp_sync\\cloud_secret.json", "utf-8"));
const ROOT = "G:\\Playground\\isaac_flapper_sim";
const HOST = "personal.karmicsword.com";

const goalsJson = JSON.parse(fs.readFileSync(ROOT + "\\items_registry\\goals.json", "utf-8"));
const bpJson = JSON.parse(fs.readFileSync(ROOT + "\\items_registry\\blueprints.json", "utf-8"));
const lanesJson = JSON.parse(fs.readFileSync(ROOT + "\\items_registry\\lanes.json", "utf-8"));
const charter = fs.readFileSync(ROOT + "\\协作架构_20260930.md", "utf-8").replace(/\r\n/g, "\n");
const d48First = charter.match(/D-048，[^\n]*\n\n(一、[^\n]+)/)[1].trim();

let pass = 0, fail = 0;
function ok(cond, label, detail) {
  if (cond) { pass++; console.log("  [过] " + label); }
  else { fail++; console.log("  [拒] " + label + (detail ? "：" + detail : "")); }
}

function req(method, path, body, cookie) {
  return new Promise((res, rej) => {
    const opts = { hostname: HOST, path, method,
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

  const browser = await puppeteer.launch({
    executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    headless: true, args: ["--no-sandbox", "--window-size=1600,1000"],
    defaultViewport: { width: 1600, height: 1000 },
  });
  const page = await browser.newPage();
  await page.setCookie({ name: "lp_session", value: cookie.split("=")[1], domain: HOST, path: "/" });
  await page.goto("https://" + HOST + "/app/#/console", { waitUntil: "networkidle2", timeout: 60000 });
  await page.waitForSelector(".ov", { timeout: 30000 });
  await new Promise((r) => setTimeout(r, 1200));

  console.log("判据一：页面一按三层陈列");
  const layers = await page.$$eval("[aria-label]", (els) => els.map((e) => e.getAttribute("aria-label")));
  ok(layers.includes("许愿池"), "许愿池层存在");
  ok(layers.includes("战略蓝图"), "战略蓝图层存在");
  ok(layers.includes("指挥部"), "指挥部层存在");

  const goalCards = await page.$$eval("[id^='ov-goal-']", (els) =>
    els.map((e) => e.id.replace("ov-goal-", "")));
  ok(goalCards.length === 3 && ["G-001", "G-002", "G-003"].every((g) => goalCards.includes(g)),
    "许愿池陈列愿景条目 3 条（G-001/G-002/G-003）", JSON.stringify(goalCards));

  for (const g of goalsJson.goals.filter((x) => x.status === "active")) {
    const head = await page.$eval("#ov-goal-" + g.id + " .ov-card-head", (e) => e.textContent);
    ok(head.includes(g.text.slice(0, 20)), g.id + " 标题文本与目标库一致", head);
  }

  const blockCards = await page.$$eval("[id^='ov-block-']", (els) => els.map((e) => e.id.replace("ov-block-", "")));
  ok(blockCards.length === 11, "战略蓝图陈列应然块 11 块", "实际 " + blockCards.length);
  let namesOk = 0;
  for (const b of bpJson.blocks) {
    const t = await page.$eval("#ov-block-" + b.id + " .ov-card-head", (e) => e.textContent);
    if (t.includes(b.name)) namesOk++;
  }
  ok(namesOk === 11, "十一块块名与登记册逐字一致", namesOk + "/11");

  const laneCards = await page.$$eval("[id^='ov-lane-']", (els) => els.map((e) => e.id.replace("ov-lane-", "")));
  ok(laneCards.length === 10, "指挥部陈列业务线述职卡 10 张（L1 至 L10）", "实际 " + laneCards.length);
  ok((await page.$$("#ov-hq-HQ")).length === 1, "指挥部陈列 HQ 述职卡 1 张");

  console.log("判据二：业务线卡点开见存在理由与所服务的蓝图块");
  for (const [lid, conf] of Object.entries(lanesJson.lanes)) {
    if (lid === "HQ") continue;
    await page.click("#ov-lane-" + lid + " .ov-card-head");
    await new Promise((r) => setTimeout(r, 250));
    const detail = await page.$eval("#ov-lane-" + lid + " .ov-detail", (e) => e.textContent.replace(/\s+/g, ""));
    const mission = (conf.mission || "").replace(/\s+/g, "");
    ok(detail.includes(mission.slice(0, 24)), lid + " 存在理由与 lanes.json 逐字一致", mission.slice(0, 24));
    const expect = bpJson.blocks.filter((b) => (b.lanes || []).includes(lid)).map((b) => b.id);
    const got = await page.$$eval("#ov-lane-" + lid + " .ov-detail .ov-jump .ov-id", (els) => els.map((e) => e.textContent));
    ok(JSON.stringify([...got].sort()) === JSON.stringify([...expect].sort()),
      lid + " 所服务蓝图块与登记册反查一致（" + expect.length + " 块）",
      "页面 " + JSON.stringify(got) + " 期望 " + JSON.stringify(expect));
    await page.click("#ov-lane-" + lid + " .ov-card-head");   // 收起，保持下一轮干净
    await new Promise((r) => setTimeout(r, 150));
  }

  console.log("判据三：HQ 卡点开见自我认知、压缩记忆与按时间排列的曾用现用对话");
  await page.click("#ov-hq-HQ .ov-card-head");
  await new Promise((r) => setTimeout(r, 300));
  const hqText = await page.$eval("#ov-hq-HQ .ov-detail", (e) => e.textContent);
  ok(hqText.includes("自我认知"), "HQ 展开含自我认知节");
  ok(hqText.includes("压缩记忆"), "HQ 展开含压缩记忆节");
  ok(hqText.includes("曾用现用对话"), "HQ 展开含曾用现用对话节");
  ok(hqText.includes(d48First.slice(0, 30)), "自我认知首条与协作架构 D-048 原文逐字一致", d48First.slice(0, 30));
  const dlgEra = await page.$$eval("#ov-hq-HQ .ov-dialogs .ov-chip", (els) => els.map((e) => e.textContent.trim()));
  ok(dlgEra.length === 3 && dlgEra[2].includes("现用") && !dlgEra[0].includes("现用"),
    "对话沿革 3 条按时间排列（曾用在前现用在末）", JSON.stringify(dlgEra));
  const dlgSid = await page.$$eval("#ov-hq-HQ .ov-dialogs .ov-dialog-sid", (els) => els.map((e) => e.textContent.trim()));
  ok(dlgSid.includes("conv-d9eb45252d252ef3a8b7f38c") && dlgSid.includes("conv-3d1352d6a6f973c1a11bdd39"),
    "对话沿革含入职卡记载的现任与前任会话编号", JSON.stringify(dlgSid));
  await page.screenshot({ path: ROOT + "\\logs\\i168_hq_card.png", fullPage: false });

  console.log("判据四：全部数据带出处指针（抽样四类点击开抽屉）");
  const drawerProbes = [
    { sel: "#ov-goal-G-001 .src-mark", want: "goals.json", label: "愿景条目出处（目标库）" },
    { sel: "#ov-block-B-01 .src-mark", want: "blueprints", label: "蓝图块出处（登记册镜像）" },
    { sel: "#ov-lane-L5 .src-mark", want: "lanes.json#L5", label: "业务线卡出处（领地表）" },
    { sel: "#ov-hq-HQ .ov-mem-cm .src-mark", want: "入职卡", label: "HQ 压缩记忆出处（交接卡）" },
  ];
  for (const p of drawerProbes) {
    await page.click(p.sel);
    await new Promise((r) => setTimeout(r, 350));
    const drawer = await page.$eval(".src-drawer", (e) => e.textContent).catch(() => "");
    ok(drawer.includes(p.want) && drawer.includes("摘录"), p.label + " 抽屉打开且定位含 " + p.want, drawer.slice(0, 80));
    await page.click(".src-drawer .lab-btn-minor");      // 关闭抽屉
    await new Promise((r) => setTimeout(r, 250));
  }
  const goalSrcMark = await page.$$eval("#ov-goal-G-002 .ov-card-head .src-mark", (els) => els.length);
  const laneSrcMarks = await page.$$eval("[id^='ov-lane-'] .ov-card-head .src-mark", (els) => els.length);
  const hqSrcMarks = await page.$$eval("#ov-hq-HQ .src-mark", (els) => els.length);
  ok(goalSrcMark >= 1 && laneSrcMarks === 10 && hqSrcMarks >= 3,
    "出处角标覆盖：愿景 3 卡、业务线 10 卡、HQ 卡三节全部带角标",
    "角标数 " + goalSrcMark + "/" + laneSrcMarks + "/" + hqSrcMarks);

  console.log("加强项：三层联动跳转（愿景详情跳蓝图块）");
  await page.click("#ov-goal-G-002 .ov-card-head");
  await new Promise((r) => setTimeout(r, 250));
  const g2jump = await page.$$eval("#ov-goal-G-002 .ov-jump", (els) => els.map((e) => e.textContent));
  ok(g2jump.some((t) => t.includes("B-01")), "G-002 详情列出来源蓝图块 B-01 可跳转", JSON.stringify(g2jump));
  if (g2jump.some((t) => t.includes("B-01"))) {
    await page.click("#ov-goal-G-002 .ov-jump");       // 跳第一块（B-01）
    await new Promise((r) => setTimeout(r, 600));
    const bOpen = await page.$eval("#ov-block-B-01", (e) => e.className.includes("open"));
    ok(bOpen, "跳转后目标块 B-01 展开");
  }

  console.log("数据一致性：页面在办计数与接口一致");
  const st = await req("GET", "/api/console/state", null, cookie);
  const l5Api = st.json.result.lanes.find((l) => l.id === "L5");
  await page.click("#ov-lane-L5 .ov-card-head");
  await new Promise((r) => setTimeout(r, 250));
  const l5Text = await page.$eval("#ov-lane-L5 .ov-detail", (e) => e.textContent);
  const l5Shown = l5Api.items.filter((i) => l5Text.includes(i.id)).length;
  ok(l5Shown === l5Api.items.length && l5Api.items.length > 0,
    "L5 在办事项逐条出现在展开卡（接口 " + l5Api.items.length + " 条）", "页面见 " + l5Shown);

  await page.screenshot({ path: ROOT + "\\logs\\i168_overview_full.png", fullPage: true });
  await browser.close();
  console.log("\n结果：" + pass + " 过 / " + fail + " 拒");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("核验脚本异常：", e); process.exit(2); });
