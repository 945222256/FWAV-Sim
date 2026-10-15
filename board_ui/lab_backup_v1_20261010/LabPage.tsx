/* lab/LabPage.tsx — 参数实验室页面（I-130 阶段一：陈列表示）。
 *
 * 设计依据：需求定稿 D-047、设计方案四稿（三稿视觉语言一字不改）。
 * 数据纪律：页面每一个数值实时来自 /api/lab/params 只读接口与
 * /app/lab/manifest.json 资产清单，前端源码不写死任何参数值。 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LabManifest, LabParam, LabPayload } from "./types";
import "./lab.css";

const STATUS_COLORS: Record<string, string> = {
  "已完成": "var(--lab-done)",
  "部分": "var(--lab-partial)",
  "缺失": "var(--lab-missing)",
  "待重算": "var(--lab-stale)",
};

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

function ParamCard({ p, onOpen }: { p: LabParam; onOpen: (p: LabParam) => void }) {
  return (
    <button className="lab-card" onClick={() => onOpen(p)}>
      <span className="lab-card-head">
        <StatusDot status={p.graph?.status} />
        <span className="lab-card-key">{p.key}</span>
        <span className="lab-card-value">
          {p.value === "" || p.value == null ? "（值空待盘点）" : p.value}
          {p.unit ? <span className="lab-card-unit"> {p.unit}</span> : null}
        </span>
      </span>
      <span className="lab-card-explain">{p.explanation}</span>
      <span className="lab-card-sub">
        <span>来源 {p.source ? p.source.slice(0, 14) + (p.source.length > 14 ? "…" : "") : "—"}</span>
        <span>有效域 {p.graph?.valid_domain || "待盘点"}</span>
        <span>变更 {p.since || "—"}</span>
        <span>下游 {p.downstream_count}</span>
      </span>
    </button>
  );
}

function PlaceholderCard({ subclass, text }: { subclass: string; text: string }) {
  return (
    <div className="lab-card lab-card-ph">
      <span className="lab-card-head">
        <StatusDot status="缺失" />
        <span className="lab-card-key">{subclass}（预留子类）</span>
      </span>
      <span className="lab-card-explain">{text}</span>
    </div>
  );
}

/* 控制配比：组内各增益按最大值归一，大数比小数的最简比标注（四稿三之一）。 */
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

