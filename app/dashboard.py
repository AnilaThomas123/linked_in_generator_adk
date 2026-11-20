import json
import os

from fastapi import Body, FastAPI, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .auth import allowed_email, auth_required
from .config import ROOT, load_topics
from .evaluators import TEMPLATE_LABELS, claims_gate, editorial_gate, hook_gate, stance_gate
from .infographic import create_infographic
from .models import CandidateArticle, Draft, now_iso
from .pipeline import run_once
from .topic_selection import DEFAULT_TOPIC, resolve_topic
from .publishing import confirm_manual_publish, publish_after_approval
from .storage import load_drafts, migrate_json_drafts, save_draft

try:
    from authlib.integrations.starlette_client import OAuth
    from starlette.middleware.sessions import SessionMiddleware
except ImportError:  # pragma: no cover
    OAuth = None
    SessionMiddleware = None

app = FastAPI(title="LinkedIn Editorial Generator")
PENDING: dict[str, Draft] = {}

# Fields a reviewer may change before approval. Sources and extraction stay fixed.
EDITABLE_FIELDS = {"title", "content", "first_comment"}
# Gates that a post edit can change. A failure here blocks approval.
POST_GATES = ("EDITORIAL", "CLAIMS", "HOOK", "STANCE")

if SessionMiddleware:
    app.add_middleware(SessionMiddleware, secret_key=os.getenv("APP_SESSION_SECRET", "dev-only-change-me"))

