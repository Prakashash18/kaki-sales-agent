import os
"""Per-company configuration and document-format training.

Uploads are processed in memory. Original documents and extracted values are never
saved. This is single-administrator onboarding, NOT secure multi-tenant isolation.
"""
import io
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import RedirectResponse
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, Session, engine, Audit

router = APIRouter(prefix="/settings", tags=["Company onboarding"])
DOC_TYPES = {
    "customer_rfq": "Customer RFQ",
    "supplier_quote": "Supplier quotation",
    "customer_po": "Customer purchase order",
    "sales_quote": "Sales quotation template",
    "enquiry": "Enquiry / correspondence",
}
FIELD_LABELS = {
    "document_number": ["quotation no", "quotation number", "quote no", "quote number", "rfq no", "rfq number", "po no", "po number", "purchase order no", "reference no", "reference number"],
    "document_date": ["quotation date", "quote date", "po date", "order date", "date"],
    "customer": ["customer", "bill to", "buyer", "sold to", "company name"],
    "supplier": ["supplier", "vendor", "from"],
    "part_number": ["part no", "part number", "item code", "product code", "model", "sku"],
    "description": ["description", "item description", "product description"],
    "quantity": ["quantity", "qty", "ordered qty"],
    "unit_price": ["unit price", "price per unit", "unit cost"],
    "amount": ["line total", "amount", "extended price"],
    "total": ["grand total", "total amount", "net total", "total"],
    "currency": ["currency", "curr"],
    "payment_terms": ["payment terms", "terms of payment"],
    "delivery_terms": ["delivery terms", "lead time", "delivery date"],
}
def now():
    return datetime.now(timezone.utc)

