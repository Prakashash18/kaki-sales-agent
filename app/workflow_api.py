import json,re,secrets,os
from fastapi import APIRouter,Request,Form,HTTPException
from fastapi.responses import RedirectResponse,JSONResponse
from sqlalchemy import select,func
from .db import Session
from .company_settings import CompanyProfile
from .workflow_core import Workflow,WorkflowVersion,WorkflowRun,WorkflowEvent,standard_template,rfq_template,validate,advance,utcnow,event

router=APIRouter(prefix="/flows")
def require(request,csrf=None):
    if not request.session.get("user"):raise HTTPException(401)
    if csrf is not None and (not csrf or not secrets.compare_digest(csrf,request.session.get("csrf",""))):raise HTTPException(403)
def company(db):
    c=db.scalar(select(CompanyProfile).where(CompanyProfile.active.is_(True)))
    if not c:raise HTTPException(409,"Create and activate a company in Settings")
    return c
def owned(db,wid,cid):
    w=db.get(Workflow,wid)
    if not w or w.company_id!=cid:raise HTTPException(404)
    return w
def version(db,wid,vid):
    v=db.get(WorkflowVersion,vid)
    if not v or v.workflow_id!=wid:raise HTTPException(404)
    return v
def redirect(path="/flows"):return RedirectResponse(path,status_code=303)
def new_version(db,wid,spec,note=""):
    number=(db.scalar(select(func.max(WorkflowVersion.version)).where(WorkflowVersion.workflow_id==wid)) or 0)+1
    v=WorkflowVersion(workflow_id=wid,version=number,definition_json=json.dumps(spec),notes=note[:500])
    db.add(v);db.flush();return v

@router.get("")
def index(request:Request):
    require(request)
    with Session() as db:
        c=company(db)
        workflows=db.scalars(select(Workflow).where(Workflow.company_id==c.id).order_by(Workflow.id.desc())).all()
        items=[]
        for w in workflows:
            vs=db.scalars(select(WorkflowVersion).where(WorkflowVersion.workflow_id==w.id).order_by(WorkflowVersion.version.desc())).all()
            items.append({"id":w.id,"name":w.name,"archived":w.archived,"versions":[{"id":v.id,"number":v.version,"status":v.status} for v in vs]})
        runs=db.scalars(select(WorkflowRun).where(WorkflowRun.company_id==c.id).order_by(WorkflowRun.id.desc()).limit(40)).all()
        runs=[{"id":r.id,"workflow_id":r.workflow_id,"status":r.status,"node":r.current_node} for r in runs]
        company_name=c.name
    return request.app.state.templates.TemplateResponse(request,"flows.html",{"request":request,"user":request.session["user"],"csrf":request.session["csrf"],"company":company_name,"flows":items,"runs":runs})

@router.post("/new")
def create(request:Request,csrf_token:str=Form(...),name:str=Form(...),template:str=Form("rfq")):
    require(request,csrf_token)
    if not name.strip() or len(name)>200 or template not in ("rfq","basic"):raise HTTPException(400)
    with Session() as db:
        c=company(db);w=Workflow(company_id=c.id,name=name.strip());db.add(w);db.flush()
        v=new_version(db,w.id,rfq_template() if template=="rfq" else standard_template(),"Initial draft")
        db.commit();wid=w.id;vid=v.id
    return redirect(f"/flows/{wid}/edit/{vid}")

@router.post("/{wid}/clone")
def clone(request:Request,wid:int,csrf_token:str=Form(...)):
    require(request,csrf_token)
    with Session() as db:
        c=company(db);w=owned(db,wid,c.id)
        last=db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id==wid).order_by(WorkflowVersion.version.desc()))
        new=Workflow(company_id=c.id,name=(w.name+" (copy)")[:200]);db.add(new);db.flush()
        v=new_version(db,new.id,json.loads(last.definition_json),f"Cloned from workflow {w.id}")
        db.commit();nid=new.id;vid=v.id
    return redirect(f"/flows/{nid}/edit/{vid}")

@router.post("/{wid}/archive")
def archive(request:Request,wid:int,csrf_token:str=Form(...),archived:bool=Form(True)):
    require(request,csrf_token)
    with Session() as db:
        c=company(db);w=owned(db,wid,c.id);w.archived=archived;db.commit()
    return redirect()

@router.get("/{wid}/edit/{vid}")
def editor(request:Request,wid:int,vid:int):
    require(request)
    with Session() as db:
        c=company(db);w=owned(db,wid,c.id);v=version(db,wid,vid)
        versions=db.scalars(select(WorkflowVersion).where(WorkflowVersion.workflow_id==wid).order_by(WorkflowVersion.version.desc())).all()
        spec=json.loads(v.definition_json)
        summary={"name":w.name,"archived":w.archived,"status":v.status,"number":v.version,"notes":v.notes,"id":v.id}
        history=[{"id":x.id,"number":x.version,"status":x.status,"notes":x.notes} for x in versions]
    return request.app.state.templates.TemplateResponse(request,"flow_editor.html",{"request":request,"user":request.session["user"],"csrf":request.session["csrf"],"workflow_id":wid,"version":summary,"history":history,"definition":json.dumps(spec,indent=2),"nodes":spec["nodes"],"errors":validate(spec)})

