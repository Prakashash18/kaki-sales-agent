"""Declarative workflow validation and deterministic execution.

The execution engine NEVER executes model-produced source code. Nodes can call
only approved handlers. External transmission is explicitly disabled in the MVP.
"""
import copy
import json
import re
from datetime import datetime, timezone, timedelta
from sqlalchemy import ForeignKey, Integer, String, Text, DateTime, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, Session, engine
from .company_settings import CompanyProfile

TYPES = {"classify", "condition", "set_value", "draft_reply", "approval", "wait", "send_reply", "end"}
ALLOWED_CONTEXT = {"subject", "body", "sender", "message_id", "category", "summary", "draft", "approved", "reply", "document_type", "amount", "customer", "status"}
MAX_NODES = 40

def utcnow(): return datetime.now(timezone.utc)
def standard_template():
    return {
      "trigger": "manual",
      "start": "classify",
      "nodes": [
        {"id":"classify","type":"classify","next":"draft"},
        {"id":"draft","type":"draft_reply","next":"approval"},
        {"id":"approval","type":"approval","on_approved":"finish","on_rejected":"rejected"},
        {"id":"finish","type":"end","config":{"outcome":"approved"}},
        {"id":"rejected","type":"end","config":{"outcome":"rejected"}}
      ]
    }
def rfq_template():
    x=standard_template()
    x["nodes"][1]["config"]={"instruction":"Prepare an acknowledgement; do not commit to price or delivery."}
    return x
def validate(spec):
    errors=[]
    if not isinstance(spec,dict): return ["Workflow must be an object"]
    nodes=spec.get("nodes")
    if not isinstance(nodes,list) or not 1<=len(nodes)<=MAX_NODES:return ["Workflow needs 1–40 nodes"]
    if spec.get("trigger") not in ("manual","email_received"):errors.append("Unsupported trigger")
    ids=[n.get("id") for n in nodes if isinstance(n,dict)]
    if len(ids)!=len(nodes) or len(set(ids))!=len(ids):errors.append("Node IDs must be unique")
    if any(not isinstance(x,str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,40}",x) for x in ids):errors.append("Invalid node ID")
    byid={n["id"]:n for n in nodes if isinstance(n,dict) and isinstance(n.get("id"),str)}
    if spec.get("start") not in byid:errors.append("Start node missing")
    for n in nodes:
        if not isinstance(n,dict):errors.append("Node must be an object");continue
        kind=n.get("type"); ident=n.get("id","?")
        if kind not in TYPES:errors.append(f"{ident}: unknown action {kind}");continue
        config=n.get("config",{})
        if not isinstance(config,dict):errors.append(f"{ident}: config must be an object");continue
        if kind=="condition":
            if config.get("field") not in ALLOWED_CONTEXT:errors.append(f"{ident}: unsupported field")
            if config.get("operator") not in ("eq","neq","contains","gte","lte","exists"):errors.append(f"{ident}: invalid operator")
            required=("on_true","on_false")
        elif kind=="approval":required=("on_approved","on_rejected")
        elif kind=="wait":
            seconds=config.get("seconds")
            if type(seconds) is not int or not 1<=seconds<=604800:errors.append(f"{ident}: wait must be 1–604800 seconds")
            required=("next",)
        elif kind=="end":required=()
        else:required=("next",)
        if kind=="set_value" and config.get("key") not in ("status","document_type","summary"):errors.append(f"{ident}: only safe context fields can be set")
        for edge in required:
            if n.get(edge) not in byid:errors.append(f"{ident}: invalid {edge} transition")
        if kind=="send_reply" and not config.get("requires_approval",True):errors.append(f"{ident}: cannot disable approval requirement")
    if not errors:
        reachable=set(); stack=[spec["start"]]
        while stack:
            ident=stack.pop()
            if ident in reachable:continue
            reachable.add(ident);n=byid[ident]
            for edge in ("next","on_true","on_false","on_approved","on_rejected"):
                if n.get(edge) in byid:stack.append(n[edge])
        if len(reachable)!=len(byid):errors.append("Unreachable nodes: "+", ".join(sorted(set(byid)-reachable)))
        if not any(byid[i]["type"]=="end" for i in reachable):errors.append("No terminal end node")
        # Disallow cycles for the initial deterministic executor.
        active=set(); finished=set()
        def visit(v):
            if v in active:errors.append("Cycle detected: "+v);return
            if v in finished:return
            active.add(v)
            for edge in ("next","on_true","on_false","on_approved","on_rejected"):
                w=byid[v].get(edge)
                if w in byid:visit(w)
            active.remove(v);finished.add(v)
        visit(spec["start"])
        # A send action must be reachable only after an approved branch.
        def walk(v,approved=False):
            n=byid[v]
            if n["type"]=="send_reply" and not approved:errors.append(f"{v}: send requires prior approval")
            if n["type"]=="approval":
                walk(n["on_approved"],True);walk(n["on_rejected"],False)
            else:
                for edge in ("next","on_true","on_false"):
                    w=n.get(edge)
                    if w in byid:walk(w,approved)
        if not errors:walk(spec["start"])
    return sorted(set(errors))

class Workflow(Base):
    __tablename__="workflows"
    id:Mapped[int]=mapped_column(primary_key=True)
    company_id:Mapped[int]=mapped_column(ForeignKey("company_profiles.id"),index=True)
    name:Mapped[str]=mapped_column(String(200))
    archived:Mapped[bool]=mapped_column(Boolean,default=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)

class WorkflowVersion(Base):
    __tablename__="workflow_versions"
    __table_args__=(UniqueConstraint("workflow_id","version"),)
    id:Mapped[int]=mapped_column(primary_key=True)
    workflow_id:Mapped[int]=mapped_column(ForeignKey("workflows.id"),index=True)
    version:Mapped[int]=mapped_column(Integer)
    status:Mapped[str]=mapped_column(String(30),default="draft")
    definition_json:Mapped[str]=mapped_column(Text)
    notes:Mapped[str]=mapped_column(Text,default="")
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    published_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)

