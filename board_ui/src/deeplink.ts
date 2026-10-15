/* deeplink.ts — 深度链接契约的前端唯一解析实现（契约 android_shell/deeplink_contract.json）。
 * 七路由：home / item/{id} / lanes / graph / line/{id} / lab / console；
 * 未匹配或参数不合法按 home 处理。lab 是参数实验室独立页（I-130），
 * console 是编排控制台独立页（I-144 起）。 */

export type DeepLink =
  | { route: "home" }
  | { route: "item"; itemId: string }
  | { route: "lanes" }
  | { route: "graph" }
  | { route: "line"; lineId: string }
  | { route: "lab" }
  | { route: "console" };

const ITEM_RE = /^I-[0-9]{3}$/;
const LINE_RE = /^[A-Za-z0-9_-]+$/;

export function parseHash(hash: string): DeepLink {
  // 防御多前导斜杠：壳侧历史 bug 曾拼出 #///item/I-055。
  let seg = hash.replace(/^#\/+/, "").trim();
  // I-136 锚点直达：先剥查询段再路由（如 #/lab?p=键名），既有
  // 七路由的解析行为不变；查询参数由各页面自行读取。
  const q = seg.indexOf("?");
  if (q >= 0) seg = seg.slice(0, q);
  if (!seg || seg === "home") return { route: "home" };
  const slash = seg.indexOf("/");
  const name = slash < 0 ? seg : seg.slice(0, slash);
  const arg = slash < 0 ? "" : seg.slice(slash + 1);
  if (name === "item" && ITEM_RE.test(arg)) return { route: "item", itemId: arg };
  if (name === "lanes") return { route: "lanes" };
  if (name === "graph") return { route: "graph" };
  if (name === "line" && LINE_RE.test(arg)) return { route: "line", lineId: arg };
  if (name === "lab") return { route: "lab" };
  if (name === "console") return { route: "console" };
  return { route: "home" };
}

/* 把当前视图写回地址栏（replaceState，不产生历史记录），
 * 使页面上的任何状态都可被再次直达。 */
export function syncHash(link: DeepLink) {
  let hash = "#/home";
  if (link.route === "item") hash = "#/item/" + link.itemId;
  else if (link.route === "lanes") hash = "#/lanes";
  else if (link.route === "graph") hash = "#/graph";
  else if (link.route === "line") hash = "#/line/" + link.lineId;
  else if (link.route === "lab") hash = "#/lab";
  else if (link.route === "console") hash = "#/console";
  if (location.hash !== hash) {
    history.replaceState(null, "", hash);
  }
}
