/* console/pending_work_hooks.ts — I-234 待领工作区数据钩子。
 * 快照：GET /api/console/pending_work（每十秒；过期/降级/失败如实标注，
 * 失败不归零）。事件：GET /api/console/pending_work/events?after_cursor=N
 * （每六秒增量）。
 * 分阶段动效纪律（判据四）：首次装载只建立游标基线（历史事件保留为
 * 记录、不回放）；基线之后到达的事件按 释放→选择→认领确认→实际运行
 * 推进阶段标记，同 event_id 去重；普通刷新重建基线故不重放；断线恢复
 * 沿游标续读，重复段不重复播放。 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { PwEvent, PwSnapshot } from "./pending_work_types";

const SNAPSHOT_MS = 10_000;
const EVENTS_MS = 6_000;

export interface PwStageMark {
  item_id: string | null;
  session_id: string | null;
  worker_id: string | null;
  type: string;
  at: string;
  event_id: string;
}

export interface PendingWorkState {
  snapshot: PwSnapshot | null;
  snapshotError: string | null;
  snapshotStale: boolean;          // 采样过期：按待核对处理
  eventsError: string | null;
  recentStages: PwStageMark[];     // 基线后已核实的分阶段标记（按到达序）
  replayCount: number;             // 被去重拦截的重复事件数（核对用）
  reconnects: number;
}

export function usePendingWork(): PendingWorkState {
  const [snapshot, setSnapshot] = useState<PwSnapshot | null>(null);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);
  const [snapshotStale, setSnapshotStale] = useState(false);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [recentStages, setRecentStages] = useState<PwStageMark[]>([]);
  const [replayCount, setReplayCount] = useState(0);
  const [reconnects, setReconnects] = useState(0);
  const cursor = useRef<number | null>(null);        // null=未建基线
  const seen = useRef<Set<string>>(new Set());
  const wasOnline = useRef(true);

  const pullSnapshot = useCallback(async () => {
    try {
      const r = await fetch("/api/console/pending_work");
      const d = await r.json().catch(() => null);
      if (d && d.ok) {
        setSnapshot(d.result as PwSnapshot);
        setSnapshotError(null);
        const exp = d.result?.sample_validity?.expires_at;
        setSnapshotStale(!!exp && new Date(exp).getTime() < Date.now());
      } else {
        setSnapshotError((d && (d.error ?? d.reason))
          ?? "待领快照接口失败（HTTP " + r.status + "）");
      }
    } catch (e) {
      setSnapshotError("待领快照接口失败：" + String(e));
    }
  }, []);

  const pullEvents = useCallback(async () => {
    try {
      const after = cursor.current != null ? "?after_cursor=" + cursor.current : "";
      const r = await fetch("/api/console/pending_work/events" + after);
      const d = await r.json().catch(() => null);
      if (!d || !d.ok) {
        setEventsError((d && (d.error ?? d.reason))
          ?? "调度事件接口失败（HTTP " + r.status + "）");
        return;
      }
      setEventsError(null);
      const evs: PwEvent[] = d.result?.events ?? [];
      let maxCursor = cursor.current ?? d.result?.cursor ?? 0;
      const fresh: PwStageMark[] = [];
      let dup = 0;
      for (const ev of evs) {
        if (ev.cursor != null && ev.cursor > (maxCursor ?? 0)) maxCursor = ev.cursor;
        if (seen.current.has(ev.event_id)) { dup++; continue; }
        seen.current.add(ev.event_id);
        if (cursor.current == null) continue;        // 首拉：只建基线不回放
        fresh.push({ item_id: ev.identity?.item_id ?? null,
                     session_id: ev.identity?.session_id ?? null,
                     worker_id: ev.identity?.worker_id ?? null,
                     type: ev.type, at: ev.occurred_at, event_id: ev.event_id });
      }
      if (!wasOnline.current && eventsErrorRef.current) {
        setReconnects((n) => n + 1);                 // 断线恢复：沿游标续读
      }
      wasOnline.current = true;
      if (dup) setReplayCount((n) => n + dup);
      if (fresh.length) {
        setRecentStages((prev) => [...prev, ...fresh].slice(-40));
      }
      cursor.current = maxCursor;
    } catch (e) {
      wasOnline.current = false;
      setEventsError("调度事件接口失败：" + String(e));
    }
  }, []);

  // eventsError 经 ref 供恢复计数判断（避免闭包旧值）
  const eventsErrorRef = useRef<string | null>(null);
  useEffect(() => { eventsErrorRef.current = eventsError; }, [eventsError]);

  useEffect(() => {
    pullSnapshot();
    pullEvents();
    const t1 = setInterval(pullSnapshot, SNAPSHOT_MS);
    const t2 = setInterval(pullEvents, EVENTS_MS);
    return () => { clearInterval(t1); clearInterval(t2); };
  }, [pullSnapshot, pullEvents]);

  return { snapshot, snapshotError, snapshotStale, eventsError,
           recentStages, replayCount, reconnects };
}

/* 阶段推导：同一事项按已核实事件取最新阶段（释放→选择→认领→运行）。 */
const STAGE_ORDER: Record<string, number> = {
  slot_released: 1, dispatch_selected: 2, claim_confirmed: 3, run_started: 4,
};
export function latestStageByItem(stages: PwStageMark[]): Map<string, PwStageMark> {
  const m = new Map<string, PwStageMark>();
  for (const s of stages) {
    if (!s.item_id) continue;
    const prev = m.get(s.item_id);
    if (!prev || (STAGE_ORDER[s.type] ?? 0) >= (STAGE_ORDER[prev.type] ?? 0)) {
      m.set(s.item_id, s);
    }
  }
  return m;
}
