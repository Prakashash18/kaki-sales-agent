"""SharePoint Excel Data Hub: read-only Graph import with explicit source approval.

No arbitrary URLs are fetched. Drive and item identifiers are restricted to
Graph paths, and only xlsx files under a selected Microsoft drive are imported.
Original files remain authoritative and are never modified.
"""
import io
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from openpyxl import load_workbook
from sqlalchemy import ForeignKey, Integer, String, Text, DateTime, UniqueConstraint, select, delete, func
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, Session
from .identity import require_role, company_for_request
from .email_connectors import MailConnection
from .secure_email_api import access_token

router = APIRouter(prefix="/data-hub")
KIND = ("inventory","pricing","customers","jobs")
FIELDS = {
    "inventory": ("sku","description","quantity","location","uom"),
    "pricing": ("sku","customer","supplier","currency","unit_price","valid_from","valid_to"),
    "customers": ("customer_id","name","email","phone","contact"),
    "jobs": ("job_id","customer","po_number","description","status","assigned_to","due_date","completed_date","invoice_status","invoice_number"),
}
ALIASES = {
    "sku":("sku","part no","part number","stock code","item code","model"),
    "quantity":("qty","quantity","qty available","stock","on hand"),
    "description":("description","item description","product","product name","job description"),
    "unit_price":("selling price","unit price","price","price per unit","list price"),
    "customer_id":("customer code","customer id","client id"),
    "name":("customer","customer name","company","company name","client"),
    "job_id":("job no","job number","job id","job ref","reference"),
    "status":("status","job status","progress"),
    "completed_date":("completion date","date completed","completed on","completed date"),
    "due_date":("due date","target date","expected completion"),
    "po_number":("po number","po no","purchase order"),
    "email":("email","email address"),
    "currency":("currency","ccy"),
    "invoice_status":("invoice status","invoiced","invoice state"),
    "invoice_number":("invoice no","invoice number"),
    "supplier":("supplier","vendor"),
    "location":("location","warehouse"),
}
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 25000
IDENT = re.compile(r"^[A-Za-z0-9_!{}.,@()=-]{1,256}$")

def now():return datetime.now(timezone.utc)
def safe_id(value):
    if not isinstance(value,str) or not IDENT.fullmatch(value):
        raise HTTPException(400,"Invalid Microsoft Graph item identifier")
    return value

class DataSource(Base):
    __tablename__="data_hub_sources"
    __table_args__=(UniqueConstraint("company_id","drive_id","item_id","sheet_name","kind"),)
    id:Mapped[int]=mapped_column(primary_key=True)
    company_id:Mapped[int]=mapped_column(ForeignKey("company_profiles.id"),index=True)
    connection_id:Mapped[int]=mapped_column(ForeignKey("mail_connections.id"))
    drive_id:Mapped[str]=mapped_column(String(256))
    item_id:Mapped[str]=mapped_column(String(256))
    file_name:Mapped[str]=mapped_column(String(260))
    sheet_name:Mapped[str]=mapped_column(String(120))
    kind:Mapped[str]=mapped_column(String(30))
    mapping_json:Mapped[str]=mapped_column(Text,default="{}")
    status:Mapped[str]=mapped_column(String(30),default="active")
    etag:Mapped[str]=mapped_column(String(512),default="")
    last_synced_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    row_count:Mapped[int]=mapped_column(Integer,default=0)

class DataRecord(Base):
    __tablename__="data_hub_records"
    __table_args__=(UniqueConstraint("source_id","row_number"),)
    id:Mapped[int]=mapped_column(primary_key=True)
    company_id:Mapped[int]=mapped_column(ForeignKey("company_profiles.id"),index=True)
    source_id:Mapped[int]=mapped_column(ForeignKey("data_hub_sources.id"),index=True)
    row_number:Mapped[int]=mapped_column(Integer)
    payload_json:Mapped[str]=mapped_column(Text)

def classify_headers(headers,kind):
    allowed=set(FIELDS[kind])
    mapping={}
    for i,header in enumerate(headers):
        h=str(header or "").lower().strip()
        if not h:continue
        exact=next((f for f in allowed if f==h),None)
        selected=exact or next((f for f in FIELDS[kind] if h in ALIASES.get(f,())),None)
        if selected and selected not in mapping:
            mapping[selected]=str(header)
    return mapping

