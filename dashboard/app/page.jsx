"use client";
import { useEffect, useMemo, useState, Fragment } from "react";
import { LineChart, VolumeBars, Heatmap, Delta, StackedBars, BarList, AREA_KEYS, STACK_KEYS, AREA_LABEL, AREA_VAR, fmt, pct } from "@/components/charts";

const TOPIC_ORDER = ["access", "usability", "playback", "downloads", "catalog", "billing", "support", "other"];
const short = (iid) => iid.replace("billing.premium_only_controls", "billing · premium-only controls").replace(".", " · ").replaceAll("_", " ");

async function get(path, run) {
  const u = run ? `${path}${path.includes("?") ? "&" : "?"}run=${encodeURIComponent(run)}` : path;
  const r = await fetch(u);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function inline(text, onClaim, claims) {
  // **bold**, [C1] claim chips, review ids as monospace
  const parts = (text || "").split(/(\*\*[^*]+\*\*|`[^`]+`|\[C\d+\]|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b)/g);
  return parts.map((p, i) => {
    if (/^\*\*[^*]+\*\*$/.test(p)) return <strong key={i}>{inline(p.slice(2, -2), onClaim, claims)}</strong>;
    if (/^`[^`]+`$/.test(p)) return <code key={i}>{p.slice(1, -1)}</code>;
    const m = p.match(/^\[(C\d+)\]$/);
    if (m) {
      const c = claims.find((x) => x.claim_id === m[1]);
      return <button key={i} className="chip" title={c ? `${c.issue_id} · ${c.metric} = ${c.value} (verified against ranking.csv)` : "claim"} onClick={() => c && onClaim(c.issue_id)}>{m[1]}</button>;
    }
    if (/^[0-9a-f]{8}-/.test(p)) return <code key={i} title={p} style={{ fontSize: 11 }}>{p.slice(0, 8)}…</code>;
    return <Fragment key={i}>{p}</Fragment>;
  });
}

function Markdown({ text, onClaim, claims }) {
  const blocks = (text || "").split(/\n{2,}/);
  return <div className="memo">{blocks.map((b, i) => {
    const t = b.trim();
    if (t.startsWith("### ")) return <h3 key={i}>{inline(t.slice(4), onClaim, claims)}</h3>;
    if (t.startsWith("## ")) return <h2 key={i}>{inline(t.slice(3), onClaim, claims)}</h2>;
    if (t.startsWith("# ")) return <h2 key={i}>{inline(t.slice(2), onClaim, claims)}</h2>;
    if (/^[-*] /m.test(t)) return <ul key={i}>{t.split(/\n/).map((l, k) => <li key={k}>{inline(l.replace(/^[-*]\s+/, ""), onClaim, claims)}</li>)}</ul>;
    if (/^\d+\. /.test(t)) return <ol key={i}>{t.split(/\n/).map((l, k) => <li key={k}>{inline(l.replace(/^\d+\.\s+/, ""), onClaim, claims)}</li>)}</ol>;
    return <p key={i}>{inline(t, onClaim, claims)}</p>;
  })}</div>;
}

function IssueDetail({ run, issueId }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { if (issueId) { setD(null); get(`/api/issues/${encodeURIComponent(issueId)}`, run).then(setD).catch((e) => setErr(String(e))); } }, [run, issueId]);
  if (!issueId) return <div className="card muted">Select an issue in the ranking (or a claim chip in the recommendation) to see its supporting reviews.</div>;
  if (err) return <div className="card bad">{err}</div>;
  if (!d) return <div className="card muted">Loading {issueId}…</div>;
  const total = d.severity.reduce((s, x) => s + x.n, 0);
  return (
    <div className="card">
      <div className="muted">Rank #{d.ranking?.rank} · <code>{d.issue.issue_id}</code></div>
      <h2 style={{ marginTop: 4 }}>{d.issue.title}</h2>
      <div className="sub">{d.issue.description}</div>
      <p><span className="badge">{fmt(d.ranking?.complaint_count)} complaints</span><span className="badge">severity sum {fmt(d.ranking?.severity_sum)}</span>
        <span className="badge">mean {d.ranking?.mean_severity}</span><span className="badge">{fmt(d.cancellations)} with cancellation intent</span></p>
      <h3>Severity mix</h3>
      <div style={{ display: "flex", gap: 2, height: 14, marginBottom: 4 }}>
        {d.severity.map((s) => <div key={s.severity} title={`severity ${s.severity}: ${s.n}`} style={{ flex: s.n, background: ["#cde2fb", "#9ec5f4", "#5598e7", "#256abf", "#0d366b"][s.severity - 1], borderRadius: 3 }} />)}
      </div>
      <div className="muted">{d.severity.map((s) => `sev ${s.severity}: ${s.n} (${((100 * s.n) / total).toFixed(0)}%)`).join(" · ")}</div>
      <h3 style={{ marginTop: 12 }}>Evidence (exact quotes, highest severity first)</h3>
      <div style={{ maxHeight: 420, overflow: "auto" }}>
        {d.members.map((m) => (
          <div className="quote" key={m.review_id}>“{m.evidence_quote}”
            <span className="muted">{m.review_id} · {m.intent} · sev {m.severity} · {m.review_timestamp?.slice(0, 10)}{m.cache_source_id ? ` · reused result of ${m.cache_source_id.slice(0, 8)}` : ""}</span></div>
        ))}
      </div>
    </div>
  );
}

