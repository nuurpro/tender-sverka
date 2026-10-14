"""Standard adapters in the actual isolated runner; skip bad files and continue."""
from io import BytesIO
import zipfile
import pytest
from app.packages.inputs import prepare_files
from app.documents.runner import DocumentRunnerError
from test_full_settings import settings

def zipped(entries):
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in entries: archive.writestr(name,data)
    return stream.getvalue()


def test_test_adapter_cannot_be_injected_in_production():
    with pytest.raises(ValueError,match='explicit test environment'):
        prepare_files([('a.txt',b'original')],settings=settings().model_copy(update={'environment':'development'}),archive_adapter=lambda *a,**k: None)


def test_archive_binary_protocol_validates_exact_boundaries():
    from app.documents import archives
    files=[('a.zip',zipped([('a.txt',b'exact original')]))]
    request=archives.encode_frame({'files':[{'name':files[0][0],'size':len(files[0][1])}]},[files[0][1]])
    metadata,payloads=archives.decode_frame(request,'files',maximum_bytes=50_000_000,maximum_items=20)
    assert payloads==[files[0][1]]
    for corrupt in [request[:-1],request+b'hidden',b'\xff'*4+request[4:]]:
        with pytest.raises(ValueError): archives.decode_frame(corrupt,'files',maximum_bytes=50_000_000,maximum_items=20)


def rar4(entries):
    import struct,zlib
    def header(typ,flags,body=b''):
        raw=struct.pack('<BHH',typ,flags,7+len(body))+body
        return struct.pack('<H',zlib.crc32(raw)&65535)+raw
    output=b'Rar!\x1a\x07\x00'+header(0x73,0,b'\0'*6)
    for name,payload in entries:
        encoded=name.encode('utf-8')
        body=struct.pack('<IIBIIBBHI',len(payload),len(payload),3,zlib.crc32(payload),0,20,0x30,len(encoded),0o100644)+encoded
        output+=header(0x74,0x8000,body)+payload
    return output+header(0x7b,0)


def rar5(entries):
    import zlib,struct
    def integer(value):
        encoded=b''
        while value>=128:encoded+=bytes([(value&127)|128]);value>>=7
        return encoded+bytes([value])
    def header(body):
        raw=integer(len(body))+body
        return struct.pack('<I',zlib.crc32(raw))+raw
    output=b'Rar!\x1a\x07\x01\x00'+header(b'\x01\x00\x00')
    for name,payload in entries:
        encoded=name.encode('utf-8')
        body=b'\x02\x02'+integer(len(payload))+b'\x04'+integer(len(payload))+integer(0o100644)
        body+=struct.pack('<I',zlib.crc32(payload))+b'\x00\x01'+integer(len(encoded))+encoded
        output+=header(body)+payload
    return output+header(b'\x05\x00\x00')


def seven_zip(entries):
    import subprocess,tarfile
    stream=BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        for name,payload in entries:
            info=tarfile.TarInfo(name);info.size=len(payload);info.mode=0o644
            archive.addfile(info,BytesIO(payload))
    result=subprocess.run(['wsl','--distribution','Ubuntu','--exec','/usr/bin/bsdtar','-cf','-','--format=7zip','@-'],
                          input=stream.getvalue(),capture_output=True,timeout=20,check=True)
    # bsdtar pads its stdout block. Retain only the actual 7Z physical envelope.
    import struct
    offset,size=struct.unpack_from('<QQ',result.stdout,12)
    return result.stdout[:32+offset+size]


@pytest.fixture
def actual_archive_settings():
    import os
    if not os.environ.get('DOCUMENT_RUNNER_INTEGRATION'):pytest.skip('Explicit actual document runner acceptance required')
    changes={field.lower():os.environ['WORKSPACE_TEST_'+field] for field in
        ('DOCUMENT_RUNNER_MODE','DOCUMENT_RUNTIME_PYTHON','DOCUMENT_RUNTIME_PYTHON_SHA256','DOCUMENT_PARSER_CODE_PATH','DOCUMENT_WSL_DISTRIBUTION')}
    return settings(**changes)


