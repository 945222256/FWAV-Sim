/* InboxPanel.tsx — 待审队列（I-122）：完整字段卡片 + 点击展开。
 * 数据来自既有只读接口 /api/state 的 inbox 数组，后端零改动。 */
import { useState } from "react";

export interface InboxEntry {
  id: string;
  title: string;
  owner?: string;
  close_criterion?: string;
  criteria?: string[];
  note?: string;
  submitted_by?: string;
  submitted_at?: string;
  lint?: {
    checked_at?: string;
    suggested_lane?: string;
    rules?: { rule: string; verdict: string; reason: string }[];
    duplicate_of?: { id: string }[];
  } | null;
  decision?: { action: string; at: string; reason?: string; lane?: string; result_item?: string } | null;
}

const VERDICT_CN: Record<string, string> = { pass: "过", warn: "建议", fail: "不过" };

function Card({ q }: { q: InboxEntry }) {
  const [open, setOpen] = useState(false);
  const rules = q.lint?.rules || [];
  const brief = rules.map(r => `${r.rule}${VERDICT_CN[r.verdict] || r.verdict}`).join("；");
  const dups = (q.lint?.duplicate_of || []).map(d => d.id).join("、");
  return (
    <div className="lcard">
      <div className="r1" style={{ cursor: "pointer" }} onClick={() => setOpen(o => !o)}>
        <b>{q.id}</b>
        <span className="ld-title">{q.title}</span>
        <span style={{ marginLeft: "auto", flexShrink: 0, color: "var(--dim)", fontSize: 11 }}>
          {open ? "收起 ▴" : "展开完整字段 ▾"}
        </span>
      </div>
      <div className="ld-why">
        <span className="lab">提交人</span>{q.submitted_by || "匿名"} · {q.submitted_at}
        {q.owner ? <> · 建议 {q.owner}</> : null}
      </div>
      <div className="ld-why"><span className="lab">正文备注</span>{q.note || "（无）"}</div>
      <div className="ld-why"><span className="lab">打勾判据</span>{q.close_criterion || "未填"}</div>
      <div className="ld-why"><span className="lab">语言检查</span>{brief || "未跑"}</div>
      {rules.length > 0 && (
        <div style={{ margin: "4px 0" }}>
          {rules.map((r, i) => (
            <div key={i} className="lint-rule" style={{ fontSize: 11, display: "flex", gap: 6 }}>
              <span className={"ld-st " + r.verdict} style={{ flexShrink: 0 }}>
                {VERDICT_CN[r.verdict] || r.verdict}
              </span>
              <span><b>{r.rule}：</b>{r.reason}</span>
            </div>
          ))}
        </div>
      )}
      {open && (
        <div style={{ borderTop: "1px dashed var(--line)", marginTop: 6, paddingTop: 6 }}>
          <div className="ld-why"><span className="lab">判据子项</span>
            {q.criteria && q.criteria.length ? q.criteria.join("；") : "未登记"}</div>
          <div className="ld-why"><span className="lab">lint 建议</span>{q.lint?.suggested_lane || "未建议"}</div>
          <div className="ld-why"><span className="lab">重复候选</span>{dups || "无"}</div>
          {q.decision && (
            <div className="ld-why"><span className="lab">审理记录</span>
              {q.decision.action} · {q.decision.at}
              {q.decision.result_item ? ` → ${q.decision.result_item}（${q.decision.lane}）` : ""}
              {q.decision.reason ? `；理由：${q.decision.reason}` : ""}</div>
          )}
        </div>
      )}
    </div>
  );
}

export default function InboxPanel({ inbox }: { inbox: InboxEntry[] }) {
  const pending = inbox.filter(q => !q.decision);
  const decided = inbox.filter(q => q.decision);
  return (
    <section className="linedrill">
      <header className="ld-head">
        <div>
          <h2>待审队列<span className="ld-prog">{pending.length} 条待审 / {decided.length} 条已审</span></h2>
          <p className="ld-mission">点卡片头部展开完整字段；审理动作在旧版看板执行</p>
        </div>
      </header>
      <div className="ld-list">
        {pending.map(q => <Card key={q.id} q={q} />)}
        {pending.length === 0 && <div className="empty">待审队列为空</div>}
        {decided.map(q => <Card key={q.id} q={q} />)}
      </div>
    </section>
  );
}
