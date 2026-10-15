/* I-232：只读语义展示、分面和排序。词表只翻译稳定ID，不推断逐键分类。 */
import type {
  LabFacetAxis, LabParam, LabPayload, LabReviewSlot, LabReviewState, LabSemantics, LabUsageTag,
} from "./types";

export const AXES: readonly LabFacetAxis[] = ["domain", "component", "channel", "role", "method", "stage"];
export const AXIS_LABELS: Record<LabFacetAxis, string> = {
  domain: "意义域", component: "部件", channel: "通道", role: "参数作用", method: "方法", stage: "阶段",
};
export const USAGE_LABELS: Record<LabUsageTag, string> = {
  current: "现行用途", historical: "历史用途", reserved: "设计预留", undetermined: "用途未判定",
};
export const REVIEW_LABELS: Record<LabReviewState, string> = {
  unreviewed: "未复核", verified: "已复核", declaration_only: "仅声明", pending: "待补证",
  disputed: "有争议", stale: "证据已过期",
};
export const SLOT_LABELS: Record<LabReviewSlot, string> = { name: "名称", definition: "定义", usage: "用途" };
export const REVIEW_SLOTS: readonly LabReviewSlot[] = ["name", "definition", "usage"];
export const UNCLASSIFIED = "__unclassified__";

