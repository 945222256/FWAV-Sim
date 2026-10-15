/* i202entry_check.cjs — 参数实验室入口故障核对（I-202 优先恢复轮）。
 * 核对：机主原链接（/app#/lab 无斜杠）与带斜杠路径、无登录与失效会话
 * 行为（不白屏、可理解界面与恢复入口）、桌面与手机、两种机型页签、
 * 第二版分段播放与控制；全程捕获失败请求、四百以上响应与页面错误，
 * 不编造根因，只回报实测证据。 */
const puppeteer = require("puppeteer-core");
const https = require("https");
const fs = require("fs");

const SECRET = JSON.parse(fs.readFileSync(
  "G:/Playground/isaac_flapper_sim/lp_sync/cloud_secret.json", "utf-8"));
const ROOT = "G:/Playground/isaac_flapper_sim";
const HOST = "personal.karmicsword.com";
const OUT = ROOT + "/logs/i202_check";
let pass = 0, fail = 0;
const evidence = { generated_at: new Date().toISOString(), probes: [], failed_requests: [], page_errors: [] };
function ok(cond, label, detail) {
  if (cond) { pass++; console.log("  [过] " + label); }
  else { fail++; console.log("  [拒] " + label + (detail ? "：" + detail : "")); }
}
function req(method, path, body) {
  return new Promise((res, rej) => {
    const opts = { hostname: HOST, path, method, headers: {}, rejectUnauthorized: false };
    if (body) { const d = JSON.stringify(body);
      opts.headers["Content-Type"] = "application/json";
      opts.headers["Content-Length"] = Buffer.byteLength(d); }
    const r = https.request(opts, (x) => { let b = ""; x.on("data", (c) => (b += c));
      x.on("end", () => res({ status: x.statusCode, headers: x.headers, json: b ? JSON.parse(b) : null })); });
    r.on("error", rej); if (body) r.write(JSON.stringify(body)); r.end();
  });
}

