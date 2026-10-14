"""Bounded isolated standard-decoder calls; a failed container becomes an issue."""
from dataclasses import dataclass
import hashlib
import json
from . import runner
from .runner import DocumentRunnerError
from .archives import (ADMISSION_VERSION,FRAME_OUTPUT_BYTES,InputFile,InputBatch,
                       encode_frame,decode_frame,safe_path,ISSUE_MESSAGES,issue,container,
                       prepare_files as orchestrate)

@dataclass(frozen=True)
class ArchiveExecution:
    source:object
    document_runner_output_bytes:int=FRAME_OUTPUT_BYTES
    document_runner_file_bytes:int=50_000_000
    document_runner_tmp_bytes:int=67_108_864
    def __getattr__(self,name):return getattr(self.source,name)


def _execution(settings):
    if settings is None or settings.document_runner_mode=='disabled':raise DocumentRunnerError('runner_disabled','Archive isolation is disabled')
    return ArchiveExecution(settings)


def archive_snapshot(settings,probe=None):
    probe=probe or {'runtime_versions':{},'archive_toolchain':{'status':'unavailable'}}
    return {'policy_version':ADMISSION_VERSION,'code_hash':runner.parser_code_hash(),
        'runtime_versions':probe['runtime_versions'],'archive_toolchain':probe['archive_toolchain'],
        'limits':{'max_files':getattr(settings,'max_package_files',20),'max_bytes':getattr(settings,'max_package_bytes',50_000_000),'max_entries':2048,'max_depth':3,'max_seconds':5},
        'runner_policy':{'cpu_seconds':getattr(settings,'document_runner_cpu_seconds',20),'memory_bytes':getattr(settings,'document_runner_memory_bytes',805_306_368),
            'process_limit':getattr(settings,'document_runner_process_limit',32),'file_limit':getattr(settings,'document_runner_file_limit',64),
            'file_bytes':50_000_000,'tmp_bytes':67_108_864,'output_bytes':FRAME_OUTPUT_BYTES,'timeout_seconds':getattr(settings,'document_runner_timeout_seconds',30)}}


def ensure_archive_available(settings,cancelled=None):
    execution=_execution(settings);command,is_wsl=runner._build_command(execution,archive=True)
    try:
        probe=json.loads(runner._stream_process(command,b'{"probe":true}',execution,cancelled,is_wsl,handshake=True))
        if set(probe)!={'isolated','code_hash','runtime_versions','archive_toolchain'} or probe['isolated'] is not True or probe['code_hash']!=runner.parser_code_hash():raise ValueError('Archive code identity mismatch')
        if not isinstance(probe['archive_toolchain'],dict) or not probe['archive_toolchain']:raise ValueError('Missing archive toolchain')
        return probe
    except DocumentRunnerError:raise
    except (ValueError,TypeError):raise DocumentRunnerError('runner_unavailable','Archive isolation probe failed') from None


def run_archive_admission(files,settings,*,max_files=20,max_bytes=50_000_000,cancelled=None):
    snapshot=archive_snapshot(settings);snapshot['limits']['max_files']=max_files;snapshot['limits']['max_bytes']=max_bytes
    probe=None
    def decode(name,data,**remaining):
        nonlocal probe,snapshot
        if cancelled and cancelled():raise DocumentRunnerError('cancelled','Archive processing cancelled')
        try:
            if probe is None:
                probe=ensure_archive_available(settings,cancelled)
                snapshot=archive_snapshot(settings,probe);snapshot['limits']['max_files']=max_files;snapshot['limits']['max_bytes']=max_bytes
            execution=_execution(settings)
            request=encode_frame({'snapshot':snapshot,'limits':remaining,'files':[{'name':name,'size':len(data)}]},[data])
            command,is_wsl=runner._build_command(execution,archive=True)
            output=runner._stream_process(command,request,execution,cancelled,is_wsl,handshake=True)
            result=_validate_output(output,[(name,data)],snapshot)
            if result.expanded_bytes>remaining['max_bytes'] or result.directory_entries>remaining['max_entries']:raise ValueError('Archive remaining budget')
            return result
        except DocumentRunnerError as failure:
            if failure.code in {'cancelled','cleanup_failed'}:raise
            resource_signal=failure.code=='parser_failed' and failure.returncode in {-14,142,-24,152,-25,153}
            code='UNSUPPORTED_FORMAT' if resource_signal or failure.code in {'runner_disabled','runner_unavailable','timeout','output_limit'} else 'ARCHIVE_CORRUPTED'
        except (ValueError,TypeError,KeyError):code='ARCHIVE_CORRUPTED'
        return InputBatch([], [container(name,data)],len(data),1,admission=snapshot,issues=[issue(name,code)])
    result=orchestrate(files,max_files=max_files,max_bytes=max_bytes,decode=decode)
    result.admission=snapshot
    return result


