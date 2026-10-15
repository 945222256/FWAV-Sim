/* console/PendingTaskBlock.tsx — I-234 待领任务块与原因展开。
 * 任务块较薄（事项号、标题、类别、一句主要原因）；点击或键盘（按钮
 * 原生 Enter/Space）展开：等待条件、关联依赖及状态、恢复条件、评估
 * 时刻/评估器/出处、入队与重试明细、调度依据。已核实的接续阶段以
 * 文字及克制辅助色标注（不依赖颜色单独表达）。 */
import { useState } from "react";
import type { PwItem } from "./pending_work_types";
import { PW_CATEGORY_LABEL } from "./pending_work_types";
import type { PwStageMark } from "./pending_work_hooks";

function fmt(iso?: string | null): string {
  return iso ? iso.replace("T", " ").slice(0, 19) : "—";
}

export default function PendingTaskBlock({ item, stage }: {
  item: PwItem;
  stage?: PwStageMark | null;
}) {
  const [open, setOpen] = useState(false);
  const r = item.primary_reason;
  /* 关联依赖合并两处来源：明细 related.dependencies 与原因内 related。 */
  const deps = [...(item.related?.dependencies ?? []), ...(r?.related ?? [])];
  return (
    <div className={"pw-task pw-cat-" + item.category}
         data-item-id={item.item_id} data-category={item.category}>
      <button className="pw-task-line" aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              aria-label={"待领事项 " + item.item_id + "，展开原因"}>
        <span className="pw-task-id">{item.item_id}</span>
        <span className="pw-task-title">{item.title ?? "（无标题登记）"}</span>
        <span className="pw-cat-chip">{PW_CATEGORY_LABEL[item.category] ?? item.category}</span>
        <span className="pw-task-reason">{r?.sentence ?? "原因未提供（待核对）"}</span>
        {stage && <span className="pw-stage-chip"
                        title={"已核实事件：" + stage.type + " @ " + stage.at}>
          接续中：{{ slot_released: "已释放", dispatch_selected: "已选择",
                    claim_confirmed: "已认领", run_started: "实际运行",
                    recovery: "已恢复" }[stage.type] ?? stage.type}
        </span>}
        <span className="pw-toggle-mark" aria-hidden="true">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="pw-detail" data-testid="pw-detail">
          <div className="pw-detail-grid">
            <div><dt>状态 / 类别</dt><dd>{item.status ?? "—"} / {
              PW_CATEGORY_LABEL[item.category] ?? item.category}</dd></div>
            <div><dt>归属 / 审核人</dt><dd>{item.owner ?? "—"} / {item.reviewer ?? "—"}</dd></div>
            <div><dt>主要原因代码</dt><dd>{r?.code ?? "未提供"}</dd></div>
            <div><dt>原因出处</dt><dd>{r?.provenance ?? item.evaluation?.provenance ?? "—"}</dd></div>
            <div><dt>恢复条件</dt><dd>{r?.recovery ?? item.recovery ?? "未提供"}</dd></div>
            <div><dt>最后评估</dt><dd>{fmt(item.evaluation?.at)}
              （{item.evaluation?.evaluator ?? "未注明评估器"}
              {item.evaluation?.validity ? "，" + item.evaluation.validity : ""}）</dd></div>
            <div><dt>入队情况</dt><dd>{
              item.enqueue?.in_kernel_queue
                ? "已入内核队列（请求 " + (item.enqueue.request_ids ?? []).join("、")
                  + "，累计派发尝试 " + (item.enqueue.total_dispatch_attempts ?? 0) + " 次；重试只计一项）"
                : "未入内核队列（静态同源评估，未经内核实际准入）"}</dd></div>
            <div><dt>调度依据</dt><dd>{
              item.execution_ref?.session_id
                ? "已关联执行：" + item.execution_ref.worker_id + " / "
                  + item.execution_ref.session_id + "（相位 " + (item.execution_ref.kernel_phase ?? "—") + "）"
                : item.enqueue?.in_kernel_queue
                  ? "已入队，等待调度器结合容量与冲突实际安排（就绪不承诺立即开工）"
                  : "尚未入队，无调度依据"}</dd></div>
          </div>
          {deps.length > 0 && (
            <div className="pw-deps">
              <dt>关联依赖</dt>
              <ul>
                {deps.map((d) => (
                  <li key={d.item_id ?? Math.random()}>
                    <span className="pw-dep-id">{d.item_id}</span>
                    <span className={"pw-dep-status s-" + (d.status ?? "")}>{d.status ?? "—"}</span>
                    <span className="pw-dep-title">{d.title ?? ""}</span>
                  </li>))}
              </ul>
            </div>)}
          {item.category === "needs_verification" && (
            <p className="pw-verify-note">该项为待核对：原因与范围以核对清单为准，
              不计入确定待领的调度结论；详情见快照 needs_verification 分列。</p>)}
        </div>
      )}
    </div>
  );
}
