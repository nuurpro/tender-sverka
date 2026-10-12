from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import select, func

from test_full_auth_api import full_api, login, auth_headers
from test_full_workspace_api import create_org, project


class MemoryPrivateStore:
    """Explicit test-only private object adapter; no production fallback."""
    def __init__(self):
        self.objects = {}
        self.fail = False
        self.on_put = None

    def put(self, key, data):
        if self.on_put:
            self.on_put()
        if self.fail:
            from app.packages.storage import StorageUnavailable
            raise StorageUnavailable()
        self.objects[key] = data

    def get(self, key):
        return self.objects[key]

    def delete(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def packages(full_api):
    client, engine, _, _, app = full_api
    try:
        from app.packages.service import PackageService
        from app.packages.crypto import EnvelopeCipher
    except ImportError:
        pytest.fail('Immutable encrypted package service/routes are missing')
    store = MemoryPrivateStore()
    app.state.packages = PackageService(engine, app.state.settings, store, EnvelopeCipher({'v1': b'k' * 32}, 'v1'))
    login(full_api)
    org = create_org(client); pid = project(client, org)
    return client, engine, app, store, org, pid


def upload(client, base, key='upload-1', name='folder/a.txt', data=b'original text', **extra):
    return client.post(base + '/packages', data={'kind': 'tender', **extra},
        files=[('files', (name, data, 'evil/type'))], headers={**auth_headers(client), 'Idempotency-Key': key})


def test_upload_history_download_preflight_idempotency_and_no_secret_response(packages):
    from app.workspace.models import AuditEvent, Document, Job, OutboxEvent
    from app.workspace.database import tenant_transaction
    client, engine, app, store, org, pid = packages
    base = f'/api/organizations/{org}/projects/{pid}'
    response = upload(client, base)
    assert response.status_code == 200, response.text
    result = response.json(); package = result['package']; document = package['documents'][0]
    assert package['version'] == 1 and document['relative_path'] == 'folder/a.txt'
    assert document['mime_type'] == 'text/plain'
    assert all(secret not in response.text for secret in ['wrapped_key', 'object_key', 'nonce', 'envelope'])
    assert upload(client, base).json() == result
    assert upload(client, base, data=b'changed').status_code == 409
    assert len(client.get(base + '/packages').json()) == 1
    path = base + f"/packages/{package['id']}"
    assert client.get(path).json()['id'] == package['id']
    preflight = client.get(path + '/preflight').json()
    assert preflight['analysis_allowed'] is False and preflight['page_count'] is None and preflight['token_count'] is None
    assert preflight['inspection_status'] == 'pending'
    download = client.get(path + f"/documents/{document['id']}/download")
    assert download.status_code == 200 and download.content == b'original text'
    assert download.headers['x-content-type-options'] == 'nosniff'
    assert download.headers['cache-control'] == 'no-store'
    assert list(store.objects.values())[0] != b'original text'
    with tenant_transaction(engine, org) as db:
        assert db.scalar(select(func.count()).select_from(Document)) == 1
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 1
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == 'package.uploaded')) == 1


def test_auth_role_and_nested_checks_before_body_and_storage(packages):
    from app.workspace.models import Membership
    from app.workspace.database import tenant_transaction
    client, engine, app, store, org, pid = packages
    base = f'/api/organizations/{org}/projects/{pid}'
    result = upload(client, base).json()['package']; document = result['documents'][0]
    foreign = project(client, org)
    wrong = f'/api/organizations/{org}/projects/{foreign}/packages/{result["id"]}'
    assert client.get(wrong).status_code == 404
    assert client.get(wrong + f'/documents/{document["id"]}/download').status_code == 404
    assert client.post(base + '/packages', content=b'malformed').status_code == 403
    assert client.post(base + '/packages', content=b'malformed', headers={**auth_headers(client), 'Origin': 'https://evil'}).status_code == 403
    own = client.get('/api/auth/session').json()['user']['id']
    with tenant_transaction(engine, org) as db:
        db.scalar(select(Membership).where(Membership.user_id == own)).role = 'viewer'
    assert upload(client, base).status_code == 403
    assert client.get(base + f'/packages/{result["id"]}/documents/{document["id"]}/download').status_code == 200
    client.cookies.clear()
    assert client.post(base+'/packages',content=b'malformed').status_code == 401
    assert client.get(base + f'/packages/{result["id"]}/documents/{document["id"]}/download').status_code == 401
    assert len(store.objects) == 1


