/* DepGraph.tsx — 第二层：依赖关系图（修订一第三条）。
 * 依赖（depends_on，阻断）实线；启发（enables，不阻断）虚线；图例说明。
 * 节点填充色=状态，边框色组=路线归属，关键路径红色高亮。 */
import { useMemo, useState } from "react";
import type { MapEdge, MapNode } from "./types";

const COLS = ["L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"];
const NODE_W = 150, NODE_H = 46, GAP_X = 60, GAP_Y = 14, PAD = 30;

const STATUS_FILL: Record<string, string> = {
  open: "#1c2330", in_progress: "#3d2e10", blocked: "#3a1519", closed: "#12291a",
};

export default function DepGraph({ nodes, edges, criticalPath, laneColors, onOpen }: {
  nodes: MapNode[]; edges: MapEdge[]; criticalPath: string[];
  laneColors: Record<string, string>; onOpen: (id: string) => void;
}) {
  const [onlyOpen, setOnlyOpen] = useState(false);   // 修订一第二条：默认全量显示，已完成不隐藏。
  const [hover, setHover] = useState<string | null>(null);

  const view = useMemo(() => {
    const shown = onlyOpen ? nodes.filter((n) => n.status !== "closed") : nodes;
    const cols = COLS.map((lid) => shown.filter((n) => n.line === lid));
    const pos = new Map<string, { x: number; y: number }>();
    const maxRows = Math.max(1, ...cols.map((c) => c.length));
    cols.forEach((col, ci) => {
      col.forEach((n, ri) => {
        pos.set(n.id, { x: PAD + ci * (NODE_W + GAP_X), y: PAD + ri * (NODE_H + GAP_Y) });
      });
    });
    const width = PAD * 2 + COLS.length * NODE_W + (COLS.length - 1) * GAP_X;
    const height = PAD * 2 + maxRows * NODE_H + (maxRows - 1) * GAP_Y;
    return { shown, pos, width, height };
  }, [nodes, onlyOpen]);

  const cpSet = useMemo(() => new Set(criticalPath), [criticalPath]);
  const cpEdges = useMemo(() => {
    const out = new Set<string>();
    for (let i = 0; i < criticalPath.length - 1; i++) {
      out.add(criticalPath[i] + ">" + criticalPath[i + 1]);
    }
    return out;
  }, [criticalPath]);

  return (
    <section className="graph-panel">
      <div className="graph-ops">
        <label>
          <input type="checkbox" checked={onlyOpen}
                 onChange={(e) => setOnlyOpen(e.target.checked)} />
          只看未完成
        </label>
        <span className="legend">
          <span className="lg-line"><i className="lg-dep" />依赖（阻断，实线）</span>
          <span className="lg-line"><i className="lg-en" />启发（不阻断，虚线）</span>
          <span className="lg-line cp-tag">红色=关键路径</span>
        </span>
        <span className="legend">
          {["L1", "L2", "L3", "L4", "HQ"].map((l) => (
            <span key={l}><i style={{ background: laneColors[l] }} />{l} 路线</span>
          ))}
        </span>
        {criticalPath.length > 1 && (
          <span className="cp-chain">关键路径：{criticalPath.join(" → ")}</span>
        )}
      </div>
      <div className="graph-scroll">
        <svg width={view.width} height={view.height} role="img" aria-label="事项关系图">
          {edges.filter((e) => view.pos.has(e.from) && view.pos.has(e.to)).map((e, i) => {
            const a = view.pos.get(e.from)!, b = view.pos.get(e.to)!;
            const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2;
            const x2 = b.x, y2 = b.y + NODE_H / 2;
            const mx = (x1 + x2) / 2;
            const isCp = e.kind === "dep" && cpEdges.has(e.from + ">" + e.to);
            const isEn = e.kind === "enables";
            return (
              <path key={i}
                    d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`}
                    stroke={isCp ? "#f85149" : isEn ? "#8b949e" : e.open ? "#415070" : "#2d3644"}
                    strokeWidth={isCp ? 2.5 : isEn ? 1.4 : 1.2}
                    strokeDasharray={isEn ? "5 4" : undefined}
                    fill="none" opacity={e.open || isEn ? 0.9 : 0.35}
                    markerEnd="url(#arrow)" />
            );
          })}
          <defs>
            <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
              <path d="M0,0 L7,3 L0,6 Z" fill="#415070" />
            </marker>
          </defs>
          {view.shown.map((n) => {
            const p = view.pos.get(n.id)!;
            return (
              <g key={n.id} transform={`translate(${p.x},${p.y})`}
                 className={"node " + (cpSet.has(n.id) ? "cp " : "") + (hover === n.id ? "hover" : "")}
                 onClick={() => onOpen(n.id)}
                 onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}>
                <rect width={NODE_W} height={NODE_H} rx="7"
                      fill={STATUS_FILL[n.status] || "#1c2330"}
                      stroke={laneColors[n.lane] || "#2d3644"} strokeWidth="1.6" />
                {cpSet.has(n.id) && <rect width={NODE_W} height={NODE_H} rx="7"
                      fill="none" stroke="#f85149" strokeWidth="2.5" />}
                <text x="8" y="17" className="nid">{n.id}</text>
                <text x="52" y="17" className={"ndot " + n.status}>●</text>
                <text x="64" y="17" className="nstat">{n.status_word}</text>
                <text x="8" y="35" className="ntitle">
                  {n.title.length > 17 ? n.title.slice(0, 17) + "…" : n.title}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </section>
  );
}