function Evidence({ ev, info }) {
  if (!ev) return null;
  const v = ev.verifier?.summary, g = ev.golden, inj = ev.injection, sp = ev.spotcheck, u = ev.usage || {};
  const pct = (x) => (x === undefined || x === null ? "—" : `${(100 * x).toFixed(1)}%`);
  const spend = Object.values(u).reduce((s, x) => s + x.cost_usd, 0);
  return (
    <div className="grid two">
      <div className="card">
        <h2>Quality checks</h2>
        <table><tbody>
          <tr><td>Independent verifier (Sonnet, blind re-label, n={v?.n ?? "—"})</td><td className="num">topic {pct(v?.agreement?.topic)} · intent {pct(v?.agreement?.intent)} · severity {pct(v?.agreement?.severity_exact)}</td></tr>
          <tr><td>Planted wrong labels caught by code comparison</td><td className="num">{ev.planted_error ? `${ev.planted_error.flagged}/${ev.planted_error.planted}` : "—"}</td></tr>
          <tr><td>Golden 50 (human, blind)</td><td className="num">topic {pct(g?.agreement?.topic)} · intent {pct(g?.agreement?.intent)} · severity {pct(g?.agreement?.severity_exact)} · MAE {g?.severity_mae ?? "—"}</td></tr>
          <tr><td>Prompt-injection cases resisted (synthetic)</td><td className="num">{inj ? `${inj.passed}/${inj.cases}` : "—"}</td></tr>
          <tr><td>Human spot-check of 500-run (labels visible)</td><td className="num">{sp ? `${sp.records_fully_accepted}/${sp.reviewed} fully accepted` : "—"}</td></tr>
        </tbody></table>
        <p className="muted">Golden disagreements are analyzed case by case in the repository (evals/golden/error_analysis.md). Synthetic tests are excluded from business numbers.</p>
      </div>
      <div className="card">
        <h2>Run cost & usage (actual provider usage)</h2>
        <div className="scroll"><table>
          <thead><tr><th>Role · model</th><th className="num">Calls (failed)</th><th className="num">In / cache-read / out tokens</th><th className="num">USD</th></tr></thead>
          <tbody>{Object.entries(u).map(([k, x]) => <tr key={k}><td>{k.replace("|", " · ")}</td><td className="num">{fmt(x.calls)} ({x.failed})</td>
            <td className="num">{fmt(x.input_tokens)} / {fmt(x.cache_read)} / {fmt(x.output_tokens)}</td><td className="num">${x.cost_usd.toFixed(4)}</td></tr>)}
            <tr><td><b>Total</b></td><td /><td /><td className="num"><b>${spend.toFixed(4)}</b></td></tr></tbody>
        </table></div>
        <p className="muted">Input SHA-256 {info?.input_sha256?.slice(0, 16)}… · wall-clock of last invocation {info?.wall ? `${Math.round(info.wall)} s` : "—"}</p>
      </div>
    </div>
  );
}


function windowStats(ins, which) {
  const w = Object.values(ins.comparable_windows.windows)[which];
  const [a, b] = Object.keys(ins.comparable_windows.windows)[which].match(/\d{4}-\d{2}/g);
  const rows = ins.monthly.filter((m) => m.month >= a && m.month <= b);
  const n = rows.reduce((s, r) => s + r.classified, 0);
  return { name: Object.keys(ins.comparable_windows.windows)[which], w, n,
    sev4: rows.reduce((s, r) => s + r.severity_4_plus, 0), cancel: rows.reduce((s, r) => s + r.cancellations, 0) };
}

const MONTH_NAME = (m) => new Date(`${m}-15T00:00:00Z`).toLocaleString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
const MONTH_SHORT = (m) => new Date(`${m}-15T00:00:00Z`).toLocaleString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
const PERIOD = (f, t) => (!f ? "—" : f === t ? MONTH_NAME(f) : `${MONTH_SHORT(f)} – ${MONTH_SHORT(t)}`);

