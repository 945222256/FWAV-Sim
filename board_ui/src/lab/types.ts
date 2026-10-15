/* lab/types.ts — 参数实验室只读接口的载荷类型（I-136 第二版）。 */

export interface LabGraph {
  level?: number;
  authority?: string;
  valid_domain?: string;
  downstream?: string[];
  status?: string;
}

export interface LabTeaching {
  definition: string;
  affects: string[];
  affects_note: string;
  increase: string;
  history: unknown[] | string;
}

/** 公式符号：target_key 与 target_family 二选一，均空为非登记量。 */
export interface LabSymbol {
  sym: string;
  name: string;
  target_key?: string;
  target_family?: string;
}

export interface LabFormula {
  id: string;
  name: string;
  text: string;
  note: string;
  symbols: LabSymbol[];
  source: string;
}

/** 约束该参数的公式（键级反查结果）。 */
export interface LabFormulaRef {
  id: string;
  name: string;
  text: string;
}

/** 下游链条的一跳：键名加后续跳。 */
export interface LabChainHop {
  key: string;
  via: LabChainHop[];
}

export type LabFacetAxis = "domain" | "component" | "channel" | "role" | "method" | "stage";
export type LabUsageTag = "current" | "historical" | "reserved" | "undetermined";
export type LabReviewSlot = "name" | "definition" | "usage";
export type LabReviewState = "unreviewed" | "verified" | "declaration_only" | "pending" | "disputed" | "stale";

/** 单一规范语义对象；扩展和未知版本只读保留，不从旧 sample 推导适用范围。 */
export interface LabSemantics {
  [extension: string]: unknown;
  schema_version: number;
  display_name?: string | null;
  definition?: string | null;
  usage_tag?: string;
  consumer_state?: string;
  review_state?: Partial<Record<LabReviewSlot, string>>;
  facets?: Partial<Record<LabFacetAxis, string[]>>;
  applicability?: {
    [extension: string]: unknown;
    sample_scope?: string;
    sample_ids?: string[];
    modes?: string[];
    conditions?: string[];
    default_path?: string;
    runtime_evidence_refs?: unknown[];
  };
  alias_of?: string | null;
  superseded_by?: string[];
  source_refs?: unknown[];
  consumer_refs?: unknown[];
  sort_order?: number | null;
  review?: {
    [extension: string]: unknown;
    reviewed_by?: string | null;
    reviewed_at?: string | null;
    reason_codes?: string[];
    evidence_refs?: unknown[];
    search_scope?: unknown;
    next_owner?: string | null;
    limitations?: string[];
  };
}

export interface LabParam {
  key: string;
  short_name: string;
  family: string;
  formulas: LabFormulaRef[];
  chain: LabChainHop[];
  value: string | null;
  unit: string | null;
  lifecycle: string | null;
  source: string | null;
  since: string | null;
  note: string | null;
  graph: LabGraph | null;
  sample: string;
  level: number | null;
  level_source: "graph" | "rule" | "none";
  level_rule: string;
  downstream_count: number;
  explanation: string;
  teaching: LabTeaching;
  semantics?: LabSemantics | null;
  semantics_diagnostics?: unknown;
}

export interface LabDomain {
  id: string;
  no: number;
  name: string;
  lead: string;
  count: number;
  groups?: { name: string; families: string[] }[];
}

export interface LabFamily {
  id: string;
  name: string;
  domain: string;
  lead: string;
  source: string;
  keys: string[];
  derived: boolean;
}

export interface LabSample {
  id: string;
  name: string;
  state: string;
  tagline: string;
  locked: boolean;
  hint?: string;
}

export interface LabPlaceholder {
  level: number;
  subclass: string;
  family: string;
  text: string;
  status: string;
}

export interface LabLoopGroup {
  id: string;
  name: string;
  keys: string[];
}

export interface LabPayload {
  schema: string;
  metadata_schema?: string;
  generated_at: string;
  samples: LabSample[];
  eaglex700?: import("./types").EagleNode;
  domains: LabDomain[];
  families: LabFamily[];
  formulas: LabFormula[];
  level_names: Record<string, string>;
  placeholders: LabPlaceholder[];
  loop_groups: LabLoopGroup[];
  params: LabParam[];
  stats: {
    total: number;
    graph: number;
    rule: number;
    none: number;
    families: number;
    domains: number;
    orphan: number;
  };
}

export interface LabManifest {
  schema_version: number;
  model_name: string;
  config: string;
  source_file: string;
  source_md5: string;
  source_git: string;
  exported_at: string;
  renderer: string;
  frames: number;
  fps: number;
  duration_s: number;
  view: string;
  frame_files: string[];
  glb: string;
  frames_bytes: number;
}

/* I-202：七百克候选动作预览数据节（/api/lab/params 的 eaglex700 键）。 */
export interface EagleSegment {
  id: string;
  label: string;
  time_s?: [number, number];
  frames?: number;
  asset?: string;
}

export interface EagleNode {
  available: boolean;
  reason?: string;
  source: { dir: string; geo_sha12: string; chord_sha12: string;
            date: string; asset: string };
  provenance?: { path: string; sha256: string }[];
  limitations?: string[];
  verification_note: string;
  sample_disclaimer: string;
  candidate_status: { mass: string; inertia: string; aero: string; flight: string };
  physics_frozen: string;
  motion: { segments: EagleSegment[]; unit: string; channels: string[];
             mechanism_note?: string };
  animv2?: import("./types").AnimV2Node;
  geometry: {
    parts: { part: string; dimensions_m: number[] | null;
             surface_area_m2: number | null;
             projected_planform_area_m2: number | null;
             spanwise_extent_m: number | null; triangles: number | null }[];
    chord_stations: { unit_note: string; rows: number; columns: string[];
                      data: Record<string, string>[] };
  };
  bindings: { joint: string; parent: string; child: string;
              origin_xyz_m: number[]; axis: number[];
              range_rad: [number, number] }[];
  preview?: { duration_s?: number; segments?: string[]; mode?: string };
}

/* I-202 复工：动画第二版视频节（机主 2026-10-13 认可尾部构型方向）。 */
export interface AnimV2Segment {
  id: string;
  label: string;
  clip: string;
  in_commands?: boolean;
  asset?: string;
  poster?: string | null;
}

export interface AnimV2Node {
  available: boolean;
  reason?: string;
  missing_clips?: string[];
  source: { dir: string; report_sha12: string; version_name: string;
            date: string; simulator_used?: boolean };
  supersedes_note?: string;
  owner_ack?: string;
  stage_note?: string;
  segments: AnimV2Segment[];
}