def read_excel(raw):
    if len(raw)>MAX_BYTES:raise HTTPException(413,"Workbook exceeds the 8 MB import limit")
    try:
        book=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
        result={}
        for sheet in book.worksheets[:20]:
            if sheet.sheet_state!="visible":continue
            rows=sheet.iter_rows(max_row=MAX_ROWS+2,max_col=80,values_only=True)
            first=next(rows,None)
            if first is None:continue
            headers=[str(v).strip() if v is not None else "" for v in first]
            if not any(headers):continue
            preview=[]
            for row in rows:
                if not any(v is not None and str(v).strip() for v in row):continue
                item={headers[i]:format_excel(v) for i,v in enumerate(row) if i<len(headers) and headers[i]}
                preview.append(item)
                if len(preview)>=MAX_ROWS:
                    # Refuse oversized rather than silently return an incomplete import.
                    if any(any(x is not None for x in tail) for tail in rows):
                        raise HTTPException(413,"Sheet exceeds the 25,000 row limit")
                    break
            result[sheet.title]={"headers":headers,"rows":preview}
        book.close()
        if not result:raise HTTPException(422,"Workbook has no readable rows")
        return result
    except HTTPException:raise
    except Exception:
        raise HTTPException(422,"Workbook could not be parsed as a supported XLSX file")

def format_excel(value):
    if value is None:return None
    if isinstance(value,datetime):return value.isoformat()
    if isinstance(value,(str,int,float,bool)):return value if not isinstance(value,str) else value[:2000]
    return str(value)[:2000]

def map_records(data,headers,kind,mapping):
    if kind not in KIND:raise HTTPException(400,"Unknown dataset category")
    if not isinstance(mapping,dict) or not mapping or any(k not in FIELDS[kind] or v not in headers for k,v in mapping.items()):
        raise HTTPException(422,"Mapping must select existing columns and supported fields")
    if len(set(mapping.values()))!=len(mapping):raise HTTPException(422,"Each source column may map to only one target field")
    converted=[]
    for rowno,item in enumerate(data,start=2):
        content={k:item.get(v) for k,v in mapping.items()}
        if not any(v is not None and v!="" for v in content.values()):continue
        converted.append((rowno,content))
    return converted

async def graph_json(token,path,params=None):
    async with httpx.AsyncClient(timeout=25) as client:
        response=await client.get("https://graph.microsoft.com/v1.0/"+path,
            headers={"Authorization":"Bearer "+token},params=params)
        if response.status_code in (401,403):
            raise HTTPException(403,"Microsoft Graph access denied. Check consent and SharePoint permissions.")
        if response.status_code==429:raise HTTPException(503,"Microsoft Graph throttled the request. Try later.")
        if response.status_code>=400:raise HTTPException(502,"Microsoft Graph request failed")
        return response.json()

async def fetch_workbook(token,drive,item):
    drive=safe_id(drive);item=safe_id(item)
    uri="https://graph.microsoft.com/v1.0/drives/"+drive+"/items/"+item+"/content"
    async with httpx.AsyncClient(timeout=35,follow_redirects=False) as client:
        result=await client.get(uri,headers={"Authorization":"Bearer "+token})
        if result.status_code in (401,403):raise HTTPException(403,"SharePoint workbook permission denied")
        if result.status_code in (301,302,303,307,308):
            from urllib.parse import urlparse
            location=result.headers.get("location","")
            parsed=urlparse(location)
            if parsed.scheme!="https" or not (
                parsed.hostname and (parsed.hostname.endswith(".sharepoint.com") or
                parsed.hostname.endswith(".sharepoint-df.com") or
                parsed.hostname.endswith(".1drv.com"))):
                raise HTTPException(502,"Unexpected SharePoint download host")
            result=await client.get(location)
        if result.status_code>=400:raise HTTPException(502,"Failed to download SharePoint workbook")
        if len(result.content)>MAX_BYTES:raise HTTPException(413,"Workbook is too large")
        return result.content