@pytest.mark.parametrize('case',['timeout','cancel','death'])
def test_actual_archive_execution_cleans_descendants_and_private_tmpfs(actual_archive_settings,case):
    import json,threading,subprocess,uuid
    from app.documents import runner
    from app.documents.archive_runner import ArchiveExecution
    token='archive-owned-'+uuid.uuid4().hex
    source=f"""import os,time,pathlib,signal
pathlib.Path('/tmp/{token}').write_text('public synthetic temporary marker')
child=os.fork()
if child==0:time.sleep(60)
else:
 if '{case}'=='death':os.kill(os.getpid(),signal.SIGKILL)
 time.sleep(60)
# {token}
"""
    config=actual_archive_settings.model_copy(update={'document_runner_timeout_seconds':2})
    execution=ArchiveExecution(config)
    command,is_wsl=runner._build_command(execution,archive=True);command[-1:]=['-c',source]
    cancelled=threading.Event();timer=threading.Timer(.8,cancelled.set)
    if case=='cancel':timer.start()
    try:
        with pytest.raises(DocumentRunnerError) as error:runner._stream_process(command,b'',execution,cancelled.is_set if case=='cancel' else None,is_wsl,handshake=True)
        assert error.value.code in ({'parser_failed','timeout'} if case=='death' else {'cancelled'} if case=='cancel' else {'timeout'})
    finally:
        timer.cancel()
        if case=='cancel':timer.join()
    remaining=subprocess.run(runner._wsl_prefix(config)+['/usr/bin/pgrep','-f',token],capture_output=True,timeout=20)
    assert remaining.returncode==1,'Archive execution leaked owned descendants'
    command,is_wsl=runner._build_command(ArchiveExecution(actual_archive_settings),archive=True)
    command[-1:]=['-c',f"import pathlib;print(pathlib.Path('/tmp/{token}').exists())"]
    assert runner._stream_process(command,b'',ArchiveExecution(actual_archive_settings),None,is_wsl,handshake=True)==b'False\n'


def public_compressed_rar(kind):
    import binascii
    from pathlib import Path
    filename='libarchive-rar4-compressed-text.uu' if kind=='rar4' else 'libarchive-rar5-compressed.uu'
    lines=(Path(__file__).parent/'fixtures'/'archives'/filename).read_bytes().splitlines()
    return b''.join(binascii.a2b_uu(line) for line in lines[1:-1] if line)



@pytest.mark.parametrize('kind',['zip','rar4','rar5','7z'])
def test_actual_standard_archive_exact_original_and_provenance(actual_archive_settings,kind):
    import struct
    if kind in {'rar4','rar5'}:
        original=public_compressed_rar(kind)
        expected=b'test text document\r\n' if kind=='rar4' else b''.join(struct.pack('<I',max(0,k*k-3*k+1)) for k in range(1,301))
    else:
        expected=b'public synthetic exact original'
        original=(zipped if kind=='zip' else seven_zip)([('folder/source.txt',expected)])
    batch=prepare_files([('public.'+('rar' if kind.startswith('rar') else kind),original)],settings=actual_archive_settings)
    assert not batch.issues and len(batch.leaves)==1
    assert batch.leaves[0].data==expected and batch.containers[0].data==original
    assert batch.admission['policy_version']=='archive-admission-v4'


def test_actual_standard_nested_mixed_with_corrupt_sibling(actual_archive_settings):
    inner=seven_zip([('source.txt',b'public surviving original'),('bad.rar',b'Rar!broken')])
    original=zipped([('inner.7z',inner),('good.txt',b'public sibling')])
    batch=prepare_files([('outer.zip',original)],settings=actual_archive_settings)
    assert {file.path:file.data for file in batch.leaves}=={'outer.zip/good.txt':b'public sibling','outer.zip/inner.7z/source.txt':b'public surviving original'}
    assert len(batch.leaves[1].source_chain)==2
    assert batch.issues[0]['path']=='outer.zip/inner.7z/bad.rar'
    assert any(file.data==b'Rar!broken' for file in batch.containers)


@pytest.mark.parametrize('kind,data',[('zip',b'PK\x03\x04bad'),('rar',b'Rar!broken'),('7z',b'7z\xbc\xaf\x27\x1cbad')])
def test_actual_corrupt_archive_is_skipped_and_valid_wire_survives(actual_archive_settings,kind,data):
    batch=prepare_files([('bad.'+kind,data),('plain.txt',b'public survivor')],settings=actual_archive_settings)
    assert [file.data for file in batch.leaves]==[b'public survivor']
    assert len(batch.issues)==1 and batch.issues[0]['code']=='ARCHIVE_CORRUPTED'
    assert batch.containers[0].data==data


