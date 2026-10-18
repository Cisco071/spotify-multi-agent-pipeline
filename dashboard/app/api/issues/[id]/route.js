import { db, resolveRun, json } from "@/lib/db";

export async function GET(req, { params }) {
  try {
    const { id } = await params;
    const sql = db();
    const run = await resolveRun(req);
    const [issue, rank, sev, cancels, members] = await Promise.all([
      sql`SELECT * FROM issues WHERE run_id=${run} AND issue_id=${id}`,
      sql`SELECT * FROM ranking WHERE run_id=${run} AND issue_id=${id}`,
      sql`SELECT r.severity, COUNT(*)::int AS n FROM membership m JOIN records r ON r.run_id=m.run_id AND r.review_id=m.review_id
          WHERE m.run_id=${run} AND m.issue_id=${id} GROUP BY r.severity ORDER BY r.severity`,
      sql`SELECT COUNT(*)::int AS n FROM membership m JOIN records r ON r.run_id=m.run_id AND r.review_id=m.review_id
          WHERE m.run_id=${run} AND m.issue_id=${id} AND r.intent='cancellation'`,
      sql`SELECT r.review_id, r.intent, r.severity, r.evidence_quote, r.review_text, r.review_timestamp, r.cache_source_id
          FROM membership m JOIN records r ON r.run_id=m.run_id AND r.review_id=m.review_id
          WHERE m.run_id=${run} AND m.issue_id=${id} ORDER BY r.severity DESC, r.review_likes DESC, r.review_id LIMIT 40`,
    ]);
    if (!issue.length) return json({ error: "unknown issue" }, 404);
    return json({ run, issue: issue[0], ranking: rank[0], severity: sev, cancellations: cancels[0].n, members });
  } catch (e) {
    return json({ error: String(e.message || e) }, 500);
  }
}
