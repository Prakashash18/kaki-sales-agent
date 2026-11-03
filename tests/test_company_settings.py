import io
from uuid import uuid4

from fastapi.testclient import TestClient
from app.main import app
from app.company_settings import discover_fields, parse_document, CompanyProfile, DocumentProfile
from app.db import Session
from sqlalchemy import select

def test_label_discovery_does_not_capture_values():
    text="PO Number: 12345\nCustomer: Private Customer\nQty: 2\nUnit Price: 50\n"
    result=discover_fields(text)
    assert "document_number" in result
    assert "quantity" in result
    assert "12345" not in str(result)
    assert "Private Customer" not in str(result)

def test_company_onboarding():
    c=TestClient(app)
    assert c.post("/login",data={"username":"admin","password":"test-password"},follow_redirects=False).status_code==303
    html=c.get("/settings")
    assert html.status_code==200
    from bs4 import BeautifulSoup
    csrf=BeautifulSoup(html.text,"html.parser").find("input",{"name":"csrf_token"})["value"]
    company="Config test "+uuid4().hex[:8]
    assert c.post("/settings/companies",data={"csrf_token":csrf,"name":company,"industry":"Marine","reply_tone":"concise","default_currency":"SGD"},follow_redirects=False).status_code==303
    with Session() as db:
        record=db.scalar(select(CompanyProfile).where(CompanyProfile.name==company))
        assert record is not None
        cid=record.id
    files={"sample":("quote.txt",io.BytesIO(b"Quotation Number: A100\nQty: 3\nUnit Price: 120\nTotal: 360\n"),"text/plain")}
    result=c.post(f"/settings/companies/{cid}/documents",data={"csrf_token":csrf,"doc_type":"supplier_quote"},files=files,follow_redirects=False)
    assert result.status_code==303, result.text
    with Session() as db:
        p=db.scalar(select(DocumentProfile).where(DocumentProfile.company_id==cid))
        assert p and p.review_status=="draft"
        pid=p.id
        assert "360" not in p.field_mapping_json
    assert c.post(f"/settings/document-profiles/{pid}/approve",data={"csrf_token":csrf,"mapping_json":'{"total":["total"],"document_number":["quotation number"]}'},follow_redirects=False).status_code==303
    with Session() as db:
        p=db.get(DocumentProfile,pid)
        assert p.review_status=="approved"
    assert c.post(f"/settings/document-profiles/{pid}/disable",data={"csrf_token":csrf},follow_redirects=False).status_code==303

def test_reject_invalid_upload_and_cross_site_post():
    c=TestClient(app)
    assert c.post("/login",data={"username":"admin","password":"test-password"},follow_redirects=False).status_code==303
    assert c.post("/settings/companies",data={"csrf_token":"bad","name":"Not allowed"},follow_redirects=False).status_code==403

def test_extract_txt():
    assert "PO Number" in parse_document("example.txt",b"PO Number: 100")
