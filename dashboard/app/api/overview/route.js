import { db, resolveRun, json } from "@/lib/db";

export async function GET(req) {
  try {
    const sql = db();
    const run = await resolveRun(req);
    if (!run) return json({ error: "no runs loaded" }, 404);
    const [runs, info, agg, ev, status] = await Promise.all([
      sql`SELECT run_id, is_final, loaded_at FROM runs ORDER BY is_final DESC, loaded_at DESC`,
      sql`SELECT run_id, is_final, scope, manifest->'roles' AS roles, manifest->>'input' AS input,
                 manifest->>'input_sha256' AS input_sha256, summary->'ledger' AS ledger, summary->'wall_seconds' AS wall FROM runs WHERE run_id=${run}`,
      sql`SELECT payload FROM aggregates WHERE run_id=${run}`,
      sql`SELECT kind, payload FROM evidence WHERE run_id=${run}`,
      sql`SELECT status, COUNT(*)::int AS n FROM records WHERE run_id=${run} GROUP BY status`,
    ]);
    const evidence = Object.fromEntries(ev.map((e) => [e.kind, e.payload]));
    return json({ run, runs, info: info[0], aggregates: agg[0]?.payload, evidence,
                  status: Object.fromEntries(status.map((s) => [s.status, s.n])) });
  } catch (e) {
    return json({ error: String(e.message || e) }, 500);
  }
}
