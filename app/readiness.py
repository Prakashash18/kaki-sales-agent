"""Owner-visible readiness without exposing credentials or customer data."""
import os
from pathlib import Path

from fastapi import APIRouter, Request
from sqlalchemy import text, select
from .db import Session, engine
from .identity import require_role
from .email_connectors import MailConnection
from .company_settings import CompanyProfile

router=APIRouter(prefix="/system")

@router.get("/readiness")
def readiness(request:Request):
    require_role(request,"admin")
    checks={}
    try:
        with Session() as db:
            db.execute(text("SELECT 1"))
            checks["database"]="ready"
            connections=db.scalars(select(MailConnection).where(
                MailConnection.company_id==request.session["company_id"],
                MailConnection.status=="connected")).all()
            checks["microsoft_mailbox"]="connected" if any(c.provider=="microsoft" for c in connections) else "not_connected"
    except Exception:
        checks["database"]="unavailable"
        checks["microsoft_mailbox"]="unknown"
    address=str(engine.url)
    if address.startswith("sqlite:///"):
        path=engine.url.database
        checks["persistent_disk"]="ready" if path and path.startswith("/var/data/") and Path("/var/data").is_dir() else "not_configured"
    else:
        checks["persistent_disk"]="not_needed_for_managed_postgres"
    settings=("MICROSOFT_OAUTH_CLIENT_ID","MICROSOFT_OAUTH_CLIENT_SECRET","KAKI_TOKEN_ENCRYPTION_KEY","KAKI_PUBLIC_URL")
    checks["microsoft_oauth"]="configured" if all(os.getenv(k) for k in settings) and os.getenv("KAKI_ENABLE_OAUTH","false").lower()=="true" else "waiting_for_configuration"
    checks["outbound_mail"]="disabled" # The secure pilot has no mail sending endpoint.
    checks["backup_export"]="enabled" if os.getenv("KAKI_ENABLE_BACKUP_EXPORT","false").lower()=="true" else "disabled"
    checks["due_worker"]="enabled" if os.getenv("KAKI_ENABLE_DUE_WORKER","false").lower()=="true" else "disabled"
    checks["single_company"]="enabled" if os.getenv("KAKI_SINGLE_COMPANY","false").lower()=="true" else "disabled"
    return {"product":"Kaki single-company pilot","checks":checks,
            "test_ready":checks["database"]=="ready" and checks["single_company"]=="enabled",
            "microsoft_testing_requires_consent":True}
