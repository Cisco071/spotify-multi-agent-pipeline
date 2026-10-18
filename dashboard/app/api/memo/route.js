import { db, resolveRun, json } from "@/lib/db";

export async function GET(req) {
  try {
    const sql = db();
    const run = await resolveRun(req);
    const [memo, claims] = await Promise.all([
      sql`SELECT * FROM memo WHERE run_id=${run}`,
      sql`SELECT c.*, r.rank FROM claims c LEFT JOIN ranking r ON r.run_id=c.run_id AND r.issue_id=c.issue_id
          WHERE c.run_id=${run} ORDER BY length(c.claim_id), c.claim_id`,
    ]);
    return json({ run, memo: memo[0] || null, claims });
  } catch (e) {
    return json({ error: String(e.message || e) }, 500);
  }
}
