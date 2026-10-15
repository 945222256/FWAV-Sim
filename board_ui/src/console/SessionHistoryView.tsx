/* console/SessionHistoryView.tsx — 会话历史目录（I-228 v2 返修版）。
 * 数据全部来自上游 I-227 session_history 只读查询服务；本页只读。
 *
 * v2 返修（Ciri 三反例 + 原需求，评审意见见
 * board_ui/lab_design/评审意见_I-228_完整会话历史界面_Ciri_20261014.md）：
 *  一 筛选异步竞态：挂载即以预筛为初始已应用条件（不发错误首请求）；
 *    请求代次递增，迟到的旧代次响应不得写入列表、数量、索引版本、
 *    错误或加载状态；覆盖快速连续筛选与关闭重开。
 *  二 分页遍历：按实际页号推进（不从去重数量推算）；识别
 *    index_built_at 换版即重置并明确重新开始本版本遍历，不跨版本
 *    拼接或宣称无遗漏；稳定版本内按页核对完整集合。
 *  三 真实状态：保留类型 active 语义为近期保留（未超时保留），
 *    不再标运行中；执行状态按索引 execution_phase 相位如实映射，
 *    无来源明确未知；实时状态仍以观察窗实况卡为准。
 *  四 旧会话事项履历：历次事项补可核标题、当前账本状态与账本事件
 *    时间线（复用 /api/item-detail 与 /api/events 只读来源）；认领
 *    核实仅认账本认领事件（会话号在册），索引/派发关联不冒充认领；
 *    无认领、归属未核实与账本读取失败分别说明。 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { HistoryDetail, HistoryListItem, HistoryListResp } from "./types";
import { RETENTION_LABEL, STATUS_WORD } from "./types";

const PAGE_SIZE = 50;

interface Filters {
  lane: string; worker_id: string; item_id: string;
  date_from: string; date_to: string; retention: string;
}

const EMPTY_FILTERS: Filters = { lane: "", worker_id: "", item_id: "",
                                 date_from: "", date_to: "", retention: "" };

/* 账本事件只读来源（与 HistoryDrawer 同源；轻缓存避免逐开抽屉重拉）。 */
interface LedgerEvent {
  seq: number; ts: string; action: string; item_id: string | null;
  actor: string | null; detail: string | null;
}
const ACT_LABEL: Record<string, string> = {
  assign: "派发", claim: "认领", release: "释放", block: "阻塞",
  unblock: "解除阻塞", close: "结载",
};
let eventsCache: { at: number; events: LedgerEvent[] } | null = null;
async function fetchLedgerEvents(): Promise<LedgerEvent[]> {
  if (eventsCache && Date.now() - eventsCache.at < 10000) return eventsCache.events;
  const r = await fetch("/api/events");
  if (!r.ok) throw new Error("events http " + r.status);
  const d = await r.json();
  if (!d.ok) throw new Error("events rejected");
  const events: LedgerEvent[] = Array.isArray(d.result) ? d.result : (d.result?.events ?? []);
  eventsCache = { at: Date.now(), events };
  return events;
}

function qs(f: Filters, page: number): string {
  const p = new URLSearchParams();
  if (f.lane) p.set("lane", f.lane);
  if (f.worker_id) p.set("worker_id", f.worker_id.trim());
  if (f.item_id) p.set("item_id", f.item_id.trim());
  if (f.date_from) p.set("date_from", f.date_from);
  if (f.date_to) p.set("date_to", f.date_to);
  if (f.retention) p.set("retention", f.retention);
  p.set("page", String(page));
  p.set("page_size", String(PAGE_SIZE));
  return p.toString();
}

function fmt(iso: string | null | undefined): string {
  return iso ? iso.replace("T", " ").slice(0, 19) : "—";
}

/* 执行相位 → 如实状态词（来源为历史索引快照，非实时判定）。 */
function phaseLabel(phase: string | null | undefined): string {
  switch (phase) {
    case "active": return "运行（索引相位）";
    case "idle": return "空闲（索引相位）";
    case "stopped": return "已停止";
    case "stopping": return "停止中";
    case "retired": return "已退役";
    default: return "未知（索引无执行相位来源）";
  }
}

