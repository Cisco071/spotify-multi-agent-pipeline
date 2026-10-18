import { neon } from "@neondatabase/serverless";

// Server-only: DATABASE_URL is a Vercel environment variable, never sent to the browser.
let sql;
export function db() {
  if (!sql) {
    if (!process.env.DATABASE_URL) throw new Error("DATABASE_URL is not configured");
    sql = neon(process.env.DATABASE_URL);
  }
  return sql;
}

export async function resolveRun(req) {
  const url = new URL(req.url);
  const asked = url.searchParams.get("run");
  const sql = db();
  if (asked) {
    const r = await sql`SELECT run_id FROM runs WHERE run_id = ${asked}`;
    if (r.length) return r[0].run_id;
  }
  const f = await sql`SELECT run_id FROM runs ORDER BY is_final DESC, loaded_at DESC LIMIT 1`;
  return f[0]?.run_id;
}

export function json(data, status = 200) {
  return Response.json(data, { status, headers: { "Cache-Control": "s-maxage=60, stale-while-revalidate=600" } });
}
