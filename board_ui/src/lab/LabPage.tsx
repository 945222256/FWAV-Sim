/* lab/LabPage.tsx — 参数实验室页面（I-136 第二版：意义索引陈列）。
 *
 * 设计依据：需求定稿 D-047、设计方案第二版、Ciri 评审意见 2026-10-10
 * 及评审并入修订附录（board_ui/lab_design/ 三件）。
 * 数据纪律：页面每一个数值实时来自 /api/lab/params 只读接口与
 * /app/lab/manifest.json 资产清单，前端源码不写死任何参数值。
 * 动效清单四条（评审第四节）：抽屉二百毫秒、选项卡一百五十毫秒、
 * 索引芯片一百五十毫秒、点跳闪烁六百毫秒一次性两拍；闪烁严格一次、
 * 新跳终止旧闪、滚动定位完成后才启动。 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LabChainHop, LabFormula, LabManifest, LabParam, LabPayload } from "./types";
import {
  AXES, AXIS_LABELS, CONSUMER_LABELS, DEFAULT_PATH_LABELS, REVIEW_LABELS, REVIEW_SLOTS,
  SAMPLE_SCOPE_LABELS, SLOT_LABELS, USAGE_LABELS,
  aliasTarget, applicableConditions, applicableModes, compareCodepoints, consumerLabel, currentParam, defaultPathLabel, displayDefinition, displayName,
  emptyFilters, facetCounts, facetIds, facetLabel, groupedParams, hasFilters, metadataGaps, nextOwnerLabel, reviewOf,
  revealTargetFilters, sampleIds, sampleLabel, selectParams, semanticDiagnostics, sortedParams, supersededKeys, usageOf,
} from "./semantics";
import type { LabFilters } from "./semantics";
import EaglePreview from "./EaglePreview";
import "./lab.css";

const STATUS_COLORS: Record<string, string> = {
  "已完成": "var(--tk-done)",
  "部分": "var(--tk-partial)",
  "缺失": "var(--tk-missing)",
  "待重算": "var(--tk-stale)",
};

/* ── 点跳与一次性闪烁（评审第四节三条边界） ──────────────────────── */
let flashTimer: number | undefined;
let flashEl: Element | null = null;

function clearFlash() {
  if (flashEl) flashEl.classList.remove("lab-flash");
  flashEl = null;
  if (flashTimer) window.clearTimeout(flashTimer);
}

/** 滚动定位完成后启动一次性闪烁：新跳开始时旧闪立即终止。 */
function jumpAndFlash(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  clearFlash();
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  const start = () => {
    clearFlash();
    el.classList.add("lab-flash");
    flashEl = el;
    flashTimer = window.setTimeout(clearFlash, 700);
  };
  let started = false;
  const once = () => { if (!started) { started = true; start(); } };
  el.addEventListener("scrollend", once, { once: true });
  window.setTimeout(once, 480);
}

function StatusDot({ status }: { status?: string }) {
  if (status && STATUS_COLORS[status]) {
    return <span className="lab-dot" style={{ background: STATUS_COLORS[status] }}
                 aria-label={"状态：" + status} />;
  }
  return <span className="lab-dot lab-dot-blank" aria-label="状态：未入四档（待盘点）" />;
}

function statusText(p: LabParam): string {
  const s = p.graph?.status;
  return s && STATUS_COLORS[s] ? s : "未入四档";
}

function LevelTag({ p }: { p: LabParam }) {
  const n = p.level;
  const label = n == null ? "未定级" : "第" + n + "级";
  const title = p.level_source === "graph" ? "盘点在册（I-127 图字段）" : p.level_rule;
  return <span className={"lab-lvtag" + (p.level_source === "rule" ? " tentative" : "")}
               title={title}>{label}</span>;
}

function SemanticTags({ p }: { p: LabParam }) {
  return <span className="lab-sem-tags">
    <span className={"lab-sem-tag lab-usage-" + usageOf(p)}>{USAGE_LABELS[usageOf(p)]}</span>
    {REVIEW_SLOTS.map((slot) => <span key={slot} className="lab-sem-tag">
      {SLOT_LABELS[slot]}：{REVIEW_LABELS[reviewOf(p, slot)]}
    </span>)}
  </span>;
}

