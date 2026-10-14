"""Standard archive adapters. Compressed inputs are decoded only in isolation."""
from dataclasses import dataclass,field
import hashlib
from io import BytesIO
from pathlib import Path
import stat
import struct
import tempfile
import time
import unicodedata
import zipfile

class ValidationError(ValueError):
    pass

class ArchiveFailure(Exception):
    def __init__(self,code='ARCHIVE_CORRUPTED',message=None):
        self.code=code
        self.message=message or ISSUE_MESSAGES[code]
        super().__init__(self.message)

ADMISSION_VERSION='archive-admission-v4'
FRAME_METADATA_BYTES=262144
FRAME_OUTPUT_BYTES=100_000_000+FRAME_METADATA_BYTES+4
UNRAR_TOOL='/runtime/opt/unrar/usr/bin/unrar-nonfree'
ISSUE_MESSAGES={'ARCHIVE_CORRUPTED':'Archive could not be decoded.',
                'UNSUPPORTED_FORMAT':'Archive format or decoder is unavailable.'}


def safe_path(name):
    if not isinstance(name,str) or not name or len(name)>500 or any(unicodedata.category(c) in {'Cc','Cf'} for c in name):
        raise ValidationError('Unsafe filename')
    normalized=unicodedata.normalize('NFC',name.replace('\\','/'))
    if ':' in normalized or normalized.startswith('/') or any(part in {'','.','..'} for part in normalized.split('/')):
        raise ValidationError('Unsafe filename')
    return normalized

@dataclass
class InputFile:
    path:str
    name:str
    data:bytes
    mime_type:str
    support:str
    role:str='document'
    source_chain:tuple=()
    @property
    def file_hash(self):return hashlib.sha256(self.data).hexdigest()

@dataclass
class InputBatch:
    leaves:list
    containers:list
    wire_bytes:int
    wire_files:int
    expanded_bytes:int=0
    directory_entries:int=0
    admission:dict|None=None
    issues:list=field(default_factory=list)
    @property
    def logical_bytes(self):return sum(len(file.data) for file in self.leaves)


def archive_kind(data,name):
    if data.startswith(b'Rar!'):return 'rar'
    if data.startswith(b'7z\xbc\xaf\x27\x1c'):return '7z'
    if data.startswith((b'PK\x03\x04',b'PK\x05\x06')):return 'zip'
    if name.lower().endswith(('.zip','.rar','.7z')):return name.rsplit('.',1)[-1].lower()
    return None


def issue(path,code,message=None):
    return {'path':safe_path(path),'code':code,'message':message or ISSUE_MESSAGES[code]}


def container(name,data,chain=()):
    kind=archive_kind(data,name) or 'zip'
    mime={'zip':'application/zip','rar':'application/vnd.rar','7z':'application/x-7z-compressed'}[kind]
    return InputFile(safe_path(name),name,data,mime,'provenance','archive',chain)


def _check_names(names):
    normalized=[safe_path(name.rstrip('/')) for name in names]
    if len({name.casefold() for name in normalized})!=len(normalized):raise ArchiveFailure()
    return normalized


def _read(source,remaining,deadline):
    output=bytearray()
    while True:
        if time.monotonic()>deadline:raise ArchiveFailure('UNSUPPORTED_FORMAT',message='Archive processing timed out.')
        part=source.read(min(65536,remaining-len(output)+1))
        if not part:break
        if len(output)+len(part)>remaining:raise ArchiveFailure('UNSUPPORTED_FORMAT',message='Archive exceeded processing limits.')
        output.extend(part)
    return bytes(output)


