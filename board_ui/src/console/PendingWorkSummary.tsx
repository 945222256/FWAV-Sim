/* console/PendingWorkSummary.tsx — I-234 全局常驻摘要。
 * 待领总量与执行容量同一阅读区：总量、四类计数、未分配入口、容量
 * （effective_limit/occupied/dispatchable；reserved=null 明示未设置）。
 * 数量可展开完整明细（按正式事项号，同快照来源）；采样时刻、有效期
 * （过期按待核对）、完整性与失联如实标注，接口失败不显示成零。
 * 容量只取快照 capacity 字段；页面额度与近五分钟活跃会话数为独立
 * 口径，只引用不混算（separate_measures_note）。 */
import { useMemo, useState } from "react";
import type { PwSnapshot, PwItem } from "./pending_work_types";
import { PW_CATEGORY_LABEL } from "./pending_work_types";
import PendingTaskBlock from "./PendingTaskBlock";
import type { PwStageMark } from "./pending_work_hooks";

const CATS = ["ready", "waiting_conditions", "awaiting_orchestration",
              "needs_verification"] as const;

export default function PendingWorkSummary({ pw, stagesByItem }: {
  pw: { snapshot: PwSnapshot | null; snapshotError: string | null;
        snapshotStale: boolean; eventsError: string | null };
  stagesByItem: Map<string, PwStageMark>;
}) {
  const [openAll, setOpenAll] = useState(false);
  const [filter, setFilter] = useState<string>("");
  const snap = pw.snapshot;
  const expired = pw.snapshotStale;
  const s = snap?.summary;

  const itemsFiltered = useMemo(() => {
    const items = snap?.items ?? [];
    return filter ? items.filter((it: PwItem) => it.category === filter) : items;
  }, [snap, filter]);

  if (pw.snapshotError && !snap) {
    return <div className="pw-summary pw-error" role="alert" data-testid="pw-summary">
      待领快照不可用：{pw.snapshotError}（不显示为零，保留最近一次可用数据前的空态说明）
    </div>;
  }
  if (!snap || !s) {
    return <div className="pw-summary" data-testid="pw-summary">待领快照读取中…</div>;
  }
  const cap = snap.capacity ?? {};
  const g = cap.global ?? {};
  const degraded = cap.status && cap.status !== "ok";
  return (
    <section className="pw-summary" data-testid="pw-summary"
             data-total={s.total_pending} aria-label="待领工作区全局摘要">
      <div className="pw-sum-row">
        <span className="pw-total" data-testid="pw-total">
          待领 <b>{s.total_pending}</b></span>
        {CATS.map((c) => (
          <button key={c}
                  className={"pw-cat-btn" + (filter === c ? " on" : "")}
                  data-cat={c} data-count={s.by_category?.[c] ?? 0}
                  onClick={() => setFilter(filter === c ? "" : c)}>
            {PW_CATEGORY_LABEL[c]} {s.by_category?.[c] ?? 0}
          </button>))}
        <button className={"pw-cat-btn pw-unassigned" + (filter === "__unassigned" ? " on" : "")}
                data-testid="pw-unassigned-entry"
                onClick={() => setFilter(filter === "__unassigned" ? "" : "__unassigned")}>
          未分配 {s.unassigned?.count ?? 0}
        </button>
        <span className="pw-cap" data-testid="pw-capacity"
              title={(g.policy_source ?? "") + (cap.separate_measures_note ? "；" + cap.separate_measures_note : "")}>
          执行容量 上限 {g.effective_limit ?? "—"} ｜ 已占用 {g.occupied ?? "—"}
          ｜ 可派 {g.dispatchable ?? "—"}
          ｜ 预留 {g.reserved == null
            ? "未设置（现行体系无预留策略）" : g.reserved}
        </span>
        <button className="pw-expand-all" aria-expanded={openAll}
                onClick={() => setOpenAll((v) => !v)}>
          {openAll ? "收起明细" : "展开明细（" + itemsFiltered.length + "）"}
        </button>
      </div>
      <div className="pw-meta">
        <span>采样 {snap.sampled_at?.replace("T", " ").slice(0, 19)}</span>
        {expired && <span className="pw-warn">采样已过期，按待核对处理（不当作当前事实）</span>}
        {snap.completeness?.status && snap.completeness.status !== "complete" && (
          <span className="pw-warn">数据不完整：{
            (snap.completeness.reasons ?? []).map((x) => x.reason ?? x.source).join("；")
            || "来源降级"}</span>)}
        {degraded && <span className="pw-warn">容量状态降级：{cap.config_read_error ?? "配置不可读"}</span>}
        {pw.snapshotError && <span className="pw-warn">快照刷新失败：{pw.snapshotError}（沿用上次数据）</span>}
        {pw.eventsError && <span className="pw-warn">{pw.eventsError}</span>}
        <span className="pw-meta-note">{cap.separate_measures_note ?? ""}</span>
        <span className="pw-snap-id">快照 {snap.snapshot_id}</span>
      </div>
      {openAll && (
        <div className="pw-all-list" data-testid="pw-all-list">
          {filter && (
            <p className="pw-filter-note">当前筛选：{filter === "__unassigned"
              ? "未分配路线" : PW_CATEGORY_LABEL[filter] ?? filter}
              ｜ {itemsFiltered.length} 项（同快照明细按正式事项号去重）</p>)}
          {itemsFiltered.length === 0
            ? <p className="pw-none">当前筛选无匹配待领事项（不编造）。</p>
            : itemsFiltered.map((it) => (
                <PendingTaskBlock key={it.item_id} item={it}
                                  stage={stagesByItem.get(it.item_id) ?? null} />))}
          {(snap.needs_verification ?? []).length > 0 && !filter && (
            <p className="pw-verify-list">
              另有待核对 {(snap.needs_verification ?? []).length} 项（历史类别缺失、
              认领与运行矛盾等），与确定待领分开核账：
              {(snap.needs_verification ?? []).slice(0, 5).map((v) => v.item_id).join("、")}
              {(snap.needs_verification ?? []).length > 5 ? " 等" : ""}</p>)}
        </div>)}
    </section>
  );
}
