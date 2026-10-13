"""Versioned evidence: bounded isolation, encrypted storage and fenced publication.

Only metadata lives in SQL. Every plaintext artifact is bounded RAM, and every
storage PUT has durable ownership before it starts. Network calls never retain
SQL transactions. Cache reuse rebinds local evidence to the authorized original.
"""
from collections import Counter
from datetime import timedelta
import hashlib
from pathlib import PurePosixPath
import secrets
import time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.jobs.service import LeaseLost, public_job
from app.packages.crypto import AuthenticationFailure
from app.packages.service import PackageService, authorized, nested_package
from app.packages.storage import StorageUnavailable
from app.workspace.database import tenant_transaction
from app.workspace.models import (ArtifactUploadAttempt, Document, Job, JobCheckpoint,
    Membership, NormalizedArtifact, Project, User, utcnow)
from app.workspace.repository import Conflict, NotFound, Repository, ValidationError, canonical_json
from .runner import DocumentRunnerError, ensure_runner_available, run_document
from .schemas import (CoverageEntry, NormalizedDocument, ParserLimits, ParserProfile, PartAnchor, RunnerPolicy,
                      OCRProfile, OCRLimits, NormalizedDocumentV2, CoverageV2, parse_document, parse_profile)

STAGE = 'package.normalize'


