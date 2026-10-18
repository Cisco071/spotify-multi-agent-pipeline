"""Model output schema (sent as JSON schema for structured outputs) and code-side validation."""
import math

from .labels import INTENTS, SUBTOPICS, TOPICS

ALL_SUBTOPICS = sorted({s for subs in SUBTOPICS.values() for s in subs})

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "review_id": {"type": "string"},
        "topic": {"type": "string", "enum": list(TOPICS)},
        "subtopic": {"type": "string", "enum": ALL_SUBTOPICS},
        "intent": {"type": "string", "enum": list(INTENTS)},
        "sentiment": {"type": "number"},
        "severity": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        "entities": {"type": "array", "items": {"type": "string"}},
        "evidence_quote": {"type": "string"},
        "needs_review": {"type": "boolean"},
        "review_reason": {"type": "string"},
    },
    "required": ["review_id", "topic", "subtopic", "intent", "sentiment", "severity", "entities", "evidence_quote",
                 "needs_review", "review_reason"],
    "additionalProperties": False,
}

BATCH_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": ITEM_SCHEMA}},
    "required": ["results"],
    "additionalProperties": False,
}

LABEL_FIELDS = ("topic", "subtopic", "intent", "sentiment", "severity", "entities", "evidence_quote", "needs_review")


def validate_item(item, source_text):
    """Return (clean_record, None) or (None, error_string). Never trusts the model's types."""
    if not isinstance(item, dict):
        return None, "not_object"
    topic, sub, intent = item.get("topic"), item.get("subtopic"), item.get("intent")
    if topic not in TOPICS:
        return None, f"bad_topic:{topic}"
    if sub not in SUBTOPICS[topic]:
        sub = "other"  # subtopic is optional/custom; coerce rather than fail, and record it
        item["_subtopic_coerced"] = True
    if intent not in INTENTS:
        return None, f"bad_intent:{intent}"
    sev = item.get("severity")
    if type(sev) is not int or not 1 <= sev <= 5:
        return None, f"bad_severity:{sev}"
    s = item.get("sentiment")
    if type(s) not in (int, float) or not math.isfinite(s) or not -1 <= s <= 1:
        return None, f"bad_sentiment:{s}"
    ents = item.get("entities")
    if not isinstance(ents, list) or not all(isinstance(e, str) for e in ents):
        return None, "bad_entities"
    ents = [e.strip() for e in ents if e.strip()]
    nr = item.get("needs_review")
    if type(nr) is not bool:
        return None, "bad_needs_review"
    quote = item.get("evidence_quote")
    if not isinstance(quote, str) or not quote.strip():
        return None, "empty_quote"
    if quote not in source_text:
        return None, "quote_not_substring"
    return {
        "topic": topic, "subtopic": sub, "intent": intent, "sentiment": round(float(s), 3), "severity": sev,
        "entities": ents, "evidence_quote": quote, "needs_review": nr,
        "review_reason": str(item.get("review_reason", ""))[:300],
    }, None