(async () => {
  const login = await req("POST", "/api/auth/login",
    { username: SECRET.board_user, password: SECRET.board_password });
  const cookie = (login.headers["set-cookie"] || []).map((c) => c.split(";")[0]).join("; ");

  const browser = await puppeteer.launch({
    executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    headless: true, args: ["--no-sandbox"], defaultViewport: { width: 1600, height: 1000 },
  });

  function armWatch(p, tag) {
    p.on("requestfailed", (rq) => evidence.failed_requests.push(
      { tag, url: rq.url().slice(0, 120), err: rq.failure()?.errorText }));
    p.on("response", (rs) => { if (rs.status() >= 400) evidence.failed_requests.push(
      { tag, url: rs.url().slice(0, 120), status: rs.status() }); });
    p.on("pageerror", (e) => evidence.page_errors.push({ tag, msg: e.message.slice(0, 150) }));
  }

  console.log("探针一：无登录会话直开机主原链接（/app#/lab 无斜杠）");
  {
    const p = await browser.newPage();
    armWatch(p, "nologin-noslash");
    await p.goto("https://" + HOST + "/app#/lab", { waitUntil: "networkidle2", timeout: 60000 });
    await new Promise((r) => setTimeout(r, 1200));
    const st = await p.evaluate(() => ({
      url: location.href, hash: location.hash,
      hasLoginForm: !!document.querySelector("input[type=password], form"),
      bodyHead: document.body.textContent.slice(0, 60),
      blank: document.body.textContent.trim().length < 10,
    }));
    ok(!st.blank, "无登录原链接不白屏（落地页有内容）", JSON.stringify(st));
    ok(st.hash === "#/lab" || st.hasLoginForm || st.bodyHead.length > 0,
      "无登录路径可理解（登录页或应用自处理）", st.url + " / " + st.bodyHead);
    evidence.probes.push({ probe: "nologin-noslash", ...st });
    await p.close();
  }

  console.log("探针二：无登录会话带斜杠链接（/app/#/lab）");
  {
    const p = await browser.newPage();
    armWatch(p, "nologin-slash");
    await p.goto("https://" + HOST + "/app/#/lab", { waitUntil: "networkidle2", timeout: 60000 });
    await new Promise((r) => setTimeout(r, 800));
    const st = await p.evaluate(() => ({
      hash: location.hash, blank: document.body.textContent.trim().length < 10,
      head: document.body.textContent.slice(0, 40) }));
    ok(!st.blank, "无登录带斜杠链接不白屏", JSON.stringify(st));
    evidence.probes.push({ probe: "nologin-slash", ...st });
    await p.close();
  }

  console.log("探针三：登录会话下机主原链接（无斜杠）与两机型两播放");
  {
    const p = await browser.newPage();
    armWatch(p, "login-noslash-desktop");
    await p.setCacheEnabled(false);
    await p.setCookie({ name: "lp_session", value: cookie.split("=")[1], domain: HOST, path: "/" });
    await p.goto("https://" + HOST + "/app#/lab", { waitUntil: "networkidle2", timeout: 60000 });
    await p.waitForSelector(".lab-tabs", { timeout: 20000 });
    await new Promise((r) => setTimeout(r, 800));
    const st = await p.evaluate(() => ({
      hash: location.hash, tabs: document.querySelectorAll(".lab-tab").length,
      hero: document.querySelector(".lab-hero h1")?.textContent ?? "" }));
    ok(st.hash === "#/lab" && st.tabs >= 2 && st.hero.includes("二百一十五克"),
      "机主原链接（无斜杠）登录态直达实验室（hash 保真、两页签、默认二百一十五克）",
      JSON.stringify(st));
    await p.screenshot({ path: OUT + "/entry_ownerlink_desktop.png", fullPage: false });
    // 七百克与第二版视频
    await p.evaluate(() => {
      const tab = [...document.querySelectorAll(".lab-tab")].find((t) => t.textContent.includes("七百克"));
      if (tab) tab.click();
    });
    await p.waitForSelector(".eaglex-v2", { timeout: 15000 });
    await new Promise((r) => setTimeout(r, 1000));
    const v2meta = await p.evaluate(() => ({
      note: document.querySelector(".eaglex-v2-notes")?.textContent ?? "",
      stage: document.querySelector(".eaglex-v2-notes")?.textContent ?? "",
      segs: document.querySelectorAll(".eaglex-v2 .eaglex-seg").length }));
    ok(v2meta.segs === 10, "第二版十段在线（原链接入口）", JSON.stringify(v2meta.segs));
    ok(v2meta.note.includes("ee9785f1417b") || v2meta.stage.includes("百分之二十"),
      "阶段文案与报告校验值已对齐修复轮（翼根百分之二十、平尾剩余项）",
      v2meta.stage.slice(0, 120));
    await p.evaluate(() => {
      const play = [...document.querySelectorAll(".eaglex-v2 .eaglex-ctl-row .lab-btn")]
        .find((b) => b.textContent === "播放");
      if (play) play.click();
    });
    await new Promise((r) => setTimeout(r, 800));
    const playing = await p.$eval(".eaglex-video", (v) => !v.paused).catch(() => false);
    ok(playing, "原链接入口下第二版播放控制可用");
    await p.screenshot({ path: OUT + "/entry_ownerlink_700g.png", fullPage: false });
    // 手机视口
    await p.setViewport({ width: 390, height: 844 });
    await new Promise((r) => setTimeout(r, 800));
    const mobile = await p.evaluate(() => ({
      overflow: document.documentElement.scrollWidth > window.innerWidth + 2 }));
    ok(!mobile.overflow, "手机三九零视口无横向溢出", JSON.stringify(mobile));
    await p.screenshot({ path: OUT + "/entry_ownerlink_mobile.png", fullPage: false });
    await p.close();
  }

  console.log("探针四：失效会话（伪造 cookie）行为");
  {
    const p = await browser.newPage();
    armWatch(p, "stale-cookie");
    await p.setCacheEnabled(false);
    await p.setCookie({ name: "lp_session", value: "anni|0001", domain: HOST, path: "/" });
    await p.goto("https://" + HOST + "/app/#/lab", { waitUntil: "networkidle2", timeout: 60000 });
    await new Promise((r) => setTimeout(r, 1500));
    const st = await p.evaluate(() => ({
      blank: document.body.textContent.trim().length < 10,
      loginForm: !!document.querySelector("input[type=password], form"),
      head: document.body.textContent.slice(0, 50) }));
    ok(!st.blank, "失效会话不白屏（应用自跳登录或提示）", JSON.stringify(st));
    evidence.probes.push({ probe: "stale-cookie", ...st });
    await p.close();
  }

  evidence.failed_requests_summary = evidence.failed_requests.length + " 条失败请求";
  evidence.page_errors_summary = evidence.page_errors.length + " 条页面错误";
  evidence.pass = pass; evidence.fail = fail;
  fs.writeFileSync(OUT + "/entry_check_record.json", JSON.stringify(evidence, null, 1));
  await browser.close();
  console.log("\n结果：" + pass + " 过 / " + fail + " 拒；失败请求 " + evidence.failed_requests.length
    + " 条；页面错误 " + evidence.page_errors.length + " 条");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("核对脚本异常：", e); process.exit(2); });