function SummaryStrip({ md }) {
  if (!md) return <div className="strip">{Array.from({ length: 6 }, (_, i) => <div key={i} className="card muted">…</div>)}</div>;
  const c = md.current, p = md.prior;
  const share = (x, n) => (n ? (100 * x) / n : 0);
  const items = [
    ["Reviews classified", fmt(c.classified), p && <Delta from={p.classified} to={c.classified} unit="" digits={0} worseIsUp={false} neutral />, c.n - c.classified ? `${fmt(c.n - c.classified)} quarantined · 100k sample` : "in the 100k sample"],
    ["Complaints", fmt(c.complaints), p && <Delta from={share(p.complaints, p.classified)} to={share(c.complaints, c.classified)} />, `${share(c.complaints, c.classified).toFixed(1)}% of reviews`],
    ["Blocking issues (sev 4+)", fmt(c.sev4), p && <Delta from={share(p.sev4, p.classified)} to={share(c.sev4, c.classified)} digits={2} />, `${share(c.sev4, c.classified).toFixed(2)}% of reviews`],
    ["Stated intent to leave", fmt(c.cancellations), p && <Delta from={share(p.cancellations, p.classified)} to={share(c.cancellations, c.classified)} digits={2} />, `${share(c.cancellations, c.classified).toFixed(2)}% of reviews`],
    ["Praise", fmt(c.praise), p && <Delta from={share(p.praise, p.classified)} to={share(c.praise, c.classified)} worseIsUp={false} />, `${share(c.praise, c.classified).toFixed(1)}% of reviews`],
    ["Avg sentiment (−1…1)", c.avg_sentiment?.toFixed(2), p && <Delta from={p.avg_sentiment} to={c.avg_sentiment} unit="" digits={2} worseIsUp={false} />, "model-scored, per review"],
  ];
  return (<>
    <div className="strip">{items.map(([l, v, d, sub]) => (
      <div className="card" key={l}><div className="l">{l}</div><div className="v">{v}</div><div className="d">{d || <span className="muted">no earlier period</span>}</div><div className="muted">{sub}</div></div>))}</div>
    <div className="muted" style={{ marginBottom: 6 }}><b style={{ color: "var(--text-primary)" }}>{PERIOD(md.from, md.to)}</b> ({md.span} month{md.span > 1 ? "s" : ""}) vs {md.prior_from ? `${PERIOD(md.prior_from, md.prior_to)} (${md.prior_kind})` : "no earlier data"} · changes are in share of reviews (pp) · data {md.first_review?.slice(0, 10)} → {md.last_review?.slice(0, 10)}
      {md.includes_partial ? <span className="badge partial" style={{ marginLeft: 6 }}>includes a partial month</span> : null}</div>
  </>);
}

function MonthView({ md, ins, memo, pick, go, setRange }) {
  if (!md || !ins) return <div className="card muted">Loading period…</div>;
  const inRange = (m) => m >= md.from && m <= md.to;
  const c = md.current, p = md.prior;
  const prevIssue = Object.fromEntries((p?.issues || []).map((i) => [i.issue_id, i.n]));
  const sh = (x, n) => (n ? (100 * x) / n : 0);
  const pp = (d) => `${d > 0 ? "▲ +" : d < 0 ? "▼ " : ""}${d.toFixed(2)} pp`;
  const partialM = md.includes_partial;
  const label = PERIOD(md.from, md.to);
  const catRows = STACK_KEYS.map((k) => ({ key: k, label: AREA_LABEL[k], value: c.areas[k].complaints, prev: p?.areas[k]?.complaints }));
  const anomalies = ins.anomalies.filter((a) => inRange(a.month));
  const events = ins.events.events.filter((e) => inRange(e.month));
  const barRows = ins.monthly.filter((m) => inRange(m.month)).map((m) => { const inA = AREA_KEYS.reduce((t, k) => t + m.areas[k], 0);
    return { month: m.month, denom: m.classified, values: { ...m.areas, other_topics: m.complaints - inA } }; });
  const m = memo.memo;
  const sevTotal = c.severity.reduce((s2, x) => s2 + x.n, 0);
  return (<>
    <div className="grid two" style={{ marginBottom: 12 }}>
      <div className="card"><h2>Complaints by category — {label}</h2>
        <BarList rows={catRows} max={Math.max(...catRows.map((r) => r.value))} colorOf={(r) => AREA_VAR[r.key]}
          valueLabel={(r) => `${pct(r.value, c.classified)}% of reviews${r.prev !== undefined ? ` · ${pp(sh(r.value, c.classified) - sh(r.prev, p.classified))} vs earlier period` : ""}`} />
        {partialM && <p className="muted">Includes a partial month: compare shares, not raw counts.</p>}
        <h3 style={{ marginTop: 12 }}>Severity of complaints in this period</h3>
        <div style={{ display: "flex", gap: 2, height: 14 }}>{c.severity.map((x) => <div key={x.severity} title={`severity ${x.severity}: ${x.n}`} style={{ flex: x.n, background: `var(--q${x.severity})`, borderRadius: 3 }} />)}</div>
        <div className="muted">{c.severity.map((x) => `sev ${x.severity}: ${fmt(x.n)} (${pct(x.n, sevTotal, 0)}%)`).join(" · ")}</div>
      </div>
      <div className="card"><h2>What’s driving {label}</h2>
        {anomalies.length === 0 && events.length === 0 && <p className="muted">No anomaly against the expected range in this period — patterns are in line with the preceding months.</p>}
        <div style={{ maxHeight: 420, overflow: "auto" }}>
        {anomalies.map((a) => (<div key={a.month + a.series} className={`card alert ${a.ratio < 3 ? "watch" : ""}`} style={{ marginBottom: 8, padding: 10 }}>
          {md.span > 1 && <span className="muted">{a.month} · </span>}<b>{a.label}</b>: {a.share_pct !== null ? `${a.share_pct}% of reviews vs ~${a.expected_share_pct}% expected` : `${fmt(a.value)} reviews vs ~${fmt(a.expected_value)} expected`} <span className="pill bad">×{a.ratio}</span>
          <div>{a.drivers.slice(0, 2).map((d) => <button key={d.issue} className="chip" onClick={() => pick(d.issue)}>{d.issue}</button>)}</div></div>))}
        {events.map((e) => <div key={e.id} className="sub" style={{ marginTop: 6 }}><span className="pill">{e.id}</span><b>{e.title}</b> ({e.date}) — external context, not proven cause. {e.sources.map((u, i) => <a key={u} href={u} target="_blank" rel="noreferrer">[{i + 1}]</a>)}</div>)}
      </div></div>
    </div>
    {md.span > 1 && <div className="card" style={{ marginBottom: 12 }}><h2>Month by month within {label}</h2>
      <StackedBars months={barRows.map((r) => r.month)} rows={barRows} keys={STACK_KEYS} labels={AREA_LABEL} colors={AREA_VAR} mode="count" height={200}
        onSelect={(m) => setRange([m, m])} events={ins.events.events} partial={ins.monthly.filter((m) => m.partial).map((m) => m.month)} />
      <p className="muted">Complaints per month by category; click a month to focus on it.</p></div>}
    <div className="grid two" style={{ marginBottom: 12 }}>
      <div className="card"><h2>Top issues in this period</h2><div className="scroll"><table>
        <thead><tr><th>Issue</th><th className="num">Complaints</th><th className="num">% of reviews</th><th className="num">vs earlier (pp)</th><th className="num">Mean sev</th><th className="num">Intent to leave</th></tr></thead>
        <tbody>{c.issues.map((i) => { const d = sh(i.n, c.classified) - sh(prevIssue[i.issue_id] || 0, p?.classified || 1); return (
          <tr key={i.issue_id} className="click" onClick={() => pick(i.issue_id)}><td><div>{i.title || i.issue_id}</div><div className="muted"><code>{i.issue_id}</code></div></td>
            <td className="num">{fmt(i.n)}</td><td className="num">{pct(i.n, c.classified)}%</td><td className={`num ${d > 0.05 ? "bad" : d < -0.05 ? "ok" : ""}`}>{p ? pp(d) : "—"}</td>
            <td className="num">{i.mean_sev.toFixed(2)}</td><td className="num">{fmt(i.cancellations)}</td></tr>); })}</tbody></table></div></div>
      <div className="card"><h2>Most severe specific complaints in this period</h2>
        {md.examples.map((e) => <div className="quote" key={e.review_id}>“{e.evidence_quote}”
          <span className="muted">sev {e.severity} · {e.intent} · {e.issue_id ? <button className="chip" onClick={() => pick(e.issue_id)}>{e.issue_id}</button> : e.topic} · {e.review_id.slice(0, 8)}</span></div>)}</div>
    </div>
    {m && <div className="card rec"><div className="muted">Quarter-level recommendation (memo agent, whole period)</div>
      <h2 style={{ fontSize: 17, margin: "4px 0" }}>{m.headline}</h2>
      <div className="grid three">{(m.recommendations || []).slice(0, 3).map((r, i) => <div key={i}><b>{i + 1}. {r.title}</b><div className="sub">{inline(r.detail, pick, memo.claims)}</div></div>)}</div>
      <p style={{ marginBottom: 0 }}><button className="chip" onClick={() => go("priorities")}>Full memo, alternatives & claims →</button></p></div>}
  </>);
}

