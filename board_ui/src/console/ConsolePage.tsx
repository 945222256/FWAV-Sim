/* console/ConsolePage.tsx — 编排控制台外壳（I-144 起）。
 * 页面一为总览（I-168 三层重构：许愿池、战略蓝图、指挥部述职卡），
 * 其余页签（战情室与观察窗按入册时序后到），页脚并行额度与采样
 * 时刻；异常态执行评审定义：断连显示骨架与通栏横幅不保留旧值，
 * 滞后显示蒙层与页脚强调色。数据十秒轮询。 */
import { useCallback, useEffect, useState } from "react";
import type { ConsoleState, SourcePtr } from "./types";
import OverviewView from "./OverviewView";
import LaneView from "./LaneView";
import WatchView from "./WatchView";
import MsgView from "./MsgView";
import SessionHistoryView from "./SessionHistoryView";
import SourceDrawer from "./SourceDrawer";
import { advanceServerClock, parseTimestamp, sampleFreshness } from "./time";
import "./console.css";

export default function ConsolePage({ onBack }: { onBack: () => void }) {
  const [state, setState] = useState<ConsoleState | null>(null);
  const [failed, setFailed] = useState(false);
  const [clockTick, setClockTick] = useState(() => performance.now());
  const [src, setSrc] = useState<SourcePtr | null>(null);
  const [tab, setTab] = useState<"hq" | "lanes" | "watch" | "history" | "msg">("hq");
  const [serverBase, setServerBase] = useState<number | null>(null);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [viewer, setViewer] = useState("机主");
  // I-228：历史目录页与观察窗各线入口共用；从某线进入时预筛该线。
  const [historyLane, setHistoryLane] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/console/state");
      const receivedAt = performance.now();
      if (r.status === 401) { location.href = "/login"; return; }
      const dateHdr = r.headers.get("Date");
      const d = await r.json().catch(() => null);
      if (d && d.ok) {
        setState(d.result);
        setFailed(false);
        // Date 响应头给服务器时刻；后续只累计单调流逝，不受客户端墙钟影响。
        const base = dateHdr ? Date.parse(dateHdr) : NaN;
        setServerBase(Number.isFinite(base) ? base : null);
        setFetchedAt(Number.isFinite(base) ? receivedAt : null);
        setClockTick(performance.now());
      } else {
        setFailed(true);   // I-207：非网络层失败（HTTP 五百或业务拒收）同样进断连态，不把旧数据当当前值
      }
    } catch {
      setFailed(true);                     // 断连：旧数据整体让位骨架
    }
  }, []);

  useEffect(() => {
    refresh();
    // 观察窗判据：页面显示与后台真实状态偏差不超过五秒——该页签下
    // 轮询收紧到三秒；其余页签十秒。
    const period = tab === "watch" ? 3000 : 10000;
    const t = setInterval(refresh, period);
    const c = setInterval(() => setClockTick(performance.now()), 1000);
    return () => { clearInterval(t); clearInterval(c); };
  }, [refresh, tab]);

  useEffect(() => {
    fetch("/api/auth/whoami").then((r) => r.json()).then((d) => {
      if (d.ok && d.result?.user) setViewer(d.result.user);
    }).catch(() => { /* 身份展示失败不影响主功能 */ });
  }, []);

  const serverNow = advanceServerClock(serverBase, fetchedAt, clockTick);
  const sampledMs = parseTimestamp(state?.footer.sampled_at).ms;
  const sampledAt = sampledMs != null ? new Date(sampledMs) : null;
  const freshness = sampleFreshness(state?.footer.sampled_at, serverNow);
  const stale = failed || freshness.status !== "fresh";

  return (
    <div className="console">
      <header className="console-top">
        <div className="console-title">编排控制台
          <small>监视 · 干预 · 编排</small>
        </div>
        <nav className="console-tabs" aria-label="控制台页面">
          <button className={"console-tab" + (tab === "hq" ? " active" : "")}
                  onClick={() => setTab("hq")}>总览</button>
          <button className={"console-tab" + (tab === "lanes" ? " active" : "")}
                  onClick={() => setTab("lanes")}>战情室</button>
          <button className={"console-tab" + (tab === "watch" ? " active" : "")}
                  onClick={() => setTab("watch")}>观察窗</button>
          <button className={"console-tab" + (tab === "history" ? " active" : "")}
                  onClick={() => { setHistoryLane(null); setTab("history"); }}>历史目录</button>
          <button className={"console-tab" + (tab === "msg" ? " active" : "")}
                  onClick={() => setTab("msg")}>消息</button>
        </nav>
        <span className="spacer" />
        <span className="console-top-meta">
          {sampledAt
            ? "采样于 " + sampledAt.toLocaleTimeString("zh-CN", { hour12: false, timeZoneName: "short" })
            : ""}
        </span>
        <button className="lab-btn-minor" onClick={onBack}>返回作战地图</button>
      </header>

      {failed ? (
        <div className="console-error" role="alert">
          数据通道不可达，已停用旧数据显示。将在下一轮询自动恢复。
        </div>
      ) : null}

      <main className={"console-body" + (stale && !failed ? " stale" : "")}
            data-freshness={freshness.status}>
        {!state ? (
          <div className="console-skeleton" aria-label="加载中">
            <div className="sk-block" /><div className="sk-block" /><div className="sk-block" />
          </div>
        ) : tab === "hq" ? (
          <OverviewView state={state} onOpenSrc={setSrc} />
        ) : tab === "lanes" ? (
          serverNow != null
            ? <LaneView state={state} serverBase={serverNow} onOpenSrc={setSrc} />
            : <div className="console-empty">服务器时钟基准未取得，倒计时不可信，不显示。</div>
        ) : tab === "watch" ? (
          serverNow != null
            ? <WatchView state={state} serverNow={serverNow} onOpenSrc={setSrc}
                         onOpenHistory={(lane) => { setHistoryLane(lane); setTab("history"); }} />
            : <div className="console-empty">服务器时钟基准未取得，活动时刻不可信，不显示。</div>
        ) : tab === "history" ? (
          <SessionHistoryView prefilterLane={historyLane} />
        ) : (
          <MsgView viewer={viewer} />
        )}
      </main>

      <footer className="console-foot">
        <span>并行额度 {state?.footer.parallel.active ?? "—"}/{state?.footer.parallel.limit ?? "—"}</span>
        <span className={freshness.status !== "fresh" ? "foot-stale" : ""}>
          数据采样时刻 {state?.footer.sampled_at ?? "—"}
        </span>
        <span>每格可点击追溯原始记录</span>
        <span className="spacer" />
        {state && stale && !failed && <span className="foot-stale" role="status"
              data-testid="console-time-warning">{freshness.message}，正在等待可核对的新采样</span>}
      </footer>

      <SourceDrawer source={src} onClose={() => setSrc(null)} />
    </div>
  );
}
