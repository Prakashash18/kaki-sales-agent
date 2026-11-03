import os
"""Optional per-company Microsoft and Google OAuth connectors.

No credentials are stored unless KAKI_TOKEN_ENCRYPTION_KEY is configured.
This is development-only until multi-user isolation and OAuth verification ship.
Outbound email sending is intentionally not implemented here.
"""
import base64,json,os,secrets,re
from datetime import datetime,timezone,timedelta
from urllib.parse import urlencode
from fastapi import APIRouter,Request,Form,HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import ForeignKey,String,Text,DateTime,select,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
import httpx
from .db import Base,Session,engine
from .company_settings import CompanyProfile
from .identity import require_role, company_for_request
from .agent import classify

router=APIRouter(prefix="/integrations")
SCOPES={
  "microsoft":"offline_access User.Read Mail.Read",
  "google":"openid email https://www.googleapis.com/auth/gmail.readonly"
}
from .email_connectors import MailConnection

class TenantMessage(Base):
    __tablename__ = "tenant_messages"
    __table_args__ = (UniqueConstraint("connection_id","provider_message_id"),)
    id:Mapped[int] = mapped_column(primary_key=True)
    company_id:Mapped[int] = mapped_column(ForeignKey("company_profiles.id"),index=True)
    connection_id:Mapped[int] = mapped_column(ForeignKey("mail_connections.id"),index=True)
    provider_message_id:Mapped[str] = mapped_column(String(512))
    subject:Mapped[str] = mapped_column(String(500),default="")
    sender:Mapped[str] = mapped_column(String(320),default="")
    body:Mapped[str] = mapped_column(Text,default="")
    category:Mapped[str] = mapped_column(String(50),default="other")
    created_at:Mapped[datetime] = mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))

if os.getenv('KAKI_AUTO_CREATE_SCHEMA', 'true' if os.getenv('DATABASE_URL','sqlite:').startswith('sqlite') else 'false').lower() == 'true':
    Base.metadata.create_all(engine)
def now():return datetime.now(timezone.utc)
def crypto():
    from cryptography.fernet import Fernet
    key=os.getenv("KAKI_TOKEN_ENCRYPTION_KEY")
    if not key:raise HTTPException(503,"OAuth token encryption key not configured")
    try:return Fernet(key.encode())
    except Exception:raise HTTPException(503,"Invalid token encryption key")
def encrypt(value):return crypto().encrypt(value.encode()).decode()
def decrypt(value):return crypto().decrypt(value.encode()).decode()
def client_config(provider):
    if provider not in SCOPES:raise HTTPException(400,"Unknown provider")
    tag="MICROSOFT" if provider=="microsoft" else "GOOGLE"
    cid=os.getenv(f"{tag}_OAUTH_CLIENT_ID")
    secret=os.getenv(f"{tag}_OAUTH_CLIENT_SECRET")
    public=os.getenv("KAKI_PUBLIC_URL","").rstrip("/")
    if os.getenv("KAKI_ENABLE_OAUTH","false").lower()!="true":
        raise HTTPException(503,"OAuth is disabled in this staging environment")
    if not cid or not secret or not public or not public.startswith("https://"):raise HTTPException(503,f"{provider} OAuth not configured")
    crypto()
    if not os.getenv("DATABASE_URL","").startswith(("postgresql://","postgresql+psycopg://","postgres://")):
        raise HTTPException(503,"Persistent PostgreSQL is required before linking mailboxes")
    return cid,secret,public+f"/integrations/callback/{provider}"
def oauth_base(provider):
    if provider == "microsoft":
        tenant=os.getenv("MICROSOFT_TENANT_ID","organizations").strip()
        if not re.fullmatch(r"[0-9a-fA-F-]{36}|organizations",tenant):
            raise HTTPException(503,"Invalid Microsoft tenant ID")
        return "https://login.microsoftonline.com/"+tenant+"/oauth2/v2.0"
    return "https://oauth2.googleapis.com"
