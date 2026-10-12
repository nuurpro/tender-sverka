"""Measured inspection only. Extraction/OCR/tokenizer stages remain explicit gates."""
import hashlib
from importlib.metadata import version
from io import BytesIO
import logging
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from sqlalchemy import select

from app.workspace.database import tenant_transaction
from app.workspace.models import Document, Job, PackageInspection, Project
from app.workspace.repository import Repository, ValidationError, canonical_json
from .service import PackageService,nested_package
from .storage import StorageUnavailable

POLICY_VERSION='inspection-v1'


def parser_identity():
    return {'policy':POLICY_VERSION,'pdfplumber':version('pdfplumber'),'python-docx':version('python-docx'),
            'openpyxl':version('openpyxl'),'options':{'text_probe':True},'model':'none','tokenizer':'pending'}


def inspection_profile(settings,language):
    identity=parser_identity()
    return {**identity,'language':language,'options':{**identity['options'],
            'max_pages':settings.max_package_pages,'max_tokens':settings.max_package_tokens}}


def inspect_document(data,mime,max_pages=1000,max_tokens=250_000):
    report={'page_count':None,'token_count':None,'text_probe_bytes':None,'token_upper_bound':None,
            'coverage':'pending','blockers':[],'warnings':[]}
    text=None
    try:
        if mime=='application/pdf':
            import pdfplumber
            with pdfplumber.open(BytesIO(data)) as pdf:
                report['page_count']=len(pdf.pages)
                if report['page_count']>max_pages:
                    report['blockers'].append('page_limit_exceeded')
                    return report
                text='\n'.join(page.extract_text() or '' for page in pdf.pages)
                if not text.strip():
                    report['coverage']='ocr_pending';report['blockers'].append('ocr_pending')
                else:
                    report['coverage']='probe'
        elif mime.endswith('wordprocessingml.document'):
            from docx import Document as WordDocument
            doc=WordDocument(BytesIO(data))
            text='\n'.join(p.text for p in doc.paragraphs)
            report['coverage']='probe';report['warnings'].append('office_layout_and_full_coverage_pending')
        elif mime.endswith('spreadsheetml.sheet'):
            from openpyxl import load_workbook
            workbook=load_workbook(BytesIO(data),read_only=True,data_only=False,keep_links=False)
            try:
                # Formula source is counted as inert text; formulas are never run.
                text='\n'.join(str(cell) for sheet in workbook for row in sheet.iter_rows(values_only=True) for cell in row if cell is not None)
            finally:
                workbook.close()
            report['coverage']='probe';report['warnings'].append('office_layout_and_full_coverage_pending')
        elif mime=='text/plain':
            text=data.decode('utf-8-sig');report['coverage']='probe'
        else:
            report['blockers'].append('adapter_unavailable')
        if text is not None:
            report['text_probe_bytes']=len(text.encode('utf-8'))
            # This is a provisional conservative byte-based bound of the probe,
            # never an accurate model token count or whole-extraction acceptance.
            report['token_upper_bound']=report['text_probe_bytes']
            if report['token_upper_bound']>max_tokens:
                report['blockers'].append('token_probe_bound_exceeded')
            report['warnings'].append('token_bound_provisional_utf8_probe_bytes')
    except Exception:
        report['coverage']='failed';report['blockers'].append('parser_failed')
    if report['page_count'] is None:
        report['blockers'].append('page_count_pending')
    report['blockers'].extend(['extraction_pending','tokenizer_profile_pending'])
    return report


