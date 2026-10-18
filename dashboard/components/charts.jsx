"use client";
import { useState } from "react";

export const AREA_KEYS = ["usability", "playback", "billing_support", "access"];
export const STACK_KEYS = [...AREA_KEYS, "other_topics"];
export const AREA_LABEL = { access: "Access", usability: "Usability", playback: "Playback (+downloads)", billing_support: "Billing / support", other_topics: "Other topics (catalog, general)" };
// Categorical slots 1-4 in fixed order (validated: adjacent CVD ΔE ≥ 9.1 light / 8.4 dark). Color follows the entity.
export const AREA_VAR = { usability: "var(--s1)", playback: "var(--s2)", billing_support: "var(--s3)", access: "var(--s4)", other_topics: "var(--s5)" };
export const fmt = (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString("en-US"));
export const pct = (a, b, d = 1) => (b ? (100 * a) / b : 0).toFixed(d);

export function Tip({ tip }) {
  if (!tip) return null;
  return <div className="tip" style={{ left: Math.min(tip.x + 14, (typeof window !== "undefined" ? window.innerWidth : 1200) - 280), top: tip.y + 14 }}>{tip.content}</div>;
}

/** Multi-series line chart on ONE axis, with switchable event annotations and a crosshair tooltip. */
export function LineChart({ months, series, height = 260, yLabel = "%", events = [], showEvents = true, partial = [], onMonth }) {
  const [tip, setTip] = useState(null);
  const [hover, setHover] = useState(null);
  const W = 760, H = height, P = { l: 44, r: 120, t: 18, b: 30 };
  const raw = Math.max(1, ...series.flatMap((s) => s.values));
  const stepv = [1, 2, 5, 10, 20, 25, 50].find((st) => raw / st <= 4) || 100;
  const max = Math.ceil((raw * 1.05) / stepv) * stepv;
  const x = (i) => P.l + (i * (W - P.l - P.r)) / Math.max(1, months.length - 1);
  const y = (v) => P.t + (H - P.t - P.b) * (1 - v / max);
  const ticks = Array.from({ length: Math.round(max / stepv) + 1 }, (_, k) => k * stepv);
  const labelY = series.map((s) => ({ key: s.key, y: y(s.values[s.values.length - 1]) })).sort((a, b) => a.y - b.y);
  for (let k = 1; k < labelY.length; k++) if (labelY[k].y - labelY[k - 1].y < 13) labelY[k].y = labelY[k - 1].y + 13;
  const ly = Object.fromEntries(labelY.map((l) => [l.key, l.y]));
  return (
    <div style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={series.map((s) => s.label).join(", ")}>
        {ticks.map((t) => <g key={t}><line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} stroke="var(--grid)" /><text x={P.l - 6} y={y(t) + 4} textAnchor="end">{t}{yLabel}</text></g>)}
        {partial.map((m) => { const i = months.indexOf(m); if (i < 0) return null; return <rect key={m} x={x(i) - 10} y={P.t} width={20} height={H - P.t - P.b} fill="var(--grid)" opacity=".45" />; })}
        {showEvents && events.map((e) => { const i = months.indexOf(e.month); if (i < 0) return null; return (
          <g key={e.id}><line x1={x(i)} x2={x(i)} y1={P.t - 6} y2={H - P.b} stroke="var(--text-muted)" strokeWidth="1" />
            <text x={x(i) + 3} y={P.t - 6} style={{ fontSize: 10, fill: "var(--text-secondary)" }}>{e.id}</text></g>); })}
        {series.map((s) => (
          <g key={s.key}>
            <path d={s.values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("")} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" />
            <text x={W - P.r + 8} y={ly[s.key] + 4} style={{ fontSize: 11, fill: "var(--text-primary)" }}>{s.short || s.label} {s.values[s.values.length - 1].toFixed(1)}{yLabel}</text>
            <circle cx={x(s.values.length - 1)} cy={y(s.values[s.values.length - 1])} r="4" fill={s.color} stroke="var(--surface-1)" strokeWidth="2" />
          </g>))}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={P.t} y2={H - P.b} stroke="var(--text-muted)" strokeWidth="1" />}
        {hover !== null && series.map((s) => <circle key={s.key} cx={x(hover)} cy={y(s.values[hover])} r="4" fill={s.color} stroke="var(--surface-1)" strokeWidth="2" />)}
        {months.map((m, i) => (
          <g key={m}>
            {(i % 3 === 0 || i === months.length - 1) && <text x={x(i)} y={H - 10} textAnchor="middle">{m}</text>}
            <rect x={x(i) - (W - P.l - P.r) / months.length / 2} y={P.t} width={(W - P.l - P.r) / months.length} height={H - P.t - P.b} fill="transparent"
              style={{ cursor: onMonth ? "pointer" : "default" }} onClick={() => onMonth && onMonth(m)}
              onMouseMove={(ev) => { setHover(i); const evs = events.filter((e) => e.month === m);
                setTip({ x: ev.clientX, y: ev.clientY, content: <><b>{m}</b>{partial.includes(m) ? " · partial month" : ""}
                  {series.map((s) => <div key={s.key}><span className="sw" style={{ background: s.color }} />{s.label}: <b>{s.values[i].toFixed(2)}{yLabel}</b>{s.counts ? ` (${fmt(s.counts[i])} of ${fmt(s.denoms[i])})` : ""}</div>)}
                  {evs.map((e) => <div key={e.id} className="muted" style={{ marginTop: 4 }}>{e.id}: {e.title}</div>)}</> }); }}
              onMouseLeave={() => { setHover(null); setTip(null); }} />
          </g>))}
      </svg>
      <div className="legend">{series.map((s) => <span key={s.key}><span className="sw" style={{ background: s.color }} />{s.label}</span>)}
        {showEvents && events.length > 0 && <span className="muted">│ vertical lines = external events (context, not cause) · shaded = partial month</span>}</div>
      <Tip tip={tip} />
    </div>
  );
}