def redirect(path):return RedirectResponse(path,status_code=303)
@router.get("")
def connections(request:Request):
    require_role(request,'viewer')
    with Session() as db:
        c=company_for_request(db,request)
        rows=db.scalars(select(MailConnection).where(MailConnection.company_id==c.id).order_by(MailConnection.id)).all()
        data=[{"id":row.id,"provider":row.provider,"address":row.address,"status":row.status,"last_sync":str(row.last_sync_at or "")[:19]} for row in rows]
        company=c.name
    return request.app.state.templates.TemplateResponse(request,"integrations.html",{"request":request,"user":request.session["user"],"csrf":request.session["csrf"],"company":company,"connections":data})
@router.post("/connect/{provider}")
def begin(request:Request,provider:str,csrf_token:str=Form(...)):
    require_role(request,'admin',csrf=csrf_token)
    cid,secret,redirect_uri=client_config(provider)
    with Session() as db:c=company_for_request(db,request);company_id=c.id
    nonce=secrets.token_urlsafe(24)
    request.session["oauth_pending"]={"provider":provider,"company_id":company_id,"nonce":nonce}
    if provider=="microsoft":
        url=oauth_base(provider)+"/authorize?"+urlencode({"client_id":cid,"redirect_uri":redirect_uri,"response_type":"code","response_mode":"query","scope":SCOPES[provider],"state":nonce})
    else:
        url="https://accounts.google.com/o/oauth2/v2/auth?"+urlencode({"client_id":cid,"redirect_uri":redirect_uri,"response_type":"code","scope":SCOPES[provider],"access_type":"offline","prompt":"consent","state":nonce})
    return RedirectResponse(url,status_code=303)
@router.get("/callback/{provider}")
async def callback(request:Request,provider:str,code:str="",state:str="",error:str=""):
    require_role(request,'admin')
    pending=request.session.pop("oauth_pending",None)
    if error:raise HTTPException(400,"Provider rejected OAuth consent")
    if not pending or pending.get("provider")!=provider or not secrets.compare_digest(pending.get("nonce",""),state) or not code:raise HTTPException(400,"Invalid or expired OAuth state")
    cid,secret,redirect_uri=client_config(provider)
    with Session() as db:
        c=company_for_request(db,request)
        if c.id!=pending["company_id"]:raise HTTPException(403,"Company changed during sign-in")
    endpoint=(oauth_base(provider)+"/token") if provider=="microsoft" else "https://oauth2.googleapis.com/token"
    form={"client_id":cid,"client_secret":secret,"redirect_uri":redirect_uri,"grant_type":"authorization_code","code":code}
    if provider=="microsoft":form["scope"]=SCOPES[provider]
    async with httpx.AsyncClient(timeout=25) as http:
        r=await http.post(endpoint,data=form);r.raise_for_status();token=r.json()
        if provider=="microsoft":
            me=await http.get("https://graph.microsoft.com/v1.0/me",headers={"Authorization":"Bearer "+token["access_token"]})
            me.raise_for_status();address=me.json().get("mail") or me.json().get("userPrincipalName")
        else:
            me=await http.get("https://openidconnect.googleapis.com/v1/userinfo",headers={"Authorization":"Bearer "+token["access_token"]})
            me.raise_for_status();address=me.json()["email"]
    if not address:raise HTTPException(502,"Could not determine connected mailbox")
    with Session() as db:
        row=db.scalar(select(MailConnection).where(MailConnection.company_id==c.id,MailConnection.provider==provider,MailConnection.address==address))
        if not row:
            row=MailConnection(company_id=c.id,provider=provider,address=address,access_ciphertext="")
            db.add(row)
        row.access_ciphertext=encrypt(token["access_token"])
        if token.get("refresh_token"):row.refresh_ciphertext=encrypt(token["refresh_token"])
        row.expires_at=now()+timedelta(seconds=int(token.get("expires_in",3600)))
        row.status="connected";db.commit()
    return redirect("/integrations")