const VOCABULARY: Record<LabFacetAxis, Record<string, string>> = {
  domain: { vehicle_physics: "整机物理", aerodynamics: "空气动力", control: "控制", rhythm: "扑动节律",
    identification_search: "辨识与搜索", simulation_runtime: "仿真运行" },
  component: { whole_vehicle: "整机", fuselage: "机身", left_wing: "左翼", right_wing: "右翼",
    wing_connector: "翼连接件", tail: "尾翼", propulsion_slipstream: "推进与滑流", sensing_estimation: "感知与估计" },
  channel: { longitudinal: "纵向", lateral: "横侧向", yaw: "偏航", altitude: "高度", speed: "速度",
    rhythm: "节律", not_applicable: "不适用" },
  role: { physical_property: "物理属性", geometry: "几何", state_reference: "状态参考", gain: "增益",
    state_limit: "状态限幅", output_limit: "输出限幅", mixing: "混合分配", model_coefficient: "模型系数",
    search_range: "搜索范围", step_size: "步长", termination: "终止条件", runtime_setting: "运行设置" },
  method: {},
  stage: { position_loop: "位置环", attitude_loop: "姿态环", rate_loop: "角速率环",
    control_allocation: "控制分配", wing_actuation: "翼面执行", aerodynamics: "气动力计算",
    identification: "辨识", search: "搜索" },
};
const REASONS: Record<string, string> = {
  no_consumer_evidence: "尚缺实际消费者证据", definition_missing: "定义尚未补齐",
  source_conflict: "来源存在冲突", evidence_version_changed: "证据文件版本已变化",
  sample_scope_unknown: "适用样本尚未确认", dynamic_read_not_excluded: "尚未排除动态读取路径",
};
export const CONSUMER_LABELS: Record<string, string> = {
  connected: "已找到消费路径", conditional: "按条件消费", offline_only: "仅离线消费",
  not_found: "已查范围内未找到消费者", unknown: "消费者待确认",
};
export const DEFAULT_PATH_LABELS: Record<string, string> = {
  active: "默认路径启用", conditional: "默认路径有条件启用", disabled: "默认路径关闭",
  not_applicable: "默认路径不适用", unknown: "默认路径待确认",
};
export const SAMPLE_SCOPE_LABELS: Record<string, string> = { unknown: "适用范围待核实", common: "通用范围", listed: "已列具体样本" };

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? [...new Set(value.filter((v): v is string => typeof v === "string" && v.trim().length > 0))] : [];
}
function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}
function hasDiagnosticCode(p: LabParam, code: string): boolean {
  const value = p.semantics_diagnostics;
  const rows = Array.isArray(value) ? value : value == null ? [] : [value];
  return rows.some((row) => row === code
    || !!row && typeof row === "object" && (row as Record<string, unknown>).code === code);
}
/** 只按稳定机器码阻断非法契约；普通冲突/关系待校验提示不整体降级。 */
export function hasInvalidSemantics(p: LabParam): boolean {
  return hasDiagnosticCode(p, "invalid_semantics");
}
export function hasUnsupportedSchema(p: LabParam): boolean {
  return hasDiagnosticCode(p, "unsupported_schema_version");
}
export function semanticsOf(p: LabParam): LabSemantics | null {
  return p.semantics?.schema_version === 1 && !hasInvalidSemantics(p) && !hasUnsupportedSchema(p) ? p.semantics : null;
}
export function usageOf(p: LabParam): LabUsageTag {
  const value = semanticsOf(p)?.usage_tag;
  return value && Object.hasOwn(USAGE_LABELS, value) ? value as LabUsageTag : "undetermined";
}
export function reviewOf(p: LabParam, slot: LabReviewSlot): LabReviewState {
  const value = semanticsOf(p)?.review_state?.[slot];
  return value && Object.hasOwn(REVIEW_LABELS, value) ? value as LabReviewState : "unreviewed";
}
export function displayName(p: LabParam): string {
  const value = text(semanticsOf(p)?.display_name);
  return reviewOf(p, "name") === "verified" && value ? value
    : "名称待核实：" + p.key + "（" + (value ? REVIEW_LABELS[reviewOf(p, "name")] : "缺少已核名称") + "）";
}
export function displayDefinition(p: LabParam): string {
  const value = text(semanticsOf(p)?.definition);
  return reviewOf(p, "definition") === "verified" && value ? value
    : "定义待核实：" + p.key + "（" + (value ? REVIEW_LABELS[reviewOf(p, "definition")] : "缺少完整定义") + "），具体缺口见详情。";
}
export function facetIds(p: LabParam, axis: LabFacetAxis): string[] {
  const ids = strings(semanticsOf(p)?.facets?.[axis]);
  return ids.length ? ids : [UNCLASSIFIED];
}
export function facetLabel(axis: LabFacetAxis, id: string): string {
  return id === UNCLASSIFIED ? "分类待核实"
    : Object.hasOwn(VOCABULARY[axis], id) ? VOCABULARY[axis][id] : "未识别标签：" + id;
}
export function consumerLabel(p: LabParam): string {
  return CONSUMER_LABELS[consumerOf(p)];
}
export function defaultPathLabel(p: LabParam): string {
  return DEFAULT_PATH_LABELS[defaultPathOf(p)];
}
export function consumerOf(p: LabParam): string {
  const state = semanticsOf(p)?.consumer_state;
  return state && Object.hasOwn(CONSUMER_LABELS, state) ? state : "unknown";
}
export function defaultPathOf(p: LabParam): string {
  const state = semanticsOf(p)?.applicability?.default_path;
  return state && Object.hasOwn(DEFAULT_PATH_LABELS, state) ? state : "unknown";
}
export function sampleIds(p: LabParam): string[] {
  return strings(semanticsOf(p)?.applicability?.sample_ids);
}
export function sampleScopeOf(p: LabParam): string {
  const scope = semanticsOf(p)?.applicability?.sample_scope;
  return scope === "common" ? "common" : scope === "listed" && sampleIds(p).length > 0 ? "listed" : "unknown";
}
export function sampleLabel(p: LabParam): string {
  const scope = semanticsOf(p)?.applicability;
  if (scope?.sample_scope === "common") return "已登记为通用样本范围";
  if (scope?.sample_scope === "listed") {
    const ids = strings(scope.sample_ids);
    if (ids.length) return "已列适用样本：" + ids.join("、");
  }
  return "适用样本待核实";
}
export function applicableModes(p: LabParam): string[] { return strings(semanticsOf(p)?.applicability?.modes); }
export function applicableConditions(p: LabParam): string[] { return strings(semanticsOf(p)?.applicability?.conditions); }
export function aliasTarget(p: LabParam): string { return text(semanticsOf(p)?.alias_of); }
export function supersededKeys(p: LabParam): string[] { return strings(semanticsOf(p)?.superseded_by); }
export function nextOwnerLabel(p: LabParam): string { return text(semanticsOf(p)?.review?.next_owner) || "尚未登记"; }
export function semanticDiagnostics(p: LabParam): string[] {
  const value = p.semantics_diagnostics;
  const rows = Array.isArray(value) ? value : value == null ? [] : [value];
  return rows.map((row) => typeof row === "string" ? row : JSON.stringify(row)).filter((row): row is string => !!row);
}
export function metadataGaps(p: LabParam): string[] {
  const s = semanticsOf(p);
  if (!s) return [hasInvalidSemantics(p) ? "语义契约校验未通过，原始记录保留待复核"
    : p.semantics ? "语义版本暂不支持，保留原始记录待复核" : "尚未提供规范语义元数据，名称、定义与用途均未复核",
    ...semanticDiagnostics(p)];
  const gaps: string[] = [];
  if (!text(s.display_name)) gaps.push("缺少对象明确的名称");
  if (!text(s.definition)) gaps.push("缺少完整定义");
  for (const slot of REVIEW_SLOTS) {
    const state = reviewOf(p, slot);
    if (state !== "verified") gaps.push(SLOT_LABELS[slot] + "：" + REVIEW_LABELS[state]);
  }
  if (usageOf(p) === "undetermined") gaps.push("用途尚未判定");
  if (s.usage_tag && !Object.hasOwn(USAGE_LABELS, s.usage_tag)) gaps.push("未识别用途标签：" + s.usage_tag);
  if (s.consumer_state === "not_found") gaps.push("只在已查范围未找到消费者，不能据此判为永久无用或设计预留");
  if (s.consumer_state === "not_found" && !s.review?.search_scope) gaps.push("未登记已查范围与版本依据");
  if (s.consumer_state === "conditional" && !strings(s.applicability?.conditions).length) gaps.push("缺少可定位的消费条件");
  if (s.usage_tag === "current" && (!Array.isArray(s.consumer_refs) || !s.consumer_refs.length)) gaps.push("现行用途尚缺规范消费者引用");
  if (s.alias_of != null && typeof s.alias_of !== "string") gaps.push("别名关系格式异常，原始记录保留待核");
  if (s.superseded_by != null && !Array.isArray(s.superseded_by)) gaps.push("后继关系必须是字符串数组，原始记录保留待核");
  else if (Array.isArray(s.superseded_by) && s.superseded_by.some((key) => typeof key !== "string")) gaps.push("后继关系包含非字符串成员，原始记录保留待核");
  if (sampleLabel(p) === "适用样本待核实") gaps.push("适用样本尚未确认");
  gaps.push(...strings(s.review?.reason_codes).map((reason) => Object.hasOwn(REASONS, reason) ? REASONS[reason] : "待核原因：" + reason));
  gaps.push(...strings(s.review?.limitations));
  gaps.push(...semanticDiagnostics(p));
  return [...new Set(gaps)];
}