/** Vertical bars (one series, slot 1) with an optional highlighted sub-portion (slot 2), 2px gaps. */
export function VolumeBars({ months, totals, part, partLabel, totalLabel, height = 170, events = [] }) {
  const [tip, setTip] = useState(null);
  const W = 760, H = height, P = { l: 44, r: 120, t: 14, b: 26 };
  const max = Math.max(...totals) * 1.05;
  const bw = (W - P.l - P.r) / months.length;
  const y = (v) => P.t + (H - P.t - P.b) * (1 - v / max);
  return (
    <div style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={totalLabel}>
        {[0, max / 2].map((t) => <g key={t}><line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} stroke="var(--grid)" /><text x={P.l - 6} y={y(t) + 4} textAnchor="end">{fmt(Math.round(t))}</text></g>)}
        {months.map((m, i) => {
          const x0 = P.l + i * bw + 1, w = bw - 2, rest = totals[i] - (part?.[i] || 0);
          return (
            <g key={m} onMouseMove={(ev) => setTip({ x: ev.clientX, y: ev.clientY, content: <><b>{m}</b><div>{totalLabel}: <b>{fmt(totals[i])}</b></div>{part && <div>{partLabel}: <b>{fmt(part[i])}</b> ({pct(part[i], totals[i])}%)</div>}{events.filter((e) => e.month === m).map((e) => <div key={e.id} className="muted">{e.id}: {e.title}</div>)}</> })} onMouseLeave={() => setTip(null)}>
              <rect x={x0} y={y(rest)} width={w} height={Math.max(0, y(0) - y(rest))} fill="var(--s1)" rx="2" />
              {part && part[i] > 0 && <rect x={x0} y={y(totals[i])} width={w} height={Math.max(0, y(rest) - y(totals[i]) - 2)} fill="var(--s2)" rx="2" />}
              {(i % 3 === 0 || i === months.length - 1) && <text x={x0 + w / 2} y={H - 8} textAnchor="middle">{m}</text>}
              {events.some((e) => e.month === m) && <text x={x0 + w / 2} y={y(totals[i]) - 4} textAnchor="middle" style={{ fontSize: 10, fill: "var(--text-secondary)" }}>{events.filter((e) => e.month === m).map((e) => e.id).join(",")}</text>}
              <rect x={x0} y={P.t} width={w} height={H - P.t - P.b} fill="transparent" />
            </g>);
        })}
      </svg>
      <div className="legend"><span><span className="sw" style={{ background: "var(--s1)" }} />{part ? "all other reviews" : totalLabel}</span>{part && <span><span className="sw" style={{ background: "var(--s2)" }} />{partLabel}</span>}</div>
      <Tip tip={tip} />
    </div>
  );
}