function SixMonth({ ins }) {
  const A = windowStats(ins, 0), B = windowStats(ins, 1);
  const kpis = [
    ["Complaint share", parseFloat(A.w.complaint_share), parseFloat(B.w.complaint_share)],
    ["Billing / support complaints", parseFloat(A.w.areas.billing_support.share_of_classified), parseFloat(B.w.areas.billing_support.share_of_classified)],
    ["Blocking (sev 4+) complaints", +(100 * A.sev4 / A.n).toFixed(2), +(100 * B.sev4 / B.n).toFixed(2)],
    ["Stated intent to leave", +(100 * A.cancel / A.n).toFixed(2), +(100 * B.cancel / B.n).toFixed(2)],
  ];
  return (<div className="card" style={{ marginBottom: 12 }}><h2>Six-month comparison <span className="muted" style={{ fontWeight: 400 }}>· {A.name} vs {B.name} (full months, share of reviews)</span></h2>
    <div className="grid four">{kpis.map(([l, a, b]) => <div key={l}><div className="muted">{l}</div><div style={{ fontSize: 20, fontWeight: 700 }}>{b.toFixed(1)}%</div><div className="d"><Delta from={a} to={b} /> <span className="muted">vs {a.toFixed(1)}%</span></div></div>)}</div></div>);
}

function AreaTable({ byArea, recurring }) {
  if (!byArea) return null;
  const rows = AREA_KEYS.map((k) => [k, byArea[k]]);
  const max = Math.max(...rows.map(([, a]) => a.severity_sum));
  return (<div className="scroll"><table>
    <thead><tr><th>Area</th><th className="num">Complaints</th><th className="num">Mean sev</th><th className="num">Sev 4+</th><th className="num">Intent to leave</th><th className="barcell">Severity sum</th></tr></thead>
    <tbody>{rows.map(([k, a]) => (
      <tr key={k}><td><span className="sw" style={{ background: AREA_VAR[k] }} />{AREA_LABEL[k]}</td><td className="num">{fmt(a.complaint_count)}</td>
        <td className="num">{Number(a.mean_severity).toFixed(2)}</td><td className="num">{fmt(a.severity_4_plus)}</td><td className="num">{fmt(a.cancellation_count)}</td>
        <td className="barcell"><div style={{ display: "flex", alignItems: "center", gap: 6 }}><div className="bar" style={{ width: `${(70 * a.severity_sum) / max}%` }} /><span className="muted">{fmt(a.severity_sum)}</span></div></td></tr>))}</tbody>
  </table></div>);
}

