"""Chat-first task navigation. Task execution stays in the authorised controllers.

This local pilot uses explicit intent matching, not an unconfigured language
model. Messages never trigger writes, approvals, provider calls or email sends.
"""
import re

from fastapi import APIRouter, Form, HTTPException, Request

from .data_hub import ask_kaki
from .db import Session
from .identity import company_for_request, render, require_role

router = APIRouter(prefix='/chat')

TASKS = {
    'new_quote': ('New quotation', '/ops#quotes', 'new-quote', 'staff'),
    'quotes': ('Quotations', '/ops#quotes', None, 'viewer'),
    'new_customer': ('Add a customer', '/ops#customers', 'new-customer', 'manager'),
    'customers': ('Customers', '/ops#customers', None, 'viewer'),
    'new_product': ('Add a product', '/ops#products', 'new-product', 'manager'),
    'products': ('Product catalogue', '/ops#products', None, 'viewer'),
    'new_order': ('Register a purchase order', '/ops#orders', 'new-order', 'staff'),
    'orders': ('Purchase orders', '/ops#orders', None, 'viewer'),
    'new_workflow': ('Create a workflow', '/flows', 'new-workflow', 'admin'),
    'workflows': ('Workflow Studio', '/flows', None, 'viewer'),
    'team': ('Team & settings', '/settings', None, 'viewer'),
    'invite': ('Invite a teammate', '/settings', None, 'admin'),
    'backup': ('Database backup', '/settings', None, 'owner'),
    'connections': ('Connections', '/integrations', None, 'admin'),
    'inbox': ('Company inbox', '/integrations/inbox', None, 'viewer'),
    'data': ('Data Hub', '/data-hub', None, 'viewer'),
    'documents': ('Document mappings', '/settings', None, 'admin'),
}


def intent(message):
    text = message.lower()
    create = bool(re.search(r'\b(create|new|add|prepare|draft|register|make|generate)\b', text))
    if re.search(r'\b(backup|back up|restore)\b', text):
        return 'backup'
    if re.search(r'\b(invite|onboard|invitation)\b', text):
        return 'invite'
    if re.search(r'\b(workflow|workflows|automation|automations|approval|approvals|run|runs|publish|simulate)\b', text):
        return 'new_workflow' if create and re.search(r'\b(workflow|automation)\b', text) else 'workflows'
    if re.search(r'\b(purchase order|purchase orders|po|pos)\b', text):
        return 'new_order' if create else 'orders'
    if re.search(r'\b(quote|quotes|quotation|quotations)\b', text):
        return 'new_quote' if create else 'quotes'
    if re.search(r'\b(document|documents|mapping|mappings)\b', text):
        return 'documents'
    if re.search(r'\b(inbox|emails|messages)\b', text) and not re.search(r'\b(connect|connection)\b', text):
        return 'inbox'
    if re.search(r'\b(connect|connection|connections|outlook|microsoft|gmail)\b', text):
        return 'connections'
    if re.search(r'\b(sharepoint|import|sync|data hub)\b', text):
        return 'data'
    if re.search(r'\b(customer|customers|client|clients)\b', text):
        return 'new_customer' if create else 'customers'
    if re.search(r'\b(product|products|sku|catalogue|catalog)\b', text):
        return 'new_product' if create else 'products'
    if re.search(r'\b(team|staff|employee|employees|settings|permissions|revoke)\b', text):
        return 'team'
    return None


@router.get('')
def index(request: Request):
    require_role(request)
    with Session() as db:
        company = company_for_request(db, request)
        company_name = company.name
    return render(request, 'chat.html', company=company_name)


@router.post('/message')
def message(request: Request, message: str = Form('', max_length=500),
            csrf_token: str = Form(...), action: str = Form('')):
    require_role(request, csrf=csrf_token)
    text = message.strip()
    if action and action not in TASKS:
        raise HTTPException(400, 'Unknown workspace action')
    if not text and not action:
        raise HTTPException(422, 'Tell Kaki what you would like to do')
    with Session() as db:
        company_for_request(db, request)

    if not action and re.search(r'\b(send|sending)\b', text, re.I):
        return {'reply': 'Email sending is disabled in this pilot. You can prepare and approve a quotation or draft, but Kaki will not send it to a customer.',
                'task': None, 'mode': 'guided'}

    # Business questions use the existing company-scoped snapshot search.
    question = re.match(r'^(which|what|who|how|when|find|search|show|list|is|are|do)\b', text, re.I)
    knowledge = re.search(r'\b(jobs?|completed|overdue|stock|inventory|prices?|pricing)\b', text, re.I)
    if not action and question and knowledge:
        result = ask_kaki(request, text)
        return {'reply': result['answer'], 'sources': result['results'],
                'note': result['note'], 'task': None, 'mode': 'guided'}

    key = action or intent(text)
    if not key:
        return {'reply': 'I can help you work with quotations, purchase orders, customers, products, workflows, approvals, staff, documents, connections and backups. Tell me the task, or choose one below. For example: “Create a quotation” or “Show completed jobs”.',
                'task': None, 'mode': 'guided'}
    title, path, dialog, role = TASKS[key]
    require_role(request, role)
    quote = re.search(r'\b(?:quote|quotation)\s*#?\s*(\d+)\b|\bKAKI-Q-(\d+)\b', text, re.I)
    run = re.search(r'\brun\s*#?\s*(\d+)\b', text, re.I)
    if not action and quote and key == 'quotes':
        path = '/ops/quotes/' + str(int(quote.group(1) or quote.group(2)))
        title = 'Quotation details'
    elif not action and run and key == 'workflows':
        path = '/flows/runs/' + str(int(run.group(1)))
        title = 'Workflow run'
    reply = f'Let’s work on {title.lower()}. I’ve opened the task beside our conversation.'
    if dialog:
        reply += ' Complete the details and review them before submitting.'
    elif key in ('workflows', 'quotes', 'orders'):
        reply += ' Select a record to review or approve it. Approvals require an explicit click.'
    elif key == 'connections':
        reply += ' Your administrator must configure the connection before Microsoft sign-in is available.'
    elif key == 'backup':
        reply += ' Use Download secure database backup. Restoring is a separate administrator operation; it cannot be done in chat.'
    return {'reply': reply, 'task': {'title': title, 'path': path, 'dialog': dialog}, 'mode': 'guided'}
