"""Verified Microsoft Entra identity binding for multitenant delegated OAuth.

The ID token is verified with a tenant-specific Microsoft signing key, exact
issuer/audience, nonce and expiry before its tenant and object IDs are trusted.
No access token is parsed as a source of user identity.
"""
import re
from datetime import datetime, timezone

import jwt
from jwt import PyJWKClient
from fastapi import HTTPException
from sqlalchemy import ForeignKey, String, DateTime, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

TENANT_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
PERSONAL_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"

class MicrosoftTenantBinding(Base):
    __tablename__ = "microsoft_tenant_bindings"
    __table_args__ = (UniqueConstraint("company_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company_profiles.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    bound_by_account_id: Mapped[int] = mapped_column(ForeignKey("kaki_accounts.id"))
    bound_by_object_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc))

def verified_microsoft_identity(id_token: str, client_id: str, expected_nonce: str):
    if not id_token or not client_id or not expected_nonce:
        raise HTTPException(400, "Missing Microsoft identity verification inputs")
    try:
        # The unverified tenant is used *only* to choose the trusted Microsoft
        # metadata endpoint; all claims are verified before authorization.
        unverified = jwt.decode(id_token, options={"verify_signature": False})
        tid = str(unverified.get("tid", "")).lower()
        if not TENANT_PATTERN.fullmatch(tid) or tid == PERSONAL_TENANT:
            raise ValueError("Unsupported Microsoft organisational tenant")
        issuer = f"https://login.microsoftonline.com/{tid}/v2.0"
        jwks = PyJWKClient(f"https://login.microsoftonline.com/{tid}/discovery/v2.0/keys",
                          cache_jwk_set=True, lifespan=300, timeout=10)
        signing_key = jwks.get_signing_key_from_jwt(id_token).key
        claims = jwt.decode(id_token, signing_key, algorithms=["RS256"],
                            audience=client_id, issuer=issuer,
                            options={"require": ["exp", "iat", "iss", "aud", "tid", "oid", "nonce"]},
                            leeway=60)
        if claims["nonce"] != expected_nonce or str(claims["tid"]).lower() != tid:
            raise ValueError("Nonce or tenant mismatch")
        oid = str(claims["oid"]).lower()
        if not TENANT_PATTERN.fullmatch(oid):
            raise ValueError("Invalid Microsoft object ID")
        return {"tenant_id": tid, "object_id": oid,
                "issuer": claims["iss"], "subject": str(claims.get("sub", ""))}
    except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(403, "Microsoft sign-in identity could not be verified") from exc
    except Exception as exc:
        # Includes untrusted metadata fetch/network errors; do not proceed.
        raise HTTPException(502, "Microsoft identity verification service unavailable") from exc
