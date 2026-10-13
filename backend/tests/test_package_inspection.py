from io import BytesIO

import pytest
from sqlalchemy import func, select

from test_package_api import packages, upload
from test_full_auth_api import full_api


def inspector():
    try:
        from app.packages.inspection import inspect_document
    except ImportError:
        pytest.fail('Real versioned package inspection is missing')
    return inspect_document


def pdf(pages=2):
    # A real minimal PDF, no optional testing-only PDF dependency.
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',
        ('<< /Type /Pages /Count %d /Kids [%s] >>' % (pages,' '.join(f'{i+3} 0 R' for i in range(pages)))).encode()]
    objects.extend(b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << >> >>' for _ in range(pages))
    output=bytearray(b'%PDF-1.4\n');offsets=[0]
    for number,obj in enumerate(objects,1):
        offsets.append(len(output));output.extend(f'{number} 0 obj\n'.encode()+obj+b'\nendobj\n')
    start=len(output);output.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        output.extend(f'{offset:010d} 00000 n \n'.encode())
    output.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
    return bytes(output)


def test_actual_pdf_pages_scans_and_unknown_counts_are_explicit():
    inspect=inspector()
    result=inspect(pdf(),'application/pdf')
    assert result['page_count']==2 and result['coverage']=='ocr_pending'
    assert 'ocr_pending' in result['blockers']
    result=inspect(b'unknown','application/octet-stream')
    assert result['page_count'] is None and result['token_count'] is None
    assert 'adapter_unavailable' in result['blockers']
    over=inspect(pdf(1001),'application/pdf')
    assert over['page_count']==1001 and 'page_limit_exceeded' in over['blockers']


def test_office_text_probe_does_not_claim_pages_complete_extraction_or_tokens():
    from docx import Document
    inspect=inspector()
    doc=Document();doc.add_paragraph('Actual text');stream=BytesIO();doc.save(stream)
    result=inspect(stream.getvalue(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    assert result['page_count'] is None and result['token_count'] is None
    assert result['text_probe_bytes']>0 and result['coverage']=='probe'
    assert 'page_count_pending' in result['blockers']


def test_real_handler_persists_fenced_inspection_checkpoint_once(packages):
    from app.jobs.service import JobService
    from app.jobs.worker import ExecutionContext
    from app.workspace.database import tenant_transaction
    from app.workspace.models import PackageInspection, JobCheckpoint
    client,engine,app,store,org,pid=packages
    from app.packages.service import PackageService
    from app.documents.runner import parser_code_hash
    from app.documents.readers import normalize
    probe=lambda settings: {'isolated':True,'code_hash':parser_code_hash(),'runtime_versions':{}}
    app.state.packages=PackageService(engine,app.state.settings,store,app.state.packages.cipher,inspection_readiness=probe)
    base=f'/api/organizations/{org}/projects/{pid}'
    response=upload(client,base,name='renamed.bin',data=pdf()).json()
    job_id=response['inspection_job_id'];package=response['package']['id']
    inspector()  # Intended red assertion before the handler module exists.
    from app.packages.inspection import inspection_handler
    runner=JobService(engine,org);claim=runner.claim(job_id)
    context=ExecutionContext(runner,job_id,claim)
    handler=inspection_handler(app.state.settings,packages=app.state.packages,
        parser=lambda data,name,mime,profile,settings,cancelled=None: normalize(data,name,mime,profile),readiness=probe)
    handler(context);handler(context);runner.finish(job_id,claim.token)
    report=client.get(base+f'/packages/{package}/preflight').json()
    assert report['inspection_status']=='inspected' and report['page_count']==2
    assert report['analysis_allowed'] is False and report['token_count'] is None
    with tenant_transaction(engine,org) as db:
        assert db.scalar(select(func.count()).select_from(PackageInspection))==1
        assert db.scalar(select(func.count()).select_from(JobCheckpoint))==1


def test_inspection_fenced_adapter_rejects_other_project_and_identity_changes(packages):
    inspector()
    from app.workspace.models import PackageInspection
    from app.jobs.scoped_writes import FencedWrites,ScopeViolation
    from app.workspace.database import tenant_transaction
    from uuid import uuid4
    client,engine,app,store,org,pid=packages
    result=upload(client,f'/api/organizations/{org}/projects/{pid}').json()
    with tenant_transaction(engine,org) as db:
        writes=FencedWrites(db,org,pid)
        with pytest.raises(ScopeViolation):
            writes.add(PackageInspection(organization_id=org,project_id=str(uuid4()),package_id=result['package']['id'],
                identity_hash='a'*64,parser_version='v1',result={}))
        valid=PackageInspection(organization_id=org,project_id=pid,package_id=result['package']['id'],
            identity_hash='a'*64,parser_version='v1',result={})
        writes.add(valid);writes.flush()
        valid.package_id=str(uuid4())
        with pytest.raises(ScopeViolation):
            writes.flush()


def test_inspection_job_freezes_parser_policy_options_and_language(packages):
    from app.workspace.database import tenant_transaction
    from app.workspace.models import Job
    client,engine,app,store,org,pid=packages
    response=upload(client,f'/api/organizations/{org}/projects/{pid}').json()
    with tenant_transaction(engine,org) as db:
        job=db.get(Job,response['inspection_job_id'])
        assert 'inspection_profile' in job.payload, 'Inspection transform identity was not frozen'
        profile=job.payload['inspection_profile']
        assert profile['options']['max_pages']==1000 and profile['options']['max_tokens']==250000
        assert profile['language']=='ru' and profile['model']=='none' and profile['tokenizer']=='pending'


def test_disabled_runner_never_registers_host_inspection_handler():
    from app.jobs.worker import HandlerRegistry
    from app.packages.inspection import register_handlers
    from test_full_settings import settings
    registry=HandlerRegistry('test')
    register_handlers(registry,settings())
    assert registry.get('package.inspect') is None