def redirect():return RedirectResponse("/data-hub",status_code=303)
def connection(db,request,connection_id=None):
    c=company_for_request(db,request)
    query=select(MailConnection).where(MailConnection.company_id==c.id,
        MailConnection.provider=="microsoft",MailConnection.status=="connected")
    if connection_id is not None:query=query.where(MailConnection.id==connection_id)
    row=db.scalar(query.order_by(MailConnection.id))
    if not row:raise HTTPException(409,"Connect a Microsoft 365 account before SharePoint import")
    return c,row


@router.get("/ask")
def ask_kaki(request:Request,question:str):
    """Deterministic, source-grounded business-data question answering."""
    require_role(request,"viewer")
    text_question=question.strip().lower()
    if not 3<=len(text_question)<=300:raise HTTPException(400,"Question must be 3–300 characters")
    if any(w in text_question for w in ("job","work order","completed","overdue")):category="jobs"
    elif any(w in text_question for w in ("stock","inventory","quantity","available")):category="inventory"
    elif any(w in text_question for w in ("price","cost","pricing")):category="pricing"
    elif any(w in text_question for w in ("customer","client","contact")):category="customers"
    else:category=None
    stop={"show","me","all","the","a","an","what","which","is","are","in","for",
          "of","at","with","my","our","their","from","how","many","list","find","check",
          "status","job","jobs","stock","inventory","customer","customers","price",
          "pricing","cost","quantity","available","completed","complete","overdue","please",
          "tell","about","and","or","current","do","we","have","on","any"}
    terms=[t for t in re.findall(r"[a-z0-9_-]+",text_question) if t not in stop and len(t)>1]
    want_completed="complete" in text_question or "finished" in text_question or "done" in text_question
    want_overdue="overdue" in text_question or "late" in text_question
    with Session() as db:
        c=company_for_request(db,request)
        q=select(DataRecord,DataSource).join(DataSource,DataRecord.source_id==DataSource.id).where(
            DataRecord.company_id==c.id,DataSource.company_id==c.id,
            DataSource.status=="active")
        if category:q=q.where(DataSource.kind==category)
        results=db.execute(q.limit(5000)).all()
        found=[]
        from datetime import date
        for record,src in results:
            value=json.loads(record.payload_json)
            if terms and not all(any(t in str(x or "").lower() for x in value.values()) for t in terms):
                continue
            if want_completed and src.kind=="jobs":
                if str(value.get("status","")).lower() not in ("completed","complete","finished","done","closed"):
                    continue
            if want_overdue and src.kind=="jobs":
                status=str(value.get("status","")).lower()
                if status in ("completed","complete","finished","done","closed"):continue
                try:due=date.fromisoformat(str(value.get("due_date",""))[:10])
                except ValueError:continue
                if due>=date.today():continue
            found.append({"kind":src.kind,"data":value,"source":src.file_name,
                          "sheet":src.sheet_name,"row":record.row_number,
                          "last_synced":str(src.last_synced_at or "")[:19]})
            if len(found)>=20:break
    summary=(f"Found {len(found)} matching records (showing up to 20)."
             if found else "No matching records in connected SharePoint snapshots. Check the source mapping, synchronisation and search terms.")
    return {"answer":summary,"results":found,"category":category or "all",
            "note":"Answers use previously imported SharePoint Excel snapshots, not live SharePoint queries."}

@router.get("")
def index(request:Request,q:str="",kind:str=""):
    require_role(request,"viewer")
    if len(q)>120:raise HTTPException(400)
    with Session() as db:
        c=company_for_request(db,request)
        sources=db.scalars(select(DataSource).where(DataSource.company_id==c.id).order_by(DataSource.id.desc())).all()
        conn=db.scalars(select(MailConnection).where(MailConnection.company_id==c.id,
                    MailConnection.provider=="microsoft",MailConnection.status=="connected")).all()
        query=select(DataRecord,DataSource).join(DataSource,DataRecord.source_id==DataSource.id).where(DataRecord.company_id==c.id)
        if kind in KIND:query=query.where(DataSource.kind==kind)
        records=db.execute(query.order_by(DataRecord.id.desc()).limit(300)).all()
        matched=[]
        for record,src in records:
            payload=json.loads(record.payload_json)
            if q and q.lower() not in json.dumps(payload,ensure_ascii=False).lower():continue
            matched.append({"id":record.id,"kind":src.kind,"file":src.file_name,"sheet":src.sheet_name,
                            "row":record.row_number,"data":payload})
        ds=[{"id":s.id,"kind":s.kind,"file":s.file_name,"sheet":s.sheet_name,
             "rows":s.row_count,"date":str(s.last_synced_at or "")[:19],
             "status":s.status} for s in sources]
        connections=[{"id":x.id,"email":x.address} for x in conn]
    return request.app.state.templates.TemplateResponse(request,"data_hub.html",
        {"request":request,"user":request.session["user"],"csrf":request.session["csrf"],
         "sources":ds,"records":matched[:100],"connections":connections,"kind":kind,"q":q})

