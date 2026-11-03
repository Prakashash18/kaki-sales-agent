"""Security and Microsoft Graph integration tests for secure Kaki staging."""
import json
from uuid import uuid4
from urllib.parse import urlsplit,parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.secure_main import app
from app.db import Base,engine,Session
from app.identity import Account,Membership,password_hash
from app.company_settings import CompanyProfile,DocumentProfile
from app.workflow_core import Workflow,WorkflowVersion,standard_template
from app.email_connectors import MailConnection
from app.secure_email_api import TenantMessage,fetch_inbox,oauth_base

Base.metadata.create_all(engine)
PASSWORD="temporary-test-password-1234"

def create_user(*,role="owner",company_id=None):
    suffix=uuid4().hex[:12]
    email=f"secure-{suffix}@example.test"
    with Session() as db:
        user=Account(email=email,password_hash=password_hash(PASSWORD))
        db.add(user)
        if company_id is None:
            c=CompanyProfile(name="Private "+suffix,active=False)
            db.add(c);db.flush()
            company_id=c.id
        else:
            db.flush()
        db.add(Membership(account_id=user.id,company_id=company_id,role=role))
        db.commit()
        return email,company_id

def login(email):
    client=TestClient(app)
    res=client.post("/login",data={"email":email,"password":PASSWORD},follow_redirects=False)
    assert res.status_code==303,res.text
    from bs4 import BeautifulSoup
    page=client.get("/settings")
    assert page.status_code==200,page.text
    csrf=BeautifulSoup(page.text,"html.parser").find("input",{"name":"csrf_token"})["value"]
    return client,csrf

def test_cross_company_workflow_document_and_email_access_denied():
    alice,cid_a=create_user()
    bob,cid_b=create_user()
    with Session() as db:
        w=Workflow(company_id=cid_b,name="Company B workflow")
        db.add(w);db.flush()
        v=WorkflowVersion(workflow_id=w.id,version=1,status="draft",definition_json=json.dumps(standard_template()))
        d=DocumentProfile(company_id=cid_b,doc_type="customer_po",name="Company B doc",source_filename="b.txt",field_mapping_json='{"total":["total"]}')
        conn=MailConnection(company_id=cid_b,provider="microsoft",address="bob@example.test",
            access_ciphertext="not-a-token",status="connected")
        db.add_all([v,d,conn]);db.flush()
        msg=TenantMessage(company_id=cid_b,connection_id=conn.id,provider_message_id="B-1",
            subject="Private B message",sender="test@example.test",body="secret text",category="rfq")
        db.add(msg);db.commit()
        w_id,v_id,d_id,m_id,c_id=w.id,v.id,d.id,msg.id,conn.id
    client,csrf=login(alice)
    assert "Company B workflow" not in client.get("/flows").text
    assert "bob@example.test" not in client.get("/integrations").text
    assert client.get(f"/flows/{w_id}/edit/{v_id}").status_code==404
    assert client.get(f"/integrations/inbox/{m_id}").status_code==404
    assert client.post(f"/settings/documents/{d_id}/approve",
        data={"csrf_token":csrf,"mapping_json":'{"total":["total"]}'},follow_redirects=False).status_code==404
    assert client.post("/companies/switch",data={"company_id":cid_b,"csrf_token":csrf},
        follow_redirects=False).status_code==403
    assert client.post(f"/integrations/{c_id}/test",data={"csrf_token":csrf},
        follow_redirects=False).status_code==404
    bclient,_=login(bob)
    assert "Company B workflow" in bclient.get("/flows").text
    assert bclient.get(f"/integrations/inbox/{m_id}").status_code==200