@router.post("/{wid}/save")
def save(request:Request,wid:int,csrf_token:str=Form(...),definition:str=Form(...),notes:str=Form(""),base_version_id:int=Form(...)):
    require(request,csrf_token)
    if len(definition)>50000:raise HTTPException(413)
    try:spec=json.loads(definition)
    except json.JSONDecodeError as e:raise HTTPException(400,f"Invalid JSON: {e}")
    errors=validate(spec)
    if errors:raise HTTPException(422,detail=errors)
    with Session() as db:
        c=company(db);w=owned(db,wid,c.id);base=version(db,wid,base_version_id)
        if w.archived:raise HTTPException(409)
        latest=db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id==wid).order_by(WorkflowVersion.version.desc()))
        if latest.id!=base.id:raise HTTPException(409,"Another revision exists. Refresh before saving.")
        v=new_version(db,wid,spec,notes);db.commit();new_id=v.id
    return redirect(f"/flows/{wid}/edit/{new_id}")

@router.post("/{wid}/publish/{vid}")
def publish(request:Request,wid:int,vid:int,csrf_token:str=Form(...)):
    require(request,csrf_token)
    with Session() as db:
        c=company(db);w=owned(db,wid,c.id);v=version(db,wid,vid)
        if w.archived or v.status!="draft":raise HTTPException(409)
        errs=validate(json.loads(v.definition_json))
        if errs:raise HTTPException(422,errs)
        v.status="published";v.published_at=utcnow()
        for old in db.scalars(select(WorkflowVersion).where(WorkflowVersion.workflow_id==wid,WorkflowVersion.id!=vid,WorkflowVersion.status=="published")).all():old.status="superseded"
        db.commit()
    return redirect(f"/flows/{wid}/edit/{vid}")

@router.post("/{wid}/revise/{vid}")
def revise(request:Request,wid:int,vid:int,csrf_token:str=Form(...)):
    require(request,csrf_token)
    with Session() as db:
        c=company(db);w=owned(db,wid,c.id);v=version(db,wid,vid)
        if w.archived:raise HTTPException(409)
        created=new_version(db,wid,json.loads(v.definition_json),f"Revision of v{v.version}");db.commit();new_id=created.id
    return redirect(f"/flows/{wid}/edit/{new_id}")

@router.post("/{wid}/simulate/{vid}")
def simulate(request:Request,wid:int,vid:int,csrf_token:str=Form(...),subject:str=Form("RFQ"),body:str=Form("Please quote 10 units")):
    require(request,csrf_token)
    with Session() as db:
        c=company(db);owned(db,wid,c.id);v=version(db,wid,vid)
        spec=json.loads(v.definition_json);errors=validate(spec)
        if errors:raise HTTPException(422,errors)
        run=WorkflowRun(company_id=c.id,workflow_id=wid,version_id=vid,status="running",current_node=spec["start"],context_json=json.dumps({"subject":subject[:300],"body":body[:3000]}))
        db.add(run);db.flush();advance(db,run,simulate=True)
        events=db.scalars(select(WorkflowEvent).where(WorkflowEvent.run_id==run.id).order_by(WorkflowEvent.id)).all()
        output={"status":run.status,"current_node":run.current_node,"context":json.loads(run.context_json),"trace":[{"node":e.node_id,"action":e.action} for e in events]}
        db.rollback()
    return JSONResponse(output)

@router.post("/{wid}/start")
def start(request:Request,wid:int,csrf_token:str=Form(...),subject:str=Form("RFQ"),body:str=Form("")):
    require(request,csrf_token)
    with Session() as db:
        c=company(db);w=owned(db,wid,c.id)
        if w.archived:raise HTTPException(409)
        v=db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id==wid,WorkflowVersion.status=="published"))
        if not v:raise HTTPException(409,"Publish a revision first")
        spec=json.loads(v.definition_json)
        if spec.get("trigger")!="manual":raise HTTPException(409,"Email-trigger workflows cannot be manually started")
        run=WorkflowRun(company_id=c.id,workflow_id=wid,version_id=v.id,status="running",current_node=spec["start"],context_json=json.dumps({"subject":subject[:300],"body":body[:5000]}))
        db.add(run);db.flush();advance(db,run);db.commit();rid=run.id
    return redirect(f"/flows/runs/{rid}")

