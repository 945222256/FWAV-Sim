/* types.ts — 全景看板数据形状，与服务端 /api/map、/api/item-detail 对齐。 */

export interface Goal {
  id: string;
  text: string;
  status: string;
}

export interface LineStat {
  id: string;
  name: string;
  mission: string;
  total: number;
  closed: number;
  progress: number | null;
  reason: string | null;
}

export interface MapNode {
  id: string;
  title: string;
  status: string;
  status_word: string;
  lane: string;
  owner: string | null;
  line: string;
  rationale: string;
  check_script: string;
  check_result: { pass: boolean; exit_code: number; ran_at: string;
                   script: string; output_tail?: string } | null;
  claimed_by: string | null;
  verified: number | null;
  criteria_total: number | null;
  depth: number;
}

export interface MapEdge {
  from: string;
  to: string;
  kind: "dep" | "enables";
  open: boolean;
}

export interface BoardMap {
  generated_at: string;
  goals: Goal[];
  lines: LineStat[];
  nodes: MapNode[];
  edges: MapEdge[];
  critical_path: string[];
  counts: {
    total: number;
    open: number;
    in_progress: number;
    blocked: number;
    closed: number;
    owner_pending: number;
  };
}

export interface Reply {
  ts: string;
  actor: string;
  text: string;
}

export interface ItemDetail {
  item: {
    id: string;
    title: string;
    status: string;
    opened_at: string;
    closed_at: string | null;
    closed_by: string | null;
    owner: string | null;
    lane: string | null;
    claimed_by: string | null;
    close_criterion: string | null;
    note: string | null;
    evidence: string[];
    criteria: { text: string; verified: boolean; evidence: string;
                verified_by: string; verified_at: string }[] | null;
    replies: Reply[];
    conclusion: string | null;
    criterion_check: { "判据": string; "结果": string; "数值出处": string }[] | null;
    rationale: string | null;
    line: string | null;
    check_script: string | null;
    check_result: { pass: boolean; exit_code: number; ran_at: string;
                     script: string; output_tail?: string } | null;
  };
  events: { ts: string; action: string; item_id: string; actor: string; detail: string }[];
}
