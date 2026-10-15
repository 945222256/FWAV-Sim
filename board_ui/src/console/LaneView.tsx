/* console/LaneView.tsx — 战情室页面（I-145）：十条任务线泳道状态视图。
 * 层级执行评审并入记录：租约剩最后一小时数字变深陶土强调色；
 * 阻塞与排队用文字标签不用状态灯。倒计时基准取接口响应的
 * 服务器 Date 头（判据：与服务器时钟偏差小于五秒）。 */
import { useEffect, useState } from "react";
import type { ConsoleState, LaneNode, SourcePtr } from "./types";
import { STATUS_WORD, waitingEntries } from "./types";
import SourceMark from "./SourceMark";

const WARN_S = 3600;                       // 最后一小时

function fmtLeft(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
  const two = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${two(h)}:${two(m)}:${two(ss)}` : `${two(m)}:${two(ss)}`;
}

function LaneCard({ lane, queue, serverBase, onOpenSrc }: {
  lane: LaneNode;
  queue?: NonNullable<ConsoleState["lane_queues"]>["queues"][string];
  serverBase: number;
  onOpenSrc: (s: SourcePtr) => void;
}) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const busy = lane.items.filter((i) => i.status === "in_progress").length;
  return (
    <section className="ln-lane">
      <header className="ln-lane-head">
        <span className="ln-lane-id">{lane.id}</span>
        <h3 className="ln-lane-name">{lane.name}</h3>
        <span className="ln-lane-meta">在办 {lane.items.length}</span>
        <span className="ln-lane-meta">会话 ● {lane.active_sessions.length}</span>
        <span className="ln-lane-meta">{queue ? "排队 " + queue.waiting_count : "排队未采集"}</span>
        <span className="spacer" />
        <SourceMark source={lane.source} onOpen={onOpenSrc} />
      </header>
      {queue && <div className="ln-queue">
        {queue.workers && queue.workers.length > 0
          ? <p>{queue.workers.map((w) =>
              (w.worker_id ?? "工位未登记") + "：" + (w.item_id ?? "空闲")).join(" ｜ ")}
               {queue.concurrency_limit != null ? "（上限 " + queue.concurrency_limit + "）" : ""}
               {queue.waiting_count > 0 ? " ｜ 等待 " + queue.waiting_count + " 项" : ""}</p>
          : <p>编排当前：{queue.current_item_id ?? "空闲"} ｜ 等待 {queue.waiting_count} 项</p>}
        {/* I-218 v2：waiting 按冻结契约为对象（request_id → 原因），
         * 经 waitingEntries 折算；queued_items 优先（带事项号与标题），
         * 缺失时回落对象键值。数组形态仅旧预备接口兼容。 */}
        {waitingEntries(queue).map((it, n) => <p key={(it.request_id ?? it.item_id ?? "?") + n}
          data-request-id={it.request_id ?? undefined}
          title={it.request_id ? "排队请求号 " + it.request_id : undefined}>
          {n + 1}. {it.item_id ?? it.request_id ?? "未登记事项"}{it.title ? " " + it.title : ""}{it.reason ? " ｜ " + it.reason : ""}
        </p>)}
      </div>}
      {lane.items.length === 0 ? (
        <p className="ln-lane-empty">本线空闲，无挂账事项。</p>
      ) : (
        <ul className="ln-items">
          {lane.items.map((it) => {
            const left = it.lease_until
              ? new Date(it.lease_until).getTime() - serverBase
              : null;
            const warn = left != null && left > 0 && left <= WARN_S;
            return (
              <li key={it.id} className="ln-item">
                <span className="ln-item-id">{it.id}</span>
                <span className="ln-item-title">{it.title}</span>
                <span className={"ln-item-status s-" + it.status}>
                  {STATUS_WORD[it.status] ?? it.status}
                </span>
                {it.progress && (
                  <span className="ln-item-prog">
                    进度 {it.progress.verified}/{it.progress.total}
                    <span className="ln-item-bar">
                      <span className="ln-item-bar-fill" style={{
                        width: (it.progress.total
                          ? it.progress.verified / it.progress.total * 100 : 0) + "%",
                      }} />
                    </span>
                  </span>
                )}
                <span className={"ln-item-lease" + (warn ? " warn" : "")}>
                  {left != null ? (left > 0 ? "租约 " + fmtLeft(left) : "租约已过期") : "无租约"}
                </span>
                <SourceMark source={it.source} onOpen={onOpenSrc} />
              </li>
            );
          })}
        </ul>
      )}
      {busy > 0 && <p className="ln-lane-foot">本线进行中事项 {busy} 件。</p>}
    </section>
  );
}

export default function LaneView({ state, serverBase, onOpenSrc }: {
  state: ConsoleState;
  serverBase: number;
  onOpenSrc: (s: SourcePtr) => void;
}) {
  const withItems = state.lanes.filter((l) => l.items.length > 0);
  return (
    <div className="ln">
      <p className="ln-summary">
        任务线 {state.lanes.length} 条 ｜ 有在办 {withItems.length} 条 ｜
        在办事项共 {state.lanes.reduce((n, l) => n + l.items.length, 0)} 件 ｜
        活跃会话 {state.sessions.active.length}
      </p>
      {state.lanes.map((l) => <LaneCard key={l.id} lane={l} queue={state.lane_queues?.queues[l.id]} serverBase={serverBase}
                                        onOpenSrc={onOpenSrc} />)}
    </div>
  );
}
