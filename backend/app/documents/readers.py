"""Bounded, pure native readers. Production use is exclusively via runner.py.

There is no OCR, rendering, conversion, formula evaluation or CAD adapter here.
Uninterpreted source regions remain visible in coverage rather than disappearing.
"""
from __future__ import annotations

import csv
import hashlib
import io
from pathlib import PurePosixPath
import re
import struct
import warnings
import zipfile
import zlib
from xml.etree import ElementTree as ET
from xml.parsers import expat

if __package__:
    from .schemas import (CellAnchor, CoverageEntry, DocumentBlock, NormalizedDocument,
                          ParserProfile, PartAnchor, PDFAnchor, TextAnchor, TextMapSegment, XMLAnchor)
else:
    from schemas import (CellAnchor, CoverageEntry, DocumentBlock, NormalizedDocument,
                         ParserProfile, PartAnchor, PDFAnchor, TextAnchor, TextMapSegment, XMLAnchor)


class DocumentReadError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _fail(code, message):
    raise DocumentReadError(code, message)


def _normalized(text, collapse):
    if not collapse:
        return text, (() if not text else (TextMapSegment(normalized_start=0, normalized_end=len(text), original_start=0, original_end=len(text)),))
    pieces, mapping, cursor = [], [], 0
    tokens = list(re.finditer(r'\S+|\s+', text))
    for index, token in enumerate(tokens):
        value = token.group()
        if value.isspace():
            if index == 0 or index == len(tokens)-1:
                continue
            value = ' '
        pieces.append(value)
        mapping.append(TextMapSegment(normalized_start=cursor, normalized_end=cursor+len(value),
                                      original_start=token.start(), original_end=token.end()))
        cursor += len(value)
    return ''.join(pieces), tuple(mapping)


class _Builder:
    def __init__(self, content, filename, mime_type, profile, format):
        self.content = content
        self.filename = filename
        self.mime_type = mime_type
        self.profile = profile
        self.format = format
        self.blocks = []
        self.coverage = []
        self.warnings = []
        self.characters = 0
        self.output_estimate = 0
        self.pages = None
        self.pixels = 0

    def coverage_add(self, id, kind, state, anchor, reason=None, block_ids=()):
        if len(self.coverage) >= self.profile.limits.max_blocks * 4:
            _fail('coverage_limit', 'Document coverage exceeds block limit')
        entry = CoverageEntry(id=id, kind=kind, state=state, anchor=anchor, reason=reason, block_ids=block_ids)
        self.output_estimate += len(entry.model_dump_json().encode())
        self._output_limit()
        self.coverage.append(entry)

    def add(self, id, kind, text, anchor, original_xml=None, flags=()):
        if len(self.blocks) >= self.profile.limits.max_blocks:
            _fail('block_limit', 'Document exceeds block limit')
        if len(text) > self.profile.limits.max_block_chars:
            _fail('block_limit', 'Document block exceeds character limit')
        self.characters += len(text)
        if self.characters > self.profile.limits.max_text_chars:
            _fail('text_limit', 'Document exceeds character limit')
        normalized, mapping = _normalized(text, self.profile.normalize_whitespace)
        block_id = 'b-'+hashlib.sha256(id.encode('utf-8')).hexdigest()[:32]
        block = DocumentBlock(id=block_id, kind=kind, original_text=text, normalized_text=normalized,
                              normalization_map=mapping, original_xml=original_xml, anchor=anchor, review_flags=flags)
        self.output_estimate += len(block.model_dump_json().encode())
        self._output_limit()
        self.blocks.append(block)
        self.coverage_add('coverage:'+id, 'native_text', 'text_extracted', anchor, block_ids=(block_id,))
        return block

    def _output_limit(self):
        if self.output_estimate > self.profile.limits.max_output_bytes:
            _fail('output_limit', 'Normalized output exceeds byte limit')

    def finish(self):
        if not self.coverage:
            self.coverage_add('coverage:empty', 'unknown', 'failed', PartAnchor(part=self.filename), 'No native evidence was found')
        pending = any(c.state not in {'extracted', 'text_extracted', 'corrected'} for c in self.coverage)
        status = 'unavailable' if all(c.state == 'adapter_unavailable' for c in self.coverage) else ('partial' if pending else 'complete')
        if self.pages is None:
            self.warnings.append('Physical page count is unknown; this document cannot pass the physical page budget gate')
        result = NormalizedDocument(content_sha256=hashlib.sha256(self.content).hexdigest(), filename=self.filename,
                                    mime_type=self.mime_type, format=self.format, profile=self.profile, status=status,
                                    blocks=tuple(self.blocks), coverage=tuple(self.coverage), physical_pages=self.pages,
                                    analysis_allowed=False,
                                    warnings=tuple(self.warnings))
        if len(result.model_dump_json().encode()) > self.profile.limits.max_output_bytes:
            _fail('output_limit', 'Normalized output exceeds byte limit')
        return result