async def access_token(row):
    if row.expires_at and row.expires_at.replace(tzinfo=timezone.utc)>now()+timedelta(minutes=2):
        return decrypt(row.access_ciphertext)
    if not row.refresh_ciphertext:raise HTTPException(409,"Reconnect mailbox to renew access")
    cid,secret,_=client_config(row.provider)
    endpoint=(oauth_base(row.provider)+"/token") if row.provider=="microsoft" else "https://oauth2.googleapis.com/token"
    payload={"grant_type":"refresh_token","refresh_token":decrypt(row.refresh_ciphertext),"client_id":cid,"client_secret":secret}
    if row.provider=="microsoft":payload["scope"]=SCOPES[row.provider]
    async with httpx.AsyncClient(timeout=25) as http:
        r=await http.post(endpoint,data=payload);r.raise_for_status();t=r.json()
    row.access_ciphertext=encrypt(t["access_token"])
    if t.get("refresh_token"):row.refresh_ciphertext=encrypt(t["refresh_token"])
    row.expires_at=now()+timedelta(seconds=int(t.get("expires_in",3600)))
    return t["access_token"]
def gmail_text(payload):
    parts=[payload];texts=[]
    while parts:
        p=parts.pop()
        parts.extend(p.get("parts",[]))
        if p.get("mimeType")=="text/plain" and p.get("body",{}).get("data"):
            raw=p["body"]["data"];texts.append(base64.urlsafe_b64decode(raw+"="*((-len(raw))%4)).decode("utf-8","replace"))
    return "\n".join(texts)[:20000]
async def fetch_inbox(row,token):
    async with httpx.AsyncClient(timeout=30) as http:
        headers={"Authorization":"Bearer "+token}
        if row.provider=="microsoft":
            resp=await http.get("https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",headers=headers,params={"$top":20,"$select":"id,subject,from,body,hasAttachments"})
            resp.raise_for_status()
            return [{"id":"ms:"+str(row.id)+":"+m["id"],"subject":m.get("subject",""),"sender":m.get("from",{}).get("emailAddress",{}).get("address",""),"body":m.get("body",{}).get("content","")[:20000],"attachment":m.get("hasAttachments",False)} for m in resp.json().get("value",[])]
        resp=await http.get("https://gmail.googleapis.com/gmail/v1/users/me/messages",headers=headers,params={"maxResults":20,"labelIds":"INBOX"})
        resp.raise_for_status();out=[]
        for ref in resp.json().get("messages",[]):
            msg=await http.get("https://gmail.googleapis.com/gmail/v1/users/me/messages/"+ref["id"],headers=headers,params={"format":"full"})
            msg.raise_for_status();m=msg.json();headers_dict={h["name"].lower():h["value"] for h in m.get("payload",{}).get("headers",[])}
            out.append({"id":"gm:"+str(row.id)+":"+m["id"],"subject":headers_dict.get("subject",""),"sender":headers_dict.get("from",""),"body":gmail_text(m.get("payload",{})) or m.get("snippet",""),"attachment":bool(m.get("payload",{}).get("parts"))})
        return out
