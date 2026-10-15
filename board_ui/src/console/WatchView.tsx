/* console/WatchView.tsx — 观察窗（I-146 卡片墙与动效；I-147 干预面板；
 * I-166 反馈整改：会话按线分组、未挂事项默认折叠、筛选覆盖全部路线、
 * 并行额度实时口径与页面上限设置、控件按设计令牌打磨；
 * I-189 十条业务线常驻：L1 至 L10 永不因无活跃会话消失，空线保留
 * 灰态会话线块（主体为上下文进度条），有归档驻留记录则显示真实
 * 会话标识与采样时刻并明示读数缺失，无记录显示占位缺失态；
 * 灰块不参与并行额度计数，额度仍按 I-166 实时口径显示接口值）。
 *
 * 六条动效实现面（评审修订稿定稿，逐项在档）：
 * 一、令牌仪表平滑推进：宽度过渡四秒线性（数据跟踪动画，非界面过渡）；
 * 二、状态灯切换过渡：颜色与透明度二百毫秒；
 * 三、新回合脉冲：单次透明度变化，无发光扩散，同一卡六十秒合并；
 * 四、自动切换双卡动画：样式与类名已备（触发三期 I-149 已上线）；
 * 五、滚动无过渡，线筛选保留过渡；
 * 六、百分之七十预警：守门状态条加文字，卡片不变底色。
 * 密度上限四条：主动运动每视口不超三个；界面过渡不超二百毫秒；
 * 动效属性只用透明度与位移（状态灯颜色与仪表宽度为方案点名数据动效）；
 * 灰态保留块零动效。 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ConsoleState, SessionCard, SourcePtr } from "./types";
import HistoryDrawer from "./HistoryDrawer";
/* I-234 待领工作区：全局摘要、每线待领块与未分配独立区（消费冻结
 * 契约 pending-work-v1；任务块位于执行会话区之前；会话块、灰态与
 * 完整历史实现保持原样）。 */
import PendingWorkSummary from "./PendingWorkSummary";
import PendingTaskBlock from "./PendingTaskBlock";
import { usePendingWork, latestStageByItem } from "./pending_work_hooks";
import type { PwItem } from "./pending_work_types";
import { ago, sampleFreshness } from "./time";

const GATE_PCT = 70;                       // 切换守门线（方案第八节，I-149 已上线）
const PULSE_COOLDOWN_MS = 60000;
const GAUGE_S = 4;

/* 干预确认弹层（I-147：先弹确认再执行；理由必填入台账）。 */
function ActionModal({ action, session, onClose }: {
  action: "inject" | "stop";
  session: SessionCard;
  onClose: () => void;
}) {
  const [reason, setReason] = useState(action === "inject" ? "指挥部注入指令" : "指挥部停止指令");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    try {
      const r = await fetch("/api/console/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, session_id: session.session_id, reason, message }),
      });
      const d = await r.json();
      setNote(d.ok ? "已记台账并下发，采集代理五秒内执行（任务 #" + d.result.task_id + "）"
                   : "失败：" + (d.error ?? r.status));
    } catch (e) {
      setNote("失败：" + String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="ws-mask" onClick={onClose}>
      <div className="ws-modal" onClick={(e) => e.stopPropagation()} aria-label="干预确认"
           data-session-id={session.session_id} data-action={action}>
        <h3>{action === "inject" ? "注入消息" : "停止会话"}
          <span className="ws-modal-sid" title={session.session_id}>
            {session.session_id.slice(0, 14)}…</span></h3>
        {action === "inject" && (
          <label className="ws-field">注入正文
            <textarea value={message} onChange={(e) => setMessage(e.target.value)}
                      rows={4} placeholder="发给该会话的正文（可空，将发默认提示）" />
          </label>
        )}
        <label className="ws-field">理由（入台账必填）
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        <p className="ws-modal-note">
          {action === "inject"
            ? "确认后经宿主服务对该会话恢复后发送，回读核验。"
            : "确认后经宿主服务终止该会话驱动回路（协议子进程随后自愈拉起）。"}
        </p>
        {note && <p className="ws-modal-result">{note}</p>}
        <footer className="ws-modal-foot">
          <button className="ws-btn primary" disabled={busy || !reason.trim()}
                  onClick={submit}>确认执行</button>
          <button className="ws-btn" onClick={onClose}>{note ? "关闭" : "取消"}</button>
        </footer>
      </div>
    </div>
  );
}

