"""End-to-end Data Hub tests using synthetic Excel and mocked Microsoft Graph.

Tests never contact Microsoft, store customer data, or modify SharePoint.
"""
import io
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select

from app.secure_main import app
from app.db import Base,engine,Session
from app.identity import Account,Membership,password_hash
from app.company_settings import CompanyProfile
from app.email_connectors import MailConnection
from app.data_hub import DataSource,DataRecord,read_excel,classify_headers,map_records

Base.metadata.create_all(engine)
PASSWORD="testing-strong-password-12345"

def make_workbook():
    b=Workbook()
    sheet=b.active;sheet.title="Jobs"
    sheet.append(["Job No","Client","Job Status","Date Completed"])
    sheet.append(["JOB-101","ABC Marine","Completed","2026-10-10"])
    sheet.append(["JOB-102","ABC Marine","In Progress",None])
    b.create_sheet("Empty")
    stream=io.BytesIO();b.save(stream);return stream.getvalue()

def user_and_client(role="owner"):
    marker=uuid4().hex[:12]
    with Session() as db:
        company=CompanyProfile(name="Data Hub "+marker,active=False)
        account=Account(email="hub-"+marker+"@example.test",password_hash=password_hash(PASSWORD))
        db.add_all([company,account]);db.flush()
        db.add(Membership(company_id=company.id,account_id=account.id,role=role))
        connection=MailConnection(company_id=company.id,provider="microsoft",
            address="user-"+marker+"@example.test",status="connected",access_ciphertext="encrypted")
        db.add(connection);db.commit()
        cid=company.id;connection_id=connection.id;email=account.email
    client=TestClient(app)
    assert client.post("/login",data={"email":email,"password":PASSWORD},follow_redirects=False).status_code==303
    from bs4 import BeautifulSoup
    page=client.get("/data-hub")
    assert page.status_code==200
    csrf=BeautifulSoup(page.text,"html.parser").find("input",{"name":"csrf_token"})["value"]
    return client,csrf,cid,connection_id

def test_excel_header_mapping_and_rows():
    sheets=read_excel(make_workbook())
    assert len(sheets["Jobs"]["rows"])==2
    mapping=classify_headers(sheets["Jobs"]["headers"],"jobs")
    assert mapping["job_id"]=="Job No"
    assert mapping["status"]=="Job Status"
    rows=map_records(sheets["Jobs"]["rows"],sheets["Jobs"]["headers"],"jobs",mapping)
    assert rows[0][1]["job_id"]=="JOB-101"
    assert rows[1][1]["status"]=="In Progress"
    with pytest.raises(Exception):
        map_records(sheets["Jobs"]["rows"],sheets["Jobs"]["headers"],"jobs",{"unsafe":"Job No"})

def test_graph_import_and_resync_preserve_source(monkeypatch):
    import app.data_hub as hub
    client,csrf,cid,connection_id=user_and_client()
    content=make_workbook()
    state={"etag":"etag-one","bytes":content}
    async def access(row):return "fake-token"
    async def metadata(token,path,params=None):
        if path=="sites":return {"value":[{"id":"siteA","displayName":"Sea Hover Test Site"}]}
        if path.endswith("/drives"):return {"value":[{"id":"driveA","name":"Documents"}]}
        if path.endswith("/root/children"):return {"value":[{"id":"itemA","name":"Job Tracker.xlsx","file":{}}]}
        return {"id":"itemA","name":"Job Tracker.xlsx","eTag":state["etag"]}
    async def download(token,drive,item):
        assert token=="fake-token"
        return state["bytes"]
    monkeypatch.setattr(hub,"access_token",access)
    monkeypatch.setattr(hub,"graph_json",metadata)
    monkeypatch.setattr(hub,"fetch_workbook",download)
    common={"csrf_token":csrf,"connection_id":connection_id}
    sites=client.post("/data-hub/discover",data={**common,"query":"Sea"},follow_redirects=False)
    assert sites.status_code==200 and sites.json()["sites"][0]["id"]=="siteA"
    drives=client.post("/data-hub/browse",data={**common,"site_id":"siteA"},follow_redirects=False)
    assert drives.json()["drives"][0]["id"]=="driveA"
    files=client.post("/data-hub/browse",data={**common,"drive_id":"driveA"},follow_redirects=False)
    assert files.json()["files"][0]["id"]=="itemA"
    preview=client.post("/data-hub/preview",data={**common,"drive_id":"driveA","item_id":"itemA","kind":"jobs"},follow_redirects=False)
    assert preview.status_code==200,preview.text
    mapping=preview.json()["sheets"][0]["suggested_mapping"]
    result=client.post("/data-hub/import",data={**common,"drive_id":"driveA","item_id":"itemA",
        "sheet_name":"Jobs","kind":"jobs","mapping_json":json.dumps(mapping)},follow_redirects=False)
    assert result.status_code==303,result.text
    with Session() as db:
        source=db.scalar(select(DataSource).where(DataSource.company_id==cid))
        sid=source.id
        assert source.row_count==2
        records=db.scalars(select(DataRecord).where(DataRecord.source_id==sid)).all()
        assert len(records)==2
    assert "JOB-101" in client.get("/data-hub",params={"q":"JOB-101"}).text
    asked=client.get("/data-hub/ask",params={"question":"show completed jobs"})
    assert asked.status_code==200
    assert asked.json()["results"][0]["data"]["job_id"]=="JOB-101"
    # Same eTag means no change.
    same=client.post(f"/data-hub/sources/{sid}/sync",data={"csrf_token":csrf},follow_redirects=False)
    assert same.status_code==303
    with Session() as db: assert db.get(DataSource,sid).row_count==2
    workbook=Workbook();worksheet=workbook.active;worksheet.title="Jobs"
    worksheet.append(["Job No","Client","Job Status","Date Completed"])
    worksheet.append(["JOB-101","ABC Marine","Delivered","2026-10-10"])
    stream=io.BytesIO();workbook.save(stream);state["bytes"]=stream.getvalue();state["etag"]="etag-two"
    changed=client.post(f"/data-hub/sources/{sid}/sync",data={"csrf_token":csrf},follow_redirects=False)
    assert changed.status_code==303
    with Session() as db:
        src=db.get(DataSource,sid)
        assert src.row_count==1 and src.etag=="etag-two"
        row=db.scalar(select(DataRecord).where(DataRecord.source_id==sid))
        assert json.loads(row.payload_json)["status"]=="Delivered"

def test_viewer_cannot_import_or_discover(monkeypatch):
    client,csrf,cid,connection_id=user_and_client("viewer")
    r=client.post("/data-hub/discover",data={"csrf_token":csrf,"connection_id":connection_id,"query":"Sea"},follow_redirects=False)
    assert r.status_code==403

def test_unsafe_graph_identifiers_rejected():
    from app.data_hub import safe_id
    from fastapi import HTTPException
    for value in ["https://evil.example","../secret","bad/id",""]:
        with pytest.raises(HTTPException):safe_id(value)