export interface LabFilters {
  usage: "all" | LabUsageTag;
  facets: Partial<Record<LabFacetAxis, string[]>>;
  consumer?: string;
  review?: Partial<Record<LabReviewSlot, string>>;
  sample_scope?: string;
  sample_id?: string;
  default_path?: string;
}
export function emptyFilters(): LabFilters {
  return { usage: "all", facets: {}, consumer: "all", review: {}, sample_scope: "all", sample_id: "all", default_path: "all" };
}
export function hasFilters(filters: LabFilters): boolean {
  return filters.usage !== "all" || AXES.some((axis) => (filters.facets[axis]?.length ?? 0) > 0)
    || [filters.consumer, filters.sample_scope, filters.sample_id, filters.default_path,
      ...REVIEW_SLOTS.map((slot) => filters.review?.[slot])].some((value) => !!value && value !== "all");
}
export function matchesFilters(p: LabParam, filters: LabFilters, exceptAxis?: LabFacetAxis): boolean {
  if (filters.usage !== "all" && usageOf(p) !== filters.usage) return false;
  if (filters.consumer && filters.consumer !== "all" && consumerOf(p) !== filters.consumer) return false;
  if (filters.default_path && filters.default_path !== "all" && defaultPathOf(p) !== filters.default_path) return false;
  if (filters.sample_scope && filters.sample_scope !== "all" && sampleScopeOf(p) !== filters.sample_scope) return false;
  if (filters.sample_id && filters.sample_id !== "all" && sampleScopeOf(p) !== "common"
    && !(sampleScopeOf(p) === "listed" && sampleIds(p).includes(filters.sample_id))) return false;
  if (!REVIEW_SLOTS.every((slot) => !filters.review?.[slot] || filters.review[slot] === "all"
    || reviewOf(p, slot) === filters.review[slot])) return false;
  return AXES.every((axis) => {
    const selected = filters.facets[axis] ?? [];
    return axis === exceptAxis || selected.length === 0 || selected.some((id) => facetIds(p, axis).includes(id));
  });
}
export function uniqueParams(params: readonly LabParam[]): LabParam[] {
  return [...new Map(params.map((p) => [p.key, p])).values()];
}
export function compareCodepoints(a: string, b: string): number {
  const aa = Array.from(a, (c) => c.codePointAt(0)!);
  const bb = Array.from(b, (c) => c.codePointAt(0)!);
  for (let i = 0; i < Math.min(aa.length, bb.length); i++) if (aa[i] !== bb[i]) return aa[i] - bb[i];
  return aa.length - bb.length;
}
export function sortedParams(params: readonly LabParam[], payload: Pick<LabPayload, "domains" | "families">): LabParam[] {
  const domainOrder = new Map(payload.domains.map((d) => [d.id, d.no]));
  const familyOrder = new Map(payload.families.map((f, i) => [f.id, i]));
  const familyDomain = new Map(payload.families.map((f) => [f.id, f.domain]));
  const order = (p: LabParam) => {
    const n = semanticsOf(p)?.sort_order;
    return typeof n === "number" && Number.isFinite(n) ? n : Infinity;
  };
  const rank = (p: LabParam) => domainOrder.get(familyDomain.get(p.family) ?? "") ?? Infinity;
  const cmp = (a: number, b: number) => a === b ? 0 : a < b ? -1 : 1;
  return uniqueParams(params).sort((a, b) => cmp(rank(a), rank(b))
    || cmp(familyOrder.get(a.family) ?? Infinity, familyOrder.get(b.family) ?? Infinity)
    || cmp(order(a), order(b)) || compareCodepoints(a.key, b.key));
}
export function selectParams(params: readonly LabParam[], payload: Pick<LabPayload, "domains" | "families">, filters: LabFilters): LabParam[] {
  return sortedParams(params, payload).filter((p) => matchesFilters(p, filters));
}
export function facetCounts(params: readonly LabParam[], axis: LabFacetAxis, filters: LabFilters): Map<string, number> {
  const counts = new Map<string, number>();
  for (const p of uniqueParams(params)) {
    for (const id of facetIds(p, axis)) {
      if (!counts.has(id)) counts.set(id, 0);
      if (matchesFilters(p, filters, axis)) counts.set(id, counts.get(id)! + 1);
    }
  }
  return counts;
}
/** 增益组只从已筛选列表取键，重叠组也只陈列一次。 */
export function groupedParams(params: readonly LabParam[], groups: readonly { id: string; keys: string[] }[]) {
  const used = new Set<string>();
  const rows = groups.map((g) => {
    const keys = new Set(g.keys);
    const members = uniqueParams(params).filter((p) => keys.has(p.key) && !used.has(p.key));
    members.forEach((p) => used.add(p.key));
    return { id: g.id, members };
  });
  return { groups: rows, rest: uniqueParams(params).filter((p) => !used.has(p.key)) };
}
export function currentParam(params: readonly LabParam[], key: string | null): LabParam | null {
  return key === null ? null : params.find((p) => p.key === key) ?? null;
}
/** 点跳目标被筛掉时明确回到全量；不存在的目标不改变用户筛选。 */
export function revealTargetFilters(params: readonly LabParam[], key: string, filters: LabFilters): LabFilters {
  const target = currentParam(params, key);
  return target && !matchesFilters(target, filters) ? emptyFilters() : filters;
}