def _zip_members(data,max_bytes,max_entries,deadline):
    with zipfile.ZipFile(BytesIO(data)) as archive:
        infos=archive.infolist()
        if len(infos)>max_entries:raise ArchiveFailure('UNSUPPORTED_FORMAT',message='Archive exceeded processing limits.')
        names=_check_names([info.orig_filename for info in infos])
        members=[];total=0;directories=[]
        for info,name in zip(infos,names):
            mode=stat.S_IFMT(info.external_attr>>16)
            if mode not in {0,stat.S_IFREG,stat.S_IFDIR}:raise ArchiveFailure()
            if info.flag_bits&1:raise ArchiveFailure('UNSUPPORTED_FORMAT')
            if info.is_dir():
                if info.file_size:raise ArchiveFailure()
                directories.append(name);continue
            if info.file_size>max_bytes-total:raise ArchiveFailure('UNSUPPORTED_FORMAT',message='Archive exceeded processing limits.')
            with archive.open(info) as member:payload=_read(member,max_bytes-total,deadline)
            total+=len(payload);members.append((name,payload))
        return members,len(infos),directories


def _rar_members(data,max_bytes,max_entries,deadline):
    try:import rarfile
    except ImportError:raise ArchiveFailure('UNSUPPORTED_FORMAT') from None
    if not Path(UNRAR_TOOL).is_file():raise ArchiveFailure('UNSUPPORTED_FORMAT')
    rarfile.UNRAR_TOOL=UNRAR_TOOL;rarfile.USE_EXTRACT_HACK=False
    try:rarfile.tool_setup(unrar=True,unar=False,bsdtar=False,sevenzip=False,sevenzip2=False,force=True)
    except rarfile.RarCannotExec:raise ArchiveFailure('UNSUPPORTED_FORMAT') from None
    with tempfile.TemporaryDirectory(prefix='archive-',dir='/tmp') as directory:
        original=Path(directory)/'source.rar';original.write_bytes(data)
        with rarfile.RarFile(original) as archive:
            if archive.needs_password():raise ArchiveFailure('UNSUPPORTED_FORMAT')
            infos=archive.infolist()
            if len(infos)>max_entries:raise ArchiveFailure('UNSUPPORTED_FORMAT',message='Archive exceeded processing limits.')
            names=_check_names([info.filename for info in infos]);members=[];directories=[];total=0
            for info,name in zip(infos,names):
                if info.is_symlink() or getattr(info,'file_redir',None) or stat.S_ISLNK(getattr(info,'mode',0) or 0):raise ArchiveFailure()
                if info.flags&(rarfile.RAR_FILE_SPLIT_BEFORE|rarfile.RAR_FILE_SPLIT_AFTER):raise ArchiveFailure('UNSUPPORTED_FORMAT')
                if info.is_dir():directories.append(name);continue
                if not info.is_file():raise ArchiveFailure()
                if info.file_size>max_bytes-total:raise ArchiveFailure('UNSUPPORTED_FORMAT',message='Archive exceeded processing limits.')
                with archive.open(info) as member:payload=_read(member,max_bytes-total,deadline)
                total+=len(payload);members.append((name,payload))
            return members,len(infos),directories


def _seven_members(data,max_bytes,max_entries,deadline):
    try:
        import py7zr
        from py7zr.io import Py7zIO,WriterFactory
        from py7zr.exceptions import UnsupportedCompressionMethodError
    except ImportError:raise ArchiveFailure('UNSUPPORTED_FORMAT') from None
    class Writer(Py7zIO):
        def __init__(self):self.buffer=BytesIO()
        def write(self,value):
            nonlocal total
            if time.monotonic()>deadline:raise ArchiveFailure('UNSUPPORTED_FORMAT',message='Archive processing timed out.')
            total+=len(value)
            if total>max_bytes:raise ArchiveFailure('UNSUPPORTED_FORMAT',message='Archive exceeded processing limits.')
            return self.buffer.write(value)
        def read(self,size=None):return self.buffer.read(size)
        def seek(self,offset,whence=0):return self.buffer.seek(offset,whence)
        def flush(self):self.buffer.flush()
        def size(self):return self.buffer.getbuffer().nbytes
    class Factory(WriterFactory):
        def create(self,filename):
            name=safe_path(filename)
            if name not in allowed or name in outputs:raise ArchiveFailure()
            outputs[name]=Writer();return outputs[name]
    with py7zr.SevenZipFile(BytesIO(data),'r',max_extract_size=max_bytes) as archive:
        if archive.needs_password():raise ArchiveFailure('UNSUPPORTED_FORMAT')
        infos=archive.list()
        if len(infos)>max_entries:raise ArchiveFailure('UNSUPPORTED_FORMAT',message='Archive exceeded processing limits.')
        names=_check_names([info.filename for info in infos]);allowed=set();directories=[]
        for info,name in zip(infos,names):
            if info.is_symlink:raise ArchiveFailure()
            if info.is_directory:directories.append(name)
            elif not info.is_file:raise ArchiveFailure()
            else:
                if info.uncompressed is not None and info.uncompressed>max_bytes:raise ArchiveFailure('UNSUPPORTED_FORMAT',message='Archive exceeded processing limits.')
                allowed.add(name)
        outputs={};total=0
        try:archive.extractall(factory=Factory())
        except UnsupportedCompressionMethodError:raise ArchiveFailure('UNSUPPORTED_FORMAT') from None
        if set(outputs)!=allowed:raise ArchiveFailure()
        return [(name,outputs[name].buffer.getvalue()) for name in names if name in allowed],len(infos),directories