/* I-207：当前事项核实与编号固定显示——认领核实值优先，派发登记快照
 * 只作历史来源；冲突时显示当前认领并注明历史挂接。 */
function sessionItem(s: SessionCard): {
  id: string | null; title: string | null; conflict: string | null; basis: string;
} {
  const cur = s.current_item_id ?? null;
  const reg = s.item_id ?? null;
  if (cur && reg && cur !== reg) {
    return { id: cur, title: null, conflict: reg, basis: "认领记录核实" };
  }
  if (cur) return { id: cur, title: null, conflict: null, basis: "认领记录核实" };
  if (reg) return { id: reg, title: null, conflict: null, basis: "派发登记（无更新认领）" };
  return { id: null, title: null, conflict: null, basis: "未挂事项" };
}

function SessionTile({ s, itemTitle, serverNow, onOpenSrc, onAction, onHistory, workerId, stage }: {
  s: SessionCard;
  itemTitle: string | null;
  serverNow: number | null;
  onOpenSrc: (p: SourcePtr) => void;
  onAction: (action: "inject" | "stop", session: SessionCard) => void;
  onHistory: (sessionId: string) => void;
  workerId?: string | null;
  stage?: string | null;
}) {
  const [pulse, setPulse] = useState(false);
  const lastSeen = useRef<string | null>(null);
  const lastPulse = useRef<number>(0);
  const gaugeBusy = useRef<number>(0);

  useEffect(() => {
    if (lastSeen.current != null && lastSeen.current !== s.last_activity_at) {
      const nowMs = Date.now();
      const gaugeRemain = gaugeBusy.current - nowMs;
      const cooled = nowMs - lastPulse.current >= PULSE_COOLDOWN_MS;
      if (gaugeRemain <= 0 && cooled) {
        setPulse(true);
        lastPulse.current = nowMs;
        const t = setTimeout(() => setPulse(false), 250);
        return () => clearTimeout(t);
      }
    }
    lastSeen.current = s.last_activity_at;
  }, [s.last_activity_at]);
  useEffect(() => {
    lastSeen.current = s.last_activity_at;
  }, []);                                    // 首挂不脉冲

  const pct = s.context_pct;
  const gated = pct != null && pct >= GATE_PCT;
  const freshness = sampleFreshness(s.source?.sampled_at, serverNow);
  const working = freshness.status === "fresh" && s.status === "在干活";
  const cur = sessionItem(s);
  return (
    <article className={"ws-card" + (pulse ? " pulse" : "")} data-session-id={s.session_id}
             data-freshness={freshness.status}
             aria-label={"会话卡 " + s.session_id}>
      {gated && <div className="ws-gatebar" aria-label="已达切换守门线" />}
      <header className="ws-card-head">
        {workerId != null
          ? <span className="ws-worker-chip" title="执行工位标识">工位 {workerId}</span>
          : <span className="ws-worker-chip ws-worker-none" title="编排队列未登记该会话的工位">工位未登记</span>}
        {cur.id
          ? <span className="ws-item-id" title={"当前事项依据：" + cur.basis}>{cur.id}</span>
          : <span className="ws-item-id ws-item-none">无事项</span>}
        <span className="ws-item">{itemTitle ?? cur.id ?? "未挂事项"}</span>
        <span className={"ws-status" + (working ? " working" : "")}>
          <span className="ws-dot" />{freshness.status === "fresh" ? (s.status ?? "未知") : "待核对"}
        </span>
      </header>
      {cur.conflict && (
        <div className="ws-conflict-note">
          当前认领 {cur.id}（依据{cur.basis}）；派发登记为 {cur.conflict}（历史挂接，未更新）。
        </div>
      )}
      {/* I-193：会话编号区与上下文主体区可直达工作履历（title 提示，
       * 点击不与按钮区重叠，按钮冒泡不误触）。I-218 v2：完整会话身份
       * 随 data-session-id 与 aria-label 保留，截断显示值不作身份依据。 */}
      <button className="ws-sid ws-sid-click" title="点击查看工作履历"
              aria-label={"会话 " + s.session_id}
              onClick={() => onHistory(s.session_id)}>{s.session_id.slice(0, 14)}…</button>
      <div className="ws-gauge-row ws-gauge-click" title="点击查看工作履历"
           role="button" tabIndex={0}
           onClick={() => onHistory(s.session_id)}
           onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onHistory(s.session_id); }}>
        <span className="ws-gauge" aria-label={"上下文占用 " + (pct ?? "无读数") + "%"}>
          <span className="ws-gauge-fill" style={{
            width: (pct ?? 0) + "%",
            transition: `width ${GAUGE_S}s linear`,
          }} ref={(el) => {
            if (el) gaugeBusy.current = Date.now() + GAUGE_S * 1000;
          }} />
        </span>
        <span className="ws-gauge-num">{pct == null ? "无读数" : pct.toFixed(1) + "%"}</span>
        {gated && <span className="ws-gate-note">已达守门线 {GATE_PCT}%</span>}
      </div>
      <div className="ws-activity" title={s.last_activity_at ?? "未提供活动时间"}>
        最后活动 {ago(s.last_activity_at, serverNow)}</div>
      {/* I-228 判据五：实况对账字段——状态取后端判定串（运行/等待/
       * 停止中/已停止/未知，含待核对原因后缀），采样时刻与对账摘录
       * 取会话行 source；不以模型请求时间单独推断停止。 */}
      {freshness.status !== "fresh" && <div className="ws-time-warning" role="status">
        {freshness.message}；上次记录：{s.status ?? "未知"}
      </div>}
      <div className="ws-sample-line" title={"采样 " + (s.source?.sampled_at ?? "未提供")
             + "；" + (s.source?.excerpt ?? "")}
           data-status={s.status ?? ""}>
        采样 {s.source?.sampled_at?.replace("T", " ") ?? "—"}
        {s.status && /[（(]/.test(s.status) ? "｜" + s.status : ""}
      </div>
      {/* I-234：已核实的接续阶段（认领确认/实际运行）按事件回读标注；
       * 原会话接新任务不重置上下文读数（进度条保持既有状态）。 */}
      {stage && (
        <div className="ws-continue-line"
             title="来自调度因果事件回读（认领确认或实际运行）">
          接续 {stage === "run_started" ? "实际运行" : "已认领"}
        </div>)}
      <footer className="ws-actions">
        <button className="ws-btn primary" onClick={() => onAction("inject", s)}>注入</button>
        <button className="ws-btn primary" onClick={() => onAction("stop", s)}>停止</button>
        <button className="ws-btn" disabled title="I-148 追溯抽屉施工后启用">转录</button>
        <button className="ws-btn" onClick={() => onHistory(s.session_id)}>履历</button>
        <button className="ws-btn" onClick={() => onOpenSrc(s.source)}>出处</button>
      </footer>
    </article>
  );
}