def _package(content, limits):
    """Read every actual decompressed payload, never trust ZIP size declarations."""
    if not zipfile.is_zipfile(io.BytesIO(content)):
        _fail('invalid_zip', 'Document is not a valid ZIP package')
    result, actual_total = {}, 0
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > limits.max_zip_members:
                _fail('zip_limit', 'ZIP package exceeds member limit')
            offsets = sorted(info.header_offset for info in members) + [archive.start_dir]
            if len(set(offsets)) != len(offsets):
                _fail('invalid_zip', 'Overlapping ZIP member headers')
            next_offset = dict(zip(offsets, offsets[1:]))
            for info in members:
                name = info.filename
                path = PurePosixPath(name)
                if (not name or '\\' in name or '\x00' in name or path.is_absolute() or '..' in path.parts
                        or name in result or info.flag_bits & 1 or (info.external_attr >> 16) & 0o170000 == 0o120000):
                    _fail('invalid_zip', 'Invalid ZIP package member')
                if info.is_dir():
                    if info.file_size or info.compress_size:
                        _fail('invalid_zip', 'ZIP directory contains a hidden payload')
                    continue
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    _fail('invalid_zip', 'Unsupported ZIP compression')
                if info.file_size > limits.max_zip_member_bytes or info.file_size > max(1, info.compress_size)*limits.max_zip_ratio:
                    _fail('zip_limit', 'ZIP package size or ratio exceeds limit')
                # ZipExtFile truncates decompression to a declared file_size.
                # Read the physical compressed payload ourselves so forged size
                # and matching prefix CRC cannot hide a decompression bomb.
                offset = info.header_offset
                if offset < 0 or content[offset:offset+4] != b'PK\x03\x04' or offset+30 > len(content):
                    _fail('invalid_zip', 'Invalid ZIP local header')
                filename_size, extra_size = struct.unpack_from('<HH', content, offset+26)
                start = offset+30+filename_size+extra_size
                end = start+info.compress_size
                if end > next_offset[offset] or end > len(content):
                    _fail('invalid_zip', 'ZIP compressed payload overlaps another member')
                local_name = content[offset+30:offset+30+filename_size]
                expected_name = name.encode('utf-8' if info.flag_bits & 0x800 else 'cp437')
                if local_name != expected_name:
                    _fail('invalid_zip', 'ZIP local filename differs from directory')
                data, checksum = bytearray(), 0
                decompressor = zlib.decompressobj(-15) if info.compress_type == zipfile.ZIP_DEFLATED else None
                for compressed_start in range(start, end, 65536):
                    compressed = content[compressed_start:min(end, compressed_start+65536)]
                    while compressed:
                        if decompressor is not None:
                            chunk = decompressor.decompress(compressed, min(65536, limits.max_zip_member_bytes + 1 - len(data)))
                            compressed = decompressor.unconsumed_tail
                            if decompressor.unused_data:
                                _fail('invalid_zip', 'ZIP payload has trailing compressed data')
                        else:
                            chunk, compressed = compressed, b''
                        data.extend(chunk)
                        checksum = zlib.crc32(chunk, checksum)
                        actual_total += len(chunk)
                        if (len(data) > limits.max_zip_member_bytes or actual_total > limits.max_zip_uncompressed_bytes
                                or len(data) > max(1, info.compress_size)*limits.max_zip_ratio):
                            _fail('zip_limit', 'Actual ZIP payload exceeds size or ratio limit')
                if decompressor is not None and not decompressor.eof:
                    _fail('invalid_zip', 'Truncated ZIP compressed payload')
                if len(data) != info.file_size or checksum != info.CRC:
                    _fail('invalid_zip', 'ZIP payload size differs from declaration')
                result[name] = bytes(data)
    except DocumentReadError:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, ValueError, zlib.error, struct.error, UnicodeError):
        _fail('invalid_zip', 'Malformed ZIP package payload')
    # Even uninterpreted XML must be syntactically bounded and entity-free.
    for name, payload in result.items():
        if name.endswith(('.xml', '.rels')):
            _xml(payload, limits)
    return result


def _image_header(builder, payload):
    """Read container dimensions only. Never decode pixels or execute media."""
    from PIL import Image, UnidentifiedImageError
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                width, height = image.size
                if width <= 0 or height <= 0:
                    _fail('pixel_limit', 'Embedded image has invalid pixel dimensions')
                builder.pixels += width*height
                if builder.pixels > builder.profile.limits.max_pixels:
                    _fail('pixel_limit', 'Embedded source images exceed pixel limit')
                return width, height
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        _fail('pixel_limit', 'Embedded source images exceed pixel limit')
    except DocumentReadError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return None


def _xml(data, limits):
    """Reject DTD/entities and bound syntax before constructing ElementTree.

    Expat byte spans retain the exact original element XML, including prefixes.
    """
    if len(data) > limits.max_xml_bytes:
        _fail('xml_limit', 'XML part exceeds byte limit')
    if data.startswith(b'\xff\xfe'):
        encoding = 'utf-16-le'
    elif data.startswith(b'\xfe\xff'):
        encoding = 'utf-16-be'
    else:
        declaration = re.match(br'\s*<\?xml[^>]*encoding=[\"\']([^\"\']+)', data)
        encoding = declaration.group(1).decode('ascii') if declaration else 'utf-8'
    try:
        end_prefix, end_marker = '</'.encode(encoding), '>'.encode(encoding)
    except LookupError:
        _fail('invalid_xml', 'Invalid XML encoding')
    parser = expat.ParserCreate()
    starts, spans, stack = [], [], []
    def reject(*args):
        _fail('invalid_xml', 'XML entities and document types are forbidden')
    def start(name, attrs):
        if len(starts) >= limits.max_xml_nodes or len(stack) >= limits.max_xml_depth:
            _fail('xml_limit', 'XML nodes or depth exceeds limit')
        offset = parser.CurrentByteIndex
        starts.append(offset)
        spans.append(None)
        stack.append(len(starts)-1)
    def end(name):
        index = stack.pop()
        offset = parser.CurrentByteIndex
        # For an empty tag Expat points after />; for a normal tag at </.
        if data[offset:offset+len(end_prefix)] == end_prefix:
            stop = data.find(end_marker, offset)+len(end_marker)
        else:
            stop = offset
        spans[index] = (starts[index], stop)
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    try:
        parser.Parse(data, True)
        root = ET.fromstring(data)
    except DocumentReadError:
        raise
    except (expat.ExpatError, ET.ParseError, ValueError, LookupError):
        _fail('invalid_xml', 'Malformed XML part')
    elements = list(root.iter())
    if len(elements) != len(spans):
        _fail('invalid_xml', 'XML provenance mismatch')
    raw = {}
    try:
        for element, span in zip(elements, spans):
            raw[id(element)] = data[span[0]:span[1]].decode(encoding)
    except (UnicodeError, LookupError):
        _fail('invalid_xml', 'Invalid XML encoding')
    return root, raw


