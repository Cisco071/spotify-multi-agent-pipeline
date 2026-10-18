# Shared labels (GRADING_CONTRACT.md) and how we applied them

Primary labels are the contract's exact eight topics and five intents; severity is the shared 1–5 scale.
Subtopics (`taxonomy_v1`) are our optional custom layer; ranking issues are `issue_id = <topic>.<subtopic>`.
The full model-facing text is in [`enrich_v1.md`](enrich_v1.md) (enricher) and [`verify_v1.md`](verify_v1.md)
(verifier, same definitions, separate instructions). All examples come from development data
(`checkpoint_500.csv`), never from the golden set.

## Topics and subtopics

| topic | contract definition | subtopics |
|---|---|---|
| access | login, signup, password or account access | login_failure, logged_out, account_hacked_or_locked, signup, password_reset, other |
| usability | navigation, controls, layout, queue/playlist management, ad interruptions | ads, navigation_layout, redesign_update, queue_playlist_management, shuffle_autoplay, controls, widgets_notifications_lockscreen, other |
| playback | playback failure, crashes, lag, connection failures, audio quality, resource use | stops_or_skips, crashes_or_wont_open, lag_or_slow, connection_errors, audio_quality, background_play, device_connectivity, battery_resource_use, other |
| downloads | downloading, saved music, offline listening, disappearing downloads | downloads_disappear, download_failure, offline_mode, storage, other |
| catalog | missing songs/artists, search/discovery, recommendations, lyrics availability | missing_or_removed_content, search, recommendations, lyrics, podcasts_audiobooks, other |
| billing | price, charges, subscriptions, paywalls, premium entitlement; explicitly premium-only controls | price, unexpected_charges, subscription_activation, premium_only_controls, cancel_or_refund, plans_family_student, other |
| support | contacting support and the support response | no_response, unhelpful_response, contact_difficulty, other |
| other | general praise/criticism, unrelated content, no supported specific topic | general_praise, general_criticism, boycott_political, unrelated_or_unintelligible, other |

Rules: highest-severity specific problem wins, ties go to the first mentioned; praise takes the first specific
praised feature (general praise = other); a paid-plan mention alone is not billing.

## Intent (precedence order)
cancellation → complaint → request → praise → unclear. Bare boycott slogans and meaningless text are unclear;
generic "bad app" is a complaint with topic other.

## Severity
1 no problem / praise / unclear / pure request · 2 dislike or generic criticism · 3 degraded or restricted function
· 4 clearly blocked core task · 5 explicit serious financial, privacy or data harm. Stars, anger and cancellation
intent never raise severity by themselves.

## Worked examples (development data)

| review (abridged) | topic / subtopic | intent | sev | why |
|---|---|---|---|---|
| "The very second I signed up for premium it stopped letting me play anything… Bye bye Spotify" | billing / subscription_activation | cancellation | 4 | paid, cannot play; departure stated |
| "this app is great. But … it gives too many ads" | usability / ads | complaint | 2 | mixed → complaint; annoyance only |
| "I paid my subscription and im still getting a pause subscription screen and can't access my music" | billing / subscription_activation | complaint | 4 | entitlement failure blocks listening |
| "Why are lyrics premium!?" | billing / premium_only_controls | complaint | 3 | paywalled feature, app still usable |
| "Nice app but why can't the lyrics of song on my playlist load?" | catalog / lyrics | complaint | 3 | degraded feature |
| "Always say no internet connection. Most of my favourite Bollywood songs are not present" | playback / connection_errors | complaint | 4 | first and most severe problem |
| "When I playing song the app is shoutdowning" | playback / crashes_or_wont_open | complaint | 4 | crash blocks playback |
| "Time to uninstall… reason : Indian people knows that" | other / general_criticism | cancellation | 1 | departure, no stated defect → needs_review |
| "Garbage app nothing is cool" | other / general_criticism | complaint | 2 | generic criticism |
| "Boycott Spotify!!" | other / boycott_political | unclear | 1 | bare slogan |

## Output schema (`schema_v1`)
Per review: `review_id, topic, subtopic, intent, sentiment (-1..1), severity (1..5), entities[], evidence_quote
(exact substring), needs_review, review_reason`. Code validates every field and the exact-substring quote; the
exported record adds `source_sha256`, `status` and `label_config`.
