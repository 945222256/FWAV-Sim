/* lab/EaglePreview.tsx — 七百克候选动作预览视图（I-202）。
 * 数据纪律：动作段清单、几何数值、绑定表、来源版本与候选状态全部
 * 来自 /api/lab/params 的 eaglex700 节（lab_payload.py 从 Jane 交付
 * 文件现读构建），本组件不写死任何提取值；帧资产路径由数据节下发，
 * 挂在 /app/lab/eaglex700/ 静态目录。
 * 播放器：帧序列轮播（I-130 转台同构，零新依赖），播放、暂停、
 * 重新开始三控制；五种动作分段可单独辨识；候选状态横幅常驻，
 * 不因播放可用而标记物理构型通过（判据四）。 */
import { useMemo, useRef, useState } from "react";
import type { AnimV2Node, EagleNode } from "./types";

/* 动画第二版视频区（I-202 复工）：机主认可尾部构型方向的阶段展示。
 * 视频为 L8 交付原片直接接入（不重渲染不换版本）；导出模型骨骼编号
 * 越界未修复前不载入交互模型；播放、暂停、重新开始用视频原生控制
 * 加显式按钮。 */
function AnimV2Panel({ v2 }: { v2: AnimV2Node }) {
  const [segId, setSegId] = useState(v2.segments[0]?.id ?? "");
  const [videoError, setVideoError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const seg = useMemo(() => v2.segments.find((x) => x.id === segId),
                      [v2, segId]);
  if (!v2.available) {
    return (
      <div className="eaglex-banner">
        动画第二版视频暂不可用：{v2.reason ?? ("缺失片段 " + (v2.missing_clips || []).join("、"))}
      </div>
    );
  }
  return (
    <section className="eaglex-v2" aria-label="动画第二版视频（机主认可的尾部构型方向）">
      <h3>动画第二版 · 机主 2026-10-13 认可的尾部构型方向（阶段预览）</h3>
      <div className="eaglex-v2-stage">
        <video ref={videoRef} key={seg?.asset}
               src={seg ? "/app/" + seg.asset : undefined}
               poster={seg?.poster ? "/app/" + seg.poster : undefined}
               controls preload="auto" playsInline
               className="eaglex-video"
               onError={() => setVideoError("视频加载失败：通道或编码不可用，请刷新重试；持续失败请回报时段与设备。")} />
        <div className="eaglex-stage-meta">{seg?.label}｜{v2.source.version_name}｜{v2.source.date}｜未启动仿真器</div>
      </div>
      <nav className="eaglex-segs" aria-label="第二版动作分段">
        {v2.segments.map((x) => (
          <button key={x.id} className={"eaglex-seg" + (x.id === segId ? " active" : "")}
                  onClick={() => { setSegId(x.id); videoRef.current?.pause(); }}>
            {x.label}
          </button>
        ))}
      </nav>
      <div className="eaglex-ctl-row">
        <button className="lab-btn" onClick={() => videoRef.current?.play()}>播放</button>
        <button className="lab-btn" onClick={() => videoRef.current?.pause()}>暂停</button>
        <button className="lab-btn" onClick={() => {
          if (videoRef.current) { videoRef.current.currentTime = 0; videoRef.current.play(); }
        }}>重新开始</button>
      </div>
      {videoError && <div className="eaglex-v2-error" role="alert">{videoError}</div>}
      <div className="eaglex-v2-notes">
        <p>{v2.owner_ack}</p>
        <p>{v2.stage_note}</p>
        <p>{v2.supersedes_note}｜来源 {v2.source.dir}，报告校验值 {v2.source.report_sha12}。</p>
      </div>
    </section>
  );
}

export default function EaglePreview({ node, onOpenSrc }: {
  node: EagleNode | undefined;
  onOpenSrc: (locus: string, excerpt: string) => void;
}) {
  /* I-202 黑屏处置：第一版帧序列播放器状态随播放器一并移除，
   * 动作展示由动画第二版视频承担（机主已认可构型方向）。 */
  if (!node || !node.available) {
    return (
      <section className="eaglex">
        <div className="eaglex-banner">
          七百克候选动作预览暂不可用：{node?.reason ?? "数据节未随载荷下发。"}
        </div>
      </section>
    );
  }

  const src = node.source;
  return (
    <section className="eaglex" aria-label="七百克候选动作预览">
      <div className="eaglex-banner" role="note">
        当前是<b>模型动作展示</b>（开发样例 · {src.date} 版），验证范围仅为视觉动作与几何陈列；
        质量为<b>{node.candidate_status.mass}</b>、惯量为<b>{node.candidate_status.inertia}</b>、
        气动为<b>{node.candidate_status.aero}</b>、飞行能力为<b>{node.candidate_status.flight}</b>，
        不因动作预览可用而认定物理构型通过。{node.physics_frozen}。{node.sample_disclaimer}
      </div>

      {node.animv2 && <AnimV2Panel v2={node.animv2} />}

      {/* I-202 黑屏处置：旧构型帧序列播放器从主界面移除（机主已认可
       * 第二版构型，两套动作入口易误导且旧区整块灰），帧资产与数据节
       * 保留在库可回退；几何数据陈列与来源不受影响。 */}
      <div className="eaglex-geo" data-legacy="几何与绑定数据（来源含旧开发样例交付，第一版构型保留为历史诊断，不进入最终页面）">
        <h3>几何数据（来源 {src.dir}，几何文件 {src.geo_sha12}，弦长表 {src.chord_sha12}）</h3>
        {node.motion.mechanism_note && (
          <p className="eaglex-note">{node.motion.mechanism_note}</p>
        )}
        <table className="eaglex-table">
          <thead>
            <tr><th>部件</th><th>尺寸 长×宽×高（米）</th><th>表面积（平方米）</th>
                <th>投影平面面积（平方米）</th><th>展向长度（米）</th><th>三角面</th></tr>
          </thead>
          <tbody>
            {node.geometry.parts.map((p) => (
              <tr key={p.part}>
                <td>{p.part}</td>
                <td>{(p.dimensions_m ?? []).map((v) => v.toFixed(4)).join(" × ")}</td>
                <td>{p.surface_area_m2?.toFixed(6)}</td>
                <td>{p.projected_planform_area_m2?.toFixed(6)}</td>
                <td>{p.spanwise_extent_m?.toFixed(4)}</td>
                <td>{p.triangles}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <details className="eaglex-details">
          <summary>弦长站位表（{node.geometry.chord_stations.rows} 行，单位米）</summary>
          <div className="eaglex-chord-scroll">
            <table className="eaglex-table eaglex-table-sm">
              <thead>
                <tr>{node.geometry.chord_stations.columns.map((c) => <th key={c}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {node.geometry.chord_stations.data.slice(0, 200).map((r, i) => (
                  <tr key={i}>
                    {node.geometry.chord_stations.columns.map((c) => (
                      <td key={c}>{r[c]}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
        <details className="eaglex-details">
          <summary>关节绑定（{node.bindings.length} 个关节，坐标单位米、限位单位弧度）</summary>
          <table className="eaglex-table eaglex-table-sm">
            <thead>
              <tr><th>关节</th><th>父件</th><th>子件</th><th>原点（米）</th><th>轴</th><th>限位（弧度）</th></tr>
            </thead>
            <tbody>
              {node.bindings.map((b) => (
                <tr key={b.joint}>
                  <td>{b.joint}</td><td>{b.parent}</td><td>{b.child}</td>
                  <td>{b.origin_xyz_m.join(", ")}</td>
                  <td>{b.axis.join(", ")}</td>
                  <td>{b.range_rad.join(" ~ ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
        <details className="eaglex-details">
          <summary>来源链与限制声明（provenance 与 limitations 原文）</summary>
          <ul className="eaglex-plain">
            {(node.provenance ?? []).map((p, i) => (
              <li key={i}>
                <button className="eaglex-src-link"
                        onClick={() => onOpenSrc(p.path, "sha256 前 12 位：" + String(p.sha256).slice(0, 12))}>
                  出处
                </button>
                {p.path}（sha256 {String(p.sha256).slice(0, 12)}）
              </li>
            ))}
          </ul>
          <ul className="eaglex-plain">
            {(node.limitations ?? []).map((l, i) => <li key={i}>{l}</li>)}
          </ul>
          <p className="eaglex-note">{node.verification_note}</p>
        </details>
      </div>
    </section>
  );
}
