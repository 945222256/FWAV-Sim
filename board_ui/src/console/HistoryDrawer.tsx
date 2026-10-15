/* console/HistoryDrawer.tsx — 会话工作履历抽屉（I-192）。
 * 关联依据：账本 claim 事件 detail 携带的施工会话号（I-174 登记），
 * 不从对话文本猜测承办关系。每个关联事项列出事件时间序列（派发、
 * 认领、释放、阻塞、解除、结载，全部事件按序号正序保留）与实时
 * 云端状态、判据结论；缺失时间标未登记；每行带出处角标（事件行
 * 指针与事项指针，出处抽屉叠加在上层）。 */
import { useEffect, useState } from "react";
import type { SourcePtr } from "./types";
import { STATUS_WORD } from "./types";
import SourceMark from "./SourceMark";

export interface LedgerEvent {
  seq: number;
  ts: string;
  action: string;
  item_id: string | null;
  actor: string | null;
  detail: string | null;
}

interface ItemDetailResp {
  ok: boolean;
  result: { item: { id: string; title: string; status: string; conclusion: string | null } };
}

/* 事件动作的展示词（履历陈列用，与账本动作一一对应）。 */
const ACT_LABEL: Record<string, string> = {
  assign: "派发", claim: "认领", release: "释放", block: "阻塞",
  unblock: "解除阻塞", close: "结载",
};

let eventsCache: { at: number; events: LedgerEvent[] } | null = null;

async function fetchEvents(): Promise<LedgerEvent[]> {
  if (eventsCache && Date.now() - eventsCache.at < 10000) return eventsCache.events;
  const r = await fetch("/api/events");
  if (!r.ok) throw new Error("events http " + r.status);
  const d = await r.json();
  if (!d.ok) throw new Error("events rejected");
  const events: LedgerEvent[] = Array.isArray(d.result) ? d.result : (d.result?.events ?? []);
  eventsCache = { at: Date.now(), events };
  return events;
}

function evPtr(e: LedgerEvent): SourcePtr {
  return {
    kind: "表", locus: "events/" + e.seq,
    excerpt: (e.detail || ACT_LABEL[e.action] || e.action).slice(0, 200),
    sampled_at: e.ts,
  };
}

export default function HistoryDrawer({ sessionId, onClose, onOpenSrc }: {
  sessionId: string;
  onClose: () => void;
  onOpenSrc: (s: SourcePtr) => void;
}) {
  const [events, setEvents] = useState<LedgerEvent[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [details, setDetails] = useState<Record<string, ItemDetailResp["result"]["item"]>>({});

  useEffect(() => {
    let alive = true;
    fetchEvents()
      .then((evs) => { if (alive) setEvents(evs); })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, []);

  /* 关联：本会话的认领事件 → 事项集合（按事项去重，保留全部认领时间）。 */
  const myClaims = (events ?? []).filter(
    (e) => e.action === "claim" && (e.detail || "").includes(sessionId));
  const itemIds = [...new Set(myClaims.map((e) => e.item_id).filter((x): x is string => !!x))];

  useEffect(() => {
    let alive = true;
    itemIds.forEach((id) => {
      if (details[id]) return;
      fetch("/api/item-detail?id=" + encodeURIComponent(id))
        .then((r) => r.json())
        .then((d: ItemDetailResp) => {
          if (alive && d.ok) setDetails((cur) => ({ ...cur, [id]: d.result.item }));
        })
        .catch(() => { /* 单项失败保留未登记态 */ });
    });
    return () => { alive = false; };
  }, [itemIds.join(",")]);   // eslint-disable-line react-hooks/exhaustive-deps

  const itemPtr = (id: string): SourcePtr => ({
    kind: "表", locus: "items/" + id,
    excerpt: (details[id]?.title || id).slice(0, 200),
    sampled_at: new Date().toISOString().slice(0, 19),
  });

  return (
    <div className="src-mask" style={{ zIndex: 30 }} onClick={onClose}>
      <aside className="src-drawer hist-drawer" onClick={(e) => e.stopPropagation()}
             aria-label="会话工作履历">
        <header className="src-drawer-head">
          <div>
            <div className="src-drawer-kind">会话工作履历</div>
            <div className="src-drawer-locus hist-full-sid">{sessionId}</div>
          </div>
          <button className="lab-btn-minor" onClick={onClose}>关闭</button>
        </header>
        <div className="src-drawer-body">
          <p className="hist-scope">
            关联依据：账本认领事件携带的本会话号（I-174 登记）。事件时间取账本
            事件行；事项状态与结论实时读云端账本；未登记即账本无该事件。
          </p>
          {failed && <p className="hist-missing">账本事件接口不可达，履历暂不可用。</p>}
          {!events && !failed && <p className="hist-missing">账本事件读取中…</p>}
          {events && itemIds.length === 0 && (
            <p className="hist-missing">
              未登记本会话的认领事件（该会话可能未走过带会话号自报的认领通道），
              暂无可陈列的承办历史。
            </p>
          )}
          {itemIds.map((id) => {
            const itemEvents = (events ?? [])
              .filter((e) => e.item_id === id && ACT_LABEL[e.action])
              .sort((a, b) => a.seq - b.seq);
            const detail = details[id];
            return (
              <section className="hist-item" key={id}>
                <header className="hist-item-head">
                  <span className="ov-id">{id}</span>
                  <span className="hist-item-title">{detail?.title ?? "读取中…"}</span>
                  <SourceMark source={itemPtr(id)} onOpen={onOpenSrc} />
                </header>
                <div className="hist-item-status">
                  <span className={"ov-chip" + (detail?.status === "closed" ? " ok" : "")}>
                    {detail ? (STATUS_WORD[detail.status] ?? detail.status) : "…"}
                  </span>
                  {detail?.conclusion && (
                    <span className="hist-conclusion">结论：{detail.conclusion}</span>
                  )}
                  {!detail && <span className="hist-missing-inline">账本读取中</span>}
                </div>
                <ul className="hist-events">
                  {itemEvents.map((e) => (
                    <li key={e.seq}>
                      <span className="hist-ev-act">{ACT_LABEL[e.action]}</span>
                      <span className="hist-ev-ts">{e.ts.replace("T", " ")}</span>
                      <span className="hist-ev-actor">{e.actor ?? "—"}</span>
                      <SourceMark source={evPtr(e)} onOpen={onOpenSrc} />
                    </li>
                  ))}
                  {itemEvents.length === 0 && (
                    <li className="hist-missing-inline">该事项无派发、认领、释放、阻塞或结载事件记录（未登记）。</li>
                  )}
                </ul>
              </section>
            );
          })}
        </div>
      </aside>
    </div>
  );
}
