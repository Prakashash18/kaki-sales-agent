import os,httpx
BASE='https://graph.microsoft.com/v1.0'
def configured(): return all(os.getenv(k) for k in ('MS_TENANT_ID','MS_CLIENT_ID','MS_CLIENT_SECRET','MS_MAILBOX'))
async def token(c):
    if not configured(): raise RuntimeError('Graph credentials missing')
    r=await c.post(f"https://login.microsoftonline.com/{os.environ['MS_TENANT_ID']}/oauth2/v2.0/token",data={'client_id':os.environ['MS_CLIENT_ID'],'client_secret':os.environ['MS_CLIENT_SECRET'],'scope':'https://graph.microsoft.com/.default','grant_type':'client_credentials'})
    r.raise_for_status(); return r.json()['access_token']
async def get_messages():
    async with httpx.AsyncClient(timeout=30) as c:
        key=await token(c)
        r=await c.get(f"{BASE}/users/{os.environ['MS_MAILBOX']}/mailFolders/inbox/messages",headers={'Authorization':f'Bearer {key}'},params={'$top':25,'$select':'id,subject,from,body,receivedDateTime,hasAttachments','$orderby':'receivedDateTime desc'})
        r.raise_for_status();return r.json().get('value',[])
async def send_reply(message_id,body):
    if os.getenv('DISABLE_SEND','true').lower()!='false': raise RuntimeError('Sending disabled')
    async with httpx.AsyncClient(timeout=30) as c:
        key=await token(c);headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'}
        prefix=f"{BASE}/users/{os.environ['MS_MAILBOX']}/messages"
        r=await c.post(f'{prefix}/{message_id}/createReply',headers=headers,json={});r.raise_for_status();draft=r.json()['id']
        r=await c.patch(f'{prefix}/{draft}',headers=headers,json={'body':{'contentType':'Text','content':body}});r.raise_for_status()
        r=await c.post(f'{prefix}/{draft}/send',headers=headers);r.raise_for_status()
