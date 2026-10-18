"""Shared label definitions (GRADING_CONTRACT.md) plus the frozen taxonomy_v1 subtopics."""

TOPICS = ("access", "usability", "playback", "downloads", "catalog", "billing", "support", "other")
INTENTS = ("cancellation", "complaint", "request", "praise", "unclear")  # precedence order
COMPLAINT_INTENTS = ("complaint", "cancellation")

# Optional custom subtopics. issue_id for ranking = "<topic>.<subtopic>".
SUBTOPICS = {
    "access": ("login_failure", "logged_out", "account_hacked_or_locked", "signup", "password_reset", "other"),
    "usability": ("ads", "navigation_layout", "redesign_update", "queue_playlist_management", "shuffle_autoplay",
                  "controls", "widgets_notifications_lockscreen", "other"),
    "playback": ("stops_or_skips", "crashes_or_wont_open", "lag_or_slow", "connection_errors", "audio_quality",
                 "background_play", "device_connectivity", "battery_resource_use", "other"),
    "downloads": ("downloads_disappear", "download_failure", "offline_mode", "storage", "other"),
    "catalog": ("missing_or_removed_content", "search", "recommendations", "lyrics", "podcasts_audiobooks", "other"),
    "billing": ("price", "unexpected_charges", "subscription_activation", "premium_only_controls",
                "cancel_or_refund", "plans_family_student", "other"),
    "support": ("no_response", "unhelpful_response", "contact_difficulty", "other"),
    "other": ("general_praise", "general_criticism", "boycott_political", "unrelated_or_unintelligible", "other"),
}

SUBTOPIC_HELP = {
    "access.login_failure": "cannot log in / sign-in errors",
    "access.logged_out": "app keeps logging the user out",
    "access.account_hacked_or_locked": "account compromised, banned, locked or lost",
    "billing.premium_only_controls": "free tier cannot pick songs, skip limits, forced shuffle when the review frames it as paywalled",
    "usability.shuffle_autoplay": "shuffle/autoplay/smart shuffle behaviour when NOT framed as a paywall",
    "playback.stops_or_skips": "music stops, pauses, skips by itself",
    "playback.connection_errors": "'no internet' / offline errors while online",
    "playback.device_connectivity": "bluetooth, car, Connect, cast, watch, speakers",
}


def issue_id(topic, subtopic):
    return f"{topic}.{subtopic}"
