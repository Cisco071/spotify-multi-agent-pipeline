-- Postgres schema for the deployed dashboard backend (Neon). Loaded by db/load_db.py from saved run artifacts.
-- Read-only for the web app; the pipeline never depends on it.
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, is_final BOOLEAN NOT NULL DEFAULT FALSE, manifest JSONB, summary JSONB,
  scope JSONB, loaded_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS records (
  run_id TEXT NOT NULL, review_id TEXT NOT NULL, status TEXT NOT NULL, reason TEXT,
  topic TEXT, subtopic TEXT, intent TEXT, sentiment REAL, severity SMALLINT, entities JSONB, evidence_quote TEXT,
  needs_review BOOLEAN, label_config TEXT, cache_source_id TEXT,
  review_text TEXT, review_rating SMALLINT, review_likes INTEGER, app_version TEXT, review_timestamp TEXT, source_sha256 TEXT,
  PRIMARY KEY (run_id, review_id));
CREATE INDEX IF NOT EXISTS records_topic ON records (run_id, topic, intent);
CREATE TABLE IF NOT EXISTS issues (
  run_id TEXT NOT NULL, issue_id TEXT NOT NULL, topic TEXT, subtopic TEXT, title TEXT, description TEXT,
  member_count INTEGER, coherent BOOLEAN, misfit_review_ids JSONB, evidence_pack JSONB, PRIMARY KEY (run_id, issue_id));
CREATE TABLE IF NOT EXISTS membership (
  run_id TEXT NOT NULL, issue_id TEXT NOT NULL, review_id TEXT NOT NULL, PRIMARY KEY (run_id, issue_id, review_id));
CREATE INDEX IF NOT EXISTS membership_issue ON membership (run_id, issue_id);
CREATE TABLE IF NOT EXISTS ranking (
  run_id TEXT NOT NULL, rank INTEGER NOT NULL, issue_id TEXT NOT NULL, complaint_count INTEGER, severity_sum INTEGER,
  mean_severity TEXT, priority_score INTEGER, PRIMARY KEY (run_id, rank));
CREATE TABLE IF NOT EXISTS aggregates (run_id TEXT PRIMARY KEY, payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS memo (
  run_id TEXT PRIMARY KEY, recommended_area TEXT, headline TEXT, memo_markdown TEXT, recommendations JSONB,
  alternatives JSONB, limitations JSONB, cited_review_ids JSONB, label_config TEXT, request_id TEXT);
CREATE TABLE IF NOT EXISTS claims (
  run_id TEXT NOT NULL, claim_id TEXT NOT NULL, issue_id TEXT, metric TEXT, value TEXT, PRIMARY KEY (run_id, claim_id));
CREATE TABLE IF NOT EXISTS evidence (run_id TEXT NOT NULL, kind TEXT NOT NULL, payload JSONB, PRIMARY KEY (run_id, kind));
CREATE INDEX IF NOT EXISTS records_month ON records (run_id, review_timestamp);
