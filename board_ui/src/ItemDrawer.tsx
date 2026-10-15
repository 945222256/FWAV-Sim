/* ItemDrawer.tsx — 一图：任意节点点开，展开判据对照、证据链与时间线。 */
import type { ItemDetail } from "./types";

export default function ItemDrawer({ id, detail, onClose, laneColors }: {
  id: string; detail: ItemDetail | null; onClose: () => void;
  laneColors: Record<string, string>;
}) {
  const it = detail?.item;
  return (
    <div className="drawer-mask" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <aside className="drawer">
        <header>
          <span className="did">{id}</span>
          <button className="close" onClick={onClose}>✕</button>
        </header>
        {!it ? (
          <div className="loading">加载中…</div>
        ) : (
          <>
            <h2>{it.title}</h2>
            <p className="meta">
              状态 {it.status}
              {it.line && <> · 逻辑线 {it.line}</>}
              {it.lane && <> · <span style={{ color: laneColors[it.lane] || "#8b949e" }}>{it.lane} 路线</span></>}
              {it.owner && <> · 负责人 {it.owner}</>}
              {it.claimed_by && <> · 认领 {it.claimed_by}</>}
              {it.closed_at && <> · 关闭 {it.closed_at}（{it.closed_by}）</>}
            </p>
            <h3>来由</h3>
            <p className="crit">{it.rationale || "该事项尚未登记来由（rationale 字段为空），补录由数据资产线负责"}</p>
            <h3>打勾判据</h3>
            <p className="crit">{it.close_criterion || "未登记"}</p>
            {it.check_script && (
              <p className="crit">
                判据脚本：{it.check_script}
                {it.check_result && (
                  <span className={it.check_result.pass ? "ccheck-ok" : "ccheck-bad"}>
                    {" "}执行结果：{it.check_result.pass ? "过" : "不过"}（退出码 {it.check_result.exit_code}，{it.check_result.ran_at}）
                  </span>
                )}
                {!it.check_result && <span className="ccheck-na">（尚未执行）</span>}
              </p>
            )}

            {it.conclusion && <p className="concl">关闭结论：{it.conclusion}</p>}

            {it.criterion_check && it.criterion_check.length > 0 && (
              <>
                <h3>判据逐条对照</h3>
                <ul className="ccheck">
                  {it.criterion_check.map((c, i) => (
                    <li key={i}>
                      <b className={c["结果"] === "过" ? "ok" : "bad"}>{c["结果"]}</b>
                      {c["判据"]}
                      <span className="src">出处：{c["数值出处"]}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}

            {it.criteria && it.criteria.length > 0 ? (
              <>
                <h3>判据子项</h3>
                <ul className="subs">
                  {it.criteria.map((c, i) => (
                    <li key={i} className={c.verified ? "ok" : ""}>
                      <span>{c.verified ? "☑" : "☐"}</span>
                      <div>
                        {c.text}
                        {c.verified && (
                          <small>验证 {c.verified_by} {c.verified_at}；证据 {c.evidence}</small>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <>
                <h3>判据子项</h3>
                <p className="na-reason">该事项未登记判据子项，判据子项口径不适用；完成度以关闭状态与判据对照为准。</p>
              </>
            )}

            <h3>证据链（{it.evidence.length}）</h3>
            <ul className="evlist">
              {it.evidence.map((e, i) => <li key={i}>{e}</li>)}
            </ul>

            {it.replies && it.replies.length > 0 && (
              <>
                <h3>机主答复</h3>
                <ul className="replies">
                  {it.replies.map((r, i) => (
                    <li key={i}><b>{r.actor}</b> {r.ts}：{r.text}</li>
                  ))}
                </ul>
              </>
            )}

            <h3>时间线</h3>
            <ul className="timeline">
              {detail.events.map((e, i) => (
                <li key={i}>
                  <span className="ts">{e.ts}</span>
                  <span className="act">{e.action}</span>
                  <span>{e.actor}：{e.detail}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </aside>
    </div>
  );
}