function RetentionBadge({ r }: { r: HistoryListItem["retention"] }) {
  const kind = r?.kind ?? "";
  const label = RETENTION_LABEL[kind] ?? (kind || "未登记");
  return <span className={"sh-badge sh-k-" + (kind || "none")}
               title={r?.reason ?? undefined}>{label}</span>;
}

/* ── 单会话只读详情（含 v2 事项履历补齐） ── */
function SessionDetail({ sid, onNavigate, onClose }: {
  sid: string;
  onNavigate: (sid: string) => void;
  onClose: () => void;
}) {
  const [d, setD] = useState<HistoryDetail | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [events, setEvents] = useState<LedgerEvent[] | null>(null);
  const [eventsErr, setEventsErr] = useState<string | null>(null);
  const [items, setItems] = useState<Record<string, {
    title?: string; status?: string; error?: string }>>({});

  useEffect(() => {
    let alive = true;
    setD(null);
    setFailed(null);
    setEvents(null);
    setEventsErr(null);
    setItems({});
    fetch("/api/session_history/sessions/" + encodeURIComponent(sid))
      .then((r) => r.json().catch(() => null))
      .then((body) => {
        if (!alive) return;
        if (!body || !body.ok) {
          setFailed((body && body.result && body.result.reason)
            ?? (body && body.reason) ?? "历史查询接口失败（无响应体）");
          return;
        }
        setD(body.result ?? body);
      })
      .catch((e) => { if (alive) setFailed("历史查询接口失败：" + String(e)); });
    return () => { alive = false; };
  }, [sid]);

  /* 账本事件（只读）：认领核实与时间线共用同一来源。 */
  useEffect(() => {
    let alive = true;
    fetchLedgerEvents()
      .then((evs) => { if (alive) setEvents(evs); })
      .catch((e) => { if (alive) setEventsErr("账本事件读取失败：" + String(e)); });
    return () => { alive = false; };
  }, []);

  /* 各事项当前账本状态（只读；失败逐项说明）。 */
  useEffect(() => {
    let alive = true;
    const ids = Object.keys(d?.items ?? {});
    ids.forEach((id) => {
      fetch("/api/item-detail?id=" + encodeURIComponent(id))
        .then((r) => r.json().catch(() => null))
        .then((body) => {
          if (!alive) return;
          setItems((cur) => {
            const next = { ...cur };
            if (body && body.ok) {
              next[id] = { title: body.result?.item?.title,
                           status: body.result?.item?.status };
            } else {
              next[id] = { error: (body && (body.error ?? body.reason))
                ?? "HTTP 无响应（账本读取失败）" };
            }
            return next;
          });
        })
        .catch((e) => {
          if (alive) setItems((cur) => ({ ...cur, [id]: { error: String(e) } }));
        });
    });
    return () => { alive = false; };
  }, [d]);

  const myClaims = (iid: string) => (events ?? []).filter(
    (e) => e.action === "claim" && e.item_id === iid
      && (e.detail || "").includes(sid));

  return (
    <div className="src-mask" onClick={onClose}>
      <aside className="src-drawer sh-drawer" onClick={(e) => e.stopPropagation()}
             aria-label="旧会话只读详情" data-session-id={sid}>
        <header className="src-drawer-head">
          <div>
            <div className="src-drawer-kind">旧会话只读详情
              <span className="sh-readonly">只读 · 不恢复执行 · 不计实时额度</span></div>
            <div className="src-drawer-locus sh-full-sid">{sid}</div>
          </div>
          <button className="lab-btn-minor" onClick={onClose}>关闭</button>
        </header>
        <div className="src-drawer-body">
          {failed && <p className="sh-reason-block" role="alert">
            无法展开该会话：{failed}</p>}
          {!failed && !d && <p className="hist-missing">历史详情读取中…</p>}
          {d && (
            <>
              <p className="sh-meta-line">
                线 {d.lane ?? "—"}｜保留类型 {RETENTION_LABEL[d.retention?.kind ?? ""]
                  ?? d.retention?.kind ?? "未登记"}
                {d.retention?.reason ? "（" + d.retention.reason + "）" : ""}
                ｜采样（索引构建）{fmt(d.last_seen_at)}
              </p>
              {/* v2 返修三：执行状态按索引相位如实映射；实时状态另有来源。 */}
              <p className="sh-meta-line" data-testid="sh-exec-phase">
                执行状态 {phaseLabel(d.execution_phase)}
                （相位取自历史索引快照；实时运行/等待/停止/未知以观察窗实况卡为准）
              </p>
              <p className="sh-meta-line">
                首见 {fmt(d.first_seen_at)}｜最后活动 {fmt(d.last_seen_at)}
                ｜来源 {(d.sources ?? []).join("、") || "—"}
              </p>
              {(Object.keys(d.missing ?? {}).length > 0) && (
                <p className="sh-reason-block">来源缺失说明：
                  {Object.entries(d.missing).map(([k, v]) => k + "：" + v).join("；")}</p>
              )}
              {d.title_head && <p className="sh-title">会话标题首行：{d.title_head.slice(0, 120)}</p>}

              <h4 className="sh-sec">历次事项工作履历（账本只读来源）</h4>
              {Object.keys(d.items ?? {}).length === 0 ? (
                <p className="sh-reason-block">无认领记录：该会话在索引内无任何事项
                  认领或派发登记来源，无法陈列事项履历。</p>
              ) : (
                <ul className="sh-items">
                  {Object.entries(d.items).map(([iid, it]) => {
                    const claims = myClaims(iid);
                    const ledger = items[iid];
                    const timeline = (events ?? [])
                      .filter((e) => e.item_id === iid && ACT_LABEL[e.action])
                      .sort((a, b) => a.seq - b.seq);
                    return (
                      <li key={iid} className="sh-item-history" data-item-id={iid}>
                        <div className="sh-item-head">
                          <span className="ov-id">{iid}</span>
                          <span className="sh-item-title">
                            {ledger?.title ?? (ledger?.error ? "标题读取失败" : "账本读取中…")}</span>
                          <span className={"ov-chip"
                            + ((ledger?.status ?? "") === "closed" ? " ok" : "")}>
                            {ledger?.status
                              ? (STATUS_WORD[ledger.status] ?? ledger.status)
                              : ledger?.error ? "账本状态读取失败" : "…"}
                          </span>
                        </div>
                        {ledger?.error && (
                          <p className="sh-reason-block">当前账本状态读取失败：
                            {ledger.error}（履历其余部分仍按索引与事件在册陈列）</p>)}
                        <div className="sh-attribution">
                          {claims.length > 0
                            ? "认领核实：本会话认领事件在册（账本）"
                              + claims.map((c) => " " + fmt(c.ts)).join("、")
                            : "归属未核实：仅索引关联（"
                              + ((it.sources ?? []).join("、") || "无来源标注")
                              + "），无本会话认领事件，不冒充认领核实"}
                        </div>
                        <ul className="sh-timeline">
                          {timeline.map((e) => (
                            <li key={e.seq} className={e.action === "claim"
                              && (e.detail || "").includes(sid) ? "mine" : ""}>
                              <span className="sh-ev-act">{ACT_LABEL[e.action]}</span>
                              <span className="sh-ev-ts">{fmt(e.ts)}</span>
                              <span className="sh-ev-actor">{e.actor ?? "—"}</span>
                              <span className="sh-ev-src">账本事件 seq {e.seq}</span>
                              {e.action === "claim"
                                && !(e.detail || "").includes(sid)
                                && <span className="sh-ev-note">（他方认领）</span>}
                            </li>))}
                          {timeline.length === 0 && (
                            <li className="sh-ev-none">
                              {eventsErr ? eventsErr
                                : "该事项无派发/认领/释放/阻塞/结载账本事件（未登记）"}</li>)}
                        </ul>
                      </li>
                    );
                  })}
                </ul>
              )}

              <h4 className="sh-sec">运行记录（{d.runs?.length ?? 0} 条）</h4>
              {(d.runs ?? []).length === 0 ? (
                <p className="sh-none-line">无运行记录（无在册运行来源）。</p>
              ) : (
                <ul className="sh-items">
                  {d.runs.map((r, n) => (
                    <li key={(r.run_id ?? "run") + n} className="sh-item-row">
                      <span>{r.run_id ?? "—"}</span><span>{r.item_id ?? "—"}</span>
                      <span>{r.worker_id ?? "无工位"}</span>
                      <span>{fmt(r.started_at)}</span><span>{r.status ?? "—"}</span>
                    </li>
                  ))}
                </ul>
              )}

              <h4 className="sh-sec">交接卡与前后会话链</h4>
              {(d.handover_cards ?? []).length === 0
                && (d.successor_chain ?? []).length === 0
                && (d.predecessor_chain ?? []).length === 0 ? (
                <p className="sh-none-line">无交接记录（该会话无交接来源）。</p>
              ) : (
                <>
                  {(d.predecessor_chain ?? []).length > 0 && (
                    <p className="sh-chain">前代链：
                      {d.predecessor_chain.map((s) => (
                        <button key={s} className="sh-chain-sid"
                                onClick={() => onNavigate(s)}>{s}</button>))}
                    </p>)}
                  {(d.handover_cards ?? []).map((h, n) => (
                    <div key={n} className="sh-handover">
                      <div className="sh-handover-head">
                        交接 {fmt(h.at)}｜{h.reason ?? "未注明原因"}｜来源 {h.source ?? "—"}
                      </div>
                      <div className="sh-handover-side">
                        自 <button className="sh-chain-sid"
                                   onClick={() => h.from && onNavigate(h.from)}>{h.from ?? "—"}</button>
                        ／至 <button className="sh-chain-sid"
                                     onClick={() => h.to && onNavigate(h.to)}>{h.to ?? "—"}</button>
                      </div>
                      {h.card_excerpt != null && (
                        <pre className="sh-card-excerpt">{h.card_excerpt}</pre>)}
                      {h.card_excerpt == null && (
                        <p className="sh-none-line">交接卡摘要不可用
                          {h.card ? "（文件：" + h.card + "）" : "（无卡文件登记）"}。</p>)}
                    </div>
                  ))}
                  {(d.successor_chain ?? []).length > 0 && (
                    <p className="sh-chain">后代链：
                      {d.successor_chain.map((s) => (
                        <button key={s} className="sh-chain-sid"
                                onClick={() => onNavigate(s)}>{s}</button>))}
                    </p>)}
                </>
              )}

              <h4 className="sh-sec">对话与工具摘要（会话库，只读，最近五条文本）</h4>
              {d.dialog_summary?.ok ? (
                <div className="sh-dialog">
                  {d.dialog_summary.texts.length === 0
                    && Object.keys(d.dialog_summary.tool_counts ?? {}).length === 0 ? (
                      <p className="sh-reason-block">会话行在册但无可读文本与工具记录。</p>
                    ) : (
                      <>
                        <ul className="sh-texts">
                          {d.dialog_summary.texts.map((t, n) => (
                            <li key={n}>{t}</li>))}
                        </ul>
                        {Object.keys(d.dialog_summary.tool_counts ?? {}).length > 0 && (
                          <p className="sh-tools">工具调用计数：
                            {Object.entries(d.dialog_summary.tool_counts)
                              .map(([k, v]) => k + "×" + v).join("、")}</p>)}
                      </>
                    )}
                </div>
              ) : (
                <p className="sh-reason-block">对话摘要不可用：
                  {d.dialog_summary?.reason ?? "未提供原因"}</p>
              )}
              <p className="sh-none-line">说明：对话摘要来自本机会话库，事项履历来自账本事件，
                两者分别标注来源；事项事件不冒充完整对话。</p>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

/* ── 历史目录主视图 ── */
export default function SessionHistoryView({ prefilterLane }: {
  prefilterLane: string | null;
}) {
  /* 返修一：挂载即以预筛为初始已应用条件，不发错误首请求。 */
  const initial = prefilterLane ? { ...EMPTY_FILTERS, lane: prefilterLane } : EMPTY_FILTERS;
  const [filters, setFilters] = useState<Filters>(initial);
  const [applied, setApplied] = useState<Filters>(initial);
  const [items, setItems] = useState<HistoryListItem[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [indexBuiltAt, setIndexBuiltAt] = useState<string | null>(null);
  const [stats, setStats] = useState<HistoryListResp["stats"]>();
  const [note, setNote] = useState<string | null>(null);
  const [versionNotice, setVersionNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailSid, setDetailSid] = useState<string | null>(null);
  /* 返修二：按实际页号推进；记录本遍历的索引版本。 */
  const [lastPage, setLastPage] = useState(0);
  const traversalVersion = useRef<string | null>(null);
  const dupFolded = useRef(0);
  const seen = useRef<Set<string>>(new Set());
  /* 返修一：请求代次；旧代次响应不写入任何状态。 */
  const gen = useRef(0);
  const [genTick, setGenTick] = useState(0);   // 触发重置后的首轮重渲染

  useEffect(() => {
    /* 挂载时初始值已含预筛（值未变不重设，避免重复请求）。 */
    if (prefilterLane && filters.lane !== prefilterLane) {
      const f = { ...EMPTY_FILTERS, lane: prefilterLane };
      setFilters(f);
      setApplied(f);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefilterLane]);

  const resetTraversal = useCallback(() => {
    seen.current = new Set();
    dupFolded.current = 0;
    setItems([]);
    setLastPage(0);
  }, []);

  const loadPage = useCallback(async (f: Filters, page: number, append: boolean) => {
    const myGen = ++gen.current;
    setGenTick((t) => t + 1);
    setLoading(true);
    setError(null);
    try {
      const r = await fetch("/api/session_history/sessions?" + qs(f, page));
      const body = await r.json().catch(() => null);
      if (myGen !== gen.current) return;        // 迟到代次：不写任何状态
      if (!body || !body.ok) {
        setError((body && (body.reason ?? body.error))
          ?? "历史查询接口失败（HTTP " + r.status + "）");
        if (!append) { resetTraversal(); setTotal(null); }
        return;
      }
      const res: HistoryListResp = body.result;
      /* 返修二：识别索引换版——本遍历中版本变化即重置并重新开始，
       * 不跨版本拼接，不冒称无遗漏。 */
      const ver = res.index_built_at ?? "";
      if (append && traversalVersion.current != null
          && ver !== traversalVersion.current) {
        setVersionNotice("索引已换版（"
          + fmt(traversalVersion.current) + " → " + fmt(ver)
          + "），已重置并重新开始本版本遍历；跨版本不累计、不宣称无遗漏。");
        traversalVersion.current = ver;
        resetTraversal();
        setTotal(null);
        setIndexBuiltAt(null);
        /* 触发响应属旧遍历的页数据，丢弃；以新版本第 1 页重新开始。 */
        return loadPage(f, 1, false);
      }
      traversalVersion.current = ver;
      setTotal(res.total);
      setIndexBuiltAt(res.index_built_at ?? null);
      setStats(res.stats);
      setNote(res.note ?? null);
      if (!append) resetTraversal();
      const fresh = res.items.filter((it) => !seen.current.has(it.session_id));
      dupFolded.current += res.items.length - fresh.length;
      fresh.forEach((it) => seen.current.add(it.session_id));
      setItems(append ? (prev: HistoryListItem[]) => [...prev, ...fresh] : fresh);
      setLastPage(page);
    } catch (e) {
      if (myGen !== gen.current) return;
      setError("历史查询接口失败：" + String(e));
    } finally {
      if (myGen === gen.current) setLoading(false);
    }
  }, [resetTraversal]);

  useEffect(() => { loadPage(applied, 1, false); }, [applied, loadPage]);
  useEffect(() => { void genTick; }, [genTick]);

  const hasMore = total != null && lastPage > 0
    && lastPage * PAGE_SIZE < total;
  const loaded = items.length;
  const filteredTotal = total;

  return (
    <div className="sh" data-testid="session-history">
      <div className="sh-head">
        <span>会话历史目录</span>
        <span className="sh-readonly">只读查询 · 覆盖近期保留、超时保留、显式归档与交接会话</span>
        <span className="spacer" />
        <span className="sh-counts" data-total={total ?? ""}
              data-loaded={loaded}>
          索引共 {stats?.sessions ?? (total ?? "—")} 会话｜当前筛选 {filteredTotal ?? "—"}
          ｜已加载 {loaded}{indexBuiltAt ? "｜索引构建于 " + fmt(indexBuiltAt) : ""}
        </span>
      </div>
      <div className="sh-filters">
        <label>业务线
          <select value={filters.lane}
                  onChange={(e) => setFilters({ ...filters, lane: e.target.value })}>
            <option value="">全部</option>
            {["L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L10"]
              .map((l) => <option key={l}>{l}</option>)}
          </select>
        </label>
        <label>工位 <input value={filters.worker_id} placeholder="如 L3-w1"
                 onChange={(e) => setFilters({ ...filters, worker_id: e.target.value })} /></label>
        <label>事项 <input value={filters.item_id} placeholder="如 I-217"
                 onChange={(e) => setFilters({ ...filters, item_id: e.target.value })} /></label>
        <label>类型
          <select value={filters.retention}
                  onChange={(e) => setFilters({ ...filters, retention: e.target.value })}>
            <option value="">全部</option>
            <option value="active">近期保留</option>
            <option value="retained_timeout">超时保留</option>
            <option value="explicit_archive">显式归档</option>
            <option value="no_source">来源缺失</option>
          </select>
        </label>
        <label>起 <input type="date" value={filters.date_from}
                 onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} /></label>
        <label>止 <input type="date" value={filters.date_to}
                 onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} /></label>
        <button className="ws-btn primary" onClick={() => setApplied({ ...filters })}
                disabled={loading}>应用筛选</button>
        <button className="ws-btn" onClick={() => {
          const f = prefilterLane ? { ...EMPTY_FILTERS, lane: prefilterLane } : EMPTY_FILTERS;
          setFilters(f);
          setApplied(f);
        }}>重置</button>
      </div>
      {error && <div className="sh-error" role="alert" data-testid="sh-error">{error}</div>}
      {versionNotice && <div className="sh-version-note" role="status"
                              data-testid="sh-version-note">{versionNotice}</div>}
      {note && <p className="sh-none-line">{note}</p>}
      {total === 0 && !error && <p className="sh-none-line">
        当前筛选无匹配会话（索引与筛选条件如上，不编造记录）。</p>}
      <ul className="sh-list" data-testid="sh-list">
        {items.map((it) => (
          <li key={it.session_id} className="sh-row" data-session-id={it.session_id}
              data-retention={it.retention?.kind ?? ""}>
            <button className="sh-sid" title="点击查看只读详情"
                    onClick={() => setDetailSid(it.session_id)}>{it.session_id}</button>
            <span className="sh-lane">{it.lane ?? "—"}</span>
            <RetentionBadge r={it.retention} />
            <span className="sh-workers">{it.workers.join("、") || "无工位"}</span>
            <span className="sh-items-line">{it.items.join("、") || "无事项"}</span>
            <span className="sh-runs">{it.run_count} 运行</span>
            <span className="sh-last">最后活动 {fmt(it.last_seen_at)}</span>
            {(it.successor || it.predecessor) && (
              <span className="sh-chain-mark" title="存在交接链">⇄ 交接链</span>)}
            {Object.keys(it.missing ?? {}).length > 0 && (
              <span className="sh-missing-mark" title={Object.entries(it.missing)
                .map(([k, v]) => k + "：" + v).join("；")}>缺失说明</span>)}
          </li>
        ))}
      </ul>
      <div className="sh-foot">
        {hasMore ? (
          <button className="ws-btn primary" disabled={loading}
                  onClick={() => loadPage(applied, lastPage + 1, true)}>
            加载更多（已加载 {loaded} / 筛选 {filteredTotal}；下一页第 {lastPage + 1} 页）</button>
        ) : (total != null && !error && total > 0 && (
          <span className="sh-none-line">
            本版本已全部加载：{loaded} / {filteredTotal}
            （版本 {fmt(indexBuiltAt)}，按页推进 1–{lastPage}
            {dupFolded.current > 0
              ? "，其中 " + dupFolded.current + " 条跨页重复行已如实折叠记录" : ""}
            ）。</span>))}
        {loading && <span className="sh-none-line">读取中…</span>}
      </div>
      {detailSid && (
        <SessionDetail sid={detailSid}
                       onNavigate={(s) => setDetailSid(s)}
                       onClose={() => setDetailSid(null)} />)}
    </div>
  );
}
