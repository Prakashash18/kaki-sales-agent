"""Company-scoped onboarding and document mapping management."""
import json
from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from .db import Session
from .identity import Account, Membership, Invitation, require_role, company_for_request, render
from .company_settings import CompanyProfile, DocumentProfile, DOC_TYPES, FIELD_LABELS, parse_document, discover_fields, allowed_mapping

router = APIRouter(prefix="/settings")

def redirect():
    return RedirectResponse("/settings", status_code=303)

@router.get("")
def index(request: Request):
    user = require_role(request, "viewer")
    with Session() as db:
        company = company_for_request(db, request)
        company_id = company.id
        profiles = db.scalars(select(DocumentProfile).where(DocumentProfile.company_id == company_id).order_by(DocumentProfile.id.desc())).all()
        memberships = db.scalars(select(Membership).where(Membership.company_id == company_id, Membership.active.is_(True))).all()
        members = [{"id": m.id, "email": db.get(Account, m.account_id).email, "role": m.role} for m in memberships]
        options = db.scalars(select(Membership).where(Membership.account_id == user.id, Membership.active.is_(True))).all()
        companies = [{"id": m.company_id, "name": db.get(CompanyProfile,m.company_id).name, "role": m.role} for m in options]
        docs = [{"id": p.id,"name": p.name, "doc_type": p.doc_type, "source_filename":p.source_filename,
                 "review_status":p.review_status, "mapping":p.field_mapping_json} for p in profiles]
        my_role = next(m.role for m in memberships if m.account_id == user.id)
    return render(request, "secure_settings.html", company=company.name, company_id=company_id,
                  companies=companies, members=members, role=my_role, docs=docs, doc_types=DOC_TYPES)

@router.post("/documents")
async def upload(request: Request, csrf_token: str = Form(...), doc_type: str = Form(...),
                 name: str = Form(""), sample: UploadFile = File(...)):
    require_role(request, "admin", csrf=csrf_token)
    if doc_type not in DOC_TYPES:
        raise HTTPException(400, "Invalid document category")
    filename = (sample.filename or "sample")[:180]
    raw = await sample.read(5 * 1024 * 1024 + 1)
    await sample.close()
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise HTTPException(413, "Document must be 1 byte to 5 MB")
    try:
        detected = discover_fields(parse_document(filename, raw))
    except Exception as exc:
        raise HTTPException(400, f"Unsupported sample: {type(exc).__name__}")
    with Session() as db:
        company = company_for_request(db, request, "admin")
        db.add(DocumentProfile(company_id=company.id, doc_type=doc_type,
                               name=(name.strip() or DOC_TYPES[doc_type])[:180],
                               source_filename=filename, field_mapping_json=json.dumps(detected),
                               detected_labels_json=json.dumps(detected), review_status="draft"))
        db.commit()
    return redirect()

@router.post("/documents/{document_id}/approve")
def approve(request: Request, document_id: int, csrf_token: str = Form(...), mapping_json: str = Form(...)):
    require_role(request, "admin", csrf=csrf_token)
    try:
        mapping = allowed_mapping(json.loads(mapping_json))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc))
    if not mapping: raise HTTPException(400, "At least one field is needed")
    with Session() as db:
        company = company_for_request(db, request, "admin")
        doc = db.get(DocumentProfile, document_id)
        if not doc or doc.company_id != company.id: raise HTTPException(404)
        doc.field_mapping_json = json.dumps(mapping)
        doc.review_status = "approved"
        db.commit()
    return redirect()

@router.post("/documents/{document_id}/disable")
def disable(request: Request, document_id: int, csrf_token: str = Form(...)):
    require_role(request, "admin", csrf=csrf_token)
    with Session() as db:
        company = company_for_request(db, request, "admin")
        doc = db.get(DocumentProfile, document_id)
        if not doc or doc.company_id != company.id: raise HTTPException(404)
        doc.review_status = "disabled"
        db.commit()
    return redirect()