def test_storage_failure_keeps_no_revision_job_audit_and_retry_succeeds(packages):
    from app.workspace.models import PackageRevision, Job, AuditEvent
    from app.workspace.database import tenant_transaction
    client, engine, app, store, org, pid = packages
    base = f'/api/organizations/{org}/projects/{pid}'
    store.fail = True
    assert upload(client, base).status_code == 503
    with tenant_transaction(engine, org) as db:
        assert not list(db.scalars(select(PackageRevision))) and not list(db.scalars(select(Job)))
        assert not list(db.scalars(select(AuditEvent).where(AuditEvent.action == 'package.uploaded')))
    store.fail = False
    assert upload(client, base).json()['package']['version'] == 1


def test_membership_revoked_during_storage_cannot_finalize(packages):
    from app.workspace.models import Membership
    from app.workspace.database import tenant_transaction
    client, engine, app, store, org, pid = packages
    base = f'/api/organizations/{org}/projects/{pid}'
    own = client.get('/api/auth/session').json()['user']['id']
    def revoke():
        with tenant_transaction(engine, org) as db:
            db.scalar(select(Membership).where(Membership.user_id == own)).role = 'viewer'
    store.on_put = revoke
    assert upload(client, base).status_code == 403
    assert not store.objects


def test_download_admission_stays_held_until_response_bytes_are_sent(packages):
    import asyncio
    import threading
    from uuid import UUID
    from starlette.requests import Request
    from app.packages.routes import download
    client,engine,app,store,org,pid=packages
    result=upload(client,f'/api/organizations/{org}/projects/{pid}').json()['package']
    app.state.document_admission=threading.BoundedSemaphore(1)
    raw=client.cookies.get('tender_session')
    user=app.state.auth.authenticate(raw)
    scope={'type':'http','method':'GET','path':'/download','headers':[(b'cookie',f'tender_session={raw}'.encode())],'app':app}
    response=asyncio.run(download(UUID(org),UUID(pid),UUID(result['id']),UUID(result['documents'][0]['id']),Request(scope),user))
    assert not app.state.document_admission.acquire(blocking=False), 'Authenticated response bytes escaped the bounded document admission gate'
    sent=[]
    async def send(message):
        sent.append(message)
    async def receive():
        return {'type':'http.disconnect'}
    asyncio.run(response(scope,receive,send))
    assert sent[-1]['body']==b'original text'
    assert app.state.document_admission.acquire(blocking=False)
    app.state.document_admission.release()


def test_unsupported_archive_format_is_visible_before_inspection_job(packages):
    client,engine,app,store,org,pid=packages
    base=f'/api/organizations/{org}/projects/{pid}'
    package=upload(client,base,name='archive.rar',data=b'Rar!\x1a\x07\x00opaque original').json()['package']
    pending=client.get(base+f'/packages/{package["id"]}/preflight').json()
    assert 'adapter_unavailable' in pending['blockers'], 'Unsupported archive disappeared behind a generic pending status'
    assert pending['formats'][0]['support']=='adapter_unavailable'
    assert package['documents'][0]['format_support']=='adapter_unavailable'


def test_cancelled_upload_waits_for_owned_storage_cleanup_before_releasing_admission(packages):
    import asyncio
    import threading
    from uuid import UUID
    from starlette.requests import Request
    from app.packages.routes import upload as route_upload
    client,engine,app,store,org,pid=packages
    app.state.document_admission=threading.BoundedSemaphore(1)
    raw=client.cookies.get('tender_session');user=app.state.auth.authenticate(raw)
    csrf=auth_headers(client)['X-CSRF-Token']
    started,release=threading.Event(),threading.Event()
    def slow_put():
        started.set();assert release.wait(timeout=5)
    store.on_put=slow_put
    body=b'--b\r\nContent-Disposition: form-data; name="kind"\r\n\r\ntender\r\n--b\r\nContent-Disposition: form-data; name="files"; filename="a.txt"\r\n\r\nbytes\r\n--b--\r\n'
    scope={'type':'http','method':'POST','path':'/packages','app':app,'headers':[(b'cookie',f'tender_session={raw}'.encode()),
        (b'origin',app.state.settings.public_origin.encode()),(b'x-csrf-token',csrf.encode()),
        (b'content-type',b'multipart/form-data; boundary=b'),(b'idempotency-key',b'cancel-upload')]}
    async def receive():
        return {'type':'http.request','body':body,'more_body':False}
    async def scenario():
        task=asyncio.create_task(route_upload(UUID(org),UUID(pid),Request(scope,receive),user))
        assert await asyncio.to_thread(started.wait,3)
        task.cancel();await asyncio.sleep(.05)
        held=not app.state.document_admission.acquire(blocking=False)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert held, 'Cancelled HTTP released admission while encrypted storage thread still owned document RAM'
        assert not store.objects, 'Cancelled upload left uncommitted objects without cleanup'
    asyncio.run(scenario())


