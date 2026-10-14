"""The user-approved standard-library archive behavior: skip failures, continue."""
from io import BytesIO
import zipfile
import pytest
from app.documents.archives import prepare_files as standard_prepare


def zipped(entries):
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in entries:archive.writestr(name,data)
    return stream.getvalue()


def test_valid_and_corrupt_archives_keep_valid_text_and_failed_original():
    valid=zipped([('folder/source.txt',b'public valid original')]);corrupt=b'PK\x03\x04bad'
    batch=standard_prepare([('valid.zip',valid),('bad.zip',corrupt)])
    assert [file.data for file in batch.leaves]==[b'public valid original']
    assert {file.path:file.data for file in batch.containers}=={'valid.zip':valid,'bad.zip':corrupt}
    assert batch.issues==[{'path':'bad.zip','code':'ARCHIVE_CORRUPTED','message':'Archive could not be decoded.'}]


def test_failed_nested_archive_keeps_valid_sibling_and_source_chain():
    bad=b'PK\x03\x04bad';outer=zipped([('bad.zip',bad),('source.txt',b'public valid sibling')])
    batch=standard_prepare([('outer.zip',outer)])
    assert [file.path for file in batch.leaves]==['outer.zip/source.txt']
    assert batch.issues[0]['path']=='outer.zip/bad.zip'
    assert batch.leaves[0].source_chain==({'path':'outer.zip','sha256':batch.containers[0].file_hash},)
    assert next(file.data for file in batch.containers if file.path.endswith('/bad.zip'))==bad


def test_all_bad_archives_are_a_visible_batch_without_available_documents():
    original=b'PK\x03\x04bad'
    batch=standard_prepare([('bad.zip',original)])
    assert not batch.leaves and batch.containers[0].data==original
    assert batch.issues[0]['code']=='ARCHIVE_CORRUPTED'


def test_partial_decode_failure_discards_incomplete_children():
    original=zipped([('first.txt',b'public first'),('second.txt',b'public second')])
    changed=bytearray(original)
    payload=changed.index(b'PK\x03\x04',4)+30+len('second.txt')
    changed[payload]^=255
    batch=standard_prepare([('partial.zip',bytes(changed)),('plain.txt',b'public surviving file')])
    assert [file.path for file in batch.leaves]==['plain.txt']
    assert batch.issues[0]['path']=='partial.zip'


def test_missing_archive_runtime_is_per_file():
    from app.packages.inputs import prepare_files
    from test_full_settings import settings
    original=zipped([('source.txt',b'public source')])
    batch=prepare_files([('archive.zip',original),('valid.txt',b'public valid')],settings=settings(document_runner_mode='disabled'))
    assert [file.path for file in batch.leaves]==['valid.txt']
    assert batch.issues[0]['code']=='UNSUPPORTED_FORMAT'


def test_unsupported_standard_zip_codec_skips_only_its_archive():
    import struct
    original=bytearray(zipped([('source.txt',b'public original')]))
    central=original.index(b'PK\x01\x02')
    struct.pack_into('<H',original,8,99);struct.pack_into('<H',original,central+10,99)
    batch=standard_prepare([('exotic.zip',bytes(original)),('valid.txt',b'public valid')])
    assert [file.path for file in batch.leaves]==['valid.txt']
    assert batch.issues[0]['code']=='UNSUPPORTED_FORMAT'


def test_wire_plain_file_is_reserved_when_archive_exhausts_logical_count():
    batch=standard_prepare([('many.zip',zipped([('a.txt',b'a'),('b.txt',b'b')])),('plain.txt',b'public survivor')],max_files=2)
    assert [item.path for item in batch.leaves]==['plain.txt']
    assert batch.issues[0]['path']=='many.zip'


def test_archive_plus_plain_file_cannot_exceed_logical_byte_budget():
    original=zipped([('a.txt',b'a'*900)])
    batch=standard_prepare([('archive.zip',original),('plain.txt',b'p'*200)],max_bytes=1000)
    assert [item.path for item in batch.leaves]==['plain.txt']
    assert batch.logical_bytes==200 and batch.issues[0]['path']=='archive.zip'


