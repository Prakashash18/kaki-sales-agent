from fastapi.testclient import TestClient
from app.secure_main import app

def test_readiness_requires_authenticated_admin():
    client=TestClient(app)
    response=client.get("/system/readiness")
    assert response.status_code==401