def _decode(content, profile):
    encoding, bom = profile.encoding, 0
    if encoding == 'auto':
        if content.startswith(b'\xef\xbb\xbf'):
            encoding, bom = 'utf-8', 3
        elif content.startswith(b'\xff\xfe'):
            encoding, bom = 'utf-16-le', 2
        elif content.startswith(b'\xfe\xff'):
            encoding, bom = 'utf-16-be', 2
        else:
            # Ambiguous legacy encodings require an explicit trusted profile.
            encoding = 'utf-8'
    elif encoding == 'utf-16':
        if content.startswith(b'\xff\xfe'):
            encoding, bom = 'utf-16-le', 2
        elif content.startswith(b'\xfe\xff'):
            encoding, bom = 'utf-16-be', 2
        else:
            _fail('encoding_unavailable', 'UTF-16 requires a byte order mark')
    elif encoding == 'utf-8' and content.startswith(b'\xef\xbb\xbf'):
        bom = 3
    try:
        return content[bom:].decode(encoding, errors='strict'), encoding, bom
    except UnicodeError:
        _fail('encoding_unavailable', 'Text encoding could not be decoded without loss')


def _text(builder):
    text, encoding, bom = _decode(builder.content, builder.profile)
    if '\x00' in text:
        _fail('invalid_text', 'Text contains binary null bytes')
    # CSV physical records use CR/LF exclusively; Unicode separators are field data.
    lines = list(io.StringIO(text, newline='')) if builder.format == 'csv' else text.splitlines(keepends=True)
    byte_starts, char_starts, byte_cursor, char_cursor = [], [], bom, 0
    for line in lines:
        byte_starts.append(byte_cursor)
        char_starts.append(char_cursor)
        byte_cursor += len(line.encode(encoding))
        char_cursor += len(line)
    if builder.format == 'csv':
        # csv.reader tracks consumed physical lines, including quoted newlines.
        csv.field_size_limit(builder.profile.limits.max_block_chars)
        reader = csv.reader(lines, delimiter=builder.profile.csv_delimiter, strict=True)
        previous_line = 0
        try:
            for record, fields in enumerate(reader, 1):
                end = reader.line_num
                original = ''.join(lines[previous_line:end])
                anchor = TextAnchor(kind='csv', encoding=encoding, line_start=previous_line+1, line_end=end,
                                    byte_start=byte_starts[previous_line], byte_end=byte_starts[previous_line]+len(original.encode(encoding)),
                                    char_start=char_starts[previous_line], char_end=char_starts[previous_line]+len(original),
                                    record=record, fields=tuple(fields))
                builder.add(f'csv:record:{record}', 'csv_record', original, anchor)
                previous_line = end
        except csv.Error:
            _fail('invalid_csv', 'Malformed or oversized CSV record')
    else:
        for index, line in enumerate(lines):
            anchor = TextAnchor(kind='text', encoding=encoding, line_start=index+1, line_end=index+1,
                                byte_start=byte_starts[index], byte_end=byte_starts[index]+len(line.encode(encoding)),
                                char_start=char_starts[index], char_end=char_starts[index]+len(line))
            builder.add(f'text:line:{index+1}', 'text_line', line, anchor)


W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def _word_text(element):
    output = []
    def walk(node):
        # Deleted text is preserved and flagged by caller, never silently active.
        if node.tag in {W+'t', W+'delText', W+'instrText'}:
            output.append(node.text or '')
        elif node.tag == W+'tab':
            output.append('\t')
        elif node.tag in {W+'br', W+'cr'}:
            output.append('\n')
        else:
            for child in node:
                walk(child)
                if child.tag == W+'p' and child is not list(node)[-1]:
                    output.append('\n')
    walk(element)
    return ''.join(output)


def _word_flags(element):
    tags = {n.tag for n in element.iter()}
    flags = []
    if tags & {W+'del', W+'ins', W+'moveFrom', W+'moveTo'}:
        flags.append('tracked_changes_review')
    if tags & {W+'instrText', W+'fldSimple', W+'fldChar'}:
        flags.append('field_result_untrusted')
    known = {'p', 'pPr', 'r', 'rPr', 't', 'delText', 'instrText', 'tab', 'br', 'cr', 'tbl', 'tblPr', 'tblGrid', 'gridCol',
             'tr', 'trPr', 'tc', 'tcPr', 'sdt', 'sdtContent', 'sdtPr', 'hyperlink', 'smartTag', 'customXml', 'ins', 'del',
             'moveFrom', 'moveTo', 'drawing', 'pict', 'object', 'fldSimple', 'fldChar', 'footnoteReference', 'endnoteReference',
             'commentReference', 'bookmarkStart', 'bookmarkEnd', 'proofErr', 'permStart', 'permEnd'}
    if any((not n.tag.startswith(W) or n.tag[len(W):] not in known) and (len(n) or (n.text or '').strip()) for n in element.iter()):
        flags.append('unknown_xml_content')
    return tuple(flags)