def test_cancelled_download_releases_admission_after_storage_thread_finishes(packages):
    import asyncio
    import threading
    client,engine,app,store,org,pid=packages
    result=upload(client,f'/api/organizations/{org}/projects/{pid}').json()['package']
    app.state.document_admission=threading.BoundedSemaphore(1)
    started,release=threading.Event(),threading.Event();get=store.get
    def slow_get(key):
        started.set();assert release.wait(timeout=5)
        return get(key)
    store.get=slow_get
    path=f'/api/organizations/{org}/projects/{pid}/packages/{result["id"]}/documents/{result["documents"][0]["id"]}/download'
    raw=client.cookies.get('tender_session')
    scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'GET','scheme':'http',
        'path':path,'raw_path':path.encode(),'query_string':b'','root_path':'',
        'headers':[(b'cookie',f'tender_session={raw}'.encode())],'server':('localhost',8000),'client':('test',1234)}
    sent=[]
    async def receive():
        return {'type':'http.request','body':b'','more_body':False}
    async def send(message):
        sent.append(message)
    async def scenario():
        task=asyncio.create_task(app(scope,receive,send))
        assert await asyncio.to_thread(started.wait,3)
        task.cancel();await asyncio.sleep(.05)
        held=not app.state.document_admission.acquire(blocking=False)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(.1)
        assert held, 'Download cancellation released RAM admission during a live storage thread'
        assert app.state.document_admission.acquire(blocking=False), 'Cancelled download leaked its admission slot'
        app.state.document_admission.release()
        assert not sent
    asyncio.run(scenario())


def test_idempotent_replay_rechecks_session_after_slow_body_before_returning_metadata(packages):
    import asyncio
    from sqlalchemy.orm import Session
    from app.workspace.models import AuthSession
    client,engine,app,store,org,pid=packages
    base=f'/api/organizations/{org}/projects/{pid}'
    original=upload(client,base).json()
    raw=client.cookies.get('tender_session');actor=app.state.auth.authenticate(raw).id
    csrf=auth_headers(client)['X-CSRF-Token']
    body=b'--b\r\nContent-Disposition: form-data; name="kind"\r\n\r\ntender\r\n--b\r\nContent-Disposition: form-data; name="files"; filename="folder/a.txt"\r\n\r\noriginal text\r\n--b--\r\n'
    path=base+'/packages'
    scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'POST','scheme':'http',
        'path':path,'raw_path':path.encode(),'query_string':b'','root_path':'',
        'headers':[(b'cookie',f'tender_session={raw}'.encode()),(b'origin',app.state.settings.public_origin.encode()),
            (b'x-csrf-token',csrf.encode()),(b'content-type',b'multipart/form-data; boundary=b'),
            (b'idempotency-key',b'upload-1')],'server':('localhost',8000),'client':('test',1234)}
    sent=[]
    async def receive():
        with Session(engine) as db,db.begin():
            db.scalar(select(AuthSession).where(AuthSession.user_id==actor)).revoked=True
        return {'type':'http.request','body':body,'more_body':False}
    async def send(message):
        sent.append(message)
    asyncio.run(app(scope,receive,send))
    assert sent[0]['status']==401, 'A revoked session received committed package metadata from idempotent replay'
    assert original['package']['id'].encode() not in sent[-1]['body']
    assert client.get('/api/auth/session').status_code==401
    assert len(store.objects)==1
