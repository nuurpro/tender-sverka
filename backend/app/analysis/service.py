"""Frozen source extraction, durable accounting, optimistic fenced publication."""
import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from typing import NamedTuple,Iterator
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select
from app.documents.service import DocumentService,digest
from app.jobs.service import public_job,LeaseLost
from app.packages.service import authorized,nested_package
from app.workspace.database import tenant_transaction
from app.workspace.models import (OrganizationAIPolicy,AnalysisRun,AnalysisCall,ParticipantFact,Job,JobCheckpoint,
    NormalizedArtifact,Document,Project,ChecklistVersion,Requirement,Membership,User,Organization)
from app.workspace.repository import Conflict,NotFound,ValidationError,snapshot,canonical_json,MAX_CHECKLIST_REQUIREMENTS
from .schemas import AnalysisOptions,GatewayProfile,Extraction,ExtractedRequirement,BidFact
from .evidence import bind_blocks,iter_chunk_blocks,resolve_citation,reference_plan,slice_block,ReferenceLink,ReferenceLimitExceeded
from .gateway import Gateway,GatewayFailure,system_prompt
from .budgets import BudgetLedger,BudgetExceeded

STAGE='analysis.extract'
MAX_PLANNING_ATTEMPTS=500
MAX_PLANNING_TOKENS=20_000_000


class ReferenceRequest(NamedTuple):
    step: str
    blocks: list[dict]
    content: str


@dataclass(frozen=True)
class Planning:
    chunks: list[list[dict]]
    links: list[ReferenceLink]
    targets: dict[str,list[dict]]
    input_upper: int
    reference_upper: int
    reference_count: int
    request_count: int
    max_provider_attempts: int
    worst_case_tokens: int


def current_policy(repo):
    row=repo.session.scalar(repo._query(OrganizationAIPolicy).order_by(OrganizationAIPolicy.version.desc()).limit(1))
    return {'allowed':row.allowed,'version':row.version} if row else {'allowed':False,'version':0}


def update_policy(repo,allowed,expected_version,reason):
    member=repo.session.scalar(repo._query(Membership).where(Membership.user_id==repo.actor_id).with_for_update())
    if not member or member.role!='admin':
        from fastapi import HTTPException
        raise HTTPException(403,'Требуются права администратора.')
    repo.session.scalar(select(Organization).where(Organization.id==repo.organization_id).with_for_update())
    policy=current_policy(repo)
    if policy['version']!=expected_version:raise Conflict()
    if type(allowed) is not bool or not isinstance(reason,str) or not reason.strip():raise ValidationError('Policy reason required')
    row=OrganizationAIPolicy(organization_id=repo.organization_id,version=expected_version+1,allowed=allowed,actor_id=repo.actor_id,reason=reason.strip())
    repo.session.add(row);repo.session.flush()
    repo._audit('organization.ai_policy_changed',row,before=policy,after={'allowed':allowed,'version':row.version},reason=reason.strip())
    return {'allowed':allowed,'version':row.version}


def _profile(settings):
    direct=settings.analysis_provider=='anthropic'
    if not direct and settings.gateway_api_style!='chat':raise ValidationError('Analysis requires configured Chat Completions JSON protocol')
    return GatewayProfile(provider=settings.analysis_provider,
        base_url='https://api.anthropic.com' if direct else settings.gateway_api_base,
        model=settings.anthropic_model if direct else settings.gateway_model,
        protocol='anthropic-messages-json-v1' if direct else 'chat-json-v1',
        context_tokens=settings.analysis_context_tokens,output_tokens=settings.analysis_output_tokens,
        timeout_seconds=settings.analysis_timeout_seconds,max_attempts=settings.analysis_max_attempts,
        input_price_per_million=settings.analysis_input_price_per_million,output_price_per_million=settings.analysis_output_price_per_million,
        pricing_version=settings.analysis_pricing_version)


def _content(blocks,kind,language,deadline,context=None,*,timezone=None):
    return canonical_json({'kind':kind,'language':language,'deadline':deadline,'timezone':timezone,
        'blocks':[{'evidence_id':b['evidence_id'],'text':b['text'],'quote_kind':b['quote_kind'],'review_flags':b['review_flags']} for b in blocks],
        'package_context':context or []})


def _token_upper(content,kind,profile):
    # UTF-8 byte length bounds text tokens without guessing model tokenizer.
    # Structural chat framing has an explicit conservative reserve.
    return len((system_prompt(kind,profile)+content).encode('utf-8'))+256