def _docx(builder):
    parts = _package(builder.content, builder.profile.limits)
    if 'word/document.xml' not in parts:
        _fail('invalid_docx', 'DOCX has no document XML part')
    limits = builder.profile.limits
    relevant = [name for name in parts if (name == 'word/document.xml'
                or re.fullmatch(r'word/(?:header\d+|footer\d+|footnotes|endnotes|comments)\.xml', name))]
    relevant.sort(key=lambda name: (name != 'word/document.xml', name))
    total_cells = 0
    for part in relevant:
        root, raw = _xml(parts[part], limits)
        total_cells += sum(1 for cell in root.iter(W+'tc'))
        if total_cells > limits.max_cells:
            _fail('cell_limit', 'Actual DOCX table cells exceed cell limit')
        if root.tag not in {W+'document', W+'hdr', W+'ftr', W+'footnotes', W+'endnotes', W+'comments'}:
            _fail('invalid_docx', 'Invalid DOCX XML root')
        counter = {'paragraph': 0, 'table': 0}
        def emit_pending(node, anchor, path):
            for index, item in enumerate(node.iter()):
                local = item.tag.rsplit('}', 1)[-1]
                if local in {'drawing', 'pict', 'object', 'altChunk', 'OLEObject'}:
                    state = 'vision_pending' if local in {'drawing', 'pict'} else 'adapter_unavailable'
                    builder.coverage_add(f'coverage:{part}:{path}:object:{index}', 'drawing' if state == 'vision_pending' else 'embedded',
                                         state, anchor, 'Embedded DOCX content requires an unavailable vision or conversion adapter')
        def walk(parent, path, note_id=None):
            nonlocal total_cells
            positions = {}
            for child in parent:
                local = child.tag.rsplit('}', 1)[-1]
                positions[local] = positions.get(local, 0)+1
                child_path = f'{path}/{local}[{positions[local]}]'
                child_note = child.attrib.get(W+'id', note_id) if local in {'footnote', 'endnote', 'comment'} else note_id
                if child.tag == W+'p':
                    counter['paragraph'] += 1
                    anchor = XMLAnchor(part=part, element_path=child_path, paragraph=counter['paragraph'], note_id=child_note)
                    text = _word_text(child)
                    flags = _word_flags(child)
                    if text or not any(n.tag.rsplit('}', 1)[-1] in {'drawing', 'pict', 'object'} for n in child.iter()):
                        builder.add(f'docx:{part}:{child_path}', 'paragraph', text, anchor, raw[id(child)], flags)
                    emit_pending(child, anchor, child_path)
                    if flags:
                        builder.coverage_add(f'review:{part}:{child_path}', 'unknown', 'review_pending', anchor, ','.join(flags))
                elif child.tag == W+'tbl':
                    counter['table'] += 1
                    table_number = counter['table']
                    rows = child.findall(W+'tr')
                    origins, cells = {}, []
                    for row_index, row in enumerate(rows, 1):
                        next_origins = {}
                        grid_before = row.find(W+'trPr/'+W+'gridBefore')
                        col = int(grid_before.attrib.get(W+'val', '0'))+1 if grid_before is not None else 1
                        for cell_index, cell in enumerate(row.findall(W+'tc'), 1):
                            span_el = cell.find(W+'tcPr/'+W+'gridSpan')
                            span = int(span_el.attrib.get(W+'val', '1')) if span_el is not None else 1
                            if span < 1 or span > 16384 or col > 16384:
                                _fail('invalid_docx', 'Invalid DOCX table merge')
                            merge = cell.find(W+'tcPr/'+W+'vMerge')
                            cell_path = f'{child_path}/tr[{row_index}]/tc[{cell_index}]'
                            origin = None
                            if merge is not None and merge.attrib.get(W+'val') != 'restart':
                                origin = origins.get((col, span))
                                if origin is None:
                                    _fail('invalid_docx', 'DOCX vertical merge has no contiguous compatible origin')
                                cells[origin]['row_span'] += 1
                                next_origins[(col, span)] = origin
                            elif merge is not None:
                                next_origins[(col, span)] = len(cells)
                            cells.append({'element': cell, 'row': row_index, 'col': col, 'col_span': span,
                                          'row_span': 1, 'path': cell_path, 'origin': origin})
                            col += span
                        # Missing, ordinary, or differently spanned cells terminate a chain.
                        origins = next_origins
                    for value in cells:
                        cell = value['element']
                        origin = value['origin']
                        anchor = XMLAnchor(part=part, element_path=value['path'], table=table_number, row=value['row'], col=value['col'],
                                           col_span=value['col_span'], row_span=value['row_span'],
                                           merge_origin=cells[origin]['path'] if origin is not None else None, note_id=child_note)
                        text, flags = _word_text(cell), _word_flags(cell)
                        builder.add(f'docx:{part}:{value["path"]}', 'table_cell', text, anchor, raw[id(cell)], flags)
                        emit_pending(cell, anchor, value['path'])
                        if flags:
                            builder.coverage_add(f'review:{part}:{value["path"]}', 'unknown', 'review_pending', anchor, ','.join(flags))
                    # Nested table structure cannot be represented by a flat cell.
                    if any(n is not child and n.tag == W+'tbl' for n in child.iter()):
                        builder.coverage_add(f'coverage:{part}:{child_path}:nested', 'unknown', 'review_pending',
                                             XMLAnchor(part=part, element_path=child_path, table=table_number), 'Nested DOCX table requires structure review')
                else:
                    if local in {'altChunk', 'object', 'drawing', 'pict', 'OLEObject'}:
                        emit_pending(child, XMLAnchor(part=part, element_path=child_path, note_id=child_note), child_path)
                    elif child.tag not in {W+'body', W+'sdt', W+'sdtContent', W+'sdtPr', W+'customXml', W+'sectPr',
                                           W+'footnote', W+'endnote', W+'comment', W+'bookmarkStart', W+'bookmarkEnd',
                                           W+'proofErr', W+'permStart', W+'permEnd'} and (''.join(child.itertext()).strip() or len(child)):
                        builder.coverage_add(f'coverage:unknown:{part}:{child_path}', 'unknown', 'review_pending',
                                             XMLAnchor(part=part, element_path=child_path, note_id=child_note),
                                             'Uninterpreted DOCX XML content requires structure review')
                    walk(child, child_path, child_note)
        walk(root, root.tag.rsplit('}', 1)[-1])
    known_metadata = {'[Content_Types].xml', 'word/styles.xml', 'word/settings.xml', 'word/fontTable.xml', 'word/webSettings.xml',
                      'word/numbering.xml', 'docProps/core.xml', 'docProps/app.xml'}
    for part, payload in sorted(parts.items()):
        if part in relevant or part in known_metadata or '/_rels/' in part or part.startswith('_rels/') or part.startswith('word/theme/'):
            if part.endswith('.xml') and part not in relevant:
                _xml(payload, limits)
            continue
        state, kind = ('vision_pending', 'image') if part.startswith('word/media/') else ('adapter_unavailable', 'embedded')
        if kind == 'image':
            _image_header(builder, payload)
        builder.coverage_add('coverage:part:'+part, kind, state, PartAnchor(part=part), 'DOCX part has no native text adapter')