@pytest.mark.parametrize('entries',[[('../unsafe.txt',b'x')],[('A.txt',b'a'),('a.txt',b'b')],[(str(i)+'.txt',b'x') for i in range(21)]])
def test_actual_basic_path_duplicate_and_file_limits_skip_container(actual_archive_settings,entries):
    batch=prepare_files([('bad.zip',zipped(entries)),('plain.txt',b'public survivor')],settings=actual_archive_settings)
    assert [file.path for file in batch.leaves]==['plain.txt'] and batch.issues


def test_actual_partial_standard_zip_failure_discards_all_children(actual_archive_settings):
    original=bytearray(zipped([('first.txt',b'public first'),('second.txt',b'public second')]))
    position=original.index(b'PK\x03\x04',4)+30+len('second.txt');original[position]^=255
    batch=prepare_files([('bad.zip',bytes(original)),('plain.txt',b'public survivor')],settings=actual_archive_settings)
    assert [file.path for file in batch.leaves]==['plain.txt'] and len(batch.issues)==1


def test_actual_timed_out_file_keeps_valid_following_archive(actual_archive_settings,monkeypatch):
    from app.documents import runner
    stream=runner._stream_process;calls=0
    def expire(command,request,*args,**kwargs):
        nonlocal calls
        if request!=b'{"probe":true}':
            calls+=1
            if calls==1:
                command=command[:-1]+['-c','import time; time.sleep(60)']
                execution=args[0]
                from app.documents.archive_runner import ArchiveExecution
                args=(ArchiveExecution(execution.source.model_copy(update={'document_runner_timeout_seconds':2})),*args[1:])
        return stream(command,request,*args,**kwargs)
    monkeypatch.setattr(runner,'_stream_process',expire)
    config=actual_archive_settings
    batch=prepare_files([('timeout.zip',zipped([('a.txt',b'bad pending')])),('valid.zip',zipped([('b.txt',b'public survivor')]))],settings=config)
    assert [file.data for file in batch.leaves]==[b'public survivor']
    assert batch.issues[0]['path']=='timeout.zip' and batch.issues[0]['code']=='UNSUPPORTED_FORMAT'


def test_actual_standard_full_50mb_expanded_envelope(actual_archive_settings):
    original=zipped([('large.txt',b'x'*50_000_000)])
    batch=prepare_files([('large.zip',original)],settings=actual_archive_settings)
    assert not batch.issues and batch.logical_bytes==50_000_000 and batch.expanded_bytes==50_000_000


@pytest.mark.parametrize('kind,number,program',[
    ('alarm',14,"signal.alarm(1); time.sleep(60)"),
    ('cpu',24,"import resource; resource.setrlimit(resource.RLIMIT_CPU,(1,2))\nwhile True:pass"),
    ('file',25,"with open('/tmp/public-limit','wb') as target: target.write(b'x'*50_000_001)"),
])
def test_actual_standard_runner_preserves_resource_signal_exit(actual_archive_settings,kind,number,program):
    from app.documents import runner
    from app.documents.archive_runner import ArchiveExecution
    command,is_wsl=runner._build_command(ArchiveExecution(actual_archive_settings),archive=True)
    source='import signal,time\nsignal.signal('+str(number)+',signal.SIG_DFL)\n'+program
    command[-1:]=['-c',source]
    with pytest.raises(DocumentRunnerError) as failure:
        runner._stream_process(command,b'',ArchiveExecution(actual_archive_settings),None,is_wsl,handshake=True)
    assert failure.value.code=='parser_failed'  # Native/OCR shared public code remains unchanged.
    assert failure.value.returncode in {-number,128+number}


@pytest.mark.parametrize('archive_first',[True,False])
def test_actual_descendant_collision_skips_archive_and_preserves_wire(actual_archive_settings,archive_first):
    archive=('a.zip',zipped([('source.txt',b'public archived source')]))
    wire=('a.zip/source.txt',b'public wire source')
    batch=prepare_files([archive,wire] if archive_first else [wire,archive],settings=actual_archive_settings)
    assert [(file.path,file.data) for file in batch.leaves]==[wire]
    assert [(file.path,file.data) for file in batch.containers]==[archive]
    assert batch.issues[0]['path']=='a.zip' and batch.issues[0]['code']=='ARCHIVE_CORRUPTED'
    assert batch.expanded_bytes==batch.directory_entries==0
