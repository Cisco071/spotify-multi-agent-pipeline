# Golden-50 error analysis (run `golden-eval`, label_config enrich_v1)

Golden labels were produced blind by the student before any model output on these 50 reviews was seen, and are kept **unchanged** as the official reference. No prompt change has been made based on these results (enrich_v1 was frozen after the 500-review development spot-check).

Each disagreement is categorized: **R** = the human label departs from a written GRADING_CONTRACT rule (model matches the rule); **M** = the model departs from the contract; **G** = genuinely ambiguous under the definitions. Categories were drafted by the assistant and reviewed by the student.

Totals over 27 disagreeing cases (topic, intent or severity): G: 10, M: 2, M/R: 1, M?: 1, R: 11, R/G: 2

## Main findings

- Most topic disagreements are a single systematic labeling-convention difference: the human used `usability` for general praise/hate of the app, whereas the contract assigns general praise/criticism to `other`. This explains 5 of the 20 topic misses.
- Rule boundaries the human labels did not apply: ads → usability, premium-locked controls and price → billing, lyrics → catalog, first specific praised feature (recommendations) → catalog.
- Genuine model errors (M): a request vs complaint confusion for an ad-frequency plea, a missed cancellation for a boycott with explicit personal departure, and a tie-break error (lyrics mentioned first). These motivate watching intent=request on negative pleas and boycott handling at the 10k check.
- Severity: exact agreement 82%, MAE 0.24; disagreements are all ±1 except the boycott case (human 5 vs model 1), where the contract caps severity without financial/privacy/data harm.
- needs_review as a prediction: caught all 4 human-flagged cases (tp 4, fn 0) with 2 extra flags.

## Cases

### 292ce26a-815d-4bce-a3b6-93cb6c9e7e2c — G
> Staff must be full of mentally ill kool-aid heads. Banning conservative music that speaks truth. Spotify is doing all they can to protect pedophiles, by banning Christian songs discussing the path the devil leads you down. Pride is a sin.

- human: usability / complaint / sev 3
- model: other / complaint / sev 2 — quote: "Banning conservative music that speaks truth"
- analysis: Political rant about content removal; contract has no fit beyond other/catalog. Severity 2 vs 3: no functional loss reported to the reviewer.

### a70e8d3e-3f84-46bc-b008-67f476c67963 — R
> Great app use it every chance I get

- human: usability / praise / sev 1
- model: other / praise / sev 1 — quote: "Great app use it every chance I get"
- analysis: General praise -> contract says topic=other.

### 0d4f8b48-74c1-4e2a-89b5-11eaa737027e — R
> It's really an amazing experience with Spotify...I love it🥰

- human: usability / praise / sev 1
- model: other / praise / sev 1 — quote: "It's really an amazing experience with Spotify...I love it🥰"
- analysis: General praise -> other.

### 599a0d41-989a-40ef-8ed2-e92db76eea46 — R
> I just loved it

- human: usability / praise / sev 1
- model: other / praise / sev 1 — quote: "I just loved it"
- analysis: General praise -> other.

### 0c6ca545-3f89-4da6-b3db-1a352371fb6e — R
> Hate this application 🤮🤮🤮🤮🤮🤮

- human: usability / complaint / sev 2
- model: other / complaint / sev 2 — quote: "Hate this application 🤮🤮🤮🤮🤮🤮"
- analysis: Generic hate with no feature -> other/general_criticism.

### 1fc8b08f-7d6a-472f-b317-8ceb28d00f22 — G
> Duo Premium .... No ads at all!

- human: usability / unclear / sev 1 (ambiguous)
- model: usability / praise / sev 1 — quote: "Duo Premium .... No ads at all!"
- analysis: 'Duo Premium .... No ads at all!' reads as praise of ad-free Premium; human marked unclear+ambiguous without an alternative.

### 947821dc-b356-45dc-a0bc-1903728fad40 — G
> Very good but wen I play my song it o ly plays a little bit then it stops I updated my phone and it worked but it happend again they need to fix this issue

- human: playback / complaint / sev 4
- model: playback / complaint / sev 3 — quote: "wen I play my song it o ly plays a little bit then it stops"
- analysis: Music stops after a few seconds: blocked (4) vs degraded (3) - recurring but sometimes works after update.