def _cell_reference(ref):
    match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})', ref or '')
    if not match:
        _fail('invalid_xlsx', 'Invalid XLSX cell reference')
    col = 0
    for letter in match[1]:
        col = col*26+ord(letter)-64
    row = int(match[2])
    if col > 16384 or row > 1048576:
        _fail('invalid_xlsx', 'XLSX cell reference exceeds sheet bounds')
    return row, col


def _target(base, value):
    if not value or '\\' in value or ':' in value or value.startswith('//'):
        _fail('invalid_xlsx', 'Invalid internal XLSX relationship')
    result = []
    path = PurePosixPath(value.lstrip('/')) if value.startswith('/') else PurePosixPath(base).parent / value
    for part in path.parts:
        if part == '..':
            if not result:
                _fail('invalid_xlsx', 'XLSX relationship escapes package')
            result.pop()
        elif part != '.':
            result.append(part)
    return '/'.join(result)


_XLSX_CELL_CHILDREN = {
    S+'c': {S+'f', S+'v', S+'is'},
    S+'is': {S+'t', S+'r'}, S+'si': {S+'t', S+'r'},
    S+'r': {S+'t', S+'rPr'},
    S+'rPr': {S+name for name in ('rFont', 'charset', 'family', 'b', 'i', 'strike', 'outline',
                                  'shadow', 'condense', 'extend', 'color', 'sz', 'u', 'vertAlign', 'scheme')},
}


def _xlsx_unknown_xml(node):
    """Recognize exact child positions; foreign/nested semantic XML stays pending."""
    for element in node.iter():
        expected = _XLSX_CELL_CHILDREN.get(element.tag, set())
        if any(child.tag not in expected for child in element):
            return True
        if element.tag not in {S+'t', S+'f', S+'v'} and (element.text or '').strip():
            return True
        if any((child.tail or '').strip() for child in element):
            return True
    return False


def _xlsx_string_text(node):
    if node is None:
        return ''
    return ''.join((child.text or '') if child.tag == S+'t' else
                   ''.join(t.text or '' for t in child.findall(S+'t')) if child.tag == S+'r' else ''
                   for child in node)


_XLSX_STRUCTURE_CHILDREN = {
    S+'worksheet': {S+name for name in ('dimension', 'sheetData', 'mergeCells', 'sheetViews', 'sheetFormatPr',
                                      'cols', 'pageMargins', 'pageSetup', 'printOptions', 'sheetPr', 'autoFilter')},
    S+'sheetData': {S+'row'}, S+'row': {S+'c'},
    S+'mergeCells': {S+'mergeCell'}, S+'cols': {S+'col'},
    S+'sheetViews': {S+'sheetView'}, S+'sheetView': {S+'pane', S+'selection'},
    S+'sheetPr': {S+'tabColor', S+'outlinePr', S+'pageSetUpPr'},
    S+'workbook': {S+name for name in ('sheets', 'fileVersion', 'workbookPr', 'bookViews', 'calcPr')},
    S+'sheets': {S+'sheet'}, S+'bookViews': {S+'workbookView'},
    S+'sst': {S+'si'},
    **_XLSX_CELL_CHILDREN,
}


def _xlsx_structure_coverage(builder, root, part, prefix):
    """Inspect recognized containers too; only separately interpreted cells are skipped."""
    stack = [(root, root.tag.rsplit('}', 1)[-1])]
    visited = 0
    while stack:
        node, path = stack.pop()
        visited += 1
        if visited > builder.profile.limits.max_xml_nodes:
            _fail('xml_limit', 'Spreadsheet structure exceeds XML node limit')
        if node.tag == S+'c':
            continue  # _xlsx_unknown_xml checks the complete cell subtree.
        meaningful_text = node.tag not in {S+'t', S+'f', S+'v'} and (node.text or '').strip()
        if meaningful_text or any((child.tail or '').strip() for child in node):
            builder.coverage_add(prefix+part+':'+path+':text', 'unknown', 'review_pending', PartAnchor(part=part),
                                 'Uninterpreted spreadsheet XML text at '+path)
        expected = _XLSX_STRUCTURE_CHILDREN.get(node.tag, set())
        for index, child in enumerate(node):
            local = child.tag.rsplit('}', 1)[-1]
            position = f'{local}[{index};r={child.attrib.get("r", "")}]' if child.tag == S+'row' else f'{local}[{index}]'
            child_path = path+'/'+position
            if child.tag not in expected:
                builder.coverage_add(prefix+part+':'+child_path, 'unknown', 'review_pending', PartAnchor(part=part),
                                     'Uninterpreted spreadsheet XML structure at '+child_path)
            else:
                stack.append((child, child_path))