class NormalizationOptions(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    encoding: Literal['auto', 'utf-8', 'utf-16', 'cp1251'] = 'auto'
    csv_delimiter: Literal[',', ';', '\t', '|'] = ','


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def processing_profile(settings, language, options=None, *, probe=None):
    options = NormalizationOptions.model_validate(options or {})
    probe = ensure_runner_available(settings) if probe is None else probe
    if probe.get('isolated') is not True:
        raise DocumentRunnerError('runner_unavailable', 'Document isolation is unavailable')
    profile_type = OCRProfile if settings.document_ocr_mode=='tesseract' else ParserProfile
    extras={}
    limit_type=ParserLimits
    limit_extras={}
    if profile_type is OCRProfile:
        if not probe.get('ocr_toolchain'):
            raise DocumentRunnerError('ocr_unavailable','Configured OCR toolchain is unavailable')
        extras={'toolchain':probe['ocr_toolchain'],'raster_dpi':settings.document_ocr_raster_dpi,
                'transform_policy':'affine-exif-osd-deskew-v3',
                'page_segmentation':settings.document_ocr_page_segmentation,
                'low_confidence_review':settings.document_ocr_low_confidence_review}
        limit_type=OCRLimits
        limit_extras={'max_frame_pixels':settings.document_ocr_max_frame_pixels,
            'max_total_pixels':settings.document_ocr_max_total_pixels,'max_raster_pixels':settings.document_ocr_max_raster_pixels,
            'max_document_seconds':settings.document_ocr_document_seconds}
    return profile_type(language=language, code_hash=probe['code_hash'], runtime_versions=probe['runtime_versions'],
        **extras,
        **options.model_dump(), limits=limit_type(**limit_extras,max_input_bytes=settings.max_package_bytes,
            max_pages=settings.max_package_pages, max_output_bytes=min(16_000_000, settings.document_runner_output_bytes)),
        runner_policy=RunnerPolicy(cpu_seconds=settings.document_runner_cpu_seconds,
            memory_bytes=settings.document_runner_memory_bytes, process_limit=settings.document_runner_process_limit,
            file_limit=settings.document_runner_file_limit, file_bytes=settings.document_runner_file_bytes,
            output_bytes=settings.document_runner_output_bytes, timeout_seconds=settings.document_runner_timeout_seconds))


def artifact_scope(org, project, artifact):
    return {'organization_id':org, 'project_id':project, 'artifact_id':artifact, 'object_kind':'normalized'}


def public_artifact(row):
    fields = ('id','document_id','file_hash','profile_hash','status','format','block_count','physical_pages','coverage_summary')
    return {**{key:getattr(row,key) for key in fields}, 'attempt':row.run_attempt,
            'created_at':row.created_at.isoformat(), 'analysis_allowed':False}


def _cache_key(doc, profile):
    # TXT/CSV share bytes but not record semantics. MIME and the adapter hint
    # are frozen original metadata; filenames themselves are never citations.
    suffix = PurePosixPath(doc['name'].replace('\\','/')).suffix.lower()
    dispatch = suffix if doc['mime_type'] in {'text/plain','application/octet-stream'} else doc['mime_type']
    return digest({'source_hash':doc['file_hash'], 'profile':profile.model_dump(mode='json'),
                   'mime_type':doc['mime_type'], 'dispatch':dispatch})


class DocumentService:
    def __init__(self, engine, settings, *, packages=None, parser=None, readiness=None, clock=utcnow):
        if settings.environment != 'test' and (parser is not None or readiness is not None or clock is not utcnow):
            raise ValueError('Document adapter injection requires the explicit test environment')
        self.engine, self.settings, self.clock = engine, settings, clock
        self.packages = packages or PackageService(engine, settings)
        self.parser, self.readiness = parser or run_document, readiness or ensure_runner_available

    def start(self, org, actor, project, package, key, options=None, *, refresh_auth=None):
        if not isinstance(key,str) or not 1 <= len(key) <= 255 or any(ord(c)<32 for c in key):
            raise ValidationError('Idempotency-Key is required')
        options = NormalizationOptions.model_validate(options or {})
        with authorized(self.engine,org,actor,project,mutate=True) as (_,repo):
            nested_package(repo,project,package)
        probe = self.readiness(self.settings)  # No SQL lock survives OS work.
        if refresh_auth:
            refresh_auth()
        with authorized(self.engine,org,actor,project,mutate=True) as (db,repo):
            original = nested_package(repo,project,package)
            profile = processing_profile(self.settings,repo._get(Project,project).report_language,options.model_dump(),probe=probe)
            values = profile.model_dump(mode='json')
            job = repo.create_job(STAGE,project,{'package_id':package,'manifest_hash':digest(original.manifest),
                'actor_id':actor,'profile':values,'profile_hash':digest(values)},key)
            return {'job':public_job(job),'profile_hash':digest(values),'analysis_allowed':False}

    def normalizations(self, org, actor, project, package):
        with authorized(self.engine,org,actor,project) as (db,repo):
            nested_package(repo,project,package)
            rows = db.scalars(repo._query(Job).where(Job.project_id==project,Job.stage==STAGE).order_by(Job.created_at,Job.id))
            return [{'job':public_job(row),'profile_hash':row.payload['profile_hash'],'analysis_allowed':False}
                    for row in rows if row.payload.get('package_id')==package]

    def artifacts(self, org, actor, project, package, job_id):
        with authorized(self.engine,org,actor,project) as (db,repo):
            nested_package(repo,project,package)
            job = repo._get(Job,job_id)
            if job.project_id != project or job.stage != STAGE or job.payload.get('package_id') != package:
                raise NotFound()
            rows = list(db.scalars(repo._query(NormalizedArtifact).where(NormalizedArtifact.job_id==job_id,
                NormalizedArtifact.package_id==package).order_by(NormalizedArtifact.created_at,NormalizedArtifact.id)))
            return {'job':public_job(job),'artifacts':[public_artifact(row) for row in rows],'analysis_allowed':False}

    def _artifact(self, db, repo, project, package, artifact):
        nested_package(repo,project,package)
        row = repo._get(NormalizedArtifact,artifact)
        if row.project_id != project or row.package_id != package:
            raise NotFound()
        doc = repo._get(Document,row.document_id)
        if doc.package_id != package or doc.file_hash != row.file_hash:
            raise AuthenticationFailure()
        return row

    def _decode(self, org, project, row):
        self.packages._configured()
        ciphertext = self.packages.store.get(row.object_key)
        plaintext = self.packages.cipher.decrypt(ciphertext,row.envelope,artifact_scope(org,project,row.id),row.content_size)
        if hashlib.sha256(plaintext).hexdigest() != row.content_hash:
            raise AuthenticationFailure()
        try:
            payload = parse_document(plaintext)
        except ValueError:
            raise AuthenticationFailure() from None
        if payload.content_sha256 != row.file_hash or digest(payload.profile.model_dump(mode='json')) != row.profile_hash:
            raise AuthenticationFailure()
        return payload

    def _read(self, org, actor, project, package, artifact, refresh_auth=None):
        with authorized(self.engine,org,actor,project) as (db,repo):
            row = self._artifact(db,repo,project,package,artifact)
            public = public_artifact(row)
        payload = self._decode(org,project,row)
        if refresh_auth:
            refresh_auth()
        with authorized(self.engine,org,actor,project) as (db,repo):
            self._artifact(db,repo,project,package,artifact)
        return public,payload

    def detail(self, org, actor, project, package, artifact, *, refresh_auth=None):
        row,payload = self._read(org,actor,project,package,artifact,refresh_auth)
        return {'artifact':row,'profile':payload.profile.model_dump(mode='json'),
            'schema_version':payload.schema_version,
            'coverage':[entry.model_dump(mode='json') for entry in payload.coverage],
            'warnings':list(payload.warnings),'analysis_allowed':False}

    def blocks(self, org, actor, project, package, artifact, offset=0, limit=100, *, refresh_auth=None):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValidationError('Block pagination is bounded')
        row,payload = self._read(org,actor,project,package,artifact,refresh_auth)
        return {'artifact_id':artifact,'document_id':row['document_id'],'file_hash':row['file_hash'],
            'schema_version':payload.schema_version,
            'blocks':[block.model_dump(mode='json') for block in payload.blocks[offset:offset+limit]],
            'total':len(payload.blocks),'offset':offset,'limit':limit,
            'coverage':[entry.model_dump(mode='json') for entry in payload.coverage],'analysis_allowed':False}

    def block(self, org, actor, project, package, artifact, block_id, *, refresh_auth=None):
        if not isinstance(block_id,str) or len(block_id)>80:
            raise NotFound()
        row,payload = self._read(org,actor,project,package,artifact,refresh_auth)
        block = next((block for block in payload.blocks if block.id==block_id),None)
        if block is None:
            raise NotFound()
        return {'artifact_id':artifact,'document_id':row['document_id'],'file_hash':row['file_hash'],
                'schema_version':payload.schema_version,
                'block':block.model_dump(mode='json'),'analysis_allowed':False}

    def _worker_inputs(self, context):
        org = context.service.organization_id
        with tenant_transaction(self.engine,org) as db:
            job = db.get(Job,context.job_id)
            project, actor, package = job.project_id,context.payload.get('actor_id'),context.payload.get('package_id')
        with authorized(self.engine,org,actor,project,mutate=True) as (db,repo):
            original = nested_package(repo,project,package)
            if digest(original.manifest) != context.payload.get('manifest_hash'):
                raise ValidationError('Frozen package input changed')
            profile = parse_profile(context.payload['profile'])
            if digest(profile.model_dump(mode='json')) != context.payload.get('profile_hash'):
                raise ValidationError('Frozen parser profile changed')
            docs = [self.packages._document_metadata(db,repo,project,package,row.id) for row in db.scalars(
                repo._query(Document).where(Document.package_id==package,Document.provenance_role=='document')
                .order_by(Document.relative_path,Document.id))]
        current = processing_profile(self.settings,profile.language,{'encoding':profile.encoding,
            'csv_delimiter':profile.csv_delimiter},probe=self.readiness(self.settings))
        if current != profile:
            raise ValidationError('Parser profile changed; start a new explicit normalization')
        return org,project,actor,package,profile,docs

    def _cancelled(self, context, actor):
        last, cached = [0.0],[False]
        def check():
            now = time.monotonic()
            if now-last[0] < .25:
                return cached[0]
            last[0] = now
            with tenant_transaction(self.engine,context.service.organization_id) as db:
                job = db.get(Job,context.job_id)
                member = db.scalar(select(Membership).where(Membership.organization_id==context.service.organization_id,Membership.user_id==actor))
                user = db.get(User,actor)
                cached[0] = (job is None or job.status!='running' or job.lease_token!=context.claim.token
                    or job.lease_expires_at<=self.clock() or not member or member.role=='viewer' or not user or not user.active)
            return cached[0]
        return check

    def _cached(self, org, actor, project, package, doc, profile, cache_key):
        with authorized(self.engine,org,actor,project,mutate=True) as (db,repo):
            current = repo._get(Document,doc['id'])
            if current.package_id!=package or current.file_hash!=doc['file_hash']:
                raise NotFound()
            cached = db.scalar(repo._query(NormalizedArtifact).where(NormalizedArtifact.project_id==project,
                NormalizedArtifact.cache_key==cache_key,NormalizedArtifact.cache_eligible.is_(True))
                .order_by(NormalizedArtifact.created_at.desc(),NormalizedArtifact.id).limit(1))
        if cached is None:
            return None
        payload = self._decode(org,project,cached)
        self.packages.authorize(org,actor,project,mutate=True)
        if payload.content_sha256!=doc['file_hash'] or payload.profile!=profile:
            raise AuthenticationFailure()
        values = payload.model_dump(mode='json')
        old_name = values['filename']
        values['filename'],values['mime_type'] = doc['name'],doc['mime_type']
        for item in values['coverage']:
            if item['anchor']['kind']=='part' and item['anchor']['part']==old_name:
                item['anchor']['part']=doc['name']
        return parse_document(canonical_json(values))

    def _failure(self, doc, profile, code):
        model=NormalizedDocumentV2 if isinstance(profile,OCRProfile) else NormalizedDocument
        coverage=CoverageV2 if model is NormalizedDocumentV2 else CoverageEntry
        return model(content_sha256=doc['file_hash'],filename=doc['name'],mime_type=doc['mime_type'],
            format='unsupported',profile=profile,status='partial',blocks=(),physical_pages=None,analysis_allowed=False,
            coverage=(coverage(id='coverage:failed',kind='unknown',state='failed',anchor=PartAnchor(part=doc['name']),reason=code),),
            warnings=('native_parser_failed',))

    def handle(self, context):
        org,project,actor,package,profile,docs = self._worker_inputs(context)
        cancelled = self._cancelled(context,actor)
        failed = False
        results = []
        known_pages = 0
        for index,doc in enumerate(docs):
            if cancelled():
                raise LeaseLost()
            cache_key = _cache_key(doc,profile)
            step = 'doc:'+doc['id']
            with tenant_transaction(self.engine,org) as db:
                history = list(db.scalars(select(JobCheckpoint).where(JobCheckpoint.organization_id==org,
                    JobCheckpoint.job_id==context.job_id,JobCheckpoint.step.in_([step]+[
                        step+':a'+str(attempt) for attempt in range(1,context.claim.attempt+1)]))
                    .order_by(JobCheckpoint.created_at.desc(),JobCheckpoint.id)))
            healthy=next((checkpoint for checkpoint in history if not checkpoint.result.get('failed')),None)
            if healthy:
                result = context.checkpoint(healthy.step,cache_key,{},progress=int((index+1)*95/max(1,len(docs))))
            else:
                if history:
                    step=step+':a'+str(context.claim.attempt)
                    current=next((checkpoint for checkpoint in history if checkpoint.step==step),None)
                    if current:
                        result=context.checkpoint(step,cache_key,{},progress=int((index+1)*95/max(1,len(docs))))
                        results.append(result);failed=failed or result.get('failed',False)
                        continue
                payload = self._cached(org,actor,project,package,doc,profile,cache_key)
                if payload is None:
                    original = self.packages.read_original(org,project,doc)
                    self.packages.authorize(org,actor,project,mutate=True)
                    if cancelled():
                        raise LeaseLost()
                    try:
                        payload = self.parser(original,doc['name'],doc['mime_type'],profile,self.settings,cancelled)
                    except DocumentRunnerError as exc:
                        if exc.code=='cancelled':
                            raise LeaseLost() from None
                        if exc.code in {'runner_disabled','runner_unavailable','cleanup_failed'}:
                            raise
                        payload = self._failure(doc,profile,exc.code)
                    finally:
                        original = None
                failed = failed or any(entry.state=='failed' for entry in payload.coverage)
                if payload.physical_pages is not None and known_pages+payload.physical_pages>profile.limits.max_pages:
                    raise ValidationError('Package cumulative physical pages exceed frozen policy')
                result = self._publish(context,org,project,actor,package,doc,profile,cache_key,payload,step=step,
                    progress=int((index+1)*95/max(1,len(docs))))
            results.append(result)
            pages=result.get('physical_pages')
            if pages is None and result.get('artifact_id'):
                with authorized(self.engine,org,actor,project,mutate=True) as (db,repo):
                    pages=self._artifact(db,repo,project,package,result['artifact_id']).physical_pages
            known_pages+=pages or 0
            if known_pages>profile.limits.max_pages:
                raise ValidationError('Package cumulative physical pages exceed frozen policy')
            failed = failed or result.get('failed',False)
        context.checkpoint('normalized:a'+str(context.claim.attempt),context.payload['profile_hash'],{'artifacts':results,'analysis_allowed':False},progress=100)
        if failed:
            raise ValidationError('One or more native documents failed; review their coverage')

    def _publish(self, context, org, project, actor, package, doc, profile, cache_key, payload, *, step, progress):
        if payload.content_sha256!=doc['file_hash'] or payload.profile!=profile or payload.filename!=doc['name'] or payload.mime_type!=doc['mime_type']:
            raise AuthenticationFailure()
        self.packages._configured()
        artifact_id,attempt_id,token = str(uuid4()),str(uuid4()),secrets.token_hex(32)
        plaintext = payload.model_dump_json().encode()
        if len(plaintext)>min(profile.limits.max_output_bytes,16_777_216):
            raise ValidationError('Artifact output exceeds its frozen budget')
        encrypted,envelope = self.packages.cipher.encrypt(plaintext,artifact_scope(org,project,artifact_id))
        content_hash,size = hashlib.sha256(plaintext).hexdigest(),len(plaintext)
        plaintext = None
        key = f'org/{org}/project/{project}/artifact-attempt/{token}/{artifact_id}'
        self.packages.authorize(org,actor,project,mutate=True)
        with context.service._transaction() as db:
            context.service._fence(db,context.job_id,context.claim.token)
            db.add(ArtifactUploadAttempt(id=attempt_id,organization_id=org,project_id=project,job_id=context.job_id,
                package_id=package,document_id=doc['id'],actor_id=actor,artifact_id=artifact_id,attempt_token=token,
                lease_token=context.claim.token,object_key=key,content_size=size,content_hash=content_hash,envelope=envelope,
                expires_at=self.clock()+timedelta(minutes=3),status='uploading'))
        result = {'artifact_id':artifact_id,'document_id':doc['id'],'status':payload.status,
                  'physical_pages':payload.physical_pages,
                  'failed':any(entry.state=='failed' for entry in payload.coverage)}
        def write(writes):
            attempt = writes.get(ArtifactUploadAttempt,attempt_id)
            if attempt.status!='uploading' or attempt.expires_at<=self.clock() or attempt.attempt_token!=token or attempt.lease_token!=context.claim.token:
                raise LeaseLost()
            writes.add(NormalizedArtifact(id=artifact_id,organization_id=org,project_id=project,job_id=context.job_id,
                package_id=package,document_id=doc['id'],attempt_id=attempt_id,run_attempt=context.claim.attempt,actor_id=actor,file_hash=doc['file_hash'],
                profile_hash=context.payload['profile_hash'],profile=profile.model_dump(mode='json'),cache_key=cache_key,
                cache_eligible=payload.status!='unavailable' and not result['failed'],object_key=key,content_size=size,
                content_hash=content_hash,envelope=envelope,status=payload.status,format=payload.format,
                block_count=len(payload.blocks),physical_pages=payload.physical_pages,
                coverage_summary=dict(Counter(entry.state for entry in payload.coverage))))
            attempt.status='committed'
        try:
            self.packages.store.put(key,encrypted)
            self.packages.authorize(org,actor,project,mutate=True)
            saved = context.checkpoint(step,cache_key,result,write,progress=progress)
            if saved.get('artifact_id')!=artifact_id:
                self.reconcile_attempt(org,attempt_id,token,force=True)
            return saved
        except BaseException:
            committed = self.reconcile_attempt(org,attempt_id,token,force=True)
            if committed is not None:
                return committed
            raise

    def reconcile_attempt(self, org, attempt_id, token=None, *, force=False, raise_on_failure=False):
        try:
            with tenant_transaction(self.engine,org) as db:
                attempt = db.scalar(select(ArtifactUploadAttempt).where(ArtifactUploadAttempt.organization_id==org,
                    ArtifactUploadAttempt.id==attempt_id).with_for_update())
                if not attempt or (token is not None and attempt.attempt_token!=token):
                    return None
                artifact = db.scalar(select(NormalizedArtifact).where(NormalizedArtifact.organization_id==org,
                    NormalizedArtifact.attempt_id==attempt.id))
                if artifact is not None:
                    return {'artifact_id':artifact.id,'document_id':artifact.document_id,'status':artifact.status,
                            'failed':artifact.coverage_summary.get('failed',0)>0}
                if attempt.status=='committed' or (attempt.status=='uploading' and not force and attempt.expires_at>self.clock()):
                    return None
                expected = f'org/{org}/project/{attempt.project_id}/artifact-attempt/{attempt.attempt_token}/{attempt.artifact_id}'
                if attempt.object_key!=expected:
                    raise StorageUnavailable()
                key = attempt.object_key
                references = db.scalar(select(func.count()).select_from(Document).where(Document.organization_id==org,Document.object_key==key))
                references += db.scalar(select(func.count()).select_from(NormalizedArtifact).where(NormalizedArtifact.organization_id==org,NormalizedArtifact.object_key==key))
                if references:
                    return None
                attempt.status='abandoned'; attempt.cleanup_attempts+=1; attempt.last_cleanup_at=self.clock()
            self.packages._configured()
            self.packages.store.delete(key)
            with tenant_transaction(self.engine,org) as db:
                attempt = db.scalar(select(ArtifactUploadAttempt).where(ArtifactUploadAttempt.organization_id==org,
                    ArtifactUploadAttempt.id==attempt_id).with_for_update())
                if attempt and attempt.status=='abandoned':
                    attempt.status='cleaned'
        except Exception:
            if raise_on_failure:
                raise StorageUnavailable() from None
            return None
        return None

    def reconcile(self, org, *, limit=100):
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError('Artifact reconciliation scan must be bounded')
        with tenant_transaction(self.engine,org) as db:
            identities = list(db.scalars(select(ArtifactUploadAttempt.id).where(ArtifactUploadAttempt.organization_id==org,
                ArtifactUploadAttempt.status!='committed',ArtifactUploadAttempt.expires_at<=self.clock())
                .order_by(ArtifactUploadAttempt.cleanup_attempts,ArtifactUploadAttempt.created_at,ArtifactUploadAttempt.id).limit(limit)))
        for identity in identities:
            self.reconcile_attempt(org,identity,raise_on_failure=True)
        return len(identities)


def register_handlers(registry, settings):
    if settings.document_runner_mode=='disabled':
        return
    try:
        ensure_runner_available(settings)
    except DocumentRunnerError:
        return  # A configured-but-unavailable runner never enables a host parser.
    def handle(context):
        from app.jobs.worker import TransientInfrastructureFailure
        try:
            DocumentService(context.service.engine,settings).handle(context)
        except (StorageUnavailable,DocumentRunnerError):
            raise TransientInfrastructureFailure() from None
    registry.register(STAGE,handle)
