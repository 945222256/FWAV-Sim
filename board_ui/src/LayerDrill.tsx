/* LayerDrill.tsx — 逻辑线下钻视图（修订一第一条）。
 * 每条事项显示三样：来由（rationale）、完成判据（close_criterion）、状态。
 * 已完成事项同样显示；逻辑线为主轴，路线为色标（修订一第二条）。 */
import { useMemo } from "react";
import type { LineStat, MapNode } from "./types";

const ORDER: Record<string, number> = { blocked: 0, in_progress: 1, open: 2, closed: 3 };

export default function LayerDrill({ line, nodes, laneColors, onOpen, onBack }: {
  line: LineStat; nodes: MapNode[];
  laneColors: Record<string, string>;
  onOpen: (id: string) => void; onBack: () => void;
}) {
  const members = useMemo(() => {
    const list = nodes.filter((n) => n.line === line.id);
    return list.sort((a, b) =>
      (ORDER[a.status] ?? 9) - (ORDER[b.status] ?? 9) || a.id.localeCompare(b.id));
  }, [nodes, line.id]);

  return (
    <section className="linedrill">
      <header className="ld-head">
        <button onClick={onBack}>← 返回地图</button>
        <div>
          <h2>{line.id} {line.name}
            {line.total > 0 && (
              <span className="ld-prog">{line.closed}/{line.total} 项完成</span>
            )}
          </h2>
          <p className="ld-mission">{line.mission}</p>
          {line.reason && <p className="ld-reason">{line.reason}</p>}
        </div>
      </header>
      <div className="ld-list">
        {members.map((n) => (
          <button key={n.id} className={"ld-card st-" + n.status}
                  style={{ borderLeftColor: laneColors[n.lane] || "#8b949e" }}
                  onClick={() => onOpen(n.id)}>
            <div className="ld-r1">
              <b>{n.id}</b>
              <span className="ld-title">{n.title}</span>
            </div>
            <div className="ld-why">
              <span className="lab">来由</span>
              {n.rationale || "该事项尚未登记来由（rationale 字段为空），补录由数据资产线负责"}
            </div>
            <div className="ld-r2">
              <span className={"ld-st " + n.status}>{n.status_word}</span>
              <span className="ld-lane" style={{ color: laneColors[n.lane] || "#8b949e" }}>
                路线 {n.lane}
              </span>
              {n.claimed_by && <span className="ld-cl">认领 {n.claimed_by}</span>}
            </div>
          </button>
        ))}
        {members.length === 0 && <div className="empty">该线暂无挂账事项</div>}
      </div>
    </section>
  );
}