def _office_type(name,members):
    values=dict(members)
    for mime in ('text','spreadsheet','presentation'):
        value=('application/vnd.oasis.opendocument.'+mime).encode('ascii')
        if values.get('mimetype')==value:return value.decode('ascii')
    if '[Content_Types].xml' in values:
        for path,mime in [('word/document.xml','wordprocessingml.document'),('xl/workbook.xml','spreadsheetml.sheet'),('ppt/presentation.xml','presentationml.presentation')]:
            if path in values:return 'application/vnd.openxmlformats-officedocument.'+mime
    suffix='.'+name.rsplit('.',1)[-1].lower() if '.' in name else ''
    return {'.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.odt':'application/vnd.oasis.opendocument.text','.ods':'application/vnd.oasis.opendocument.spreadsheet',
            '.odp':'application/vnd.oasis.opendocument.presentation'}.get(suffix)


def expand_one(name,data,*,max_files=20,max_bytes=50_000_000,max_entries=2048):
    root=container(name,data);batch=InputBatch([], [root],len(data),1)
    try:
        deadline=time.monotonic()+5
        kind=archive_kind(data,name)
        reader={'zip':_zip_members,'rar':_rar_members,'7z':_seven_members}.get(kind)
        if reader is None:raise ArchiveFailure('UNSUPPORTED_FORMAT')
        members,entries,directories=reader(data,max_bytes,max_entries,deadline)
        files={part.casefold() for part,_ in members}
        for path in files|{part.casefold() for part in directories}:
            if any('/'.join(path.split('/')[:i]) in files for i in range(1,len(path.split('/')))):raise ArchiveFailure()
        office=_office_type(name,members) if kind=='zip' else None
        if office:
            if max_files<1 or len(data)>max_bytes:raise ArchiveFailure('UNSUPPORTED_FORMAT',message='Archive exceeded processing limits.')
            batch.containers=[];batch.leaves=[InputFile(safe_path(name),name,data,office,'adapter_unavailable' if office.startswith('application/vnd.oasis.') else 'inspection_available')]
        else:
            chain=({'path':root.path,'sha256':root.file_hash},)
            for part,payload in members:
                path=safe_path(root.path+'/'+part)
                if not payload:batch.issues.append(issue(path,'ARCHIVE_CORRUPTED'));continue
                if archive_kind(payload,part):
                    child=container(path,payload,chain);child.name=part;batch.containers.append(child)
                else:batch.leaves.append(InputFile(path,part,payload,*detect(payload,part),source_chain=chain))
            if len(batch.leaves)>max_files or len(batch.containers)>61:raise ArchiveFailure('UNSUPPORTED_FORMAT',message='Archive exceeded processing limits.')
        batch.expanded_bytes=sum(len(payload) for _,payload in members);batch.directory_entries=entries
        if not batch.leaves and len(batch.containers)==1 and not batch.issues:batch.issues.append(issue(root.path,'ARCHIVE_CORRUPTED'))
        return batch
    except ArchiveFailure as failure:code,message=failure.code,failure.message
    except (NotImplementedError,ImportError,MemoryError,TimeoutError):code,message='UNSUPPORTED_FORMAT',None
    except Exception:code,message='ARCHIVE_CORRUPTED',None
    return InputBatch([], [root],len(data),1,issues=[issue(root.path,code,message)])