function Trends({ ins, pick, range, onBar }) {
  const [showEv, setShowEv] = useState(true);
  const [mode, setMode] = useState("areas");
  const [barMode, setBarMode] = useState("count");
  const barRows = ins.monthly.map((m) => { const inA = AREA_KEYS.reduce((t, k) => t + m.areas[k], 0);
    return { month: m.month, denom: m.classified, values: { ...m.areas, other_topics: m.complaints - inA } }; });
  const months = ins.months;
  const partial = ins.monthly.filter((m) => m.partial).map((m) => m.month);
  const den = ins.monthly.map((m) => m.classified);
  const areaSeries = AREA_KEYS.map((k) => ({ key: k, label: AREA_LABEL[k], short: AREA_LABEL[k].split(" ")[0], color: AREA_VAR[k],
    values: ins.monthly.map((m) => (100 * m.areas[k]) / m.classified), counts: ins.monthly.map((m) => m.areas[k]), denoms: den }));
  const focus = ["billing.premium_only_controls", "usability.redesign_update", "playback.stops_or_skips", "access.login_failure"];
  const issueSeries = focus.map((i, k) => ({ key: i, label: i, short: i.split(".")[1].split("_")[0], color: ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"][k],
    values: ins.monthly.map((m) => (100 * (m.issues[i] || 0)) / m.classified), counts: ins.monthly.map((m) => m.issues[i] || 0), denoms: den }));
  const evs = ins.events.events;
  return (<>
    <div className="card" style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h2>Complaints per month by category</h2>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span className="seg"><button className={barMode === "count" ? "on" : ""} onClick={() => setBarMode("count")}>Number of reviews</button><button className={barMode === "share" ? "on" : ""} onClick={() => setBarMode("share")}>% of reviews</button></span>
          <label className="muted"><input type="checkbox" checked={showEv} onChange={(e) => setShowEv(e.target.checked)} /> show events</label></div></div>
      <StackedBars months={months} rows={barRows} keys={STACK_KEYS} labels={AREA_LABEL} colors={AREA_VAR} mode={barMode} range={range}
        onSelect={onBar} events={showEv ? evs : []} partial={partial} />
      <p className="muted">Highlighted = selected period. Click a month to open it; shift-click to extend the period to that month. Counts are complaint/cancellation reviews in the 100k sample (≈15% of all reviews); “% of reviews” divides by all classified reviews that month, which removes the effect of review-volume spikes.</p>
    </div>
    <SixMonth ins={ins} />
    <div className="card" style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h2>Complaint share by month</h2>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span className="seg"><button className={mode === "areas" ? "on" : ""} onClick={() => setMode("areas")}>Areas</button><button className={mode === "issues" ? "on" : ""} onClick={() => setMode("issues")}>Key issues</button></span></div></div>
      <LineChart months={months} series={mode === "areas" ? areaSeries : issueSeries} events={evs} showEvents={showEv} partial={partial} />
      <p className="muted">Share = complaint/cancellation reviews in that area ÷ all classified reviews that month (hover for numerators and denominators). One y-axis; partial months shaded.</p>
    </div>
    <div className="grid two" style={{ marginBottom: 12 }}>
      <div className="card"><h2>Review volume & the boycott wave</h2>
        <VolumeBars months={months} totals={den} part={ins.monthly.map((m) => m.boycott_political)} partLabel="boycott / political reviews" totalLabel="classified reviews" events={showEv ? evs : []} />
        <p className="muted">2023-07 volume is ~3× normal, driven by boycott/political reviews that are not product feedback. They are kept out of product priorities (topic “other”, intent mostly unclear).</p></div>
      <div className="card"><h2>Event timeline</h2>
        {evs.map((e) => (<div key={e.id} style={{ marginBottom: 10 }}>
          <div><span className="pill">{e.id}</span><b>{e.title}</b> <span className="muted">{e.date}</span></div>
          <div className="sub">{e.description}</div>
          <div className="muted">Matched anomalies: {e.matching_anomalies.length ? e.matching_anomalies.join(", ") : "none"} · sources: {e.sources.map((u, i) => <a key={u} href={u} target="_blank" rel="noreferrer">[{i + 1}]</a>)}</div></div>))}
        <p className="muted">{ins.events._note}</p></div>
    </div>
    <div className="card" style={{ marginBottom: 12 }}><h2>All detected anomalies</h2>
      <div className="muted" style={{ marginBottom: 6 }}>{ins.rules}</div>
      <div className="scroll"><table><thead><tr><th>Month</th><th>Series</th><th className="num">Actual</th><th className="num">Expected</th><th className="num">×</th><th>Top drivers</th></tr></thead>
        <tbody>{ins.anomalies.map((a) => (<tr key={a.month + a.series}><td>{a.month}</td><td>{a.label}</td>
          <td className="num">{a.share_pct !== null ? `${a.share_pct}%` : fmt(a.value)}</td><td className="num">{a.expected_share_pct !== null ? `${a.expected_share_pct}%` : fmt(a.expected_value)}</td>
          <td className="num">{a.ratio}</td><td>{a.drivers.slice(0, 2).map((d) => <button key={d.issue} className="chip" onClick={() => pick(d.issue)}>{d.issue}</button>)}</td></tr>))}</tbody></table></div></div>
    <div className="card"><h2>Release lens — complaint share by app version</h2>
      <Versions versions={ins.versions} pick={pick} /></div>
  </>);
}

