/* console/SourceMark.tsx — 出处标记（评审意见第四节钉固形态）。
 * 十二像素线性回形针角标，一点五像素描边，次要文字色；悬停出摘要
 * 浮层（二百毫秒），点击开右侧出处抽屉。每格信息右下角一个。 */
import type { SourcePtr } from "./types";

export default function SourceMark({ source, onOpen }: {
  source: SourcePtr;
  onOpen: (s: SourcePtr) => void;
}) {
  return (
    <span className="src-wrap">
      <button className="src-mark" aria-label={"出处：" + source.kind + " " + source.locus}
              onClick={(e) => { e.stopPropagation(); onOpen(source); }}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
          <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
        </svg>
      </button>
      <span className="src-pop" role="tooltip">
        <b>{source.kind}</b> {source.locus}
        <br />采样 {source.sampled_at}
      </span>
    </span>
  );
}