def prepare_files(files,*,max_files=20,max_bytes=50_000_000,decode=None):
    """Recursive standard preparation. Production supplies isolated decode calls."""
    if type(max_files)is not int or not 1<=max_files<=20 or type(max_bytes)is not int or not 1<=max_bytes<=50_000_000:
        raise ValidationError('Invalid bounded archive policy')
    if not files or len(files)>max_files or sum(len(data) for _,data in files)>max_bytes:raise ValidationError('Wire package exceeds limits')
    batch=InputBatch([],[],sum(len(data) for _,data in files),len(files));seen=set()
    wire_paths={safe_path(name).casefold() for name,_ in files}
    reserved_files=sum(not archive_kind(data,name) for name,data in files)
    reserved_bytes=sum(len(data) for name,data in files if not archive_kind(data,name))
    def visit(name,data,chain=(),depth=0):
        nonlocal reserved_files,reserved_bytes
        path=safe_path(name)
        if not data or path.casefold() in seen:raise ValidationError('Empty or duplicate wire document')
        seen.add(path.casefold())
        if not archive_kind(data,name):
            if not chain:reserved_files-=1;reserved_bytes-=len(data)
            if len(batch.leaves)>=max_files or batch.logical_bytes+len(data)>max_bytes:
                batch.issues.append(issue(path,'UNSUPPORTED_FORMAT','File exceeded package limits.'));return
            batch.leaves.append(InputFile(path,name if not chain else path[len(chain[-1]['path'])+1:],data,*detect(data,name),source_chain=chain));return
        root=container(path,data,chain)
        if depth>=3 or len(batch.containers)>=60:
            batch.containers.append(root);batch.issues.append(issue(path,'UNSUPPORTED_FORMAT','Archive nesting limit was reached.'));return
        result=(decode or expand_one)(path,data,max_files=max_files-len(batch.leaves)-reserved_files,max_bytes=min(max_bytes-batch.expanded_bytes,max_bytes-batch.logical_bytes-reserved_bytes),max_entries=2048-batch.directory_entries)
        if result.admission is not None:batch.admission=result.admission
        descendants=[file for file in result.leaves+result.containers if file.path!=path]
        if any(file.path.casefold() in wire_paths or file.path.casefold() in seen for file in descendants):
            batch.containers.append(root)
            batch.issues.append(issue(path,'ARCHIVE_CORRUPTED','Archive member path conflicts with another input.'))
            return
        batch.expanded_bytes+=result.expanded_bytes;batch.directory_entries+=result.directory_entries;batch.issues.extend(result.issues)
        if not result.containers:
            for leaf in result.leaves:leaf.source_chain=chain;batch.leaves.append(leaf)
            return
        batch.containers.append(root)
        for leaf in result.leaves:
            leaf.source_chain=chain+leaf.source_chain;batch.leaves.append(leaf);seen.add(leaf.path.casefold())
        for child in result.containers[1:]:visit(child.path,child.data,chain+child.source_chain,depth+1)
    for name,data in files:visit(name,data)
    return batch