def _xlsx(builder):
    parts = _package(builder.content, builder.profile.limits)
    limits = builder.profile.limits
    if 'xl/workbook.xml' not in parts or 'xl/_rels/workbook.xml.rels' not in parts:
        _fail('invalid_xlsx', 'XLSX workbook or relationships are missing')
    workbook, _ = _xml(parts['xl/workbook.xml'], limits)
    if workbook.tag != S+'workbook':
        _fail('invalid_xlsx', 'Invalid XLSX workbook root')
    _xlsx_structure_coverage(builder, workbook, 'xl/workbook.xml', 'coverage:workbook-region:')
    relationships, _ = _xml(parts['xl/_rels/workbook.xml.rels'], limits)
    rels = {}
    for relation in relationships:
        if relation.attrib.get('TargetMode') == 'External':
            builder.coverage_add('coverage:external:'+relation.attrib.get('Id', ''), 'embedded', 'adapter_unavailable',
                                 PartAnchor(part='xl/_rels/workbook.xml.rels'), 'External spreadsheet relationship is not fetched')
            continue
        relationship_id = relation.attrib.get('Id')
        if not relationship_id or relationship_id in rels:
            _fail('invalid_xlsx', 'Invalid XLSX relationship ID')
        rels[relationship_id] = _target('xl/workbook.xml', relation.attrib.get('Target'))
    shared = []
    if 'xl/sharedStrings.xml' in parts:
        shared_root, _ = _xml(parts['xl/sharedStrings.xml'], limits)
        _xlsx_structure_coverage(builder, shared_root, 'xl/sharedStrings.xml', 'coverage:shared-string-xml:')
        for string in shared_root.findall(S+'si'):
            shared.append((_xlsx_string_text(string), _xlsx_unknown_xml(string)))
            if len(shared) > limits.max_cells:
                _fail('cell_limit', 'XLSX shared strings exceed cell limit')
    total_cells, visited = 0, {'xl/workbook.xml', 'xl/_rels/workbook.xml.rels', 'xl/sharedStrings.xml'}
    seen_sheet_ids = set()
    for sheet in workbook.findall(S+'sheets/'+S+'sheet'):
        name, sheet_id, rid = sheet.attrib.get('name'), sheet.attrib.get('sheetId'), sheet.attrib.get(R+'id')
        part = rels.get(rid)
        if not name or not sheet_id or sheet_id in seen_sheet_ids or not part or part not in parts:
            _fail('invalid_xlsx', 'Invalid XLSX sheet descriptor')
        seen_sheet_ids.add(sheet_id)
        visited.add(part)
        root, raw = _xml(parts[part], limits)
        if root.tag != S+'worksheet':
            builder.coverage_add('coverage:nonworksheet:'+part, 'unknown', 'adapter_unavailable', PartAnchor(part=part),
                                 'Workbook sheet is not a native cell worksheet')
            continue
        _xlsx_structure_coverage(builder, root, part, 'coverage:worksheet-xml:')
        merges = []
        for merge in root.findall(S+'mergeCells/'+S+'mergeCell'):
            ref = merge.attrib.get('ref', '')
            endpoints = ref.split(':')
            if len(endpoints) != 2:
                _fail('invalid_xlsx', 'Invalid XLSX merge range')
            start, end = _cell_reference(endpoints[0]), _cell_reference(endpoints[1])
            if start[0] > end[0] or start[1] > end[1]:
                _fail('invalid_xlsx', 'Reversed XLSX merge range')
            if len(merges) >= limits.max_cells:
                _fail('cell_limit', 'XLSX merge count exceeds cell limit')
            merges.append((start, end, ref, endpoints[0]))
        seen = set()
        for row in root.findall(S+'sheetData/'+S+'row'):
            for cell in row.findall(S+'c'):
                total_cells += 1
                if total_cells > limits.max_cells:
                    _fail('cell_limit', 'Actual XLSX cells exceed cell limit')
                ref = cell.attrib.get('r', '')
                r, col = _cell_reference(ref)
                if ref in seen or (row.attrib.get('r') is not None and int(row.attrib['r']) != r):
                    _fail('invalid_xlsx', 'Duplicate or inconsistent XLSX cell reference')
                seen.add(ref)
                formula = cell.find(S+'f')
                value = cell.find(S+'v')
                cached = value.text if value is not None else None
                type = cell.attrib.get('t')
                flags = ['unknown_xml_content'] if _xlsx_unknown_xml(cell) else []
                if formula is not None:
                    text = formula.text or ''
                    flags.append('formula_cache_missing' if cached is None else 'formula_cache_untrusted')
                    if formula.attrib:
                        flags.append('formula_metadata_review')
                elif type == 's':
                    try:
                        index = int(cached)
                        if index < 0:
                            raise ValueError()
                        text, shared_unknown = shared[index]
                        if shared_unknown and 'unknown_xml_content' not in flags:
                            flags.append('unknown_xml_content')
                    except (ValueError, IndexError, TypeError):
                        _fail('invalid_xlsx', 'Invalid shared string reference')
                elif type == 'inlineStr':
                    text = _xlsx_string_text(cell.find(S+'is'))
                else:
                    text = cached or ''
                matching = [m for m in merges if m[0][0] <= r <= m[1][0] and m[0][1] <= col <= m[1][1]]
                if len(matching) > 1:
                    _fail('invalid_xlsx', 'Overlapping XLSX cell merge')
                merge = matching[0] if matching else None
                anchor = CellAnchor(part=part, sheet=name, sheet_id=sheet_id, cell=ref, row=r, col=col,
                                    merge_range=merge[2] if merge else None, merge_origin=merge[3] if merge else None,
                                    raw_formula=formula.text or '' if formula is not None else None,
                                    cached_value=cached if formula is not None else None, cell_type=type)
                block = builder.add(f'xlsx:{part}:cell:{ref}', 'spreadsheet_cell', text, anchor, raw[id(cell)], tuple(flags))
                if 'unknown_xml_content' in flags:
                    builder.coverage_add(f'coverage:unknown:{part}:{ref}', 'unknown', 'review_pending', anchor,
                                         'Uninterpreted spreadsheet cell XML requires structure review', (block.id,))
                if formula is not None:
                    builder.coverage_add(f'coverage:formula:{part}:{ref}', 'formula', 'review_pending', anchor,
                                         'Formula results are original unverified cache; no formula evaluation is available')
        for index, merge in enumerate(merges):
            if merge[3] not in seen:
                builder.coverage_add(f'coverage:merge:{part}:{index}', 'unknown', 'review_pending', PartAnchor(part=part),
                                     'Merged range has no physical origin cell')
    known = {'[Content_Types].xml', 'xl/styles.xml', 'docProps/core.xml', 'docProps/app.xml'}
    for part, payload in sorted(parts.items()):
        if part in visited or part in known or '/_rels/' in part or part.startswith('_rels/') or part.startswith('xl/theme/'):
            if part.endswith('.xml') and part not in visited:
                _xml(payload, limits)
            continue
        kind, state = ('image', 'vision_pending') if part.startswith('xl/media/') else ('embedded', 'adapter_unavailable')
        if kind == 'image':
            _image_header(builder, payload)
        builder.coverage_add('coverage:part:'+part, kind, state, PartAnchor(part=part), 'Spreadsheet part has no native cell adapter')