@router.post("/discover")
async def discover(request:Request,csrf_token:str=Form(...),connection_id:int=Form(...),query:str=Form(...)):
    require_role(request,"admin",csrf=csrf_token)
    if not 2<=len(query.strip())<=80:raise HTTPException(400,"Enter 2–80 characters to search SharePoint sites")
    with Session() as db:
        c,conn=connection(db,request,connection_id);token=await access_token(conn);db.commit()
    result=await graph_json(token,"sites",{"search":query.strip()})
    sites=[{"id":x["id"],"name":x.get("displayName",x.get("name","Site")),
            "url":x.get("webUrl","")} for x in result.get("value",[])[:30]]
    return JSONResponse({"sites":sites})

@router.post("/browse")
async def browse(request:Request,csrf_token:str=Form(...),connection_id:int=Form(...),
                 site_id:str=Form(""),drive_id:str=Form(""),folder_id:str=Form("")):
    require_role(request,"admin",csrf=csrf_token)
    with Session() as db:
        c,conn=connection(db,request,connection_id);token=await access_token(conn);db.commit()
    if not drive_id:
        data=await graph_json(token,"sites/"+safe_id(site_id)+"/drives")
        return JSONResponse({"drives":[{"id":d["id"],"name":d.get("name","Documents")} for d in data.get("value",[])[:50]]})
    path="drives/"+safe_id(drive_id)+("/items/"+safe_id(folder_id)+"/children" if folder_id else "/root/children")
    data=await graph_json(token,path,{"$top":200})
    files=[{"id":x["id"],"name":x.get("name",""),"folder":bool(x.get("folder")),
            "modified":x.get("lastModifiedDateTime",""),"url":x.get("webUrl","")}
           for x in data.get("value",[]) if x.get("folder") or x.get("name","").lower().endswith(".xlsx")]
    return JSONResponse({"files":files,"more":bool(data.get("@odata.nextLink"))})

@router.post("/preview")
async def preview(request:Request,csrf_token:str=Form(...),connection_id:int=Form(...),
                  drive_id:str=Form(...),item_id:str=Form(...),kind:str=Form(...)):
    require_role(request,"admin",csrf=csrf_token)
    if kind not in KIND:raise HTTPException(400)
    with Session() as db:
        c,conn=connection(db,request,connection_id);token=await access_token(conn);db.commit()
    metadata=await graph_json(token,"drives/"+safe_id(drive_id)+"/items/"+safe_id(item_id),
                              {"$select":"id,name,eTag,webUrl"})
    if not metadata.get("name","").lower().endswith(".xlsx"):raise HTTPException(422,"Select an XLSX workbook")
    sheets=read_excel(await fetch_workbook(token,drive_id,item_id))
    return JSONResponse({"file":metadata.get("name"),"drive_id":drive_id,"item_id":item_id,
        "sheets":[{"name":name,"headers":value["headers"],
                   "suggested_mapping":classify_headers(value["headers"],kind),
                   "sample_rows":value["rows"][:5],"count":len(value["rows"])}
                  for name,value in sheets.items()]})