/* I-189 灰态保留线块：无活跃会话的业务线常驻显示。
 * 有归档驻留记录：显示真实会话标识、采样时刻，读数按缺失态明示
 * （归档行接口不带上下文读数，不编造数值）；出处角标保留。
 * 无任何记录：占位缺失态，无会话动作、无出处指针。零动效。
 * I-218 v2：同一业务线每个空闲或退出的真实历史工位各保留一张灰卡
 * （按完整 session_id 一人一卡，最后事项与履历出处随卡保留），
 * 与活跃工位卡并存显示；卡体携带完整会话身份（data-session-id）。 */
function RetainedTile({ laneId, laneName, retainedCard, retainedKind, serverNow, onOpenSrc, onHistory, reasonMissing }: {
  laneId: string;
  laneName: string;
  retainedCard: SessionCard | null;
  retainedKind: "retained" | "archived" | "none";
  serverNow: number | null;
  onOpenSrc: (p: SourcePtr) => void;
  onHistory: (sessionId: string) => void;
  reasonMissing?: string | null;
}) {
  const card = retainedCard;
  const kind = card ? retainedKind : "none";
  const cur = card ? sessionItem(card) : null;
  return (
    <article className="ws-card retained" aria-label={laneId + " 非活跃会话线块"}
             data-session-id={card?.session_id}>
      <header className="ws-card-head">
        <span className="ws-item">{laneId} {laneName}</span>
        <span className="ws-status ws-status-retained">
          <span className="ws-dot" />{kind === "retained" ? "空闲超时保留" : kind === "archived" ? "已归档" : "非活跃"}
        </span>
      </header>
      {card ? (
        <>
          <button className="ws-sid ws-sid-click" title="点击查看工作履历"
                  onClick={() => onHistory(card.session_id)}>
            {card.session_id.slice(0, 14)}…
          </button>
          <div className="ws-gauge-row ws-gauge-click" title="点击查看工作履历"
               role="button" tabIndex={0}
               onClick={() => onHistory(card.session_id)}
               onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onHistory(card.session_id); }}>
            <span className="ws-gauge" aria-label={"上下文占用 " + (card.context_pct != null ? card.context_pct + "%（超时前采样，已过期）" : "暂无读数")}>
              <span className="ws-gauge-fill ws-gauge-fill-retained"
                    style={{ width: (card.context_pct ?? 0) + "%" }} />
            </span>
            <span className="ws-gauge-num ws-gauge-num-retained">
              {card.context_pct != null ? card.context_pct.toFixed(1) + "%（过期）" : "暂无读数"}
            </span>
          </div>
          <div className="ws-activity">
            {kind === "retained"
              ? <>空闲超时保留（跨三十五分钟新鲜度门槛，未归档）｜最近活动 {ago(card.last_activity_at, serverNow)}｜采样 {card.source?.sampled_at?.replace("T", " ") ?? "—"}</>
              : <>最近记录 {ago(card.last_activity_at, serverNow)}（已归档）｜采样 {card.source?.sampled_at?.replace("T", " ") ?? "—"}</>}
          </div>
          {sampleFreshness(card.source?.sampled_at, serverNow).status !== "fresh" && (
            <div className="ws-time-warning" role="status">
              {sampleFreshness(card.source?.sampled_at, serverNow).message}；保留为历史记录
            </div>)}
          {cur?.id
            ? <div className="ws-retained-note">最近承办 {cur.id}（{kind === "retained" ? "认领核实" : "归档前"}）{cur.conflict ? "，派发登记 " + cur.conflict + " 为历史挂接" : ""}</div>
            : <div className="ws-retained-note">{card.source?.excerpt || "驻留记录"}</div>}
          <footer className="ws-actions">
            <button className="ws-btn" onClick={() => onHistory(card.session_id)}>履历</button>
            <button className="ws-btn" onClick={() => onOpenSrc(card.source)}>出处</button>
          </footer>
        </>
      ) : (
        <>
          <div className="ws-sid ws-retained-missing">尚未核查到驻留会话记录</div>
          <div className="ws-gauge-row">
            <span className="ws-gauge" aria-label="上下文占用 暂无读数">
              <span className="ws-gauge-fill ws-gauge-fill-retained" style={{ width: "0%" }} />
            </span>
            <span className="ws-gauge-num ws-gauge-num-retained">暂无读数</span>
          </div>
          <div className="ws-activity">{reasonMissing
            ?? "本线当前无活跃会话，暂无可核查的驻留记录与上下文读数"}</div>
          <div className="ws-activity">暂无会话历史（未建立会话）</div>
        </>
      )}
    </article>
  );
}

