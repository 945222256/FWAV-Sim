/* console/pending_work_types.ts — I-234 待领工作区（消费冻结契约
 * pending-work-v1，后端 I-233 交付；字段语义见
 * organization/contracts/pending_work_v1/README.md，不解析自由文字、
 * 不用页面额度与活跃会话数推算容量）。 */

export type PwCategory = "ready" | "waiting_conditions"
  | "awaiting_orchestration" | "needs_verification";

export const PW_CATEGORY_LABEL: Record<string, string> = {
  ready: "就绪",
  waiting_conditions: "等条件",
  awaiting_orchestration: "待编排",
  needs_verification: "待核对",
};

export interface PwReason {
  code?: string;
  category?: string;
  sentence?: string;
  related?: { item_id?: string; status?: string; title?: string }[] | null;
  recovery?: string | null;
  provenance?: string | null;
}

export interface PwItem {
  item_id: string;
  title: string | null;
  kind: string | null;
  status: string | null;
  lane: string | null;
  goal: string | null;
  reviewer: string | null;
  owner: string | null;
  category: PwCategory | string;
  primary_reason?: PwReason | null;
  related?: { dependencies?: { item_id?: string; status?: string;
    title?: string; delivery_impact?: unknown }[] } | null;
  recovery?: string | null;
  evaluation?: { at?: string; validity?: string; evaluator?: string;
                 provenance?: string } | null;
  enqueue?: { in_kernel_queue?: boolean; request_ids?: string[];
              first_enqueued_at?: string | null;
              total_dispatch_attempts?: number } | null;
  execution_ref?: { worker_id?: string | null; session_id?: string | null;
                    run_id?: string | null; dispatch_request_id?: string | null;
                    kernel_phase?: string | null } | null;
}

export interface PwSnapshot {
  contract_version?: string;
  snapshot_id: string;
  sampled_at: string;
  sample_validity?: { max_age_s?: number; expires_at?: string };
  source_versions?: Record<string, unknown>;
  completeness?: { status?: string; reasons?: { source?: string;
    severity?: string; reason?: string }[] };
  summary: {
    total_pending: number;
    by_category?: Record<string, number>;
    exclusions?: Record<string, { count?: number; note?: string }>;
    needs_verification_count?: number;
    by_lane?: Record<string, { count: number; items: string[] } | null>;
    unassigned?: { count: number; items?: string[]; note?: string };
  };
  items: PwItem[];
  needs_verification?: { item_id?: string; code?: string;
                         sentence?: string; provenance?: string }[];
  capacity: {
    status?: string;
    global?: { effective_limit?: number; occupied?: number;
               dispatchable?: number; reserved?: number | null;
               policy_source?: string };
    by_lane?: Record<string, { effective_limit?: number; occupied?: number;
                               dispatchable?: number; reserved?: number | null;
                               policy_source?: string }>;
    reservation_policy?: { status?: string };
    separate_measures_note?: string;
    config_read_error?: string | null;
  };
  read_only?: boolean;
}

export type PwEventType = "slot_released" | "dispatch_selected"
  | "claim_confirmed" | "run_started" | "condition_wait" | "recovery"
  | "result_unknown";

export const PW_EVENT_LABEL: Record<string, string> = {
  slot_released: "工位释放",
  dispatch_selected: "调度选择",
  claim_confirmed: "认领确认",
  run_started: "实际运行",
  condition_wait: "条件等待",
  recovery: "恢复",
  result_unknown: "结果未知",
};

export interface PwEvent {
  event_id: string;
  type: PwEventType | string;
  source_kind?: string;
  source_ref?: Record<string, unknown>;
  occurred_at: string;
  identity?: { item_id?: string | null; lane?: string | null;
               worker_id?: string | null; session_id?: string | null;
               run_id?: string | null; request_id?: string | null };
  state_change?: Record<string, unknown> | null;
  fact_source?: string;
  causes?: unknown[];
  triggers?: unknown[];
  cursor?: number;
}