/* 使用默认浅比较：名称、定义或任意语义元数据更新都随新记录重新渲染。 */
function ParamCardInner({ p, onOpen }: { p: LabParam; onOpen: (p: LabParam) => void }) {
  const gaps = metadataGaps(p);
  return (
    <button className="lab-card" id={"lab-p-" + encodeURIComponent(p.key)} onClick={() => onOpen(p)}>
      <span className="lab-card-top">
        <span className="lab-card-short">{displayName(p)}</span>
        <LevelTag p={p} />
      </span>
      <span className="lab-card-key">{p.key}</span>
      <SemanticTags p={p} />
      <span className="lab-card-explain">{displayDefinition(p)}</span>
      <span className="lab-card-valuerow">
        <StatusDot status={p.graph?.status} />
        <span className="lab-card-value">
          {p.value === "" || p.value == null ? "（值空待盘点）" : p.value}
          {p.unit ? <span className="lab-card-unit"> {p.unit}</span> : null}
        </span>
        {p.formulas.length > 0 && (
          <span className="lab-card-fxmark" title={"出现在力律公式：" + p.formulas.map((f) => f.name).join("、")}>
            公式×{p.formulas.length}
          </span>
        )}
      </span>
      {gaps.length > 0 && <span className="lab-sem-gap">{gaps.slice(0, 2).join("；")}
        {gaps.length > 2 ? "；其余缺口见详情" : ""}</span>}
      <span className="lab-card-sub">
        <span>有效域 {p.graph?.valid_domain || "待盘点"}</span>
        <span>下游 {p.downstream_count}</span>
        <span>变更 {p.since || "—"}</span>
      </span>
    </button>
  );
}

const ParamCard = memo(ParamCardInner);

function PlaceholderCard({ subclass, text }: { subclass: string; text: string }) {
  return (
    <div className="lab-card-ph">
      <span className="lab-card-top">
        <span className="lab-card-short">{subclass}（预留子类）</span>
        <StatusDot status="缺失" />
      </span>
      <span className="lab-card-explain">{text}</span>
    </div>
  );
}

/* 控制配比：组内各增益按最大值归一（阶段一素材沿用）。 */
function ratioOf(values: number[]): string {
  const mx = Math.max(...values);
  return values
    .map((v) => {
      const r = v / mx;
      const s = r.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
      return s === "" ? "0" : s;
    })
    .join(" : ");
}

