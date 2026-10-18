import { db, resolveRun, json } from "@/lib/db";

export async function GET(req) {
  try {
    const sql = db();
    const run = await resolveRun(req);
    const rows = await sql`SELECT r.rank, r.issue_id, r.complaint_count, r.severity_sum, r.mean_severity, r.priority_score,
                                  i.title, i.description, i.topic, i.subtopic, i.coherent
                           FROM ranking r LEFT JOIN issues i ON i.run_id=r.run_id AND i.issue_id=r.issue_id
                           WHERE r.run_id=${run} ORDER BY r.rank`;
    return json({ run, rule: "priority_score = severity_sum = complaint_count × mean_severity; ties → ascending issue_id", rows });
  } catch (e) {
    return json({ error: String(e.message || e) }, 500);
  }
}
