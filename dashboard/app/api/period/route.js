import { db, resolveRun, json } from "@/lib/db";

const AREAS = { usability: ["usability"], playback: ["playback", "downloads"], billing_support: ["billing", "support"], access: ["access"] };
const nextMonth = (m) => { const [y, mo] = m.split("-").map(Number); return mo === 12 ? `${y + 1}-01` : `${y}-${String(mo + 1).padStart(2, "0")}`; };
const prevMonth = (m) => { const [y, mo] = m.split("-").map(Number); return mo === 1 ? `${y - 1}-12` : `${y}-${String(mo - 1).padStart(2, "0")}`; };

async function stats(sql, run, from, to) {
  const lo = from, hi = nextMonth(to);
  const [tot, topics, issues, sev] = await Promise.all([
    sql`SELECT COUNT(*)::int n, COUNT(*) FILTER (WHERE status='completed')::int classified,
               COUNT(*) FILTER (WHERE intent IN ('complaint','cancellation'))::int complaints,
               COUNT(*) FILTER (WHERE intent='cancellation')::int cancellations,
               COUNT(*) FILTER (WHERE intent IN ('complaint','cancellation') AND severity>=4)::int sev4,
               COUNT(*) FILTER (WHERE intent='praise')::int praise,
               COUNT(*) FILTER (WHERE needs_review)::int needs_review,
               ROUND(AVG(sentiment)::numeric, 3)::float avg_sentiment
        FROM records WHERE run_id=${run} AND review_timestamp >= ${lo} AND review_timestamp < ${hi}`,
    sql`SELECT topic, COUNT(*)::int n, COUNT(*) FILTER (WHERE intent IN ('complaint','cancellation'))::int complaints,
               COUNT(*) FILTER (WHERE intent='cancellation')::int cancellations,
               COUNT(*) FILTER (WHERE intent IN ('complaint','cancellation') AND severity>=4)::int sev4
        FROM records WHERE run_id=${run} AND status='completed' AND review_timestamp >= ${lo} AND review_timestamp < ${hi} GROUP BY topic`,
    sql`SELECT m.issue_id, COUNT(*)::int n, ROUND(AVG(r.severity)::numeric, 2)::float mean_sev,
               COUNT(*) FILTER (WHERE r.intent='cancellation')::int cancellations, i.title
        FROM membership m JOIN records r ON r.run_id=m.run_id AND r.review_id=m.review_id
        LEFT JOIN issues i ON i.run_id=m.run_id AND i.issue_id=m.issue_id
        WHERE m.run_id=${run} AND r.review_timestamp >= ${lo} AND r.review_timestamp < ${hi}
        GROUP BY m.issue_id, i.title ORDER BY n DESC LIMIT 20`,
    sql`SELECT severity, COUNT(*)::int n FROM records WHERE run_id=${run} AND intent IN ('complaint','cancellation')
        AND review_timestamp >= ${lo} AND review_timestamp < ${hi} GROUP BY severity ORDER BY severity`,
  ]);
  const byTopic = Object.fromEntries(topics.map((t) => [t.topic, t]));
  const areas = Object.fromEntries(Object.entries(AREAS).map(([a, ts]) => [a, ts.reduce((s, t) => ({
    complaints: s.complaints + (byTopic[t]?.complaints || 0), cancellations: s.cancellations + (byTopic[t]?.cancellations || 0),
    sev4: s.sev4 + (byTopic[t]?.sev4 || 0) }), { complaints: 0, cancellations: 0, sev4: 0 })]));
  const inAreas = Object.values(areas).reduce((s, a) => s + a.complaints, 0);
  areas.other_topics = { complaints: tot[0].complaints - inAreas, cancellations: 0, sev4: 0 };
  return { from, to, ...tot[0], areas, topics: byTopic, issues, severity: sev };
}

const shift = (m, k) => { let [y, mo] = m.split("-").map(Number); mo += k; while (mo < 1) { mo += 12; y -= 1; } while (mo > 12) { mo -= 12; y += 1; } return `${y}-${String(mo).padStart(2, "0")}`; };

// Period statistics for [from, to] (inclusive months), compared with the equal-length period just before it.
export async function GET(req) {
  try {
    const sql = db();
    const run = await resolveRun(req);
    const months = (await sql`SELECT DISTINCT substr(review_timestamp,1,7) m FROM records WHERE run_id=${run} ORDER BY m`).map((r) => r.m);
    const u = new URL(req.url);
    let from = u.searchParams.get("from"), to = u.searchParams.get("to");
    if (!months.includes(to)) to = months[months.length - 1];
    if (!months.includes(from)) from = to;
    if (from > to) [from, to] = [to, from];
    const span = months.filter((m) => m >= from && m <= to).length;
    const pTo = shift(from, -1);
    let pFrom = shift(from, -span), priorKind = "equal-length previous period";
    if (!months.includes(pFrom) && months.includes(pTo)) { pFrom = months[0]; priorKind = "all earlier months available"; }
    const hasPrior = months.includes(pFrom) && months.includes(pTo);
    const [cur, prior, examples, last, monthly] = await Promise.all([
      stats(sql, run, from, to),
      hasPrior ? stats(sql, run, pFrom, pTo) : null,
      sql`SELECT r.review_id, r.topic, r.subtopic, r.intent, r.severity, r.evidence_quote, r.review_timestamp, m.issue_id
          FROM records r LEFT JOIN membership m ON m.run_id=r.run_id AND m.review_id=r.review_id
          WHERE r.run_id=${run} AND r.intent IN ('complaint','cancellation') AND r.topic <> 'other'
            AND r.review_timestamp >= ${from} AND r.review_timestamp < ${nextMonth(to)} AND r.cache_source_id IS NULL
          ORDER BY r.severity DESC, r.review_likes DESC, r.review_id LIMIT 8`,
      sql`SELECT MIN(review_timestamp) first, MAX(review_timestamp) last FROM records WHERE run_id=${run} AND review_timestamp >= ${from} AND review_timestamp < ${nextMonth(to)}`,
      sql`SELECT substr(review_timestamp,1,7) m, COUNT(*) FILTER (WHERE status='completed')::int classified,
                 COUNT(*) FILTER (WHERE intent IN ('complaint','cancellation'))::int complaints
          FROM records WHERE run_id=${run} AND review_timestamp >= ${from} AND review_timestamp < ${nextMonth(to)} GROUP BY 1 ORDER BY 1`,
    ]);
    return json({ run, months, from, to, span, prior_from: hasPrior ? pFrom : null, prior_to: hasPrior ? pTo : null, prior_kind: hasPrior ? priorKind : null,
                  includes_partial: from === months[0] || to === months[months.length - 1],
                  first_review: last[0].first, last_review: last[0].last, current: cur, prior, examples, monthly });
  } catch (e) {
    return json({ error: String(e.message || e) }, 500);
  }
}