function Versions({ versions, pick }) {
  const v = versions.filter((x) => x.version !== "(missing)");
  const miss = versions.find((x) => x.version === "(missing)");
  const max = Math.max(...v.map((x) => x.complaint_share_pct));
  const worst = [...v].sort((a, b) => b.complaint_share_pct - a.complaint_share_pct).slice(0, 3).map((x) => x.version);
  return (<>
    <div className="scroll" style={{ maxHeight: 360, overflow: "auto" }}><table><thead><tr><th>Version</th><th>First seen</th><th className="num">Reviews</th><th className="barcell">Complaint share</th><th className="num">Sev 4+</th><th>Top specific issue</th></tr></thead>
      <tbody>{v.map((x) => (<tr key={x.version}><td>{worst.includes(x.version) ? <b>{x.version}</b> : x.version}</td><td className="muted">{x.first_month}</td><td className="num">{fmt(x.classified)}</td>
        <td className="barcell"><div style={{ display: "flex", alignItems: "center", gap: 6 }}><div className="bar" style={{ width: `${(70 * x.complaint_share_pct) / max}%`, background: worst.includes(x.version) ? "var(--s2)" : "var(--s1)" }} /><span className="muted">{x.complaint_share_pct}%</span></div></td>
        <td className="num">{x.severity_4_plus_share_pct}%</td><td>{x.top_issue && <button className="chip" onClick={() => pick(x.top_issue)}>{x.top_issue} ({fmt(x.top_issue_count)})</button>}</td></tr>))}</tbody></table></div>
    <p className="muted">Versions with ≥400 classified reviews; highlighted = three highest complaint shares. {miss ? `${fmt(miss.classified)} reviews have no app version and are excluded here.` : ""} Version and date are confounded (later versions shipped during the 2023 waves).</p>
  </>);
}

function Recurring({ ins, pick }) {
  const issues = ins.recurring.map((r) => r.issue);
  const full = ins.monthly.filter((m) => !m.partial);
  const byM = Object.fromEntries(full.map((m) => [m.month, m]));
  const rec = Object.fromEntries(ins.recurring.map((r) => [r.issue, r]));
  return (<>
    <div className="card" style={{ marginBottom: 12 }}><h2>Recurring problems — share of reviews per month</h2>
      <Heatmap rows={issues} cols={full.map((m) => m.month)} label="Issue" rowLabel={(i) => i} bins={[0.25, 0.5, 1, 2, 4]} onRow={pick}
        value={(i, c) => ({ v: (100 * (byM[c].issues[i] || 0)) / byM[c].classified, n: byM[c].issues[i] || 0 })}
        extra={(i) => <><span className={`pill ${rec[i].trend === "rising" ? "bad" : rec[i].trend === "falling" ? "ok" : ""}`}>{rec[i].trend === "rising" ? "▲ rising" : rec[i].trend === "falling" ? "▼ falling" : "■ steady"}</span><span className="pill">{rec[i].pattern}</span></>} />
      <p className="muted">Full months only. Chronic = ≥0.5% of reviews in ≥75% of months; rising/falling = last-6 vs first-6 month mean share (≥1.5× and ≥0.5 pp). Click a row for its evidence.</p></div>
    <div className="card"><h2>Persistence table</h2><div className="scroll"><table>
      <thead><tr><th>Issue</th><th className="num">Complaints</th><th className="num">Months ≥0.5%</th><th className="num">Months in top 5</th><th className="num">First 6 mo</th><th className="num">Last 6 mo</th><th>Peak</th></tr></thead>
      <tbody>{ins.recurring.map((r) => (<tr key={r.issue} className="click" onClick={() => pick(r.issue)}><td>{r.issue}</td><td className="num">{fmt(r.complaints)}</td>
        <td className="num">{r.months_present}/{r.full_months}</td><td className="num">{r.months_in_top5}</td><td className="num">{r.first6_mean_share_pct}%</td><td className="num">{r.last6_mean_share_pct}%</td><td className="muted">{r.peak_month} ({r.peak_share_pct}%)</td></tr>))}</tbody></table></div></div>
  </>);
}

