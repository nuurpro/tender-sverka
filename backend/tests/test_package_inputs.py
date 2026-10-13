import asyncio
from io import BytesIO
import zipfile

import pytest


def test_csv_rtf_and_odf_remain_distinct_native_or_pending_leaf_originals():
    from app.packages.inputs import prepare_files
    from io import BytesIO
    import zipfile
    csv = prepare_files([('data.csv',b'a,b\n1,2\n')]).leaves[0]
    assert csv.mime_type == 'text/csv'
    rtf = prepare_files([('renamed.txt',br'{\rtf1\ansi original text}')]).leaves[0]
    assert rtf.mime_type == 'application/rtf' and rtf.support == 'adapter_unavailable'
    output = BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        archive.writestr('mimetype','application/vnd.oasis.opendocument.text')
        archive.writestr('content.xml','<document>original content</document>')
    batch = prepare_files([('source.odt',output.getvalue())])
    assert len(batch.leaves)==1 and not batch.containers
    assert batch.leaves[0].data == output.getvalue()
    assert batch.leaves[0].mime_type == 'application/vnd.oasis.opendocument.text'
    assert batch.leaves[0].support == 'adapter_unavailable'


def test_ascii_drawings_and_malformed_office_zip_are_not_plain_text_evidence():
    from app.packages.inputs import prepare_files
    from io import BytesIO
    import zipfile
    drawing=prepare_files([('drawing.dxf',b'0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n')]).leaves[0]
    assert drawing.support=='adapter_unavailable' and drawing.mime_type!='text/plain'
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('unrecognized.xml','<document>incomplete office package</document>')
    original=prepare_files([('incomplete.docx',stream.getvalue())])
    assert len(original.leaves)==1 and not original.containers
    assert original.leaves[0].data==stream.getvalue()


@pytest.mark.parametrize('budget',[1000,1000000])
def test_forged_zip_size_and_prefix_crc_cannot_truncate_an_original_member(budget):
    from io import BytesIO
    import struct,zipfile,zlib
    from app.packages.inputs import prepare_files
    from app.workspace.repository import ValidationError
    original=b'original prefix '+b'x'*100000
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('a.txt',original)
    forged=bytearray(stream.getvalue());prefix=512;crc=zlib.crc32(original[:prefix])
    central=forged.index(b'PK\x01\x02')
    for offset in (14,central+16):
        struct.pack_into('<I',forged,offset,crc)
    for offset in (22,central+24):
        struct.pack_into('<I',forged,offset,prefix)
    with pytest.raises(ValidationError):
        prepare_files([('forged.zip',bytes(forged))],max_bytes=budget)

from app.workspace.repository import ValidationError


def inputs():
    try:
        from app.packages.inputs import prepare_files, receive_multipart
    except ImportError:
        pytest.fail('Bounded package receiving/inspection is missing')
    return prepare_files, receive_multipart


def archive(entries):
    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return stream.getvalue()


def test_zip_provenance_and_measured_independent_wire_expansion_limits():
    prepare, _ = inputs()
    original = archive([('folder/a.txt', b'hello'), ('b.txt', b'world')])
    batch = prepare([('archive.zip', original)], max_files=2, max_bytes=1000)
    assert batch.wire_bytes == len(original) and batch.logical_bytes == 10
    assert len(batch.leaves) == 2 and len(batch.containers) == 1
    assert batch.leaves[0].path == 'archive.zip/folder/a.txt'
    assert batch.containers[0].data == original
    assert batch.leaves[0].file_hash and batch.leaves[0].mime_type == 'text/plain'
    with pytest.raises(ValidationError):
        prepare([('bomb.zip', archive([('a.txt', b'a' * 1001)]))], max_bytes=1000)
    with pytest.raises(ValidationError):
        prepare([('many.zip', archive([(f'{i}.txt', b'a') for i in range(21)]))])


@pytest.mark.parametrize('name', ['../evil.txt', '/root.txt', 'C:\\evil.txt', 'nul\x00.txt', 'a/../x.txt', 'a//x.txt', 'a\n.txt'])
def test_unsafe_names_rejected_at_any_depth(name):
    prepare, _ = inputs()
    with pytest.raises(ValidationError):
        prepare([(name, b'x')])
    with pytest.raises(ValidationError):
        zipped = archive([(name.replace('\x00', 'X'), b'x')])
        if '\x00' in name:
            zipped = zipped.replace(b'nulX.txt', b'nul\x00.txt')
        prepare([('safe.zip', zipped)])


def test_duplicates_symlinks_empty_corrupt_and_nested_depth_refused():
    prepare, _ = inputs()
    for entries in [[('a.txt', b'x'), ('a.txt', b'y')], [('a.txt', b'')]]:
        with pytest.raises(ValidationError):
            prepare(entries)
    with pytest.raises(ValidationError):
        prepare([('bad.zip', b'PK\x03\x04bad')])
    with pytest.raises(ValidationError):
        prepare([('dup.zip', archive([('A.txt', b'a'), ('a.txt', b'b')]))])
    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        info = zipfile.ZipInfo('link'); info.create_system = 3; info.external_attr = 0o120777 << 16
        z.writestr(info, 'target')
    with pytest.raises(ValidationError):
        prepare([('link.zip', stream.getvalue())])
    nested = b'leaf'
    for _ in range(5):
        nested = archive([('deep.zip', nested)])
    with pytest.raises(ValidationError):
        prepare([('nested.zip', nested)])


