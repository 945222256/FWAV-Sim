/** I-232持久验收：内存转译纯函数，真实载荷只读，无生产源码回写。
 * node scripts/i232_lab_semantics_check.mjs --payload ../outputs/.../real_old_lab_payload.json
 * 可重复传入 --payload 核对真实新载荷；结果固定写入本轮隔离证据目录。
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const UI = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ROOT = path.dirname(UI);
const OUT = path.join(ROOT, "outputs/nono_i232_subagent_20261015");
const filename = path.join(UI, "src/lab/semantics.ts");
const source = fs.readFileSync(filename, "utf8");
const js = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023,
}, reportDiagnostics: true });
assert.equal(js.diagnostics?.length ?? 0, 0);
const exports = {};
vm.runInNewContext("(function(exports) {" + js.outputText + "\n})", {}, { filename })(exports);
const S = exports;
const results = [];
const realPayloads = [];
function test(name, run) {
  try { run(); results.push({ name, pass: true }); }
  catch (error) { results.push({ name, pass: false, detail: String(error.message).slice(0, 700) }); }
}
const keys = (rows) => rows.map((p) => p.key).join(",");
const layout = { domains: [{ id: "d1", no: 1 }, { id: "d2", no: 2 }],
  families: [{ id: "f2", domain: "d2" }, { id: "gain", domain: "d1" }, { id: "f1", domain: "d1" }] };
function param(key, patch = {}) {
  return { key, family: "gain", value: "0.25", unit: "rad", sample: "215g",
    short_name: "通用名称（2）", explanation: "旧通用讲解", teaching: { definition: "旧定义" }, ...patch };
}
function semantic(patch = {}) {
  return { schema_version: 1, display_name: "已核对象的量", definition: "经逐项核对的完整定义",
    usage_tag: "current", consumer_state: "conditional", facets: { channel: ["yaw"], role: ["gain"] },
    review_state: { name: "verified", definition: "verified", usage: "verified" },
    applicability: { sample_scope: "unknown", default_path: "disabled", conditions: ["开关启用且处于试验模式"] },
    consumer_refs: [{ path: "modules/consumer.py", line: 20 }], ...patch };
}

test("旧载荷不以通用短名、旧讲解或旧sample冒充核定语义", () => {
  const p = param("legacy.key");
  assert.match(S.displayName(p), /legacy\.key/);
  assert.doesNotMatch(S.displayName(p), /通用名称/);
  assert.doesNotMatch(S.displayDefinition(p), /旧定义/);
  assert.equal(S.usageOf(p), "undetermined");
  assert.equal(S.sampleLabel(p), "适用样本待核实");
  assert.ok(S.metadataGaps(p).length);
});
test("名称已核而定义争议时仅采用名称，三槽状态独立", () => {
  const p = param("split.review", { semantics: semantic({ review_state: {
    name: "verified", definition: "disputed", usage: "declaration_only" } }) });
  assert.equal(S.displayName(p), "已核对象的量");
  assert.match(S.displayDefinition(p), /split\.review.*有争议/);
  assert.doesNotMatch(S.displayDefinition(p), /经逐项核对/);
  assert.equal(S.reviewOf(p, "name"), "verified");
  assert.equal(S.reviewOf(p, "definition"), "disputed");
  assert.equal(S.reviewOf(p, "usage"), "declaration_only");
});
for (const state of ["pending", "disputed", "stale", "declaration_only", "unreviewed", "future_state"]) {
  test("未核名称和定义不采纳原文：" + state, () => {
    const p = param("unverified." + state, { semantics: semantic({ review_state: { name: state, definition: state } }) });
    assert.match(S.displayName(p), /名称待核实/);
    assert.doesNotMatch(S.displayName(p), /已核对象/);
    assert.match(S.displayDefinition(p), /定义待核实/);
    assert.doesNotMatch(S.displayDefinition(p), /经逐项核对/);
  });
}
test("未来schema与扩展完整保留，但不能冒充已复核", () => {
  const p = param("future.key", { semantics: semantic({ schema_version: 99, extension: { opaque: [1, 2] } }) });
  const original = JSON.stringify(p);
  assert.match(S.displayName(p), /名称待核实/);
  assert.match(S.displayDefinition(p), /定义待核实/);
  assert.equal(S.usageOf(p), "undetermined");
  assert.equal(S.reviewOf(p, "name"), "unreviewed");
  assert.match(S.metadataGaps(p).join("；"), /版本暂不支持/);
  assert.equal(JSON.stringify(p), original);
});
test("1.0解析为JS数值1后仍服从后端unsupported_schema_version诊断", () => {
  const version = JSON.parse('{"schema_version":1.0}').schema_version;
  assert.equal(version, 1);
  for (const diagnosis of ["unsupported_schema_version", { code: "unsupported_schema_version" }]) {
    const p = param("float.version", { semantics: semantic({ schema_version: version }), semantics_diagnostics: [diagnosis] });
    const original = JSON.stringify(p);
    assert.equal(S.hasUnsupportedSchema(p), true);
    assert.equal(S.semanticsOf(p), null);
    assert.equal(S.usageOf(p), "undetermined");
    for (const slot of S.REVIEW_SLOTS) assert.equal(S.reviewOf(p, slot), "unreviewed");
    assert.match(S.displayName(p), /名称待核实.*float\.version/);
    assert.match(S.displayDefinition(p), /定义待核实.*float\.version/);
    assert.match(S.metadataGaps(p).join("；"), /版本暂不支持/);
    assert.equal(JSON.stringify(p), original);
  }
});
test("现行用途不等于默认启用，not_found不推断预留", () => {
  const current = param("conditional", { semantics: semantic() });
  assert.equal(S.usageOf(current), "current");
  assert.equal(S.defaultPathLabel(current), "默认路径关闭");
  const absent = param("absent", { semantics: semantic({ usage_tag: "undetermined", consumer_state: "not_found" }) });
  assert.equal(S.usageOf(absent), "undetermined");
  assert.match(S.metadataGaps(absent).join("；"), /已查范围|检索|范围与版本/);
});
test("四种用途标签各自保留，不借用graph状态", () => {
  for (const tag of ["current", "historical", "reserved", "undetermined"]) {
    assert.equal(S.usageOf(param(tag, { semantics: semantic({ usage_tag: tag }), graph: { status: "已完成" } })), tag);
  }
});
test("样本范围仅从规范applicability读取", () => {
  assert.equal(S.sampleLabel(param("common", { semantics: semantic({ applicability: { sample_scope: "common" } }) })), "已登记为通用样本范围");
  assert.equal(S.sampleLabel(param("listed", { semantics: semantic({ applicability: { sample_scope: "listed", sample_ids: ["700g"] } }) })), "已列适用样本：700g");
  assert.equal(S.sampleLabel(param("empty", { semantics: semantic({ applicability: { sample_scope: "listed", sample_ids: [] } }) })), "适用样本待核实");
});
const rows = [
  param("a", { semantics: semantic({ facets: { channel: ["yaw", "lateral", "yaw"], role: ["gain"] }, sort_order: 2 }) }),
  param("b", { semantics: semantic({ facets: { channel: ["altitude"], role: ["state_limit"] }, sort_order: 1 }) }),
  param("c", { semantics: semantic({ facets: { channel: ["lateral"], role: ["gain"] } }) }),
  param("unknown"),
];
test("同轴OR与跨轴AND，空筛选保留未知", () => {
  assert.equal(S.selectParams(rows, layout, S.emptyFilters()).length, 4);
  const f = { usage: "all", facets: { channel: ["yaw", "altitude"], role: ["gain"] } };
  assert.equal(keys(S.selectParams(rows, layout, f)), "a");
  assert.equal(keys(S.selectParams(rows, layout, { usage: "all", facets: { channel: [S.UNCLASSIFIED] } })), "unknown");
});
test("唯一key计数，重复分面不重复计入，分面命中允许交叠", () => {
  const duplicated = [...rows, rows[0]];
  assert.equal(S.uniqueParams(duplicated).length, 4);
  const counts = S.facetCounts(duplicated, "channel", S.emptyFilters());
  assert.equal(counts.get("yaw"), 1);
  assert.equal(counts.get("lateral"), 2);
  assert.equal(counts.get(S.UNCLASSIFIED), 1);
  assert.ok([...counts.values()].reduce((a, b) => a + b, 0) > 4);
});
test("分面计数只忽略本轴，继续满足其他轴", () => {
  const f = { usage: "all", facets: { channel: ["yaw"], role: ["gain"] } };
  const counts = S.facetCounts(rows, "channel", f);
  assert.equal(counts.get("lateral"), 2);
  assert.equal(counts.get("altitude"), 0);
});
test("未知受控ID原样可辨，不按键名猜标签", () => {
  assert.equal(S.facetLabel("component", "left_wing"), "左翼");
  assert.match(S.facetLabel("method", "unknown_method"), /unknown_method/);
  assert.equal(S.facetIds(param("ctrl.yaw.fake"), "channel")[0], S.UNCLASSIFIED);
  assert.equal(S.facetLabel("method", "toString"), "未识别标签：toString");
  assert.equal(S.facetLabel("domain", "__proto__"), "未识别标签：__proto__");
  assert.ok(S.metadataGaps(param("unknown.reason", { semantics: semantic({ review: { reason_codes: ["__proto__"] } }) }))
    .every((gap) => typeof gap === "string"));
});
test("稳定排序：域优先、族序优先、空排序末尾、键码点兜底", () => {
  const input = [param("z", { family: "f2" }), param("null.z"), param("null.a"),
    param("ranked", { semantics: semantic({ sort_order: 0 }) }), param("family", { family: "f1", semantics: semantic({ sort_order: 0 }) })];
  const original = JSON.stringify(input);
  const expected = "ranked,null.a,null.z,family,z";
  assert.equal(keys(S.sortedParams(input, layout)), expected);
  assert.equal(keys(S.sortedParams([...input].reverse(), layout)), expected);
  assert.equal(JSON.stringify(input), original);
  assert.ok(S.compareCodepoints("\uE000", "\u{10000}") < 0);
});
test("增益路径不能漏筛，重叠组和重复组键只出现一次", () => {
  const filtered = S.selectParams(rows, layout, { usage: "all", facets: { role: ["gain"] } });
  const grouped = S.groupedParams(filtered, [{ id: "one", keys: ["a", "a", "b"] }, { id: "two", keys: ["a", "c"] }]);
  const shown = [...grouped.groups.flatMap((g) => g.members), ...grouped.rest];
  assert.equal(keys(shown), "a,c");
  assert.equal(new Set(shown.map((p) => p.key)).size, shown.length);
});
test("同值改名和语义单独更新使用最新对象，移除记录不留旧抽屉", () => {
  const old = param("refresh", { semantics: semantic() });
  const fresh = { ...old, semantics: { ...old.semantics, display_name: "只改元数据的新名称" } };
  assert.equal(old.value, fresh.value);
  assert.equal(old.unit, fresh.unit);
  assert.equal(S.displayName(S.currentParam([fresh], old.key)), "只改元数据的新名称");
  assert.equal(S.currentParam([], old.key), null);
});
test("被筛掉的深链接目标解除筛选后可定位，不存在目标不扰动", () => {
  const f = { usage: "current", facets: { channel: ["yaw"] } };
  assert.equal(S.selectParams(rows, layout, f).some((p) => p.key === "b"), false);
  const revealed = S.revealTargetFilters(rows, "b", f);
  assert.equal(S.selectParams(rows, layout, revealed).some((p) => p.key === "b"), true);
  assert.equal(S.revealTargetFilters(rows, "missing", f), f);
  assert.equal(S.revealTargetFilters(rows, "a", f), f);
});
test("消费条件仅为文本，所有派生读取都不执行或修改", () => {
  const p = param("conditions", { semantics: semantic({ applicability: {
    sample_scope: "unknown", conditions: ["globalThis.__i232Executed = true"] } }) });
  const original = JSON.stringify(p);
  assert.equal(S.applicableConditions(p)[0], "globalThis.__i232Executed = true");
  S.metadataGaps(p); S.displayName(p); S.displayDefinition(p); S.facetIds(p, "channel");
  assert.equal(globalThis.__i232Executed, undefined);
  assert.equal(JSON.stringify(p), original);
});
test("消费关系与三槽复核筛选独立且与六轴AND组合", () => {
  const p = param("mixed.review", { semantics: semantic({ review_state: {
    name: "verified", definition: "disputed", usage: "pending" } }) });
  const f = { ...S.emptyFilters(), consumer: "conditional", review: {
    name: "verified", definition: "disputed", usage: "pending" }, facets: { channel: ["yaw"] } };
  assert.equal(S.matchesFilters(p, f), true);
  assert.equal(S.matchesFilters(p, { ...f, consumer: "connected" }), false);
  assert.equal(S.matchesFilters(p, { ...f, review: { ...f.review, definition: "verified" } }), false);
  assert.equal(S.matchesFilters(p, { ...f, facets: { channel: ["altitude"] } }), false);
  assert.equal(S.hasFilters(f), true);
  assert.equal(S.hasFilters(S.emptyFilters()), false);
});
test("具体样本含通用与明确列出项，不把旧sample标记当适用证据", () => {
  const listed = param("listed", { semantics: semantic({ applicability: { sample_scope: "listed", sample_ids: ["700g"] } }) });
  const common = param("common", { semantics: semantic({ applicability: { sample_scope: "common" } }) });
  const unknown = param("legacy.215", { sample: "700g" });
  const f = { ...S.emptyFilters(), sample_id: "700g" };
  assert.equal(S.matchesFilters(listed, f), true);
  assert.equal(S.matchesFilters(common, f), true);
  assert.equal(S.matchesFilters(unknown, f), false);
  assert.equal(S.matchesFilters(listed, { ...f, sample_scope: "common" }), false);
  assert.equal(S.matchesFilters(unknown, { ...S.emptyFilters(), sample_scope: "unknown" }), true);
});
test("默认路径关闭仍可筛到现行用途，默认与样本条件均参与AND", () => {
  const p = param("disabled.current", { semantics: semantic({ applicability: {
    sample_scope: "listed", sample_ids: ["700g"], default_path: "disabled" } }) });
  const f = { ...S.emptyFilters(), usage: "current", default_path: "disabled", sample_id: "700g" };
  assert.equal(S.matchesFilters(p, f), true);
  assert.equal(S.matchesFilters(p, { ...f, default_path: "active" }), false);
  assert.equal(S.matchesFilters(p, { ...f, sample_id: "215g" }), false);
  assert.equal(S.defaultPathOf(param("unknown")), "unknown");
});
test("坏格式v1关系只读保留，安全显示诊断而不对字符串调用map", () => {
  const p = param("malformed.relations", { semantics: semantic({ alias_of: { bad: true },
    superseded_by: "must_not_map", review: { next_owner: { bad: true } },
    applicability: { conditions: ["有效条件", null, 9], modes: "must_not_join" } }),
    semantics_diagnostics: [{ code: "invalid_relation", path: "superseded_by" }] });
  const original = JSON.stringify(p);
  assert.equal(S.aliasTarget(p), "");
  assert.equal(S.supersededKeys(p).length, 0);
  assert.equal(S.nextOwnerLabel(p), "尚未登记");
  assert.equal(S.applicableConditions(p).join(","), "有效条件");
  assert.equal(S.applicableModes(p).length, 0);
  assert.match(S.semanticDiagnostics(p).join("；"), /invalid_relation/);
  assert.match(S.metadataGaps(p).join("；"), /关系.*格式|后继关系/);
  assert.equal(JSON.stringify(p), original);
  const mixed = param("mixed.relations", { semantics: semantic({ superseded_by: ["a", null, 4, "a", "b"] }) });
  assert.equal(S.supersededKeys(mixed).join(","), "a,b");
});
for (const [label, patch] of [
  ["非法预留缺设计依据与负责人", { usage_tag: "reserved", source_refs: [], review: {} }],
  ["非法现行消费者引用为空对象", { usage_tag: "current", consumer_refs: [{}] }],
]) {
  test(label + "：invalid_semantics禁止用途和三槽已核断言", () => {
    const p = param("invalid.contract", { semantics: semantic(patch),
      semantics_diagnostics: [{ code: "invalid_semantics", message: label }] });
    const original = JSON.stringify(p);
    assert.equal(S.hasInvalidSemantics(p), true);
    assert.equal(S.semanticsOf(p), null);
    assert.equal(S.usageOf(p), "undetermined");
    for (const slot of S.REVIEW_SLOTS) assert.equal(S.reviewOf(p, slot), "unreviewed");
    assert.match(S.displayName(p), /名称待核实.*invalid\.contract/);
    assert.match(S.displayDefinition(p), /定义待核实.*invalid\.contract/);
    assert.match(S.metadataGaps(p).join("；"), /校验未通过/);
    assert.match(S.semanticDiagnostics(p).join("；"), /invalid_semantics/);
    assert.equal(JSON.stringify(p), original);
  });
}
test("普通旧冲突和关系校验提示不整体降级已核槽", () => {
  const p = param("valid.with.warning", { semantics: semantic(), semantics_diagnostics: [
    { code: "legacy_semantics_conflict" }, "relations_require_registry_validation" ] });
  assert.equal(S.hasInvalidSemantics(p), false);
  assert.equal(S.usageOf(p), "current");
  assert.equal(S.reviewOf(p, "name"), "verified");
  assert.equal(S.displayName(p), "已核对象的量");
  assert.equal(S.displayDefinition(p), "经逐项核对的完整定义");
  assert.ok(S.semanticDiagnostics(p).length > 0);
});

const args = process.argv.slice(2);
const paths = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] !== "--payload" || !args[i + 1]) throw new Error("仅支持 --payload <真实载荷路径>，可重复。");
  paths.push(path.resolve(args[++i]));
}
if (!paths.length) paths.push(path.join(OUT, "real_old_lab_payload.json"));
for (const input of paths) {
  test("真实载荷只读回归：" + path.basename(input), () => {
    const bytes = fs.readFileSync(input);
    const raw = JSON.parse(bytes.toString("utf8").replace(/^\uFEFF/, ""));
    const payload = raw.result ?? raw;
    assert.equal(payload.schema, "lab-payload-v2");
    assert.ok(Array.isArray(payload.params) && payload.params.length > 0);
    const before = JSON.stringify(payload);
    const selected = S.selectParams(payload.params, payload, S.emptyFilters());
    assert.equal(selected.length, new Set(payload.params.map((p) => p.key)).size);
    for (const p of selected) {
      assert.ok(S.displayName(p)); assert.ok(S.displayDefinition(p));
      S.sampleLabel(p); S.metadataGaps(p);
      for (const axis of S.AXES) S.facetIds(p, axis);
      if (S.reviewOf(p, "name") !== "verified") assert.notEqual(S.displayName(p), p.short_name);
    }
    assert.equal(JSON.stringify(payload), before);
    assert.equal(fs.readFileSync(input).equals(bytes), true);
    realPayloads.push({ path: path.relative(ROOT, input), sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
      count: selected.length, metadata_schema: payload.metadata_schema ?? null });
  });
}
const failures = results.filter((r) => !r.pass);
const report = { generated_at: new Date().toISOString(), pass: failures.length === 0,
  scope: "纯函数反例和真实载荷只读回归，不替代I-211/Ciri页面独立验收", passed: results.length - failures.length,
  failed: failures.length, source_sha256: crypto.createHash("sha256").update(source).digest("hex"),
  real_payloads: realPayloads, cases: results };
fs.mkdirSync(OUT, { recursive: true });
fs.writeFileSync(path.join(OUT, "frontend_semantics_checks.json"), JSON.stringify(report, null, 2) + "\n");
console.log(JSON.stringify({ pass: report.pass, passed: report.passed, failed: report.failed,
  real_payloads: realPayloads, failures }, null, 2));
process.exitCode = failures.length ? 1 : 0;