def _original_pdf_bbox(page, objects):
    """Invert PDFMiner's exact page CTM, retaining original PDF user space."""
    x0, y0, x1, y1 = (float(v) for v in page.page_obj.mediabox)
    rotation = page.rotation
    # pdfplumber x is shifted by its rotated MediaBox x origin; top is
    # downward from that box top. PDFMiner's y0/y1 remain in CTM space.
    points = []
    for obj in objects:
        for x in (obj['x0']-page.mediabox[0], obj['x1']-page.mediabox[0]):
            for y in (obj['y0'], obj['y1']):
                if rotation == 90:
                    original = (x1-y, x+y0)
                elif rotation == 180:
                    original = (x1-x, y1-y)
                elif rotation == 270:
                    original = (y+x0, y1-x)
                else:
                    original = (x+x0, y+y0)
                points.append(original)
    return (min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points))


def _pdf_anchor(page, objects, index):
    media = tuple(float(v) for v in page.page_obj.mediabox)
    crop = tuple(float(v) for v in page.page_obj.cropbox)
    return PDFAnchor(page=page.page_number, object_index=index,
                     original_bbox=_original_pdf_bbox(page, objects),
                     rendered_bbox=(min(o['x0'] for o in objects), min(o['top'] for o in objects),
                                    max(o['x1'] for o in objects), max(o['bottom'] for o in objects)),
                     media_box=media, crop_box=crop, rotation=page.rotation)


def _pdf_embedded_files(builder, source, location='pdf:catalog'):
    """Record names-tree and associated attachments, without decoding streams."""
    from pdfminer.pdftypes import PDFObjRef, resolve1
    from pdfminer.utils import decode_text
    def source_text(value):
        value = resolve1(value)
        if isinstance(value, bytes):
            if len(value) > builder.profile.limits.max_block_chars:
                _fail('block_limit', 'PDF embedded filename exceeds character limit')
            return decode_text(value)
        return value if isinstance(value, str) else ''

    def record_attachment(file_ref, name, path):
        file = resolve1(file_ref)
        file_id = str(file_ref.objid) if isinstance(file_ref, PDFObjRef) else 'direct'
        filename, streams, relationship = name, '', ''
        if isinstance(file, dict):
            filename = source_text(file.get('UF') or file.get('F')) or filename
            relationship = str(resolve1(file.get('AFRelationship')) or '')
            embedded = resolve1(file.get('EF'))
            if isinstance(embedded, dict):
                if len(embedded) > builder.profile.limits.max_blocks:
                    _fail('block_limit', 'PDF embedded stream count exceeds block limit')
                # Only reference identities are inspected: get_data() is never called.
                streams = ','.join(f'{key}:{value.objid if isinstance(value, PDFObjRef) else "direct"}'
                                   for key, value in sorted(embedded.items()))
        part = f'{path}/name:{name}/Filespec:{file_id}/filename:{filename}/EF:{streams}/AFRelationship:{relationship}'
        builder.coverage_add('coverage:'+path, 'embedded', 'adapter_unavailable', PartAnchor(part=part),
                             'Original PDF embedded file requires a separate bounded adapter')

    if 'AF' in source:
        base = location+'/AF'
        builder.coverage_add('coverage:'+base, 'embedded', 'adapter_unavailable', PartAnchor(part=base),
                             'PDF associated-file structure requires a separate bounded adapter')
        associated = resolve1(source['AF'])
        if isinstance(associated, list):
            if len(associated) > builder.profile.limits.max_blocks:
                _fail('block_limit', 'PDF associated file count exceeds block limit')
            for index, file_ref in enumerate(associated):
                record_attachment(file_ref, '', f'{base}[{index}]')
    names = resolve1(source.get('Names'))
    if not isinstance(names, dict) or 'EmbeddedFiles' not in names:
        return
    base = location+'/Names/EmbeddedFiles'
    builder.coverage_add('coverage:'+base, 'embedded', 'adapter_unavailable', PartAnchor(part=base),
                         'PDF names tree contains embedded files; the embedded-file adapter is unavailable')
    stack, visited, count = [(names['EmbeddedFiles'], '', 0)], set(), 0

    while stack:
        reference, path, depth = stack.pop()
        count += 1
        if count > builder.profile.limits.max_blocks or depth > builder.profile.limits.max_xml_depth:
            _fail('block_limit', 'PDF embedded name tree exceeds structure limit')
        node = resolve1(reference)
        identity = ('ref', reference.objid) if isinstance(reference, PDFObjRef) else ('direct', id(node))
        if identity in visited:
            _fail('invalid_pdf', 'PDF embedded name tree contains a repeated node')
        visited.add(identity)
        if not isinstance(node, dict):
            continue  # Catalog-level pending coverage already records uninterpreted structure.
        entries = resolve1(node.get('Names'))
        if isinstance(entries, list):
            if len(entries) > builder.profile.limits.max_blocks * 2:
                _fail('block_limit', 'PDF embedded file count exceeds block limit')
            for index in range(0, len(entries)-1, 2):
                record_attachment(entries[index+1], source_text(entries[index]), f'{base}{path}/Names[{index}]')
        kids = resolve1(node.get('Kids'))
        if isinstance(kids, list):
            if len(stack) + len(kids) + count > builder.profile.limits.max_blocks:
                _fail('block_limit', 'PDF embedded name tree exceeds structure limit')
            stack.extend((child, f'{path}/Kids[{index}]', depth+1) for index, child in reversed(list(enumerate(kids))))


