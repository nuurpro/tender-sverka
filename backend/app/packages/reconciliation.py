"""Independent, bounded periodic owned-object reconciliation for the dispatcher.

Durable attempts carry discovery/ownership state. A process-local cursor and
bounded backoff map merely schedule work; restarting cannot lose an orphan.
"""
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.workspace.models import Organization
from .service import PackageService


def configured_storage(settings):
    values=(settings.s3_endpoint_url,settings.s3_bucket,settings.s3_access_key,
            settings.s3_secret_key,settings.document_encryption_key)
    if any(values) and not all(values):
        raise ValueError('Incomplete encrypted S3 configuration for upload reconciliation')
    return all(values)


class UploadReconciler:
    def __init__(self,engine,settings,*,packages=None,clock=time.monotonic):
        if packages is not None and settings.environment!='test':
            raise ValueError('Reconciliation adapter injection requires the explicit test environment')
        self.engine,self.settings,self.clock=engine,settings,clock
        self.packages=packages or PackageService(engine,settings)
        self.cursor=None
        self.backoff={}

    def run_once(self,*,organization_limit=25,attempt_limit=10):
        if type(organization_limit) is not int or not 1<=organization_limit<=100 or type(attempt_limit) is not int or not 1<=attempt_limit<=100:
            raise ValueError('Reconciliation scan limits must be between one and one hundred')
        with Session(self.engine) as db:
            query=select(Organization.id).order_by(Organization.id).limit(organization_limit)
            organizations=list(db.scalars(query.where(Organization.id>self.cursor) if self.cursor else query))
            if not organizations and self.cursor:
                organizations=list(db.scalars(query))
            self.cursor=organizations[-1] if organizations else None
        # Global identities are the same internal boundary as OutboxPublisher;
        # every attempt/reference proof runs in a separate tenant transaction.
        attempted,failed=0,0
        for org in organizations:
            count,available=self.backoff.get(org,(0,0))
            if available>self.clock():
                continue
            attempted+=1
            try:
                self.packages.reconcile(org,limit=attempt_limit)
                self.backoff.pop(org,None)
            except Exception:
                failed+=1
                count=min(count+1,8)
                self.backoff[org]=(count,self.clock()+min(300,2**count))
                if len(self.backoff)>1000:
                    # Scheduling metadata is bounded; durable ownership is not
                    # discarded. Evicted organizations are safely rediscovered.
                    self.backoff.pop(next(iter(self.backoff)))
        return {'attempted_organizations':attempted,'failed_organizations':failed}

    def run(self,stop):
        failures=0
        while not stop.is_set():
            try:
                self.run_once()
                failures=0
            except Exception:
                failures=min(failures+1,8)
            stop.wait(min(300,30*(2**failures)))


def start_reconciliation(engine,settings,stop,*,packages=None):
    if not configured_storage(settings):
        return None
    reconciler=UploadReconciler(engine,settings,packages=packages)
    thread=threading.Thread(target=reconciler.run,args=(stop,),name='upload-object-reconciliation',daemon=True)
    thread.start()
    return thread
