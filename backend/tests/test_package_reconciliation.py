from datetime import timedelta
import threading
import time

import pytest
from sqlalchemy import select

from test_full_auth_api import full_api
from test_package_api import packages,upload


def maintenance():
    try:
        from app.packages.reconciliation import UploadReconciler,start_reconciliation
    except ImportError:
        pytest.fail('Automatic bounded upload reconciliation is missing')
    return UploadReconciler,start_reconciliation


def configured(app):
    from pydantic import SecretStr
    return app.state.settings.model_copy(update={'s3_endpoint_url':'http://127.0.0.1:53900','s3_bucket':'private',
        's3_access_key':SecretStr('fixture'),'s3_secret_key':SecretStr('fixture'),
        'document_encryption_key':SecretStr('a'*64)})


def owned_orphan(packages,monkeypatch):
    from app.packages.inputs import prepare_files
    from app.packages.storage import StorageUnavailable
    from app.workspace.repository import Conflict
    from app.workspace.models import UploadAttempt,utcnow
    from app.workspace.database import tenant_transaction
    client,engine,app,store,org,pid=packages
    instant=[utcnow()];app.state.packages.clock=lambda:instant[0]
    store.on_put=lambda:instant.__setitem__(0,instant[0]+timedelta(minutes=6))
    delete=store.delete
    monkeypatch.setattr(store,'delete',lambda key: (_ for _ in ()).throw(StorageUnavailable()))
    with pytest.raises(Conflict):
        app.state.packages.upload(org,app.state.auth.authenticate(client.cookies.get('tender_session')).id,pid,
            'tender',None,prepare_files([('orphan.txt',b'owned encrypted orphan')]),'orphan')
    monkeypatch.setattr(store,'delete',delete);store.on_put=None
    with tenant_transaction(engine,org) as db:
        attempt=db.scalar(select(UploadAttempt));assert attempt.status=='abandoned'
    assert store.objects
    return attempt.id


def test_job_only_dispatcher_does_not_start_unconfigured_storage_maintenance(packages):
    cls,start=maintenance()
    _,engine,app,*_=packages
    assert start(engine,app.state.settings,threading.Event()) is None


def test_partial_storage_configuration_refuses_to_silently_skip_maintenance(packages):
    cls,start=maintenance()
    _,engine,app,*_=packages
    settings=app.state.settings.model_copy(update={'s3_endpoint_url':'http://127.0.0.1:53900'})
    with pytest.raises(ValueError,match='Incomplete'):
        start(engine,settings,threading.Event())


def test_automatic_thread_discovers_and_cleans_durable_owned_orphan(packages,monkeypatch):
    cls,start=maintenance();owned_orphan(packages,monkeypatch)
    _,engine,app,store,org,pid=packages
    removed=threading.Event();delete=store.delete
    def observed_delete(key):
        delete(key);removed.set()
    monkeypatch.setattr(store,'delete',observed_delete)
    stop=threading.Event()
    thread=start(engine,configured(app),stop,packages=app.state.packages)
    try:
        assert thread is not None and removed.wait(timeout=3)
    finally:
        stop.set();thread.join(timeout=3)
    assert not thread.is_alive() and not store.objects


def test_blocked_cleanup_network_does_not_hold_sql_or_starve_outbox_dispatch(packages,monkeypatch):
    from app.jobs.publisher import OutboxPublisher
    from app.workspace.database import tenant_transaction
    from app.workspace.repository import Repository
    cls,start=maintenance();owned_orphan(packages,monkeypatch)
    client,engine,app,store,org,pid=packages
    actor=app.state.auth.authenticate(client.cookies.get('tender_session')).id
    with tenant_transaction(engine,org) as db:
        Repository(db,org,actor).create_job('fixture',pid,{'version':1},'outbox-independent')
    began,release=threading.Event(),threading.Event();delete=store.delete
    def blocked_delete(key):
        began.set();assert release.wait(timeout=5);delete(key)
    monkeypatch.setattr(store,'delete',blocked_delete)
    stop=threading.Event();thread=start(engine,configured(app),stop,packages=app.state.packages)
    try:
        assert began.wait(timeout=3)
        seen=[];before=time.monotonic()
        assert OutboxPublisher(engine,'j'*48,lambda message:seen.append(message)).dispatch_once(per_org_limit=1)==1
        assert time.monotonic()-before<1 and len(seen)==1
    finally:
        release.set();stop.set();thread.join(timeout=3)
    assert not thread.is_alive()


def test_cleanup_outage_uses_per_org_backoff_and_recovers_without_false_success(packages,monkeypatch):
    from app.packages.storage import StorageUnavailable
    cls,start=maintenance();owned_orphan(packages,monkeypatch)
    _,engine,app,store,org,pid=packages
    instant=[0.0];delete=store.delete;calls=[]
    def unavailable(key):
        calls.append(key);raise StorageUnavailable()
    monkeypatch.setattr(store,'delete',unavailable)
    reconciler=cls(engine,configured(app),packages=app.state.packages,clock=lambda:instant[0])
    assert reconciler.run_once()['failed_organizations']==1
    assert store.objects and len(calls)==1
    assert reconciler.run_once()['failed_organizations']==0 and len(calls)==1
    instant[0]=301;monkeypatch.setattr(store,'delete',delete)
    assert reconciler.run_once()['failed_organizations']==0 and not store.objects
