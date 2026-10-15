/* console/HqView.tsx — 指挥部页面（I-144）：目标到需求到事项三级拆解树。
 * 层级执行评审并入记录（方案第十四节）：目标卡名称大字，授权与出处
 * 降为合并次要行；需求行进度条占行宽三分之一，事项数次要文字；
 * 需求明细卡默认不自动弹出，点击才展开。只渲染接口返回字段。 */
import { useState } from "react";
import type { ConsoleState, GoalNode, RequirementNode, SourcePtr } from "./types";
import { STATUS_WORD } from "./types";
import SourceMark from "./SourceMark";

function ReqDetail({ req, goal, onOpenSrc }: {
  req: RequirementNode;
  goal: GoalNode;
  onOpenSrc: (s: SourcePtr) => void;
}) {
  const done = req.enables.filter((e) => e.status === "closed").length;
  return (
    <div className="hq-detail" onClick={(e) => e.stopPropagation()}>
      <dl className="hq-detail-grid">
        <dt>名称</dt><dd>{req.id} {req.title}</dd>
        <dt>状态</dt><dd>{STATUS_WORD[req.status] ?? req.status}</dd>
        <dt>归属目标</dt><dd>{goal.id} {goal.text.slice(0, 30)}{goal.text.length > 30 ? "…" : ""}</dd>
        <dt>进度</dt>
        <dd>{req.enables.length > 0 ? done + "/" + req.enables.length + " 完成" : "—（无关联事项）"}</dd>
        <dt>授权记录</dt><dd>接口未提供该字段</dd>
        <dt>出处</dt>
        <dd className="hq-detail-src">
          <span>{req.source.kind} · {req.source.locus}</span>
          <SourceMark source={req.source} onOpen={onOpenSrc} />
        </dd>
      </dl>
      {req.enables.length > 0 && (
        <ul className="hq-items">
          {req.enables.map((e) => (
            <li key={e.id}>
              <span className="hq-item-id">{e.id}</span>
              <span className="hq-item-title">{e.title}</span>
              <span className="hq-item-status">{STATUS_WORD[e.status] ?? e.status}</span>
              <SourceMark source={e.source} onOpen={onOpenSrc} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function GoalCard({ goal, onOpenSrc }: {
  goal: GoalNode;
  onOpenSrc: (s: SourcePtr) => void;
}) {
  const [openReq, setOpenReq] = useState<string | null>(null);
  return (
    <section className="hq-goal">
      <header className="hq-goal-head">
        <h3 className="hq-goal-title">{goal.id}
          <span className={"hq-goal-status" + (goal.status === "active" ? " ok" : "")}>
            {STATUS_WORD[goal.status] ?? goal.status}
          </span>
        </h3>
        <p className="hq-goal-text">{goal.text}</p>
        <p className="hq-goal-sub">
          出处 {goal.source.locus} ｜ 采样 {goal.source.sampled_at.slice(0, 19).replace("T", " ")}
          <SourceMark source={goal.source} onOpen={onOpenSrc} />
        </p>
      </header>
      {goal.requirements.length === 0 ? (
        <p className="hq-goal-empty">该目标下暂无挂接需求。</p>
      ) : (
        <ul className="hq-reqs">
          {goal.requirements.map((req) => {
            const done = req.enables.filter((e) => e.status === "closed").length;
            const pct = req.enables.length > 0 ? done / req.enables.length : null;
            const open = openReq === req.id;
            return (
              <li key={req.id}>
                <button className={"hq-req" + (open ? " open" : "")}
                        onClick={() => setOpenReq(open ? null : req.id)}>
                  <span className="hq-req-id">{req.id}</span>
                  <span className="hq-req-title">{req.title}</span>
                  <span className="hq-req-bar" aria-label={"进度 " + done + "/" + req.enables.length}>
                    <span className="hq-req-bar-fill" style={{ width: pct == null ? 0 : pct * 100 + "%" }} />
                  </span>
                  <span className="hq-req-count">
                    {req.enables.length > 0 ? done + "/" + req.enables.length + " 事项" : "事项 0"}
                  </span>
                  <span className={"hq-req-status" + (req.status === "closed" ? " ok" : "")}>
                    {STATUS_WORD[req.status] ?? req.status}
                  </span>
                  <SourceMark source={req.source} onOpen={onOpenSrc} />
                </button>
                {open && <ReqDetail req={req} goal={goal} onOpenSrc={onOpenSrc} />}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export default function HqView({ state, onOpenSrc }: {
  state: ConsoleState;
  onOpenSrc: (s: SourcePtr) => void;
}) {
  return (
    <div className="hq">
      {state.goals.map((g) => <GoalCard key={g.id} goal={g} onOpenSrc={onOpenSrc} />)}
    </div>
  );
}
