"""Memory-only multipart and actual-byte bounded, non-executing ZIP expansion."""
from dataclasses import dataclass
import hashlib
from io import BytesIO
import stat
import struct
import time
import unicodedata
import zipfile
import zlib

from python_multipart import MultipartParser
from python_multipart.multipart import parse_options_header

from app.workspace.repository import ValidationError


def safe_path(name):
    if not isinstance(name, str) or not name or len(name) > 500 or any(unicodedata.category(c) in {'Cc','Cf'} for c in name):
        raise ValidationError('Unsafe filename')
    normalized = unicodedata.normalize('NFC', name.replace('\\', '/'))
    if ':' in normalized or normalized.startswith('/') or any(part in {'', '.', '..'} for part in normalized.split('/')):
        raise ValidationError('Unsafe filename')
    return normalized


@dataclass
class InputFile:
    path: str
    name: str
    data: bytes
    mime_type: str
    support: str
    role: str = 'document'

    @property
    def file_hash(self):
        return hashlib.sha256(self.data).hexdigest()


@dataclass
class InputBatch:
    leaves: list
    containers: list
    wire_bytes: int
    wire_files: int
    expanded_bytes: int = 0

    @property
    def logical_bytes(self):
        return sum(len(file.data) for file in self.leaves)


def validate_zip_directory(data,deadline):
    """Scan bounded records before ZipFile eagerly allocates its directory.

    Accept single-disk ZIP and ordinary fixed-record ZIP64 using consistent
    absolute directory offsets. Inert prefixes with absolute offsets are kept
    as original bytes. Layouts requiring relative-offset prefix correction,
    extensible ZIP64 end records and oversized metadata are explicit errors.
    Neither EOCD counts nor declared directory sizes can bypass actual records.
    """
    if time.monotonic()>deadline:
        raise ValidationError('Archive inspection timed out')
    end=data.rfind(b'PK\x05\x06',max(0,len(data)-65557))
    if end<0 or end+22>len(data):
        raise ValidationError('Missing ZIP directory end')
    _,disk,directory_disk,on_disk,count,size,offset,comment=struct.unpack_from('<4s4H2IH',data,end)
    if end+22+comment!=len(data) or disk!=0 or directory_disk!=0 or on_disk!=count:
        raise ValidationError('Invalid or multi-disk ZIP directory')
    directory_end=end
    locator=end-20
    if locator>=0 and data[locator:locator+4]==b'PK\x06\x07':
        _,zip64_disk,record_offset,disks=struct.unpack_from('<4sIQI',data,locator)
        if zip64_disk!=0 or disks!=1 or record_offset!=locator-56 or record_offset<0:
            raise ValidationError('Unsupported ZIP64 directory layout')
        signature,record_size,made,needed,disk,directory_disk,on_disk,count,size,offset=struct.unpack_from('<4sQ2H2I4Q',data,record_offset)
        if signature!=b'PK\x06\x06' or record_size!=44 or disk!=0 or directory_disk!=0 or on_disk!=count:
            raise ValidationError('Invalid or unsupported ZIP64 end record')
        directory_end=record_offset
    elif count==65535 or size==0xffffffff or offset==0xffffffff:
        raise ValidationError('Missing ZIP64 end record')
    if count>2048 or size>8_000_000 or offset<0 or offset+size!=directory_end:
        raise ValidationError('ZIP directory count, metadata size or layout limit')
    position,actual,names=offset,0,set()
    while position<directory_end:
        if time.monotonic()>deadline:
            raise ValidationError('Archive inspection timed out')
        if position+46>directory_end:
            raise ValidationError('Truncated ZIP directory record')
        record=struct.unpack_from('<4s6H3I5H2I',data,position)
        name_length,extra_length,comment_length=record[10:13]
        next_position=position+46+name_length+extra_length+comment_length
        actual+=1
        if record[0]!=b'PK\x01\x02' or actual>2048 or not 1<=name_length<=2000 or extra_length>8192 or comment_length>8192 or next_position>directory_end:
            raise ValidationError('ZIP directory record limit')
        if record[13]!=0 or record[3]&1 or stat.S_ISLNK(record[15]>>16):
            raise ValidationError('Encrypted, symlink or multi-disk ZIP member')
        raw=data[position+46:position+46+name_length].decode('utf-8' if record[3]&0x800 else 'cp437')
        path=safe_path(raw.rstrip('/') if raw.endswith('/') else raw)
        if path.casefold() in names:
            raise ValidationError('Ambiguous ZIP directory member')
        names.add(path.casefold())
        position=next_position
    if actual!=count or not actual:
        raise ValidationError('ZIP actual directory count mismatch or empty archive')
    if time.monotonic()>deadline:
        raise ValidationError('Archive inspection timed out')