def _validate_output(output,wire,snapshot):
    maximum=snapshot['limits']['max_bytes'];maximum_files=snapshot['limits']['max_files']
    metadata,payloads=decode_frame(output,'files',maximum_bytes=maximum*2,maximum_items=80)
    if set(metadata)!={'snapshot','wire_bytes','wire_files','expanded_bytes','directory_entries','files','issues'} or metadata['snapshot']!=snapshot:
        raise ValueError('Archive result fields or snapshot')
    if metadata['wire_bytes']!=sum(len(data) for _,data in wire) or metadata['wire_files']!=len(wire):raise ValueError('Archive wire accounting')
    for field in ('expanded_bytes','directory_entries'):
        if type(metadata[field])is not int or not 0<=metadata[field]<=(maximum if field=='expanded_bytes' else 2048):raise ValueError('Archive cumulative accounting')
    leaves=[];containers=[];seen=set();lookup={}
    for item,payload in zip(metadata['files'],payloads):
        if set(item)!={'path','name','size','sha256','mime_type','support','role','source_chain'}:raise ValueError('Archive file metadata')
        path=safe_path(item['path']);safe_path(item['name'])
        if path!=item['path'] or path.casefold() in seen or item['sha256']!=hashlib.sha256(payload).hexdigest():raise ValueError('Archive file identity')
        if item['role'] not in {'archive','document'} or not isinstance(item['mime_type'],str) or len(item['mime_type'])>256 or item['support'] not in {'provenance','inspection_available','adapter_unavailable','ocr_required'}:raise ValueError('Archive file support')
        if (item['role']=='archive')!=(item['support']=='provenance'):raise ValueError('Archive role mismatch')
        if item['role']=='archive' and item['mime_type'] not in {'application/zip','application/vnd.rar','application/x-7z-compressed'}:raise ValueError('Archive container MIME')
        if not isinstance(item['source_chain'],list) or len(item['source_chain'])>3:raise ValueError('Archive source depth')
        file=InputFile(path,item['name'],payload,item['mime_type'],item['support'],item['role'],tuple(item['source_chain']))
        (containers if file.role=='archive' else leaves).append(file);seen.add(path.casefold());lookup[path]=file
    if not 0<=len(leaves)<=maximum_files or len(containers)>60 or sum(len(file.data) for file in leaves)>maximum:raise ValueError('Archive logical count/bytes')
    wire_lookup={safe_path(name):hashlib.sha256(data).hexdigest() for name,data in wire}
    for file in leaves+containers:
        parent=None
        for source in file.source_chain:
            if not isinstance(source,dict) or set(source)!={'path','sha256'}:raise ValueError('Archive source shape')
            ancestor=lookup.get(source['path'])
            if not ancestor or ancestor.role!='archive' or ancestor.file_hash!=source['sha256'] or ancestor.source_chain!=file.source_chain[:file.source_chain.index(source)]:raise ValueError('Archive source hash/chain')
            if parent and not ancestor.path.startswith(parent+'/'):raise ValueError('Archive source ancestry')
            parent=ancestor.path
        root=file.source_chain[0]['path'] if file.source_chain else file.path
        if root not in wire_lookup or (not file.source_chain and file.file_hash!=wire_lookup[root]):raise ValueError('Archive root original')
        if parent and file.path!=parent+'/'+safe_path(file.name):raise ValueError('Archive member path')
    if any(path not in lookup or lookup[path].file_hash!=digest or lookup[path].source_chain for path,digest in wire_lookup.items()):raise ValueError('Missing wire original')
    # Every decoded member is retained as leaf/container or office internals;
    # all retained descendants must be covered by measured expansion.
    if sum(len(file.data) for file in leaves+containers if file.source_chain)>metadata['expanded_bytes']:raise ValueError('Archive expansion accounting')
    issues=metadata['issues']
    if not isinstance(issues,list) or len(issues)>2068:raise ValueError('Archive issue limit')
    for entry in issues:
        if not isinstance(entry,dict) or set(entry)!={'path','code','message'} or safe_path(entry['path'])!=entry['path'] or entry['code'] not in ISSUE_MESSAGES or not isinstance(entry['message'],str) or not 1<=len(entry['message'])<=160:raise ValueError('Archive issue fields')
        if not any(entry['path']==root or entry['path'].startswith(root+'/') for root in wire_lookup):raise ValueError('Archive issue scope')
    return InputBatch(leaves,containers,metadata['wire_bytes'],metadata['wire_files'],metadata['expanded_bytes'],metadata['directory_entries'],snapshot,issues)
