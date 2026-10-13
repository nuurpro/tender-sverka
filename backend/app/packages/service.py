"""Short SQL authorization/reservation/finalization around encrypted S3 I/O.

Attempt objects are owned before PUT. Finalization atomically creates immutable
originals, an audit and an inspection job/outbox. Failed/ambiguous commits are
rechecked in a fresh transaction before deleting anything. DB outage defers GC.
"""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import secrets
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select

from app.workspace.database import tenant_transaction
from app.workspace.models import (Document, DocumentKeyEnvelope, Membership, Organization, PackageInspection,
    PackageRevision, Participant, Project, UploadAttempt, UploadIntent, User, NormalizedArtifact, utcnow)
from app.workspace.repository import Repository, Conflict, NotFound, ValidationError, canonical_json
from .crypto import AuthenticationFailure, EnvelopeCipher
from .storage import S3Store, StorageUnavailable

DOCUMENT_FIELDS = ('id','name','relative_path','size','mime_type','file_hash','provenance_role')


def format_support(mime,role='document'):
    if role=='archive':
        return 'provenance'
    if mime in {'image/jpeg','image/png','image/tiff','image/heic','image/bmp','image/webp'}:
        return 'ocr_required'
    if mime in {'application/pdf','text/plain','text/csv',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}:
        return 'inspection_available'
    return 'adapter_unavailable'


@contextmanager
def authorized(engine, org, actor, project, *, mutate=False):
    with tenant_transaction(engine, org) as db:
        if mutate and not db.scalar(select(Organization).where(Organization.id == org).with_for_update()):
            raise NotFound()
        query = select(Membership).where(Membership.organization_id == org, Membership.user_id == actor)
        if mutate:
            query = query.with_for_update()
        membership = db.scalar(query)
        user = db.get(User, actor)
        if not membership or not user or not user.active:
            raise NotFound()
        if mutate and membership.role == 'viewer':
            raise HTTPException(403, 'Недостаточно прав для изменения данных.')
        repo = Repository(db, org, actor)
        repo.get_project(project)
        yield db, repo


def nested_package(repo, project, package):
    row = repo._get(PackageRevision, package)
    if row.project_id != project:
        raise NotFound()
    return row


def package_detail(db, repo, package):
    docs = list(db.scalars(repo._query(Document).where(Document.package_id == package.id).order_by(Document.relative_path,Document.id)))
    return {'id':package.id,'project_id':package.project_id,'kind':package.kind,'participant_id':package.participant_id,
        'created_at':package.created_at.isoformat(),
        'version':package.version,'status':package.status,'manifest':package.manifest,
        'documents':[{**{field:getattr(doc,field) for field in DOCUMENT_FIELDS},
            'format_support':format_support(doc.mime_type,doc.provenance_role)} for doc in docs]}


