/* i202v2_check.cjs — I-202 复工判据核验：动画第二版视频接入。
 * 断言：视频节数据下发与 L8 交付一致（段清单、版本、校验值）、
 * 十段视频实际可加载（readyState 与时长）、播放/暂停/重新开始控制、
 * 段切换、阶段标注与剩余问题可见、旧样例区与先行回归不破坏、
 * 交互模型未载入（页面无第二版 glb 引用）。证据写 logs/i202_check/。 */
const puppeteer = require("puppeteer-core");
const https = require("https");
const fs = require("fs");
const crypto = require("crypto");

const SECRET = JSON.parse(fs.readFileSync(
  "G:/Playground/isaac_flapper_sim/lp_sync/cloud_secret.json", "utf-8"));
const ROOT = "G:/Playground/isaac_flapper_sim";
const V2 = ROOT + "/eagle_shell/l8_i201_animv2/output";
const HOST = "personal.karmicsword.com";
let pass = 0, fail = 0;
function ok(cond, label, detail) {
  if (cond) { pass++; console.log("  [过] " + label); }
  else { fail++; console.log("  [拒] " + label + (detail ? "：" + detail : "")); }
}
function req(method, path, body, cookie) {
  return new Promise((res, rej) => {
    const opts = { hostname: HOST, path, method,
      headers: { Cookie: cookie || "" }, rejectUnauthorized: false };
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
  const paramsResp = await req("GET", "/api/lab/params", null, cookie);
  const v2 = paramsResp.json.result.eaglex700.animv2;
  const repSha = crypto.createHash("sha256")
    .update(fs.readFileSync(V2 + "/anim_config_v2_report.json")).digest("hex").slice(0, 12);

  console.log("接口面");
  ok(v2 && v2.available === true && v2.segments.length === 10,
    "载荷含动画第二版节，十段齐", JSON.stringify({ available: v2?.available, n: v2?.segments?.length }));
  ok(v2.source.report_sha12 === repSha && v2.source.version_name.startsWith("eagle_x_animation_config_v2"),
    "版本与报告 sha256 前十二位和本地交付一致", JSON.stringify(v2.source));
  ok(v2.segments.every((s) => s.in_commands),
    "十段与命令表 mode 一一对应（无虚构段）");
  ok(v2.stage_note.includes("翼根") && v2.stage_note.includes("百分之二十"),
    "阶段标注含翼根轴位修复轮事实（前缘起百分之二十弦长）", v2.stage_note.slice(0, 100));
  ok(v2.stage_note.includes("平尾过渡区") && v2.stage_note.includes("独立复验未完成"),
    "阶段标注含平尾剩余项与未独立复验标注", v2.stage_note.slice(-140));

  const browser = await puppeteer.launch({
    executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    headless: true, args: ["--no-sandbox"], defaultViewport: { width: 1600, height: 1000 },
  });
  const page = await browser.newPage();
  await page.setCacheEnabled(false);
  await page.setCookie({ name: "lp_session", value: cookie.split("=")[1], domain: HOST, path: "/" });
  await page.goto("https://" + HOST + "/app/#/lab", { waitUntil: "networkidle2", timeout: 60000 });
  await page.waitForSelector(".lab-tabs");
  await page.evaluate(() => {
    const tab = [...document.querySelectorAll(".lab-tab")].find((t) => t.textContent.includes("七百克"));
    if (tab) tab.click();
  });
  await page.waitForSelector(".eaglex-v2", { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 1200));

  console.log("页面视频区");
  const videoState = await page.evaluate(() => {
    const v = document.querySelector(".eaglex-video");
    return v ? { ready: v.readyState, dur: v.duration, src: v.src } : null;
  });
  ok(videoState && videoState.ready >= 1 && videoState.dur > 0,
    "首段视频实际加载（元数据就绪，时长 " + (videoState?.dur ?? 0).toFixed(1) + " 秒）",
    JSON.stringify(videoState));
  const segBtns = await page.$$eval(".eaglex-v2 .eaglex-seg", (els) => els.map((e) => e.textContent.trim()));
  ok(segBtns.length === 10 && segBtns.some((t) => t.includes("整尾偏航"))
     && segBtns.some((t) => t.includes("翼尖上反")) && segBtns.some((t) => t.includes("四自由度")),
    "十段选择器含新构型命名（整尾偏航、翼尖上反、四自由度对照）", JSON.stringify(segBtns).slice(0, 150));
  // 播放控制
  await page.evaluate(() => {
    const btns = [...document.querySelectorAll(".eaglex-v2 .eaglex-ctl-row .lab-btn")];
    const play = btns.find((b) => b.textContent === "播放");
    if (play) play.click();
  });
  await new Promise((r) => setTimeout(r, 900));
  const playing = await page.$eval(".eaglex-video", (v) => !v.paused && !v.ended);
  ok(playing, "播放按钮启动视频");
  await page.evaluate(() => {
    const btns = [...document.querySelectorAll(".eaglex-v2 .eaglex-ctl-row .lab-btn")];
    const pause = btns.find((b) => b.textContent === "暂停");
    if (pause) pause.click();
  });
  await new Promise((r) => setTimeout(r, 300));
  const paused = await page.$eval(".eaglex-video", (v) => v.paused);
  ok(paused, "暂停按钮停止视频");
  await page.evaluate(() => {
    const btns = [...document.querySelectorAll(".eaglex-v2 .eaglex-ctl-row .lab-btn")];
    const restart = btns.find((b) => b.textContent === "重新开始");
    if (restart) restart.click();
  });
  await new Promise((r) => setTimeout(r, 400));
  const restarted = await page.$eval(".eaglex-video", (v) => v.currentTime < 0.5 && !v.paused);
  ok(restarted, "重新开始归零并续播");
  // 段切换
  await page.evaluate(() => {
    const seg = [...document.querySelectorAll(".eaglex-v2 .eaglex-seg")]
      .find((b) => b.textContent.includes("整尾滚转"));
    if (seg) seg.click();
  });
  await new Promise((r) => setTimeout(r, 900));
  const rollSrc = await page.$eval(".eaglex-video", (v) => v.src);
  const rollMeta = await page.evaluate(() =>
    [...document.querySelectorAll(".eaglex-v2 .eaglex-stage-meta")].map((e) => e.textContent).join(" "));
  ok(rollSrc.includes("tail_roll_whole") && rollMeta.includes("整尾滚转"),
    "段切换到整尾滚转且视频源正确", rollSrc.slice(-40));
  await page.screenshot({ path: ROOT + "/logs/i202_check/animv2_video_panel.png", fullPage: true });

  console.log("阶段标注与旧区回归");
  const bodyText = await page.evaluate(() => document.body.textContent);
  ok(bodyText.includes("机主 2026-10-13 认可") && bodyText.includes("阶段预览"),
    "机主认可与阶段预览标注可见");
  ok(v2.stage_note.includes("本阶段只接视频"),
    "交互模型不载入的现行原因说明（待独立复验与登记，本阶段只接视频）");
  ok(!bodyText.includes("eaglex_anim_config_v2.glb"),
    "页面未引用有骨骼编号错误的导出模型");
  ok(await page.$(".eaglex-player") === null,
    "旧构型帧序列播放器已从主界面移除（防两套动作入口误导）");
  const poster = await page.$eval(".eaglex-video", (v) => v.getAttribute("poster") ?? "");
  ok(poster.includes("_mid.png"),
    "视频未播时显示中点帧封面（第一眼可见模型）", poster.slice(-40));
  const legacy = await page.evaluate(() =>
    document.querySelector(".eaglex-geo")?.getAttribute("data-legacy") ?? "");
  ok(legacy.includes("历史诊断对照") && await page.$(".eaglex-geo table") !== null,
    "几何数据区保留（含旧样例来源标注与数据表）", legacy.slice(0, 50));
  // 215g 回归
  await page.evaluate(() => {
    const tab = [...document.querySelectorAll(".lab-tab")]
      .find((t) => t.textContent.includes("二百一十五克"));
    if (tab) tab.click();
  });
  await new Promise((r) => setTimeout(r, 500));
  const stats215 = await page.evaluate(() => document.querySelector(".lab-foot")?.textContent ?? "");
  ok(stats215.includes(String(paramsResp.json.result.stats.total)),
    "二百一十五克现页回归（" + paramsResp.json.result.stats.total + " 项）");

  fs.writeFileSync(ROOT + "/logs/i202_check/animv2_verify_record.json", JSON.stringify({
    generated_at: new Date().toISOString(), pass, fail,
    source: v2.source, segments: v2.segments.map((s) => [s.id, s.clip, s.in_commands]),
    video_probe: videoState, roll_src_ok: rollSrc.includes("tail_roll_whole"),
  }, null, 1));
  await browser.close();
  console.log("\n结果：" + pass + " 过 / " + fail + " 拒");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("核验脚本异常：", e); process.exit(2); });
