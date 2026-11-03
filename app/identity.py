"""Kaki user identities, membership authorization, and invitation lifecycle."""
import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, Session
from .company_settings import CompanyProfile

router = APIRouter()
ROLES = {"owner": 5, "admin": 4, "manager": 3, "staff": 2, "viewer": 1}
SESSION_MAX_SECONDS = 12 * 3600

def utcnow():
    return datetime.now(timezone.utc)

def normalized_email(value):
    addr = (value or "").strip().lower()
    if len(addr) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", addr):
        raise HTTPException(400, "Enter a valid email address")
    return addr

def password_hash(password):
    if not 12 <= len(password) <= 256:
        raise HTTPException(400, "Password must contain 12 to 256 characters")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return "pbkdf2_sha256$600000$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()

def password_verify(password, encoded):
    try:
        algorithm, iterations, salt, hash_ = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(iterations) < 500_000 or len(password) > 256:
            return False
        proposed = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.urlsafe_b64decode(salt), int(iterations))
        return hmac.compare_digest(proposed, base64.urlsafe_b64decode(hash_))
    except (ValueError, TypeError):
        return False

class Account(Base):
    __tablename__ = "kaki_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Membership(Base):
    __tablename__ = "kaki_memberships"
    __table_args__ = (UniqueConstraint("account_id", "company_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("kaki_accounts.id"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company_profiles.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Invitation(Base):
    __tablename__ = "kaki_invitations"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company_profiles.id"), index=True)
    invited_email: Mapped[str] = mapped_column(String(254))
    invited_role: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[int] = mapped_column(ForeignKey("kaki_accounts.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class LoginFailure(Base):
    __tablename__ = "kaki_login_failures"
    email: Mapped[str] = mapped_column(String(254), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

def _aware(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

def require_user(request):
    user_id = request.session.get("uid")
    since = request.session.get("login_at", 0)
    if not isinstance(user_id, int) or time.time() - since > SESSION_MAX_SECONDS:
        raise HTTPException(401, "Please sign in")
    with Session() as db:
        user = db.get(Account, user_id)
        if not user or user.disabled:
            raise HTTPException(401, "Account disabled or unavailable")
        return user

def require_role(request, required="viewer", csrf=None):
    user = require_user(request)
    if csrf is not None:
        supplied = csrf or ""
        expected = request.session.get("csrf", "")
        if not expected or not secrets.compare_digest(supplied, expected):
            raise HTTPException(403, "Invalid security token")
    company_id = request.session.get("company_id")
    if not isinstance(company_id, int):
        raise HTTPException(403, "Select an authorised company")
    with Session() as db:
        member = db.scalar(select(Membership).where(
            Membership.account_id == user.id,
            Membership.company_id == company_id,
            Membership.active.is_(True)
        ))
        if not member or ROLES.get(member.role, 0) < ROLES[required]:
            raise HTTPException(403, "Insufficient company permissions")
    return user

def company_for_request(db, request, role="viewer"):
    require_role(request, role)
    company_id = request.session["company_id"]
    company = db.get(CompanyProfile, company_id)
    if not company:
        raise HTTPException(404, "Company unavailable")
    return company

def login_session(request, user, company_id=None):
    with Session() as db:
        memberships = db.scalars(select(Membership).where(Membership.account_id == user.id, Membership.active.is_(True))).all()
        selected = next((m for m in memberships if m.company_id == company_id), None)
        if not selected:
            selected = memberships[0] if memberships else None
    request.session.clear()
    request.session["uid"] = user.id
    request.session["user"] = user.email
    request.session["company_id"] = selected.company_id if selected else None
    request.session["login_at"] = int(time.time())
    request.session["csrf"] = secrets.token_urlsafe(32)

def render(request, name, **data):
    return request.app.state.templates.TemplateResponse(
        request, name, {"request": request, "user": request.session.get("user"),
                         "csrf": request.session.get("csrf"), **data}
    )

def redirect(url="/flows"):
    return RedirectResponse(url, status_code=303)

@router.get("/login")
def login_page(request: Request):
    return render(request, "secure_login.html", error="")

@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    addr = normalized_email(email)
    # A persistent per-account throttle, shared across application workers.
    with Session() as db:
        failure = db.get(LoginFailure, addr)
        if failure and _aware(failure.locked_until) and _aware(failure.locked_until) > utcnow():
            raise HTTPException(429, "Too many login attempts. Try later.")
        user = db.scalar(select(Account).where(Account.email == addr))
        # Always perform a full password hash even for unknown users.
        fake = password_hash("unused-dummy-password")
        ok = password_verify(password, user.password_hash if user else fake)
        if not user or not ok or user.disabled:
            if not failure:
                failure = LoginFailure(email=addr, attempts=0)
                db.add(failure)
            failure.attempts += 1
            if failure.attempts >= 6:
                failure.locked_until = utcnow() + timedelta(minutes=15)
                failure.attempts = 0
            db.commit()
            return render(request, "secure_login.html", error="Invalid credentials")
        if failure:
            db.delete(failure)
            db.commit()
        login_session(request, user)
    return redirect()

@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    require_role(request, csrf=csrf_token)
    request.session.clear()
    return redirect("/login")

@router.get("/setup")
def setup_page(request: Request):
    if not os.getenv("KAKI_BOOTSTRAP_TOKEN"):
        raise HTTPException(503, "Bootstrap disabled")
    return render(request, "secure_setup.html", error="")

@router.post("/setup")
def setup(request: Request, bootstrap_token: str = Form(...), email: str = Form(...),
          password: str = Form(...), company_name: str = Form(...)):
    expected = os.getenv("KAKI_BOOTSTRAP_TOKEN", "")
    if not expected or not secrets.compare_digest(expected, bootstrap_token):
        raise HTTPException(403, "Bootstrap token rejected")
    addr = normalized_email(email)
    name = company_name.strip()
    if not 2 <= len(name) <= 200:
        raise HTTPException(400, "Company name required")
    hashed = password_hash(password)
    with Session() as db:
        if db.scalar(select(Account.id).limit(1)) is not None:
            raise HTTPException(409, "Initial setup is already complete")
        user = Account(email=addr, password_hash=hashed)
        company = CompanyProfile(name=name, active=False)
        db.add_all([user, company])
        db.flush()
        db.add(Membership(account_id=user.id, company_id=company.id, role="owner"))
        db.commit()
        user_id, company_id = user.id, company.id
    with Session() as db:
        login_session(request, db.get(Account, user_id), company_id)
    return redirect()

@router.post("/companies/switch")
def switch(request: Request, company_id: int = Form(...), csrf_token: str = Form(...)):
    user = require_user(request)
    if not request.session.get("csrf") or not secrets.compare_digest(request.session["csrf"], csrf_token):
        raise HTTPException(403)
    with Session() as db:
        membership = db.scalar(select(Membership).where(
            Membership.account_id == user.id, Membership.company_id == company_id,
            Membership.active.is_(True)
        ))
        if not membership:
            raise HTTPException(403, "Company is not available to this user")
    request.session["company_id"] = company_id
    request.session["csrf"] = secrets.token_urlsafe(32)
    return redirect()

@router.post("/companies/new")
def create_company(request: Request, name: str = Form(...), csrf_token: str = Form(...)):
    user = require_user(request)
    if not request.session.get("csrf") or not secrets.compare_digest(request.session["csrf"], csrf_token):
        raise HTTPException(403)
    clean = name.strip()
    if not 2 <= len(clean) <= 200: raise HTTPException(400)
    with Session() as db:
        if db.scalar(select(CompanyProfile.id).where(CompanyProfile.name == clean)):
            raise HTTPException(409, "A company with that name already exists")
        company = CompanyProfile(name=clean, active=False)
        db.add(company);db.flush()
        db.add(Membership(account_id=user.id, company_id=company.id, role="owner"))
        db.commit();company_id=company.id
    request.session["company_id"] = company_id
    request.session["csrf"] = secrets.token_urlsafe(32)
    return redirect()

@router.post("/companies/invite")
def invite(request: Request, email: str = Form(...), role: str = Form(...),
           csrf_token: str = Form(...)):
    user = require_role(request, "admin", csrf=csrf_token)
    role = role.lower().strip()
    if role not in ROLES or role == "owner":
        raise HTTPException(400, "Choose admin, manager, staff, or viewer")
    addr = normalized_email(email)
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    with Session() as db:
        company = company_for_request(db, request, "admin")
        record = Invitation(token_hash=token_hash, company_id=company.id,
                            invited_email=addr, invited_role=role,
                            created_by=user.id, expires_at=utcnow() + timedelta(hours=48))
        db.add(record);db.commit()
    # Display only once. A production service should send via verified transactional mail.
    return render(request, "secure_invite_result.html", invitation_link=str(request.base_url).rstrip("/") + "/invite/" + raw)

@router.get("/invite/{token}")
def invite_page(request: Request, token: str):
    if len(token) > 100: raise HTTPException(404)
    with Session() as db:
        invitation = db.scalar(select(Invitation).where(Invitation.token_hash == hashlib.sha256(token.encode()).hexdigest()))
        if not invitation or invitation.used_at or _aware(invitation.expires_at) < utcnow():
            raise HTTPException(404, "Invitation expired")
        return render(request, "secure_accept.html", email=invitation.invited_email, token=token)

@router.post("/invite/{token}")
def accept(request: Request, token: str, email: str = Form(...), password: str = Form(...)):
    addr = normalized_email(email)
    digest = hashlib.sha256(token.encode()).hexdigest()
    with Session() as db:
        invitation = db.scalar(select(Invitation).where(Invitation.token_hash == digest).with_for_update())
        if not invitation or invitation.used_at or _aware(invitation.expires_at) < utcnow() or invitation.invited_email != addr:
            raise HTTPException(403, "Invalid invitation")
        user = db.scalar(select(Account).where(Account.email == addr))
        if user:
            # Existing account must authenticate first. Do not allow invitation token to reset an account password.
            if not request.session.get("uid") or request.session["uid"] != user.id:
                raise HTTPException(403, "Sign in to your existing account before accepting invitation")
        else:
            user = Account(email=addr, password_hash=password_hash(password))
            db.add(user);db.flush()
        existing = db.scalar(select(Membership).where(Membership.account_id == user.id,
                                                       Membership.company_id == invitation.company_id))
        if existing:
            existing.active = True
            existing.role = invitation.invited_role
        else:
            db.add(Membership(account_id=user.id, company_id=invitation.company_id,
                              role=invitation.invited_role))
        invitation.used_at = utcnow()
        db.commit()
        user_id, company_id = user.id, invitation.company_id
    with Session() as db:
        login_session(request, db.get(Account, user_id), company_id)
    return redirect()

@router.post("/members/{member_id}/revoke")
def revoke_member(request: Request, member_id: int, csrf_token: str = Form(...)):
    user = require_role(request, "owner", csrf=csrf_token)
    with Session() as db:
        company = company_for_request(db, request, "owner")
        member = db.get(Membership, member_id)
        if not member or member.company_id != company.id:
            raise HTTPException(404)
        if member.role == "owner":
            raise HTTPException(409, "Owner access cannot be revoked here")
        member.active = False
        db.commit()
    return redirect("/settings")
