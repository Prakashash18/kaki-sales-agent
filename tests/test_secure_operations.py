"""Role-protected sales operation acceptance tests."""
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.secure_main import app
from app.db import Base,engine,Session
from app.identity import Account,Membership,password_hash
from app.company_settings import CompanyProfile
from app.operations import Customer,Product,Quote,PurchaseOrder

Base.metadata.create_all(engine)
PASSWORD="test-long-employee-pass-123456"

def staff_client(cid,role):
    suffix=uuid4().hex[:8]
    email="sales-"+suffix+"@example.test"
    with Session() as db:
        u=Account(email=email,password_hash=password_hash(PASSWORD))
        db.add(u);db.flush()
        db.add(Membership(account_id=u.id,company_id=cid,role=role));db.commit()
    c=TestClient(app)
    assert c.post("/login",data={"email":email,"password":PASSWORD},follow_redirects=False).status_code==303
    from bs4 import BeautifulSoup
    page=c.get("/ops")
    assert page.status_code==200
    csrf=BeautifulSoup(page.text,"html.parser").find("input",{"name":"csrf_token"})["value"]
    return c,csrf

def test_sales_quote_to_po_approval_with_role_checks():
    suffix=uuid4().hex[:10]
    with Session() as db:
        company=CompanyProfile(name="Sales Accept "+suffix,active=False)
        db.add(company);db.flush();cid=company.id;db.commit()
    manager,mtoken=staff_client(cid,"manager")
    staff,stoken=staff_client(cid,"staff")
    sku="SKU-"+suffix;customer_name="Customer "+suffix
    assert staff.post("/ops/products",data={"csrf_token":stoken,"sku":sku,
        "description":"Test gauge","unit_price":"100","available_quantity":"10"},
        follow_redirects=False).status_code==403
    assert manager.post("/ops/products",data={"csrf_token":mtoken,"sku":sku,
        "description":"Test gauge","unit_price":"100","available_quantity":"10"},
        follow_redirects=False).status_code==303
    assert manager.post("/ops/customers",data={"csrf_token":mtoken,"name":customer_name,
        "email":"buyer@example.test"},follow_redirects=False).status_code==303
    with Session() as db:
        cust=db.scalar(select(Customer).where(Customer.name==customer_name))
        prod=db.scalar(select(Product).where(Product.sku==sku))
        cid_customer,pid=cust.id,prod.id
    quote=staff.post("/ops/quotes",data={"csrf_token":stoken,"customer_id":cid_customer,
        "tax_rate":"0"},follow_redirects=False)
    assert quote.status_code==303
    qid=int(quote.headers["location"].split("/")[-1])
    assert staff.post(f"/ops/quotes/{qid}/lines",data={"csrf_token":stoken,
        "product_id":pid,"quantity":"2"},follow_redirects=False).status_code==303
    assert staff.post(f"/ops/quotes/{qid}/approve",
        data={"csrf_token":stoken},follow_redirects=False).status_code==403
    assert manager.post(f"/ops/quotes/{qid}/approve",
        data={"csrf_token":mtoken},follow_redirects=False).status_code==303
    pdf=staff.get(f"/ops/quotes/{qid}/pdf")
    assert pdf.status_code==200 and pdf.content.startswith(b"%PDF")
    po="PO-"+suffix
    result=staff.post("/ops/purchase-orders",data={"csrf_token":stoken,
        "po_number":po,"customer_id":cid_customer,"quote_id":qid,
        "customer_total":"200.00"},follow_redirects=False)
    assert result.status_code==303,result.text
    with Session() as db:
        order=db.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number==po))
        oid=order.id
        assert order.discrepancy==""
    assert staff.post(f"/ops/purchase-orders/{oid}/approve",data={"csrf_token":stoken},
        follow_redirects=False).status_code==403
    assert manager.post(f"/ops/purchase-orders/{oid}/approve",data={"csrf_token":mtoken},
        follow_redirects=False).status_code==303
    with Session() as db:
        assert db.get(Quote,qid).status=="approved"
        assert db.get(PurchaseOrder,oid).status=="approved"

def test_unauthed_sales_dashboard_forbidden():
    assert TestClient(app).get("/ops").status_code==401