function Priorities({ rank, memo, ins, sel, pick, run }) {
  const [view, setView] = useState("baseline");
  const alt = Object.fromEntries(ins.alt_ranking_table.map((a) => [a.issue, a]));
  const views = { baseline: "Baseline (severity sum)", severity_4_plus: "Blocking (sev 4+)", cancellations: "Intent to leave", recent_3m_complaints: "Last 3 full months" };
  const rows = view === "baseline" ? rank.rows : ins.alt_rankings[view].map((iid, k) => ({ ...rank.rows.find((r) => r.issue_id === iid), alt_rank: k + 1, alt_value: alt[iid]?.[view] }));
  const metric = (r) => (view === "baseline" ? r.priority_score : r.alt_value);
  const maxScore = Math.max(1, ...rows.map(metric));
  const m = memo.memo;
  const heat = ins.severity_heatmap;
  const hmax = Math.max(...Object.values(heat).flatMap((r) => Object.values(r)));
  return (<>
    <div className="grid two" style={{ marginBottom: 12 }}>
      <div className="card"><div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}><h2>Issue ranking</h2>
        <span className="seg">{Object.entries(views).map(([k, l]) => <button key={k} className={view === k ? "on" : ""} onClick={() => setView(k)}>{l}</button>)}</span></div>
        <div className="muted" style={{ marginBottom: 6 }}>{view === "baseline" ? rank.rule + " (official, reproducible)" : "Alternative view for sensitivity only; the baseline above is the official ranking."}</div>
        <div className="scroll" style={{ maxHeight: 560, overflow: "auto" }}><table>
          <thead><tr><th>#</th><th>Issue</th><th className="num">Count</th><th className="num">Mean</th><th className="barcell">{views[view]}</th></tr></thead>
          <tbody>{rows.map((r, k) => (<tr key={r.issue_id} className={`click ${sel === r.issue_id ? "sel" : ""}`} onClick={() => pick(r.issue_id)}>
            <td>{view === "baseline" ? r.rank : r.alt_rank}</td><td><div>{r.title || r.issue_id}</div><div className="muted"><code>{r.issue_id}</code></div></td>
            <td className="num">{fmt(r.complaint_count)}</td><td className="num">{Number(r.mean_severity).toFixed(2)}</td>
            <td className="barcell"><div style={{ display: "flex", alignItems: "center", gap: 6 }}><div className="bar" style={{ width: `${(70 * metric(r)) / maxScore}%` }} /><span className="muted">{fmt(metric(r))}</span></div></td></tr>))}</tbody>
        </table></div></div>
      <div id="detail"><IssueDetail run={run} issueId={sel} /></div>
    </div>
    <div className="grid two" style={{ marginBottom: 12 }}>
      <div className="card"><h2>Severity mix by topic (complaints)</h2>
        <Heatmap rows={TOPIC_ORDER} cols={[1, 2, 3, 4, 5]} label="Topic" rowLabel={(t) => t} bins={[1, 0.05, 0.15, 0.35, 0.6].map((f, k) => (k ? Math.round(f * hmax) : 1))} colLabel={(c) => `sev ${c}`} fmtVal={(v) => fmt(Math.round(v))}
          value={(t, c) => ({ v: heat[t][c] })} />
        <p className="muted">Volume-heavy topics (usability, other) sit at severity 2; access and playback concentrate the blocking 4s.</p></div>
      <div className="card"><h2>Alternatives & limitations (memo)</h2>
        {(m?.alternatives || []).map((a, i) => <p key={i} style={{ margin: "4px 0" }}><b>{AREA_LABEL[a.area] || a.area}:</b> {inline(a.assessment, pick, memo.claims)}</p>)}
        <ul style={{ paddingLeft: 18 }}>{(m?.limitations || []).map((l, i) => <li key={i}>{l}</li>)}</ul>
        <div>{memo.claims.map((c) => <button key={c.claim_id} className="chip" style={{ marginBottom: 4 }} onClick={() => pick(c.issue_id)}>{c.claim_id}: {c.metric.replace("_", " ")} {c.value}</button>)}</div></div>
    </div>
    <div className="card"><h2>Full decision memo</h2><Markdown text={m?.memo_markdown} onClaim={pick} claims={memo.claims} /></div>
  </>);
}

