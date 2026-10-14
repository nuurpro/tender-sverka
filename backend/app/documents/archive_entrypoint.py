"""Bounded standard archive protocol inside the source-only networkless sandbox."""
import json
import hashlib
import os
import signal
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from archives import expand_one,decode_frame,encode_frame,ADMISSION_VERSION,FRAME_METADATA_BYTES
from entrypoint import identity
from toolchain import archive_identity,CODE_FILES


def archive_probe():
    assert os.getuid()!=0
    root=Path(__file__).resolve().parent
    assert {item.name for item in root.iterdir()}==set(CODE_FILES)
    assert all((root/name).is_file() and not (root/name).is_symlink() for name in CODE_FILES)
    return {**identity(),'archive_toolchain':archive_identity()}


def main():
    raw=sys.stdin.buffer.read(50_000_000+FRAME_METADATA_BYTES+5)
    if raw==b'{"probe":true}':
        sys.stdout.write(json.dumps(archive_probe(),separators=(',',':')));return
    request,payloads=decode_frame(raw,'files',maximum_bytes=50_000_000,maximum_items=1)
    if set(request)!={'files','snapshot','limits'} or any(set(item)!={'name','size'} for item in request['files']):raise ValueError('Archive request fields')
    snapshot=request['snapshot'];expected=archive_probe()
    if not isinstance(snapshot,dict) or set(snapshot)!={'policy_version','code_hash','runtime_versions','archive_toolchain','limits','runner_policy'}:raise ValueError('Archive snapshot fields')
    if snapshot['policy_version']!=ADMISSION_VERSION or any(snapshot[key]!=expected[key] for key in ('code_hash','runtime_versions','archive_toolchain')):raise ValueError('Archive identity mismatch')
    limits=snapshot['limits']
    if set(limits)!={'max_files','max_bytes','max_entries','max_depth','max_seconds'} or limits['max_entries']!=2048 or limits['max_depth']!=3 or limits['max_seconds']!=5:raise ValueError('Archive policy mismatch')
    if type(limits['max_files'])is not int or not 1<=limits['max_files']<=20 or type(limits['max_bytes'])is not int or not 1<=limits['max_bytes']<=50_000_000:raise ValueError('Archive policy bounds')
    remaining=request['limits']
    if not isinstance(remaining,dict) or set(remaining)!={'max_files','max_bytes','max_entries'} or any(type(value)is not int or not 0<=value<=limits[key] for key,value in remaining.items()):raise ValueError('Archive remaining bounds')
    # A kernel watchdog also interrupts a native C decoder, not just Python.
    signal.signal(signal.SIGALRM,signal.SIG_DFL)
    signal.setitimer(signal.ITIMER_REAL,limits['max_seconds'])
    try:
        batch=expand_one(request['files'][0]['name'],payloads[0],**remaining)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
    files=batch.leaves+batch.containers
    metadata={'snapshot':snapshot,'wire_bytes':batch.wire_bytes,'wire_files':batch.wire_files,
        'expanded_bytes':batch.expanded_bytes,'directory_entries':batch.directory_entries,
        'issues':batch.issues,
        'files':[{'path':item.path,'name':item.name,'size':len(item.data),'sha256':item.file_hash,'mime_type':item.mime_type,
                  'support':item.support,'role':item.role,'source_chain':list(item.source_chain)} for item in files]}
    sys.stdout.buffer.write(encode_frame(metadata,[item.data for item in files]))


if __name__=='__main__':
    try: main()
    except Exception:
        sys.stderr.write('{"code":"archive_refused","message":"Isolated archive admission refused the input"}')
        sys.exit(2)
