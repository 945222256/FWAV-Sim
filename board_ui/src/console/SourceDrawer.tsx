/* console/SourceDrawer.tsx — 出处抽屉（评审意见第四节钉固形态）。
 * 右侧滑入，宽三百六十像素，发丝线边框无投影；摘录不超过十行超出
 * 折叠；含记录时间、账本编号与复制按钮；动效与参数实验室抽屉同一
 * 规格（二百毫秒，位移与透明度）。 */
import { useState } from "react";
import type { SourcePtr } from "./types";

export default function SourceDrawer({ source, onClose }: {
  source: SourcePtr | null;
  onClose: () => void;
}) {
  const [unfolded, setUnfolded] = useState(false);
  const [copied, setCopied] = useState(false);
  if (!source) return null;
  const item = source.locus.match(/items\/(I-\d{3})/);
  const lines = source.excerpt.split("\n");
  return (
    <div className="src-mask" onClick={onClose}>
      <aside className="src-drawer" onClick={(e) => e.stopPropagation()} aria-label="出处抽屉">
        <header className="src-drawer-head">
          <div>
            <div className="src-drawer-kind">出处 · {source.kind}</div>
            <div className="src-drawer-locus">{source.locus}</div>
          </div>
          <button className="lab-btn-minor" onClick={onClose}>关闭</button>
        </header>
        <div className="src-drawer-body">
          <h4>摘录</h4>
          <pre className={"src-excerpt" + (unfolded || lines.length <= 10 ? " unfolded" : "")}>
            {source.excerpt}
          </pre>
          {lines.length > 10 && (
            <button className="lab-btn-minor src-fold" onClick={() => setUnfolded(!unfolded)}>
              {unfolded ? "收起" : "展开全部 " + lines.length + " 行"}
            </button>
          )}
          <dl className="src-meta">
            <dt>记录时间</dt><dd>{source.sampled_at}</dd>
            <dt>账本编号</dt>
            <dd>
              {item
                ? <a className="src-link" href={"#/item/" + item[1]}>{item[1]}（查看账本原文）</a>
                : "—"}
            </dd>
          </dl>
          <button className="lab-btn-minor" onClick={() => {
            navigator.clipboard.writeText(JSON.stringify(source, null, 1)).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            });
          }}>
            {copied ? "已复制" : "复制出处四字段"}
          </button>
        </div>
      </aside>
    </div>
  );
}
