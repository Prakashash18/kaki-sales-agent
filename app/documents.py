import os,re,base64,io
from fastapi import APIRouter,Request,HTTPException
from sqlalchemy import select
from .db import Session,Email
from .graph import token,BASE,configured
import httpx
router=APIRouter(prefix="/documents")
def _text_from_file(name,data):
    name=name.lower()
    try:
        if name.endswith(".pdf"):
            from pypdf import PdfReader
            reader=PdfReader(io.BytesIO(data))
            if reader.is_encrypted:return "Encrypted PDF — manual review required"
            return "\n".join((p.extract_text() or "") for p in reader.pages[:15])[:18000] or "Image-only PDF; OCR not available in MVP"
        if name.endswith(".xlsx"):
            from openpyxl import load_workbook
            wb=load_workbook(io.BytesIO(data),read_only=True,data_only=True)
            rows=[]
            for ws in wb.worksheets[:3]:
                rows.append("Sheet: "+ws.title)
                for row in ws.iter_rows(max_row=100,max_col=15,values_only=True):
                    rows.append(" | ".join(str(v or "")[:100] for v in row))
            wb.close()
            return "\n".join(rows)[:18000]
        if name.endswith((".txt",".csv")):return data.decode("utf-8","replace")[:18000]
        return "Unsupported format — download and review manually"
    except Exception:return "Could not safely parse this file — manual review required"
@router.get("/emails/{id}")
async def inspect(request:Request,id:int):
    if not request.session.get("user"):raise HTTPException(401)
    if not configured():raise HTTPException(503,"Graph not configured")
    with Session() as db:
        e=db.get(Email,id)
        if not e or e.graph_id.startswith("demo"):raise HTTPException(404)
        graph_id=e.graph_id
    result=[]
    async with httpx.AsyncClient(timeout=30) as client:
        key=await token(client)
        headers={"Authorization":f"Bearer {key}"}
        res=await client.get(f"{BASE}/users/{os.environ['MS_MAILBOX']}/messages/{graph_id}/attachments",headers=headers,params={"$top":20})
        res.raise_for_status()
        for a in res.json().get("value",[])[:10]:
            name=str(a.get("name","unnamed"))[:120]
            size=int(a.get("size",0))
            if a.get("@odata.type")!="#microsoft.graph.fileAttachment" or size>8*1024*1024:
                result.append({"name":name,"text":"Attachment not supported or exceeds 8 MB","size":size})
                continue
            content=a.get("contentBytes")
            if not content:
                detail=await client.get(f"{BASE}/users/{os.environ['MS_MAILBOX']}/messages/{graph_id}/attachments/{a['id']}",headers=headers)
                detail.raise_for_status();content=detail.json().get("contentBytes")
            if not content:result.append({"name":name,"size":size,"text":"Attachment has no downloadable content"});continue
            raw=base64.b64decode(content,validate=True)
            if len(raw)>8*1024*1024:raise HTTPException(413,"Attachment too large")
            result.append({"name":name,"size":size,"text":_text_from_file(name,raw)})
    return request.app.state.templates.TemplateResponse(request,"documents.html",{"request":request,"user":request.session["user"],"csrf":request.session["csrf"],"email_id":id,"items":result})