/** Heatmap table: rows x months, single-hue sequential ramp (blue), value in tooltip + optional text. */
const RAMP = ["var(--q0)", "var(--q1)", "var(--q2)", "var(--q3)", "var(--q4)", "var(--q5)"];
export function Heatmap({ rows, cols, value, label, rowLabel, bins, onRow, extra, colLabel = (c) => c, fmtVal = (v) => v.toFixed(1) + "%" }) {
  // bins: 5 ascending thresholds; step 0 = zero, step k = value >= bins[k-1]
  const [tip, setTip] = useState(null);
  const step = (v) => (v <= 0 ? 0 : 1 + bins.slice(1).filter((b) => v >= b).length);
  return (
    <div className="scroll" style={{ position: "relative" }}>
      <table className="heat"><thead><tr><th>{label}</th>{extra && <th>Pattern</th>}{cols.map((c) => <th key={c} className="hc">{colLabel(c)}</th>)}</tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r} className={onRow ? "click" : ""} onClick={() => onRow && onRow(r)}>
            <td style={{ whiteSpace: "nowrap" }}>{rowLabel(r)}</td>{extra && <td style={{ whiteSpace: "nowrap" }}>{extra(r)}</td>}
            {cols.map((c) => { const v = value(r, c); return (
              <td key={c} className="hcell" style={{ background: RAMP[step(v.v)] }}
                onMouseMove={(ev) => setTip({ x: ev.clientX, y: ev.clientY, content: <><b>{rowLabel(r)}</b><div>{colLabel(c)}: <b>{fmtVal(v.v)}</b>{v.n !== undefined ? ` (${fmt(v.n)} reviews)` : ""}</div></> })}
                onMouseLeave={() => setTip(null)} />); })}
          </tr>))}</tbody></table>
      <div className="legend">{RAMP.map((c, i) => <span key={i}><span className="sw" style={{ background: c, width: 18 }} />{i === 0 ? "0" : i === 1 ? `<${fmtVal(bins[1])}` : i < 5 ? `${fmtVal(bins[i - 1])}–${fmtVal(bins[i])}` : `≥${fmtVal(bins[4])}`}</span>).slice(0, 6)}</div>
      <Tip tip={tip} />
    </div>
  );
}

export function Delta({ from, to, unit = "pp", worseIsUp = true, digits = 1, neutral = false }) {
  const d = to - from;
  const worse = worseIsUp ? d > 0 : d < 0;
  if (Math.abs(d) < 1e-9) return <span className="muted">no change</span>;
  const mag = unit === "" && digits === 0 ? fmt(Math.abs(d)) : Math.abs(d).toFixed(digits);
  if (neutral) return <span className="muted">{d > 0 ? "▲" : "▼"} {mag}{unit} vs earlier period</span>;
  return <span className={worse ? "bad" : "ok"}>{d > 0 ? "▲" : "▼"} {mag}{unit} {worse ? "worse" : "better"}</span>;
}

