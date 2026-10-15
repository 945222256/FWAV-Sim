/* App.tsx — 全景作战地图：三层一图（修订一形态）。
 * 第一层目标树（主菜单，可点击下钻），第二层关系图，第三层泳道下钻。 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { BoardMap, ItemDetail } from "./types";
import GoalTree from "./GoalTree";
import DepGraph from "./DepGraph";
import LaneBoard from "./LaneBoard";
import InboxPanel, { type InboxEntry } from "./InboxPanel";
import LayerDrill from "./LayerDrill";
import ItemDrawer from "./ItemDrawer";
import LabPage from "./lab/LabPage";
import ConsolePage from "./console/ConsolePage";
import { parseHash, syncHash } from "./deeplink";
import { LANE_COLORS } from "./theme";
import "./App.css";
import "./tokens.css";

export default function App() {
  const [map, setMap] = useState<BoardMap | null>(null);
  const [offline, setOffline] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ItemDetail | null>(null);
  const [tab, setTab] = useState<"graph" | "lanes" | "inbox">("graph");
  const [inbox, setInbox] = useState<InboxEntry[]>([]);
  const [activeLine, setActiveLine] = useState<string | null>(null);
  const [labOpen, setLabOpen] = useState(parseHash(location.hash).route === "lab");
  const [consoleOpen, setConsoleOpen] = useState(parseHash(location.hash).route === "console");
  const detailCache = useRef<Map<string, ItemDetail>>(new Map());

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/map");
      if (r.status === 401) { location.href = "/login"; return; }
      const d = await r.json();
      if (d.ok) { setMap(d.result); setOffline(false); }
      // I-122：待审队列来自既有只读接口 /api/state（后端零改动）。
      try {
        const rs = await fetch("/api/state");
        if (rs.status === 200) {
          const ds = await rs.json();
          if (Array.isArray(ds.inbox)) setInbox(ds.inbox);
        }
      } catch { /* 待审区拉取失败不拖垮地图 */ }
    } catch {
      setOffline(true);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10000);
    if ("EventSource" in window) {
      const es = new EventSource("/api/stream");
      es.onmessage = () => refresh();
      return () => { clearInterval(t); es.close(); };
    }
    return () => clearInterval(t);
  }, [refresh]);

  const openItem = useCallback(async (id: string) => {
    setSelected(id);
    syncHash({ route: "item", itemId: id });
    const hit = detailCache.current.get(id);
    if (hit) setDetail(hit);
    try {
      const r = await fetch("/api/item-detail?id=" + encodeURIComponent(id));
      const d = await r.json();
      if (d.ok) {
        const normalized = { item: d.result.item, events: d.result.events };
        setDetail(normalized);
        detailCache.current.set(id, normalized);
      }
    } catch { /* 保留缓存或空态 */ }
  }, []);

  const closeDrawer = useCallback(() => {
    setSelected(null); setDetail(null);
    syncHash({ route: "graph" });
  }, []);

  /* 深度链接摄入（契约 android_shell/deeplink_contract.json）：
   * 启动与 hashchange 时按契约路由落视图，未匹配按 home。
   * lab 是参数实验室独立页（I-130），console 是编排控制台（I-144 起）。 */
  useEffect(() => {
    const apply = () => {
      const link = parseHash(location.hash);
      if (link.route === "lab") { setLabOpen(true); setConsoleOpen(false); return; }
      if (link.route === "console") { setConsoleOpen(true); setLabOpen(false); return; }
      setLabOpen(false);
      setConsoleOpen(false);
      if (!map) return;
      if (link.route === "item") { setTab("graph"); openItem(link.itemId); }
      else if (link.route === "lanes") { setTab("lanes"); }
      else if (link.route === "graph") { setTab("graph"); }
      else if (link.route === "line") { setActiveLine(link.lineId); }
    };
    apply();
    window.addEventListener("hashchange", apply);
    return () => window.removeEventListener("hashchange", apply);
  }, [map, openItem]);

  const exitLab = useCallback(() => {
    setLabOpen(false);
    syncHash({ route: "graph" });
  }, []);

  const exitConsole = useCallback(() => {
    setConsoleOpen(false);
    syncHash({ route: "graph" });
  }, []);

  if (labOpen) {
    return <LabPage onBack={exitLab} />;
  }
  if (consoleOpen) {
    return <ConsolePage onBack={exitConsole} />;
  }

  if (!map) {
    return <div className="boot">{offline ? "云端不可达，正在重试…" : "作战地图加载中…"}</div>;
  }

  const line = activeLine ? map.lines.find((l) => l.id === activeLine) : null;

  return (
    <div className="board">
      <header className="top">
        <div className="title">LP 作战地图<small>目标 · 关系 · 泳道</small></div>
        <span className="badge">事项 <b>{map.counts.total}</b></span>
        <span className="badge b-open">在办 <b>{map.counts.open}</b></span>
        <span className="badge b-prog">进行中 <b>{map.counts.in_progress}</b></span>
        <span className="badge b-block">卡住 <b>{map.counts.blocked}</b></span>
        <span className="badge b-done">完成 <b>{map.counts.closed}</b></span>
        <span className="badge b-owner">待机主 <b>{map.counts.owner_pending}</b></span>
        <span className="spacer" />
        <span className="muted">{offline ? "离线缓存" : "更新于 " + map.generated_at}</span>
        <button onClick={() => { setConsoleOpen(true); syncHash({ route: "console" }); }}>编排控制台</button>
        <button onClick={() => { setLabOpen(true); syncHash({ route: "lab" }); }}>参数实验室</button>
        <button onClick={() => (location.href = "/")}>旧版看板</button>
      </header>

      <GoalTree goals={map.goals} lines={map.lines}
                onOpenLine={(id) => {
                  setActiveLine((cur) => (cur === id ? null : id));
                  syncHash(id ? { route: "line", lineId: id } : { route: "graph" });
                }}
                activeLine={activeLine} />

      {line ? (
        <LayerDrill line={line} nodes={map.nodes} laneColors={LANE_COLORS}
                    onOpen={openItem} onBack={() => { setActiveLine(null); syncHash({ route: "graph" }); }} />
      ) : (
        <>
          <nav className="tabs">
            <button className={tab === "graph" ? "active" : ""} onClick={() => setTab("graph")}>
              关系图
            </button>
            <button className={tab === "lanes" ? "active" : ""} onClick={() => setTab("lanes")}>
              泳道任务板
            </button>
            <button className={tab === "inbox" ? "active" : ""} onClick={() => setTab("inbox")}>
              待审队列{inbox.filter(q => !q.decision).length > 0 ? `（${inbox.filter(q => !q.decision).length}）` : ""}
            </button>
          </nav>
          {tab === "graph" ? (
            <DepGraph nodes={map.nodes} edges={map.edges}
                      criticalPath={map.critical_path}
                      laneColors={LANE_COLORS} onOpen={openItem} />
          ) : tab === "inbox" ? (
            <InboxPanel inbox={inbox} />
          ) : (
            <LaneBoard nodes={map.nodes} laneColors={LANE_COLORS} onOpen={openItem} />
          )}
        </>
      )}

      {selected && (
        <ItemDrawer id={selected} detail={detail} onClose={closeDrawer}
                    laneColors={LANE_COLORS} />
      )}
    </div>
  );
}
