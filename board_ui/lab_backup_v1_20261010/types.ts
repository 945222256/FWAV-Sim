/* lab/types.ts — 参数实验室只读接口的载荷类型（I-130 阶段一）。 */

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

export interface LabParam {
  key: string;
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
}

export interface LabZone {
  id: string;
  no: number;
  title: string;
  levels: number[];
  layout: "core" | "grid";
  lead: string;
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
  text: string;
  status: string;
}

export interface LabLoopGroup {
  id: string;
  name: string;
  keys: string[];
}

export interface LabPayload {
  generated_at: string;
  samples: LabSample[];
  zones: LabZone[];
  level_names: Record<string, string>;
  placeholders: LabPlaceholder[];
  empty_level_notes: Record<string, string>;
  loop_groups: LabLoopGroup[];
  params: LabParam[];
  stats: {
    total: number;
    graph: number;
    rule: number;
    none: number;
    by_level: Record<string, number>;
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
