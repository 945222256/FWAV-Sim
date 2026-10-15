/* LaneBoard.tsx — 第三层：泳道任务板（下钻视图，按路线分列）。 */
import { useMemo } from "react";
import type { MapNode } from "./types";

export default function LaneBoard({ nodes, laneColors, onOpen }: {
  nodes: MapNode[]; laneColors: Record<string, string>; onOpen: (id: string) => void;
}) {
  const lanes = useMemo(() => {
    const groups = new Map<string, MapNode[]>();
    for (const n of nodes) {
      if (n.status === "closed") continue;
      const key = n.lane;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(n);
    }
    const order = ["L1", "L2", "L3", "L4", "HQ", "未派发"];
    return order.filter((k) => groups.has(k)).map((k) => ({
      lane: k, color: laneColors[k] || "#8b949e", items: groups.get(k)!,
    }));
  }, [nodes, laneColors]);

  return (
    <section className="laneboard">
      {lanes.map(({ lane, color, items }) => (
        <div key={lane} className="lane" style={{ borderColor: color + "55" }}>
          <div className="lane-head" style={{ color }}>
            {lane} <span className="count">{items.length}</span>
          </div>
          {items.map((n) => (
            <button key={n.id} className={"lcard st-" + n.status} onClick={() => onOpen(n.id)}>
              <div className="r1">
                <b>{n.id}</b>
                <span className="st">{n.status_word}</span>
                {n.claimed_by && <span className="cl">认领 {n.claimed_by}</span>}
              </div>
              <div className="t">{n.title}</div>
              {n.criteria_total != null && (
                <div className="pbar">
                  <div style={{ width: (n.verified! / n.criteria_total) * 100 + "%" }} />
                </div>
              )}
            </button>
          ))}
          {items.length === 0 && <div className="empty">该路无在办事项</div>}
        </div>
      ))}
    </section>
  );
}