function Drawer({ p, levelNames, onClose }: { p: LabParam; levelNames: Record<string, string>; onClose: () => void }) {
  const t = p.teaching;
  const history = Array.isArray(t.history) ? t.history : [];
  return (
    <div className="lab-drawer-mask" onClick={onClose}>
      <aside className="lab-drawer" onClick={(e) => e.stopPropagation()} aria-label="参数详情抽屉">
        <header className="lab-drawer-head">
          <div>
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
              {levelNames[String(p.level)] ?? ("第" + p.level + "级")} · {p.level_source === "graph" ? "盘点在册" : "暂定（" + p.level_rule + "）"}
              {" "}· 样本 {p.sample}
            </span>
          </p>

          <section className="lab-teach">
            <h4>完整定义</h4>
            <p>{t.definition}</p>
            <h4>它影响什么</h4>
            <p>{t.affects_note}</p>
            {t.affects.length > 0 && (
              <ul className="lab-downstream">
                {t.affects.map((k) => <li key={k}>{k}</li>)}
              </ul>
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
  const [drawer, setDrawer] = useState<LabParam | null>(null);
  const [activeZone, setActiveZone] = useState(0);
  const zoneRefs = useRef<(HTMLElement | null)[]>([]);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/lab/params");
      if (r.status === 401) { location.href = "/login"; return; }
      const d = await r.json();
      if (d.ok) { setPayload(d.result); setFailed(false); }
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

  useEffect(() => {
    const onScroll = () => {
      let cur = 0;
      zoneRefs.current.forEach((el, i) => {
        if (el && el.getBoundingClientRect().top <= window.innerHeight * 0.4) cur = i;
      });
      setActiveZone(cur);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener("scroll", onScroll);
  }, [payload]);

  const byLevel = useMemo(() => {
    const m = new Map<number, LabParam[]>();
    (payload?.params ?? []).forEach((p) => {
      if (p.level == null) return;
      if (!m.has(p.level)) m.set(p.level, []);
      m.get(p.level)!.push(p);
    });
    return m;
  }, [payload]);

  const byKey = useMemo(() => {
    const m = new Map<string, LabParam>();
    (payload?.params ?? []).forEach((p) => m.set(p.key, p));
    return m;
  }, [payload]);

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

  const active = payload.samples.find((s) => !s.locked) ?? payload.samples[0];
  const zones = payload.zones;
  const placeholdersOf = (lv: number) => payload.placeholders.filter((p) => p.level === lv);

  const levelSection = (lv: number, compact = false) => {
    const list = byLevel.get(lv) ?? [];
    const note = payload.empty_level_notes[String(lv)];
    return (
      <section className="lab-level" key={lv} data-level={lv}>
        <h3>{payload.level_names[String(lv)] ?? "第" + lv + "级"}
          <small>{list.length} 项{compact ? "（摘要）" : ""}</small>
        </h3>
        <div className={compact ? "lab-grid lab-grid-compact" : "lab-grid"}>
          {list.map((p) => <ParamCard key={p.key} p={p} onOpen={setDrawer} />)}
          {placeholdersOf(lv).map((ph) => <PlaceholderCard key={ph.subclass} subclass={ph.subclass} text={ph.text} />)}
        </div>
        {note && <p className="lab-level-note">{note}</p>}
      </section>
    );
  };

  const controlZone = () => {
    const l7 = byLevel.get(7) ?? [];
    const grouped = new Set(payload.loop_groups.flatMap((g) => g.keys));
    const rest = l7.filter((p) => !grouped.has(p.key));
    return (
      <>
        <section className="lab-level">
          <h3>{payload.level_names["7"]}<small>{l7.length} 项 · 按通道分组</small></h3>
          {payload.loop_groups.map((g) => {
            const members = g.keys.map((k) => byKey.get(k)).filter(Boolean) as LabParam[];
            if (members.length === 0) return null;
            const nums = members.map((m) => numeric(m.value)).filter((n): n is number => n != null && n > 0);
            return (
              <div className="lab-loop" key={g.id}>
                <div className="lab-loop-head">
                  <span className="lab-loop-name">{g.name}</span>
                  {nums.length >= 2 && <span className="lab-loop-ratio">配比 {ratioOf(nums)}</span>}
                </div>
                <div className="lab-grid lab-grid-compact">
                  {members.map((p) => <ParamCard key={p.key} p={p} onOpen={setDrawer} />)}
                </div>
              </div>
            );
          })}
          <div className="lab-loop">
            <div className="lab-loop-head"><span className="lab-loop-name">其余控制族</span></div>
            <div className="lab-grid">
              {rest.map((p) => <ParamCard key={p.key} p={p} onOpen={setDrawer} />)}
            </div>
          </div>
        </section>
        {levelSection(8)}
        {levelSection(9)}
      </>
    );
  };

  return (
    <div className="lab">
      <header className="lab-top">
        <div className="lab-title">参数实验室<small>全机参数陈列 · 阶段一 陈列表示</small></div>
        <nav className="lab-tabs" aria-label="机型选项卡">
          {payload.samples.map((s) => (
            <button key={s.id}
                    className={"lab-tab" + (s.id === active.id ? " active" : "") + (s.locked ? " locked" : "")}
                    disabled={s.locked}
                    aria-disabled={s.locked}>
              {s.name}
              {s.locked && <span className="lab-tab-hint">{s.hint}</span>}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        <span className="lab-top-meta">更新于 {payload.generated_at}</span>
        <button className="lab-btn lab-btn-minor" onClick={onBack}>返回作战地图</button>
      </header>

      {zones.map((z, zi) => (
        <section className={"lab-zone" + (z.layout === "core" ? " lab-zone-core" : "")}
                 key={z.id}
                 ref={(el) => { zoneRefs.current[zi] = el; }}
                 id={"lab-zone-" + z.no}
                 data-zone={z.id}>
          {z.layout === "core" ? (
            <>
              <div className="lab-hero">
                <h1>{active.name}</h1>
                <p className="lab-hero-tag">{active.tagline} · {z.lead}</p>
              </div>
              <div className="lab-core-grid">
                <div className="lab-core-col">
                  {levelSection(0)}
                  {levelSection(1)}
                </div>
                <div className="lab-core-center">
                  <Turntable manifest={manifest} />
                </div>
                <div className="lab-core-col">
                  {levelSection(2, true)}
                  <p className="lab-core-more">第二级完整陈列见下方第二区。</p>
                </div>
              </div>
            </>
          ) : (
            <>
              <div className="lab-zone-head">
                <div className="lab-zone-no">第{z.no}区</div>
                <h2>{z.title}</h2>
                <p className="lab-zone-lead">{z.lead}</p>
                <div className="lab-zone-levels">
                  {z.levels.map((lv) => (
                    <span key={lv} className="lab-zone-level">
                      {payload.level_names[String(lv)]}
                      <small>{byLevel.get(lv)?.length ?? 0} 项</small>
                    </span>
                  ))}
                </div>
              </div>
              <div className="lab-zone-body">
                {z.id === "control" ? controlZone() : z.levels.map((lv) => levelSection(lv))}
              </div>
            </>
          )}
        </section>
      ))}

      <footer className="lab-foot">
        <span>在册参数 {payload.stats.total} 项：盘点定级 {payload.stats.graph} 项，暂定定级 {payload.stats.rule} 项（待指挥部盘点核定）。</span>
        <span>页面数值全部实时来自参数登记库只读接口，前端不存任何参数值。</span>
      </footer>

      <nav className="lab-rail" aria-label="级联轨道">
        {zones.map((z, i) => (
          <button key={z.id}
                  className={"lab-rail-tick" + (i === activeZone ? " active" : "")}
                  onClick={() => zoneRefs.current[i]?.scrollIntoView({ behavior: "auto", block: "start" })}
                  aria-label={"跳到第" + z.no + "区 " + z.title}>
            <span className="lab-rail-no">{z.no}</span>
            <span className="lab-rail-name">{z.title}</span>
          </button>
        ))}
      </nav>

      {drawer && <Drawer p={drawer} levelNames={payload.level_names} onClose={() => setDrawer(null)} />}
    </div>
  );
}
