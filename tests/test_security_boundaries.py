"""Security regressions; these do NOT prove multi-company isolation."""
import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from app.main import app
from app.workflow_core import standard_template,validate
from app.email_connectors import client_config

def test_oauth_disabled_by_default(monkeypatch):
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID","test-id")
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_SECRET","test-secret")
    monkeypatch.setenv("KAKI_PUBLIC_URL","https://example.invalid")
    monkeypatch.setenv("KAKI_ALLOW_SINGLE_COMPANY_OAUTH","false")
    with pytest.raises(HTTPException) as e:client_config("microsoft")
    assert e.value.status_code==503

def test_legacy_send_denied_even_when_enabled(monkeypatch):
    monkeypatch.setenv("ADMIN_USER","admin")
    monkeypatch.setenv("ADMIN_PASSWORD","test-password")
    monkeypatch.setenv("DISABLE_SEND","false")
    c=TestClient(app)
    assert c.post("/login",data={"username":"admin","password":"test-password"},follow_redirects=False).status_code==303
    from bs4 import BeautifulSoup
    csrf=BeautifulSoup(c.get("/").text,"html.parser").find("input",{"name":"csrf_token"})["value"]
    assert c.post("/emails/99999/send",data={"csrf_token":csrf},follow_redirects=False).status_code==403

def test_unapproved_send_and_arbitrary_node_blocked():
    w=standard_template()
    w["nodes"]=[{"id":"start","type":"shell","next":"end"},{"id":"end","type":"end"}];w["start"]="start"
    assert validate(w)
    w["nodes"]=[{"id":"start","type":"send_reply","next":"end"},{"id":"end","type":"end"}]
    assert any("prior approval" in e for e in validate(w))

def test_unauthenticated_pages_denied():
    c=TestClient(app)
    assert c.get("/flows").status_code==401
    assert c.get("/integrations").status_code==401
