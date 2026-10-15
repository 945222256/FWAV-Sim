/* console/OverviewView.tsx — 总览页三层陈列（I-168）。
 * 第一层许愿池陈列愿景条目（/api/console/state goals，I-165 目标镜像）；
 * 第二层战略蓝图陈列应然块（/api/blueprints，I-167 蓝图镜像）；
 * 第三层指挥部陈列 HQ 与业务线述职卡。业务线存在理由与 HQ 卡三节
 * （自我认知、压缩记忆、曾用现用对话）来自生成物 overview_data.ts
 * （构建前从 lanes.json 与交接卡现读生成，逐字摘录带出处指针）。
 * 全部卡片可点击展开，层间关联可互跳，每格带出处角标。 */
import { useEffect, useState } from "react";
import type { ConsoleState, GoalNode, SourcePtr } from "./types";
import { STATUS_WORD } from "./types";
import SourceMark from "./SourceMark";
import { LANE_MISSIONS, HQ_CARD } from "./overview_data";

interface RawBpSource {
  kind: string;
  locus?: string;
  locator?: string;   // I-167 接口用 locator 字段名，与 I-142 冻结的 locus 形态不一致，
                      // 前端归一兼容（缺陷已申报待审队列，服务端修正前双字段取值）。
  excerpt: string;
  sampled_at: string;
}

interface RawBlueprintBlock {
  id: string;
  name: string;
  description: string;
  vision: string[];
  lanes: string[];
  approved_at: string | null;
  approved_by: string | null;
  note: string | null;
  source: RawBpSource;
}

interface BlueprintResp {
  ok: boolean;
  result: { count: number; blocks: RawBlueprintBlock[] };
}

interface BlueprintBlock extends Omit<RawBlueprintBlock, "source"> {
  source: SourcePtr;
}