def _zip(data, deadline, max_bytes):
    z=None
    try:
        validate_zip_directory(data,deadline)
        z = zipfile.ZipFile(BytesIO(data))
        if len(z.infolist()) > 2048:
            raise ValidationError('ZIP entry limit exceeded')
        offsets=sorted(info.header_offset for info in z.infolist())+[z.start_dir]
        if len(set(offsets))!=len(offsets):
            raise ValidationError('Overlapping ZIP local headers')
        next_offset=dict(zip(offsets,offsets[1:]))
        names, total, contents = set(), 0, []
        for info in z.infolist():
            if time.monotonic() > deadline:
                raise ValidationError('Archive inspection timed out')
            raw_name = info.orig_filename
            path = safe_path(raw_name.rstrip('/') if info.is_dir() else raw_name)
            identity = path.casefold()
            if identity in names or info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16):
                raise ValidationError('Ambiguous, encrypted or symlink archive member')
            names.add(identity)
            # Declared length is an early rejection only; actual bytes are counted.
            if info.file_size > max_bytes:
                raise ValidationError('Archive expansion exceeds byte limit')
            offset=info.header_offset
            if offset<0 or offset+30>len(data) or data[offset:offset+4]!=b'PK\x03\x04':
                raise ValidationError('Invalid ZIP local header')
            flags,method=struct.unpack_from('<HH',data,offset+6)
            name_size,extra_size=struct.unpack_from('<HH',data,offset+26)
            start=offset+30+name_size+extra_size;end=start+info.compress_size
            if (flags!=info.flag_bits or method!=info.compress_type or end>next_offset[offset]
                or data[offset+30:offset+30+name_size]!=raw_name.encode('utf-8' if flags&0x800 else 'cp437')):
                raise ValidationError('Inconsistent ZIP local payload')
            if method not in {zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED}:
                raise ValidationError('Unsupported ZIP compression')
            output,checksum=bytearray(),0
            inflater=zlib.decompressobj(-15) if method==zipfile.ZIP_DEFLATED else None
            for position in range(start,end,64*1024):
                compressed=data[position:min(end,position+64*1024)]
                while compressed:
                    if time.monotonic()>deadline:
                        raise ValidationError('Archive inspection timed out')
                    if inflater is None:
                        chunk,compressed=compressed,b''
                    else:
                        chunk=inflater.decompress(compressed,min(64*1024,max_bytes-total+1))
                        compressed=inflater.unconsumed_tail
                        if inflater.unused_data:
                            raise ValidationError('Trailing ZIP compressed payload')
                    total+=len(chunk);output.extend(chunk);checksum=zlib.crc32(chunk,checksum)
                    if total>max_bytes:
                        raise ValidationError('Archive expansion exceeds actual byte budget')
            if (inflater is not None and not inflater.eof) or len(output)!=info.file_size or checksum!=info.CRC:
                raise ValidationError('ZIP physical payload differs from directory')
            if info.is_dir():
                if output:
                    raise ValidationError('ZIP directory contains hidden content')
                continue
            if not output:
                raise ValidationError('Empty archive member')
            contents.append((info.filename, bytes(output)))
        return contents
    except ValidationError:
        raise
    except Exception:
        raise ValidationError('Invalid ZIP archive') from None
    finally:
        if z is not None:
            z.close()


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


def prepare_files(files, *, max_files=20, max_bytes=50_000_000):
    if not files or len(files) > max_files:
        raise ValidationError('Package requires one to twenty wire files')
    wire_bytes = sum(len(data) for _, data in files)
    if wire_bytes > max_bytes:
        raise ValidationError('Wire byte limit exceeded')
    batch = InputBatch([], [], wire_bytes, len(files))
    seen, wire_seen = set(), set()
    deadline = time.monotonic() + 5

    def visit(name, data, prefix='', depth=0):
        path = safe_path(name)
        full_path = safe_path(prefix + path)
        if not data:
            raise ValidationError('Empty document')
        if full_path.casefold() in seen:
            raise ValidationError('Duplicate document path')
        seen.add(full_path.casefold())
        if data.startswith(b'PK\x03\x04') or data.startswith(b'PK\x05\x06') or path.lower().endswith('.zip'):
            members = _zip(data, deadline, max_bytes-batch.expanded_bytes)
            batch.expanded_bytes += sum(len(member_data) for _,member_data in members)
            member_names = {p for p, _ in members}
            # Office ZIP containers are documents; internal expansion is bounded
            # and validated before any office parser sees their original bytes.
            office = None
            odf = next((payload.decode('ascii') for part,payload in members if part=='mimetype'
                and payload in {b'application/vnd.oasis.opendocument.text',b'application/vnd.oasis.opendocument.spreadsheet',
                    b'application/vnd.oasis.opendocument.presentation'}),None)
            if odf:
                batch.leaves.append(InputFile(full_path,name,data,odf,'adapter_unavailable'))
                return
            if '[Content_Types].xml' in member_names:
                if 'word/document.xml' in member_names:
                    office = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                elif 'xl/workbook.xml' in member_names:
                    office = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                elif 'ppt/presentation.xml' in member_names:
                    office = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
            if office is None:
                extension_mimes={'.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    '.odt':'application/vnd.oasis.opendocument.text','.ods':'application/vnd.oasis.opendocument.spreadsheet',
                    '.odp':'application/vnd.oasis.opendocument.presentation'}
                suffix='.'+path.rsplit('.',1)[-1].lower() if '.' in path else ''
                office=extension_mimes.get(suffix)
            if office:
                support='adapter_unavailable' if office.startswith('application/vnd.oasis.') else 'inspection_available'
                batch.leaves.append(InputFile(full_path, name, data, office, support))
            else:
                if depth >= 3 or not members:
                    raise ValidationError('Archive depth or empty archive limit')
                batch.containers.append(InputFile(full_path, name, data, 'application/zip', 'provenance', 'archive'))
                if len(batch.containers) > 60:
                    raise ValidationError('Archive container limit')
                for member_name, member_data in members:
                    visit(member_name, member_data, full_path + '/', depth + 1)
        else:
            mime, support = detect(data, path)
            batch.leaves.append(InputFile(full_path, name, data, mime, support))
        if len(batch.leaves) > max_files or batch.logical_bytes > max_bytes:
            raise ValidationError('Expanded logical package exceeds limits')
    for name, data in files:
        identity = safe_path(name).casefold()
        if identity in wire_seen:
            raise ValidationError('Duplicate wire filename')
        wire_seen.add(identity)
        visit(name, data)
    if not batch.leaves:
        raise ValidationError('Empty logical package')
    return batch