export default function WatchView({ state, serverNow, onOpenSrc, onOpenHistory }: {
  state: ConsoleState;
  serverNow: number | null;
  onOpenSrc: (p: SourcePtr) => void;
  onOpenHistory: (lane: string) => void;
}) {
  const [laneFilter, setLaneFilter] = useState<string>("全部");
  const [modal, setModal] = useState<{ action: "inject" | "stop"; session: SessionCard } | null>(null);
  const [historySid, setHistorySid] = useState<string | null>(null);
  const [gateMode, setGateMode] = useState<string>("…");
  const [, setCapLimit] = useState<number | null>(null);
  const [capInput, setCapInput] = useState<string>("");
  const [capNote, setCapNote] = useState<string | null>(null);
  const [foldUnattached, setFoldUnattached] = useState(true);
  const pw = usePendingWork();
  const stagesByItem = useMemo(() => latestStageByItem(pw.recentStages), [pw.recentStages]);
  const stagesBySession = useMemo(() => {
    const m = new Map<string, string>();
    for (const st of pw.recentStages) {
      if (st.session_id && (st.type === "claim_confirmed" || st.type === "run_started")) {
        m.set(st.session_id, st.type);
      }
    }
    return m;
  }, [pw.recentStages]);
  const pendingByLane = useMemo(() => {
    const m = new Map<string, PwItem[]>();
    for (const it of pw.snapshot?.items ?? []) {
      if (!it.lane) continue;
      const k = it.lane;
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(it);
    }
    return m;
  }, [pw.snapshot]);
  const pendingUnassigned = useMemo(
    () => (pw.snapshot?.items ?? []).filter((it) => !it.lane), [pw.snapshot]);
  const itemTitles = useMemo(() => {
    const m = new Map<string, string>();
    state.lanes.forEach((l) => l.items.forEach((i) => m.set(i.id, i.title)));
    return m;
  }, [state.lanes]);
  const laneNames = useMemo(() => {
    const m = new Map<string, string>();
    state.lanes.forEach((l) => m.set(l.id, l.name));
    return m;
  }, [state.lanes]);

  const refreshConfig = useCallback(() => {
    fetch("/api/console/config").then((r) => r.json()).then((d) => {
      if (d.ok) {
        setGateMode(d.result.handoff_mode);
        setCapLimit(d.result.parallel_limit);
        setCapInput(String(d.result.parallel_limit));
      }
    }).catch(() => setGateMode("未知"));
  }, []);
  useEffect(() => { refreshConfig(); }, [refreshConfig, state.footer.sampled_at]);

  const saveCap = async () => {
    const v = parseInt(capInput, 10);
    if (!Number.isFinite(v)) { setCapNote("须为整数"); return; }
    try {
      const r = await fetch("/api/console/config", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parallel_limit: v }),
      });
      const d = await r.json();
      setCapNote(d.ok ? "上限已设为 " + d.result.parallel_limit + "（调整已留痕）"
                      : "失败：" + (d.error ?? r.status));
      if (d.ok) setCapLimit(d.result.parallel_limit);
    } catch (e) {
      setCapNote("失败：" + String(e));
    }
  };

  const gateLabel = gateMode === "auto" ? "开（自动）"
    : gateMode === "confirm" ? "切换前需机主确认" : gateMode === "off" ? "关" : gateMode;

  /* I-189：业务线白名单 L1 至 L10（机主本次明确要求十条常驻，判据五
   * 按 10 个业务线区域核验）。HQ 与未知路线的会话归未挂组，不再单列。 */
  const bizLanes = useMemo(
    () => state.lanes.filter((l) => /^L\d+$/.test(l.id)),
    [state.lanes]);
  const allLanes = useMemo(() => bizLanes.map((l) => l.id), [bizLanes]);
  const shown = laneFilter === "全部"
    ? state.sessions.active
    : state.sessions.active.filter((s) => s.lane === laneFilter);

  /* I-207/I-218 v2：灰块驻留来源三级——超时保留行（retained，行本体
   * 与最近采样读数在册）优先，其次归档行，最后无记录占位。
   * I-218 v2 改逐卡保留：不再只取最新一张，同一业务线每个空闲或
   * 退出的真实工位会话各一张（按完整 session_id 去重，retained 覆盖
   * 同号 archived，按更新时间倒序）。工位会话判定优先服务端
   * _is_worker 标记（api_console 下发），旧载荷缺该字段时按
   * 线别加事项挂接启发（与服务端判定同式），不把机主组行算作工位。
   * 无工位历史时回落 I-189 旧口径（任意驻留行最新一张），保持空线
   * 常驻灰块行为不变。 */
  const laneHistory = useCallback((lane: string): { card: SessionCard; kind: "retained" | "archived" }[] => {
    const pool = [
      ...state.sessions.retained.map((c) => ({ card: c, kind: "retained" as const, rank: 0 })),
      ...state.sessions.archived.map((c) => ({ card: c, kind: "archived" as const, rank: 1 })),
    ].filter((r) => r.card.lane === lane);
    const bySid = new Map<string, { card: SessionCard; kind: "retained" | "archived"; rank: number }>();
    for (const r of pool) {
      const prev = bySid.get(r.card.session_id);
      if (!prev || r.rank < prev.rank
          || (r.rank === prev.rank
              && (r.card.updated_at ?? "").localeCompare(prev.card.updated_at ?? "") > 0)) {
        bySid.set(r.card.session_id, r);
      }
    }
    const list = [...bySid.values()].sort((a, b) =>
      (b.card.updated_at ?? "").localeCompare(a.card.updated_at ?? ""));
    const isWorker = (c: SessionCard) => {
      const flag = (c as SessionCard & { _is_worker?: boolean })._is_worker;
      if (flag != null) return flag;
      return !!(c.lane && (c.current_item_id || c.item_id));
    };
    const workerHist = list.filter((r) => isWorker(r.card));
    if (workerHist.length > 0) return workerHist.map(({ card, kind }) => ({ card, kind }));
    const fallback = list[0];
    return fallback ? [{ card: fallback.card, kind: fallback.kind }] : [];
  }, [state.sessions.retained, state.sessions.archived]);

  /* I-189：全部路线视图业务线组常驻（空组不过滤，灰块兜底）；单路线
   * 筛选只显示选中线（该线非活跃时灰块可见，判据五）。未挂组仅有卡时显示。 */
  const groups = useMemo(() => {
    const order = laneFilter === "全部" ? [...allLanes, "未挂"] : [laneFilter];
    const byLane = new Map<string, SessionCard[]>();
    order.forEach((l) => byLane.set(l, []));
    shown.forEach((s) => {
      const key = s.lane && byLane.has(s.lane) ? s.lane : "未挂";
      if (byLane.has(key)) byLane.get(key)!.push(s);
    });
    return order
      .map((l) => ({ lane: l, cards: byLane.get(l) ?? [] }))
      .filter((g) => g.lane === "未挂" ? g.cards.length > 0 : true);
  }, [shown, allLanes, laneFilter]);

  const renderGroup = (lane: string, cards: SessionCard[]) => {
    const attached = cards.filter((c) => c.item_id);
    const unattached = cards.filter((c) => !c.item_id);
    const isBiz = lane !== "未挂";
    const label = lane === "未挂" ? "未挂路线"
      : (laneNames.get(lane) ? lane + " " + laneNames.get(lane) : lane);
    const foldable = unattached.length > 0;
    /* I-218 v2：逐工位历史灰卡——本线驻留/归档会话按完整 session_id
     * 去重后全部保留；正以活跃卡显示的会话不再重复出灰卡（同号排除）。
     * 活跃与历史并存：一活跃一空闲出两卡，双空闲出两张灰卡。 */
    const activeSids = new Set(cards.map((c) => c.session_id));
    const historyRecs = isBiz
      ? laneHistory(lane).filter((r) => !activeSids.has(r.card.session_id))
      : [];
    const workersBySession = new Map<string, string>(
      ((state.lane_queues?.queues[lane]?.workers) ?? [])
        .filter((w) => w.session_id)
        .map((w) => [w.session_id as string, w.worker_id ?? "工位未登记"] as [string, string]));
    /* I-234：待领任务块位于执行会话区之前（较薄块）；每线待领数取
     * 快照 summary.by_lane（同一可核对数据范围）。 */
    const lanePending = isBiz ? (pendingByLane.get(lane) ?? []) : [];
    return (
      <section className="ws-group" key={lane} data-pending={lanePending.length}>
        <header className="ws-group-head">
          <span className="ws-group-lane">{label}</span>
          <span className="ws-group-count">
            {(() => {
              const q = isBiz ? state.lane_queues?.queues[lane] : undefined;
              const pend = isBiz && lanePending.length > 0
                ? " ｜ 待领 " + lanePending.length : "";
              const base = cards.length > 0 ? cards.length + " 会话" : "0 活跃 ｜ 灰态保留";
              const gray = historyRecs.length > 0
                ? " ｜ 历史工位灰卡 " + historyRecs.length : "";
              if (!q) return isBiz ? base + gray + pend + " ｜ 排队未采集" : base;
              const running = q.workers?.length;
              const qtext = running != null
                ? `执行 ${running}/${q.concurrency_limit ?? "—"} ｜ 等待 ${q.waiting_count}`
                : `排队 ${q.waiting_count}（工位明细未采集）`;
              return base + gray + pend + " ｜ " + qtext;
            })()}
          </span>
          {foldable && (
            <button className="ws-fold-toggle" onClick={() => setFoldUnattached((v) => !v)}>
              {foldUnattached ? "▸" : "▾"} 未挂事项（{unattached.length}）
            </button>
          )}
          {/* I-228 判据一：每条业务线的完整会话历史入口（活跃、保留、
           * 归档与历次交接全部可达，不只最新一条）。 */}
          {isBiz && (
            <button className="ws-history-toggle"
                    title="展开本线完整会话历史（含超时保留、显式归档与交接链，只读）"
                    onClick={() => onOpenHistory(lane)}>完整历史</button>
          )}
        </header>
        {isBiz && lanePending.length > 0 && (
          <div className="pw-lane-list" data-testid="pw-lane-list">
            {lanePending.map((it) => (
              <PendingTaskBlock key={it.item_id} item={it}
                                stage={stagesByItem.get(it.item_id) ?? null} />))}
          </div>
        )}
        {cards.length === 0 && isBiz ? (
          <div className="ws-grid">
            {historyRecs.length > 0
              ? historyRecs.map((r) => (
                  <RetainedTile key={r.card.session_id} laneId={lane}
                                laneName={laneNames.get(lane) ?? lane}
                                retainedCard={r.card} retainedKind={r.kind}
                                serverNow={serverNow} onOpenSrc={onOpenSrc}
                                onHistory={setHistorySid} />))
              : <RetainedTile laneId={lane} laneName={laneNames.get(lane) ?? lane}
                              retainedCard={null} retainedKind="none"
                              serverNow={serverNow} onOpenSrc={onOpenSrc}
                              onHistory={setHistorySid}
                              reasonMissing="本线当前无活跃会话，且接口无可核查的工位驻留或归档记录（不编造会话）" />}
          </div>
        ) : (
          <>
            <div className="ws-grid">
              {attached.map((s) => (
                <SessionTile key={s.session_id} s={s}
                             itemTitle={itemTitles.get((s.current_item_id ?? s.item_id)!) ?? null}
                             serverNow={serverNow} onOpenSrc={onOpenSrc} onAction={setModalWrap}
                             onHistory={setHistorySid}
                             workerId={workersBySession.get(s.session_id)}
                             stage={stagesBySession.get(s.session_id) ?? null} />
              ))}
              {isBiz && historyRecs.map((r) => (
                <RetainedTile key={"hist-" + r.card.session_id} laneId={lane}
                              laneName={laneNames.get(lane) ?? lane}
                              retainedCard={r.card} retainedKind={r.kind}
                              serverNow={serverNow} onOpenSrc={onOpenSrc}
                              onHistory={setHistorySid} />
              ))}
            </div>
            {foldable && (
              <div className={"ws-unattached" + (foldUnattached ? " folded" : "")}>
                {foldUnattached ? (
                  <p className="ws-none">未挂事项会话 {unattached.length} 张已折叠。</p>
                ) : (
                  <div className="ws-grid">
                    {unattached.map((s) => (
                      <SessionTile key={s.session_id} s={s} itemTitle={null}
                                   serverNow={serverNow} onOpenSrc={onOpenSrc} onAction={setModalWrap}
                                   onHistory={setHistorySid} />
                    ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </section>
    );
  };

  const setModalWrap = useCallback(
    (action: "inject" | "stop", session: SessionCard) => setModal({ action, session }),
    []);

  return (
    <div className="ws">
      <PendingWorkSummary pw={pw} stagesByItem={stagesByItem} />
      <div className="ws-head">
        <span>活跃会话 {state.sessions.active.length}</span>
        <span>实时活跃 {state.footer.parallel.active} ｜ 额度 {state.footer.parallel.active}/{state.footer.parallel.limit}</span>
        <span>自动切换：{gateLabel}（守门线 {GATE_PCT}%）</span>
        <span className="spacer" />
        <label className="ws-cap">额度上限
          <input className="ws-cap-input" value={capInput} inputMode="numeric"
                 onChange={(e) => setCapInput(e.target.value)} aria-label="并行额度上限" />
        </label>
        <button className="ws-btn" onClick={saveCap}>设上限</button>
        <label className="ws-filter">线筛选
          <select value={laneFilter} onChange={(e) => setLaneFilter(e.target.value)}>
            <option>全部</option>
            {allLanes.map((l) => <option key={l}>{l}</option>)}
          </select>
        </label>
      </div>
      {capNote && <p className="ws-cap-note">{capNote}</p>}
      {/* I-189：业务线常驻，空态不再吞掉分组；额度读数仍取接口实时值，
          灰块不参与活跃计数。 */}
      <div key={laneFilter} className="ws-groups">
        {groups.map((g) => renderGroup(g.lane, g.cards))}
      </div>
      {/* I-234：未分配路线的待领任务独立入口（不伪装成第十一条业务线）。 */}
      {pendingUnassigned.length > 0 && (
        <section className="ws-group pw-unassigned-group" aria-label="未分配路线的待领任务">
          <header className="ws-group-head">
            <span className="ws-group-lane">未分配路线</span>
            <span className="ws-group-count">
              待领 {pendingUnassigned.length}
              ｜ {pw.snapshot?.summary.unassigned?.note ?? "未分配路线的正式任务独立入口"}</span>
          </header>
          <div className="pw-lane-list">
            {pendingUnassigned.map((it) => (
              <PendingTaskBlock key={it.item_id} item={it}
                                stage={stagesByItem.get(it.item_id) ?? null} />))}
          </div>
        </section>
      )}
      <section className="ws-archive">
        <h3>归档抽屉</h3>
        {state.sessions.archived.length === 0 ? (
          <p className="ws-none">暂无归档会话（上下文自动切换在此可查）。</p>
        ) : (
          <ul className="ws-archive-list">
            {state.sessions.archived.map((s) => (
              <li key={s.session_id}>
                <span className="ws-sid">{s.session_id.slice(0, 14)}…</span>
                <span className="ws-item">{s.item_id ?? "未挂事项"}</span>
                <span className="ws-archive-reason">{s.source?.excerpt ?? ""}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      {modal && (
        <ActionModal action={modal.action} session={modal.session}
                     onClose={() => setModal(null)} />
      )}
      {historySid && (
        <HistoryDrawer sessionId={historySid} onClose={() => setHistorySid(null)}
                       onOpenSrc={onOpenSrc} />
      )}
    </div>
  );
}
