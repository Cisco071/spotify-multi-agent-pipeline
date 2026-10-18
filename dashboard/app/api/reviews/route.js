import { db, resolveRun, json } from "@/lib/db";

export async function GET(req) {
  try {
    const sql = db();
    const run = await resolveRun(req);
    const u = new URL(req.url);
    const id = u.searchParams.get("id");
    if (id) {
      const r = await sql`SELECT r.*, m.issue_id FROM records r LEFT JOIN membership m ON m.run_id=r.run_id AND m.review_id=r.review_id
                          WHERE r.run_id=${run} AND r.review_id=${id}`;
      return json({ run, review: r[0] || null });
    }
    const q = (u.searchParams.get("q") || "").slice(0, 80);
    const topic = u.searchParams.get("topic");
    const rows = await sql`SELECT review_id, status, topic, subtopic, intent, severity, sentiment, evidence_quote, review_text, review_timestamp
                           FROM records WHERE run_id=${run}
                             AND (${topic}::text IS NULL OR topic=${topic})
                             AND (${q} = '' OR review_text ILIKE ${"%" + q + "%"})
                           ORDER BY review_id LIMIT 50`;
    return json({ run, rows });
  } catch (e) {
    return json({ error: String(e.message || e) }, 500);
  }
}
