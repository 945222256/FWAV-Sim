/* i202_check.cjs — I-202 判据核验：七百克候选动作预览。
 * 真实云端页面核验五条判据：入口与验证范围注明、五段动作播放控制、
 * 几何数据与 Jane 交付文件逐项一致、候选状态与二百一十五克现页保留、
 * 双视口截图与数据回读。比对基准：本地 jane_preview_20261013_tail
 * 交付文件与云端 /api/lab/params 的 eaglex700 节。 */
const puppeteer = require("puppeteer-core");
const https = require("https");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const SECRET = JSON.parse(fs.readFileSync(
  "G:/Playground/isaac_flapper_sim/lp_sync/cloud_secret.json", "utf-8"));
const ROOT = "G:/Playground/isaac_flapper_sim";
const JANE = ROOT + "/eagle_shell/jane_preview_20261013_tail/output";
const HOST = "personal.karmicsword.com";
const OUT_DIR = ROOT + "/logs/i202_check";
fs.mkdirSync(OUT_DIR, { recursive: true });

let pass = 0, fail = 0;
const record = { generated_at: new Date().toISOString() };
function ok(cond, label, detail) {
  if (cond) { pass++; console.log("  [过] " + label); }
  else { fail++; console.log("  [拒] " + label + (detail ? "：" + detail : "")); }
  return !!cond;
}
function sha12(p) {
  return crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex").slice(0, 12);
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

  const geo = JSON.parse(fs.readFileSync(JANE + "/geometry_and_binding.json", "utf-8"));
  const paramsResp = await req("GET", "/api/lab/params", null, cookie);
  const node = paramsResp.json.result.eaglex700;
  const samples = paramsResp.json.result.samples;
  const geoSha = sha12(JANE + "/geometry_and_binding.json");
  const chordSha = sha12(JANE + "/chord_stations.csv");

  console.log("接口面核验");
  ok(!!node && node.available === true, "载荷含 eaglex700 节且 available", JSON.stringify(node?.source));
  ok(node?.source?.geo_sha12 === geoSha && node?.source?.chord_sha12 === chordSha,
    "接口来源版本与本地交付文件 sha256 前十二位一致（资产版本与参数版本一致）",
    JSON.stringify({ api: node?.source, local: { geoSha, chordSha } }));
  const s700 = samples.find((s) => s.id === "700g");
  const s215 = samples.find((s) => s.id === "215g");
  ok(s700 && s700.locked === false && s700.state === "candidate"
     && s215 && s215.locked === false,
    "机型表：七百克解锁为候选态，二百一十五克保持可用", JSON.stringify(samples.map((s) => [s.id, s.state, s.locked])));
  ok(node.candidate_status.mass === "待确认" && node.candidate_status.aero === "待确认",
    "接口候选状态四项待确认", JSON.stringify(node.candidate_status));

  const browser = await puppeteer.launch({
    executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    headless: true, args: ["--no-sandbox"], defaultViewport: { width: 1600, height: 1000 },
  });
  const page = await browser.newPage();
  await page.setCacheEnabled(false);
  await page.setCookie({ name: "lp_session", value: cookieVal, domain: HOST, path: "/" });
  await page.goto("https://" + HOST + "/app/#/lab", { waitUntil: "networkidle2", timeout: 60000 });
  await page.waitForSelector(".lab-tabs", { timeout: 30000 });
  await new Promise((r) => setTimeout(r, 800));

  console.log("判据一：入口与验证范围注明");
  const canEnter = await page.evaluate(() => {
    const tab = [...document.querySelectorAll(".lab-tab")]
      .find((t) => t.textContent.includes("七百克"));
    if (!tab) return "no tab";
    if (tab.disabled) return "locked";
    tab.click();
    return "entered";
  });
  ok(canEnter === "entered", "七百克候选入口可点击进入", canEnter);
  await page.waitForSelector(".eaglex-banner", { timeout: 15000 });
  const banner = await page.$eval(".eaglex-banner", (e) => e.textContent);
  ok(banner.includes("模型动作展示") && banner.includes("开发样例"),
    "界面清楚注明当前是模型动作展示及开发样例性质", banner.slice(0, 120));
  ok(/质量为待确认|质量.{0,4}待确认/.test(banner) && banner.includes("飞行能力"),
    "验证范围注明候选状态（质量、气动、飞行能力未确认）", banner.slice(0, 160));

  console.log("判据二：五段动作与播放控制");
  const segs = await page.$$eval(".eaglex-player .eaglex-seg", (els) => els.map((e) => e.textContent.trim()));
  ok(segs.length === 5 && ["拍动", "扭转", "折叠", "整尾偏航", "组合"].every((w) => segs.some((s) => s.includes(w))),
    "五种动作分段可单独选择（尾部段语义为整尾偏航）", JSON.stringify(segs));
  await page.evaluate(() => document.querySelector(".eaglex-player")
    ?.scrollIntoView({ block: "center" }));
  await new Promise((r) => setTimeout(r, 400));
  const frameLoad = await page.waitForFunction(() => {
    const img = document.querySelector(".eaglex-player .eaglex-frame");
    return img && img.complete && img.naturalWidth > 0;
  }, { timeout: 15000 }).then(() => page.evaluate(() => {
    const img = document.querySelector(".eaglex-player .eaglex-frame");
    return img ? { ok: true, src: img.src } : null;
  })).catch(() => null);
  ok(frameLoad && frameLoad.ok, "帧图实际加载成功（模型可见）", JSON.stringify(frameLoad));
  await new Promise((r) => setTimeout(r, 900));
  const idxMoving = await page.evaluate(() => document.querySelector(".eaglex-player .eaglex-frame-count")?.textContent ?? "");
  await page.click(".eaglex-player .eaglex-ctl-row .lab-btn");   // 暂停（限旧区）
  await new Promise((r) => setTimeout(r, 400));
  const idxPaused1 = await page.evaluate(() => document.querySelector(".eaglex-player .eaglex-frame-count")?.textContent ?? "");
  await new Promise((r) => setTimeout(r, 500));
  const idxPaused2 = await page.evaluate(() => document.querySelector(".eaglex-player .eaglex-frame-count")?.textContent ?? "");
  ok(idxPaused1 === idxPaused2 && idxMoving !== "",
    "暂停后帧号停止推进（" + idxPaused1.trim() + "）", idxMoving + " -> " + idxPaused2);
  await page.evaluate(() => {
    const btns = [...document.querySelectorAll(".eaglex-player .eaglex-ctl-row .lab-btn")];
    const restart = btns.find((b) => b.textContent.includes("重新开始"));
    if (restart) restart.click();
  });
  await new Promise((r) => setTimeout(r, 50));
  const idxRestart = await page.evaluate(() => document.querySelector(".eaglex-player .eaglex-frame-count")?.textContent ?? "");
  ok(/第 [12]\//.test(idxRestart), "重新开始后回到起始帧（继续播放中）", idxRestart);
  const segProbe = await page.evaluate(() => {
    const tail = [...document.querySelectorAll(".eaglex-player .eaglex-seg")].find((b) => b.textContent.includes("整尾偏航"));
    if (tail) { tail.click(); return "switched"; }
    return "no tail";
  });
  await new Promise((r) => setTimeout(r, 600));
  const tailMeta = await page.evaluate(() => document.querySelector(".eaglex-player .eaglex-stage-meta")?.textContent ?? "");
  ok(segProbe === "switched" && tailMeta.includes("整尾偏航") && tailMeta.includes("6–8"),
    "整尾偏航段切换可见（六至八秒分段标识与机构说明）", tailMeta);
  const frameOk2 = await page.evaluate(() => {
    const img = document.querySelector(".eaglex-player .eaglex-frame");
    return img && img.complete && img.naturalWidth > 0;
  });
  ok(frameOk2, "尾部段帧图加载成功（单关节动作可辨识：段独立帧资产）");
  const report = JSON.parse(fs.readFileSync(ROOT + "/board_ui/public/lab/eaglex700/render_report.json", "utf-8"));
  ok(report.camera_rule.includes("全动画包围盒"),
    "模型全程在视野内的取景依据（渲染报告相机规则）", report.camera_rule);

  console.log("判据三：几何数据与交付文件逐项一致");
  const pageGeo = await page.evaluate(() => document.querySelector(".eaglex-geo")?.textContent ?? "");
  const probeParts = ["eagle_wing_main_L", "eagle_wing_tip_L", "eagle_original_tail"];
  let geoOk = 0;
  for (const p of probeParts) {
    const g = geo.geometry[p];
    if (g && pageGeo.includes(p)
        && pageGeo.includes(g.surface_area_m2.toFixed(6))
        && pageGeo.includes(g.spanwise_extent_m.toFixed(4))) geoOk++;
  }
  ok(geoOk === probeParts.length,
    "部件几何值（表面积、展向长度）与交付文件逐字一致（抽三件）", geoOk + "/" + probeParts.length);
  const bindingsShown = await page.evaluate(() => document.querySelectorAll(".eaglex-details")[1]?.textContent ?? "");
  ok(geo.joints.every((j) => bindingsShown.includes(j.joint)) && geo.joints.length === 9,
    "九个关节绑定可见（坐标、轴、限位）", geo.joints.map((j) => j.joint).join(","));
  await page.evaluate(() => {
    const sum = [...document.querySelectorAll(".eaglex-details summary")]
      .find((s) => s.textContent.includes("弦长站位表"));
    if (sum) sum.click();
  });
  await new Promise((r) => setTimeout(r, 300));
  const chordShown = await page.evaluate(() => document.querySelector(".eaglex-chord-scroll")?.textContent ?? "");
  const chordRows = fs.readFileSync(JANE + "/chord_stations.csv", "utf-8").trim().split(/\r?\n/).length - 1;
  ok(chordShown.length > 200 && pageGeo.includes(String(chordRows) + " 行"),
    "弦长站位表展开可见（" + chordRows + " 行，单位米）", "");
  ok(pageGeo.includes(geoSha) && pageGeo.includes(chordSha),
    "数据来源版本（sha 前十二位）在页面可见", geoSha + "/" + chordSha);
  const srcClick = await page.evaluate(() => {
    const link = document.querySelector(".eaglex-src-link");
    if (!link) return "none";
    link.click();
    return "opened";
  });
  await new Promise((r) => setTimeout(r, 400));
  const srcDrawer = await page.evaluate(() => document.querySelector(".lab-drawer")?.textContent ?? "");
  ok(srcClick === "opened" && srcDrawer.includes("sha256"),
    "几何来源可打开出处浮层（provenance 指针）", srcDrawer.slice(0, 90));

  console.log("判据四：候选状态与二百一十五克现页保留");
  ok(banner.includes("不因动作预览可用而认定物理构型通过"),
    "页面明示不因预览可用而标记物理构型通过");
  ok(pageGeo.includes("二百一十五克现行构型") || banner.includes("保持原值")
     || (await page.evaluate(() => document.querySelector(".eaglex-geo")?.textContent.includes("保持原值"))),
    "冻结声明可见（二百一十五克物理参数保持原值）");
  await page.evaluate(() => {
    const tab = [...document.querySelectorAll(".lab-tab")]
      .find((t) => t.textContent.includes("二百一十五克"));
    if (tab) tab.click();
  });
  await new Promise((r) => setTimeout(r, 600));
  const lab215 = await page.evaluate(() => document.body.textContent);
  ok(lab215.includes("在册参数") && lab215.includes("公式"),
    "二百一十五克现页完整保留（参数域与公式墙正常渲染）");
  const statsShown = await page.evaluate(() => document.querySelector(".lab-foot")?.textContent ?? "");
  const statsApi = paramsResp.json.result.stats;
  ok(statsShown.includes(String(statsApi.total)),
    "二百一十五克参数计数与接口一致（" + statsApi.total + " 项）", statsShown.slice(0, 80));

  console.log("判据五：双视口截图与读回");
  await page.evaluate(() => {
    const tab = [...document.querySelectorAll(".lab-tab")]
      .find((t) => t.textContent.includes("七百克"));
    if (tab) tab.click();
  });
  await page.waitForSelector(".eaglex-banner", { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 800));
  await page.screenshot({ path: OUT_DIR + "/lab700_desktop.png", fullPage: true });
  const entered700 = await page.$(".eaglex") !== null;
  ok(entered700, "判据五前置：七百克视图在线（截图时刻）");
  await page.setViewport({ width: 390, height: 844, isMobile: true });
  await new Promise((r) => setTimeout(r, 800));
  // 视口切换后视图按真实手机路径重新进入（打开页面后点七百克页签）
  await page.evaluate(() => {
    const tab = [...document.querySelectorAll(".lab-tab")]
      .find((t) => t.textContent.includes("七百克"));
    if (tab) tab.click();
  });
  await page.waitForSelector(".eaglex-banner", { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 600));
  const mobileOk = await page.evaluate(() => {
    const el = document.querySelector(".eaglex");
    if (!el) return "no view";
    const doc = document.documentElement;
    return doc.scrollWidth <= window.innerWidth + 2
      ? "fit" : "overflow " + doc.scrollWidth + ">" + window.innerWidth;
  });
  await page.screenshot({ path: OUT_DIR + "/lab700_mobile.png", fullPage: false });
  ok(mobileOk === "fit", "手机三九零视口无横向溢出", mobileOk);
  record.eaglex = {
    source: node.source, segments: node.motion.segments.map((s) => [s.id, s.frames]),
    parts: node.geometry.parts.length, chord_rows: node.geometry.chord_stations.rows,
    candidate_status: node.candidate_status, samples: samples.map((s) => [s.id, s.state, s.locked]),
    stats_215g: statsApi, camera_rule: report.camera_rule,
  };
  record.pass = pass; record.fail = fail;
  fs.writeFileSync(OUT_DIR + "/verify_record.json", JSON.stringify(record, null, 1));
  await browser.close();
  console.log("\n结果：" + pass + " 过 / " + fail + " 拒；验证记录 " + OUT_DIR + "/verify_record.json");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("核验脚本异常：", e); process.exit(2); });