class PackageService:
    def __init__(self, engine, settings, store=None, cipher=None, *, clock=utcnow, inspection_readiness=None):
        if inspection_readiness is not None and settings.environment!='test':
            raise ValueError('Inspection adapter injection requires the explicit test environment')
        self.engine, self.settings, self.store, self.cipher, self.clock = engine, settings, store, cipher, clock
        self.inspection_readiness=inspection_readiness

    def _configured(self):
        if self.cipher is None:
            if not self.settings.document_encryption_key:
                raise StorageUnavailable()
            version = self.settings.document_master_key_version
            self.cipher = EnvelopeCipher({version:bytes.fromhex(self.settings.document_encryption_key.get_secret_value())},version)
        if self.store is None:
            self.store = S3Store(self.settings)

    def authorize(self, org, actor, project, *, mutate=False):
        with authorized(self.engine,org,actor,project,mutate=mutate):
            pass

    def upload(self, org, actor, project, kind, participant, batch, idempotency_key, *, refresh_auth=None,cancelled=None):
        self._configured()
        if kind not in {'tender','bid'} or (kind == 'bid') != bool(participant):
            raise ValidationError('Tender has no participant; bid requires participant')
        if not isinstance(idempotency_key,str) or not 1 <= len(idempotency_key) <= 255 or any(ord(c)<32 for c in idempotency_key):
            raise ValidationError('Idempotency-Key is required')
        with authorized(self.engine,org,actor,project,mutate=True) as (_,repo):
            language=repo._get(Project,project).report_language
        from .inspection import inspection_profile
        frozen_inspection=inspection_profile(self.settings,language,readiness=self.inspection_readiness)
        file_inputs = sorted([{'path':f.path,'name':f.name,'hash':f.file_hash,'size':len(f.data),'role':f.role}
                              for f in batch.leaves + batch.containers],key=lambda v:v['path'])
        digest = hashlib.sha256(canonical_json({'kind':kind,'participant_id':participant,'files':file_inputs}).encode()).hexdigest()
        token = secrets.token_hex(32)
        pending = []
        for file in batch.leaves + batch.containers:
            doc_id = str(uuid4())
            pending.append({'id':doc_id,'name':file.name,'relative_path':file.path,'size':len(file.data),
                'mime_type':file.mime_type,'file_hash':file.file_hash,'provenance_role':file.role,
                'object_key':f'org/{org}/project/{project}/attempt/{token}/{doc_id}'})
        # Encryption happens in bounded RAM outside SQL and before any storage PUT.
        encrypted = []
        for file, metadata in zip(batch.leaves + batch.containers,pending):
            if cancelled is not None and cancelled.is_set():
                raise ValidationError('Upload cancelled')
            ciphertext,envelope = self.cipher.encrypt(file.data,self._scope(org,project,metadata['id']))
            metadata['envelope'] = envelope
            encrypted.append(ciphertext)
        if refresh_auth:
            refresh_auth()  # Slow body/expansion/encryption may outlive the session.
        now = self.clock()
        replayed = None
        with authorized(self.engine,org,actor,project,mutate=True) as (db,repo):
            repo._get(Project,project,lock=True)
            if participant:
                target = repo._get(Participant,participant)
                if target.project_id != project:
                    raise NotFound()
            intent = db.scalar(repo._query(UploadIntent).where(UploadIntent.project_id==project, UploadIntent.idempotency_key==idempotency_key).with_for_update())
            if intent:
                if intent.input_digest != digest:
                    raise Conflict('Idempotency key input changed')
                if intent.response is not None:
                    replayed = intent.response
                else:
                    active = list(db.scalars(repo._query(UploadAttempt).where(UploadAttempt.intent_id==intent.id,UploadAttempt.status=='uploading').with_for_update()))
                    for attempt in active:
                        if attempt.expires_at > now:
                            raise Conflict('Upload is still in progress')
                        attempt.status = 'abandoned'
            else:
                intent = UploadIntent(organization_id=org,project_id=project,idempotency_key=idempotency_key,input_digest=digest)
                db.add(intent); db.flush()
            if replayed is None:
                attempt = UploadAttempt(organization_id=org,project_id=project,intent_id=intent.id,actor_id=actor,
                    attempt_token=token,expires_at=now+timedelta(minutes=5),status='uploading',objects=pending)
                db.add(attempt); db.flush()
                intent_id,attempt_id = intent.id,attempt.id
        if replayed is not None:
            if refresh_auth:
                refresh_auth()  # Replay also rechecks outside the closed tenant transaction.
            self.authorize(org,actor,project,mutate=True)
            return replayed
        try:
            for obj,data in zip(pending,encrypted):
                if cancelled is not None and cancelled.is_set():
                    raise ValidationError('Upload cancelled')
                self.store.put(obj['object_key'],data)
            if cancelled is not None and cancelled.is_set():
                raise ValidationError('Upload cancelled')
            if refresh_auth:
                refresh_auth()  # Fresh session check after slow storage, before tenant transaction.
            result=self._finalize(org,actor,project,kind,participant,batch,intent_id,attempt_id,token,cancelled=cancelled,
                frozen_inspection=frozen_inspection)
            if refresh_auth:
                refresh_auth()
            self.authorize(org,actor,project,mutate=True)
            return result
        except BaseException:
            # Includes cancellation. A fresh SQL proof is mandatory before GC;
            # if the commit actually succeeded, replay its original response.
            recovered = self.reconcile_attempt(org,attempt_id,token,force=True)
            if recovered is not None:
                if refresh_auth:
                    refresh_auth()
                self.authorize(org,actor,project,mutate=True)
                return recovered
            raise

    @staticmethod
    def _scope(org,project,document):
        return {'organization_id':org,'project_id':project,'document_id':document,'object_kind':'original'}

    def _finalize(self,org,actor,project,kind,participant,batch,intent_id,attempt_id,token,*,cancelled=None,frozen_inspection=None):
        with authorized(self.engine,org,actor,project,mutate=True) as (db,repo):
            repo._get(Project,project,lock=True)
            intent = repo._get(UploadIntent,intent_id,lock=True)
            attempt = repo._get(UploadAttempt,attempt_id,lock=True)
            if intent.project_id != project or attempt.project_id != project or attempt.intent_id != intent.id or attempt.actor_id != actor or attempt.attempt_token != token:
                raise Conflict('Upload attempt changed')
            if intent.response:
                if attempt.status=='committed':
                    return intent.response
                raise Conflict('A different upload attempt committed this intent')
            if attempt.status != 'uploading' or attempt.expires_at <= self.clock():
                raise Conflict('Upload attempt expired')
            if participant and repo._get(Participant,participant).project_id != project:
                raise NotFound()
            version = db.scalar(select(func.max(PackageRevision.version)).where(PackageRevision.organization_id==org,
                PackageRevision.project_id==project,PackageRevision.kind==kind,PackageRevision.participant_id==participant)) or 0
            manifest = {'schema_version':1,'accounting':{'wire_files':batch.wire_files,'wire_bytes':batch.wire_bytes,
                'logical_files':len(batch.leaves),'logical_bytes':batch.logical_bytes,'archive_containers':len(batch.containers),
                'expanded_bytes':batch.expanded_bytes},
                'documents':[{**{field:obj[field] for field in DOCUMENT_FIELDS},
                    'format_support':format_support(obj['mime_type'],obj['provenance_role'])} for obj in attempt.objects]}
            package = PackageRevision(organization_id=org,project_id=project,kind=kind,participant_id=participant,
                                      version=version+1,manifest=manifest,status='uploaded')
            db.add(package); db.flush()
            for obj in attempt.objects:
                db.add(Document(organization_id=org,package_id=package.id,
                    **{key:value for key,value in obj.items() if key!='envelope'}))
            db.flush()
            for obj in attempt.objects:
                envelope=obj['envelope']
                db.add(DocumentKeyEnvelope(organization_id=org,project_id=project,package_id=package.id,
                    document_id=obj['id'],master_key_version=envelope['master_key_version'],envelope=envelope))
            db.flush()
            job = repo.create_job('package.inspect',project,{'package_id':package.id,'manifest_hash':
                hashlib.sha256(canonical_json(manifest).encode()).hexdigest(),
                'actor_id':actor,'inspection_profile':frozen_inspection},f'package-inspect:{package.id}')
            response = {'package':package_detail(db,repo,package),'inspection_job_id':job.id}
            repo._audit('package.uploaded',package,project_id=project,after={'id':package.id,'version':package.version,'accounting':manifest['accounting']})
            attempt.status='committed'; intent.package_id=package.id; intent.response=response
            db.flush()
            if attempt.expires_at<=self.clock():
                raise Conflict('Upload attempt expired during finalization')
            if cancelled is not None and cancelled.is_set():
                raise ValidationError('Upload cancelled')
            return response

    def reconcile_attempt(self,org,attempt_id,token=None,*,force=False,raise_on_failure=False):
        """Return committed response or prove objects unreferenced before cleanup.

        Network/DB outages deliberately retain owned objects for later retry.
        Cleaned attempts remain discoverable: a delayed stale PUT can be deleted
        on a later reconciliation pass without losing object ownership records.
        """
        try:
            with tenant_transaction(self.engine,org) as db:
                attempt=db.scalar(select(UploadAttempt).where(UploadAttempt.organization_id==org,UploadAttempt.id==attempt_id))
                if not attempt or (token is not None and attempt.attempt_token!=token):
                    return None
                intent=db.scalar(select(UploadIntent).where(UploadIntent.organization_id==org,UploadIntent.id==attempt.intent_id).with_for_update())
                # Same lock order as finalization: intent, then attempt.
                attempt=db.scalar(select(UploadAttempt).where(UploadAttempt.organization_id==org,UploadAttempt.id==attempt_id)
                    .with_for_update().execution_options(populate_existing=True))
                if attempt.status=='committed':
                    return intent.response
                if attempt.status=='uploading' and not force and attempt.expires_at>self.clock():
                    return None
                for obj in attempt.objects:
                    expected=f"org/{org}/project/{attempt.project_id}/attempt/{attempt.attempt_token}/{obj['id']}"
                    if obj['object_key']!=expected:
                        raise StorageUnavailable()
                keys=[obj['object_key'] for obj in attempt.objects]
                references=db.scalar(select(func.count()).select_from(Document).where(Document.organization_id==org,Document.object_key.in_(keys)))
                references+=db.scalar(select(func.count()).select_from(NormalizedArtifact).where(NormalizedArtifact.organization_id==org,NormalizedArtifact.object_key.in_(keys)))
                if references:
                    return None  # Fail closed even if inconsistent intent state.
                attempt.status='abandoned'
                attempt.cleanup_attempts+=1
                attempt.last_cleanup_at=self.clock()
            self._configured()
            for key in keys:
                self.store.delete(key)
            with tenant_transaction(self.engine,org) as db:
                attempt=db.scalar(select(UploadAttempt).where(UploadAttempt.organization_id==org,UploadAttempt.id==attempt_id).with_for_update())
                if attempt and attempt.status=='abandoned':
                    attempt.status='cleaned'
        except Exception:
            if raise_on_failure:
                raise StorageUnavailable() from None
            return None  # No deletion on unavailable proof; durable ownership is retained.
        return None

    def reconcile(self,org,*,limit=100):
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError('Reconciliation scan is bounded to one hundred attempts')
        with tenant_transaction(self.engine,org) as db:
            identities=list(db.scalars(select(UploadAttempt.id).where(UploadAttempt.organization_id==org,
                UploadAttempt.status!='committed',UploadAttempt.expires_at<=self.clock())
                .order_by(UploadAttempt.cleanup_attempts,UploadAttempt.created_at,UploadAttempt.id).limit(limit)))
        for identity in identities:
            self.reconcile_attempt(org,identity,raise_on_failure=True)
        return len(identities)

    def history(self,org,actor,project):
        with authorized(self.engine,org,actor,project) as (db,repo):
            rows=list(db.scalars(repo._query(PackageRevision).where(PackageRevision.project_id==project).order_by(PackageRevision.created_at,PackageRevision.id)))
            return [package_detail(db,repo,row) for row in rows]

    def detail(self,org,actor,project,package):
        with authorized(self.engine,org,actor,project) as (db,repo):
            return package_detail(db,repo,nested_package(repo,project,package))

    def preflight(self,org,actor,project,package):
        with authorized(self.engine,org,actor,project) as (db,repo):
            row=nested_package(repo,project,package)
            inspection=db.scalar(repo._query(PackageInspection).where(PackageInspection.package_id==package).order_by(PackageInspection.created_at.desc()))
            accounting=row.manifest.get('accounting',{})
            pending={'inspection_status':'pending','analysis_allowed':False,'page_count':None,'token_count':None,
                'token_upper_bound':None,'blockers':['inspection_pending','extraction_pending','tokenizer_profile_pending'],
                'warnings':[]}
            formats=[{'document_id':doc['id'],'mime_type':doc['mime_type'],'support':doc.get('format_support',
                format_support(doc['mime_type'],doc['provenance_role']))} for doc in row.manifest['documents'] if doc['provenance_role']=='document']
            if any(doc['support']=='adapter_unavailable' for doc in formats):
                pending['blockers'].append('adapter_unavailable')
            return {**accounting,**(inspection.result if inspection else pending),'formats':formats}

    def _document_metadata(self,db,repo,project,package,document):
        nested_package(repo,project,package)
        doc=repo._get(Document,document)
        if doc.package_id!=package:
            raise NotFound()
        envelopes=list(db.scalars(repo._query(DocumentKeyEnvelope).where(DocumentKeyEnvelope.document_id==document).order_by(DocumentKeyEnvelope.created_at.desc())))
        self._configured()
        envelope=next((row.envelope for row in envelopes if row.master_key_version in self.cipher.keys),None)
        if envelope is None:
            raise StorageUnavailable()
        return {**{field:getattr(doc,field) for field in DOCUMENT_FIELDS},'object_key':doc.object_key,'envelope':envelope}

    def read_original(self,org,project,metadata):
        self._configured()
        ciphertext=self.store.get(metadata['object_key'])
        original=self.cipher.decrypt(ciphertext,metadata['envelope'],self._scope(org,project,metadata['id']),metadata['size'])
        if hashlib.sha256(original).hexdigest()!=metadata['file_hash']:
            raise AuthenticationFailure()
        return original

    def download(self,org,actor,project,package,document,*,refresh_auth=None):
        with authorized(self.engine,org,actor,project) as (db,repo):
            metadata=self._document_metadata(db,repo,project,package,document)
        original=self.read_original(org,project,metadata)
        if refresh_auth:
            refresh_auth()
        self.authorize(org,actor,project)  # Revocation during network is observed.
        return original,metadata
