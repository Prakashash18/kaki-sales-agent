import jwt
import pytest
from datetime import datetime,timezone,timedelta
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app.microsoft_multitenant import verified_microsoft_identity

TENANT_A="11111111-2222-3333-4444-555555555555"
TENANT_B="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OID="99999999-8888-7777-6666-555555555555"
CLIENT="kaki-public-client-id"

def signed_token(private_key, tenant, nonce, audience=CLIENT):
    current=datetime.now(timezone.utc)
    return jwt.encode({
        "tid":tenant, "oid":OID, "nonce":nonce, "sub":"user-opaque-id",
        "aud":audience, "iss":f"https://login.microsoftonline.com/{tenant}/v2.0",
        "iat":int(current.timestamp()),
        "exp":int((current+timedelta(minutes=30)).timestamp()),
    }, private_key, algorithm="RS256",headers={"kid":"test"})

def test_validated_microsoft_tenant_and_nonce(monkeypatch):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    seen=[]
    class JWK:
        def __init__(self,url,**kwargs):seen.append(url)
        def get_signing_key_from_jwt(self,token):
            from types import SimpleNamespace
            return SimpleNamespace(key=key.public_key())
    monkeypatch.setattr("app.microsoft_multitenant.PyJWKClient",JWK)
    token=signed_token(key,TENANT_A,"specific-nonce")
    identity=verified_microsoft_identity(token,CLIENT,"specific-nonce")
    assert identity["tenant_id"]==TENANT_A
    assert identity["object_id"]==OID
    assert seen==[f"https://login.microsoftonline.com/{TENANT_A}/discovery/v2.0/keys"]

def test_microsoft_id_token_claims_enforced(monkeypatch):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    class JWK:
        def __init__(self,*args,**kwargs):pass
        def get_signing_key_from_jwt(self,token):
            from types import SimpleNamespace
            return SimpleNamespace(key=key.public_key())
    monkeypatch.setattr("app.microsoft_multitenant.PyJWKClient",JWK)
    token=signed_token(key,TENANT_B,"correct")
    for audience,nonce in [("wrong-audience","correct"),(CLIENT,"wrong-nonce")]:
        with pytest.raises(HTTPException) as err:
            verified_microsoft_identity(token,audience,nonce)
        assert err.value.status_code==403
    with pytest.raises(HTTPException):
        verified_microsoft_identity(signed_token(key,TENANT_B,"right")+"tampered",CLIENT,"right")

def test_reject_personal_microsoft_tenant(monkeypatch):
    from app.microsoft_multitenant import PERSONAL_TENANT
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    with pytest.raises(HTTPException) as err:
        verified_microsoft_identity(signed_token(key,PERSONAL_TENANT,"x"),CLIENT,"x")
    assert err.value.status_code==403