def isolated_inspection(data,mime,max_pages,max_tokens,*,timeout=20):
    """Parser gets bounded RAM input and a hard process runtime, no temp file."""
    environment={key:value for key,value in os.environ.items() if key.upper() in {'PATH','SYSTEMROOT','WINDIR','LANG','LC_ALL'}}
    environment['PYTHONPATH']=str(Path(__file__).resolve().parents[2])
    header=json.dumps({'mime':mime,'max_pages':max_pages,'max_tokens':max_tokens}).encode()+b'\n'
    process=subprocess.Popen([sys.executable,'-m','app.packages.inspection'],stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        try:
            output,_=process.communicate(header+data,timeout=timeout)
            if process.returncode!=0 or len(output)>64*1024:
                raise ValueError()
            return json.loads(output)
        except subprocess.TimeoutExpired:
            process.kill();process.communicate()
            return {'page_count':None,'token_count':None,'token_upper_bound':None,'text_probe_bytes':None,
                'coverage':'failed','blockers':['parser_timeout','page_count_pending','extraction_pending','tokenizer_profile_pending'],'warnings':[]}
        except (ValueError,OSError):
            return {'page_count':None,'token_count':None,'token_upper_bound':None,'text_probe_bytes':None,
                'coverage':'failed','blockers':['parser_failed','page_count_pending','extraction_pending','tokenizer_profile_pending'],'warnings':[]}
    finally:
        if process.poll() is None:
            process.kill();process.wait(timeout=5)


def inspection_handler(settings,*,packages=None):
    def handle(context):
        service=context.service;org=service.organization_id
        package_id=context.payload.get('package_id')
        pipeline=packages or PackageService(service.engine,settings)
        with tenant_transaction(service.engine,org) as db:
            job=db.scalar(select(Job).where(Job.organization_id==org,Job.id==context.job_id))
            repo=Repository(db,org,str(uuid4()))
            package=nested_package(repo,job.project_id,package_id)
            if hashlib.sha256(canonical_json(package.manifest).encode()).hexdigest()!=context.payload.get('manifest_hash'):
                raise ValidationError('Package inspection input mismatch')
            profile=context.payload.get('inspection_profile')
            if not isinstance(profile,dict) or profile!=inspection_profile(settings,profile.get('language')):
                raise ValidationError('Inspection parser/policy profile changed; create a new versioned inspection job')
            identity={'organization_id':org,'project_id':job.project_id,'package_id':package_id,
                'manifest':package.manifest,'parser':profile}
            identity_hash=hashlib.sha256(canonical_json(identity).encode()).hexdigest()
            existing=db.scalar(repo._query(PackageInspection).where(PackageInspection.package_id==package_id,PackageInspection.identity_hash==identity_hash))
            existing_result=existing.result if existing else None
            project_id=job.project_id
            metadata=[pipeline._document_metadata(db,repo,project_id,package_id,doc.id)
                for doc in db.scalars(repo._query(Document).where(Document.package_id==package_id,Document.provenance_role=='document'))] if not existing else []
        if existing_result is not None:
            context.checkpoint('inspect',identity_hash,existing_result,progress=100)
            return
        results=[]
        for doc in metadata:
            try:
                original=pipeline.read_original(org,project_id,doc)
            except StorageUnavailable:
                from app.jobs.worker import TransientInfrastructureFailure
                raise TransientInfrastructureFailure() from None
            result=isolated_inspection(original,doc['mime_type'],profile['options']['max_pages'],profile['options']['max_tokens'])
            results.append({'document_id':doc['id'],**result})
        known_pages=sum(r['page_count'] for r in results if r['page_count'] is not None)
        pages=known_pages if all(r['page_count'] is not None for r in results) else None
        probes=[r['token_upper_bound'] for r in results]
        bound=sum(probes) if all(n is not None for n in probes) else None
        blockers=sorted({b for r in results for b in r['blockers']})
        if known_pages>settings.max_package_pages:
            blockers.append('page_limit_exceeded')
        if bound is not None and bound>settings.max_package_tokens:
            blockers.append('token_probe_bound_exceeded')
        report={'inspection_status':'inspected','analysis_allowed':False,'page_count':pages,'known_page_count':known_pages,
            'token_count':None,'token_upper_bound':bound,'tokenizer_profile':'pending','parser_versions':profile,
            'blockers':sorted(set(blockers+['extraction_pending','tokenizer_profile_pending'])),
            'warnings':sorted({w for r in results for w in r['warnings']}),'documents':results}
        def persist(writes):
            writes.add(PackageInspection(organization_id=org,project_id=project_id,package_id=package_id,
                identity_hash=identity_hash,parser_version=POLICY_VERSION,result=report))
        context.checkpoint('inspect',identity_hash,report,persist,progress=100)
    return handle


def register_handlers(registry,settings):
    registry.register('package.inspect',inspection_handler(settings))


if __name__=='__main__':
    logging.disable(logging.CRITICAL)
    from .crypto import MAX_DOCUMENT_BYTES
    options=json.loads(sys.stdin.buffer.readline(2048))
    data=sys.stdin.buffer.read(MAX_DOCUMENT_BYTES+1)
    if len(data)>MAX_DOCUMENT_BYTES:
        raise SystemExit(1)
    print(json.dumps(inspect_document(data,options['mime'],options['max_pages'],options['max_tokens'])))
