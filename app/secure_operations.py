"""Role-protected sales operations for single-company Kaki.

The existing customer/product/quote/PO models are reused, but legacy
session-only authorization is replaced with membership-based checks.
"""
from datetime import datetime,timezone
from decimal import Decimal,ROUND_HALF_UP
from fastapi import APIRouter,Request,Form,HTTPException
from fastapi.responses import RedirectResponse,Response
from sqlalchemy import select
from .db import Session,Audit
from .operations import Customer,Product,Quote,QuoteLine,PurchaseOrder,now,money,qty
from .identity import require_role

router=APIRouter(prefix="/ops")

def check(request,csrf):
    require_role(request,"staff",csrf=csrf)

def auth(request):
    require_role(request,"viewer")

def back(url="/ops"):return RedirectResponse(url,status_code=303)
def audit(db,actor,action,target):
    db.add(Audit(email_id=0,actor=actor,action=f"{action}:{target}"[:80]))
def calc_quote(q):
    subtotal=sum((line.quantity*line.unit_price for line in q.lines),Decimal("0"))
    tax=(subtotal*q.tax_rate/Decimal("100")).quantize(Decimal(".01"),rounding=ROUND_HALF_UP)
    return subtotal,tax,subtotal+tax
@router.get("")
def dashboard(request:Request):
    auth(request)
    with Session() as db:
        customers=db.scalars(select(Customer).order_by(Customer.name)).all()
        products=db.scalars(select(Product).order_by(Product.sku)).all()
        quotes=db.scalars(select(Quote).order_by(Quote.id.desc())).all()
        pos=db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.id.desc())).all()
        return request.app.state.templates.TemplateResponse(request,"ops.html",{"request":request,"user":request.session["user"],"csrf":request.session["csrf"],"customers":customers,"products":products,"quotes":quotes,"pos":pos})
@router.post("/customers")
def add_customer(request:Request,csrf_token:str=Form(...),name:str=Form(...),email:str=Form(""),phone:str=Form(""),address:str=Form(""),payment_terms:str=Form("Subject to agreement")):
    require_role(request,"manager",csrf=csrf_token)
    if not name.strip() or len(name)>200 or len(email)>250:raise HTTPException(400)
    with Session() as db:
        if db.scalar(select(Customer).where(Customer.name==name.strip())):raise HTTPException(409,"Customer exists")
        db.add(Customer(name=name.strip(),email=email.strip(),phone=phone.strip()[:60],address=address[:3000],payment_terms=payment_terms[:100]))
        db.flush();audit(db,request.session["user"],"customer_created",name.strip());db.commit()
    return back()
@router.post("/products")
def add_product(request:Request,csrf_token:str=Form(...),sku:str=Form(...),description:str=Form(...),unit_price:str=Form(...),available_quantity:str=Form("0")):
    require_role(request,"manager",csrf=csrf_token)
    if not sku.strip() or len(sku)>100 or not description.strip() or len(description)>500:raise HTTPException(400)
    with Session() as db:
        if db.scalar(select(Product).where(Product.sku==sku.strip())):raise HTTPException(409,"SKU exists")
        db.add(Product(sku=sku.strip(),description=description.strip(),unit_price=money(unit_price),available_quantity=money(available_quantity)))
        db.flush();audit(db,request.session["user"],"product_created",sku.strip());db.commit()
    return back()
@router.post("/quotes")
def add_quote(request:Request,csrf_token:str=Form(...),customer_id:int=Form(...),tax_rate:str=Form("0"),notes:str=Form("")):
    check(request,csrf_token)
    rate=money(tax_rate)
    if rate>100:raise HTTPException(400,"Tax rate must be 0–100")
    with Session() as db:
        if not db.get(Customer,customer_id):raise HTTPException(404,"Customer not found")
        q=Quote(customer_id=customer_id,quote_number="TEMP",tax_rate=rate,notes=notes[:3000])
        db.add(q);db.flush();q.quote_number=f"KAKI-Q-{q.id:06d}"
        audit(db,request.session["user"],"quote_created",q.quote_number);db.commit()
        qid=q.id
    return back(f"/ops/quotes/{qid}")
@router.get("/quotes/{qid}")
def view_quote(request:Request,qid:int):
    auth(request)
    with Session() as db:
        q=db.get(Quote,qid)
        if not q:raise HTTPException(404)
        products=db.scalars(select(Product).order_by(Product.sku)).all()
        subtotal,tax,total=calc_quote(q)
        return request.app.state.templates.TemplateResponse(request,"quote.html",{"request":request,"user":request.session["user"],"csrf":request.session["csrf"],"q":q,"products":products,"subtotal":subtotal,"tax":tax,"total":total})
@router.post("/quotes/{qid}/lines")
def quote_line(request:Request,qid:int,csrf_token:str=Form(...),product_id:int=Form(...),quantity:str=Form(...)):
    check(request,csrf_token);quantity=qty(quantity)
    with Session() as db:
        q=db.get(Quote,qid);p=db.get(Product,product_id)
        if not q or not p:raise HTTPException(404)
        if q.status!="draft":raise HTTPException(409,"Only draft quotations can change")
        if p.currency!=q.currency:raise HTTPException(400,"Currency mismatch")
        db.add(QuoteLine(quote_id=q.id,product_id=p.id,description=p.description,unit_price=p.unit_price,quantity=quantity))
        audit(db,request.session["user"],"line_added",q.quote_number);db.commit()
    return back(f"/ops/quotes/{qid}")