oauth = OAuth() if OAuth else None
if oauth and os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"):
    oauth.register(
        name="google",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def _require_user(request: Request) -> None:
    if auth_required() and not request.session.get("user"):
        raise HTTPException(401, "Google authentication is required")


def _persist(draft: Draft) -> None:
    target = ROOT / "drafts" / f"{draft.draft_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(draft.model_dump(), indent=2), encoding="utf-8")


def _load_persisted() -> None:
    migrate_json_drafts()
    for draft in load_drafts():
        PENDING.setdefault(draft.draft_id, draft)
    for path in (ROOT / "drafts").glob("*.json"):
        try:
            draft = Draft.model_validate_json(path.read_text(encoding="utf-8"))
            PENDING.setdefault(draft.draft_id, draft)
        except (OSError, ValueError):
            continue


def _latest(draft: Draft, gate: str):
    """The most recent result for a gate. Earlier revisions can leave older entries in the list."""
    hits = [item for item in draft.evaluations if item.gate == gate]
    return hits[-1] if hits else None


def _recheck_post(draft: Draft) -> None:
    """Rerun the editorial and claims gates on the edited post.

    The claims check compares numbers and names against the source excerpts stored with the draft,
    because the full article text is not kept on the draft. A human still reviews the post."""
    _, settings = load_topics()
    source = draft.sources[0]
    candidate = CandidateArticle(
        source_id=source.source_id, topic_id=draft.topic_id, title=source.title, url=source.url,
        publisher=source.publisher, text=" ".join(item.excerpt for item in draft.sources),
        authority_score=source.authority_score,
    )
    kept = [item for item in draft.evaluations if item.gate not in POST_GATES]
    kept.append(editorial_gate(
        draft,
        int(settings.get("max_post_characters", 1300)),
        settings.get("banned_buzzwords", []),
        int(settings.get("minimum_post_characters", 700)),
        settings.get("template_labels", TEMPLATE_LABELS),
        settings.get("executive_signal_terms", []),
    ))
    kept.append(claims_gate(draft, candidate))
    kept.append(hook_gate(draft))
    kept.append(stance_gate(draft))
    draft.evaluations = kept
    draft.evaluator_status = "PASS" if all(item.status == "PASS" for item in kept) else "FAIL"


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> str:
    _require_user(request)
    return """
<!doctype html><html><head><meta charset='utf-8'><title>Editorial Review</title>
<style>
body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem}
article{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:1rem 0}
button{margin-right:.5rem;padding:.5rem .8rem}
pre{white-space:pre-wrap;background:#f6f6f6;padding:1rem}
.meta{color:#555}
.tracker{display:flex;flex-wrap:wrap;align-items:center;gap:.3rem;margin:.5rem 0}
.chip{padding:.2rem .6rem;border-radius:999px;font-size:.85rem;color:#fff}
.PASS{background:#2e7d32}.FAIL{background:#c62828}.PENDING{background:#e09a00}.NOT_RUN{background:#9e9e9e}
.arrow{color:#999}
#runStatus{margin-left:1rem;font-weight:600;color:#1565c0}
#runStatus.error{color:#c62828}
#runStatus .spin{display:inline-block;width:12px;height:12px;border:2px solid #1565c0;border-right-color:transparent;border-radius:50%;margin-right:.4rem;vertical-align:-2px;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
button:disabled{opacity:.5;cursor:wait}
label.disabled{color:#aaa;cursor:not-allowed}
.editor{border-top:1px dashed #bbb;margin-top:1rem;padding-top:1rem}
.editor label{display:block;margin:.5rem 0;font-weight:600}
.editor input,.editor textarea{width:100%;font:15px system-ui;padding:.4rem;box-sizing:border-box}
.result{margin-top:.5rem;color:#333}
</style></head>
<body><h1>LinkedIn Post Workflow</h1><p>The agent drafts posts from news sources. Nothing is posted until you approve it and post it on LinkedIn yourself.</p>
<select id='topic'><option value='cybersecurity' selected>Cybersecurity (default)</option><option value='governance_risk'>AI Governance</option><option value='ai_announcements'>AI Announcements</option></select> <button id='runBtn' onclick='runOnce()'>Generate new drafts</button><span id='runStatus'></span><br><label id='discardLabel' class='disabled' style='margin-top:.5rem;display:inline-block'><input type='checkbox' id='showDiscarded' disabled onchange='load()'> Show discarded <span id='discardHint' class='meta'>(none discarded yet)</span></label><div id='drafts'></div>
<script>
const DRAFTS = {};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function latest(d, gate){const hits=(d.evaluations||[]).filter(e=>e.gate===gate);return hits.length?hits[hits.length-1]:null;}
function gateState(d, gate){const e=latest(d, gate);return e?e.status:'NOT_RUN';}
const DECISION_TEXT={PENDING:'waiting for your decision',APPROVED:'approved',DISCARDED:'discarded'};
const INFO_TEXT={NOT_REQUESTED:'infographic not created yet',GENERATING:'infographic being drawn',READY:'infographic ready',FAILED:'infographic failed'};
const PUB_TEXT={NOT_PUBLISHED:'not posted yet',MANUAL_REQUIRED:'waiting for you to post on LinkedIn',MANUALLY_PUBLISHED:'posted by you on LinkedIn',PUBLISHED:'posted on LinkedIn'};
function statusLine(d){return esc(d.topic_name)+' · '+(DECISION_TEXT[d.decision]||d.decision)+' · '+(PUB_TEXT[d.publication_status]||d.publication_status)+' · '+(INFO_TEXT[d.infographic_status]||d.infographic_status);}
function tracker(d){
  const decision = d.decision==='APPROVED'?'PASS':d.decision==='DISCARDED'?'FAIL':'PENDING';
  const info = {READY:'PASS',FAILED:'FAIL',NOT_REQUESTED:'NOT_RUN',GENERATING:'PENDING'}[d.infographic_status] || 'NOT_RUN';
  const pub = d.publication_status==='NOT_PUBLISHED'?'NOT_RUN':d.publication_status==='MANUAL_REQUIRED'?'PENDING':'PASS';
  const steps = [
    ['Source articles found', d.sources.length?'PASS':'FAIL', 'The news articles the agent used as evidence for this post'],
    ['Matches sources', gateState(d,'FAITHFULNESS'), 'Every claim in the post is supported by the source articles'],
    ['Style and length check', gateState(d,'EDITORIAL'), 'Post length, banned buzzwords and template labels are checked'],
    ['Numbers and names check', gateState(d,'CLAIMS'), 'Figures and names in the post appear in the source text'],
    ['Your approval', decision, 'You approve or discard the draft. Nothing is posted without this'],
    ['Infographic image', info, 'The image card drawn for this post'],
    ['Posted on LinkedIn', pub, 'You copy the post to LinkedIn yourself and confirm it here'],
  ];
  return `<div class='tracker'>${steps.map(([label,state,desc])=>`<span class='chip ${state}' title='${esc(desc)}'>${label}</span>`).join("<span class='arrow'>→</span>")}</div>`;
}
async function runOnce(){
  const t=document.getElementById('topic').value;
  const btn=document.getElementById('runBtn'), status=document.getElementById('runStatus');
  const started=Date.now();
  btn.disabled=true; btn.textContent='Generating drafts…';
  status.className=''; status.innerHTML="<span class='spin'></span>Agent started on '"+esc(t)+"'. This can take several minutes. Do not close this page.";
  const timer=setInterval(()=>{const s=Math.floor((Date.now()-started)/1000);status.innerHTML="<span class='spin'></span>Agent running on '"+esc(t)+"' · "+Math.floor(s/60)+"m "+(s%60)+"s elapsed";},1000);
  try{
    const r=await fetch('/runs?topic='+encodeURIComponent(t),{method:'POST'});
    clearInterval(timer);
    if(!r.ok){status.className='error';status.textContent='Run failed: '+(await r.text());}
    else{const drafts=await r.json();status.textContent='Run finished: '+drafts.length+' draft(s) created.';}
  }catch(e){clearInterval(timer);status.className='error';status.textContent='Run failed: '+e;}
  finally{btn.disabled=false; btn.textContent='Generate new drafts';}
  load();
}
async function act(id, action){const r=await fetch('/drafts/'+id+'/'+action,{method:'POST'}); if(!r.ok){alert(await r.text());} load();}
async function confirmManual(id){const postId=prompt('Optional LinkedIn post ID after manual publishing:')||'';await fetch('/drafts/'+id+'/confirm-manual?linkedin_post_id='+encodeURIComponent(postId),{method:'POST'});load();}
function openEditor(id){
  const slot=document.querySelector(`[data-id='${id}'] .editslot`);
  if(!slot||slot.firstChild) return;
  const d=DRAFTS[id];
  const wrap=document.createElement('div'); wrap.className='editor';
  wrap.innerHTML=`<label>Title<input name='title'></label><label>Post text<textarea name='content' rows='14'></textarea></label><label>First comment<textarea name='first_comment' rows='3'></textarea></label><button class='save'>Save and re-check</button><button class='cancel'>Close</button><div class='result'></div>`;
  wrap.querySelector("[name=title]").value=d.title;
  wrap.querySelector("[name=content]").value=d.content;
  wrap.querySelector("[name=first_comment]").value=d.first_comment||'';
  wrap.querySelector('.save').onclick=()=>saveEdit(id, wrap);
  wrap.querySelector('.cancel').onclick=()=>wrap.remove();
  slot.appendChild(wrap);
}
async function saveEdit(id, wrap){
  const out=wrap.querySelector('.result');
  const body={title:wrap.querySelector("[name=title]").value,content:wrap.querySelector("[name=content]").value,first_comment:wrap.querySelector("[name=first_comment]").value};
  const r=await fetch('/drafts/'+id+'/edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){out.textContent='Not saved: '+(await r.text());return;}
  const d=await r.json();
  DRAFTS[id]=d;
  const checks=['EDITORIAL','CLAIMS'].map(g=>{const e=latest(d,g);return e?`${g}: ${e.status}${e.reasons.length?' ('+e.reasons.join('; ')+')':''}`:`${g}: not run`;});
  out.textContent='Saved. Infographic must be regenerated. '+checks.join(' | ');
  document.querySelector(`[data-id='${id}'] .meta`).innerHTML=statusLine(d);
  document.querySelector(`[data-id='${id}'] .tracker-slot`).innerHTML=tracker(d);
}
async function load(){
  const box=document.getElementById('showDiscarded'), label=document.getElementById('discardLabel'), hint=document.getElementById('discardHint');
  // Check for discarded drafts first so the toggle stays greyed out until one exists.
  const all=await (await fetch('/drafts?include_discarded=true')).json();
  const discardedCount=all.filter(d=>d.decision==='DISCARDED').length;
  box.disabled=discardedCount===0;
  if(discardedCount===0) box.checked=false;
  label.className=discardedCount===0?'disabled':'';
  hint.textContent=discardedCount===0?'(none discarded yet)':'('+discardedCount+' discarded)';
  const inc=box.checked;
  const ds=inc?all:all.filter(d=>d.decision!=='DISCARDED');
  ds.forEach(d=>DRAFTS[d.draft_id]=d);
  document.getElementById('drafts').innerHTML=ds.map(d=>`<article data-id='${esc(d.draft_id)}'>
    <h2>${esc(d.title)}</h2>
    <div class='meta'>${statusLine(d)}</div>
    <div class='tracker-slot'>${tracker(d)}</div>
    <p>${esc(d.abstract)}</p><pre>${esc(d.content)}</pre>
    <p>Source articles: ${d.sources.map(s=>`<a href='${esc(s.url)}' target='_blank'>${esc(s.title)}</a>`).join(', ')}</p>
    ${d.infographic_status==='READY'?`<p><a href='/infographics/${esc(d.draft_id)}' target='_blank'>View infographic image</a></p>`:''}
    ${d.infographic_status==='FAILED'?`<p class='meta'>The infographic could not be drawn: ${esc(d.infographic_error)}</p>`:''}
    ${d.decision==='PENDING'?`<button onclick="openEditor('${esc(d.draft_id)}')">Edit post text</button><button onclick="act('${esc(d.draft_id)}','approve')">Approve this post</button><button onclick="act('${esc(d.draft_id)}','discard')">Discard draft</button>`:''}
    ${d.publication_status==='MANUAL_REQUIRED'?`<p><a href='https://www.linkedin.com/feed/?shareActive=true' target='_blank'>Open LinkedIn to post this approved text</a></p><button onclick="confirmManual('${esc(d.draft_id)}')">I have posted this on LinkedIn</button>`:''}
    <div class='editslot'></div>
  </article>`).join('');
}
load();
</script></body></html>"""


@app.get("/health")
def health(request: Request) -> dict:
    return {"status": "ok"}


@app.get("/auth/login")
async def login(request: Request):
    if not oauth or not oauth.google:
        raise HTTPException(503, "Google OAuth is not configured")
    return await oauth.google.authorize_redirect(request, request.url_for("auth_callback"))


@app.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request):
    if not oauth or not oauth.google:
        raise HTTPException(503, "Google OAuth is not configured")
    token = await oauth.google.authorize_access_token(request)
    user = token.get("userinfo") or await oauth.google.userinfo(token=token)
    email = user.get("email", "")
    if not email or not allowed_email(email):
        raise HTTPException(403, "Google account is not allowlisted")
    request.session["user"] = {"email": email, "name": user.get("name", "")}
    return RedirectResponse("/")