def test_ooxml_is_document_and_unknown_formats_explicitly_pending():
    from docx import Document
    prepare, _ = inputs()
    stream = BytesIO(); doc = Document(); doc.add_paragraph('actual office text'); doc.save(stream)
    batch = prepare([('renamed.bin', stream.getvalue()), ('legacy.rar', b'Rar!\x1a\x07\x00payload')])
    assert len(batch.leaves) == 2 and not batch.containers
    assert batch.leaves[0].mime_type.endswith('wordprocessingml.document')
    assert batch.leaves[1].support == 'adapter_unavailable'


def test_multipart_counts_stream_bytes_and_requires_final_boundary():
    _, receive = inputs()
    body = b'--b\r\nContent-Disposition: form-data; name="kind"\r\n\r\ntender\r\n--b\r\nContent-Disposition: form-data; name="files"; filename="a.txt"\r\nContent-Type: evil/type\r\n\r\nhello\r\n--b--\r\n'
    async def stream(data):
        for start in range(0, len(data), 3):
            yield data[start:start+3]
    result = asyncio.run(receive(stream(body), 'multipart/form-data; boundary=b', max_bytes=5))
    assert result == ({'kind': 'tender'}, [('a.txt', b'hello')])
    for changed in [body[:-10], body.replace(b'hello', b'helloo')]:
        with pytest.raises(ValidationError):
            asyncio.run(receive(stream(changed), 'multipart/form-data; boundary=b', max_bytes=5))
    with pytest.raises(ValidationError):
        asyncio.run(receive(stream(body), 'multipart/form-data; boundary=' + 'x'*201))


def test_nested_archive_cumulative_expansion_budget_and_unicode_controls():
    prepare,_=inputs()
    inner=archive([('a.txt',b'a'*900)])
    outer=archive([('inner.zip',inner)])
    assert len(inner)+900>1000 and len(outer)<1000
    with pytest.raises(ValidationError):
        prepare([('outer.zip',outer)],max_bytes=1000)
    with pytest.raises(ValidationError):
        prepare([('unsafe\u0085name.txt',b'x')])


def test_multipart_rejects_unexpected_epilogue_and_huge_headers():
    _,receive=inputs()
    body=b'--b\r\nContent-Disposition: form-data; name="files"; filename="a.txt"\r\n\r\nx\r\n--b--\r\n'
    async def stream(data):
        yield data
    for changed in [body+b'not a multipart body',body.replace(b'filename="a.txt"',b'filename="'+b'a'*9000+b'"')]:
        with pytest.raises(ValidationError):
            asyncio.run(receive(stream(changed),'multipart/form-data; boundary=b'))


def test_expired_archive_deadline_never_constructs_eager_zip_directory(monkeypatch):
    import time
    from app.packages.inputs import _zip
    data=archive([('a.txt',b'contents')]);constructed=[];constructor=zipfile.ZipFile
    def observed_constructor(*args,**kwargs):
        constructed.append(True);return constructor(*args,**kwargs)
    monkeypatch.setattr(zipfile,'ZipFile',observed_constructor)
    with pytest.raises(ValidationError):
        _zip(data,time.monotonic()-1,50_000_000)
    assert not constructed,'An expired request allocated the complete ZIP central directory'


def test_actual_central_directory_entries_are_bounded_before_zipfile_even_if_eocd_lies(monkeypatch):
    import struct
    import time
    from app.packages.inputs import _zip
    data=bytearray(archive([(f'{i:06x}',b'x') for i in range(2050)]))
    eocd=len(data)-22
    struct.pack_into('<HH',data,eocd+8,1,1)  # Dishonest total counts cannot weaken the scan.
    constructed=[];constructor=zipfile.ZipFile
    def observed_constructor(*args,**kwargs):
        constructed.append(True);return constructor(*args,**kwargs)
    monkeypatch.setattr(zipfile,'ZipFile',observed_constructor)
    with pytest.raises(ValidationError):
        _zip(bytes(data),time.monotonic()+5,50_000_000)
    assert not constructed,'The declared count bypassed pre-allocation real-entry accounting'


def test_small_zip64_archive_has_bounded_valid_directory_and_preserves_original():
    import struct
    prepare,_=inputs()
    original=archive([('a.txt',b'actual ZIP64 original')])
    eocd=len(original)-22
    _,disk,start,on_disk,count,size,offset,comment=struct.unpack_from('<4s4H2IH',original,eocd)
    end64=struct.pack('<4sQ2H2I4Q',b'PK\x06\x06',44,45,45,0,0,count,count,size,offset)
    locator=struct.pack('<4sIQI',b'PK\x06\x07',0,eocd,1)
    end=struct.pack('<4s4H2IH',b'PK\x05\x06',0,0,65535,65535,0xffffffff,0xffffffff,0)
    data=original[:eocd]+end64+locator+end
    batch=prepare([('small.zip',data)])
    assert batch.leaves[0].data==b'actual ZIP64 original' and batch.containers[0].data==data


def test_inert_prefix_with_consistent_absolute_zip_offsets_is_preserved_without_execution():
    prepare,_=inputs()
    stream=BytesIO(b'MZ'+b'\0'*40)
    with zipfile.ZipFile(stream,'a') as zipped:
        zipped.writestr('a.txt',b'exact leaf original')
    original=stream.getvalue()
    batch=prepare([('absolute-offset.zip',original)])
    assert batch.leaves[0].data==b'exact leaf original'
    assert batch.containers[0].data==original