function numeric(v: string | null): number | null {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/* ── 公式墙（评审第二节规格） ─────────────────────────────────────── */
function FormulaText({ f, onJumpKey }: { f: LabFormula; onJumpKey: (key: string) => void }) {
  const parts = useMemo(() => {
    const syms = f.symbols.map((s) => s.sym);
    // 把公式正文按符号切分成可点段与纯文本段。
    const sorted = [...syms].sort((a, b) => b.length - a.length);
    let text = f.text;
    const segs: { t: string; sym?: string }[] = [];
    outer: while (text.length) {
      for (const s of sorted) {
        if (text.startsWith(s)) {
          segs.push({ t: s, sym: s });
          text = text.slice(s.length);
          continue outer;
        }
      }
      const next = sorted.reduce((m, s) => {
        const i = text.indexOf(s);
        return i === -1 ? m : Math.min(m, i);
      }, Infinity);
      const cut = next === Infinity ? text.length : next;
      segs.push({ t: text.slice(0, cut) });
      text = text.slice(cut);
    }
    return segs;
  }, [f]);
  return (
    <span className="lab-fx-text">
      {parts.map((seg, i) => {
        if (!seg.sym) return <span key={i}>{seg.t}</span>;
        const s = f.symbols.find((x) => x.sym === seg.sym)!;
        if (!s.target_key && !s.target_family) {
          return <span key={i} className="lab-fx-sym-plain" title={s.name + "：非登记量"}>{seg.t}</span>;
        }
        const target = s.target_key
          ? "lab-p-" + encodeURIComponent(s.target_key)
          : "lab-fam-" + s.target_family;
        return (
          <button key={i} className="lab-fx-sym" title={s.name + "，点击跳转"}
                  onClick={() => s.target_key ? onJumpKey(s.target_key) : jumpAndFlash(target)}>{seg.t}</button>
        );
      })}
    </span>
  );
}

function FormulaWall({ formulas, onJumpKey }: { formulas: LabFormula[]; onJumpKey: (key: string) => void }) {
  return (
    <section className="lab-fxwall" id="lab-fxwall" aria-label="力律公式墙">
      <header className="lab-sec-head">
        <h2>力律公式墙</h2>
        <p className="lab-sec-lead">
          十个公式是这架飞机的受力法则。带下划虚线的符号是登记在册的参数，点击跳到它的参数卡；
          每个参数的抽屉里也能反查它受哪些公式约束。公式只写符号与结构，数值全部在参数卡上实时来自登记库。
        </p>
      </header>
      <div className="lab-fxgrid">
        {formulas.map((f) => (
          <article className="lab-fxcard" key={f.id} id={"lab-fx-" + f.id}>
            <h3>{f.name}</h3>
            <FormulaText f={f} onJumpKey={onJumpKey} />
            <p className="lab-fx-note">{f.note}</p>
            <p className="lab-fx-src">{f.source}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

/* ── 下游链条（评审第七节风险三：空态占位，标题恒在） ─────────────── */
function ChainView({ hops, depth, onOpen }: {
  hops: LabChainHop[]; depth: number; onOpen: (key: string) => void;
}) {
  if (hops.length === 0) return null;
  return (
    <ul className={"lab-chain" + (depth > 0 ? " lab-chain-nested" : "")}>
      {hops.map((h) => (
        <li key={h.key}>
          <button className="lab-chain-key" onClick={() => onOpen(h.key)}>{h.key}</button>
          <ChainView hops={h.via} depth={depth + 1} onOpen={onOpen} />
        </li>
      ))}
    </ul>
  );
}

function Drawer({ p, payload, onClose, onOpenKey }: {
  p: LabParam; payload: LabPayload; onClose: () => void; onOpenKey: (key: string) => void;
}) {
  const t = p.teaching;
  const history = Array.isArray(t.history) ? t.history : [];
  const fam = payload.families.find((f) => f.id === p.family);
  const gaps = metadataGaps(p);
  return (
    <div className="lab-drawer-mask" onClick={onClose}>
      <aside className="lab-drawer" onClick={(e) => e.stopPropagation()} aria-label="参数详情抽屉">
        <header className="lab-drawer-head">
          <div>
            <div className="lab-drawer-short">{displayName(p)}</div>
            <div className="lab-drawer-key">{p.key}</div>
            <div className="lab-drawer-value">
              {p.value === "" || p.value == null ? "（值空待盘点）" : p.value}
              {p.unit ? " " + p.unit : ""}
            </div>
          </div>
          <button className="lab-btn lab-btn-minor" onClick={onClose}>关闭</button>
        </header>
        <div className="lab-drawer-body">
          <p className="lab-drawer-line">
            <StatusDot status={p.graph?.status} /> {statusText(p)}
            <span className="lab-drawer-meta">
              {fam ? fam.name : "待归族"} · {payload.level_names[String(p.level)] ?? ("第" + p.level + "级")}
              {" "}· {p.level_source === "graph" ? "盘点在册" : "暂定"}
            </span>
          </p>
          <section className="lab-sem-detail" aria-label="参数语义与复核">
            <SemanticTags p={p} />
            <p>{consumerLabel(p)} · {defaultPathLabel(p)}</p>
            <p>{sampleLabel(p)}；原记录样本标记：{p.sample || "未登记"}（仅保留原记录，不据此推断适用范围）。</p>
            {AXES.map((axis) => <p key={axis}>{AXIS_LABELS[axis]}：
              {facetIds(p, axis).map((id) => facetLabel(axis, id)).join("、")}</p>)}
            {gaps.length > 0 && <><h4>具体缺口与限制</h4><ul>{gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul></>}
            <p>补证责任人：{nextOwnerLabel(p)}</p>
            {applicableModes(p).length > 0 && <p>适用模式：{applicableModes(p).join("、")}</p>}
            {applicableConditions(p).length > 0 && <><h4>消费条件（仅作说明）</h4>
              <ul>{applicableConditions(p).map((condition, i) => <li key={i}>{condition}</li>)}</ul></>}
            {aliasTarget(p) && <p>对应别名目标：<button className="lab-chain-key"
              onClick={() => onOpenKey(aliasTarget(p))}>{aliasTarget(p)}</button></p>}
            {supersededKeys(p).length > 0 && <p>后继参数：{supersededKeys(p).map((key) =>
              <button className="lab-chain-key" key={key} onClick={() => onOpenKey(key)}>{key}</button>)}</p>}
            {semanticDiagnostics(p).length > 0 && <><h4>载荷语义诊断</h4>
              <ul>{semanticDiagnostics(p).map((message, i) => <li key={i}>{message}</li>)}</ul></>}
            {p.semantics && <details><summary>语义来源与原始复核记录（未核文本保留作依据）</summary>
              <pre className="lab-sem-raw">{JSON.stringify(p.semantics, null, 2)}</pre>
            </details>}
          </section>

          <section className="lab-teach">
            <h4>完整定义</h4>
            <p>{displayDefinition(p)}</p>
            <details><summary>原有讲解记录（不代表语义已复核）</summary>
              <p>{p.explanation}</p><p>{t.definition}</p>
            </details>
            <h4>约束它的公式</h4>
            {p.formulas.length > 0 ? (
              <ul className="lab-drawer-fx">
                {p.formulas.map((f) => (
                  <li key={f.id}>
                    <button onClick={() => {
                      onClose();
                      window.setTimeout(() => jumpAndFlash("lab-fx-" + f.id), 60);
                    }}>{f.name}：<span className="lab-fx-inline">{f.text}</span></button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="lab-empty">该参数不直接出现在力律公式中，属配置或整定量。</p>
            )}
            <h4>它影响什么</h4>
            <p>{t.affects_note}</p>
            <h4>下游传导链条</h4>
            {p.chain.length > 0 ? (
              <ChainView hops={p.chain} depth={0} onOpen={onOpenKey} />
            ) : (
              <p className="lab-empty">暂无在册下游边：图结构登记库目前只有种子链连边。</p>
            )}
            <h4>改大了会怎样</h4>
            <p>{t.increase}</p>
            <h4>历史取值与每次改动的理由</h4>
            {history.length > 0 ? (
              <ul className="lab-history">
                {history.map((e, i) => {
                  const rec = e as Record<string, string>;
                  return (
                    <li key={i}>
                      <span className="lab-hist-val">{String(rec.value)}</span>
                      <span className="lab-hist-period">{rec.valid_period}</span>
                      <span className="lab-hist-reason">{rec.retired_reason}</span>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p>{typeof t.history === "string" ? t.history : "尚无退役历史。"}</p>
            )}
          </section>

          <section className="lab-src">
            <h4>这个数从哪里来</h4>
            <p>{p.source || "来源待考（见登记库）。"}</p>
            <dl>
              <dt>权威来源</dt><dd>{p.graph?.authority || "待盘点录入"}</dd>
              <dt>有效域</dt><dd>{p.graph?.valid_domain || "待盘点录入"}</dd>
              <dt>双写核对</dt><dd>{p.note || "登记库无附注"}</dd>
              <dt>登记以来</dt><dd>{p.since || "—"}</dd>
            </dl>
          </section>
        </div>
      </aside>
    </div>
  );
}

const LAB_BASE = import.meta.env.BASE_URL + "lab/";

/* 三维转台（阶段一资产沿用；评审第五节：缩小为族头局部插图）。 */
function Turntable({ manifest }: { manifest: LabManifest | null }) {
  const [idx, setIdx] = useState(0);
  const frames = manifest?.frame_files ?? [];
  const n = frames.length;
  const fps = manifest?.fps || 6;
  useEffect(() => {
    if (n === 0) return;
    const t = setInterval(() => setIdx((i) => (i + 1) % n), Math.round(1000 / fps));
    return () => clearInterval(t);
  }, [n, fps]);
  useEffect(() => {
    frames.forEach((f) => { const im = new Image(); im.src = LAB_BASE + f; });
  }, [frames]);
  if (!manifest || frames.length === 0) {
    return <div className="lab-stage lab-stage-empty">三维资产清单加载中…</div>;
  }
  return (
    <figure className="lab-stage">
      <img src={LAB_BASE + frames[idx]} alt="整机固定渲染转台序列"
           width={1024} height={768} draggable={false} />
      <figcaption className="lab-stage-caption">
        <span>{manifest.model_name}</span>
        <span>{manifest.config}</span>
        <span>导出 {manifest.exported_at.slice(0, 10)}</span>
        <span>源文件 {manifest.source_file.split("/").pop()} @ {manifest.source_git || manifest.source_md5.slice(0, 8)}</span>
      </figcaption>
    </figure>
  );
}

export default function LabPage({ onBack }: { onBack: () => void }) {
  const [payload, setPayload] = useState<LabPayload | null>(null);
  const [manifest, setManifest] = useState<LabManifest | null>(null);
  const [failed, setFailed] = useState(false);
  const [drawerKey, setDrawerKey] = useState<string | null>(null);
  const [filters, setFilters] = useState<LabFilters>(emptyFilters);
  const [activeDomain, setActiveDomain] = useState(0);
  const [activeId, setActiveId] = useState<string>("215g");   // I-202：机型可切换，默认二百一十五克
  const [eagleSrc, setEagleSrc] = useState<{ locus: string; excerpt: string } | null>(null);
  const domRefs = useRef<(HTMLElement | null)[]>([]);
  const linkedHash = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/lab/params");
      if (r.status === 401) { location.href = "/login"; return; }
      const d = await r.json();
      if (d.ok && d.result?.schema === "lab-payload-v2") {
        const rp: LabPayload = d.result;
        setPayload(rp);
        setFailed(false);
      }
    } catch {
      setPayload(null);
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    fetch(LAB_BASE + "manifest.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("manifest 缺失"))))
      .then(setManifest)
      .catch(() => setManifest(null));
  }, []);

  /* 滚动联动：当前域高亮索引带与轨道。 */
  useEffect(() => {
    const onScroll = () => {
      let cur = 0;
      domRefs.current.forEach((el, i) => {
        if (el && el.getBoundingClientRect().top <= window.innerHeight * 0.4) cur = i;
      });
      setActiveDomain(cur);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener("scroll", onScroll);
  }, [payload]);

  /* 深链接只在目标变化时定位，不随元数据轮询重复滚动。 */
  useEffect(() => {
    if (!payload) return;
    const followLink = () => {
      const hash = location.hash;
      const m = /[?&]p=([^&]+)/.exec(hash);
      if (!m) { linkedHash.current = null; return; }
      if (hash === linkedHash.current) return;
      try {
        const key = decodeURIComponent(m[1]);
        if (payload.params.some((p) => p.key === key)) {
          linkedHash.current = hash;
          setFilters((current) => revealTargetFilters(payload.params, key, current));
          setActiveId("215g");
          window.setTimeout(() => jumpAndFlash("lab-p-" + encodeURIComponent(key)), 300);
        }
      } catch {
        // 无效URL编码不影响整页读取。
      }
    };
    followLink();
    window.addEventListener("hashchange", followLink);
    return () => window.removeEventListener("hashchange", followLink);
  }, [payload]);

  const allParams = useMemo(() => payload ? sortedParams(payload.params, payload) : [], [payload]);
  const visibleParams = useMemo(() => payload ? selectParams(allParams, payload, filters) : [], [allParams, payload, filters]);
  const byFamily = useMemo(() => {
    const m = new Map<string, LabParam[]>();
    visibleParams.forEach((p) => {
      if (!m.has(p.family)) m.set(p.family, []);
      m.get(p.family)!.push(p);
    });
    return m;
  }, [visibleParams]);

  const byKey = useMemo(() => {
    const m = new Map<string, LabParam>();
    allParams.forEach((p) => m.set(p.key, p));
    return m;
  }, [allParams]);

  const drawer = currentParam(allParams, drawerKey);
  const openParam = useCallback((p: LabParam) => setDrawerKey(p.key), []);

  const openKey = useCallback((key: string) => {
    setDrawerKey(byKey.has(key) ? key : null);
  }, [byKey]);
  const jumpToKey = useCallback((key: string) => {
    if (!byKey.has(key)) return;
    setFilters((current) => revealTargetFilters(allParams, key, current));
    setActiveId("215g");
    window.setTimeout(() => jumpAndFlash("lab-p-" + encodeURIComponent(key)), 180);
  }, [allParams, byKey]);

  if (failed && !payload) {
    return (
      <div className="lab">
        <div className="lab-error">
          <h1>参数实验室</h1>
          <p>数据接口不可达，页面不显示任何缓存数值（数据源铁规矩）。</p>
          <button className="lab-btn" onClick={refresh}>重试</button>
          <button className="lab-btn lab-btn-minor" onClick={onBack}>返回作战地图</button>
        </div>
      </div>
    );
  }
  if (!payload) {
    return <div className="lab"><div className="lab-error">参数实验室加载中…</div></div>;
  }

  const firstOpen = payload.samples.find((s) => !s.locked) ?? payload.samples[0];
  const active = payload.samples.find((s) => s.id === activeId) ?? firstOpen;
  const domains = [...payload.domains].sort((a, b) => a.no - b.no || compareCodepoints(a.id, b.id));
  const familiesOf = (did: string) => payload.families.filter((f) => f.domain === did);
  const placeholdersOf = (fid: string) => payload.placeholders.filter((p) => p.family === fid);
  const filtering = hasFilters(filters);
  const familyCount = (fid: string) => byFamily.get(fid)?.length ?? 0;
  const domainCount = (did: string) => familiesOf(did).reduce((n, f) => n + familyCount(f.id), 0);

  const familyBlock = (fid: string) => {
    const fam = payload.families.find((f) => f.id === fid)!;
    const list = byFamily.get(fid) ?? [];
    const gainGroups = groupedParams(list, payload.loop_groups);
    return (
      <section className="lab-family" key={fid} id={"lab-fam-" + fid}>
        <header className="lab-family-head">
          <div className="lab-family-text">
            <h3>{fam.name}
              <small>{list.length} 项</small>
              {fam.derived && <span className="lab-family-tag">派生量 · 计算公式见公式墙</span>}
              <span className="lab-family-src" title="族划分依据">
                {"来源：" + (fam.source === "graph" ? "I-127 图字段" : fam.source === "cfd" ? "I-134 对账报告" : fam.source === "assert" ? "构建断言" : "阶段一规则表（暂定）")}
              </span>
            </h3>
            <p className="lab-family-lead">{fam.lead}</p>
          </div>
          {fid === "mass-cg" && <Turntable manifest={manifest} />}
        </header>
        {fid === "gain" ? (
          <>
            {payload.loop_groups.map((g) => {
              const members = gainGroups.groups.find((group) => group.id === g.id)?.members ?? [];
              if (members.length === 0) return null;
              const nums = members.map((m) => numeric(m.value)).filter((n): n is number => n != null && n > 0);
              return (
                <div className="lab-loop" key={g.id}>
                  <div className="lab-loop-head">
                    <span className="lab-loop-name">{g.name}</span>
                    {nums.length >= 2 && <span className="lab-loop-ratio">配比 {ratioOf(nums)}</span>}
                  </div>
                  <div className="lab-grid lab-grid-compact">
                    {members.map((p) => <ParamCard key={p.key} p={p} onOpen={openParam} />)}
                  </div>
                </div>
              );
            })}
            {(() => {
              const rest = gainGroups.rest;
              if (rest.length === 0) return null;
              return (
                <div className="lab-loop">
                  <div className="lab-loop-head"><span className="lab-loop-name">其余控制增益</span></div>
                  <div className="lab-grid">
                    {rest.map((p) => <ParamCard key={p.key} p={p} onOpen={openParam} />)}
                  </div>
                </div>
              );
            })()}
          </>
        ) : (
          <div className="lab-grid">
            {list.map((p) => <ParamCard key={p.key} p={p} onOpen={openParam} />)}
            {!filtering && placeholdersOf(fid).map((ph) => <PlaceholderCard key={ph.subclass} subclass={ph.subclass} text={ph.text} />)}
          </div>
        )}
      </section>
    );
  };

  const domainSection = (d: typeof domains[number], di: number) => {
    const fams = familiesOf(d.id);
    const grouped = d.groups ?? [];
    const groupedIds = new Set(grouped.flatMap((g) => g.families));
    const rest = fams.filter((f) => !groupedIds.has(f.id));
    return (
      <section className="lab-domain" key={d.id}
               id={"lab-dom-" + d.id}
               ref={(el) => { domRefs.current[di] = el; }}
               data-domain={d.id}>
        <header className="lab-dom-head">
          <div className="lab-dom-no">域 {d.no}</div>
          <h2>{d.name}<small>{domainCount(d.id)} 项</small></h2>
          <p className="lab-dom-lead">{d.lead}</p>
          {grouped.length > 0 ? (
            <div className="lab-fam-chips">
              {grouped.map((g) => (
                <div className="lab-chip-group" key={g.name}>
                  <span className="lab-chip-groupname">{g.name}</span>
                  {g.families.map((fid) => {
                    const f = fams.find((x) => x.id === fid);
                    return f ? (
                      <button key={fid} className="lab-chip"
                              onClick={() => jumpAndFlash("lab-fam-" + fid)}>
                        {f.name}<small>{familyCount(f.id)}</small>
                      </button>
                    ) : null;
                  })}
                </div>
              ))}
              {rest.length > 0 && (
                <div className="lab-chip-group">
                  {rest.map((f) => (
                    <button key={f.id} className="lab-chip"
                            onClick={() => jumpAndFlash("lab-fam-" + f.id)}>
                      {f.name}<small>{familyCount(f.id)}</small>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="lab-fam-chips">
              {fams.map((f) => (
                <button key={f.id} className="lab-chip"
                        onClick={() => jumpAndFlash("lab-fam-" + f.id)}>
                  {f.name}<small>{familyCount(f.id)}</small>
                </button>
              ))}
            </div>
          )}
        </header>
        {fams.map((f) => familyBlock(f.id))}
      </section>
    );
  };

  return (
    <div className="lab">
      <header className="lab-top">
        <div className="lab-title">参数实验室<small>全机参数陈列 · 第二版 意义索引</small></div>
        <nav className="lab-tabs" aria-label="机型选项卡">
          {payload.samples.map((s) => (
            <button key={s.id}
                    className={"lab-tab" + (s.id === active.id ? " active" : "") + (s.locked ? " locked" : "")}
                    disabled={s.locked}
                    aria-disabled={s.locked}
                    onClick={() => { if (!s.locked) setActiveId(s.id); }}>
              {s.name}
              {s.locked && <span className="lab-tab-hint">{s.hint}</span>}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        <span className="lab-top-meta">更新于 {payload.generated_at}</span>
        <button className="lab-btn lab-btn-minor" onClick={onBack}>返回作战地图</button>
      </header>

      <nav className="lab-indexband" aria-label="意义域索引">
        {domains.map((d, i) => (
          <button key={d.id}
                  className={"lab-band-chip" + (i === activeDomain ? " active" : "")}
                  onClick={() => domRefs.current[i]?.scrollIntoView({ behavior: "smooth", block: "start" })}>
            {d.name}<small>{domainCount(d.id)}</small>
          </button>
        ))}
      </nav>

      <div className="lab-hero">
        <h1>{active.name}</h1>
        <p className="lab-hero-tag">{active.id === "700g"
          ? active.tagline + " · 模型动作展示与几何陈列，候选状态未通过物理验证"
          : active.tagline + " · 按物理意义归纳的全部登记参数，变量名是别名，公式是法则。"}</p>
      </div>

      {active.id !== "700g" && <section className="lab-filters" aria-label="参数用途与多分类筛选">
        <div className="lab-filter-summary" aria-live="polite">
          显示 {visibleParams.length} / {allParams.length} 项参数
          <button className="lab-btn lab-btn-minor" onClick={() => setFilters(emptyFilters())}>显示全部 / 清除筛选</button>
        </div>
        <p className="lab-filter-note">同一分类内任选其一，不同分类同时满足；每个参数只计一次，分类计数可交叠。
          用途标签说明支持路径，不代表本次运行已启用或已经验证。
          {payload.metadata_schema !== "param-semantics-v1" ? " 接口尚未声明本版语义契约，缺失或不支持的语义明确保留为未核。" : ""}</p>
        <fieldset className="lab-filter-axis"><legend>用途</legend>
          <button className="lab-chip" aria-pressed={filters.usage === "all"}
            onClick={() => setFilters((f) => ({ ...f, usage: "all" }))}>全部<small>{allParams.length}</small></button>
          {(Object.keys(USAGE_LABELS) as (keyof typeof USAGE_LABELS)[]).map((usage) =>
            <button key={usage} className="lab-chip" aria-pressed={filters.usage === usage}
              onClick={() => setFilters((f) => ({ ...f, usage }))}>{USAGE_LABELS[usage]}
              <small>{allParams.filter((p) => usageOf(p) === usage).length}</small>
            </button>)}
        </fieldset>
        <details className="lab-filter-facet lab-filter-states"><summary>消费关系、复核与适用条件筛选</summary>
          <div className="lab-filter-statuses">
            <label>消费关系<select value={filters.consumer ?? "all"}
              onChange={(e) => setFilters((f) => ({ ...f, consumer: e.target.value }))}>
              <option value="all">全部消费关系</option>
              {Object.entries(CONSUMER_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select></label>
            {REVIEW_SLOTS.map((slot) => <label key={slot}>{SLOT_LABELS[slot]}复核
              <select value={filters.review?.[slot] ?? "all"}
                onChange={(e) => setFilters((f) => ({ ...f, review: { ...f.review, [slot]: e.target.value } }))}>
                <option value="all">全部{SLOT_LABELS[slot]}复核状态</option>
                {Object.entries(REVIEW_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select></label>)}
            <label>适用范围<select value={filters.sample_scope ?? "all"}
              onChange={(e) => setFilters((f) => ({ ...f, sample_scope: e.target.value }))}>
              <option value="all">全部适用范围</option>
              {Object.entries(SAMPLE_SCOPE_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select></label>
            <label>具体样本<select value={filters.sample_id ?? "all"}
              onChange={(e) => setFilters((f) => ({ ...f, sample_id: e.target.value }))}>
              <option value="all">全部，包括适用样本待核实项</option>
              {[...new Set([...allParams.flatMap(sampleIds), ...payload.samples.map((s) => s.id)])].sort(compareCodepoints)
                .map((id) => <option key={id} value={id}>{payload.samples.find((s) => s.id === id)?.name ?? id}</option>)}
            </select></label>
            <label>默认路径<select value={filters.default_path ?? "all"}
              onChange={(e) => setFilters((f) => ({ ...f, default_path: e.target.value }))}>
              <option value="all">全部默认路径状态</option>
              {Object.entries(DEFAULT_PATH_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select></label>
          </div>
        </details>
        <div className="lab-filter-facets">{AXES.map((axis) => {
          const counts = facetCounts(allParams, axis, filters);
          const selected = filters.facets[axis] ?? [];
          const ids = [...new Set([...counts.keys(), ...selected])].sort(compareCodepoints);
          return <details className="lab-filter-facet" key={axis}>
            <summary>{AXIS_LABELS[axis]}{selected.length ? "（已选" + selected.length + "）" : "（全部）"}</summary>
            <fieldset className="lab-filter-axis"><legend>{AXIS_LABELS[axis]}，可多选</legend>
              {ids.map((id) => <button className="lab-chip" key={id} aria-pressed={selected.includes(id)}
                onClick={() => setFilters((f) => {
                  const values = f.facets[axis] ?? [];
                  return { ...f, facets: { ...f.facets, [axis]: values.includes(id)
                    ? values.filter((value) => value !== id) : [...values, id] } };
                })}>{facetLabel(axis, id)}<small>{counts.get(id) ?? 0}</small></button>)}
            </fieldset>
          </details>;
        })}</div>
        {visibleParams.length === 0 && <p role="status">没有同时满足当前条件的参数；可减少筛选条件或显示全部。</p>}
      </section>}

      {active.id === "700g" ? (
        <EaglePreview node={payload.eaglex700}
                      onOpenSrc={(locus, excerpt) => setEagleSrc({ locus, excerpt })} />
      ) : (
        <FormulaWall formulas={payload.formulas} onJumpKey={jumpToKey} />
      )}

      {active.id !== "700g" && domains.map((d, i) => domainSection(d, i))}

      {eagleSrc && (
        <div className="lab-drawer-mask" onClick={() => setEagleSrc(null)}>
          <aside className="lab-drawer" onClick={(e) => e.stopPropagation()}
                 aria-label="几何来源浮层" style={{ maxWidth: 420 }}>
            <header className="lab-drawer-head">
              <div>
                <div className="lab-drawer-short">几何来源</div>
                <div className="lab-drawer-key">{eagleSrc.locus}</div>
              </div>
              <button className="lab-btn lab-btn-minor" onClick={() => setEagleSrc(null)}>关闭</button>
            </header>
            <div className="lab-drawer-body" style={{ padding: 16 }}>
              <p style={{ fontSize: 13 }}>{eagleSrc.excerpt}</p>
              <p style={{ fontSize: 12, color: "var(--tk-dim)" }}>
                来源链原文见 geometry_and_binding.json 的 provenance 节（随 eaglex700 数据节下发）。
              </p>
            </div>
          </aside>
        </div>
      )}

      <footer className="lab-foot">
        <span>在册参数 {payload.stats.total} 项、{payload.stats.families} 族、{payload.stats.domains} 域：盘点定级 {payload.stats.graph} 项，暂定定级 {payload.stats.rule} 项（待指挥部盘点核定），待归 {payload.stats.orphan} 项显式陈列。</span>
        <span>页面数值全部实时来自参数登记库只读接口，前端不存任何参数值。</span>
      </footer>

      <nav className="lab-rail" aria-label="域导航轨道">
        {domains.map((d, i) => (
          <button key={d.id}
                  className={"lab-rail-tick" + (i === activeDomain ? " active" : "")}
                  onClick={() => domRefs.current[i]?.scrollIntoView({ behavior: "smooth", block: "start" })}
                  aria-label={"跳到 " + d.name}>
            <span className="lab-rail-no">{d.no}</span>
            <span className="lab-rail-name">{d.name}</span>
          </button>
        ))}
      </nav>

      {drawer && (
        <Drawer p={drawer} payload={payload} onClose={() => setDrawerKey(null)} onOpenKey={openKey} />
      )}
    </div>
  );
}
