"""Build a self-contained offline HTML labeling tool for the golden 50 (no model, no network).

Usage: python evals/build_golden_labeler.py [path/to/golden_50_to_label.csv]
Open evals/golden/labeler.html in a browser, label, click "Download CSV", save as evals/golden/golden_50_labeled.csv.
Labels autosave in the browser's localStorage. Star rating is shown only as metadata; label the TEXT.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "Final Assignment - Spotify Reviews Dataset" / "golden_50_to_label.csv"
rows = list(csv.DictReader(src.open(encoding="utf-8-sig", newline="")))
data = [{k: r[k] for k in ("review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp")} for r in rows]

HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Golden 50 Labeler</title><style>
:root{--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--card:#fff;--bd:#e7e5e4;--acc:#15803d;--warn:#b45309}
@media (prefers-color-scheme:dark){:root{--bg:#1c1917;--fg:#f5f5f4;--mut:#a8a29e;--card:#292524;--bd:#44403c;--acc:#4ade80;--warn:#fbbf24}}
body{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0}
.wrap{max-width:1100px;margin:0 auto;padding:16px;display:grid;grid-template-columns:1fr 340px;gap:16px}
@media(max-width:900px){.wrap{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px;margin-bottom:12px}
.text{font-size:18px;white-space:pre-wrap;background:var(--bg);padding:12px;border-radius:8px;border:1px solid var(--bd)}
label{display:block;font-weight:600;margin:10px 0 4px}.mut{color:var(--mut);font-size:13px}
.opts{display:flex;flex-wrap:wrap;gap:6px}.opts button{border:1px solid var(--bd);background:var(--card);color:var(--fg);border-radius:6px;padding:5px 9px;cursor:pointer}
.opts button.on{background:var(--acc);color:var(--bg);border-color:var(--acc)}
input[type=text],textarea{width:100%;box-sizing:border-box;background:var(--bg);color:var(--fg);border:1px solid var(--bd);border-radius:6px;padding:6px}
.nav{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.nav button{padding:6px 12px}
.dots{display:flex;flex-wrap:wrap;gap:4px;margin-top:8px}.dot{width:22px;height:22px;border-radius:4px;border:1px solid var(--bd);font-size:11px;display:grid;place-items:center;cursor:pointer}
.dot.done{background:var(--acc);color:var(--bg)}.dot.cur{outline:2px solid var(--warn)}
.err{color:var(--warn);font-size:13px}table{font-size:12.5px;border-collapse:collapse}td{border-top:1px solid var(--bd);padding:3px 4px;vertical-align:top}
</style></head><body><div class="wrap"><main>
<div class="card nav"><button id="prev">&larr; Prev</button><b id="pos"></b><button id="next">Next &rarr;</button>
<span class="mut" id="meta"></span><span style="flex:1"></span><button id="dl">Download CSV</button></div>
<div class="card"><div class="text" id="text"></div><div class="mut" style="margin-top:6px">Select part of the text, then click "Use selection" for the evidence quote. Stars are metadata only.</div></div>
<div class="card">
<label>Topic</label><div class="opts" id="topic"></div>
<label>Intent <span class="mut">(precedence: cancellation &gt; complaint &gt; request &gt; praise &gt; unclear)</span></label><div class="opts" id="intent"></div>
<label>Severity</label><div class="opts" id="severity"></div>
<label>Sentiment (−1 … 1)</label><div class="opts" id="sentiment"></div>
<label>Evidence quote <button id="usesel" type="button">Use selection</button> <button id="usefull" type="button">Whole text</button></label>
<textarea id="quote" rows="2"></textarea><div class="err" id="qerr"></div>
<label>Entities <span class="mut">(comma separated, explicit features/products only)</span></label><input type="text" id="entities">
<label><input type="checkbox" id="needs_review"> needs_review (missing context / genuinely unclear)</label>
<label><input type="checkbox" id="ambiguous"> ambiguous case</label>
<label>Also-acceptable topics / intents / severities <span class="mut">(only if ambiguous, e.g. "topic:billing; severity:3")</span></label><input type="text" id="alts">
<label>Notes</label><input type="text" id="notes">
</div></main>
<aside><div class="card"><b>Progress</b><div class="dots" id="dots"></div></div>
<div class="card"><b>Definitions</b><table id="defs"></table></div></aside></div>
<script>
const DATA=__DATA__;
const TOPICS={access:"Login, signup, password or account access",usability:"Navigation, controls, layout, queue/playlist mgmt, ad interruptions",playback:"Playback failure, crashes, lag, connection failures, audio quality, resource use",downloads:"Downloading, saved music, offline listening, disappearing downloads",catalog:"Missing songs/artists, search/discovery, recommendations, lyrics availability",billing:"Price, charges, subscriptions, paywalls, premium entitlement; explicitly premium-only controls",support:"Contacting support and the support response",other:"General praise/criticism, unrelated content, or no supported specific topic"};
const INTENTS={cancellation:"explicitly leaving/uninstalling/cancelling or threatening to",complaint:"negative experience incl. mixed praise/criticism",request:"desired change without a reported failure",praise:"positive",unclear:"boycott slogans, unrelated/meaningless text"};
const SEV={1:"No problem: praise, neutral/unclear, pure feature request",2:"Dislike/generic criticism/minor annoyance; no functional loss",3:"Degraded/restricted function; some use or workaround remains",4:"Clearly blocked core task (can't log in / play)",5:"Explicit serious financial, privacy or data harm"};
const SENT=[-1,-0.6,-0.3,0,0.3,0.6,1];
const KEY="golden50-labels-v1";let S={};try{S=JSON.parse(localStorage.getItem(KEY)||"{}")}catch(e){}
let i=0;const $=id=>document.getElementById(id);
function save(){try{localStorage.setItem(KEY,JSON.stringify(S))}catch(e){}dots()}
function cur(){const id=DATA[i].review_id;return S[id]=S[id]||{}}
function opts(el,vals,field,fmt){$(el).innerHTML="";vals.forEach(v=>{const b=document.createElement("button");b.textContent=fmt?fmt(v):v;b.title=(field=="topic"?TOPICS[v]:field=="intent"?INTENTS[v]:field=="severity"?SEV[v]:"");
 if(String(cur()[field])===String(v))b.className="on";b.onclick=()=>{cur()[field]=v;save();render()};$(el).appendChild(b)})}
function done(id){const s=S[id]||{};return s.topic&&s.intent&&s.severity&&s.sentiment!==undefined&&s.quote&&DATA.find(d=>d.review_id==id).review_text.includes(s.quote)}
function dots(){$("dots").innerHTML="";DATA.forEach((d,k)=>{const e=document.createElement("div");e.className="dot"+(done(d.review_id)?" done":"")+(k==i?" cur":"");e.textContent=k+1;e.onclick=()=>{i=k;render()};$("dots").appendChild(e)})}
function render(){const d=DATA[i],s=cur();$("pos").textContent=`${i+1} / ${DATA.length}`;$("meta").textContent=`${d.review_id.slice(0,8)} · ★${d.review_rating} · v${d.app_version||"—"} · ${d.review_timestamp}`;
 $("text").textContent=d.review_text;opts("topic",Object.keys(TOPICS),"topic");opts("intent",Object.keys(INTENTS),"intent");opts("severity",[1,2,3,4,5],"severity",v=>v+" "+SEV[v].split(/[:;,]/)[0]);opts("sentiment",SENT,"sentiment");
 $("quote").value=s.quote||"";$("entities").value=s.entities||"";$("needs_review").checked=!!s.needs_review;$("ambiguous").checked=!!s.ambiguous;$("alts").value=s.alts||"";$("notes").value=s.notes||"";qcheck();dots()}
function qcheck(){const q=$("quote").value;$("qerr").textContent=q&&!DATA[i].review_text.includes(q)?"Quote is not an exact substring of the review.":""}
["quote","entities","alts","notes"].forEach(f=>$(f).oninput=e=>{cur()[f]=e.target.value;qcheck();save()});
["needs_review","ambiguous"].forEach(f=>$(f).onchange=e=>{cur()[f]=e.target.checked;save()});
$("usesel").onclick=()=>{const t=String(window.getSelection());if(t){cur().quote=t;save();render()}};
$("usefull").onclick=()=>{cur().quote=DATA[i].review_text;save();render()};
$("prev").onclick=()=>{i=Math.max(0,i-1);render()};$("next").onclick=()=>{i=Math.min(DATA.length-1,i+1);render()};
document.addEventListener("keydown",e=>{if(e.target.tagName=="INPUT"||e.target.tagName=="TEXTAREA")return;if(e.key=="ArrowRight")$("next").click();if(e.key=="ArrowLeft")$("prev").click()});
function esc(v){v=String(v??"");return /[",\n\r]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v}
$("dl").onclick=()=>{const cols=["review_id","review_text","review_rating","review_likes","app_version","review_timestamp","topic","intent","sentiment","severity","entities","evidence_quote","needs_review","ambiguous","acceptable_alternatives","notes"];
 const lines=[cols.join(",")];DATA.forEach(d=>{const s=S[d.review_id]||{};lines.push([d.review_id,d.review_text,d.review_rating,d.review_likes,d.app_version,d.review_timestamp,s.topic,s.intent,s.sentiment,s.severity,JSON.stringify((s.entities||"").split(",").map(x=>x.trim()).filter(Boolean)),s.quote,!!s.needs_review,!!s.ambiguous,s.alts,s.notes].map(esc).join(","))});
 const missing=DATA.filter(d=>!done(d.review_id)).length;if(missing&&!confirm(missing+" reviews are incomplete. Download anyway?"))return;
 const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([lines.join("\n")+"\n"],{type:"text/csv"}));a.download="golden_50_labeled.csv";a.click()};
const defs=$("defs");Object.entries(TOPICS).forEach(([k,v])=>defs.innerHTML+=`<tr><td><b>${k}</b></td><td>${v}</td></tr>`);Object.entries(SEV).forEach(([k,v])=>defs.innerHTML+=`<tr><td><b>sev ${k}</b></td><td>${v}</td></tr>`);
defs.innerHTML+=`<tr><td colspan=2 class=mut>Pick the highest-severity specific problem; tie → first mentioned. Praise → first specific praised feature (general praise = other). Paid plan mention alone ≠ billing. Stars/anger/cancellation alone don't set severity.</td></tr>`;
render();
</script></body></html>"""
out = ROOT / "evals" / "golden" / "labeler.html"
out.write_text(HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
print("wrote", out)