def test_nested_container_transport_preserves_relative_name():
    from app.documents.archives import expand_one
    from app.documents.archive_runner import _validate_output,archive_snapshot
    from app.documents.archives import encode_frame
    original=zipped([('inner.zip',zipped([('a.txt',b'public original')]))])
    batch=expand_one('outer.zip',original);snapshot=archive_snapshot(None)
    metadata={'snapshot':snapshot,'wire_bytes':len(original),'wire_files':1,'expanded_bytes':batch.expanded_bytes,
        'directory_entries':batch.directory_entries,'issues':batch.issues,
        'files':[{'path':f.path,'name':f.name,'size':len(f.data),'sha256':f.file_hash,'mime_type':f.mime_type,
        'support':f.support,'role':f.role,'source_chain':list(f.source_chain)} for f in batch.containers]}
    checked=_validate_output(encode_frame(metadata,[f.data for f in batch.containers]),[('outer.zip',original)],snapshot)
    assert checked.containers[1].name=='inner.zip'


def test_archive_entrypoint_accepts_one_level_v4_and_emits_issues(monkeypatch):
    import sys,json
    from app.documents import archive_entrypoint as entry
    from app.documents.archives import encode_frame,decode_frame
    from app.documents.archive_runner import archive_snapshot
    snapshot=archive_snapshot(None,{'runtime_versions':{},'archive_toolchain':{'standard':'fixture'}})
    probe={'isolated':True,'code_hash':snapshot['code_hash'],'runtime_versions':{},'archive_toolchain':snapshot['archive_toolchain']}
    raw=encode_frame({'snapshot':snapshot,'limits':{'max_files':20,'max_bytes':50000000,'max_entries':2048},
        'files':[{'name':'bad.zip','size':7}]},[b'PK\x03\x04bad'])
    class Stream:
        def __init__(self,data=b''):self.buffer=BytesIO(data)
    stdout=Stream()
    monkeypatch.setattr(entry,'archive_probe',lambda:probe)
    monkeypatch.setattr(sys,'stdin',Stream(raw));monkeypatch.setattr(sys,'stdout',stdout)
    monkeypatch.setattr(entry.signal,'signal',lambda *a:None)
    monkeypatch.setattr(entry.signal,'setitimer',lambda *a:None,raising=False)
    monkeypatch.setattr(entry.signal,'SIGALRM',14,raising=False)
    monkeypatch.setattr(entry.signal,'ITIMER_REAL',0,raising=False)
    entry.main()
    metadata,payloads=decode_frame(stdout.buffer.getvalue(),'files',maximum_bytes=100000000,maximum_items=80)
    assert metadata['issues'][0]['code']=='ARCHIVE_CORRUPTED' and payloads==[b'PK\x03\x04bad']


def test_standard_archive_identity_does_not_require_native_or_ocr_tools(monkeypatch):
    from app.documents import toolchain
    monkeypatch.setattr(toolchain,'file_hash',lambda path:'public-hash')
    monkeypatch.setattr(toolchain,'runtime_tree_assets',lambda path:({'public.py':'public-hash'},[]))
    monkeypatch.setattr(toolchain,'elf_dependency_assets',lambda roots:{'/usr/lib/public.so':'public-hash'})
    selected=[]
    def installed(names):
        selected.extend(names);return {}
    monkeypatch.setattr(toolchain,'installed_distributions',installed)
    result=toolchain.archive_identity()
    assert result['decoder-policy']=='standard-zipfile-rarfile-py7zr-skip-failed-container-v1'
    assert not any(name in selected for name in ('pdfplumber','Pillow','numpy'))
    assert 'libarchive.sha256' not in result and 'liblzma.sha256' not in result


def test_cancellation_propagates_instead_of_becoming_skipped_issue():
    from app.documents.archive_runner import run_archive_admission
    from app.documents.runner import DocumentRunnerError
    from test_full_settings import settings
    with pytest.raises(DocumentRunnerError) as failure:
        run_archive_admission([('a.zip',zipped([('a.txt',b'public original')]))],settings(),cancelled=lambda:True)
    assert failure.value.code=='cancelled'


