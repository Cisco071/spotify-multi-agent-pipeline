"""Stage 2 - Classify (model role: ENRICHER, claude-haiku-5-5; capped Sonnet fallback).

Code owns: queueing, exact-text cache, <=50 reviews/request, ID validation, schema/quote validation, retries,
fallback quota, quarantine, atomic saves after each batch, spend cap and graceful stop.
The model owns: reading the review language and choosing labels.
"""
import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .. import config, llm, result_cache
from ..ledger import BudgetExceeded
from ..schema import BATCH_SCHEMA, validate_item

QUOTE_TRANSLATE = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def _user_content(batch):
    payload = [{"review_id": rid, "review_text": text} for rid, text in batch]
    return ("Label each review below. Return one result per review_id.\n<reviews>\n"
            + json.dumps(payload, ensure_ascii=False) + "\n</reviews>")


def _repair_quote(item, text):
    """Deterministic, conservative quote repair. Returns a note or None."""
    q = item.get("evidence_quote")
    if isinstance(q, str):
        for cand in (q.strip(), q.strip().strip('"').strip("'"), q.translate(QUOTE_TRANSLATE).strip()):
            if cand and cand in text:
                item["evidence_quote"] = cand
                return "quote_trimmed"
    if len(text) <= config.SHORT_TEXT_QUOTE_LIMIT:
        item["evidence_quote"] = text
        return "quote_fallback_fulltext"
    return None