class AnalysisService:
    def __init__(self,engine,settings,*,documents=None,gateway=None):
        if settings.environment!='test' and (documents is not None or gateway is not None):raise ValueError('Synthetic adapters require environment=test')
        self.engine,self.settings=engine,settings
        self.documents=documents or DocumentService(engine,settings)
        self.gateway=gateway

    def _freeze(self,org,actor,project,package,options):
        with authorized(self.engine,org,actor,project,mutate=True) as (db,repo):
            original=nested_package(repo,project,package)
            job=repo._get(Job,options.normalization_job_id)
            if job.project_id!=project or job.stage!='package.normalize' or job.payload.get('package_id')!=package:raise NotFound()
            if job.status not in {'succeeded','failed'} or options.normalization_attempt>job.attempt:raise ValidationError('Normalization must be terminal; running and cancelled sources cannot be used')
            final=db.scalar(repo._query(JobCheckpoint).where(JobCheckpoint.job_id==job.id,JobCheckpoint.step=='normalized:a'+str(options.normalization_attempt)))
            if final is None and (job.status!='failed' or options.normalization_attempt!=job.attempt):raise ValidationError('Requested normalization attempt is not a completed snapshot')
            documents=list(db.scalars(repo._query(Document).where(Document.package_id==package).order_by(Document.relative_path,Document.id)))
            checkpoints=list(db.scalars(repo._query(JobCheckpoint).where(JobCheckpoint.job_id==job.id)))
            accepted={r['document_id']:r['artifact_id'] for r in final.result['artifacts']} if final else {}
            if not final:
                for doc in documents:
                    candidates=[]
                    for cp in checkpoints:
                        if cp.step=='doc:'+doc.id:attempt=1
                        elif cp.step.startswith('doc:'+doc.id+':a'):
                            try:attempt=int(cp.step.rsplit(':a',1)[1])
                            except ValueError:continue
                        else:continue
                        if attempt<=options.normalization_attempt:candidates.append((attempt,cp))
                    healthy=[r for r in candidates if not r[1].result.get('failed')]
                    if healthy or candidates:
                        chosen=max(healthy or candidates,key=lambda r:r[0])[1]
                        if chosen.result.get('artifact_id'):accepted[doc.id]=chosen.result['artifact_id']
            artifacts=[];coverage=[]
            for doc in documents:
                if doc.provenance_role=='archive':continue
                identity=accepted.get(doc.id)
                if not identity:
                    coverage.append({'document_id':doc.id,'filename':doc.name,'state':'unavailable','reason':'no_published_artifact'})
                    continue
                artifact=self.documents._artifact(db,repo,project,package,identity)
                if artifact.job_id!=job.id or artifact.document_id!=doc.id or artifact.run_attempt>options.normalization_attempt:raise ValidationError('Artifact does not belong to frozen attempt')
                artifacts.append({'id':artifact.id,'document_id':doc.id,'filename':doc.name,'file_hash':artifact.file_hash,
                    'content_hash':artifact.content_hash,'profile_hash':artifact.profile_hash,'profile':artifact.profile,
                    'run_attempt':artifact.run_attempt,'status':artifact.status,'normalization_job_id':job.id})
            source_project=repo.get_project(project)
            frozen={'package_id':package,'package_version':original.version,'manifest':copy.deepcopy(original.manifest),
                'manifest_hash':digest(original.manifest),'kind':original.kind,'participant_id':original.participant_id,
                'normalization_job_id':job.id,'normalization_attempt':options.normalization_attempt,
                'normalization_profile_hash':job.payload['profile_hash'],'artifacts':artifacts,
                'language':source_project.report_language,'deadline':source_project.deadline.isoformat() if source_project.deadline else None,
                'timezone':source_project.timezone,'coverage':coverage,'profile':_profile(self.settings).model_dump(mode='json'),
                'budget':options.budget.model_dump(mode='json'),'source_policy':'declaration-is-not-proof-v1'}
            policy=current_policy(repo)
        blocks=[]
        for artifact in artifacts:
            _,payload=self.documents._read(org,actor,project,package,artifact['id'])
            blocks+=bind_blocks(artifact['id'],artifact['document_id'],artifact['filename'],[b.model_dump(mode='json') for b in payload.blocks])
            frozen['coverage'] += [{'artifact_id':artifact['id'],'document_id':artifact['document_id'],'filename':artifact['filename'],**c.model_dump(mode='json')} for c in payload.coverage]
            frozen['coverage'] += [{'artifact_id':artifact['id'],'document_id':artifact['document_id'],'filename':artifact['filename'],'state':'warning','reason':warning} for warning in payload.warnings]
        frozen['coverage'] += [{'state':'unavailable','reason':'archive_member_skipped','detail':entry} for entry in original.manifest.get('issues',[])]
        return frozen,blocks,policy

    def _chunks(self,frozen,blocks):
        return list(self._iter_chunks(frozen,blocks))

    def _iter_chunks(self,frozen,blocks):
        profile=GatewayProfile.model_validate(frozen['profile'])
        overhead=_token_upper(_content([],frozen['kind'],frozen['language'],frozen['deadline'],timezone=frozen['timezone']),frozen['kind'],profile)
        capacity=profile.context_tokens-profile.output_tokens-overhead-1024
        if capacity<1:raise ValidationError('Profile context cannot hold extraction schema')
        count=0;worst=0
        for chunk in iter_chunk_blocks(blocks,max(1,capacity//12)):
            upper=_token_upper(_content(chunk,frozen['kind'],frozen['language'],frozen['deadline'],timezone=frozen['timezone']),frozen['kind'],profile)
            if upper+profile.output_tokens>profile.context_tokens:raise ValidationError('Evidence block framing exceeds context profile')
            count+=1;worst+=(upper+profile.output_tokens)*profile.max_attempts
            self._planning_limits(count*profile.max_attempts,worst)
            yield chunk

    @staticmethod
    def _planning_limits(attempts,tokens):
        if attempts>MAX_PLANNING_ATTEMPTS:raise ValidationError('Analysis planning attempt limit exhausted (500 supported provider attempts)')
        if tokens>MAX_PLANNING_TOKENS:raise ValidationError('Analysis planning token limit exhausted (20000000 supported worst-case tokens)')

    @staticmethod
    def _reference_index(blocks):
        try:return reference_plan(blocks,max_links=MAX_PLANNING_ATTEMPTS,max_target_bindings=MAX_PLANNING_ATTEMPTS)
        except ReferenceLimitExceeded:raise ValidationError('Analysis planning reference graph attempt limit exhausted (500 supported reference bindings)') from None

    def _planning(self,frozen,blocks):
        profile=GatewayProfile.model_validate(frozen['profile'])
        chunks=[];upper=0;worst=0;count=0
        for chunk in self._iter_chunks(frozen,blocks):
            input_upper=_token_upper(_content(chunk,frozen['kind'],frozen['language'],frozen['deadline'],timezone=frozen['timezone']),frozen['kind'],profile)
            count+=1;upper+=input_upper;worst+=(input_upper+profile.output_tokens)*profile.max_attempts
            self._planning_limits(count*profile.max_attempts,worst)
            chunks.append(chunk)
        links,targets=self._reference_index(blocks)
        references=0;reference_upper=0
        for request in self._iter_references(frozen,blocks,links,targets):
            input_upper=_token_upper(request.content,frozen['kind'],profile)
            references+=1;count+=1;reference_upper+=input_upper;worst+=(input_upper+profile.output_tokens)*profile.max_attempts
            self._planning_limits(count*profile.max_attempts,worst)
        return Planning(chunks,links,targets,upper,reference_upper,references,count,count*profile.max_attempts,worst)

    def preflight(self,org,actor,project,package,options):
        options=AnalysisOptions.model_validate(options)
        frozen,blocks,policy=self._freeze(org,actor,project,package,options)
        profile=GatewayProfile.model_validate(frozen['profile']);plan=self._planning(frozen,blocks)
        warnings=[c for c in frozen['coverage'] if c.get('state') not in {'text_extracted','extracted','corrected'}]
        return {'external_ai_allowed':policy['allowed'],'policy_version':policy['version'],'frozen_identity':digest(frozen),
            'input_token_upper_bound':plan.input_upper,'package_text_token_upper_bound':sum(len(b['text'].encode()) for b in blocks),
            'request_count':plan.request_count,'extraction_chunk_count':len(plan.chunks),'reference_lookup_count':plan.reference_count,
            'reference_links':plan.links,'max_provider_attempts':plan.max_provider_attempts,
            'context_tokens':profile.context_tokens,'output_tokens':profile.output_tokens,'budget':frozen['budget'],
            'worst_case_run_tokens':plan.worst_case_tokens,
            'pricing_known':profile.input_price_per_million is not None and profile.output_price_per_million is not None,
            'coverage_warnings':warnings,'artifacts':frozen['artifacts']}

    def _references(self,frozen,blocks) -> tuple[list[ReferenceLink],Iterator[ReferenceRequest]]:
        links,targets=self._reference_index(blocks)
        return links,self._iter_references(frozen,blocks,links,targets)

    def _iter_references(self,frozen,blocks,links,targets) -> Iterator[ReferenceRequest]:
        by_id={b['evidence_id']:b for b in blocks}
        profile=GatewayProfile.model_validate(frozen['profile']);count=0;worst=0
        for link_index,link in enumerate(links):
            if link['state']!='resolved':continue
            original=by_id[link['source_id']]
            source=slice_block(original,max(0,link['source_start']-200),min(len(original['text']),link['source_end']+200))
            for fragment_index,chunk in enumerate(self._iter_chunks(frozen,targets[link['target_ids'][0]])):
                relevant=[source]+chunk
                content=_content(relevant,frozen['kind'],frozen['language'],frozen['deadline'],
                    [{'instruction':'Resolve this explicit reference using exact source and target section fragments. Extract atomic referenced obligations/facts. Other section fragments are inspected in separate bounded requests.',
                      'reference':link['reference'],'fragment_index':fragment_index}],timezone=frozen['timezone'])
                upper=_token_upper(content,frozen['kind'],profile)
                if upper+profile.output_tokens>profile.context_tokens:
                    raise ValidationError('Reference framing exceeds configured context')
                count+=1;worst+=(upper+profile.output_tokens)*profile.max_attempts
                self._planning_limits(count*profile.max_attempts,worst)
                yield ReferenceRequest('reference:'+str(link_index)+':'+str(fragment_index),relevant,content)

    def start(self,org,actor,project,package,key,options,*,refresh_auth=None):
        options=AnalysisOptions.model_validate(options)
        # Replay resolves stored immutable output before expensive evidence reads.
        with authorized(self.engine,org,actor,project,mutate=True) as (db,repo):
            existing=db.scalar(repo._query(Job).where(Job.project_id==project,Job.idempotency_key==key))
            if existing:
                if existing.stage!=STAGE or existing.payload.get('package_id')!=package or existing.payload.get('options')!=options.model_dump(mode='json'):raise Conflict()
                run=db.scalar(repo._query(AnalysisRun).where(AnalysisRun.job_id==existing.id))
                if run is None:raise Conflict()
                return {'job':public_job(existing),'run':self._public_run(run)}
        frozen,blocks,policy=self._freeze(org,actor,project,package,options)
        if not blocks:raise ValidationError('No available evidence text')
        self._planning(frozen,blocks)
        profile=GatewayProfile.model_validate(frozen['profile'])
        if not policy['allowed']:raise ValidationError('Organization external AI permission is disabled')
        if self.gateway is None and not self.settings.gateway_api_key.get_secret_value():raise ValidationError('AgentRouter is not configured')
        # Cost ceilings are enforceable only with explicit configured prices.
        if (profile.input_price_per_million is None or profile.output_price_per_million is None) and options.budget.cost_ceiling is not None:
            # Unknown pricing is visible, but a positive hard ceiling cannot be certified.
            raise ValidationError('A monetary ceiling requires configured verified pricing; token budgets remain enforced')
        if refresh_auth:refresh_auth()
        with authorized(self.engine,org,actor,project,mutate=True) as (db,repo):
            current=current_policy(repo)
            if not current['allowed'] or current['version']!=policy['version']:raise ValidationError('Policy changed during preparation')
            original=nested_package(repo,project,package)
            if digest(original.manifest)!=frozen['manifest_hash']:raise Conflict()
            existing=db.scalar(repo._query(Job).where(Job.project_id==project,Job.idempotency_key==key))
            if existing:raise Conflict()
            run_id=str(uuid4())
            checklist=repo.create_checklist_draft(project) if frozen['kind']=='tender' else None
            job=repo.create_job(STAGE,project,{'run_id':run_id,'actor_id':actor,'package_id':package,'options':options.model_dump(mode='json'),'frozen_hash':digest(frozen)},key)
            run=AnalysisRun(id=run_id,organization_id=org,project_id=project,job_id=job.id,package_id=package,
                participant_id=frozen['participant_id'],checklist_id=checklist.id if checklist else None,actor_id=actor,
                expected_revision=1,policy_version=current['version'],frozen=frozen,cache_key=digest({'org':org,'project':project,'frozen':frozen}),status='draft',result={})
            db.add(run);db.flush();repo._audit('analysis.started',run,project_id=project,after=self._public_run(run))
            return {'job':public_job(job),'run':self._public_run(run)}

    def _public_run(self,run):
        return {key:copy.deepcopy(getattr(run,key)) for key in ('id','project_id','package_id','participant_id','checklist_id','expected_revision','policy_version','status','result')} | {'created_at':run.created_at.isoformat()}

    def detail(self,org,actor,project,run_id):
        with authorized(self.engine,org,actor,project) as (db,repo):
            run=repo._get(AnalysisRun,run_id)
            if run.project_id!=project:raise NotFound()
            calls=list(db.scalars(repo._query(AnalysisCall).where(AnalysisCall.run_id==run.id).order_by(AnalysisCall.sequence)))
            return {'run':self._public_run(run),'job':public_job(repo.get_job(run.job_id)),
                'frozen':copy.deepcopy(run.frozen),'calls':[snapshot(c) for c in calls]}

    def history(self,org,actor,project,package):
        with authorized(self.engine,org,actor,project) as (db,repo):
            original=nested_package(repo,project,package)
            runs=list(db.scalars(repo._query(AnalysisRun).where(AnalysisRun.project_id==project,
                AnalysisRun.package_id==original.id,AnalysisRun.participant_id==original.participant_id)
                .order_by(AnalysisRun.created_at.desc(),AnalysisRun.id.desc()).limit(100)))
            return [{'run':self._public_run(run),'job':public_job(repo.get_job(run.job_id))}
                for run in runs if run.frozen['kind']==original.kind]

    def _access(self,context,run):
        with authorized(self.engine,run.organization_id,run.actor_id,run.project_id,mutate=True) as (db,repo):
            if not current_policy(repo)['allowed']:raise ValidationError('External AI permission revoked')
            job=repo._get(Job,context.job_id)
            if job.status!='running' or job.lease_token!=context.claim.token:raise LeaseLost()

    def _ledger(self,org,project,run):
        from .schemas import RunBudget
        ledger=BudgetLedger(RunBudget.model_validate(run.frozen['budget']),GatewayProfile.model_validate(run.frozen['profile']))
        with tenant_transaction(self.engine,org) as db:
            calls=list(db.scalars(select(AnalysisCall).where(AnalysisCall.organization_id==org,AnalysisCall.project_id==project,AnalysisCall.run_id==run.id).order_by(AnalysisCall.sequence)))
        for call in calls:
            reservation=ledger.reserve(call.input_upper,call.output_upper)
            ledger.reconcile(reservation,call.usage)
        return ledger

    def _bound(self,extraction,blocks,frozen=None):
        result={'requirements':[],'facts':[],'findings':[]}
        for kind in result:
            for row in getattr(extraction,kind):
                value=row.model_dump(mode='json')
                try:value['citations']=[resolve_citation(c,blocks) for c in value['citations']]
                except ValueError:
                    result['findings'].append({'kind':'invalid_evidence','detail':'Provider returned an ungrounded '+kind+' claim','citations':[],'review_flags':['invalid_evidence']})
                    continue
                value['review_flags']=sorted({f for c in value['citations'] for f in c['review_flags']})
                if any(c['quote_kind']=='ocr_transcription' for c in value['citations']):value['review_flags']=sorted(set(value['review_flags'])|{'ocr_transcription_review'})
                if kind=='findings':value['review_flags']=sorted(set(value['review_flags'])|{value['kind']+'_review'})
                predicate=value.get('predicate',{})
                if predicate.get('kind')=='date' and predicate.get('basis')=='tender_deadline' and frozen is not None:
                    deadline=frozen['deadline']
                    expected=datetime.fromisoformat(deadline).astimezone(ZoneInfo(frozen['timezone'])).date().isoformat() if deadline else None
                    if predicate['timezone']!=frozen['timezone'] or predicate['value']!=expected:
                        value['predicate']={'kind':'semantic','text':value['condition']}
                        value['review_flags']=sorted(set(value['review_flags'])|{'deadline_basis_review'})
                        result['findings'].append({'kind':'ambiguity','detail':'Deadline-relative predicate does not match the frozen deadline/timezone; retained for semantic review',
                            'citations':copy.deepcopy(value['citations']),'review_flags':['deadline_basis_review']})
                result[kind].append(value)
        return result

    def _cached(self,context,run,blocks):
        """Reuse an immutable successful extraction, never editable checklist rows."""
        with authorized(self.engine,run.organization_id,run.actor_id,run.project_id,mutate=True) as (db,repo):
            policy=current_policy(repo)
            saved=db.scalar(repo._query(JobCheckpoint).where(JobCheckpoint.job_id==run.job_id,JobCheckpoint.step=='cache'))
            if not policy['allowed'] or policy['version']!=run.policy_version:
                if saved:raise ValidationError('Cached extraction policy version changed')
                return None
            query=repo._query(AnalysisRun).join(Job,(Job.organization_id==AnalysisRun.organization_id)&(Job.id==AnalysisRun.job_id)).where(
                AnalysisRun.project_id==run.project_id,AnalysisRun.id!=run.id,AnalysisRun.cache_key==run.cache_key,
                AnalysisRun.policy_version==run.policy_version,AnalysisRun.status=='published',Job.status=='succeeded')
            if saved:query=query.where(AnalysisRun.id==saved.result['cache']['source_run_id'])
            source=db.scalar(query.order_by(AnalysisRun.created_at.desc(),AnalysisRun.id.desc()).limit(1))
            if source is None:
                if saved:raise ValidationError('Cached extraction source is unavailable')
                return None
            if canonical_json(source.frozen)!=canonical_json(run.frozen):raise ValidationError('Cache frozen identity mismatch')
            provenance={'source_run_id':source.id,'source_job_id':source.job_id,'source_result_hash':digest(source.result),
                'frozen_identity':digest(run.frozen),'policy_version':run.policy_version}
            cached={key:copy.deepcopy(source.result[key]) for key in ('requirements','facts','findings','coverage','source_policy')}
        needed={citation['evidence_id'] for key in ('requirements','facts','findings') for row in cached[key] for citation in row['citations']}
        evidence=self._cache_evidence(run.frozen,blocks,needed)
        for kind in ('requirements','facts','findings'):
            for row in cached[kind]:
                citations=row['citations']
                for citation in citations:
                    rebound=resolve_citation({'evidence_id':citation['evidence_id'],'quote':citation['quote']},[evidence[citation['evidence_id']]])
                    if canonical_json(citation)!=canonical_json(rebound):raise ValidationError('Cached source citation mismatch')
                if kind!='findings':
                    schema=ExtractedRequirement if kind=='requirements' else BidFact
                    schema.model_validate({**{key:value for key,value in row.items() if key in schema.model_fields},
                        'citations':[{'evidence_id':c['evidence_id'],'quote':c['quote']} for c in citations]})
        if (run.frozen['kind']=='tender' and cached['facts']) or (run.frozen['kind']=='bid' and cached['requirements']):
            raise ValidationError('Cached extraction kind mismatch')
        cached['cache']=provenance
        replayed=context.checkpoint('cache',digest(provenance),cached,progress=95)
        # A retry must replay this exact immutable source, not a newly found run.
        if canonical_json(replayed)!=canonical_json(cached):raise ValidationError('Cached checkpoint mismatch')
        return replayed

    def _cache_evidence(self,frozen,blocks,needed):
        """Rebuild only cited bindings, with each section inspected once.

        Source clipping and section chunk boundaries are deterministic. No
        reference request JSON or source/section cross-product is needed.
        """
        index={}
        def remember(block):
            if block['evidence_id'] in needed:index[block['evidence_id']]=block
        for block in blocks:remember(block)
        if set(index)==needed:return index
        for chunk in self._iter_chunks(frozen,blocks):
            for block in chunk:remember(block)
        if set(index)==needed:return index
        links,sections=self._reference_index(blocks)
        originals={b['evidence_id']:b for b in blocks}
        section_ids=set()
        for link in links:
            original=originals[link['source_id']]
            remember(slice_block(original,link['source_start'],link['source_end']))
            remember(slice_block(original,max(0,link['source_start']-200),min(len(original['text']),link['source_end']+200)))
            section_ids.update(link['target_ids'])
        if set(index)==needed:return index
        for identity in section_ids:
            section=sections[identity]
            for block in section:remember(block)
            for chunk in self._iter_chunks(frozen,section):
                for block in chunk:remember(block)
            if set(index)==needed:return index
        if set(index)!=needed:raise ValidationError('Cached source evidence identifier is unavailable')
        return index

    def handle(self,context):
        org=context.service.organization_id
        with tenant_transaction(self.engine,org) as db:
            run=db.scalar(select(AnalysisRun).where(AnalysisRun.organization_id==org,AnalysisRun.id==context.payload['run_id']))
            if not run or run.job_id!=context.job_id or digest(run.frozen)!=context.payload['frozen_hash']:raise ValidationError('Frozen run mismatch')
        self._access(context,run)
        frozen=run.frozen;profile=GatewayProfile.model_validate(frozen['profile'])
        blocks=[]
        for artifact in frozen['artifacts']:
            with authorized(self.engine,org,run.actor_id,run.project_id,mutate=True) as (db,repo):
                source=self.documents._artifact(db,repo,run.project_id,run.package_id,artifact['id'])
                if source.content_hash!=artifact['content_hash'] or source.file_hash!=artifact['file_hash'] or source.profile_hash!=artifact['profile_hash']:raise ValidationError('Artifact changed')
            _,payload=self.documents._read(org,run.actor_id,run.project_id,run.package_id,artifact['id'])
            blocks+=bind_blocks(artifact['id'],artifact['document_id'],artifact['filename'],[b.model_dump(mode='json') for b in payload.blocks])
        cached=self._cached(context,run,blocks)
        if cached is not None:
            self._publish(context,run,cached)
            return
        plan=self._planning(frozen,blocks);chunks=plan.chunks
        if self.gateway is None and profile.provider!=self.settings.analysis_provider:
            raise ValidationError('AI provider changed; start a new analysis with the current provider')
        key=self.settings.anthropic_api_key if profile.provider=='anthropic' else self.settings.gateway_api_key
        gateway=self.gateway or Gateway(profile,key.get_secret_value())
        extracted=[]
        for index,chunk in enumerate(chunks):
            step='extract:'+str(index)
            with tenant_transaction(self.engine,org) as db:
                saved=db.scalar(select(JobCheckpoint).where(JobCheckpoint.organization_id==org,JobCheckpoint.job_id==context.job_id,JobCheckpoint.step==step))
            if saved:
                extracted.append(context.checkpoint(step,digest(chunk),{}));continue
            content=_content(chunk,frozen['kind'],frozen['language'],frozen['deadline'],timezone=frozen['timezone'])
            result=self._call(context,run,step,content,gateway,profile)
            bound=self._bound(result,chunk,frozen)
            extracted.append(context.checkpoint(step,digest(chunk),bound,progress=int((index+1)*85/len(chunks))))
        # References use an index over the entire frozen block set, independent
        # of extraction chunks. Each exact target is interpreted with its source.
        links,targets=plan.links,plan.targets
        reference_requests=self._iter_references(frozen,blocks,links,targets)
        by_id={b['evidence_id']:b for b in blocks}
        for link_index,link in enumerate(links):
            source=by_id[link['source_id']]
            source=slice_block(source,link['source_start'],link['source_end'])
            candidates=[targets[identity][0] for identity in link['target_ids']]
            citations=[resolve_citation({'evidence_id':b['evidence_id'],'quote':b['text'][:min(2000,len(b['text']))]},[b]) for b in [source]+candidates]
            finding={'kind':'cross_reference','detail':'Reference '+link['reference']+' is '+link['state'],
                'citations':citations,'reference':link['reference'],'source_id':link['source_id'],
                'target_ids':link['target_ids'],'resolution_state':link['state'],
                'review_flags':[] if link['state']=='resolved' else ['cross_reference_review']}
            extracted.append({'requirements':[],'facts':[],'findings':[finding]})
        for step,relevant,content in reference_requests:
            with tenant_transaction(self.engine,org) as db:
                saved=db.scalar(select(JobCheckpoint).where(JobCheckpoint.organization_id==org,JobCheckpoint.job_id==context.job_id,JobCheckpoint.step==step))
            if saved:resolved=context.checkpoint(step,digest(content),{})
            else:resolved=context.checkpoint(step,digest(content),self._bound(self._call(context,run,step,content,gateway,profile),relevant,frozen),progress=90)
            extracted.append(resolved)
        result=self._combine(extracted,frozen)
        self._publish(context,run,result)

    def _publish(self,context,run,result):
        org=run.organization_id
        if len(result['requirements'])>MAX_CHECKLIST_REQUIREMENTS:
            raise ValidationError('Checklist requirement limit exhausted (2000 supported requirements)')
        self._access(context,run)
        def publish(writes):
            target=writes.get(AnalysisRun,run.id)
            if target.status!='draft':raise Conflict()
            if target.checklist_id:
                checklist=writes.get(ChecklistVersion,target.checklist_id)
                if checklist.status!='draft' or checklist.revision!=target.expected_revision or writes.all(Requirement,checklist_id=checklist.id):raise Conflict('Extraction target changed')
                counters={'product':0,'participant':0};ids=[];bindings={}
                for raw in result['requirements']:
                    value=copy.deepcopy(raw);flags=value.pop('review_flags',[])
                    counters[value['scope']]+=1
                    value['display_id']=('T-' if value['scope']=='product' else 'U-')+str(counters[value['scope']])
                    value['logical_id']=str(uuid4());value['origin']={'kind':'document','analysis_run_id':run.id,'review_flags':flags}
                    row=Requirement(id=str(uuid4()),organization_id=org,checklist_id=checklist.id,**value)
                    writes.add(row);ids.append(row.id);bindings[row.logical_id]=copy.deepcopy(value['citations'])
                result['requirement_bindings']=bindings
                checklist.revision+=1;result['published_requirement_ids']=ids
            else:
                ids=[]
                for value in result['facts']:
                    row=ParticipantFact(id=str(uuid4()),organization_id=org,project_id=run.project_id,participant_id=run.participant_id,run_id=run.id,logical_id=str(uuid4()),data=value)
                    writes.add(row);ids.append(row.id)
                result['published_fact_ids']=ids
            target.result=copy.deepcopy(result);target.status='published'
            writes.audit_analysis_publication(target)
        context.checkpoint('published',run.cache_key,{'run_id':run.id,'checklist_id':run.checklist_id},publish,progress=100)

    def _call(self,context,run,chunk,content,gateway,profile):
        upper=_token_upper(content,run.frozen['kind'],profile)
        if upper+profile.output_tokens>profile.context_tokens:raise ValidationError('Gateway context exceeded')
        for attempt in range(profile.max_attempts):
            self._access(context,run)
            # Durable reservation and actor/lease recheck precede actual provider I/O.
            with context.service._transaction() as db:
                context.service._fence(db,context.job_id,context.claim.token)
                from app.workspace.repository import Repository
                repo=Repository(db,run.organization_id,run.actor_id)
                if not current_policy(repo)['allowed']:raise ValidationError('Policy revoked')
                db.scalar(select(AnalysisRun).where(AnalysisRun.organization_id==run.organization_id,AnalysisRun.id==run.id).with_for_update())
                calls=list(db.scalars(repo._query(AnalysisCall).where(AnalysisCall.run_id==run.id).order_by(AnalysisCall.sequence)))
                from .schemas import RunBudget
                ledger=BudgetLedger(RunBudget.model_validate(run.frozen['budget']),profile)
                for previous in calls:
                    reserved=ledger.reserve(previous.input_upper,previous.output_upper);ledger.reconcile(reserved,previous.usage)
                try:reservation=ledger.reserve(upper,profile.output_tokens)
                except BudgetExceeded:raise ValidationError('Analysis budget exhausted') from None
                call=AnalysisCall(id=str(uuid4()),organization_id=run.organization_id,project_id=run.project_id,job_id=run.job_id,run_id=run.id,sequence=len(calls)+1,chunk=chunk,input_upper=upper,output_upper=profile.output_tokens,status='reserved',usage=None,cost_upper=str(ledger.reservations[reservation][2]) if ledger.reservations[reservation][2] is not None else None,cost_actual=None,error_code=None)
                db.add(call);db.flush();identity=call.id
            failure=None;usage=None
            try:output,usage=gateway.extract(content,run.frozen['kind'])
            except GatewayFailure as exc:failure=exc.code;usage=exc.usage
            valid_usage=usage if usage and set(usage)=={'input_tokens','output_tokens'} and all(type(v) is int and v>=0 for v in usage.values()) and usage['input_tokens']<=upper and usage['output_tokens']<=profile.output_tokens else None
            def reconcile(writes):
                row=writes.get(AnalysisCall,identity);row.usage=valid_usage
                row.status='failed' if failure else 'complete';row.error_code=failure
                if valid_usage and profile.input_price_per_million is not None and profile.output_price_per_million is not None:
                    row.cost_actual=str((Decimal(valid_usage['input_tokens'])*Decimal(profile.input_price_per_million)+Decimal(valid_usage['output_tokens'])*Decimal(profile.output_price_per_million))/1000000)
            context.checkpoint('call:'+identity,identity,{'status':'failed' if failure else 'complete','usage_known':valid_usage is not None},reconcile)
            if failure is None:return output
            if failure not in {'timeout','rate_limit','connection','invalid_json','invalid_schema','truncation'} or attempt+1>=profile.max_attempts:raise ValidationError('Gateway '+failure)
        raise ValidationError('Gateway attempts exhausted')

    def _combine(self,parts,frozen):
        result={'requirements':[],'facts':[],'findings':[],'coverage':copy.deepcopy(frozen['coverage']),'source_policy':frozen['source_policy']}
        for key in ('requirements','facts'):
            seen={}
            for part in parts:
                for raw in part[key]:
                    value=copy.deepcopy(raw)
                    identity=digest({k:v for k,v in value.items() if k not in {'citations','review_flags'}})
                    if identity in seen:
                        target=seen[identity]
                        for citation in value['citations']:
                            if citation not in target['citations']:target['citations'].append(citation)
                        target['review_flags']=sorted(set(target['review_flags'])|set(value['review_flags']))
                    else:
                        seen[identity]=value;result[key].append(value)
                        if key=='requirements' and len(result[key])>MAX_CHECKLIST_REQUIREMENTS:
                            raise ValidationError('Checklist requirement limit exhausted (2000 supported requirements)')
        result['findings']=[finding for part in parts for finding in part['findings']]
        # A grounded finding marks every affected claim even when it was emitted
        # in a different chunk. Match original block ancestry and quote overlap.
        for finding in result['findings']:
            flags=set(finding.get('review_flags',[]))
            for key in ('requirements','facts'):
                for value in result[key]:
                    if any(a['artifact_id']==b['artifact_id'] and a['block_id']==b['block_id']
                           and a['source_start']<b['source_end'] and b['source_start']<a['source_end']
                           for a in value['citations'] for b in finding['citations']):
                        value['review_flags']=sorted(set(value['review_flags'])|flags)
        # Exact incompatible predicates for the same item/parameter remain separate.
        for key in ('requirements','facts'):
            grouped={}
            for value in result[key]:grouped.setdefault((value.get('scope'),value.get('item'),value['parameter']),[]).append(value)
            for group in grouped.values():
                signatures={canonical_json(v.get('predicate') if key=='requirements' else {k:v.get(k) for k in ('model','value','unit','date')}) for v in group}
                if len(signatures)>1:
                    result['findings'].append({'kind':'contradiction' if key=='requirements' else 'internal_conflict','detail':'Incompatible claims for the same item and parameter require review','citations':[c for v in group for c in v['citations']],'review_flags':['contradiction_review']})
                    for value in group:value['review_flags']=sorted(set(value['review_flags'])|{'contradiction_review'})
        for value in result['facts']:
            value['source_priority']={'registry':4,'certificate':3,'technical_document':2,'declaration':1,'unknown':0}[value['source_kind']]
            value['source_priority_version']='declaration-is-not-proof-v1'
        if any(c.get('state') not in {'extracted','text_extracted','corrected'} for c in frozen['coverage']):
            result['findings'].append({'kind':'coverage','detail':'Unavailable or review-required source regions remain in this frozen package','citations':[],'review_flags':['incomplete_coverage']})
        return result


def register_handlers(registry,settings):
    registry.register(STAGE,lambda context:AnalysisService(context.service.engine,settings).handle(context))
