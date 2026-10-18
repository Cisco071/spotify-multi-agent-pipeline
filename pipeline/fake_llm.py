"""Offline stand-in for the Anthropic client, used ONLY by tests / dry runs (PIPELINE_FAKE_LLM=1).

Outputs are synthetic and labeled with model id 'fake-*'; they are never used for business results.
PIPELINE_FAKE_FAIL=0.1 makes ~10% of items invalid, PIPELINE_FAKE_TRANSIENT=0.1 raises transient errors.
"""
import hashlib
import json
import os
import random
import re
import types
import time
import uuid

_START = time.time()


def _h(s):
    return int(hashlib.sha256(s.encode()).hexdigest(), 16)


class _Messages:
    def create(self, model, system, messages, output_config, **kw):
        import anthropic
        content = messages[0]["content"]
        rnd = random.Random(_h(content))
        outage = float(os.environ.get("PIPELINE_FAKE_OUTAGE_SECONDS", "0"))
        if outage and time.time() - _START < outage:
            raise anthropic.APIConnectionError(request=types.SimpleNamespace(method="POST", url="fake-outage"))
        if rnd.random() < float(os.environ.get("PIPELINE_FAKE_TRANSIENT", "0")):
            raise anthropic.APIConnectionError(request=types.SimpleNamespace(method="POST", url="fake"))
        sys_text = system[0]["text"]
        if "MEMO WRITER" in sys_text:
            data = self._memo(content)
        elif "GROUPER" in sys_text:
            items = json.loads(content.split("<issues>\n", 1)[1].rsplit("\n</issues>", 1)[0])
            data = {"issues": [{"issue_id": i["issue_id"], "title": i["issue_id"].replace(".", ": ").replace("_", " "),
                                "description": "Synthetic description.", "coherent": True, "misfit_review_ids": [],
                                "suggested_split": ""} for i in items]}
        else:
            items = json.loads(content.split("<reviews>\n", 1)[1].rsplit("\n</reviews>", 1)[0])
            fail = float(os.environ.get("PIPELINE_FAKE_FAIL", "0"))
            res = []
            for it in items:
                r = random.Random(_h(it["review_text"]))
                topic = r.choice(["playback", "billing", "usability", "other", "access", "catalog"])
                subs = {"playback": "stops_or_skips", "billing": "premium_only_controls", "usability": "ads",
                        "other": "general_criticism", "access": "login_failure", "catalog": "lyrics"}
                quote = it["review_text"][:60]
                if rnd.random() < fail:
                    quote = "NOT IN TEXT " + quote[::-1]
                if "ignore previous instructions" in it["review_text"].lower():
                    topic = "other"
                res.append({"review_id": it["review_id"], "topic": topic, "subtopic": subs[topic],
                            "intent": r.choice(["complaint", "complaint", "praise", "cancellation", "request"]),
                            "sentiment": round(r.uniform(-1, 1), 1), "severity": r.randint(1, 5), "entities": [],
                            "evidence_quote": quote, "needs_review": False, "review_reason": ""})
            data = {"results": res}
        text = json.dumps(data)
        usage = types.SimpleNamespace(input_tokens=len(content) // 4, output_tokens=len(text) // 4,
                                      cache_creation_input_tokens=0, cache_read_input_tokens=len(sys_text) // 4)
        return types.SimpleNamespace(id=f"fake_{uuid.uuid4().hex[:12]}", stop_reason="end_turn", usage=usage,
                                     content=[types.SimpleNamespace(type="text", text=text)])

    @staticmethod
    def _memo(content):
        inputs = json.loads(content.split("<inputs>\n", 1)[1].split("\n</inputs>", 1)[0])
        top = inputs["ranking_top"][:2]
        claims = []
        for i, r in enumerate(top):
            claims += [{"claim_id": f"C{2 * i + 1}", "issue_id": r["issue_id"], "metric": "complaint_count", "value": str(r["complaint_count"])},
                       {"claim_id": f"C{2 * i + 2}", "issue_id": r["issue_id"], "metric": "priority_score", "value": str(r["priority_score"])}]
        ex = inputs["evidence_pack"][0]["examples"][0]["review_id"]
        body = " ".join(f"Issue {c['issue_id']} {c['metric']} is {c['value']} [{c['claim_id']}]." for c in claims)
        body += f" Example (review {ex})."
        return {"recommended_area": "playback", "headline": "SYNTHETIC dry-run memo", "memo_markdown": body,
                "recommendations": [{"title": "Fix top issue", "detail": "Synthetic.", "issue_ids": [top[0]["issue_id"]],
                                     "claim_ids": ["C1"]}],
                "alternatives": [{"area": "billing_support", "assessment": "Synthetic."}],
                "limitations": ["Synthetic dry run."], "claims": claims, "cited_review_ids": [ex]}


class FakeAnthropic:
    def __init__(self):
        self.messages = _Messages()
