import os
os.environ["ADMIN_USER"]="admin"
os.environ["ADMIN_PASSWORD"]="test-password"
os.environ["SESSION_SECRET"]="test-only-session-key"
os.environ["DISABLE_SEND"]="true"
from fastapi.testclient import TestClient
from app.main import app
from app.db import Session
from app.operations import Customer,Product,Quote,PurchaseOrder
from sqlalchemy import select
from uuid import uuid4

def test_full_sales_flow():
    c=TestClient(app)
    assert c.post("/login",data={"username":"admin","password":"test-password"},follow_redirects=False).status_code==303
    page=c.get("/ops")
    assert page.status_code==200
    from bs4 import BeautifulSoup
    csrf=BeautifulSoup(page.text,"html.parser").find("input",attrs={"name":"csrf_token"})["value"]
    suffix=uuid4().hex[:8]
    cust="Test customer "+suffix
    sku="SKU-"+suffix
    assert c.post("/ops/customers",data={"csrf_token":csrf,"name":cust,"email":"contact@example.com"},follow_redirects=False).status_code==303
    assert c.post("/ops/products",data={"csrf_token":csrf,"sku":sku,"description":"Pressure gauge","unit_price":"10.50","available_quantity":"20"},follow_redirects=False).status_code==303
    with Session() as db:
        customer=db.scalar(select(Customer).where(Customer.name==cust))
        product=db.scalar(select(Product).where(Product.sku==sku))
        assert customer and product
        cid,pid=customer.id,product.id
    assert c.post("/ops/quotes",data={"csrf_token":csrf,"customer_id":cid,"tax_rate":"9"},follow_redirects=False).status_code==303
    with Session() as db:
        q=db.scalar(select(Quote).where(Quote.customer_id==cid))
        qid=q.id
    assert c.post(f"/ops/quotes/{qid}/lines",data={"csrf_token":csrf,"product_id":pid,"quantity":"2"},follow_redirects=False).status_code==303
    page=c.get(f"/ops/quotes/{qid}")
    assert page.status_code==200
    assert "22.89" in page.text
    pdf=c.get(f"/ops/quotes/{qid}/pdf")
    assert pdf.status_code==200 and pdf.content[:4]==b"%PDF"
    assert c.post(f"/ops/quotes/{qid}/approve",data={"csrf_token":csrf},follow_redirects=False).status_code==303
    badpo="BAD-"+suffix
    assert c.post("/ops/purchase-orders",data={"csrf_token":csrf,"po_number":badpo,"customer_id":cid,"quote_id":qid,"customer_total":"1.00"},follow_redirects=False).status_code==303
    with Session() as db:
        po=db.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number==badpo))
        assert "Total mismatch" in po.discrepancy
        bad_id=po.id
    assert c.post(f"/ops/purchase-orders/{bad_id}/approve",data={"csrf_token":csrf},follow_redirects=False).status_code==409
    goodpo="GOOD-"+suffix
    assert c.post("/ops/purchase-orders",data={"csrf_token":csrf,"po_number":goodpo,"customer_id":cid,"quote_id":qid,"customer_total":"22.89"},follow_redirects=False).status_code==303
    with Session() as db:
        po=db.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number==goodpo))
        assert po.discrepancy==""
        good_id=po.id
    assert c.post(f"/ops/purchase-orders/{good_id}/approve",data={"csrf_token":csrf},follow_redirects=False).status_code==303
    assert c.post("/ops/products",data={"csrf_token":"invalid","sku":"NOPE","description":"x","unit_price":"1"},follow_redirects=False).status_code==403