class CompanyProfile(Base):
    __tablename__ = "company_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    industry: Mapped[str] = mapped_column(String(180), default="")
    reply_tone: Mapped[str] = mapped_column(String(30), default="professional")
    default_currency: Mapped[str] = mapped_column(String(3), default="SGD")
    auto_send: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class DocumentProfile(Base):
    __tablename__ = "document_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company_profiles.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(180))
    source_filename: Mapped[str] = mapped_column(String(180), default="")
    field_mapping_json: Mapped[str] = mapped_column(Text, default="{}")
    detected_labels_json: Mapped[str] = mapped_column(Text, default="{}")
    review_status: Mapped[str] = mapped_column(String(24), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

if os.getenv('KAKI_AUTO_CREATE_SCHEMA', 'true' if os.getenv('DATABASE_URL','sqlite:').startswith('sqlite') else 'false').lower() == 'true':
    Base.metadata.create_all(engine)

def check(request, csrf):
    import secrets
    if not request.session.get("user") or not csrf or not secrets.compare_digest(csrf, request.session.get("csrf", "")):
        raise HTTPException(403, "Authentication or CSRF validation failed")

def auth(request):
    if not request.session.get("user"):
        raise HTTPException(401, "Please sign in")

def redirect(path="/settings"):
    return RedirectResponse(path, status_code=303)

def audit(db, actor, action, identifier):
    db.add(Audit(email_id=0, actor=actor, action=(action + ":" + str(identifier))[:80]))

def allowed_mapping(value):
    if not isinstance(value, dict) or len(value) > 40:
        raise ValueError("A maximum of 40 field mappings is supported")
    clean = {}
    for field, labels in value.items():
        if field not in FIELD_LABELS:
            raise ValueError("Unrecognised target field: " + str(field))
        if not isinstance(labels, list) or len(labels) > 20:
            raise ValueError("Each field must contain a list of up to 20 aliases")
        vals = [str(label).strip()[:100] for label in labels]
        clean[field] = list(dict.fromkeys(s for s in vals if s))
    return clean

def parse_document(filename, raw):
    """Extract text without persisting original documents or sample values."""
    filename = filename.lower()
    if filename.endswith(".pdf"):
        from pypdf import PdfReader
        pdf = PdfReader(io.BytesIO(raw))
        if pdf.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported")
        text = "\n".join(page.extract_text() or "" for page in pdf.pages[:12])
    elif filename.endswith(".docx"):
        from docx import Document
        d = Document(io.BytesIO(raw))
        parts = [p.text for p in d.paragraphs]
        for table in d.tables[:10]:
            for row in table.rows[:100]:
                parts.append(" | ".join(c.text for c in row.cells[:20]))
        text = "\n".join(parts)
    elif filename.endswith(".xlsx"):
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        try:
            parts = []
            for sheet in book.worksheets[:3]:
                for row in sheet.iter_rows(max_row=100, max_col=25, values_only=True):
                    parts.append(" | ".join(str(v)[:120] if v is not None else "" for v in row))
            text = "\n".join(parts)
        finally:
            book.close()
    elif filename.endswith((".csv", ".txt")):
        text = raw.decode("utf-8-sig", "replace")
    else:
        raise ValueError("Supported files: PDF, DOCX, XLSX, CSV and TXT")
    if not text.strip():
        raise ValueError("No selectable text found. Scanned PDFs need OCR, which is not yet enabled.")
    return text[:50000]

def discover_fields(text):
    """Detect *label names* only, never store the corresponding customer values."""
    found = {}
    lower = text.lower()
    for field, aliases in FIELD_LABELS.items():
        labels = [alias for alias in aliases if re.search(r"(?<![\w])" + re.escape(alias) + r"(?![\w])", lower)]
        if labels:
            found[field] = labels
    return found

def active_company_context():
    with Session() as db:
        company = db.scalar(select(CompanyProfile).where(CompanyProfile.active.is_(True)).order_by(CompanyProfile.id))
        if not company:
            return {}
        templates = db.scalars(select(DocumentProfile).where(
            DocumentProfile.company_id == company.id, DocumentProfile.review_status == "approved"
        )).all()
        return {
            "company": company.name,
            "industry": company.industry,
            "reply_tone": company.reply_tone,
            "policy_notes": company.policy_notes[:1800],
            "currency": company.default_currency,
            "approved_document_types": sorted(set(t.doc_type for t in templates)),
        }

@router.get("")
def settings(request: Request):
    auth(request)
    with Session() as db:
        companies = db.scalars(select(CompanyProfile).order_by(CompanyProfile.id)).all()
        docs = db.scalars(select(DocumentProfile).order_by(DocumentProfile.id.desc())).all()
        items = [{"id": d.id, "company_id": d.company_id, "doc_type": d.doc_type, "name": d.name,
                  "source_filename": d.source_filename, "status": d.review_status,
                  "mapping": json.loads(d.field_mapping_json)} for d in docs]
        profiles = [{"id": c.id, "name": c.name, "industry": c.industry, "active": c.active,
                     "reply_tone": c.reply_tone, "policy_notes": c.policy_notes,
                     "default_currency": c.default_currency} for c in companies]
    return request.app.state.templates.TemplateResponse(request, "settings.html", {
        "request": request, "user": request.session["user"], "csrf": request.session["csrf"],
        "companies": profiles, "docs": items, "doc_types": DOC_TYPES, "fields": FIELD_LABELS
    })

@router.post("/companies")
def create_company(request: Request, csrf_token: str = Form(...), name: str = Form(...),
                   industry: str = Form(""), reply_tone: str = Form("professional"),
                   default_currency: str = Form("SGD"), policy_notes: str = Form("")):
    check(request, csrf_token)
    name = name.strip()
    if not name or len(name) > 200 or reply_tone not in {"professional", "concise", "friendly"}:
        raise HTTPException(400, "Invalid company details")
    if not re.fullmatch(r"[A-Z]{3}", default_currency.strip().upper()):
        raise HTTPException(400, "Currency must be a three-letter code")
    with Session() as db:
        if db.scalar(select(CompanyProfile).where(CompanyProfile.name == name)):
            raise HTTPException(409, "Company already exists")
        is_first = db.scalar(select(CompanyProfile.id)) is None
        obj = CompanyProfile(name=name, industry=industry[:180], reply_tone=reply_tone,
            default_currency=default_currency.strip().upper(), policy_notes=policy_notes[:3000],
            active=is_first)
        db.add(obj); db.flush()
        audit(db, request.session["user"], "company_created", obj.id)
        db.commit()
    return redirect()

@router.post("/companies/{company_id}/activate")
def activate(request: Request, company_id: int, csrf_token: str = Form(...)):
    check(request, csrf_token)
    with Session() as db:
        target = db.get(CompanyProfile, company_id)
        if not target: raise HTTPException(404)
        for c in db.scalars(select(CompanyProfile)).all():
            c.active = c.id == company_id
        audit(db, request.session["user"], "company_activated", company_id)
        db.commit()
    return redirect()

@router.post("/companies/{company_id}/documents")
async def upload(request: Request, company_id: int, csrf_token: str = Form(...),
                 doc_type: str = Form(...), name: str = Form(""),
                 sample: UploadFile = File(...)):
    check(request, csrf_token)
    if doc_type not in DOC_TYPES: raise HTTPException(400, "Invalid document category")
    filename = (sample.filename or "sample")[:180]
    content_type = (sample.content_type or "").lower()
    size_limit = 5 * 1024 * 1024
    raw = await sample.read(size_limit + 1)
    await sample.close()
    if len(raw) > size_limit: raise HTTPException(413, "Sample exceeds the 5 MB limit")
    if not raw: raise HTTPException(400, "Sample is empty")
    try:
        text = parse_document(filename, raw)
        detected = discover_fields(text)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    # Deliberately never store raw bytes, extracted values, or full source text.
    with Session() as db:
        if not db.get(CompanyProfile, company_id): raise HTTPException(404)
        obj = DocumentProfile(company_id=company_id, doc_type=doc_type,
            name=(name.strip() or DOC_TYPES[doc_type])[:180], source_filename=filename,
            field_mapping_json=json.dumps(detected), detected_labels_json=json.dumps(detected),
            review_status="draft")
        db.add(obj); db.flush()
        audit(db, request.session["user"], "sample_analyzed", obj.id)
        db.commit()
    return redirect()

@router.post("/document-profiles/{profile_id}/approve")
def approve(request: Request, profile_id: int, csrf_token: str = Form(...),
            mapping_json: str = Form(...)):
    check(request, csrf_token)
    try:
        mapping = allowed_mapping(json.loads(mapping_json))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"Invalid mapping: {exc}")
    if not mapping: raise HTTPException(400, "At least one mapping is required")
    with Session() as db:
        item = db.get(DocumentProfile, profile_id)
        if not item: raise HTTPException(404)
        item.field_mapping_json = json.dumps(mapping, ensure_ascii=True)
        item.review_status = "approved"; item.reviewed_at = now()
        audit(db, request.session["user"], "schema_approved", item.id)
        db.commit()
    return redirect()

@router.post("/document-profiles/{profile_id}/disable")
def disable(request: Request, profile_id: int, csrf_token: str = Form(...)):
    check(request, csrf_token)
    with Session() as db:
        item = db.get(DocumentProfile, profile_id)
        if not item: raise HTTPException(404)
        item.review_status = "disabled"
        audit(db, request.session["user"], "schema_disabled", item.id)
        db.commit()
    return redirect()