### 723f07de-9881-4049-8680-1a9e25652914 — G
> Update: I reached out to Spotify Support and with some help from Sarah I was able to get the app working again. Old Post:Normally, the app works great, and I use it constantly. I tried to open it today, and it keeps crashing every time. I've tried uninstalling and reinstalling, and updating. Nothing seems to work unfortunately.

- human: support / praise / sev 4
- model: playback / complaint / sev 4 — quote: "it keeps crashing every time"
- analysis: Crash resolved via support. Model labels the reported crash (playback/complaint/4); human labels the resolution (support/praise). Contract: highest-severity problem -> playback; praise cannot carry severity 4.

### fc344263-308d-48a1-9715-c380d063b6de — G
> Great...

- human: other / unclear / sev 1
- model: other / praise / sev 1 — quote: "Great..."
- analysis: 'Great...' - ellipsis may be sarcasm; model took it literally as praise.

### ac6cd66d-a9d2-42a4-afb8-2f84beff7fd1 — M?
> Lhat ng song nasa spotify

- human: other / unclear / sev 1
- model: catalog / praise / sev 1 — quote: "Lhat ng song nasa spotify"
- analysis: Tagalog 'all the songs are on Spotify' - model read it as catalog praise (plausibly correct); human could not read it and marked unclear/needs_review.

### 46842184-32f8-4088-ac15-2a700a7e94ed — R
> asem, lirik nya kdang gaada kek mana??

- human: usability / complaint / sev 2
- model: catalog / complaint / sev 2 — quote: "lirik nya kdang gaada kek mana??"
- analysis: Indonesian: lyrics sometimes missing -> contract puts lyrics availability under catalog.

### 5de7f95b-ccad-41b2-9f63-a13f7c1d9f09 — M
> We are boycotting Swedish app in our protest against Sweden for their desrecpect & desceration of our holy book Qur'an

- human: other / cancellation / sev 5
- model: other / complaint / sev 1 — quote: "We are boycotting Swedish app in our protest against Sweden"
- analysis: Boycott with explicit personal departure ('we are boycotting') -> contract: cancellation intent; model said complaint. Severity 5 (human) conflicts with contract: no financial/privacy/data harm -> 1.

### de48c99b-46fe-4837-8634-e63dbc72a5ea — R
> I LOVE that I was able to put in only a few songs I like and Spotify gave me recommendations for an AWESOME Playlist...!!!

- human: playback / praise / sev 1
- model: catalog / praise / sev 1 — quote: "Spotify gave me recommendations for an AWESOME Playlist"
- analysis: Praise of recommendations -> catalog.

### 372d4e67-59cc-4c54-879f-3e8a202ee554 — M/R
> Please ye ads ko thoda kumm Karo harr ek song ke baad ad 🙏🙄

- human: playback / complaint / sev 2
- model: usability / request / sev 2 — quote: "Please ye ads ko thoda kumm Karo harr ek song ke baad ad"
- analysis: Ads -> usability (human said playback: rule). Model said request; 'an ad after every song' is a reported annoyance -> complaint (model error).

### f4c8146a-d5c8-4168-b00e-21af666afc6a — G
> Unconditional love, Stoli Canales

- human: other / praise / sev 1
- model: other / unclear / sev 1 — quote: "Unconditional love, Stoli Canales"
- analysis: 'Unconditional love, Stoli Canales' - could be praise or unrelated name.

### 16d640e9-b281-4746-9562-c7dbcf7126b6 — R
> It's my best music app but now it's not...everything basic features is premium....

- human: usability / complaint / sev 3
- model: billing / complaint / sev 3 — quote: "everything basic features is premium"
- analysis: Basic features locked behind Premium -> contract: billing (premium-only controls).

### 8cc4fad4-26ec-47b7-96e8-49c7da714eca — G
> I hate this app and sweden .I love islam

- human: other / complaint / sev 2
- model: other / complaint / sev 1 — quote: "I hate this app and sweden .I love islam"
- analysis: Boycott slogan with 'I hate this app': complaint severity 1 vs 2 (generic criticism = 2 per contract; model under-scored).

### 215463ae-95b7-4481-8747-1754b68f31fa — R
> What is happening with Spotify?? Recently I'm trying to play a song but it doesn't working. So I unstalled and thn again install it now I can't even open the app.. Just showing try again?? I really loved Spotify but this making me to rate it 1 star plz fix the problm as fast as u can

