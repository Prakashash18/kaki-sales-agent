from uuid import uuid4

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.chat import TASKS, intent
from app.company_settings import CompanyProfile
from app.db import Session
from app.identity import Account, Membership, password_hash
from app.operations import Quote
from app.secure_main import app
from app.workflow_core import Workflow


@pytest.fixture(scope='module')
def owner():
    email = f'chat-{uuid4().hex}@example.test'
    password = 'synthetic-chat-password-2026'
    with Session() as db:
        company = CompanyProfile(name='Chat test', active=False)
        user = Account(email=email, password_hash=password_hash(password))
        db.add_all([company, user])
        db.flush()
        db.add(Membership(account_id=user.id, company_id=company.id, role='owner'))
        db.commit()
    client = TestClient(app)
    login = client.post('/login', data={'email': email, 'password': password}, follow_redirects=False)
    assert login.headers['location'] == '/chat'
    page = client.get('/chat')
    assert page.status_code == 200
    csrf = BeautifulSoup(page.text, 'html.parser').select_one('[name=csrf_token]')['value']
    return client, csrf


@pytest.mark.parametrize('message,expected', [
    ('Create a quotation for my customer', 'new_quote'),
    ('Register a purchase order', 'new_order'),
    ('Add a product', 'new_product'),
    ('Invite an employee', 'invite'),
    ('Create a workflow', 'new_workflow'),
    ('Create a workflow with approval', 'new_workflow'),
    ('Show workflow approvals', 'workflows'),
    ('Connect Microsoft 365', 'connections'),
])
def test_task_matching(message, expected):
    assert intent(message) == expected


def test_task_requests_do_not_execute_business_changes(owner):
    client, csrf = owner
    def counts():
        with Session() as db:
            return [db.scalar(select(func.count()).select_from(model)) for model in (Quote, Workflow, Account)]
    before = counts()
    for action in TASKS:
        response = client.post('/chat/message', data={'action': action, 'csrf_token': csrf})
        assert response.status_code == 200
        assert response.json()['task']['path'].startswith('/')
    assert counts() == before


def test_chat_authentication_csrf_and_action_allowlist(owner):
    client, csrf = owner
    anonymous = TestClient(app)
    assert anonymous.get('/chat').status_code == 401
    assert anonymous.post('/chat/message', data={'message': 'Create quote', 'csrf_token': csrf}).status_code == 401
    assert client.post('/chat/message', data={'message': 'Create quote', 'csrf_token': 'wrong'}).status_code == 403
    assert client.post('/chat/message', data={'action': 'https://example.com', 'csrf_token': csrf}).status_code == 400
    assert client.post('/chat/message', data={'message': 'x' * 501, 'csrf_token': csrf}).status_code == 422


def test_chat_knowledge_search_stays_company_scoped(owner):
    client, csrf = owner
    result = client.post('/chat/message', data={'message': 'Which jobs are completed?', 'csrf_token': csrf}).json()
    assert result['task'] is None
    assert result['sources'] == []
    assert 'snapshots' in result['reply']


def test_chat_does_not_send_on_request(owner):
    client, csrf = owner
    result = client.post('/chat/message', data={'message': 'Send quotation 3', 'csrf_token': csrf}).json()
    assert result['task'] is None
    assert 'sending is disabled' in result['reply']


def test_chat_blocks_privileged_actions_for_staff():
    email = f'chat-staff-{uuid4().hex}@example.test'
    with Session() as db:
        company = CompanyProfile(name='Staff chat test', active=False)
        user = Account(email=email, password_hash=password_hash('synthetic-staff-password-2026'))
        db.add_all([company, user])
        db.flush()
        db.add(Membership(account_id=user.id, company_id=company.id, role='staff'))
        db.commit()
    client = TestClient(app)
    client.post('/login', data={'email': email, 'password': 'synthetic-staff-password-2026'})
    csrf = BeautifulSoup(client.get('/chat').text, 'html.parser').select_one('[name=csrf_token]')['value']
    for action in ('invite', 'new_product', 'backup', 'new_workflow'):
        assert client.post('/chat/message', data={'action': action, 'csrf_token': csrf}).status_code == 403


def test_task_embedding_remains_same_origin_only():
    client = TestClient(app)
    assert client.get('/health').headers['x-frame-options'] == 'DENY'
    local = client.get('/health', headers={'sec-fetch-dest': 'iframe', 'sec-fetch-site': 'same-origin'})
    assert local.headers['x-frame-options'] == 'SAMEORIGIN'
    assert "frame-ancestors 'self'" in local.headers['content-security-policy']
    cross_site = client.get('/health', headers={'sec-fetch-dest': 'iframe', 'sec-fetch-site': 'cross-site'})
    assert cross_site.headers['x-frame-options'] == 'DENY'