class Enricher:
    def __init__(self, store, ledger, *, invocation, phase, workers=1, limit=None, stop_event=None, log=print,
                 fallback_cap=None, run_id=None, progress_path=None, event_log=None, use_global_cache=True,
                 stop_after_calls=None):
        self.stop_after_calls = stop_after_calls
        self.run_id, self.progress_path, self.event_log = run_id, progress_path, event_log
        self.use_global_cache = use_global_cache
        self.store, self.ledger, self.inv, self.phase = store, ledger, invocation, phase
        self.workers, self.limit, self.log = workers, limit, log
        self.stop = stop_event or threading.Event()
        self.lc = config.label_config("enrich")
        self.lc_fb = config.label_config("enrich_fallback")
        nonempty = store.one("SELECT COUNT(*) n FROM reviews WHERE is_empty=0")["n"]
        self.fallback_cap = fallback_cap if fallback_cap is not None else int(nonempty * config.FALLBACK_FRACTION_CAP)
        self.in_flight = set()
        self.dispatched_records = 0
        self.stats = {"batches": 0, "calls_ok": 0, "calls_failed": 0, "completed": 0, "cache_hits": 0,
                      "retried_ids": 0, "fallback_ids": 0, "quarantined": 0, "stopped_reason": None,
                      "imported_from_global_cache": 0, "agents": {}}
        self.t0 = time.time()

    # ------------------------------------------------------------------ cache
    def propagate_cache(self):
        """Pending rows whose exact text already has a completed ORIGINAL result get a direct cache copy."""
        with self.store.tx() as db:
            db.execute("""
              WITH orig AS (
                SELECT rv.text_sha, MIN(rv.row_idx) AS row_idx FROM results r JOIN reviews rv USING(review_id)
                WHERE r.status='completed' AND r.cache_source_id IS NULL GROUP BY rv.text_sha),
              src AS (SELECT rv.text_sha, rv.review_id AS src_id FROM orig JOIN reviews rv USING(text_sha, row_idx))
              UPDATE results SET status='completed',
                 topic=s.topic, subtopic=s.subtopic, intent=s.intent, sentiment=s.sentiment, severity=s.severity,
                 entities=s.entities, evidence_quote=s.evidence_quote, needs_review=s.needs_review,
                 review_reason=s.review_reason, label_config=s.label_config, cache_source_id=s.review_id,
                 phase=?, notes='exact_text_cache', updated_at=?
              FROM (SELECT src.text_sha, r2.* FROM src JOIN results r2 ON r2.review_id=src.src_id) AS s,
                   reviews me
              WHERE results.review_id=me.review_id AND me.text_sha=s.text_sha AND results.status='pending'
                AND s.label_config IN (?, ?)
            """, (self.phase, time.time(), self.lc, self.lc_fb))
            n = db.execute("SELECT changes()").fetchone()[0]
        self.stats["cache_hits"] += n
        return n

    # ------------------------------------------------------------------ queue
    def next_batches(self, n_batches):
        rows = self.store.q("""
            SELECT rv.review_id, rv.review_text, rv.text_sha FROM results r JOIN reviews rv USING(review_id)
            WHERE r.status='pending' AND rv.is_empty=0
              AND rv.row_idx = (SELECT MIN(row_idx) FROM reviews x JOIN results y USING(review_id)
                                WHERE x.text_sha=rv.text_sha AND y.status='pending')
            ORDER BY rv.row_idx LIMIT ?""", (n_batches * config.BATCH_SIZE * 2,))
        out, cur = [], []
        for r in rows:
            if r["text_sha"] in self.in_flight:
                continue
            if self.limit is not None and self.dispatched_records + len(cur) + sum(map(len, out)) >= self.limit:
                break
            cur.append((r["review_id"], r["review_text"], r["text_sha"]))
            if len(cur) == config.BATCH_SIZE:
                out.append(cur)
                cur = []
                if len(out) == n_batches:
                    break
        if cur and len(out) < n_batches:
            out.append(cur)
        for b in out:
            self.in_flight |= {t for _, _, t in b}
            self.dispatched_records += len(b)
        return out

    # ------------------------------------------------------------------ one request
    def run_batch(self, batch, attempt=0, role_key="enrich"):
        lc = self.lc if role_key == "enrich" else self.lc_fb
        ids = [rid for rid, _, _ in batch]
        texts = {rid: text for rid, text, _ in batch}
        try:
            data, rec = llm.call(self.store, self.ledger, role_key=role_key, role="enrich",
                                 user_content=_user_content([(rid, texts[rid]) for rid in ids]), schema=BATCH_SCHEMA,
                                 review_ids=ids, phase=self.phase, invocation=self.inv, label_config=lc,
                                 attempt_tag=attempt)
        except llm.TransientExhausted:
            self.stats["calls_failed"] += 1
            raise  # outage: records stay pending, orchestrator pauses
        except llm.CallFailed as e:
            self.stats["calls_failed"] += 1
            return {"ok": {}, "invalid": {rid: f"call_failed:{e}"[:200] for rid in ids}, "rec": None}
        self.stats["calls_ok"] += 1
        return self.outcome_from_data(data, ids, texts, rec, lc)

    @staticmethod
    def outcome_from_data(data, ids, texts, rec, lc):
        """Validate one model response against the IDs/texts that were sent. Pure function (also used by recovery)."""
        results = data.get("results") if isinstance(data, dict) else None
        ok, invalid, seen = {}, {}, set()
        for item in results or []:
            rid = item.get("review_id") if isinstance(item, dict) else None
            if rid not in texts or rid in seen:
                continue  # foreign or duplicate ID: ignored (missing IDs handled below)
            seen.add(rid)
            clean, err = validate_item(item, texts[rid])
            note = None
            if err in ("quote_not_substring", "empty_quote"):
                note = _repair_quote(item, texts[rid])
                if note:
                    clean, err = validate_item(item, texts[rid])
            if err:
                invalid[rid] = err
            else:
                clean["notes"] = note
                ok[rid] = clean
        for rid in ids:
            if rid not in ok and rid not in invalid:
                invalid[rid] = "missing_from_output"
        return {"ok": ok, "invalid": invalid, "rec": rec, "lc": lc}

    # ------------------------------------------------------------------ crash recovery
    def recover_journal(self):
        """On start: (1) responses that arrived but were never committed are validated and saved WITHOUT a new call;
        (2) requests that were in flight with no response are logged as failed/unknown-outcome (possible charge)
        and their reviews simply stay pending."""
        rec_n = lost = 0
        for row in self.store.q("SELECT * FROM responses WHERE consumed=0 ORDER BY received_at"):
            rec = json.loads(row["rec"])
            if row["role"] != "enrich":
                self.store.mark_consumed(row["request_id"])
                continue
            ids = rec["review_ids"]
            pend = {r["review_id"]: r["review_text"] for r in self.store.q(
                f"""SELECT rv.review_id, rv.review_text FROM reviews rv JOIN results r USING(review_id)
                    WHERE r.status='pending' AND rv.review_id IN ({','.join('?' * len(ids))})""", ids)}
            known = self.store.one("SELECT 1 FROM calls WHERE request_id=?", (rec["request_id"],))
            if not pend or known:
                self.store.mark_consumed(row["request_id"])
                continue
            try:
                data = json.loads(row["raw"])
            except json.JSONDecodeError:
                self.store.mark_consumed(row["request_id"])
                continue
            rec["outcome"] = "succeeded"
            rec["settings"] = {**rec.get("settings", {}), "recovered_from_journal": True}
            out = self.outcome_from_data(data, [i for i in ids if i in pend], pend, rec, rec["label_config"])
            self.save(out, list(pend), 0)
            rec_n += 1
        for row in self.store.q("SELECT * FROM inflight"):
            rec = json.loads(row["rec"])
            rec.update(outcome="failed", error="interrupted: outcome unknown (process stopped while request in flight; "
                                               "possible provider charge not captured)", cost_usd=0.0,
                       settings={**rec.get("settings", {}), "cost_unknown": True})
            with self.store.tx() as db:
                if not db.execute("SELECT 1 FROM calls WHERE request_id=?", (rec["request_id"],)).fetchone():
                    self.store.insert_call(db, rec)
                db.execute("DELETE FROM inflight WHERE request_id=?", (rec["request_id"],))
            lost += 1
        if rec_n or lost:
            self.log(f"[enrich] crash recovery: {rec_n} saved responses committed without new calls; "
                     f"{lost} interrupted requests logged as unknown-outcome (their reviews stay pending)")
        self.stats["recovered_responses"], self.stats["interrupted_requests"] = rec_n, lost
        return rec_n, lost

    def save(self, outcome, batch_ids, attempt):
        """Atomic: successful call log + completed records + attempt counters in one transaction."""
        now = time.time()
        with self.store.tx() as db:
            if outcome["rec"] is not None:
                if not db.execute("SELECT 1 FROM calls WHERE request_id=?", (outcome["rec"]["request_id"],)).fetchone():
                    self.store.insert_call(db, outcome["rec"])
                self.store.mark_consumed(outcome["rec"]["request_id"], db)
            for rid, c in outcome["ok"].items():
                db.execute("""UPDATE results SET status='completed', topic=?, subtopic=?, intent=?, sentiment=?, severity=?,
                              entities=?, evidence_quote=?, needs_review=?, review_reason=?, label_config=?,
                              cache_source_id=NULL, reason=NULL, attempts=attempts+1, request_id=?, phase=?, notes=?,
                              updated_at=? WHERE review_id=? AND status='pending'""",
                           (c["topic"], c["subtopic"], c["intent"], c["sentiment"], c["severity"],
                            json.dumps(c["entities"], ensure_ascii=False), c["evidence_quote"], int(c["needs_review"]),
                            c["review_reason"], outcome["lc"], outcome["rec"]["request_id"], self.phase, c["notes"],
                            now, rid))
            for rid, err in outcome["invalid"].items():
                db.execute("UPDATE results SET attempts=attempts+1, reason=?, updated_at=? WHERE review_id=?",
                           (err, now, rid))
        self.stats["completed"] += len(outcome["ok"])
        if outcome["rec"] is not None and self.use_global_cache:
            shas = {r["review_id"]: r["text_sha"] for r in self.store.q(
                f"SELECT review_id, text_sha FROM reviews WHERE review_id IN ({','.join('?' * len(outcome['ok']))}) AND synthetic=0",
                list(outcome["ok"]))} if outcome["ok"] else {}
            for rid, c in outcome["ok"].items():
                if rid in shas:
                    result_cache.put(shas[rid], outcome["lc"], rid, c, outcome["rec"], self.run_id)
        rec = outcome["rec"] or {}
        agent = threading.current_thread().name
        a = self.stats["agents"].setdefault(agent, {"batches": 0, "records_ok": 0, "seconds": 0.0, "cost_usd": 0.0})
        a["batches"] += 1
        a["records_ok"] += len(outcome["ok"])
        a["seconds"] += (rec.get("latency_ms") or 0) / 1000
        a["cost_usd"] += rec.get("cost_usd") or 0
        if self.event_log:
            self.event_log("batch", agent=agent, attempt=attempt, model=rec.get("model"), sent=len(batch_ids),
                           ok=len(outcome["ok"]), invalid=len(outcome["invalid"]),
                           invalid_reasons=sorted(set(v.split(":")[0] for v in outcome["invalid"].values())),
                           latency_ms=rec.get("latency_ms"), in_tok=rec.get("input_tokens"), out_tok=rec.get("output_tokens"),
                           cache_read=rec.get("cache_read_input_tokens"), cost_usd=round(rec.get("cost_usd") or 0, 6),
                           request_id=rec.get("request_id"), quiet=True)

    def quarantine(self, rid, reason):
        with self.store.tx() as db:
            db.execute("UPDATE results SET status='quarantined', reason=?, updated_at=? WHERE review_id=? AND status='pending'",
                       (reason[:300], time.time(), rid))
        self.stats["quarantined"] += 1

    def fallback_used(self):
        return self.store.one("SELECT COUNT(*) n FROM results WHERE label_config=? AND cache_source_id IS NULL",
                              (self.lc_fb,))["n"]

    def process(self, batch):
        """Full lifecycle for one batch: call -> save -> retry invalid once -> capped fallback -> quarantine."""
        by_id = {rid: (rid, text, tsha) for rid, text, tsha in batch}
        out = self.run_batch(batch, attempt=0)
        self.save(out, list(by_id), 0)
        bad = list(out["invalid"])
        if bad:
            self.stats["retried_ids"] += len(bad)
            out2 = self.run_batch([by_id[r] for r in bad], attempt=1)
            self.save(out2, bad, 1)
            for rid, err in out2["invalid"].items():
                if self.fallback_used() < self.fallback_cap and not err.startswith("call_failed:budget"):
                    self.stats["fallback_ids"] += 1
                    out3 = self.run_batch([by_id[rid]], attempt=2, role_key="enrich_fallback")
                    self.save(out3, [rid], 2)
                    if rid in out3["invalid"]:
                        self.quarantine(rid, f"invalid_after_retry_and_fallback:{out3['invalid'][rid]}")
                else:
                    self.quarantine(rid, f"invalid_after_retry:{err}")
        return batch

    # ------------------------------------------------------------------ cross-run cache
    def import_global_cache(self):
        """Reuse results produced in earlier runs under the SAME label_config for the SAME review_id (e.g. the 500
        and 10k development runs are nested in the 100k subset). The producing call record is imported too, so the
        record keeps model-call evidence. Other identical texts then reuse it via propagate_cache()."""
        if not self.use_global_cache:
            return 0
        pend = {r["review_id"]: r["text_sha"] for r in self.store.q(
            "SELECT rv.review_id, rv.text_sha FROM results r JOIN reviews rv USING(review_id) WHERE r.status='pending' AND rv.is_empty=0")}
        if not pend:
            return 0
        hits = result_cache.lookup_many(set(pend.values()), [self.lc, self.lc_fb])
        n = 0
        known_calls = {r["request_id"] for r in self.store.q("SELECT request_id FROM calls")}
        run_ids = {r["review_id"] for r in self.store.q("SELECT review_id FROM reviews")}
        with self.store.tx() as db:
            for h in hits.values():
                rid = h["review_id"]
                if rid not in pend or pend[rid] != h["text_sha"]:
                    continue  # producing review not pending in this run -> do new work instead
                call = dict(h["call"])
                if not set(call.get("review_ids", [])) <= run_ids:
                    continue  # evidence would reference foreign IDs
                if call["request_id"] not in known_calls:
                    call["invocation"] = f"imported:{h['run_id']}:{call.get('invocation')}"
                    call["phase"] = call.get("phase") or "initial"  # keep the producing run's phase (initial/resume)
                    self.store.insert_call(db, call)
                    known_calls.add(call["request_id"])
                c = h["result"]
                db.execute("""UPDATE results SET status='completed', topic=?, subtopic=?, intent=?, sentiment=?, severity=?,
                              entities=?, evidence_quote=?, needs_review=?, review_reason=?, label_config=?,
                              cache_source_id=NULL, reason=NULL, request_id=?, phase='initial', notes=?, updated_at=?
                              WHERE review_id=? AND status='pending'""",
                           (c["topic"], c["subtopic"], c["intent"], c["sentiment"], c["severity"],
                            json.dumps(c["entities"], ensure_ascii=False), c["evidence_quote"], int(c["needs_review"]),
                            c["review_reason"], h["label_config"], call["request_id"],
                            f"imported_from_run:{h['run_id']}", time.time(), rid))
                n += 1
        self.stats["imported_from_global_cache"] = n
        if n:
            self.log(f"[enrich] imported {n} results from the cross-run cache (same label_config, same review_id)")
        return n

    def write_progress(self):
        if not self.progress_path:
            return
        c = self.store.one("""SELECT SUM(status='completed') c, SUM(status='pending') p, SUM(status='quarantined') q,
                              SUM(cache_source_id IS NOT NULL) reuse FROM results""")
        el = time.time() - self.t0
        done_now = self.stats["completed"]
        rate = done_now / el if el > 0 else 0
        snap = {"run_id": self.run_id, "invocation": self.inv, "phase": self.phase, "workers": self.workers,
                "completed": c["c"] or 0, "pending": c["p"] or 0, "quarantined": c["q"] or 0,
                "exact_text_reuse": c["reuse"] or 0, "imported_from_global_cache": self.stats["imported_from_global_cache"],
                "batches": self.stats["batches"], "calls_ok": self.stats["calls_ok"], "calls_failed": self.stats["calls_failed"],
                "retried_ids": self.stats["retried_ids"], "fallback_ids": self.stats["fallback_ids"],
                "elapsed_s": round(el, 1), "model_records_per_s": round(rate, 3),
                "eta_s": round((c["p"] or 0) / rate, 0) if rate else None, "ledger": self.ledger.snapshot(),
                "agents": self.stats["agents"], "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        tmp = str(self.progress_path) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f, indent=2)
        import os
        os.replace(tmp, self.progress_path)

    # ------------------------------------------------------------------ outage circuit breaker
    def on_outage(self, err):
        """Network/API outage: pause all dispatch, wait with growing backoff, then let the next batch probe.
        Gives up (graceful stop, everything saved) after MAX_OUTAGE_S of continuous outage."""
        now = time.time()
        self.outage_started = getattr(self, "outage_started", None) or now
        self.outages = getattr(self, "outages", 0) + 1
        self.stats["outage_events"] = self.stats.get("outage_events", 0) + 1
        if now - self.outage_started > config.MAX_OUTAGE_S:
            self.stats["stopped_reason"] = f"outage longer than {config.MAX_OUTAGE_S}s: {err}"
            self.log(f"[enrich] OUTAGE: giving up after {int(now - self.outage_started)}s; progress is saved, rerun to resume")
            self.stop.set()
            return
        wait_s = min(300, 15 * 2 ** min(self.outages - 1, 4))
        self.log(f"[enrich] OUTAGE detected ({str(err)[:120]}); pausing dispatch {wait_s}s, records stay pending")
        self.pause_until = now + wait_s

    # ------------------------------------------------------------------ main loop
    def run(self):
        self.recover_journal()
        self.import_global_cache()
        self.propagate_cache()
        pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="enricher")
        futures = {}
        t0 = time.time()
        try:
            while True:
                if self.stop.is_set():
                    self.stats["stopped_reason"] = self.stats["stopped_reason"] or "stop_requested"
                    break
                free = self.workers - len(futures)
                if getattr(self, "pause_until", 0) > time.time():
                    free = 0  # circuit open: let in-flight work finish, dispatch nothing new
                    if not futures:
                        time.sleep(min(5, self.pause_until - time.time()))
                        continue
                if free > 0:
                    batches = self.next_batches(free)
                    for b in batches:
                        futures[pool.submit(self.process, b)] = b
                if not futures:
                    break
                done, _ = wait(list(futures), return_when=FIRST_COMPLETED)
                for f in done:
                    b = futures.pop(f)
                    try:
                        f.result()
                    except BudgetExceeded as e:
                        self.stats["stopped_reason"] = f"budget: {e}"
                        self.stop.set()
                    except llm.TransientExhausted as e:
                        self.on_outage(e)
                    finally:
                        self.in_flight -= {t for _, _, t in b}
                    self.stats["batches"] += 1
                    if f.exception() is None:
                        self.outage_started, self.outages = None, 0
                    if self.stop_after_calls is not None and self.stats["calls_ok"] >= self.stop_after_calls and not self.stop.is_set():
                        self.log(f"--stop-after {self.stop_after_calls}: requesting graceful stop")
                        self.stats["stopped_reason"] = "stop_after"
                        self.stop.set()
                self.propagate_cache()
                self.write_progress()
                c = self.store.one("SELECT SUM(status='completed') c, SUM(status='pending') p, SUM(status='quarantined') q FROM results")
                self.log(f"[enrich] batches={self.stats['batches']} completed={c['c']} pending={c['p']} "
                         f"quarantined={c['q']} spent=${self.ledger.snapshot()['spent_usd']:.4f} "
                         f"elapsed={time.time() - t0:.0f}s")
        finally:
            for f in list(futures):
                try:
                    f.result()
                except BudgetExceeded as e:
                    self.stats["stopped_reason"] = f"budget: {e}"
            pool.shutdown(wait=True)
            self.propagate_cache()
            self.write_progress()
        self.stats["wall_s"] = round(time.time() - t0, 2)
        return self.stats