@router.post("/import")
async def import_source(request:Request,csrf_token:str=Form(...),connection_id:int=Form(...),
                        drive_id:str=Form(...),item_id:str=Form(...),sheet_name:str=Form(...),
                        kind:str=Form(...),mapping_json:str=Form(...)):
    require_role(request,"admin",csrf=csrf_token)
    if kind not in KIND or len(sheet_name)>120:raise HTTPException(400)
    try:mapping=json.loads(mapping_json)
    except ValueError:raise HTTPException(422,"Invalid mapping JSON")
    with Session() as db:
        c,conn=connection(db,request,connection_id);cid=c.id
        bearer=await access_token(conn);db.commit()
    meta=await graph_json(bearer,"drives/"+safe_id(drive_id)+"/items/"+safe_id(item_id),
                          {"$select":"id,name,eTag"})
    if not meta.get("name","").lower().endswith(".xlsx"):raise HTTPException(422,"Select an XLSX file")
    sheets=read_excel(await fetch_workbook(bearer,drive_id,item_id))
    if sheet_name not in sheets:raise HTTPException(422,"Selected sheet does not exist")
    sheet=sheets[sheet_name]
    converted=map_records(sheet["rows"],sheet["headers"],kind,mapping)
    with Session() as db:
        # Re-check membership; a user's access could be revoked during a slow Graph call.
        c=company_for_request(db,request,"admin")
        if c.id!=cid:raise HTTPException(403)
        existing=db.scalar(select(DataSource).where(DataSource.company_id==cid,
            DataSource.drive_id==drive_id,DataSource.item_id==item_id,
            DataSource.sheet_name==sheet_name,DataSource.kind==kind))
        if existing:
            src=existing;db.execute(delete(DataRecord).where(DataRecord.source_id==src.id,DataRecord.company_id==cid))
        else:
            src=DataSource(company_id=cid,connection_id=connection_id,drive_id=drive_id,
                item_id=item_id,file_name=meta["name"],sheet_name=sheet_name,kind=kind)
            db.add(src);db.flush()
        src.mapping_json=json.dumps(mapping);src.etag=meta.get("eTag","")[:512]
        src.row_count=len(converted);src.last_synced_at=now();src.status="active"
        db.add_all([DataRecord(company_id=cid,source_id=src.id,row_number=n,
                         payload_json=json.dumps(p,ensure_ascii=True)) for n,p in converted])
        db.commit()
    return redirect()

@router.post("/sources/{source_id}/sync")
async def resync(request:Request,source_id:int,csrf_token:str=Form(...)):
    require_role(request,"admin",csrf=csrf_token)
    with Session() as db:
        c=company_for_request(db,request,"admin")
        src=db.get(DataSource,source_id)
        if not src or src.company_id!=c.id:raise HTTPException(404)
        source={"drive":src.drive_id,"item":src.item_id,"sheet":src.sheet_name,
                "kind":src.kind,"mapping":json.loads(src.mapping_json),"etag":src.etag}
        c,conn=connection(db,request,src.connection_id)
        token=await access_token(conn);db.commit();cid=c.id
    meta=await graph_json(token,"drives/"+safe_id(source["drive"])+"/items/"+safe_id(source["item"]),
                          {"$select":"id,name,eTag"})
    if meta.get("eTag") and meta["eTag"]==source["etag"]:
        return redirect()
    sheets=read_excel(await fetch_workbook(token,source["drive"],source["item"]))
    if source["sheet"] not in sheets:raise HTTPException(409,"Original worksheet no longer exists")
    sheet=sheets[source["sheet"]]
    converted=map_records(sheet["rows"],sheet["headers"],source["kind"],source["mapping"])
    with Session() as db:
        c=company_for_request(db,request,"admin")
        if c.id!=cid:raise HTTPException(403)
        src=db.get(DataSource,source_id)
        if not src or src.company_id!=cid or src.etag!=source["etag"]:
            raise HTTPException(409,"Source was modified by another synchronisation")
        db.execute(delete(DataRecord).where(DataRecord.source_id==source_id,DataRecord.company_id==cid))
        db.add_all([DataRecord(company_id=cid,source_id=source_id,row_number=n,
                         payload_json=json.dumps(p,ensure_ascii=True)) for n,p in converted])
        src.etag=meta.get("eTag","")[:512];src.row_count=len(converted)
        src.last_synced_at=now();db.commit()
    return redirect()

@router.post("/sources/{source_id}/disable")
def disable(request:Request,source_id:int,csrf_token:str=Form(...)):
    require_role(request,"admin",csrf=csrf_token)
    with Session() as db:
        c=company_for_request(db,request,"admin")
        src=db.get(DataSource,source_id)
        if not src or src.company_id!=c.id:raise HTTPException(404)
        src.status="disabled";db.commit()
    return redirect()