def detect(data, name):
    if data.startswith(b'%PDF-'):
        return 'application/pdf', 'inspection_available'
    signatures=((b'\xff\xd8\xff','image/jpeg'),(b'\x89PNG\r\n\x1a\n','image/png'),
        (b'II*\x00','image/tiff'),(b'MM\x00*','image/tiff'),(b'BM','image/bmp'))
    for signature,mime in signatures:
        if data.startswith(signature): return mime,'ocr_required'
    if data.startswith(b'RIFF') and data[8:12]==b'WEBP': return 'image/webp','ocr_required'
    if data[4:8]==b'ftyp' and data[8:12] in {b'heic',b'heix',b'hevc',b'hevx',b'heif',b'mif1',b'msf1'}:
        return 'image/heic','ocr_required'
    if data.lstrip().startswith(b'{\\rtf'):
        return 'application/rtf', 'adapter_unavailable'
    if data.startswith(b'Rar!'):
        return 'application/vnd.rar', 'adapter_unavailable'
    if data.startswith(b'7z\xbc\xaf\x27\x1c'):
        return 'application/x-7z-compressed', 'adapter_unavailable'
    if data.startswith(b'\xd0\xcf\x11\xe0'):
        return 'application/x-ole-storage', 'adapter_unavailable'
    unavailable={'.dxf':'image/vnd.dxf','.dwg':'image/vnd.dwg','.svg':'image/svg+xml',
        '.rtf':'application/rtf','.doc':'application/msword','.xls':'application/vnd.ms-excel',
        '.html':'text/html','.htm':'text/html','.jpg':'image/jpeg','.jpeg':'image/jpeg',
        '.png':'image/png','.tif':'image/tiff','.tiff':'image/tiff','.bmp':'image/bmp',
        '.webp':'image/webp','.heic':'image/heic'}
    suffix='.'+name.rsplit('.',1)[-1].lower() if '.' in name else ''
    if suffix in unavailable:
        mime=unavailable[suffix]
        return mime, 'ocr_required' if mime in {'image/jpeg','image/png','image/tiff','image/bmp','image/webp','image/heic'} else 'adapter_unavailable'
    try:
        text = data.decode('utf-8-sig')
        if all(ord(c) >= 32 or c in '\t\n\r' for c in text):
            return ('text/csv' if name.lower().endswith('.csv') else 'text/plain'), 'inspection_available'
    except UnicodeError:
        pass
    # These extensions are only inert adapter hints. Decoding and validation
    # happen in the isolated reader under the explicit encoding profile.
    if name.lower().endswith(('.txt','.csv')):
        return ('text/csv' if name.lower().endswith('.csv') else 'text/plain'), 'inspection_available'
    return 'application/octet-stream', 'adapter_unavailable'


def canonical_metadata(value):
    import json
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


def encode_frame(metadata,payloads):
    encoded=canonical_metadata(metadata)
    if len(encoded)>FRAME_METADATA_BYTES: raise ValidationError('Archive metadata limit')
    return b''.join([struct.pack('<I',len(encoded)),encoded,*payloads])


def decode_frame(frame,collection,*,maximum_bytes,maximum_items):
    import json
    if len(frame)<4 or len(frame)>maximum_bytes+FRAME_METADATA_BYTES+4: raise ValidationError('Archive frame byte limit')
    length=struct.unpack_from('<I',frame)[0]
    if not 1<=length<=FRAME_METADATA_BYTES or 4+length>len(frame): raise ValidationError('Archive metadata framing')
    raw=frame[4:4+length]
    try: metadata=json.loads(raw)
    except (ValueError,UnicodeError): raise ValidationError('Archive metadata JSON') from None
    if not isinstance(metadata,dict) or canonical_metadata(metadata)!=raw: raise ValidationError('Noncanonical archive metadata')
    entries=metadata.get(collection)
    if not isinstance(entries,list) or not 1<=len(entries)<=maximum_items: raise ValidationError('Archive frame item count')
    position=4+length;payloads=[];total=0
    for entry in entries:
        if not isinstance(entry,dict) or type(entry.get('size'))is not int or entry['size']<1: raise ValidationError('Invalid archive frame size')
        size=entry['size'];total+=size
        if total>maximum_bytes or position+size>len(frame): raise ValidationError('Archive payload boundary')
        payloads.append(frame[position:position+size]);position+=size
    if position!=len(frame): raise ValidationError('Trailing archive frame payload')
    return metadata,payloads