def _pdf(builder):
    import pdfplumber
    from pdfminer.pdfpage import PDFPage
    from pdfplumber.page import Page
    limits = builder.profile.limits
    if not builder.content.startswith(b'%PDF-'):
        _fail('invalid_pdf', 'PDF signature is missing')
    try:
        with pdfplumber.open(io.BytesIO(builder.content), unicode_norm=None) as pdf:
            _pdf_embedded_files(builder, pdf.doc.catalog)
            builder.pages = 0
            doctop = 0
            pixels = 0
            for number, page_obj in enumerate(PDFPage.create_pages(pdf.doc), 1):
                if number > limits.max_pages:
                    _fail('page_limit', 'PDF exceeds physical page limit')
                page = Page(pdf, page_obj, page_number=number, initial_doctop=doctop)
                builder.pages = number
                _pdf_embedded_files(builder, page_obj.attrs, f'pdf:page:{number}')
                doctop += page.height
                if page.rotation not in {0, 90, 180, 270}:
                    _fail('invalid_pdf', 'Unsupported PDF page rotation')
                chars = page.chars
                if len(chars) > limits.max_text_chars or len(chars) + builder.characters > limits.max_text_chars:
                    _fail('text_limit', 'PDF glyph count exceeds character limit')
                images = page.images
                object_count = sum(len(values) for values in page.objects.values())
                if object_count > limits.max_blocks*10:
                    _fail('block_limit', 'PDF objects exceed block limit')
                # Source order and glyphs are preserved. Text extraction creates
                # a separate layout view only; no Unicode folding touches source.
                if chars:
                    original = ''.join(c.get('text', '') for c in chars)
                    anchor = _pdf_anchor(page, chars, 0)
                    builder.add(f'pdf:page:{page.page_number}:glyphs', 'pdf_text', original, anchor)
                page_anchor = PartAnchor(part='pdf:page', page=page.page_number)
                page_state = 'extracted' if chars else 'ocr_pending'
                builder.coverage_add(f'coverage:pdf:page:{page.page_number}', 'page', page_state, page_anchor,
                                     None if chars else 'Page has no native text; OCR adapter is unavailable')
                for index, image in enumerate(images):
                    width, height = image.get('srcsize', (0, 0))
                    if width <= 0 or height <= 0:
                        _fail('invalid_pdf', 'PDF image has invalid pixel dimensions')
                    pixels += int(width)*int(height)
                    if pixels > limits.max_pixels:
                        _fail('pixel_limit', 'PDF source images exceed pixel limit')
                    builder.coverage_add(f'coverage:pdf:page:{page.page_number}:image:{index}', 'image',
                                         'vision_pending' if chars else 'ocr_pending', _pdf_anchor(page, [image], index),
                                         'Image requires an unavailable vision or OCR adapter')
                for type in ('curve', 'rect', 'line'):
                    for index, drawing in enumerate(page.objects.get(type, [])):
                        builder.coverage_add(f'coverage:pdf:page:{page.page_number}:{type}:{index}', 'drawing', 'vision_pending',
                                             _pdf_anchor(page, [drawing], index), 'Vector drawing requires visual review')
                for index, annotation in enumerate(page.annots):
                    builder.coverage_add(f'coverage:pdf:page:{page.page_number}:annotation:{index}', 'unknown', 'review_pending',
                                         page_anchor, 'PDF annotation is not part of native text extraction')
                page.close()
    except DocumentReadError:
        raise
    except Exception:
        _fail('invalid_pdf', 'PDF native reader failed; encrypted or malformed PDFs are unavailable')


def normalize(content: bytes, filename: str, mime_type: str, profile: ParserProfile) -> NormalizedDocument:
    """Normalize native evidence; callers must not invoke on production host."""
    if not isinstance(content, bytes) or not isinstance(profile, ParserProfile):
        _fail('invalid_input', 'Native reader requires bytes and a trusted ParserProfile')
    if len(content) > profile.limits.max_input_bytes:
        _fail('input_limit', 'Document exceeds input byte limit')
    if not isinstance(filename, str) or not filename or len(filename) > 500 or not isinstance(mime_type, str) or len(mime_type) > 200:
        _fail('invalid_input', 'Document metadata is invalid')
    extension = PurePosixPath(filename.replace('\\', '/')).suffix.lower()
    trusted_mimes = {'application/pdf': 'pdf',
                     'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
                     'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
                     'text/plain': 'txt', 'text/csv': 'csv'}
    format = trusted_mimes.get(mime_type.split(';', 1)[0].strip().lower())
    if format is None and mime_type in {'application/octet-stream', ''}:
        format = {'.pdf': 'pdf', '.docx': 'docx', '.xlsx': 'xlsx', '.txt': 'txt', '.csv': 'csv'}.get(extension, 'unsupported')
    if format is None:
        format = 'unsupported'
    builder = _Builder(content, filename, mime_type, profile, format)
    if format in {'txt', 'csv'}:
        _text(builder)
    elif format == 'pdf':
        _pdf(builder)
    elif format == 'docx':
        _docx(builder)
    elif format == 'xlsx':
        _xlsx(builder)
    else:
        builder.coverage_add('coverage:adapter', 'adapter', 'adapter_unavailable', PartAnchor(part=filename),
                             'No native reader, OCR, conversion or CAD adapter is available for this format')
    return builder.finish()