/** Stacked monthly bars by category (counts or share), 2px surface gaps, click a month to select it. */
export function StackedBars({ months, rows, keys, labels, colors, mode = "count", range, onSelect, events = [], partial = [], height = 290 }) {
  const [tip, setTip] = useState(null);
  const W = 780, H = height, P = { l: 52, r: 12, t: 22, b: 30 };
  const totalOf = (r) => keys.reduce((a, k) => a + r.values[k], 0);
  const val = (r, k) => (mode === "count" ? r.values[k] : (100 * r.values[k]) / r.denom);
  const raw = Math.max(...rows.map((r) => keys.reduce((a, k) => a + val(r, k), 0)));
  const stepv = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000].find((st) => raw / st <= 5) || 10000;
  const max = Math.ceil(raw / stepv) * stepv;
  const bw = (W - P.l - P.r) / months.length;
  const y = (v) => P.t + (H - P.t - P.b) * (1 - v / max);
  const ticks = Array.from({ length: Math.round(max / stepv) + 1 }, (_, k) => k * stepv);
  return (
    <div style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Monthly complaints by category">
        {ticks.map((t) => <g key={t}><line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} stroke="var(--grid)" /><text x={P.l - 6} y={y(t) + 4} textAnchor="end">{mode === "count" ? fmt(t) : `${t}%`}</text></g>)}
        {rows.map((r, i) => {
          const x0 = P.l + i * bw + 2, w = bw - 4;
          let acc = 0;
          const sel = range && r.month >= range[0] && r.month <= range[1];
          const evs = events.filter((e) => e.month === r.month);
          return (
            <g key={r.month} style={{ cursor: "pointer" }} onClick={(ev) => onSelect && onSelect(r.month, ev.shiftKey)}
              onMouseMove={(ev) => setTip({ x: ev.clientX, y: ev.clientY, content: <><b>{r.month}</b>{partial.includes(r.month) ? " · partial month" : ""} — click: this month · shift-click: extend range
                {[...keys].reverse().map((k) => <div key={k}><span className="sw" style={{ background: colors[k] }} />{labels[k]}: <b>{fmt(r.values[k])}</b> ({pct(r.values[k], r.denom)}% of reviews)</div>)}
                <div className="muted">complaints {fmt(totalOf(r))} of {fmt(r.denom)} classified reviews</div>
                {evs.map((e) => <div key={e.id} className="muted">{e.id}: {e.title}</div>)}</> })}
              onMouseLeave={() => setTip(null)}>
              {sel && <rect x={x0 - 2} y={P.t - 4} width={w + 4} height={H - P.t - P.b + 4} fill="rgba(30,215,96,0.07)" stroke="var(--accent)" strokeWidth="1.5" rx="4" />}
              {keys.map((k) => {
                const v = val(r, k); const y1 = y(acc + v), y0 = y(acc); acc += v;
                const h = Math.max(0, y0 - y1 - 2);
                return h > 0 ? <rect key={k} x={x0} y={y1} width={w} height={h} fill={colors[k]} rx="2" opacity={range && !sel ? 0.5 : 1} /> : null;
              })}
              {evs.length > 0 && <text x={x0 + w / 2} y={y(acc) - 5} textAnchor="middle" style={{ fontSize: 10, fill: "var(--text-secondary)" }}>{evs.map((e) => e.id).join(",")}</text>}
              {(i % 2 === 0 || sel) && <text x={x0 + w / 2} y={H - 10} textAnchor="middle" style={sel ? { fill: "var(--text-primary)", fontWeight: 700 } : {}}>{r.month.slice(2)}</text>}
              <rect x={x0} y={P.t} width={w} height={H - P.t - P.b} fill="transparent" />
            </g>);
        })}
      </svg>
      <div className="legend">{keys.map((k) => <span key={k}><span className="sw" style={{ background: colors[k] }} />{labels[k]}</span>)}</div>
      <Tip tip={tip} />
    </div>
  );
}

/** Horizontal bar list for one month (single series per row, colored by the row's category identity). */
export function BarList({ rows, max, colorOf, valueLabel }) {
  return (
    <table><tbody>{rows.map((r) => (
      <tr key={r.key} className={r.onClick ? "click" : ""} onClick={r.onClick}>
        <td style={{ width: "34%" }}><span className="sw" style={{ background: colorOf(r) }} />{r.label}</td>
        <td><div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ height: 12, width: `${(78 * r.value) / Math.max(1, max)}%`, minWidth: 2, background: colorOf(r), borderRadius: "0 4px 4px 0" }} />
          <span style={{ whiteSpace: "nowrap" }}><b>{fmt(r.value)}</b> <span className="muted">{valueLabel(r)}</span></span></div></td>
      </tr>))}</tbody></table>
  );
}