def test_directory_entries_are_cumulative_and_valid_sibling_survives():
    inner=zipped([(f'd{i}/',b'') for i in range(1100)]+[('a.txt',b'public original')])
    outer=zipped([('one.zip',inner),('two.zip',inner)])
    batch=standard_prepare([('outer.zip',outer),('plain.txt',b'public survivor')])
    assert batch.directory_entries<=2048
    assert any(file.path=='plain.txt' for file in batch.leaves) and batch.issues[0]['path']=='outer.zip/two.zip'


@pytest.mark.parametrize('entries,limits',[
    ([('a.txt',b'a'*1001)],{'max_bytes':1000}),
    ([(str(i)+'.txt',b'x') for i in range(21)],{}),
    ([(str(i)+'/',b'') for i in range(2049)],{}),
])
def test_standard_processing_limits_are_unsupported_format(entries,limits):
    batch=standard_prepare([('limited.zip',zipped(entries))],**limits)
    assert not batch.leaves and batch.issues[0]['code']=='UNSUPPORTED_FORMAT'


def test_standard_expired_decode_is_unsupported_format():
    import time
    from app.documents.archives import _read,ArchiveFailure
    with pytest.raises(ArchiveFailure) as failure:_read(BytesIO(b'public source'),50,time.monotonic()-1)
    assert failure.value.code=='UNSUPPORTED_FORMAT'


@pytest.mark.parametrize('code',['timeout','output_limit'])
def test_host_resource_failure_skips_one_archive_as_unsupported(monkeypatch,code):
    from app.documents import archive_runner
    from app.documents.runner import DocumentRunnerError
    from test_full_settings import settings
    probe={'runtime_versions':{},'archive_toolchain':{'standard':'fixture'}}
    monkeypatch.setattr(archive_runner,'ensure_archive_available',lambda *a:probe)
    monkeypatch.setattr(archive_runner.runner,'_build_command',lambda *a,**k:([],False))
    def fail(*a,**k):raise DocumentRunnerError(code,'Public synthetic resource refusal')
    monkeypatch.setattr(archive_runner.runner,'_stream_process',fail)
    batch=archive_runner.run_archive_admission([('limited.zip',zipped([('a.txt',b'public source')])),('valid.txt',b'public survivor')],settings(document_runner_mode='bubblewrap'))
    assert [file.path for file in batch.leaves]==['valid.txt'] and batch.issues[0]['code']=='UNSUPPORTED_FORMAT'


@pytest.mark.parametrize('returncode',[-14,142,-24,152,-25,153])
def test_qualified_archive_resource_signals_are_unsupported(monkeypatch,returncode):
    from app.documents import archive_runner
    from app.documents.runner import DocumentRunnerError
    from test_full_settings import settings
    monkeypatch.setattr(archive_runner,'ensure_archive_available',lambda *a:{'runtime_versions':{},'archive_toolchain':{'standard':'fixture'}})
    monkeypatch.setattr(archive_runner.runner,'_build_command',lambda *a,**k:([],False))
    def fail(*a,**k):
        failure=DocumentRunnerError('parser_failed','Public resource signal');failure.returncode=returncode;raise failure
    monkeypatch.setattr(archive_runner.runner,'_stream_process',fail)
    batch=archive_runner.run_archive_admission([('resource.zip',zipped([('a.txt',b'public source')])),('plain.txt',b'public survivor')],settings(document_runner_mode='bubblewrap'))
    assert batch.issues[0]['code']=='UNSUPPORTED_FORMAT' and batch.leaves[0].path=='plain.txt'


@pytest.mark.parametrize('archive_first',[True,False])
def test_archive_descendant_collision_preserves_wire_file_in_either_order(archive_first):
    archive=('a.zip',zipped([('source.txt',b'public archived source')]))
    wire=('a.zip/source.txt',b'public wire source')
    batch=standard_prepare([archive,wire] if archive_first else [wire,archive])
    assert [(file.path,file.data) for file in batch.leaves]==[wire]
    assert batch.leaves[0].source_chain==()
    assert [(file.path,file.data) for file in batch.containers]==[archive]
    assert batch.issues==[{'path':'a.zip','code':'ARCHIVE_CORRUPTED','message':'Archive member path conflicts with another input.'}]
    assert batch.expanded_bytes==batch.directory_entries==0
