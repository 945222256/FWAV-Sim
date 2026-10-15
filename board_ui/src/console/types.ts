/* console/types.ts — 控制台状态接口（/api/console/state，I-142）载荷类型。 */

export interface SourcePtr {
  kind: string;
  locus: string;
  excerpt: string;
  sampled_at: string;
}

export interface ItemRef {
  id: string;
  title: string;
  status: string;
  source: SourcePtr;
}

export interface RequirementNode {
  id: string;
  title: string;
  status: string;
  enables: ItemRef[];
  source: SourcePtr;
}

export interface GoalNode {
  id: string;
  text: string;
  status: string;
  requirements: RequirementNode[];
  source: SourcePtr;
}

export interface LaneCard {
  id: string;
  title: string;
  status: string;
  progress: { verified: number; total: number } | null;
  lease_until: string | null;
  source: SourcePtr;
}

export interface LaneNode {
  id: string;
  name: string;
  items: LaneCard[];
  active_sessions: string[];
  source: SourcePtr;
}

export interface SessionCard {
  session_id: string;
  lane: string | null;
  item_id: string | null;
  /* I-207：当前事项以认领事件核实（服务端修正下发）；item_id 为
   * 采集侧派发登记快照，两者不一致时为旧派发冲突。 */
  current_item_id?: string | null;
  status: string | null;
  context_pct: number | null;
  last_activity_at: string | null;
  updated_at: string;
  source: SourcePtr;
}

/* I-218 v2：waiting 的统一消费形态。冻结契约 lane-workers-v1 中
 * waiting 为对象（request_id → 等待原因）；早期预备接口曾为数组
 * （元素含 item_id/reason）。前端两种都收：对象按契约、数组按旧
 * 预备形态，消费侧统一折算为下列条目，不编造 request_id。 */
export interface WaitingEntry {
  request_id: string | null;
  item_id: string | null;
  title?: string | null;
  reason: string | null;
}

export function waitingEntries(q: {
  waiting?: unknown;
  queued_items?: unknown;
}): WaitingEntry[] {
  const out: WaitingEntry[] = [];
  if (Array.isArray(q.queued_items)) {
    for (const it of q.queued_items as { request_id?: string; item_id?: string;
      title?: string; reason?: string | null }[]) {
      if (it && it.reason != null) {
        out.push({ request_id: it.request_id ?? null, item_id: it.item_id ?? null,
                   title: it.title ?? null, reason: it.reason ?? null });
      }
    }
    return out;
  }
  const w = q.waiting;
  if (w && typeof w === "object" && !Array.isArray(w)) {
    for (const [rid, reason] of Object.entries(w as Record<string, string | null>)) {
      out.push({ request_id: rid, item_id: null, reason: reason ?? null });
    }
  } else if (Array.isArray(w)) {
    for (const it of w as { item_id?: string; reason?: string | null }[]) {
      if (it) out.push({ request_id: null, item_id: it.item_id ?? null,
                         reason: it.reason ?? null });
    }
  }
  return out;
}

export interface ConsoleState {
  /* I-218 v2 多工位：按冻结契约 lp_sync/lane_workers_contract_v1.json
   * （lane-workers-v1）。workers 为活跃工位数组（worker_id、session_id、
   * item_id、run_id、phase、request_id）；waiting 为对象
   * （request_id → 等待原因），queued_items 为排队明细数组
   * （request_id、item_id、title、phase、reason）；waiting 的旧预备
   * 数组形态仅在过渡期兼容（waitingEntries 统一折算）；
   * current_item_id 与 current 为旧形态兼容字段，多工位下语义以
   * workers 为准（仅代表最早派发工位）。 */
  lane_queues?: { sampled_at: string | null; queues: Record<string, {
    concurrency_limit?: number;
    workers?: { worker_id?: string; session_id?: string | null; item_id?: string | null;
                run_id?: string | null; phase?: string | null; request_id?: string }[];
    waiting_count: number; current_item_id?: string | null;
    queued_items?: { request_id?: string; item_id?: string; title?: string;
                     phase?: string; reason?: string | null }[];
    waiting?: Record<string, string | null> | { item_id?: string; reason?: string | null }[];
    active_count?: number; order?: string[]; current?: string | null; conflict?: string[];
  }> };
  goals: GoalNode[];
  lanes: LaneNode[];
  sessions: { active: SessionCard[]; retained: SessionCard[];
               archived: SessionCard[]; owner: SessionCard[] };
  footer: {
    parallel: { active: number; limit: number };
    sampled_at: string;
    source: SourcePtr;
  };
}

export const STATUS_WORD: Record<string, string> = {
  open: "未认领",
  in_progress: "进行中",
  blocked: "卡住",
  pending_review: "已交付，待验收",
  closed: "完成",
  active: "生效",
};

/* ── I-228 会话历史目录（上游 I-227 session_history 只读查询服务） ──
 * 接口：GET /api/session_history/sessions（筛选分页）与
 * /api/session_history/sessions/<会话号>（详情含交接链与对话摘要）。
 * 全部只读；字段形态取自 lp_sync/session_history.py 的
 * list_sessions/get_session 输出。 */
export type RetentionKind = "active" | "retained_timeout"
  | "explicit_archive" | "no_source";

/* I-228 v2 返修三：active 的索引语义是"未超时保留"（最近活动未超过
 * 三十五分钟且未显式归档），不代表仍在执行；标签改为近期保留，
 * 执行状态另按 execution_phase 相位呈现。 */
export const RETENTION_LABEL: Record<string, string> = {
  active: "近期保留",
  retained_timeout: "超时保留",
  explicit_archive: "显式归档",
  no_source: "来源缺失",
};

export interface HistoryListItem {
  session_id: string;
  lane: string | null;
  workers: string[];
  items: string[];
  run_count: number;
  successor: string | null;
  predecessor: string | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  retention: { kind?: string; reason?: string; at?: string } | null;
  sources: string[] | null;
  missing: Record<string, string>;
}

export interface HistoryListResp {
  total: number;
  page: number;
  page_size: number;
  items: HistoryListItem[];
  index_built_at: string | null;
  stats?: { sessions?: number; retention?: Record<string, number>;
            by_lane?: Record<string, number> };
  note?: string;
}

export interface HistoryDetail {
  ok: boolean;
  reason?: string;
  session_id: string;
  lane: string | null;
  title_head: string | null;
  workers: Record<string, unknown>;
  items: Record<string, { first?: string | null; last?: string | null;
                          sources?: string[]; claimed_at?: string | null }>;
  runs: { run_id?: string; item_id?: string; worker_id?: string | null;
          started_at?: string; status?: string }[];
  handover_cards: { at?: string; from?: string; to?: string; reason?: string;
                    card?: string | null; card_excerpt?: string | null;
                    source?: string }[];
  predecessor_chain: string[];
  successor_chain: string[];
  first_seen_at: string | null;
  last_seen_at: string | null;
  retention: { kind?: string; reason?: string; at?: string } | null;
  execution_phase: string | null;
  sources: string[] | null;
  missing: Record<string, string>;
  read_only: boolean;
  dialog_summary: { ok: boolean; texts: string[]; tool_counts: Record<string, number>;
                    reason: string | null };
}