export default function OverviewView({ state, onOpenSrc }: {
  state: ConsoleState;
  onOpenSrc: (s: SourcePtr) => void;
}) {
  const [blocks, setBlocks] = useState<BlueprintBlock[] | null>(null);
  const [bpFailed, setBpFailed] = useState(false);
  const [openGoal, setOpenGoal] = useState<string | null>(null);
  const [openBlock, setOpenBlock] = useState<string | null>(null);
  const [openLane, setOpenLane] = useState<string | null>(null);
  const [hqOpen, setHqOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    fetch("/api/blueprints")
      .then((r) => r.json())
      .then((d: BlueprintResp) => {
        if (alive && d.ok) {
          setBlocks(d.result.blocks.map((b) => ({
            ...b,
            source: {
              kind: b.source?.kind ?? "",
              locus: b.source?.locus ?? b.source?.locator ?? "",
              excerpt: b.source?.excerpt ?? "",
              sampled_at: b.source?.sampled_at ?? "",
            } as SourcePtr,
          })));
        }
      })
      .catch(() => { if (alive) setBpFailed(true); });
    return () => { alive = false; };
  }, []);

  const goalText = (gid: string) =>
    state.goals.find((g) => g.id === gid)?.text ?? "";
  const laneName = (lid: string) =>
    state.lanes.find((l) => l.id === lid)?.name ?? lid;
  const blocksForGoal = (gid: string) =>
    (blocks ?? []).filter((b) => b.vision.includes(gid));
  const blocksForLane = (lid: string) =>
    (blocks ?? []).filter((b) => b.lanes.includes(lid));

  /* 层间互跳：展开目标卡并滚入视口（点击行为全部可见，无隐藏态）。 */
  const jump = (kind: "goal" | "block" | "lane" | "hq", id: string) => {
    if (kind === "goal") setOpenGoal(id);
    else if (kind === "block") setOpenBlock(id);
    else if (kind === "lane") setOpenLane(id);
    else setHqOpen(true);
    requestAnimationFrame(() => {
      document
        .getElementById("ov-" + (kind === "hq" ? "hq" : kind) + "-" + id)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  };

  return (
    <div className="ov">
      <section className="ov-layer" aria-label="许愿池">
        <header className="ov-layer-head">
          <h2 className="ov-layer-title">许愿池</h2>
          <span className="ov-layer-sub">愿景条目 {state.goals.length}｜目标库实时镜像</span>
        </header>
        {state.goals.map((g) => (
          <WishCard key={g.id} goal={g} open={openGoal === g.id}
                    blockCount={blocksForGoal(g.id).length}
                    blocks={blocksForGoal(g.id)}
                    onToggle={() => setOpenGoal(openGoal === g.id ? null : g.id)}
                    onJumpBlock={(bid) => jump("block", bid)}
                    onOpenSrc={onOpenSrc} />
        ))}
      </section>

      <section className="ov-layer" aria-label="战略蓝图">
        <header className="ov-layer-head">
          <h2 className="ov-layer-title">战略蓝图</h2>
          <span className="ov-layer-sub">
            应然块 {blocks?.length ?? "…"}｜核定稿镜像{bpFailed ? "（通道不可达）" : ""}
          </span>
        </header>
        {!blocks && !bpFailed && <p className="ov-empty">蓝图数据加载中…</p>}
        {bpFailed && <p className="ov-empty">蓝图接口不可达，本层暂无数据。</p>}
        {(blocks ?? []).map((b) => (
          <BlockCard key={b.id} block={b} open={openBlock === b.id}
                     onToggle={() => setOpenBlock(openBlock === b.id ? null : b.id)}
                     goalText={goalText} laneName={laneName}
                     onJumpGoal={(gid) => jump("goal", gid)}
                     onJumpLane={(lid) => jump("lane", lid)}
                     onOpenSrc={onOpenSrc} />
        ))}
      </section>

      <section className="ov-layer" aria-label="指挥部">
        <header className="ov-layer-head">
          <h2 className="ov-layer-title">指挥部</h2>
          <span className="ov-layer-sub">HQ 与业务线述职卡 {1 + state.lanes.length} 张</span>
        </header>
        <HqCard open={hqOpen} onToggle={() => setHqOpen(!hqOpen)}
                openCount={state.lanes.find((l) => l.id === "HQ")?.items.length ?? 0}
                onOpenSrc={onOpenSrc} />
        {state.lanes.filter((l) => l.id !== "HQ").map((l) => (
          <LaneReportCard key={l.id} lane={l} open={openLane === l.id}
                          mission={LANE_MISSIONS[l.id]}
                          blocks={blocksForLane(l.id)}
                          onToggle={() => setOpenLane(openLane === l.id ? null : l.id)}
                          onJumpBlock={(bid) => jump("block", bid)}
                          onOpenSrc={onOpenSrc} />
        ))}
      </section>
    </div>
  );
}

/* ── 第一层：许愿池愿景条目 ─────────────────────────────────────── */
function WishCard({ goal, open, blockCount, blocks, onToggle, onJumpBlock, onOpenSrc }: {
  goal: GoalNode;
  open: boolean;
  blockCount: number;
  blocks: BlueprintBlock[];
  onToggle: () => void;
  onJumpBlock: (bid: string) => void;
  onOpenSrc: (s: SourcePtr) => void;
}) {
  const done = goal.requirements.filter(
    (r) => r.enables.length > 0 && r.enables.every((e) => e.status === "closed")).length;
  return (
    <article className={"ov-card" + (open ? " open" : "")} id={"ov-goal-" + goal.id}>
      <button className="ov-card-head" onClick={onToggle} aria-expanded={open}>
        <span className="ov-id">{goal.id}</span>
        <span className="ov-title">
          {goal.text.length > 44 ? goal.text.slice(0, 44) + "…" : goal.text}
        </span>
        <span className={"ov-chip" + (goal.status === "active" ? " ok" : "")}>
          {STATUS_WORD[goal.status] ?? goal.status}
        </span>
        <span className="ov-meta">蓝图块 {blockCount}</span>
        {goal.requirements.length > 0 && (
          <span className="ov-meta">需求 {done}/{goal.requirements.length}</span>
        )}
        <SourceMark source={goal.source} onOpen={onOpenSrc} />
      </button>
      {open && (
        <div className="ov-detail">
          <p className="ov-fulltext">{goal.text}</p>
          {goal.requirements.length > 0 && (
            <>
              <h4 className="ov-subhead">挂接需求（目标库镜像）</h4>
              <ul className="ov-plain-list">
                {goal.requirements.map((r) => (
                  <li key={r.id}>
                    <span className="ov-id">{r.id}</span>
                    <span className="ov-line-title">{r.title}</span>
                    <span className={"ov-chip" + (r.status === "closed" ? " ok" : "")}>
                      {STATUS_WORD[r.status] ?? r.status}
                    </span>
                    <SourceMark source={r.source} onOpen={onOpenSrc} />
                  </li>
                ))}
              </ul>
            </>
          )}
          <h4 className="ov-subhead">来源此愿景的蓝图块</h4>
          {blocks.length === 0 ? (
            <p className="ov-empty-inline">暂无蓝图块挂接此愿景。</p>
          ) : (
            <ul className="ov-plain-list">
              {blocks.map((b) => (
                <li key={b.id}>
                  <button className="ov-jump" onClick={() => onJumpBlock(b.id)}>
                    <span className="ov-id">{b.id}</span>
                    <span className="ov-line-title">{b.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <p className="ov-src-line">
            出处 {goal.source.locus} ｜ 采样 {goal.source.sampled_at.slice(0, 19).replace("T", " ")}
          </p>
        </div>
      )}
    </article>
  );
}

/* ── 第二层：战略蓝图应然块 ─────────────────────────────────────── */
function BlockCard({ block, open, onToggle, goalText, laneName, onJumpGoal, onJumpLane, onOpenSrc }: {
  block: BlueprintBlock;
  open: boolean;
  onToggle: () => void;
  goalText: (gid: string) => string;
  laneName: (lid: string) => string;
  onJumpGoal: (gid: string) => void;
  onJumpLane: (lid: string) => void;
  onOpenSrc: (s: SourcePtr) => void;
}) {
  return (
    <article className={"ov-card" + (open ? " open" : "")} id={"ov-block-" + block.id}>
      <button className="ov-card-head" onClick={onToggle} aria-expanded={open}>
        <span className="ov-id">{block.id}</span>
        <span className="ov-title">
          {block.name}——{block.description.length > 30
            ? block.description.slice(0, 30) + "…" : block.description}
        </span>
        <span className="ov-meta">愿景 {block.vision.length}</span>
        <span className="ov-meta">业务线 {block.lanes.length}</span>
        <SourceMark source={block.source} onOpen={onOpenSrc} />
      </button>
      {open && (
        <div className="ov-detail">
          <h4 className="ov-subhead">应然描绘（项目应拥有的能力、组件与表现力）</h4>
          <p className="ov-fulltext">{block.description}</p>
          <h4 className="ov-subhead">来源愿景</h4>
          <ul className="ov-plain-list">
            {block.vision.map((gid) => (
              <li key={gid}>
                <button className="ov-jump" onClick={() => onJumpGoal(gid)}>
                  <span className="ov-id">{gid}</span>
                  <span className="ov-line-title">
                    {goalText(gid).slice(0, 40)}{goalText(gid).length > 40 ? "…" : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <h4 className="ov-subhead">服务业务线</h4>
          <ul className="ov-plain-list">
            {block.lanes.map((lid) => (
              <li key={lid}>
                <button className="ov-jump" onClick={() => onJumpLane(lid)}>
                  <span className="ov-id">{lid}</span>
                  <span className="ov-line-title">{laneName(lid)}</span>
                </button>
              </li>
            ))}
          </ul>
          <p className="ov-src-line">
            核定 {block.approved_at ?? "—"} ｜ {block.approved_by ?? "—"} ｜
            出处 {block.source.locus}
            {block.note ? " ｜ " + block.note : ""}
          </p>
        </div>
      )}
    </article>
  );
}

/* ── 第三层：HQ 述职卡（自我认知、压缩记忆、曾用现用对话） ───────── */
function HqCard({ open, onToggle, openCount, onOpenSrc }: {
  open: boolean;
  onToggle: () => void;
  openCount: number;
  onOpenSrc: (s: SourcePtr) => void;
}) {
  const mission = LANE_MISSIONS["HQ"];
  return (
    <article className={"ov-card ov-hq" + (open ? " open" : "")} id="ov-hq-HQ">
      <button className="ov-card-head" onClick={onToggle} aria-expanded={open}>
        <span className="ov-id">HQ</span>
        <span className="ov-title">指挥部述职卡</span>
        {openCount > 0 && <span className="ov-meta">在办 {openCount}</span>}
        {mission && <SourceMark source={mission.source} onOpen={onOpenSrc} />}
      </button>
      {open && (
        <div className="ov-detail">
          {mission && (
            <>
              <h4 className="ov-subhead">存在理由</h4>
              <p className="ov-fulltext">{mission.mission}</p>
            </>
          )}
          <h4 className="ov-subhead">自我认知</h4>
          {HQ_CARD.self.map((s) => (
            <div className="ov-mem" key={s.id}>
              <p className="ov-fulltext">{s.text}</p>
              <SourceMark source={s.source} onOpen={onOpenSrc} />
            </div>
          ))}
          <h4 className="ov-subhead">压缩记忆（记忆存档位置，交接卡原文）</h4>
          {HQ_CARD.memory.map((m) => (
            <div className="ov-mem ov-mem-cm" key={m.id}>
              <p className="ov-mem-label">{m.label}</p>
              <p className="ov-fulltext">{m.text}</p>
              <SourceMark source={m.source} onOpen={onOpenSrc} />
            </div>
          ))}
          <h4 className="ov-subhead">曾用现用对话（按时间排列）</h4>
          <ol className="ov-dialogs">
            {HQ_CARD.dialogs.map((d) => (
              <li key={d.id}>
                <span className={"ov-chip" + (d.era.includes("现用") ? " ok" : "")}>{d.era}</span>
                <span className="ov-dialog-who">{d.who}</span>
                <span className="ov-dialog-sid">{d.session}</span>
                <span className="ov-dialog-note">{d.note}</span>
                <SourceMark source={d.source} onOpen={onOpenSrc} />
              </li>
            ))}
          </ol>
          <p className="ov-src-line">
            述职卡数据源为决定记录（协作架构 D-048）、交接卡（入职卡）与会话沿革记载，
            注册数据生成时刻 {HQ_CARD.dialogs[0]?.source.sampled_at ?? "—"}。
          </p>
        </div>
      )}
    </article>
  );
}

/* ── 第三层：业务线述职卡（存在理由 + 所服务的蓝图块） ──────────── */
function LaneReportCard({ lane, open, mission, blocks, onToggle, onJumpBlock, onOpenSrc }: {
  lane: ConsoleState["lanes"][number];
  open: boolean;
  mission?: { mission: string; source: SourcePtr };
  blocks: BlueprintBlock[];
  onToggle: () => void;
  onJumpBlock: (bid: string) => void;
  onOpenSrc: (s: SourcePtr) => void;
}) {
  const working = lane.items.filter((i) => i.status === "in_progress").length;
  return (
    <article className={"ov-card" + (open ? " open" : "")} id={"ov-lane-" + lane.id}>
      <button className="ov-card-head" onClick={onToggle} aria-expanded={open}>
        <span className="ov-id">{lane.id}</span>
        <span className="ov-title">{lane.name}</span>
        {lane.items.length > 0 && <span className="ov-meta">在办 {lane.items.length}</span>}
        {working > 0 && <span className="ov-meta">进行中 {working}</span>}
        <span className="ov-meta">服务块 {blocks.length}</span>
        <SourceMark source={lane.source} onOpen={onOpenSrc} />
      </button>
      {open && (
        <div className="ov-detail">
          <h4 className="ov-subhead">存在理由</h4>
          {mission ? (
            <>
              <p className="ov-fulltext">{mission.mission}</p>
              <SourceMark source={mission.source} onOpen={onOpenSrc} />
            </>
          ) : (
            <p className="ov-empty-inline">该路线暂无使命登记（lanes.json mission 字段为空）。</p>
          )}
          <h4 className="ov-subhead">所服务的蓝图块</h4>
          {blocks.length === 0 ? (
            <p className="ov-empty-inline">首批核定蓝图块中暂无块登记此路线为服务线。</p>
          ) : (
            <ul className="ov-plain-list">
              {blocks.map((b) => (
                <li key={b.id}>
                  <button className="ov-jump" onClick={() => onJumpBlock(b.id)}>
                    <span className="ov-id">{b.id}</span>
                    <span className="ov-line-title">{b.name}</span>
                  </button>
                  <SourceMark source={b.source} onOpen={onOpenSrc} />
                </li>
              ))}
            </ul>
          )}
          <h4 className="ov-subhead">在办事项</h4>
          {lane.items.length === 0 ? (
            <p className="ov-empty-inline">该路线当前无未关闭事项。</p>
          ) : (
            <ul className="ov-plain-list">
              {lane.items.map((i) => (
                <li key={i.id}>
                  <span className="ov-id">{i.id}</span>
                  <span className="ov-line-title">{i.title}</span>
                  <span className="ov-meta">{STATUS_WORD[i.status] ?? i.status}</span>
                  <SourceMark source={i.source} onOpen={onOpenSrc} />
                </li>
              ))}
            </ul>
          )}
          <p className="ov-src-line">出处 {lane.source.locus}</p>
        </div>
      )}
    </article>
  );
}
