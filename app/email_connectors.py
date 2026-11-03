"""Optional per-company Microsoft and Google OAuth connectors.

No credentials are stored unless KAKI_TOKEN_ENCRYPTION_KEY is configured.
This is development-only until multi-user isolation and OAuth verification ship.
Outbound email sending is intentionally not implemented here.
"""
import base64,json,os,secrets
from datetime import datetime,timezone,timedelta
from urllib.parse import urlencode
from fastapi import APIRouter,Request,Form,HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import ForeignKey,String,Text,DateTime,select,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
import httpx
from .db import Base,Session,engine,Email
from .company_settings import CompanyProfile
from .agent import classify

router=APIRouter(prefix="/integrations")
SCOPES={
  "microsoft":"offline_access User.Read Mail.Read",
  "google":"openid email https://www.googleapis.com/auth/gmail.readonly"
}
class MailConnection(Base):
    __tablename__="mail_connections"
    __table_args__=(UniqueConstraint("company_id","provider","address"),)
    id:Mapped[int]=mapped_column(primary_key=True)
    company_id:Mapped[int]=mapped_column(ForeignKey("company_profiles.id"),index=True)
    provider:Mapped[str]=mapped_column(String(30))
    address:Mapped[str]=mapped_column(String(320))
    access_ciphertext:Mapped[str]=mapped_column(Text)
    refresh_ciphertext:Mapped[str]=mapped_column(Text,default="")
    expires_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    status:Mapped[str]=mapped_column(String(30),default="connected")
    last_sync_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
Base.metadata.create_all(engine)
def now():return datetime.now(timezone.utc)
def require(request,csrf=None):
    if not request.session.get("user"):raise HTTPException(401)
    if csrf is not None and (not csrf or not secrets.compare_digest(request.session.get("csrf",""),csrf)):raise HTTPException(403)
def active_company(db):
    c=db.scalar(select(CompanyProfile).where(CompanyProfile.active.is_(True)))
    if not c:raise HTTPException(409,"Configure a company first")
    return c
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
    if not cid or not secret or not public or not public.startswith("https://"):raise HTTPException(503,f"{provider} OAuth not configured")
    crypto()
    if not os.getenv("DATABASE_URL","").startswith(("postgresql://","postgresql+psycopg://","postgres://")):
        raise HTTPException(503,"Persistent PostgreSQL is required before linking mailboxes")
    return cid,secret,public+f"/integrations/callback/{provider}"
def oauth_base(provider):
    return "https://login.microsoftonline.com/organizations/oauth2/v2.0" if provider=="microsoft" else "https://oauth2.googleapis.com"
def redirect(path):return RedirectResponse(path,status_code=303)
@router.get("")
def connections(request:Request):
    require(request)
    with Session() as db:
        c=active_company(db)
        rows=db.scalars(select(MailConnection).where(MailConnection.company_id==c.id).order_by(MailConnection.id)).all()
        data=[{"id":row.id,"provider":row.provider,"address":row.address,"status":row.status,"last_sync":str(row.last_sync_at or "")[:19]} for row in rows]
        company=c.name
    return request.app.state.templates.TemplateResponse(request,"integrations.html",{"request":request,"user":request.session["user"],"csrf":request.session["csrf"],"company":company,"connections":data})
@router.post("/connect/{provider}")
def begin(request:Request,provider:str,csrf_token:str=Form(...)):
    require(request,csrf_token)
    cid,secret,redirect_uri=client_config(provider)
    with Session() as db:c=active_company(db);company_id=c.id
    nonce=secrets.token_urlsafe(24)
    request.session["oauth_pending"]={"provider":provider,"company_id":company_id,"nonce":nonce}
    if provider=="microsoft":
        url=oauth_base(provider)+"/authorize?"+urlencode({"client_id":cid,"redirect_uri":redirect_uri,"response_type":"code","response_mode":"query","scope":SCOPES[provider],"state":nonce})
    else:
        url="https://accounts.google.com/o/oauth2/v2/auth?"+urlencode({"client_id":cid,"redirect_uri":redirect_uri,"response_type":"code","scope":SCOPES[provider],"access_type":"offline","prompt":"consent","state":nonce})
    return RedirectResponse(url,status_code=303)
@router.get("/callback/{provider}")
async def callback(request:Request,provider:str,code:str="",state:str="",error:str=""):
    require(request)
    pending=request.session.pop("oauth_pending",None)
    if error:raise HTTPException(400,"Provider rejected OAuth consent")
    if not pending or pending.get("provider")!=provider or not secrets.compare_digest(pending.get("nonce",""),state) or not code:raise HTTPException(400,"Invalid or expired OAuth state")
    cid,secret,redirect_uri=client_config(provider)
    with Session() as db:
        c=active_company(db)
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
@router.post("/{connection_id}/sync")
async def sync(request:Request,connection_id:int,csrf_token:str=Form(...)):
    require(request,csrf_token)
    if os.getenv("KAKI_ENABLE_MAIL_SYNC","false").lower()!="true":
        raise HTTPException(503,"Mailbox sync remains disabled pending tenant isolation and security review")
    with Session() as db:
        c=active_company(db);row=db.get(MailConnection,connection_id)
        if not row or row.company_id!=c.id or row.status!="connected":raise HTTPException(404)
        token=await access_token(row);incoming=await fetch_inbox(row,token)
        for item in incoming:
            if db.scalar(select(Email).where(Email.graph_id==item["id"])):continue
            a=classify(item["subject"],item["body"],item["attachment"])
            db.add(Email(graph_id=item["id"],sender=item["sender"][:300],subject=item["subject"][:500],body=item["body"],category=a["category"],summary=a["summary"],draft=a["draft"],confidence=a["confidence"],source=a["source"],has_attachments=item["attachment"]))
        row.last_sync_at=now();db.commit()
    return redirect("/integrations")
@router.post("/{connection_id}/disconnect")
def disconnect(request:Request,connection_id:int,csrf_token:str=Form(...)):
    require(request,csrf_token)
    with Session() as db:
        c=active_company(db);row=db.get(MailConnection,connection_id)
        if not row or row.company_id!=c.id:raise HTTPException(404)
        row.status="disconnected";row.access_ciphertext="";row.refresh_ciphertext="";db.commit()
    return redirect("/integrations")
