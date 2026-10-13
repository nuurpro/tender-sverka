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
from schemas import ParserProfile


def identity():
    root = Path(__file__).resolve().parent
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
              for name in ('__init__.py', 'entrypoint.py', 'readers.py', 'schemas.py')}
    return {'isolated': bool(sys.flags.isolated),
            'runtime_versions': {**{name: importlib.metadata.version(name)
                                   for name in ('pdfplumber', 'python-docx', 'openpyxl', 'pydantic',
                                                'lxml', 'Pillow', 'pdfminer.six', 'pypdfium2')},
                                 'python': sys.version},
            'code_hash': hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def main():
    # Bound before decoding JSON/base64. The subprocess also has address/CPU limits.
    request_raw = sys.stdin.buffer.read(70_000_001)
    if len(request_raw) > 70_000_000:
        raise DocumentReadError('input_limit', 'Parser request exceeds input limit')
    request = json.loads(request_raw)
    if request == {'probe': True}:
        sys.stdout.write(json.dumps(identity(), separators=(',', ':')))
        return
    if not isinstance(request, dict) or set(request) != {'filename', 'mime_type', 'content_base64', 'profile'}:
        raise DocumentReadError('protocol', 'Invalid parser request')
    profile = ParserProfile.model_validate(request['profile'])
    expected = identity()
    if profile.code_hash != expected['code_hash'] or profile.runtime_versions != expected['runtime_versions']:
        raise DocumentReadError('profile_mismatch', 'Parser code or runtime differs from trusted profile')
    if not isinstance(request['filename'], str) or not isinstance(request['mime_type'], str):
        raise DocumentReadError('protocol', 'Invalid document metadata')
    encoded = request['content_base64']
    if not isinstance(encoded, str) or len(encoded) > ((profile.limits.max_input_bytes + 2)//3)*4:
        raise DocumentReadError('input_limit', 'Encoded document exceeds input limit')
    content = base64.b64decode(encoded, validate=True)
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
