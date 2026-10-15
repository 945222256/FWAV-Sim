/* i202pixel_check.cjs — 黑屏修复像素级验证（I-202）。
 * 不以 readyState 或 HTTP 状态代替像素证据：直接截取视频元素区域，
 * 交由 Python PIL 统计亮度分布。断言：未播（poster 封面）画面非黑、
 * 播放中画面非黑、失败注入时错误横幅出现、旧播放器入口已移除。 */
const puppeteer = require("puppeteer-core");
const https = require("https");
const fs = require("fs");
const { execFileSync } = require("child_process");

const SECRET = JSON.parse(fs.readFileSync(
  "G:/Playground/isaac_flapper_sim/lp_sync/cloud_secret.json", "utf-8"));
const ROOT = "G:/Playground/isaac_flapper_sim";
const HOST = "personal.karmicsword.com";
const OUT = ROOT + "/logs/i202_check";
let pass = 0, fail = 0;
function ok(cond, label, detail) {
  if (cond) { pass++; console.log("  [过] " + label); }
  else { fail++; console.log("  [拒] " + label + (detail ? "：" + detail : "")); }
}
function analyze(pngPath) {
  const out = execFileSync("python", ["-c", `
from PIL import Image
import sys
im = Image.open(r"${pngPath}").convert("L")
px = list(im.getdata())
n = len(px)
dark = sum(1 for v in px if v < 24) / n
mean = sum(px) / n
print(f"{dark:.3f} {mean:.1f}")
`], { encoding: "utf-8" }).trim().split(" ");
  return { dark_ratio: parseFloat(out[0]), mean_luma: parseFloat(out[1]) };
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
  await new Promise((r) => setTimeout(r, 1500));

  console.log("像素证据一：未播状态（poster 封面）");
  await page.evaluate(() => document.querySelector(".eaglex-v2")
    ?.scrollIntoView({ block: "center" }));
  await new Promise((r) => setTimeout(r, 600));
  const pausedBefore = await page.$eval(".eaglex-video", (v) => v.paused);
  const box = await page.$eval(".eaglex-video", (v) => {
    const r = v.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y),
             w: Math.round(r.width), h: Math.round(r.height) };
  });
  await page.screenshot({ path: OUT + "/pixel_idle.png",
    clip: { x: box.x, y: box.y, width: box.w, height: box.h } });
  const idle = analyze(OUT + "/pixel_idle.png");
  ok(pausedBefore && idle.dark_ratio < 0.5 && idle.mean_luma > 30,
    "未播画面非黑（封面可见模型：暗像素占比 " + idle.dark_ratio.toFixed(3) + "，平均亮度 " + idle.mean_luma.toFixed(1) + "）",
    JSON.stringify(idle));

  console.log("像素证据二：播放中画面");
  await page.evaluate(() => {
    const play = [...document.querySelectorAll(".eaglex-v2 .eaglex-ctl-row .lab-btn")]
      .find((b) => b.textContent === "播放");
    if (play) play.click();
  });
  await new Promise((r) => setTimeout(r, 900));
  const pausedMid = await page.$eval(".eaglex-video", (v) => v.paused);
  await page.screenshot({ path: OUT + "/pixel_playing.png",
    clip: { x: box.x, y: box.y, width: box.w, height: box.h } });
  const playing = analyze(OUT + "/pixel_playing.png");
  ok(!pausedMid && playing.dark_ratio < 0.5 && playing.mean_luma > 30,
    "播放中画面非黑（暗像素占比 " + playing.dark_ratio.toFixed(3) + "，平均亮度 " + playing.mean_luma.toFixed(1) + "）",
    JSON.stringify(playing));

  console.log("像素证据三：失败反馈与旧入口");
  // 注入坏源（页面内改 src 触发 onError）
  await page.evaluate(() => {
    const v = document.querySelector(".eaglex-video");
    if (v) v.src = "/app/lab/eaglex700v2/clips/not_exist.mp4";
  });
  await new Promise((r) => setTimeout(r, 1200));
  const errShown = await page.evaluate(() =>
    document.querySelector(".eaglex-v2-error")?.textContent ?? "");
  ok(errShown.includes("视频加载失败"),
    "播放失败给明确反馈横幅", errShown.slice(0, 60));
  ok(await page.$(".eaglex-player") === null,
    "旧灰屏播放器已从主界面移除");
  ok(await page.$(".eaglex-geo table") !== null,
    "几何数据区完整保留");

  fs.writeFileSync(OUT + "/pixel_check_record.json", JSON.stringify({
    generated_at: new Date().toISOString(),
    idle: { ...idle, paused: pausedBefore }, playing: { ...playing, paused: pausedMid },
    error_banner: errShown.slice(0, 80),
    legacy_player_removed: true, geo_table_kept: true,
    box, pass, fail,
  }, null, 1));
  await page.screenshot({ path: OUT + "/pixel_final_state.png", fullPage: false });
  await browser.close();
  console.log("\n结果：" + pass + " 过 / " + fail + " 拒");
  process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error("核验脚本异常：", e); process.exit(2); });