def test_staff_cannot_publish_or_invite_and_revocation_applies_immediately():
    owner,cid=create_user()
    staff,_=create_user(role="staff",company_id=cid)
    boss,token=login(owner)
    subordinate,token_staff=login(staff)
    assert subordinate.get("/flows").status_code==200
    assert subordinate.post("/flows/new",data={"csrf_token":token_staff,"name":"No permissions"},follow_redirects=False).status_code==403
    assert subordinate.post("/companies/invite",data={"csrf_token":token_staff,"email":"foo@example.test","role":"admin"},follow_redirects=False).status_code==403
    assert boss.post("/flows/new",data={"csrf_token":token,"name":"Authorised"},follow_redirects=False).status_code==303
    with Session() as db:
        record=db.scalar(select(Membership).join(Account,Membership.account_id==Account.id).where(Account.email==staff,Membership.company_id==cid))
        record.active=False;db.commit()
    assert subordinate.get("/flows").status_code==403

def test_microsoft_single_tenant_authority(monkeypatch):
    monkeypatch.setenv("MICROSOFT_TENANT_ID","12345678-1234-1234-1234-123456789abc")
    assert oauth_base("microsoft")=="https://login.microsoftonline.com/12345678-1234-1234-1234-123456789abc/oauth2/v2.0"
    monkeypatch.setenv("MICROSOFT_TENANT_ID","../../bad")
    with pytest.raises(Exception):oauth_base("microsoft")

def test_microsoft_connect_returns_expected_redirect(monkeypatch):
    email,cid=create_user()
    client,csrf=login(email)
    from cryptography.fernet import Fernet
    monkeypatch.setenv("KAKI_ENABLE_OAUTH","true")
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID","fake-client-id")
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_SECRET","fake-client-secret")
    monkeypatch.setenv("MICROSOFT_TENANT_ID","12345678-1234-1234-1234-123456789abc")
    monkeypatch.setenv("KAKI_PUBLIC_URL","https://kaki-staging.example")
    monkeypatch.setenv("KAKI_TOKEN_ENCRYPTION_KEY",Fernet.generate_key().decode())
    monkeypatch.setenv("DATABASE_URL","postgresql+psycopg://dummy:dummy@localhost/kaki")
    response=client.post("/integrations/connect/microsoft",data={"csrf_token":csrf},follow_redirects=False)
    assert response.status_code==303,response.text
    url=urlsplit(response.headers["location"])
    assert url.netloc=="login.microsoftonline.com"
    assert "/12345678-1234-1234-1234-123456789abc/" in url.path
    query=parse_qs(url.query)
    assert query["redirect_uri"]==["https://kaki-staging.example/integrations/callback/microsoft"]
    assert "Mail.Read" in query["scope"][0]
    assert "offline_access" in query["scope"][0]
    assert query["response_type"]==["code"]
    assert query["state"][0]

def test_graph_fetch_inbox_simulated(monkeypatch):
    import asyncio
    import app.secure_email_api as api
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        assert request.headers["Authorization"]=="Bearer mocked-token"
        assert request.url.host=="graph.microsoft.com"
        return httpx.Response(200,json={"value":[
            {"id":"ABC123","subject":"Request for quote",
             "from":{"emailAddress":{"address":"customer@example.test"}},
             "body":{"contentType":"text","content":"Please quote ten units"},
             "hasAttachments":True}
        ]})
    transport=httpx.MockTransport(handler)
    actual=httpx.AsyncClient
    monkeypatch.setattr(api.httpx,"AsyncClient",lambda **kwargs: actual(transport=transport,**kwargs))
    row=type("Row",(),{"id":73,"provider":"microsoft"})()
    inbox=asyncio.run(api.fetch_inbox(row,"mocked-token"))
    assert len(inbox)==1
    assert inbox[0]["id"]=="ms:73:ABC123"
    assert inbox[0]["subject"]=="Request for quote"
    assert inbox[0]["attachment"] is True
    assert len(calls)==1

def test_graph_disabled_without_approval(monkeypatch):
    email,cid=create_user()
    client,csrf=login(email)
    monkeypatch.setenv("KAKI_ENABLE_OAUTH","false")
    r=client.post("/integrations/connect/microsoft",data={"csrf_token":csrf},follow_redirects=False)
    assert r.status_code==503
