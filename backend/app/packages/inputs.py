"""Bounded RAM multipart intake; archive parsing is delegated to isolation."""
from python_multipart import MultipartParser
from python_multipart.multipart import parse_options_header
from app.workspace.repository import ValidationError
from app.documents.archives import InputFile, InputBatch, safe_path as _safe_path, detect, archive_kind
from app.documents.runner import DocumentRunnerError


def safe_path(name):
    try: return _safe_path(name)
    except ValueError as exc: raise ValidationError(str(exc)) from None


def prepare_files(files, *, max_files=20, max_bytes=50_000_000, settings=None, archive_adapter=None, cancelled=None):
    if archive_adapter is not None and (settings is None or settings.environment!='test'):
        raise ValueError('Archive adapter injection requires the explicit test environment')
    if not files or len(files)>max_files or sum(len(data) for _,data in files)>max_bytes:
        raise ValidationError('Wire package exceeds limits')
    identities=set()
    for name,data in files:
        path=safe_path(name)
        if not data or path.casefold() in identities:
            raise ValidationError('Empty or duplicate wire document')
        identities.add(path.casefold())
    if any(archive_kind(data,name) for name,data in files):
        if archive_adapter is not None:
            batch=archive_adapter(files,max_files=max_files,max_bytes=max_bytes)
            from app.documents.archive_runner import archive_snapshot
            batch.admission=archive_snapshot(settings,{'runtime_versions':{'adapter':'explicit-test'},'archive_toolchain':{'status':'explicit-test'}})
            return batch
        from app.documents.archive_runner import run_archive_admission
        return run_archive_admission(files,settings,max_files=max_files,max_bytes=max_bytes,cancelled=cancelled)
    return InputBatch([InputFile(safe_path(name),name,data,*detect(data,name)) for name,data in files],[],
                      sum(len(data) for _,data in files),len(files))


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