@router.post("/quotes/{qid}/approve")
def approve_quote(request:Request,qid:int,csrf_token:str=Form(...)):
    require_role(request,"manager",csrf=csrf_token)
    with Session() as db:
        q=db.get(Quote,qid)
        if not q:raise HTTPException(404)
        if q.status!="draft" or not q.lines:raise HTTPException(409,"Cannot approve empty/non-draft quotation")
        q.status="approved";q.approved_at=now()
        audit(db,request.session["user"],"quote_approved",q.quote_number);db.commit()
    return back(f"/ops/quotes/{qid}")
@router.get("/quotes/{qid}/pdf")
def quote_pdf(request:Request,qid:int):
    auth(request)
    from io import BytesIO
    from reportlab.pdfgen.canvas import Canvas
    with Session() as db:
        q=db.get(Quote,qid)
        if not q:raise HTTPException(404)
        subtotal,tax,total=calc_quote(q)
        buf=BytesIO();c=Canvas(buf,pagesize=(595,842))
        def safe(s,n=100):return str(s).replace("\n"," ")[:n].encode("latin-1","replace").decode("latin-1")
        c.setFont("Helvetica-Bold",18);c.drawString(45,795,"Kaki - Draft Sales Quotation" if q.status=="draft" else "Sales Quotation")
        c.setFont("Helvetica",10)
        for y,v in [(760,f"Quote: {q.quote_number}"),(742,f"Customer: {q.customer.name}"),(724,f"Currency: {q.currency}"),(706,f"Status: {q.status}")]:c.drawString(45,y,safe(v))
        c.line(45,688,550,688);y=666
        c.drawString(45,y,"SKU");c.drawString(150,y,"Description");c.drawString(415,y,"Qty");c.drawString(470,y,"Amount");y-=22
        for line in q.lines:
            if y<100:c.showPage();y=790;c.setFont("Helvetica",10)
            c.drawString(45,y,safe(line.product.sku,15));c.drawString(150,y,safe(line.description,42));c.drawRightString(450,y,str(line.quantity));c.drawRightString(550,y,f"{line.quantity*line.unit_price:.2f}");y-=21
        y-=10
        for value in (f"Subtotal: {subtotal:.2f}",f"Tax ({q.tax_rate}%): {tax:.2f}",f"Total: {total:.2f}"):
            if y<50:c.showPage();y=790;c.setFont("Helvetica",10)
            c.drawRightString(550,y,value);y-=20
        c.setFont("Helvetica",8);c.drawString(45,max(35,y-25),"Internal draft unless independently authorised and issued by sales.")
        c.save()
        return Response(buf.getvalue(),media_type="application/pdf",headers={"Content-Disposition":f'attachment; filename="{q.quote_number}.pdf"'})
@router.post("/purchase-orders")
def add_po(request:Request,csrf_token:str=Form(...),po_number:str=Form(...),customer_id:int=Form(...),quote_id:int=Form(...),customer_total:str=Form(...),notes:str=Form("")):
    check(request,csrf_token)
    if not po_number.strip() or len(po_number)>100:raise HTTPException(400)
    with Session() as db:
        if db.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number==po_number.strip())):raise HTTPException(409,"Duplicate PO")
        c=db.get(Customer,customer_id);q=db.get(Quote,quote_id)
        if not c or not q:raise HTTPException(404)
        discrepancies=[]
        if q.customer_id!=customer_id:discrepancies.append("Customer differs from quotation")
        if q.status!="approved":discrepancies.append("Quotation not approved")
        expected=calc_quote(q)[2];received=money(customer_total)
        if expected!=received:discrepancies.append(f"Total mismatch: quote {expected:.2f}; PO {received:.2f}")
        for line in q.lines:
            if line.quantity>line.product.available_quantity:discrepancies.append(f"Insufficient stock for {line.product.sku}")
        po=PurchaseOrder(po_number=po_number.strip(),customer_id=customer_id,quote_id=quote_id,customer_total=received,discrepancy="; ".join(discrepancies),notes=notes[:3000])
        db.add(po);audit(db,request.session["user"],"po_registered",po_number.strip());db.commit()
    return back()
@router.post("/purchase-orders/{poid}/approve")
def approve_po(request:Request,poid:int,csrf_token:str=Form(...)):
    require_role(request,"manager",csrf=csrf_token)
    with Session() as db:
        po=db.get(PurchaseOrder,poid)
        if not po:raise HTTPException(404)
        if po.status!="pending_review" or po.discrepancy:raise HTTPException(409,"PO has discrepancies or invalid status")
        po.status="approved";po.approved_at=now()
        audit(db,request.session["user"],"po_approved",po.po_number);db.commit()
    return back()