export default function Page() {
  const [run, setRun] = useState(null);
  const [ov, setOv] = useState(null), [rank, setRank] = useState(null), [memo, setMemo] = useState(null), [ins, setIns] = useState(null);
  const [md, setMd] = useState(null), [range, setRangeState] = useState(null);
  const [err, setErr] = useState(null);
  const [sel, setSel] = useState(null);
  const [tab, setTab] = useState("month");
  const [lookup, setLookup] = useState(""), [found, setFound] = useState(null);
  useEffect(() => {
    setErr(null);
    Promise.all([get("/api/overview", run), get("/api/ranking", run), get("/api/memo", run), get("/api/insights", run)])
      .then(([o, r, m, i]) => { setOv(o); setRank(r); setMemo(m); setIns(i.insights); if (!run) setRun(o.run); })
      .catch((e) => setErr(String(e)));
  }, [run]);
  useEffect(() => {
    if (!run) return;
    get(`/api/period${range ? `?from=${range[0]}&to=${range[1]}` : ""}`, run).then((d) => { setMd(d); if (!range) setRangeState([d.from, d.to]); }).catch((e) => setErr(String(e)));
  }, [run, range?.[0], range?.[1]]);
  const setRange = (r, open) => { const [f, t] = r[0] <= r[1] ? r : [r[1], r[0]]; setRangeState([f, t]); if (open) { setTab("month"); window.scrollTo({ top: 0, behavior: "smooth" }); } };
  const onBar = (m, extend) => setRange(extend && range ? [range[0] <= m ? range[0] : m, range[0] <= m ? m : range[1]] : [m, m], !extend);
  const pick = (id) => { setSel(id); setTab("priorities"); setTimeout(() => document.getElementById("detail")?.scrollIntoView({ behavior: "smooth", block: "start" }), 80); };
  if (err) return <div className="wrap"><div className="card bad">Backend error: {err}</div></div>;
  if (!ov || !rank || !memo) return <div className="wrap muted">Loading saved results from the database…</div>;
  const months = md?.months || ins?.months || [];
  const fi = range ? months.indexOf(range[0]) : -1, ti = range ? months.indexOf(range[1]) : -1, span = ti - fi + 1;
  const last = months[months.length - 1];
  const presets = { latest: ["Latest month", [last, last]], l3: ["Last 3 months", [months[months.length - 3], last]], l6: ["Last 6 months", [months[months.length - 6], last]],
    y2023: ["2023", [months.find((m) => m.startsWith("2023")), [...months].reverse().find((m) => m.startsWith("2023"))]],
    y2022: ["2022", [months.find((m) => m.startsWith("2022")), [...months].reverse().find((m) => m.startsWith("2022"))]], all: ["All data", [months[0], last]] };
  const activePreset = Object.entries(presets).find(([, [, r]]) => range && r[0] === range[0] && r[1] === range[1])?.[0] || "custom";
  const tabs = { month: "Period summary", trends: "Monthly trends & events", recurring: "Recurring problems", priorities: "Priorities & evidence", evidence: "Quality & cost", lookup: "Review lookup" };
  return (
    <div className="wrap">
      <header className="top">
        <div><h1><span className="brand" />Spotify review insights</h1>
          <div className="sub">Google Play reviews, May 2022 – Nov 2023 · multi-agent pipeline outputs served from Postgres</div></div>
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <div className="monthpick" aria-label="Period">
            <select value={activePreset} onChange={(e) => e.target.value !== "custom" && setRange(presets[e.target.value][1])} title="Preset period">
              {Object.entries(presets).map(([k, [l]]) => <option key={k} value={k}>{l}</option>)}<option value="custom">Custom range</option></select>
            <button disabled={fi <= 0} onClick={() => setRange([months[fi - 1], months[ti - 1]])} title="shift period earlier">‹</button>
            <select value={range?.[0] || ""} onChange={(e) => setRange([e.target.value, range[1] < e.target.value ? e.target.value : range[1]])} title="From">{months.map((m) => <option key={m} value={m}>{MONTH_SHORT(m)}</option>)}</select>
            <span className="muted">to</span>
            <select value={range?.[1] || ""} onChange={(e) => setRange([range[0] > e.target.value ? e.target.value : range[0], e.target.value])} title="To">{months.map((m) => <option key={m} value={m}>{MONTH_SHORT(m)}</option>)}</select>
            <button disabled={ti < 0 || ti >= months.length - 1} onClick={() => setRange([months[fi + 1], months[ti + 1]])} title="shift period later">›</button>
          </div>
          <label className="muted">Run&nbsp;<select value={run || ""} onChange={(e) => { setRun(e.target.value); setSel(null); setRangeState(null); }}>
            {ov.runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.run_id}{r.is_final ? " (final)" : ""}</option>)}</select></label>
        </div>
      </header>
      <div style={{ opacity: md && range && (md.from !== range[0] || md.to !== range[1]) ? 0.45 : 1, transition: "opacity .15s" }}><SummaryStrip md={md} /></div>
      <div className="tabs" style={{ margin: "6px 0 12px", borderBottom: "1px solid var(--border)" }}>
        {Object.entries(tabs).map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      {!ins && tab !== "evidence" && tab !== "lookup" && <div className="card muted">This run has no insights layer; choose the final run.</div>}
      {ins && tab === "month" && <div style={{ opacity: md && range && (md.from !== range[0] || md.to !== range[1]) ? 0.45 : 1 }}><MonthView md={md} ins={ins} memo={memo} pick={pick} go={setTab} setRange={setRange} /></div>}
      {ins && tab === "trends" && <Trends ins={ins} pick={pick} range={range} onBar={onBar} />}
      {ins && tab === "recurring" && <Recurring ins={ins} pick={pick} />}
      {ins && tab === "priorities" && <Priorities rank={rank} memo={memo} ins={ins} sel={sel} pick={pick} run={ov.run} />}
      {tab === "evidence" && <Evidence ev={ov.evidence} info={ov.info} />}
      {tab === "lookup" && <div className="card">
        <h2>Trace one review</h2>
        <form onSubmit={(e) => { e.preventDefault(); get(`/api/reviews?id=${encodeURIComponent(lookup.trim())}`, ov.run).then((j) => setFound(j.review || "not found")).catch((x) => setFound(String(x))); }}>
          <input value={lookup} onChange={(e) => setLookup(e.target.value)} placeholder="review_id (UUID)" style={{ width: "min(420px,100%)" }} /> <button type="submit">Look up</button>
        </form>
        {found && typeof found === "object" && <div style={{ marginTop: 12 }}>
          <div className="quote">{found.review_text}</div>
          <table><tbody>{["status", "reason", "topic", "subtopic", "intent", "severity", "sentiment", "evidence_quote", "needs_review", "label_config", "cache_source_id", "issue_id", "source_sha256", "review_timestamp", "review_rating", "app_version"].map((k) =>
            <tr key={k}><td className="muted">{k}</td><td>{k === "issue_id" && found[k] ? <button className="chip" onClick={() => pick(found[k])}>{found[k]}</button> : String(found[k] ?? "—")}</td></tr>)}</tbody></table>
        </div>}
        {typeof found === "string" && <p className="muted">{found}</p>}
      </div>}
      <p className="muted" style={{ marginTop: 18 }}>Self-selected public reviews (declared 100k sample of 660,622); no revenue, plan tier or confirmed churn. Stated intent to leave is not observed churn. External events are news context, not proven causes. Star ratings are metadata only.</p>
    </div>
  );
}
