"""Kaki staging application: authenticated, role-scoped and fail-closed.

Legacy routes are intentionally NOT registered. Production requires a persistent
SQLite disk or PostgreSQL, verified OAuth consent, and tested backups.
"""
import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .db import Base, engine
from . import operations, company_settings, workflow_core
from . import identity, secure_settings, secure_workflow_api, secure_email_api

secret = os.getenv("SESSION_SECRET", "")
env = os.getenv("APP_ENV", "testing")
if env in ("production", "staging") and len(secret) < 40:
    raise RuntimeError("A 40+ character SESSION_SECRET is required for staging and production")
if not secret:
    secret = "local-test-secret-do-not-use-in-production"

if env == "production":
    address=os.getenv("DATABASE_URL","")
    if address.startswith("sqlite"):
        if not address.startswith("sqlite:////var/data/"):
            raise RuntimeError("Production SQLite must use persistent /var/data mount")
        if os.getenv("KAKI_SINGLE_COMPANY","false").lower() != "true":
            raise RuntimeError("Production SQLite is restricted to single-company Kaki")
    elif not address.startswith(("postgres://","postgresql://","postgresql+psycopg://")):
        raise RuntimeError("Production requires persistent storage")

app = FastAPI(title="Kaki Secure Workflow Pilot", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=secret, same_site="lax",
    https_only=env in ("staging", "production"), max_age=12*3600)
hosts = [host.strip() for host in os.getenv("KAKI_ALLOWED_HOSTS", "").split(",") if host.strip()]
if hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
app.state.templates = Jinja2Templates(directory="app/templates")

app.include_router(identity.router)
app.include_router(secure_settings.router)
app.include_router(secure_workflow_api.router)
app.include_router(secure_email_api.router)

if os.getenv("KAKI_AUTO_CREATE_SCHEMA", "false").lower() == "true" and env != "production":
    Base.metadata.create_all(engine)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    if env in ("production", "staging"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

@app.get("/health")
def health():
    return {"status": "ok", "version": "secure-pilot"}

@app.get("/")
def homepage(request: Request):
    from .identity import require_user
    try:
        require_user(request)
    except HTTPException:
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/flows", status_code=303)

@app.on_event("startup")
async def begin_due_workflow_worker():
    if os.getenv("KAKI_ENABLE_DUE_WORKER","false").lower()!="true":
        return
    import asyncio
    import logging
    from .db import Session
    from .workflow_core import run_due
    async def tick():
        while True:
            await asyncio.sleep(30)
            try:
                def process():
                    with Session() as db:
                        count=run_due(db,limit=20)
                        db.commit()
                        return count
                await asyncio.to_thread(process)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger(__name__).exception("Kaki scheduled workflow check failed")
    app.state.due_worker=asyncio.create_task(tick())

@app.on_event("shutdown")
async def stop_due_workflow_worker():
    task=getattr(app.state,"due_worker",None)
    if task:
        task.cancel()
        try:
            await task
        except __import__("asyncio").CancelledError:
            pass