async def receive_multipart(stream, content_type, *, max_files=20, max_bytes=50_000_000):
    """Receive bounded plaintext in RAM. Never invoke Starlette form/UploadFile."""
    try:
        media, params = parse_options_header(content_type)
        boundary = params.get(b'boundary', b'')
        if media != b'multipart/form-data' or not 1 <= len(boundary) <= 200:
            raise ValidationError('Invalid multipart boundary')
        files, fields, part = [], {}, {}
        total, received, completed = 0, 0, False
        last_tail, ending = b'', None
        end_marker=b'\r\n--'+boundary+b'--'
        def begin():
            part.clear(); part.update(headers={}, field=bytearray(), value=bytearray(), data=bytearray(), header_bytes=0)
        def header_field(data, start, end):
            part['header_bytes'] += end-start
            if part['header_bytes'] > 8192:
                raise ValidationError('Multipart header limit')
            part['field'].extend(data[start:end])
        def header_value(data, start, end):
            part['header_bytes'] += end-start
            if part['header_bytes'] > 8192:
                raise ValidationError('Multipart header limit')
            part['value'].extend(data[start:end])
        def header_end():
            key = bytes(part['field']).lower()
            if key in part['headers'] or len(part['headers']) >= 8:
                raise ValidationError('Duplicate or excessive multipart headers')
            part['headers'][key] = bytes(part['value']); part['field'].clear(); part['value'].clear()
        def headers_done():
            disposition, options = parse_options_header(part['headers'].get(b'content-disposition', b''))
            if disposition != b'form-data' or b'name' not in options:
                raise ValidationError('Invalid multipart disposition')
            name = options[b'name'].decode('utf-8')
            filename = options.get(b'filename')
            if filename is not None:
                if name != 'files' or len(files) >= max_files:
                    raise ValidationError('Unexpected or excessive file parts')
                part['filename'] = filename.decode('utf-8'); safe_path(part['filename'])
            elif name not in {'kind', 'participant_id'} or name in fields:
                raise ValidationError('Unexpected or duplicate multipart field')
            part['name'] = name
        def data_received(data, start, end):
            nonlocal total
            if 'filename' in part:
                total += end-start
                if total > max_bytes:
                    raise ValidationError('Actual file byte limit')
            elif len(part['data']) + end-start > 256:
                raise ValidationError('Multipart field limit')
            part['data'].extend(data[start:end])
        def end():
            if 'filename' in part:
                files.append((part['filename'], bytes(part['data'])))
            else:
                fields[part['name']] = part['data'].decode('utf-8')
            part.clear()
        def finished():
            nonlocal completed
            completed = True
        parser = MultipartParser(boundary, {'on_part_begin': begin, 'on_header_field': header_field,
            'on_header_value': header_value, 'on_header_end': header_end, 'on_headers_finished': headers_done,
            'on_part_data': data_received, 'on_part_end': end, 'on_end': finished})
        async for chunk in stream:
            received += len(chunk)
            if received > max_bytes + 200_000:
                raise ValidationError('Multipart wire limit')
            if completed:
                ending+=chunk
                if ending not in {b'',b'\r',b'\r\n'}:
                    raise ValidationError('Unexpected multipart epilogue')
                continue
            parser.write(chunk)
            combined=last_tail+chunk
            if completed:
                position=combined.find(end_marker)
                if position<0:
                    raise ValidationError('Invalid multipart ending')
                ending=combined[position+len(end_marker):]
                if ending not in {b'',b'\r',b'\r\n'}:
                    raise ValidationError('Unexpected multipart epilogue')
            last_tail=combined[-len(end_marker):]
        parser.finalize()
        if not completed or ending not in {b'',b'\r\n'} or not files or any(not data for _, data in files):
            raise ValidationError('Incomplete or empty multipart upload')
        return fields, files
    except ValidationError:
        raise
    except Exception:
        raise ValidationError('Malformed multipart upload') from None
