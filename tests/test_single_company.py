"""Single-company deployment uses one persistent SQLite database and no company switching."""
import os
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from uuid import uuid4
from sqlalchemy import select

from app.secure_main import app
from app.db import Base,engine,Session
from app.identity import Account,Membership,password_hash
from app.company_settings import CompanyProfile
from app.secure_email_api import client_config

Base.metadata.create_all(engine)

def test_no_company_switching_or_second_company(monkeypatch):
    monkeypatch.setenv("KAKI_SINGLE_COMPANY","true")
    suffix=uuid4().hex[:8]
    with Session() as db:
        # Existing CI suites also create several companies. The request handlers
        # must refuse expansion regardless of how many exist already.
        company=CompanyProfile(name="Test "+suffix,active=False)
        account=Account(email="single-"+suffix+"@example.test",password_hash=password_hash("long-secure-pass-1234"))
        db.add_all([company,account]);db.flush()
        db.add(Membership(account_id=account.id,company_id=company.id,role="owner"))
        db.commit()
    client=TestClient(app)
    res=client.post("/login",data={"email":"single-"+suffix+"@example.test","password":"long-secure-pass-1234"},follow_redirects=False)
    assert res.status_code==303
    from bs4 import BeautifulSoup
    # Obtain CSRF from cookie-backed session without requiring an authorised
    # company page (which refuses multi-company CI fixtures).
    from itsdangerous import TimestampSigner
    from starlette.middleware.sessions import SessionMiddleware
    # The login page session renders no token, so use the cookie decode test only.
    from starlette.datastructures import Headers
    from itsdangerous import TimestampSigner
    import base64,json
    cookie=client.cookies.get("session")
    raw=TimestampSigner(os.getenv("SESSION_SECRET","local-test-secret-do-not-use-in-production")).unsign(cookie,max_age=43200)
    payload=json.loads(base64.b64decode(raw))
    csrf=payload["csrf"]
    forbidden=client.post("/companies/new",data={"csrf_token":csrf,"name":"Forbidden Company"},follow_redirects=False)
    assert forbidden.status_code==403
    forbidden=client.post("/companies/switch",data={"csrf_token":csrf,"company_id":company.id},follow_redirects=False)
    assert forbidden.status_code==403

def test_oauth_accepts_persistent_sqlite_only_for_single_company(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("KAKI_ENABLE_OAUTH","true")
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID","fake-id")
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_SECRET","fake-secret")
    monkeypatch.setenv("KAKI_PUBLIC_URL","https://example.test")
    monkeypatch.setenv("KAKI_TOKEN_ENCRYPTION_KEY",Fernet.generate_key().decode())
    monkeypatch.setenv("DATABASE_URL","sqlite:////var/data/kaki.db")
    monkeypatch.setenv("KAKI_SINGLE_COMPANY","true")
    assert client_config("microsoft")[0]=="fake-id"
    monkeypatch.setenv("KAKI_SINGLE_COMPANY","false")
    with pytest.raises(HTTPException) as exception:
        client_config("microsoft")
    assert exception.value.status_code==503
