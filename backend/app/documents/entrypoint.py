"""Credential-free JSON subprocess protocol for the isolated native parser."""
from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

# Python -I excludes script paths. Bind only this trusted pure-source directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from readers import DocumentReadError, normalize
from schemas import ParserProfile, OCRProfile, NormalizedDocumentV2, parse_profile
from toolchain import CODE_FILES, NATIVE_PACKAGES, ocr_identity


def identity(ocr=False):
    root = Path(__file__).resolve().parent
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
              for name in CODE_FILES}
    result = {'isolated': bool(sys.flags.isolated),
            'runtime_versions': {**{name: importlib.metadata.version(name)
                                   for name in NATIVE_PACKAGES},
                                 'python': sys.version},
            'code_hash': hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    if ocr:
        result['ocr_toolchain']=ocr_identity()
    return result


def cpu_seconds():
    import resource
    own=resource.getrusage(resource.RUSAGE_SELF)
    children=resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime+own.ru_stime+children.ru_utime+children.ru_stime


def main():
    # Bound before decoding JSON/base64. The subprocess also has address/CPU limits.
    request_raw = sys.stdin.buffer.read(70_000_001)
    if len(request_raw) > 70_000_000:
        raise DocumentReadError('input_limit', 'Parser request exceeds input limit')
    request = json.loads(request_raw)
    if request in ({'probe': True},{'probe':True,'ocr':True}):
        sys.stdout.write(json.dumps(identity(request.get('ocr',False)), separators=(',', ':')))
        return
    if not isinstance(request, dict) or set(request) not in ({'filename', 'mime_type', 'content_base64', 'profile'},
                                                            {'filename', 'mime_type', 'content_base64', 'profile','frame'}):
        raise DocumentReadError('protocol', 'Invalid parser request')
    profile = parse_profile(request['profile'])
    expected = identity(isinstance(profile,OCRProfile))
    if profile.code_hash != expected['code_hash'] or profile.runtime_versions != expected['runtime_versions']:
        raise DocumentReadError('profile_mismatch', 'Parser code or runtime differs from trusted profile')
    if isinstance(profile,OCRProfile) and profile.toolchain!=expected.get('ocr_toolchain'):
        raise DocumentReadError('profile_mismatch','OCR model or executable identity changed')
    if isinstance(profile,OCRProfile) and profile.transform_policy!='affine-exif-osd-deskew-v3':
        raise DocumentReadError('profile_mismatch','TIFF coordinate policy changed; start a new explicit processing run')
    if not isinstance(request['filename'], str) or not isinstance(request['mime_type'], str):
        raise DocumentReadError('protocol', 'Invalid document metadata')
    encoded = request['content_base64']
    if not isinstance(encoded, str) or len(encoded) > ((profile.limits.max_input_bytes + 2)//3)*4:
        raise DocumentReadError('input_limit', 'Encoded document exceeds input limit')
    content = base64.b64decode(encoded, validate=True)
    if isinstance(profile,OCRProfile):
        from images import raster_manifest, ocr_frame
        if 'frame' in request:
            if type(request['frame']) is not int:
                raise DocumentReadError('protocol','Invalid OCR frame identity')
            blocks,coverage=ocr_frame(content,request['mime_type'],request['frame'],profile)
            sys.stdout.write(json.dumps({'frame':request['frame'],'blocks':[b.model_dump(mode='json') for b in blocks],
                'coverage':[c.model_dump(mode='json') for c in coverage],'cpu_seconds':cpu_seconds()},separators=(',',':')))
            return
        raster=request['mime_type'] in {'application/pdf','image/jpeg','image/png','image/tiff','image/heic','image/bmp','image/webp'} or content.startswith(b'%PDF-')
        manifest=raster_manifest(content,request['mime_type'],profile) if raster else None
        if manifest and manifest['format']!='pdf':
            result=NormalizedDocumentV2(content_sha256=hashlib.sha256(content).hexdigest(),filename=request['filename'],
                mime_type=request['mime_type'],format=manifest['format'],profile=profile,status='partial',blocks=(),coverage=(),
                physical_pages=len(manifest['frames']))
        else:
            result=normalize(content,request['filename'],request['mime_type'],profile)
        sys.stdout.write(json.dumps({'document':result.model_dump(mode='json'),'manifest':manifest,'cpu_seconds':cpu_seconds()},separators=(',',':')))
    else:
        if 'frame' in request:
            raise DocumentReadError('protocol','Native profile cannot request OCR')
        result = normalize(content, request['filename'], request['mime_type'], profile)
        sys.stdout.write(result.model_dump_json())


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Source content, filenames and exception inputs never escape on stderr.
        code = exc.code if isinstance(exc, DocumentReadError) else 'invalid_document'
        sys.stderr.write(json.dumps({'code': code, 'message': 'Native document parser refused the input'}))
        sys.exit(2)
