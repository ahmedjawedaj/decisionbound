"""Optional authenticated, loopback-only control API for shared local clients.

The web layer is intentionally thin: CLI and browser use exactly the same
services. There is no login SaaS, cloud synchronization or external API by
default. A non-loopback client cannot gain access by merely knowing a project ID.
"""
from __future__ import annotations
import secrets
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from urllib.parse import urlsplit
from pydantic import BaseModel, Field
from .identity import load_project
from .profiles import ProfileManager
from .services import DecisionService
from .runtime import Supervisor
from .settings import repository, state_dir
from .local_auth import local_token


class TaskRequest(BaseModel):
    """Validated input for safe offline task preparation."""
    project_path: str
    task: str = Field(min_length=1, max_length=20000)
    max_tokens: int = Field(default=1200, ge=100, le=100000)


class LocalExportReviewRequest(BaseModel):
    """Local-user approval bound to an exact review fingerprint and revision."""
    fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    revision: int = Field(ge=0)
    acknowledge_unverified: bool = False
    confirm_export_only: bool = False


def create_app() -> FastAPI:
    """Build a fresh API instance for tests or a local runtime process."""
    app = FastAPI(title="DecisionBound Local Control", version="0.10.0a1")
    token = local_token()

    @app.middleware("http")
    async def require_local_origin(request: Request, call_next):
        """Reject remote Host and unsafe cross-origin access before routes."""
        host = request.headers.get("host", "")
        # Parse the supplied authority rather than a prefix match, which could
        # accept an attacker-controlled hostname beginning with localhost.
        authority = urlsplit("http://" + host)
        origin = request.headers.get("origin", "")
        if authority.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            return JSONResponse({"detail": "Only localhost requests are allowed"}, status_code=403)
        if origin:
            source = urlsplit(origin)
            if source.scheme != "http" or source.hostname not in {"localhost", "127.0.0.1", "::1"}:
                return JSONResponse({"detail": "Cross-origin requests are forbidden"}, status_code=403)
        return await call_next(request)

    def authorized(authorization: str | None) -> None:
        if not authorization or not secrets.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401, "Valid local bearer token required")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        """Serve a small local UI without loading any remote JavaScript."""
        return """<!doctype html><html lang="en"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>DecisionBound — Local Control</title><style>
        :root{color-scheme:dark;font-family:system-ui,Arial,sans-serif;background:#0b1220;color:#eaf1fa}
        body{max-width:1100px;margin:auto;padding:30px 22px}
        h1{font-size:1.8rem;letter-spacing:-.05em}h2{font-size:1.1rem}
        .muted{color:#a7b7cb}.layout{display:grid;grid-template-columns:1fr 1fr;gap:16px}
        .panel{border:1px solid #2a3b55;background:#131f31;padding:18px;border-radius:12px;margin-bottom:15px}
        label{display:block;font-weight:600;font-size:.85rem;margin:12px 0 4px}
        input,select{width:100%;max-width:100%;padding:12px;border:1px solid #34465d;background:#0d1829;color:white;border-radius:7px}
        button{padding:11px 16px;border:0;border-radius:7px;background:#91d5f0;color:#102132;font-weight:700;cursor:pointer;margin-top:15px}
        button:focus,input:focus{outline:2px solid #91d5f0;outline-offset:2px}
        .node{border-left:3px solid #607d9a;padding:9px 12px;margin:8px 0;background:#101c2d;border-radius:4px}
        .node.verified{border-color:#5bbf92}.node.blocked,.node.failed{border-color:#e8a47d}
        .node.mock_passed,.node.needs_real_tests{border-color:#dfc47c}
        pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#101c2d;padding:12px;border-radius:7px;max-height:500px;overflow:auto}
        .event{border-bottom:1px solid #2b3b53;padding:7px 0;font-size:.86rem}
        .warning{border-left:3px solid #efaa83;background:#2b242a;padding:10px;margin-top:8px}
        .review-buttons{display:flex;gap:9px;flex-wrap:wrap}
        .review-buttons button{font-size:.83rem}
        .review-buttons button:disabled{opacity:.4;cursor:not-allowed}
        .review-buttons .danger{background:#f1a4a4}
        .review-buttons .quiet{background:#bed2e0}
        .check{display:flex;gap:10px;align-items:start;font-size:.9rem;margin:12px 0}
        .check input{width:auto;margin:3px 0 0 0}
        .diff{max-height:410px;overflow:auto;font-size:.82rem;white-space:pre}
        @media(max-width:720px){.layout{display:block}body{padding:16px}}
        </style></head><body>
        <h1>DecisionBound <span class="muted">/ Local Control</span></h1>
        <p class="muted">Shared profiles and read-only workflow inspection. The browser cannot authorize code writes, workers or paid calls.</p>
        <section class="panel"><label for="token">Local pairing token</label>
          <input type="password" id="token" autocomplete="off" placeholder="Stored in private local runtime state">
          <label for="workflow">Workflow ID</label>
          <input id="workflow" placeholder="wf_..." autocomplete="off">
          <button id="inspect" type="button">Inspect workflow</button>
          <button id="profiles" type="button">View profiles</button>
          <p id="feedback" role="status" aria-live="polite"></p>
        </section>
        <section class="panel" id="setup-panel"><h2>Profile setup and readiness</h2>
          <p class="muted">Select an end-user profile. This does not validate provider authentication, call a model, enable network access, or approve writes.</p>
          <label for="setup-profile">Profile</label>
          <select id="setup-profile" aria-label="Selected profile"></select>
          <div class="review-buttons"><button id="setup-load" type="button" class="quiet">Load profiles</button>
            <button id="setup-activate" type="button">Use selected profile</button>
            <button id="setup-check" type="button" class="quiet">Check readiness</button></div>
          <pre id="setup-status" role="status" aria-live="polite">No setup report loaded.</pre>
        </section>
        <section class="panel" id="sessions-panel"><h2>Shared sessions · CLI / Browser / VS Code</h2>
          <p class="muted">Enter the project ID from <code>dbound status</code>. Only authenticated, project-scoped workflow metadata is displayed. Refreshes never execute agents.</p>
          <label for="project-id">Initialized project ID</label>
          <input id="project-id" placeholder="prj_..." autocomplete="off">
          <div class="review-buttons"><button type="button" id="sessions-list">List sessions</button>
            <button type="button" id="sessions-watch" class="quiet">Watch changes</button>
            <button type="button" id="sessions-stop" class="quiet">Stop watching</button></div>
          <p id="sessions-feedback" role="status" aria-live="polite"></p>
          <div id="session-list" aria-label="Available project sessions"></div>
          <pre id="session-changes" aria-label="Project event changes">No events loaded.</pre>
        </section>
        <div class="layout"><section class="panel"><h2>Task graph</h2><div id="graph" aria-live="polite"></div></section>
        <section class="panel"><h2>Audited event timeline</h2><div id="events"></div></section></div>
        <section class="panel" id="review-panel"><h2>Patch review &amp; export approval</h2>
          <p class="muted">Local user approval authorizes exporting this exact patch only. Never commits, merges, deploys, or certifies the task. Raw diff is requested only when you click Preview patch.</p>
          <div class="review-buttons">
            <button id="review-load" type="button" class="quiet">Load review metadata</button>
            <button id="review-preview" type="button" class="quiet">Preview patch</button>
          </div>
          <pre id="review-metadata" aria-live="polite">No patch loaded</pre>
          <pre id="review-diff" class="diff" aria-label="Source-code patch preview">Patch preview is opt-in.</pre>
          <div class="warning" id="review-warning" role="status">Review approval is disabled until this exact patch is inspected.</div>
          <label class="check"><input id="review-confirm" type="checkbox">I reviewed the patch and explicitly approve exporting its exact bytes.</label>
          <label class="check"><input id="review-unverified" type="checkbox">I acknowledge the tests or outcome are unverified (required when applicable).</label>
          <div class="review-buttons">
            <button id="review-approve" type="button">Approve export only</button>
            <button id="review-reject" type="button" class="danger">Reject patch</button>
            <button id="review-revoke" type="button" class="quiet">Revoke approval</button>
          </div>
          <p class="muted" id="review-result" role="status" aria-live="polite"></p>
        </section>
        <script>
        const token=document.getElementById('token'),workflow=document.getElementById('workflow');
        const graph=document.getElementById('graph'),events=document.getElementById('events');
        const feedback=document.getElementById('feedback');
        async function get(path){
           const response=await fetch(path,{headers:{Authorization:'Bearer '+token.value}});
           const value=await response.json();
           if(!response.ok)throw Error(value.detail||'API request failed');
           return value;
        }
        function line(host, text, name){const el=document.createElement('div');el.className=name||'';
           el.textContent=text;host.appendChild(el);return el}
        const setupSelect=document.getElementById('setup-profile');
        const setupReport=document.getElementById('setup-status');
        async function loadProfileOptions(){const current=await get('/api/profiles');
          setupSelect.replaceChildren();
          for(const name of current.profiles){const option=document.createElement('option');
            option.value=name;option.textContent=name;setupSelect.appendChild(option);}
          setupSelect.value=current.active;
          setupReport.textContent='Active profile: '+current.active+'\\nNo model calls were made.';
        }
        async function inspectReadiness(){const report=await get('/api/setup/v1/status');
          setupReport.textContent='Profile: '+report.profile+'\\nPrerequisites: '+
            (report.ready?'present (not provider-tested)':'incomplete')+'\\nNext: '+report.next_action+
            '\\nNetwork/model use: not authorized by setup';
          for(const check of report.checks){
            setupReport.textContent+='\\n'+(check.ok?'OK':'NOT CONFIRMED')+' · '+check.detail;}
        }
        document.getElementById('setup-load').onclick=async()=>{try{await loadProfileOptions()}
          catch(error){setupReport.textContent='Unable to load profiles: '+error.message}};
        document.getElementById('setup-check').onclick=async()=>{try{await inspectReadiness()}
          catch(error){setupReport.textContent='Unable to inspect setup: '+error.message}};
        document.getElementById('setup-activate').onclick=async()=>{try{
          const name=setupSelect.value;
          if(!/^[a-z][a-z0-9_-]{1,63}$/.test(name) ||
             !Array.from(setupSelect.options).some(x=>x.value===name))
            throw Error('Load and select a known profile first');
          const response=await fetch('/api/profiles/'+encodeURIComponent(name)+'/activate',{
            method:'POST',headers:{Authorization:'Bearer '+token.value}});
          if(!response.ok)throw Error('Profile selection refused (HTTP '+response.status+')');
          await loadProfileOptions();await inspectReadiness();
        }catch(error){setupReport.textContent='Cannot select profile: '+error.message}};
        document.getElementById('inspect').onclick=async()=>{
          graph.replaceChildren();events.replaceChildren();feedback.textContent='Loading metadata…';
          try{const id=encodeURIComponent(workflow.value.trim());
            if(!/^wf_[a-f0-9]{32}$/.test(workflow.value.trim()))throw Error('Enter a workflow ID from the CLI');
            const [status,trace]=await Promise.all([get('/api/workflows/'+id),get('/api/workflows/'+id+'/events')]);
            feedback.textContent='Workflow '+status.state+' · '+status.tasks.length+' tasks · completion NOT asserted';
            for(const task of status.tasks){
              const el=line(graph,task.task_id+' · '+task.role+' · '+task.state,'node '+task.state);
              if(task.depends_on.length)line(el,'After: '+task.depends_on.join(', '),'muted');
              if(task.error_type)line(el,'Reason: '+task.error_type,'muted');
            }
            for(const e of trace.slice(-40))line(events,'#'+e.id+' '+(e.task_id||'workflow')+' — '+e.event,'event');
          }catch(error){feedback.textContent='Unable to inspect workflow: '+error.message}
        };
        let reviewState=null,patchWasPreviewed=false;
        const reviewMeta=document.getElementById('review-metadata');
        const reviewDiff=document.getElementById('review-diff');
        const reviewResult=document.getElementById('review-result');
        const confirmReview=document.getElementById('review-confirm');
        const acknowledge=document.getElementById('review-unverified');
        function reviewPath(){const id=workflow.value.trim();
          if(!/^wf_[a-f0-9]{32}$/.test(id))throw Error('Enter a workflow ID from the CLI');
          return '/api/workflows/'+encodeURIComponent(id)+'/review';}
        function clearReview(){reviewState=null;patchWasPreviewed=false;
          confirmReview.checked=false;acknowledge.checked=false;
          reviewDiff.textContent='Patch preview is opt-in.';reviewMeta.textContent='No patch loaded';}
        async function loadReview(withDiff){
          const data=await get(reviewPath()+(withDiff?'?include_diff=true':''));
          if(!reviewState || reviewState.fingerprint!==data.fingerprint){
            confirmReview.checked=false;acknowledge.checked=false;patchWasPreviewed=false;
          }
          reviewState=data;
          if(withDiff){reviewDiff.textContent=data.diff;patchWasPreviewed=true;}
          reviewMeta.textContent='Patch SHA-256: '+data.patch_sha256+'\\nSnapshot: '+data.fingerprint+
            '\\nFiles: '+data.paths.join(', ')+'\\nChanges: +'+data.added_lines+' / -'+data.removed_lines+
            '\\nEvidence: '+(data.contract_verified?'Independently verified':'NOT independently verified')+
            '\\nDecision: '+data.approval.decision+' (revision '+data.approval.revision+')';
          document.getElementById('review-warning').textContent=data.contract_verified?
            'Tests verified for the specified contract only; semantic task completion is not certified.':
            'WARNING: tests or evidence are unverified. Approval requires explicit acknowledgment.';
          reviewResult.textContent=withDiff?'Preview loaded. Verify changed lines before approval.':'Review metadata loaded.';
        }
        async function decide(action){
          if(!reviewState)throw Error('Load review metadata first');
          const isApproval=action==='approve_export';
          if(isApproval && (!patchWasPreviewed || !confirmReview.checked))
            throw Error('Preview the patch and confirm export approval first');
          if(isApproval && reviewState.export_requires_unverified_acknowledgment && !acknowledge.checked)
            throw Error('Explicit acknowledgment of unverified tests is required');
          if(action==='revoke' && reviewState.approval.decision!=='approve_export')
            throw Error('No active approval to revoke');
          const response=await fetch(reviewPath()+'/'+action,{method:'POST',
            headers:{Authorization:'Bearer '+token.value,'Content-Type':'application/json'},
            body:JSON.stringify({fingerprint:reviewState.fingerprint,
              revision:reviewState.approval.revision,
              acknowledge_unverified:isApproval&&acknowledge.checked,
              confirm_export_only:isApproval&&confirmReview.checked})});
          const body=await response.json();if(!response.ok)throw Error(body.detail||'Review action failed');
          clearReview();await loadReview(false);
          reviewResult.textContent='Recorded '+body.decision+' at revision '+body.revision;
        }
        document.getElementById('review-load').onclick=async()=>{try{
          clearReview();await loadReview(false);
        }catch(error){reviewResult.textContent='Review failed: '+error.message}};
        document.getElementById('review-preview').onclick=async()=>{try{
          await loadReview(true);
        }catch(error){reviewResult.textContent='Preview failed: '+error.message}};
        for(const [id,action] of [['review-approve','approve_export'],['review-reject','reject'],['review-revoke','revoke']]){
          document.getElementById(id).onclick=async()=>{try{await decide(action)}
            catch(error){reviewResult.textContent='Action refused: '+error.message}};
        }
        const projectInput=document.getElementById('project-id');
        const sessionList=document.getElementById('session-list');
        const sessionChanges=document.getElementById('session-changes');
        const sessionFeedback=document.getElementById('sessions-feedback');
        let changeCursor=0,watchTimer=null,watchProject=null,changesInFlight=false;
        function projectId(){const id=projectInput.value.trim();
          if(!/^prj_[0-9a-f]{32}$/.test(id))throw Error('Enter a project ID from dbound status');
          return id;}
        function stopWatch(){if(watchTimer!==null)clearInterval(watchTimer);
          watchTimer=null;watchProject=null;}
        async function listSessions(){const id=projectId();
          const data=await get('/api/client/v1/sessions?project_id='+encodeURIComponent(id)+'&limit=50');
          if(id!==projectInput.value.trim())return;
          sessionList.replaceChildren();
          for(const entry of data.sessions){
            const button=document.createElement('button');button.type='button';button.className='quiet';
            button.textContent=entry.session_id+' · '+entry.state+' · '+entry.task_count+' tasks';
            button.onclick=()=>{workflow.value=entry.session_id;workflow.dispatchEvent(new Event('input'));
              document.getElementById('inspect').click();};
            sessionList.appendChild(button);
          }
          sessionFeedback.textContent=data.sessions.length+' sessions found (first page)';}
        async function pollChanges(){if(changesInFlight)return;
          const id=projectId();if(watchProject!==id)return;changesInFlight=true;
          try{let pages=0,latest=[];let result;
            do{result=await get('/api/client/v1/changes?project_id='+encodeURIComponent(id)+
                 '&after='+changeCursor+'&limit=100');
              if(watchProject!==id)return;
              latest.push(...result.events);changeCursor=result.next_after;pages++;
            }while(result.has_more&&pages<5);
            for(const e of latest){const entry='#'+e.id+' '+e.session_id+' · '+e.event+'\\n';
              sessionChanges.textContent=(sessionChanges.textContent==='No events loaded.'?'':sessionChanges.textContent)+entry;}
            if(sessionChanges.textContent.length>12000)
              sessionChanges.textContent=sessionChanges.textContent.slice(-12000);
          }finally{changesInFlight=false;}
        }
        document.getElementById('sessions-list').onclick=async()=>{try{await listSessions()}
          catch(error){sessionFeedback.textContent='Session lookup failed: '+error.message}};
        document.getElementById('sessions-watch').onclick=async()=>{try{
          stopWatch();watchProject=projectId();changeCursor=0;
          sessionChanges.textContent='';await pollChanges();
          watchTimer=setInterval(()=>pollChanges().catch(error=>{
            stopWatch();sessionFeedback.textContent='Sync stopped: '+error.message;
          }),5000);
          sessionFeedback.textContent='Watching project changes (5-second polling)';
        }catch(error){stopWatch();sessionFeedback.textContent='Watch failed: '+error.message}};
        document.getElementById('sessions-stop').onclick=()=>{stopWatch();sessionFeedback.textContent='Watch stopped'};
        projectInput.addEventListener('input',()=>{stopWatch();changeCursor=0;
          sessionList.replaceChildren();sessionChanges.textContent='No events loaded.';});
        workflow.addEventListener('input',clearReview);
        document.getElementById('profiles').onclick=async()=>{try{
          const result=await get('/api/profiles');feedback.textContent='Active profile: '+result.active;
          graph.replaceChildren();events.replaceChildren();
          for(const name of result.profiles)line(graph,name,'node');
        }catch(error){feedback.textContent='Unable to inspect profiles: '+error.message}};
        </script></body></html>"""

    @app.get("/api/health")
    def health(authorization: str | None = Header(None)) -> dict:
        """Report availability without disclosing private user state."""
        authorized(authorization)
        return {"status": "ok", "mode": "local-offline"}

    @app.get("/api/client/v1/capabilities")
    def client_capabilities(authorization: str | None = Header(None)) -> dict:
        """Declare actual supported synchronization features to editor clients."""
        authorized(authorization)
        from .client_sync import LocalClientSync
        return LocalClientSync.capabilities()

    @app.get("/api/client/v1/sessions")
    def client_sessions(project_id: str, limit: int = Query(default=25, ge=1, le=100),
                        before: int | None = Query(default=None, ge=1),
                        authorization: str | None = Header(None)) -> dict:
        """Paginate one project's durable workflow sessions; no raw task text."""
        authorized(authorization)
        from .client_sync import LocalClientSync, LocalSyncError
        try:
            return LocalClientSync(repository()).list_sessions(
                project_id, limit=limit, before=before)
        except LocalSyncError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/client/v1/sessions/{session_id}")
    def client_session(session_id: str, project_id: str,
                       authorization: str | None = Header(None)) -> dict:
        """Read a journal snapshot using explicit project-scoped identity."""
        authorized(authorization)
        from .client_sync import LocalClientSync, LocalSyncError
        try:
            return LocalClientSync(repository()).snapshot(project_id, session_id)
        except LocalSyncError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/client/v1/sessions/{session_id}/review")
    def client_review(session_id: str, project_id: str,
                      authorization: str | None = Header(None)) -> dict:
        """Inspect current export approval metadata; never serve a code diff."""
        authorized(authorization)
        from .client_sync import LocalClientSync, LocalSyncError
        try:
            return LocalClientSync(repository()).review_state(project_id, session_id)
        except LocalSyncError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/client/v1/sessions/{session_id}/events")
    def client_session_events(session_id: str, project_id: str,
                              after: int = Query(default=0, ge=0),
                              limit: int = Query(default=50, ge=1, le=100),
                              authorization: str | None = Header(None)) -> dict:
        """Replay an exact event cursor without duplicating already seen events."""
        authorized(authorization)
        from .client_sync import LocalClientSync, LocalSyncError
        try:
            return LocalClientSync(repository()).session_events(
                project_id, session_id, after=after, limit=limit)
        except LocalSyncError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/client/v1/changes")
    def client_changes(project_id: str, after: int = Query(default=0, ge=0),
                       limit: int = Query(default=50, ge=1, le=100),
                       authorization: str | None = Header(None)) -> dict:
        """Read the project's incrementally ordered global metadata change feed."""
        authorized(authorization)
        from .client_sync import LocalClientSync, LocalSyncError
        try:
            return LocalClientSync(repository()).changes(
                project_id, after=after, limit=limit)
        except LocalSyncError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/setup/v1/status")
    def setup_status(authorization: str | None = Header(None)) -> dict:
        """Return credential-free end-user prerequisite diagnostics.

        This route never accepts an arbitrary filesystem path, returns secret
        values, contacts providers, or changes setup/consent state.
        """
        authorized(authorization)
        from .onboarding import SetupService
        return SetupService(repository()).inspect().to_dict()

    @app.get("/api/profiles")
    def profiles(authorization: str | None = Header(None)) -> dict:
        """Return the same active profile that the CLI reads."""
        authorized(authorization)
        manager = ProfileManager(repository())
        return {"active": manager.active(), "profiles": manager.names()}

    @app.post("/api/profiles/{name}/activate")
    def activate(name: str, authorization: str | None = Header(None)) -> dict:
        """Select a validated profile for subsequent CLI and browser runs."""
        authorized(authorization)
        try:
            return ProfileManager(repository()).use(name)
        except KeyError:
            raise HTTPException(404, "Unknown profile")

    @app.post("/api/prepare")
    def prepare(body: TaskRequest, authorization: str | None = Header(None)) -> dict:
        """Build a task capsule without contacting a provider or writing code."""
        authorized(authorization)
        store = repository()
        try:
            project = load_project(Path(body.project_path), store)
            return DecisionService(store).prepare(body.task, project, body.max_tokens).to_dict()
        except (ValueError, FileNotFoundError, PermissionError) as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/run")
    def run(body: TaskRequest, authorization: str | None = Header(None)) -> dict:
        """Execute the offline preparation worker with durable event tracing."""
        authorized(authorization)
        store = repository()
        try:
            project = load_project(Path(body.project_path), store)
            capsule = DecisionService(store).prepare(body.task, project, body.max_tokens)
            return Supervisor(store).run(body.task, capsule)
        except (ValueError, FileNotFoundError, PermissionError) as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/agent/demo")
    def demo_agent(body: TaskRequest, authorization: str | None = Header(None)) -> dict:
        """Run a deterministic read-only agent rehearsal with trace receipts.

        Deliberately no remote-model endpoint or API key accepted by browser.
        """
        authorized(authorization)
        from .execution import run_readonly_agent
        from .model_planner import ScriptedModelTransport, Pricing
        import json
        search = json.dumps({"kind": "tool", "tool": "search_project",
                             "arguments_json": json.dumps({"query": body.task[:100]}), "message": ""})
        finish = json.dumps({"kind": "finish", "tool": None, "arguments_json": "{}",
                             "message": "Inspected project; user task not independently completed."})
        try:
            return run_readonly_agent(project_path=Path(body.project_path), store=repository(),
                task=body.task, transport=ScriptedModelTransport([search, finish]),
                pricing=Pricing(0, 0), max_context_tokens=body.max_tokens)
        except (ValueError, FileNotFoundError, PermissionError) as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/repair/{session_id}")
    def repair_status(session_id: str, authorization: str | None = Header(None)) -> dict:
        """Read shared durable repair status; no browser endpoint can run writes."""
        authorized(authorization)
        from .code_journal import SQLiteRepairJournal
        from .settings import state_dir
        try:
            return SQLiteRepairJournal(repository(), state_dir()).get(session_id).to_dict()
        except (KeyError, ValueError):
            raise HTTPException(404, 'Unknown repair session')

    @app.get("/api/workflows/{workflow_id}")
    def workflow_status(workflow_id: str, authorization: str | None = Header(None)) -> dict:
        """Inspect shared workflow state; no worker credentials are exposed."""
        authorized(authorization)
        from .workflows import WorkflowJournal
        try:
            return WorkflowJournal(repository()).status(workflow_id)
        except KeyError:
            raise HTTPException(404, "Unknown workflow")

    @app.get("/api/workflows/{workflow_id}/attestation")
    def workflow_attestation(workflow_id: str, authorization: str | None = Header(None)) -> dict:
        """Read current code-review integrity; never rerun or elevate a task."""
        authorized(authorization)
        from .settings import state_dir
        from .workflows import WorkflowJournal
        try:
            return WorkflowJournal(repository()).attest(workflow_id, state_root=state_dir())
        except KeyError:
            raise HTTPException(404, "Unknown workflow")

    @app.get("/api/workflows/{workflow_id}/events")
    def workflow_events(workflow_id: str, authorization: str | None = Header(None)) -> list[dict]:
        """Replay metadata-only orchestration transition events."""
        authorized(authorization)
        from .workflows import WorkflowJournal
        try:
            return WorkflowJournal(repository()).events(workflow_id)
        except KeyError:
            raise HTTPException(404, "Unknown workflow")

    @app.post("/api/workflows/{workflow_id}/reconcile")
    def workflow_reconcile(workflow_id: str, authorization: str | None = Header(None)) -> dict:
        """Reconcile expired leases; never execute or replay tasks."""
        authorized(authorization)
        from .workflows import WorkflowJournal
        try:
            return WorkflowJournal(repository()).reconcile(workflow_id)
        except KeyError:
            raise HTTPException(404, "Unknown workflow")

    @app.get("/api/workflows/{workflow_id}/review")
    def review_snapshot(workflow_id: str, include_diff: bool = False,
                        authorization: str | None = Header(None)) -> dict:
        """View an integrity-checked patch; raw diff is opt-in and locally authenticated."""
        authorized(authorization)
        from .review_gate import ReviewGate, ReviewUnavailable
        try:
            return ReviewGate(repository(), state_dir()).snapshot(workflow_id, include_diff=include_diff)
        except (ReviewUnavailable, KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/workflows/{workflow_id}/review/history")
    def review_history(workflow_id: str, authorization: str | None = Header(None)) -> list[dict]:
        """Read local export approval history; never code text or credentials."""
        authorized(authorization)
        from .review_gate import ReviewGate, ReviewUnavailable
        try:
            return ReviewGate(repository(), state_dir()).history(workflow_id)
        except (ReviewUnavailable, KeyError, ValueError) as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/workflows/{workflow_id}/review/{action}")
    def review_action(workflow_id: str, action: str, body: LocalExportReviewRequest,
                      authorization: str | None = Header(None)) -> dict:
        """Write only a local review decision; never invoke a coding worker or export."""
        authorized(authorization)
        from .review_gate import ReviewGate, ReviewConflict, ReviewUnavailable
        if action not in {"approve_export", "reject", "revoke"}:
            raise HTTPException(404, "Unknown review action")
        if action == "approve_export" and body.confirm_export_only is not True:
            raise HTTPException(400, "Explicit export-only confirmation is required")
        try:
            return ReviewGate(repository(), state_dir()).decide(workflow_id,
                expected_fingerprint=body.fingerprint, expected_revision=body.revision,
                action=action, acknowledge_unverified=body.acknowledge_unverified)
        except (ReviewConflict, ReviewUnavailable, KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: str, authorization: str | None = Header(None)) -> list[dict]:
        """Replay the current run ledger; authentication always required."""
        authorized(authorization)
        return repository().events(run_id)

    return app
