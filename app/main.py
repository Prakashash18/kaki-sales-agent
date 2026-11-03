import os,secrets,re
from datetime import datetime,timezone
from fastapi import FastAPI,Request,Form,HTTPException
from fastapi.responses import HTMLResponse,RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select
from .db import Session,Email,Audit
from .agent import classify
from .graph import get_messages,send_reply,configured
app=FastAPI(title='Kaki Sales Agent MVP',docs_url=None)
app.add_middleware(SessionMiddleware,secret_key=os.getenv('SESSION_SECRET','development-secret-only'),same_site='lax',https_only=os.getenv('APP_ENV')=='production')
t=Jinja2Templates(directory='app/templates')
def go(path='/'):return RedirectResponse(path,status_code=303)
def permitted(request,token):return bool(request.session.get('user')) and bool(token) and secrets.compare_digest(token,request.session.get('csrf',''))
def context(request,**items):return {'request':request,'user':request.session.get('user'),'csrf':request.session.get('csrf'),**items}
@app.get('/health')
def health():return {'status':'ok'}
@app.get('/login',response_class=HTMLResponse)
def login_page(request:Request):return t.TemplateResponse(request,'login.html',context(request,error=''))
@app.post('/login')
def login(request:Request,username:str=Form(...),password:str=Form(...)):
    if not os.getenv('ADMIN_PASSWORD') or not (secrets.compare_digest(username,os.getenv('ADMIN_USER','admin')) and secrets.compare_digest(password,os.environ['ADMIN_PASSWORD'])):
        return t.TemplateResponse(request,'login.html',context(request,error='Incorrect credentials'),status_code=401)
    request.session.clear();request.session['user']=username;request.session['csrf']=secrets.token_urlsafe(32);return go()
@app.post('/logout')
def logout(request:Request,csrf_token:str=Form(...)):
    if not permitted(request,csrf_token):raise HTTPException(403)
    request.session.clear();return go('/login')
@app.get('/',response_class=HTMLResponse)
def index(request:Request):
    if not request.session.get('user'):return go('/login')
    with Session() as db:
        emails=db.scalars(select(Email).order_by(Email.created_at.desc()).limit(200)).all()
        items=[{'id':e.id,'sender':e.sender,'subject':e.subject,'category':e.category,'status':e.status,'summary':e.summary,'confidence':int(e.confidence*100),'source':e.source,'has_attachments':e.has_attachments} for e in emails]
    return t.TemplateResponse(request,'index.html',context(request,items=items,graph_ready=configured(),send_enabled=os.getenv('DISABLE_SEND','true').lower()=='false'))
@app.post('/demo')
def demo(request:Request,csrf_token:str=Form(...)):
    if not permitted(request,csrf_token):raise HTTPException(403)
    examples=[('demo1','customer@marine.example','Quotation request','Please quote 20 PG-01 gauges.'),('demo2','buyer@marine.example','Purchase Order PO-2026-143','Attached PO-2026-143 for processing.'),('demo3','engineer@marine.example','Technical enquiry','Is PG-01 suitable for marine applications?'),('demo4','customer@marine.example','Delivery follow-up','Any update on our delivery status?')]
    with Session() as db:
        for uid,sender,subject,body in examples:
            if db.scalar(select(Email).where(Email.graph_id==uid)):continue
            a=classify(subject,body,uid=='demo2')
            db.add(Email(graph_id=uid,sender=sender,subject=subject,body=body,category=a['category'],summary=a['summary'],draft=a['draft'],confidence=a['confidence'],source=a['source'],has_attachments=uid=='demo2'))
        db.commit()
    return go()
@app.post('/sync')
async def sync(request:Request,csrf_token:str=Form(...)):
    if not permitted(request,csrf_token):raise HTTPException(403)
    try:messages=await get_messages()
    except Exception as err:raise HTTPException(502,f'Graph sync failed: {type(err).__name__}')
    with Session() as db:
        for m in messages:
            if db.scalar(select(Email).where(Email.graph_id==m['id'])):continue
            body=re.sub(r'<[^>]*>',' ',m.get('body',{}).get('content',''))[:30000]
            a=classify(m.get('subject',''),body,m.get('hasAttachments',False))
            db.add(Email(graph_id=m['id'],sender=m.get('from',{}).get('emailAddress',{}).get('address',''),subject=m.get('subject',''),body=body,category=a['category'],summary=a['summary'],draft=a['draft'],confidence=a['confidence'],source=a['source'],has_attachments=m.get('hasAttachments',False)))
        db.commit()
    return go()
@app.get('/emails/{id}',response_class=HTMLResponse)
def detail(request:Request,id:int):
    if not request.session.get('user'):return go('/login')
    with Session() as db:
        e=db.get(Email,id)
        if not e:raise HTTPException(404)
        item={k:getattr(e,k) for k in ('id','graph_id','sender','subject','body','category','summary','draft','confidence','status','has_attachments')}
    return t.TemplateResponse(request,'detail.html',context(request,e=item,send_enabled=os.getenv('DISABLE_SEND','true').lower()=='false'))
@app.post('/emails/{id}/approve')
def approve(request:Request,id:int,csrf_token:str=Form(...),draft:str=Form(...)):
    if not permitted(request,csrf_token):raise HTTPException(403)
    if not draft.strip() or len(draft)>5000:raise HTTPException(400)
    with Session() as db:
        e=db.get(Email,id)
        if not e or e.status not in ('pending_approval','approved'):raise HTTPException(409)
        e.draft=draft.strip();e.status='approved';e.approved_by=request.session['user']
        db.add(Audit(email_id=id,action='approved',actor=request.session['user']));db.commit()
    return go(f'/emails/{id}')
@app.post('/emails/{id}/reject')
def reject(request:Request,id:int,csrf_token:str=Form(...)):
    if not permitted(request,csrf_token):raise HTTPException(403)
    with Session() as db:
        e=db.get(Email,id)
        if not e or e.status not in ('pending_approval','approved'):raise HTTPException(409)
        e.status='rejected';db.add(Audit(email_id=id,action='rejected',actor=request.session['user']));db.commit()
    return go(f'/emails/{id}')
@app.post('/emails/{id}/send')
async def send(request:Request,id:int,csrf_token:str=Form(...)):
    if not permitted(request,csrf_token):raise HTTPException(403)
    if os.getenv('DISABLE_SEND','true').lower()!='false':raise HTTPException(403,'Sending disabled')
    with Session() as db:
        e=db.get(Email,id)
        if not e or e.status!='approved' or e.graph_id.startswith('demo'):raise HTTPException(409)
        e.status='sending';db.add(Audit(email_id=id,action='send_attempt',actor=request.session['user']));db.commit()
        message_id=e.graph_id;draft=e.draft
    try:await send_reply(message_id,draft)
    except Exception:
        with Session() as db:
            e=db.get(Email,id);e.status='send_unknown';db.add(Audit(email_id=id,action='send_result_unknown',actor=request.session['user']));db.commit()
        raise HTTPException(502,'Send outcome uncertain: verify Outlook Sent Items before retry')
    with Session() as db:
        e=db.get(Email,id);e.status='sent';e.sent_at=datetime.now(timezone.utc)
        db.add(Audit(email_id=id,action='sent',actor=request.session['user']));db.commit()
    return go(f'/emails/{id}')