@app.get("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


@app.post("/runs")
def start_run(request: Request, topic: str = DEFAULT_TOPIC) -> list[Draft]:
    _require_user(request)
    topic_id = resolve_topic(topic)
    if topic_id is None:
        raise HTTPException(status_code=400, detail="Unknown topic")
    drafts = run_once(topic_id)
    PENDING.update({draft.draft_id: draft for draft in drafts})
    return drafts


@app.get("/drafts")
def list_drafts(request: Request, include_discarded: bool = False) -> list[Draft]:
    """Discarded drafts are hidden by default. Their audit rows are kept in reports/editorial_audit.csv."""
    _require_user(request)
    _load_persisted()
    drafts = list(PENDING.values())
    if not include_discarded:
        drafts = [draft for draft in drafts if draft.decision != "DISCARDED"]
    return drafts


@app.post("/drafts/{draft_id}/edit")
def edit_draft(request: Request, draft_id: str, changes: dict = Body(...)) -> Draft:
    """Save edits to a pending post, then rerun the editorial and claims gates."""
    _require_user(request)
    draft = PENDING.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft not found")
    if draft.decision != "PENDING":
        raise HTTPException(409, "Only pending drafts can be edited")
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise HTTPException(400, f"Not editable: {', '.join(sorted(unknown))}")
    for field, value in changes.items():
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(400, f"{field} must be non-empty text")
        setattr(draft, field, value.strip())
    _recheck_post(draft)
    # The card was drawn from the old text, so it must be rebuilt after any edit.
    draft.infographic_status = "NOT_REQUESTED"
    draft.infographic_path = None
    draft.infographic_qa = None
    draft.infographic_error = None
    _persist(draft)
    save_draft(draft)
    return draft


class DraftMetrics(BaseModel):
    impressions: int | None = None
    comments: int | None = None
    reposts: int | None = None


@app.post("/drafts/{draft_id}/metrics")
def record_metrics(request: Request, draft_id: str, metrics: DraftMetrics) -> Draft:
    """Record LinkedIn results for a post after it has been published by hand."""
    _require_user(request)
    draft = PENDING.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft not found")
    if draft.publication_status not in ("MANUALLY_PUBLISHED", "PUBLISHED"):
        raise HTTPException(409, "Metrics can only be recorded after the post is published")
    draft.post_impressions = metrics.impressions
    draft.post_comments = metrics.comments
    draft.post_reposts = metrics.reposts
    draft.metrics_recorded_at = now_iso()
    _persist(draft)
    save_draft(draft)
    return draft


@app.post("/drafts/{draft_id}/approve")
def approve(request: Request, draft_id: str) -> Draft:
    _require_user(request)
    draft = PENDING.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft not found")
    blocking = [item for gate in POST_GATES if (item := _latest(draft, gate)) and item.status != "PASS"]
    if blocking:
        reasons = "; ".join(reason for item in blocking for reason in item.reasons) or "a post check failed"
        raise HTTPException(409, f"Fix these before approving: {reasons}")
    draft.decision = "APPROVED"
    draft.approved_at = now_iso()
    draft = create_infographic(draft)
    if draft.infographic_status != "READY":
        # A failed infographic blocks approval: undo the decision and keep the draft pending.
        draft.decision = "PENDING"
        draft.approved_at = None
        # The infographic step may return a new object, so store the reverted draft as the pending one.
        PENDING[draft_id] = draft
        _persist(draft)
        save_draft(draft)
        raise HTTPException(409, f"Approval blocked: the infographic failed its checks ({draft.infographic_error})")
    result = publish_after_approval(draft)
    save_draft(result)
    _persist(result)
    return result


@app.get("/infographics/{draft_id}")
def get_infographic(request: Request, draft_id: str):
    _require_user(request)
    draft = PENDING.get(draft_id)
    if not draft or draft.infographic_status != "READY" or not draft.infographic_path:
        raise HTTPException(404, "Infographic not found")
    path = (ROOT / draft.infographic_path).resolve()
    infographic_root = (ROOT / "infographics").resolve()
    if infographic_root not in path.parents or not path.is_file():
        raise HTTPException(404, "Infographic not found")
    return FileResponse(path, media_type="image/png", filename=path.name)


@app.post("/drafts/{draft_id}/discard")
def discard(request: Request, draft_id: str) -> Draft:
    _require_user(request)
    draft = PENDING.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft not found")
    draft.decision = "DISCARDED"
    from .audit import record
    record(draft, method="human_discard")
    _persist(draft)
    save_draft(draft)
    return draft


@app.post("/drafts/{draft_id}/confirm-manual")
def confirm_manual(request: Request, draft_id: str, linkedin_post_id: str = "") -> Draft:
    _require_user(request)
    draft = PENDING.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft not found")
    result = confirm_manual_publish(draft, linkedin_post_id)
    save_draft(result)
    _persist(result)
    return result
