from app.agent import fallback

def test_rfq(): assert fallback('Quote request','Please quote 2 units')['category']=='rfq'
def test_po(): assert fallback('Purchase Order PO-123','Attached')['category']=='purchase_order'
def test_enquiry(): assert fallback('Enquiry','Is it suitable?')['category']=='enquiry'
def test_followup(): assert fallback('Delivery update','Any status?')['category']=='follow_up'
