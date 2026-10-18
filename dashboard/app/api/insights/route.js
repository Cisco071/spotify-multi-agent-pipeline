import { db, resolveRun, json } from "@/lib/db";

// Code-computed insights (monthly series, anomalies, recurring problems, alternative rankings, events).
export async function GET(req) {
  try {
    const sql = db();
    const run = await resolveRun(req);
    const r = await sql`SELECT payload FROM evidence WHERE run_id=${run} AND kind='insights'`;
    if (!r.length) return json({ run, insights: null });
    return json({ run, insights: r[0].payload });
  } catch (e) {
    return json({ error: String(e.message || e) }, 500);
  }
}
