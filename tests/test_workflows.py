import json,os
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import Session
from app.company_settings import CompanyProfile
from app.workflow_core import Workflow,WorkflowVersion,WorkflowRun,validate,standard_template
from app.email_connectors import SCOPES,crypto

def client_with_company():
    os.environ["ADMIN_PASSWORD"]="test-password"
    c=TestClient(app)
    res=c.post("/login",data={"username":"admin","password":"test-password"},follow_redirects=False)
    assert res.status_code==303
    from bs4 import BeautifulSoup
    token=BeautifulSoup(c.get("/settings").text,"html.parser").find("input",{"name":"csrf_token"})["value"]
    name="Workflow test "+uuid4().hex[:8]
    assert c.post("/settings/companies",data={"csrf_token":token,"name":name},follow_redirects=False).status_code==303
    with Session() as db:
        item=db.scalar(select(CompanyProfile).where(CompanyProfile.name==name));cid=item.id
    assert c.post(f"/settings/companies/{cid}/activate",data={"csrf_token":token},follow_redirects=False).status_code==303
    return c,token,cid

def test_validation_blocks_dangerous_nodes():
    wf=standard_template()
    assert validate(wf)==[]
    wf["nodes"][1]["type"]="shell_exec"
    assert any("unknown action" in x for x in validate(wf))
    wf=standard_template();wf["nodes"].insert(2,{"id":"send","type":"send_reply","next":"approval"})
    wf["nodes"][1]["next"]="send"
    assert any("send requires prior approval" in x for x in validate(wf))
    wf=standard_template();wf["nodes"][0]["next"]="classify"
    assert any("Cycle detected" in x for x in validate(wf))

def test_create_edit_publish_simulate_run_and_approve():
    c,csrf,cid=client_with_company()
    response=c.post("/flows/new",data={"csrf_token":csrf,"name":"RFQ pipeline"},follow_redirects=False)
    assert response.status_code==303,response.text
    loc=response.headers["location"]
    with Session() as db:
        w=db.scalar(select(Workflow).where(Workflow.company_id==cid));assert w
        v=db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id==w.id));assert v
        wid,vid=w.id,v.id
    assert c.get(loc).status_code==200
    out=c.post(f"/flows/{wid}/simulate/{vid}",data={"csrf_token":csrf,"subject":"Request for Quote","body":"Please quote 2"},follow_redirects=False)
    assert out.status_code==200,out.text
    assert out.json()["status"]=="awaiting_approval"
    assert c.post(f"/flows/{wid}/publish/{vid}",data={"csrf_token":csrf},follow_redirects=False).status_code==303
    started=c.post(f"/flows/{wid}/start",data={"csrf_token":csrf,"subject":"RFQ","body":"Please quote"},follow_redirects=False)
    assert started.status_code==303
    rid=int(started.headers["location"].split("/")[-1])
    assert c.get(f"/flows/runs/{rid}").status_code==200
    assert c.post(f"/flows/runs/{rid}/decide",data={"csrf_token":csrf,"decision":"approve"},follow_redirects=False).status_code==303
    with Session() as db:
        run=db.get(WorkflowRun,rid)
        assert run.status=="completed"
        assert json.loads(run.context_json)["approved"] is True
    assert c.post(f"/flows/runs/{rid}/decide",data={"csrf_token":csrf,"decision":"approve"},follow_redirects=False).status_code==409
    assert c.post(f"/flows/{wid}/revise/{vid}",data={"csrf_token":csrf},follow_redirects=False).status_code==303
    assert c.post(f"/flows/{wid}/clone",data={"csrf_token":csrf},follow_redirects=False).status_code==303

def test_oauth_needs_keys_and_csrf():
    c,csrf,cid=client_with_company()
    assert c.get("/integrations").status_code==200
    bad=c.post("/integrations/connect/google",data={"csrf_token":"incorrect"},follow_redirects=False)
    assert bad.status_code==403
    no_key=c.post("/integrations/connect/google",data={"csrf_token":csrf},follow_redirects=False)
    assert no_key.status_code==503
    assert "gmail.readonly" in SCOPES["google"]

def test_save_draft_creates_revision_and_published_immutable():
    c,csrf,cid=client_with_company()
    c.post("/flows/new",data={"csrf_token":csrf,"name":"Change test"},follow_redirects=False)
    with Session() as db:
        w=db.scalar(select(Workflow).where(Workflow.company_id==cid))
        v=db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id==w.id))
        wid,vid=w.id,v.id
    spec=standard_template()
    result=c.post(f"/flows/{wid}/save",data={"csrf_token":csrf,"base_version_id":vid,"definition":json.dumps(spec),"notes":"Added configuration"},follow_redirects=False)
    assert result.status_code==303,result.text
    newer=int(result.headers["location"].split("/")[-1])
    assert newer!=vid
    again=c.post(f"/flows/{wid}/save",data={"csrf_token":csrf,"base_version_id":vid,"definition":json.dumps(spec)},follow_redirects=False)
    assert again.status_code==409