class WorkflowRun(Base):
    __tablename__="workflow_runs"
    id:Mapped[int]=mapped_column(primary_key=True)
    company_id:Mapped[int]=mapped_column(ForeignKey("company_profiles.id"),index=True)
    workflow_id:Mapped[int]=mapped_column(ForeignKey("workflows.id"))
    version_id:Mapped[int]=mapped_column(ForeignKey("workflow_versions.id"))
    status:Mapped[str]=mapped_column(String(32),default="queued")
    current_node:Mapped[str]=mapped_column(String(60))
    context_json:Mapped[str]=mapped_column(Text,default="{}")
    due_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    error:Mapped[str]=mapped_column(Text,default="")
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)

class WorkflowEvent(Base):
    __tablename__="workflow_events"
    id:Mapped[int]=mapped_column(primary_key=True)
    run_id:Mapped[int]=mapped_column(ForeignKey("workflow_runs.id"),index=True)
    node_id:Mapped[str]=mapped_column(String(60))
    action:Mapped[str]=mapped_column(String(80))
    note:Mapped[str]=mapped_column(String(500),default="")
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)

def event(db,run,node,action,note=""):
    db.add(WorkflowEvent(run_id=run.id,node_id=node,action=action,note=str(note)[:500]))

def evaluate_condition(config,context):
    v=context.get(config["field"]);target=config.get("value")
    op=config["operator"]
    if op=="exists":return v is not None and v!=""
    if op=="eq":return str(v).lower()==str(target).lower()
    if op=="neq":return str(v).lower()!=str(target).lower()
    if op=="contains":return str(target).lower() in str(v).lower()
    try:
        from decimal import Decimal
        if op=="gte":return Decimal(str(v))>=Decimal(str(target))
        if op=="lte":return Decimal(str(v))<=Decimal(str(target))
    except Exception:return False
    return False

def advance(db,run, *, simulate=False, limit=40):
    """Advance synchronously through deterministic nodes, stopping at a wait or approval."""
    version=db.get(WorkflowVersion,run.version_id)
    spec=json.loads(version.definition_json)
    nodes={n["id"]:n for n in spec["nodes"]}
    context=json.loads(run.context_json)
    for _ in range(limit):
        node=nodes[run.current_node];kind=node["type"];ident=node["id"]
        if kind=="classify":
            from .agent import fallback
            result=fallback(str(context.get("subject","")),str(context.get("body","")))
            context.update({k:result[k] for k in ("category","summary","draft")})
            run.current_node=node["next"];event(db,run,ident,"classified",result["category"])
        elif kind=="draft_reply":
            # AI drafting is not automatically executed; safe deterministic acknowledgement.
            context["draft"]=context.get("draft") or "Thank you for your email. Our team will review your request."
            run.current_node=node["next"];event(db,run,ident,"draft_created")
        elif kind=="condition":
            outcome=evaluate_condition(node["config"],context)
            run.current_node=node["on_true"] if outcome else node["on_false"]
            event(db,run,ident,"condition",str(outcome))
        elif kind=="set_value":
            context[node["config"]["key"]]=str(node["config"].get("value",""))[:500]
            run.current_node=node["next"];event(db,run,ident,"context_updated")
        elif kind=="approval":
            run.status="awaiting_approval";event(db,run,ident,"approval_requested");break
        elif kind=="wait":
            if simulate:
                run.current_node=node["next"];event(db,run,ident,"wait_skipped_in_simulation")
            else:
                run.status="waiting";run.due_at=utcnow()+timedelta(seconds=node["config"]["seconds"])
                run.current_node=node["next"];event(db,run,ident,"wait_scheduled");break
        elif kind=="send_reply":
            # Never send on this initial release. Explicit outbound integration is a later gate.
            run.status="blocked";run.error="Outbound email delivery is disabled pending verified OAuth, tenant scoping and approval audit."
            event(db,run,ident,"external_send_blocked");break
        elif kind=="end":
            run.status="completed";context["outcome"]=node.get("config",{}).get("outcome","done")
            event(db,run,ident,"completed");break
        else:
            run.status="failed";run.error=f"Unsupported node {ident}";break
    else:
        run.status="failed";run.error="Execution step limit exceeded"
    run.context_json=json.dumps(context,ensure_ascii=True)
    run.updated_at=utcnow()
    return run

def run_due(db, limit=20):
    q=db.query(WorkflowRun).filter(WorkflowRun.status=="waiting",WorkflowRun.due_at<=utcnow()).order_by(WorkflowRun.id).limit(limit)
    count=0
    for run in q:
        run.status="running";run.due_at=None;advance(db,run);count+=1
    return count
