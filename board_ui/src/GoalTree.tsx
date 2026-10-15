/* GoalTree.tsx — 主菜单：目标树。节点点击进入逻辑线下钻视图。
 * 完成度=已完成事项数除以事项总数（修订一第四条）；线无挂账时写明原因，
 * 全组件禁止出现"未定义"字样。 */
import type { Goal, LineStat } from "./types";

function ring(pct: number): string {
  const a = Math.round(pct * 360);
  return `conic-gradient(#39c5cf ${a}deg, #21262d ${a}deg)`;
}

export default function GoalTree({ goals, lines, onOpenLine, activeLine }: {
  goals: Goal[]; lines: LineStat[];
  onOpenLine: (id: string) => void; activeLine: string | null;
}) {
  const [g1, g2] = goals;
  return (
    <section className="goaltree">
      <div className="goal g1">
        <span className="gid">G-001</span>
        <span className="gtext">{g1 ? g1.text : "总目标未登记"}</span>
      </div>
      {g2 && (
        <div className="goal g2">
          <span className="gid">G-002</span>
          <span className="gtext">{g2.text}</span>
        </div>
      )}
      <div className="layers">
        {lines.map((l) => (
          <button key={l.id}
                  className={"layer" + (activeLine === l.id ? " active" : "")}
                  onClick={() => onOpenLine(l.id)}
                  title={l.mission}>
            <div className="ring" style={{ background: l.progress != null ? ring(l.progress) : "#21262d" }}>
              <div className="ring-in">
                {l.progress != null ? Math.round(l.progress * 100) + "%" : "0 项"}
              </div>
            </div>
            <div className="lbody">
              <div className="lname">{l.name}</div>
              <div className="lmission">{l.mission}</div>
              <div className="lmeta">
                {l.total > 0 ? `${l.closed}/${l.total} 项完成` : (l.reason || "")}
              </div>
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}