@router.post("/{connection_id}/test")
async def test_connection(request:Request,connection_id:int,csrf_token:str=Form(...)):
    """Live read-only Microsoft Graph diagnostics (does not import messages)."""
    require_role(request,"admin",csrf=csrf_token)
    with Session() as db:
        c=company_for_request(db,request)
        row=db.get(MailConnection,connection_id)
        if not row or row.company_id!=c.id or row.status!="connected":
            raise HTTPException(404,"Mailbox connection not found")
        if row.provider!="microsoft":
            raise HTTPException(400,"This diagnostic is for Microsoft Graph")
        bearer=await access_token(row)
        db.commit()
    async with httpx.AsyncClient(timeout=30) as http:
        headers={"Authorization":"Bearer "+bearer,"Accept":"application/json"}
        profile=await http.get("https://graph.microsoft.com/v1.0/me",headers=headers)
        if profile.status_code!=200:
            raise HTTPException(502,f"Graph profile check failed (HTTP {profile.status_code})")
        inbox=await http.get("https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
                            headers=headers,params={"$top":1,"$select":"id,subject"})
        if inbox.status_code==403:
            raise HTTPException(502,"Graph denied Mail.Read. Check consent and mailbox permissions.")
        if inbox.status_code!=200:
            raise HTTPException(502,f"Graph inbox check failed (HTTP {inbox.status_code})")
        # Never display customer message content in a connection diagnostic.
        return {"status":"ok","provider":"microsoft","signed_in_user":profile.json().get("userPrincipalName"),
                "mailbox_readable":True,"sample_message_count":len(inbox.json().get("value",[])),
                "sending_enabled":False}

@router.post("/{connection_id}/sync")
async def sync(request:Request,connection_id:int,csrf_token:str=Form(...)):
    require_role(request,'admin',csrf=csrf_token)
    if os.getenv("KAKI_ENABLE_MAIL_SYNC","false").lower()!="true":
        raise HTTPException(503,"Mailbox sync remains disabled pending tenant isolation and security review")
    with Session() as db:
        c=company_for_request(db,request);row=db.get(MailConnection,connection_id)
        if not row or row.company_id!=c.id or row.status!="connected":raise HTTPException(404)
        token=await access_token(row);incoming=await fetch_inbox(row,token)
        for item in incoming:
            if db.scalar(select(TenantMessage).where(TenantMessage.connection_id==row.id,TenantMessage.provider_message_id==item["id"])):continue
            a=classify(item["subject"],item["body"],item["attachment"])
            db.add(TenantMessage(company_id=c.id,connection_id=row.id,provider_message_id=item["id"],
                sender=item["sender"][:320],subject=item["subject"][:500],body=item["body"][:20000],
                category=a["category"]))
        row.last_sync_at=now();db.commit()
    return redirect("/integrations/inbox")
@router.get("/inbox")
def tenant_inbox(request:Request):
    require_role(request,"viewer")
    with Session() as db:
        c=company_for_request(db,request)
        items=db.scalars(select(TenantMessage).where(TenantMessage.company_id==c.id).order_by(TenantMessage.id.desc()).limit(50)).all()
        messages=[{"id":m.id,"subject":m.subject,"sender":m.sender,"category":m.category} for m in items]
    return request.app.state.templates.TemplateResponse(request,"tenant_inbox.html",
        {"request":request,"user":request.session["user"],"csrf":request.session["csrf"],"messages":messages})
@router.get("/inbox/{message_id}")
def tenant_message(request:Request,message_id:int):
    require_role(request,"viewer")
    with Session() as db:
        c=company_for_request(db,request)
        m=db.scalar(select(TenantMessage).where(TenantMessage.id==message_id,TenantMessage.company_id==c.id))
        if not m:raise HTTPException(404)
        item={"subject":m.subject,"sender":m.sender,"category":m.category,"body":m.body}
    return request.app.state.templates.TemplateResponse(request,"tenant_message.html",
        {"request":request,"user":request.session["user"],"csrf":request.session["csrf"],"item":item})
@router.post("/{connection_id}/disconnect")
def disconnect(request:Request,connection_id:int,csrf_token:str=Form(...)):
    require_role(request,'admin',csrf=csrf_token)
    with Session() as db:
        c=company_for_request(db,request);row=db.get(MailConnection,connection_id)
        if not row or row.company_id!=c.id:raise HTTPException(404)
        row.status="disconnected";row.access_ciphertext="";row.refresh_ciphertext="";db.commit()
    return redirect("/integrations")