@router.get("/runs/{rid}")
def detail(request:Request,rid:int):
    require(request)
    with Session() as db:
        c=company(db);run=db.get(WorkflowRun,rid)
        if not run or run.company_id!=c.id:raise HTTPException(404)
        events=db.scalars(select(WorkflowEvent).where(WorkflowEvent.run_id==rid).order_by(WorkflowEvent.id)).all()
        info={"id":run.id,"status":run.status,"node":run.current_node,"context":json.loads(run.context_json),"error":run.error}
        trace=[{"node":e.node_id,"action":e.action,"note":e.note} for e in events]
    return request.app.state.templates.TemplateResponse(request,"flow_run.html",{"request":request,"user":request.session["user"],"csrf":request.session["csrf"],"run":info,"trace":trace})

@router.post("/runs/{rid}/decide")
def decide(request:Request,rid:int,csrf_token:str=Form(...),decision:str=Form(...)):
    require(request,csrf_token)
    if decision not in ("approve","reject"):raise HTTPException(400)
    with Session() as db:
        c=company(db);run=db.scalar(select(WorkflowRun).where(WorkflowRun.id==rid,WorkflowRun.company_id==c.id).with_for_update())
        if not run or run.status!="awaiting_approval":raise HTTPException(409)
        v=db.get(WorkflowVersion,run.version_id);spec=json.loads(v.definition_json)
        node=next(n for n in spec["nodes"] if n["id"]==run.current_node)
        if node["type"]!="approval":raise HTTPException(409)
        event(db,run,run.current_node,"approved" if decision=="approve" else "rejected",request.session["user"])
        ctx=json.loads(run.context_json);ctx["approved"]=decision=="approve";run.context_json=json.dumps(ctx)
        run.current_node=node["on_approved"] if decision=="approve" else node["on_rejected"];run.status="running"
        advance(db,run);db.commit()
    return redirect(f"/flows/runs/{rid}")

@router.post("/runs/{rid}/cancel")
def cancel(request:Request,rid:int,csrf_token:str=Form(...)):
    require(request,csrf_token)
    with Session() as db:
        c=company(db);run=db.get(WorkflowRun,rid)
        if not run or run.company_id!=c.id:raise HTTPException(404)
        if run.status not in ("waiting","queued","awaiting_approval"):raise HTTPException(409)
        run.status="cancelled";db.commit()
    return redirect(f"/flows/runs/{rid}")

@router.post("/{wid}/propose/{vid}")
def propose(request:Request,wid:int,vid:int,csrf_token:str=Form(...),instruction:str=Form(...)):
    require(request,csrf_token)
    if not instruction.strip() or len(instruction)>2000:raise HTTPException(400)
    with Session() as db:
        c=company(db);owned(db,wid,c.id);v=version(db,wid,vid);spec=json.loads(v.definition_json)
    if not os.getenv("ANTHROPIC_API_KEY"):
        return JSONResponse({"error":"Configure ANTHROPIC_API_KEY for chat proposals. The JSON editor works without AI."},status_code=503)
    try:
        from anthropic import Anthropic
        msg=Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"],timeout=30).messages.create(
          model=os.getenv("ANTHROPIC_MODEL","claude-sonnet-4-5"),max_tokens=3000,
          system="You are Kaki's workflow configuration assistant. Return strictly ONE valid JSON workflow object with keys trigger,start,nodes. Allowed triggers: manual,email_received. Allowed node types: classify,condition,set_value,draft_reply,approval,wait,send_reply,end. Do not use custom code or new tools. Every path to send_reply must pass through an approval. Keep the graph acyclic with at most 40 nodes. Use edges next,on_true,on_false,on_approved,on_rejected and supported configs only. Preserve requirements not being changed. If the requested capability is unsupported, return the existing workflow.",
          messages=[{"role":"user","content":json.dumps({"existing":spec,"requested_change":instruction})}])
        raw="".join(c.text for c in msg.content if c.type=="text").strip()
        fence=chr(96)*3
        if raw.startswith(fence):raw=raw.split("\n",1)[-1].rsplit(fence,1)[0].strip()
        candidate=json.loads(raw);errors=validate(candidate)
        return JSONResponse({"valid":not errors,"errors":errors,"proposal":candidate,"message":"Proposal only. Review JSON and save a new revision manually."})
    except Exception:
        return JSONResponse({"error":"Could not create proposal; original workflow unchanged."},status_code=502)

@router.post("/runs/advance-due")
def advance_due(request:Request,csrf_token:str=Form(...)):
    """Manually resume due waits for current company in the demo.
    Production requires a dedicated worker and row locking."""
    require(request,csrf_token)
    with Session() as db:
        c=company(db)
        runs=db.scalars(select(WorkflowRun).where(
            WorkflowRun.company_id==c.id,
            WorkflowRun.status=="waiting",
            WorkflowRun.due_at<=utcnow()
        ).order_by(WorkflowRun.id).limit(20).with_for_update()).all()
        for run in runs:
            run.status="running";run.due_at=None
            advance(db,run)
        db.commit()
    return redirect()