- human: usability / complaint / sev 3
- model: playback / complaint / sev 4 — quote: "now I can't even open the app"
- analysis: Cannot open/play at all -> playback, severity 4 (blocked core task).

### 46c0b49f-83a1-427e-8f0b-cef62e3c94b5 — R/G
> Are you out of your mind? This is dangerous! Listening to music in a normal volume and suddenly an add starts and my ears feel like they explode!! Stop this BS, or you're gonna destroy someones ears.

- human: playback / complaint / sev 2
- model: usability / complaint / sev 3 — quote: "suddenly an add starts and my ears feel like they explode!!"
- analysis: Ad volume -> usability (rule). Severity 2 vs 3: annoyance vs degraded function, ambiguous.

### 2aa566c6-b98d-4513-b900-83702a8db8eb — G
> Nice app, very less advertising

- human: other / praise / sev 1
- model: usability / praise / sev 1 — quote: "Nice app, very less advertising"
- analysis: Praise mentioning fewer ads: first specific praised feature = ads (usability) vs general praise (other).

### 8c0546b0-ce43-4a36-9d33-9f979e98d522 — M
> 1-Lyrics not getting loaded, 2-can't go back to or start song where to wanted. Pathetic update

- human: usability / complaint / sev 2
- model: playback / complaint / sev 3 — quote: "1-Lyrics not getting loaded, 2-can't go back to or start song where to wanted. Pathetic update"
- analysis: Lyrics not loading (first) + can't seek: contract tie-break -> first-mentioned at equal severity -> catalog/lyrics; model chose playback.

### b58dd6f0-ad88-4319-b6db-58afb60c37c1 — R
> The only thing Spotify lacks is the ability to organize saved 
albums/artists into collections. Would definitely deserve 5 stars if it 
included this feature.

- human: other / praise / sev 1
- model: usability / request / sev 1 — quote: "The only thing Spotify lacks is the ability to organize saved 
albums/artists into collections. Would definitely deserve 5 stars if it 
included this "
- analysis: Feature wish without failure -> intent request (model matches contract).

### dceb14e7-2df0-422f-bb2b-73d7c373159a — G
> I gave one star because I can't give any less than that. the new update is sooooooo annoying like I can't even play the songs that I like and I can't rewind songs what the heck

- human: usability / complaint / sev 2
- model: usability / complaint / sev 3 — quote: "the new update is sooooooo annoying like I can't even play the songs that I like and I can't rewind songs"
- analysis: 'can't even play the songs that I like' after update - likely premium-gated selection (billing/usability) - severity 2 vs 3.

### 5fba12ea-4980-4f25-a129-f830a3974fd2 — R
> I find Spotify very good, it finds and play whatever music you want and also adds songs that you might like, judging from your playlist if you choose to arrange one. 😊💃🎸🎶

- human: usability / praise / sev 1
- model: catalog / praise / sev 1 — quote: "it finds and play whatever music you want and also adds songs that you might like"
- analysis: First specific praised feature is finding/recommending music -> catalog.

### ac4e860a-c042-49cc-90f3-189591c336ac — R/G
> paying for subscribing but unable to play in offline mode really?

- human: usability / complaint / sev 3
- model: downloads / complaint / sev 4 — quote: "paying for subscribing but unable to play in offline mode really?"
- analysis: Paying but offline playback fails -> downloads (or billing entitlement); blocked offline listening -> 4 plausible.

### 9034d675-9582-49b6-a1c1-c4ddabf8baf1 — G
> Worst app ever too much ad

- human: other / complaint / sev 2
- model: usability / complaint / sev 2 — quote: "Worst app ever too much ad"
- analysis: 'Worst app ever too much ad': ads is the only specific problem -> usability is defensible; other also defensible.

### d2f3874f-d15d-4a2f-bb97-4f1213defa46 — R
> Too expensive and the free version is pretty much unusable with constant ads... Every two or three songs.. Forget it

- human: usability / complaint / sev 3
- model: billing / complaint / sev 3 — quote: "Too expensive and the free version is pretty much unusable with constant ads"
- analysis: Price mentioned first ('Too expensive') -> billing per tie-break.
