"""Public synthetic originals verify provenance, never expert OCR accuracy."""
import json
import hashlib
import os
import pytest
from pydantic import ValidationError


@pytest.mark.skipif(os.environ.get('DOCUMENT_OCR_INTEGRATION')!='1',reason='requires selected isolated runtime')
def test_actual_matching_stdlib_adapter_and_early_encoding_caches_cannot_override_source(actual_ocr_runtime):
    import subprocess,uuid
    from app.documents import runner
    config,_=actual_ocr_runtime
    root='/tmp/public-cache-policy-'+uuid.uuid4().hex
    create=f'''import pathlib,sys,importlib.util,marshal,struct,json
root=pathlib.Path({root!r});root.mkdir(mode=0o700)
paths=['/usr/lib/python3.14/encodings/__init__.py','/usr/lib/python3.14/json/__init__.py','/usr/lib/python3.14/colorsys.py',str(pathlib.Path(sys.prefix)/'lib/python3.14/site-packages/PIL/Image.py')]
result=[]
for index,name in enumerate(paths):
 original=pathlib.Path(name);source=original.read_bytes()+b'\\nPUBLIC_CACHE_POLICY="SOURCE_A"\\n'
 target=root/str(index);target.write_bytes(source)
 stamp=int(target.stat().st_mtime)
 malicious=compile(source.replace(b'"SOURCE_A"',b'"BYTECD_B"'),name,'exec')
 cache=root/(str(index)+'.pyc');cache.write_bytes(importlib.util.MAGIC_NUMBER+struct.pack('<III',0,stamp,len(source))+marshal.dumps(malicious))
 result.append([str(target),name,str(cache),importlib.util.cache_from_source(name)])
print(json.dumps(result))
'''
    prefix=runner._wsl_prefix(config)
    created=subprocess.run(prefix+[config.document_runtime_python,'-I','-c',create],check=True,capture_output=True,timeout=5)
    overlays=json.loads(created.stdout)
    try:
        command,is_wsl=runner._build_command(config)
        at=command.index('--')
        mounts=[]
        for source,target,cache,cache_target in overlays:
            target=target.replace(str(__import__('pathlib').PurePosixPath(config.document_runtime_python).parent.parent),'/runtime')
            cache_target=cache_target.replace(str(__import__('pathlib').PurePosixPath(config.document_runtime_python).parent.parent),'/runtime')
            mounts+=['--ro-bind',source,target,'--ro-bind',cache,cache_target]
        command[at:at]=mounts
        # Early encodings uses the CLI policy. Resetting the prefix afterward
        # proves the source loader hook independently protects ordinary imports.
        script="import sys; sys.pycache_prefix=None; import encodings,json,colorsys; from PIL import Image; print(json.dumps([m.PUBLIC_CACHE_POLICY for m in [encodings,json,colorsys,Image]]))"
        command[-1:]=['-c',script]
        result=json.loads(runner._stream_process(command,b'',config,None,is_wsl,handshake=True))
        assert result==['SOURCE_A']*4
    finally:
        cleanup=f"import pathlib; root=pathlib.Path({root!r}); [p.unlink() for p in root.iterdir()]; root.rmdir()"
        subprocess.run(prefix+['/usr/bin/python3','-I','-c',cleanup],check=True,capture_output=True,timeout=5)


@pytest.mark.skipif(os.environ.get('DOCUMENT_OCR_INTEGRATION')!='1',reason='requires selected isolated runtime')
def test_actual_source_policy_rejects_sourceless_and_zip_and_skips_site(actual_ocr_runtime):
    from app.documents import runner
    config,profile=actual_ocr_runtime
    command,is_wsl=runner._build_command(config)
    script='''import sys,marshal,struct,importlib.util,importlib.machinery,zipfile,json
assert 'site' not in sys.modules and 'sitecustomize' not in sys.modules
assert sys.pycache_prefix=='/dev/null' and sys.flags.no_site
assert all(not path.endswith('.zip') for path in sys.path)
with open('/tmp/public_sourceless.pyc','wb') as output:
 output.write(importlib.util.MAGIC_NUMBER+struct.pack('<III',0,0,0)+marshal.dumps(compile('raise AssertionError("unfrozen bytecode executed")','public','exec')))
with zipfile.ZipFile('/tmp/public_code.zip','w') as output:
 output.writestr('public_zipped.py','raise AssertionError("unfrozen zip executed")')
sys.path[:0]=['/tmp','/tmp/public_code.zip']
for name in ['public_sourceless','public_zipped']:
 try:__import__(name)
 except ModuleNotFoundError:pass
 else:raise AssertionError('unfrozen code admitted')
try:importlib.machinery.SourcelessFileLoader('public','/tmp/public_sourceless.pyc').get_code('public')
except ImportError:pass
else:raise AssertionError('direct sourceless loader admitted')
print(json.dumps({'source_only':True}))
'''
    command[-1:]=['-c',script]
    assert json.loads(runner._stream_process(command,b'',config,None,is_wsl,handshake=True))=={'source_only':True}
    assert profile.toolchain['python-execution-policy']=='source-only-v2-prestart-zip-guard-qualified-pin'


@pytest.mark.skipif(os.environ.get('DOCUMENT_OCR_INTEGRATION')!='1',reason='requires selected isolated runtime')
def test_actual_cache_only_variants_leave_frozen_identity_and_source_behavior_unchanged(actual_ocr_runtime):
    import subprocess,uuid
    from app.documents import runner
    config,profile=actual_ocr_runtime
    root='/tmp/public-cache-identity-'+uuid.uuid4().hex
    create=f'''import pathlib,sys,importlib.util,marshal,struct,json
root=pathlib.Path({root!r});root.mkdir(mode=0o700)
paths=['/usr/lib/python3.14/encodings/__init__.py','/usr/lib/python3.14/json/__init__.py','/usr/lib/python3.14/colorsys.py',str(pathlib.Path(sys.prefix)/'lib/python3.14/site-packages/PIL/Image.py')]
result=[]
for index,name in enumerate(paths):
 original=pathlib.Path(name);source=original.read_bytes();stamp=int(original.stat().st_mtime)
 malicious=compile(source+b'\\nPUBLIC_CACHE_TAMPER=True\\n',name,'exec')
 cache=root/(str(index)+'.pyc');cache.write_bytes(importlib.util.MAGIC_NUMBER+struct.pack('<III',0,stamp,len(source))+marshal.dumps(malicious))
 result.append([str(cache),importlib.util.cache_from_source(name)])
print(json.dumps(result))
'''
    prefix=runner._wsl_prefix(config)
    created=subprocess.run(prefix+[config.document_runtime_python,'-I','-c',create],check=True,capture_output=True,timeout=5)
    try:
        command,is_wsl=runner._build_command(config,probe=True)
        runtime_root=str(__import__('pathlib').PurePosixPath(config.document_runtime_python).parent.parent)
        mounts=[]
        for cache,target in json.loads(created.stdout):mounts+=['--ro-bind',cache,target.replace(runtime_root,'/runtime')]
        command[command.index('--'):command.index('--')]=mounts
        script=runner._PROBE.replace('print(json.dumps(result))',"import encodings,colorsys; from PIL import Image\nassert not any(hasattr(m,'PUBLIC_CACHE_TAMPER') for m in [encodings,json,colorsys,Image])\nprint(json.dumps(result))")
        command[-2]=script
        result=json.loads(runner._stream_process(command,b'',config,None,is_wsl,handshake=True))
        assert result['ocr_toolchain']==dict(profile.toolchain)
        assert result['code_hash']==profile.code_hash
    finally:
        cleanup=f"import pathlib; root=pathlib.Path({root!r}); [p.unlink() for p in root.iterdir()]; root.rmdir()"
        subprocess.run(prefix+['/usr/bin/python3','-I','-c',cleanup],check=True,capture_output=True,timeout=5)


@pytest.mark.skipif(os.environ.get('DOCUMENT_OCR_INTEGRATION')!='1',reason='requires selected isolated runtime')
@pytest.mark.parametrize('asset',['zip','dangling'])
def test_actual_earliest_encoding_zip_is_refused_before_first_inner_python(actual_ocr_runtime,asset):
    import subprocess,uuid
    from app.documents import runner
    config,_=actual_ocr_runtime
    root='/tmp/public-earlyzip-'+uuid.uuid4().hex
    create=f'''import pathlib,zipfile
root=pathlib.Path({root!r});root.mkdir(mode=0o700);(root/'lib').mkdir()
for original in pathlib.Path('/usr/lib').iterdir():
 (root/'lib'/original.name).symlink_to('/public-lib/'+original.name,target_is_directory=original.is_dir())
with zipfile.ZipFile(root/'lib/python314.zip','w',compression=zipfile.ZIP_STORED) as archive:
 for name in ['__init__.py','aliases.py','utf_8.py']:
  source=pathlib.Path('/usr/lib/python3.14/encodings',name).read_bytes()
  if name=='__init__.py':source+=b'\\nPUBLIC_EARLY_ZIP_MARKER="unfrozen_initial_zip_code"\\n'
  archive.writestr('encodings/'+name,source)
if {asset!r}=='dangling':
 (root/'lib/python314.zip').unlink();(root/'lib/python314.zip').symlink_to('/missing-startup-zip')
'''
    prefix=runner._wsl_prefix(config)
    subprocess.run(prefix+['/usr/bin/python3','-I','-c',create],check=True,capture_output=True,timeout=5)
    try:
        command,is_wsl=runner._build_command(config)
        at=command.index('--')
        command[at:at]=['--ro-bind','/usr/lib','/public-lib','--ro-bind',root+'/lib','/usr/lib']
        # No script-side guard: admission must stop before Python's initial
        # encodings import, before _SANDBOX_START or dispatch can execute.
        command[-1:]=['-c','import encodings,json;print(json.dumps({"marker":getattr(encodings,"PUBLIC_EARLY_ZIP_MARKER",None)}))']
        with pytest.raises(runner.DocumentRunnerError) as error:
            runner._stream_process(command,b'',config,None,is_wsl,handshake=True)
        assert error.value.code=='parser_failed'
    finally:
        cleanup=f"import pathlib; root=pathlib.Path({root!r}); [p.unlink() for p in (root/'lib').iterdir()]; (root/'lib').rmdir(); root.rmdir()"
        subprocess.run(prefix+['/usr/bin/python3','-I','-c',cleanup],check=True,capture_output=True,timeout=5)


@pytest.mark.skipif(os.environ.get('DOCUMENT_OCR_INTEGRATION')!='1',reason='requires selected isolated runtime')
@pytest.mark.parametrize('pin',['','b'*64])
def test_actual_missing_or_unqualified_pin_refused_before_outer_python(actual_ocr_runtime,pin):
    from app.documents import runner
    config,_=actual_ocr_runtime
    config=config.model_copy(update={'document_runtime_python_sha256':pin})
    command,is_wsl=runner._build_command(config)
    with pytest.raises(runner.DocumentRunnerError) as error:
        runner._stream_process(command,b'',config,None,is_wsl,handshake=True)
    assert error.value.code=='runner_unavailable'


@pytest.mark.skipif(os.environ.get('DOCUMENT_OCR_INTEGRATION')!='1',reason='requires selected isolated runtime')
def test_actual_sourceless_initial_encoding_is_refused_before_python(actual_ocr_runtime):
    import subprocess,uuid
    from app.documents import runner
    config,_=actual_ocr_runtime
    root='/tmp/public-earlybytecode-'+uuid.uuid4().hex
    create=f'''import pathlib,marshal,struct,importlib.util
root=pathlib.Path({root!r});root.mkdir(mode=0o700)
source=pathlib.Path('/usr/lib/python3.14/encodings')
for original in source.iterdir():
 (root/original.name).symlink_to('/public-encodings/'+original.name,target_is_directory=original.is_dir())
(root/'__init__.py').unlink()
code=compile((source/'__init__.py').read_bytes()+b'\\nPUBLIC_EARLY_BYTECODE_MARKER=True\\n','public','exec')
(root/'__init__.pyc').write_bytes(importlib.util.MAGIC_NUMBER+struct.pack('<III',0,0,0)+marshal.dumps(code))
'''
    prefix=runner._wsl_prefix(config)
    subprocess.run(prefix+['/usr/bin/python3','-I','-c',create],check=True,capture_output=True,timeout=5)
    try:
        command,is_wsl=runner._build_command(config)
        at=command.index('--')
        command[at:at]=['--ro-bind','/usr/lib/python3.14/encodings','/public-encodings','--ro-bind',root,'/usr/lib/python3.14/encodings']
        command[-1:]=['-c','import encodings,json; print(json.dumps({"marker":getattr(encodings,"PUBLIC_EARLY_BYTECODE_MARKER",False)}))']
        with pytest.raises(runner.DocumentRunnerError) as error:
            runner._stream_process(command,b'',config,None,is_wsl,handshake=True)
        assert error.value.code=='parser_failed'
    finally:
        cleanup=f"import pathlib; root=pathlib.Path({root!r}); [p.unlink() for p in root.iterdir()]; root.rmdir()"
        subprocess.run(prefix+['/usr/bin/python3','-I','-c',cleanup],check=True,capture_output=True,timeout=5)


def test_v2_profile_is_explicit_and_legacy_serialization_is_unchanged():
    from app.documents.schemas import ParserProfile, OCRProfile, parse_profile
    native = ParserProfile()
    assert set(native.model_dump()) == {'version', 'policy_version', 'language', 'normalize_whitespace',
        'encoding', 'csv_delimiter', 'code_hash', 'runtime_versions', 'limits', 'runner_policy'}
    old = native.model_dump_json()
    assert parse_profile(old).model_dump_json() == old
    v2 = OCRProfile()
    assert v2.version == 'image-ocr-v2'
    assert parse_profile(v2.model_dump_json()) == v2
    with pytest.raises(ValidationError):
        ParserProfile.model_validate_json(v2.model_dump_json())


def test_ocr_profile_assets_are_deeply_frozen_and_hash_material():
    from app.documents.schemas import OCRProfile
    p = OCRProfile(toolchain={'tesseract': '5.5.0', 'eng.sha256': 'a'*64})
    with pytest.raises(TypeError):
        p.toolchain['eng.sha256'] = 'b'*64
    assert p.model_dump_json() != OCRProfile(toolchain={'tesseract': '5.5.0', 'eng.sha256': 'b'*64}).model_dump_json()


def test_new_transform_policy_keeps_prior_v2_profile_serialized_hash():
    from app.documents.schemas import OCRProfile,parse_profile
    from app.documents.service import processing_profile
    from test_full_settings import settings
    old=OCRProfile()
    assert old.transform_policy=='affine-exif-osd-deskew-v2'
    old_json=old.model_dump_json()
    assert hashlib.sha256(old_json.encode()).hexdigest()=='2eb8886ef02b2facc4dfac046102bdacdf3a8efd36e41d775757f1a351daed60'
    assert parse_profile(old_json).model_dump_json()==old_json
    probe={'isolated':True,'code_hash':'a'*64,'runtime_versions':{},'ocr_toolchain':{'tesseract':'verified'}}
    current=processing_profile(settings(document_ocr_mode='tesseract'),'auto',probe=probe)
    assert current.transform_policy=='affine-exif-osd-deskew-v3'


def test_ocr_evidence_never_exposes_original_text_or_claims_full_visual_coverage():
    from app.documents.schemas import OCRBlock, RasterAnchor, NormalizedDocumentV2, OCRProfile, CoverageV2, parse_document
    anchor = RasterAnchor(frame=1, source_sha256='a'*64, original_size=(100, 80), raster_size=(100, 80),
        original_quad=((1.,2.),(21.,2.),(21.,12.),(1.,12.)), raster_bbox=(1.,2.,21.,12.))
    word = OCRBlock(id='ocr:1:w:1', kind='ocr_word', transcription_text='16', normalized_text='16',
        normalization_map=({'normalized_start':0, 'normalized_end':2, 'original_start':0, 'original_end':2},),
        anchor=anchor, confidence=96.0, languages=('eng',), review_flags=('numeric_ocr_unverified',))
    assert 'original_text' not in word.model_dump()
    assert word.source_map_space == 'ocr_transcription'
    document = NormalizedDocumentV2(content_sha256='a'*64, filename='a.png', mime_type='image/png',
        format='png', profile=OCRProfile(languages=('eng',)), status='partial', blocks=(word,), physical_pages=1,
        coverage=(CoverageV2(id='visual:1',kind='unknown',state='vision_pending',anchor=anchor),))
    assert parse_document(document.model_dump_json()) == document
    with pytest.raises(ValidationError):
        NormalizedDocumentV2.model_validate_json(document.model_copy(update={'status':'complete'}).model_dump_json())


def image_fixture(format='PNG', *, frames=1, orientation=None):
    import io
    from PIL import Image, ImageDraw, ImageFont
    images=[]
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 46)
    for index in range(frames):
        image=Image.new('RGB',(800,240),'white')
        ImageDraw.Draw(image).text((40,60),'Memory 16 GB',font=font,fill='black')
        images.append(image)
    output=io.BytesIO()
    options={}
    if orientation is not None:
        exif=Image.Exif(); exif[274]=orientation;options['exif']=exif
    if frames>1:
        options.update(save_all=True,append_images=images[1:])
    images[0].save(output,format=format,**options)
    return output.getvalue()


@pytest.mark.parametrize('format', ['PNG','JPEG','TIFF','BMP','WEBP'])
def test_manifest_reads_actual_image_format_dimensions_and_frames(format):
    from app.documents.images import raster_manifest
    from app.documents.schemas import OCRProfile
    content=image_fixture(format,frames=3 if format=='TIFF' else 1)
    result=raster_manifest(content,'image/'+format.lower(),OCRProfile())
    assert result['format']==format.lower()
    assert len(result['frames'])==(3 if format=='TIFF' else 1)
    assert all(frame['original_size']==[800,240] for frame in result['frames'])


@pytest.mark.parametrize('format',['PNG','JPEG','TIFF','BMP','WEBP'])
def test_image_magic_and_public_capability_are_ocr_required_hints(format):
    from app.packages.inputs import detect
    from app.packages.service import format_support
    mime,support=detect(image_fixture(format),'renamed.bin')
    assert mime=='image/'+format.lower() and support=='ocr_required'
    assert format_support(mime)=='ocr_required'


def test_frame_pixel_admission_happens_before_load(monkeypatch):
    from app.documents.images import raster_manifest
    from app.documents.schemas import OCRProfile, OCRLimits
    from app.documents.readers import DocumentReadError
    from PIL import Image
    source=image_fixture()
    monkeypatch.setattr(Image.Image,'load',lambda *args: pytest.fail('oversized raster decoded before admission'))
    with pytest.raises(DocumentReadError) as error:
        raster_manifest(source,'image/png',OCRProfile(limits=OCRLimits(max_frame_pixels=100)))
    assert error.value.code=='pixel_limit'


@pytest.mark.parametrize('orientation',range(1,9))
def test_exif_affine_maps_transformed_corners_back_to_original(orientation):
    from app.documents.images import orientation_affine, inverse_affine, apply_affine
    forward,size=orientation_affine(orientation,800,240)
    reverse=inverse_affine(forward)
    corners=((0.,0.),(800.,0.),(800.,240.),(0.,240.))
    transformed=[apply_affine(forward,p) for p in corners]
    assert max(p[0] for p in transformed)==size[0]
    assert max(p[1] for p in transformed)==size[1]
    for original,raster in zip(corners,transformed):
        assert apply_affine(reverse,raster)==pytest.approx(original)


def asymmetric_tiff_fixture(orientations):
    import struct
    output=bytearray(b'II'+struct.pack('<HI',42,8))
    for frame,orientation in enumerate(orientations):
        pixels=bytes(channel for y in range(24) for x in range(80) for channel in (x*3,y*9,(x+y+frame*11)%256))
        offset=len(output);bits=offset+2+11*12+4;strip=bits+6
        entries=((256,4,1,80),(257,4,1,24),(258,3,3,bits),(259,3,1,1),(262,3,1,2),
            (273,4,1,strip),(274,3,1,orientation),(277,3,1,3),(278,4,1,24),
            (279,4,1,len(pixels)),(284,3,1,1))
        output.extend(struct.pack('<H',len(entries)))
        for entry in entries:output.extend(struct.pack('<HHII',*entry))
        next_ifd=strip+len(pixels) if frame<len(orientations)-1 else 0
        output.extend(struct.pack('<IHHH',next_ifd,8,8,8)+pixels)
    return bytes(output)


@pytest.mark.parametrize('orientation',range(2,9))
def test_tiff_orientation_keeps_encoded_basis_and_decoder_transform_once(orientation):
    from app.documents.images import raster_manifest,prepare_raster,apply_affine
    from app.documents.schemas import OCRProfile
    from PIL import Image
    source=asymmetric_tiff_fixture((1,orientation))
    profile=OCRProfile()
    manifest=raster_manifest(source,'image/tiff',profile)
    frame=manifest['frames'][1]
    assert frame['original_size']==[80,24] and frame['exif_orientation']==orientation
    assert frame['decoder_applied_orientation'] is True
    image,anchor=prepare_raster(source,'image/tiff',2,profile)
    try:
        assert anchor.original_size==(80,24)
        assert anchor.decoder_applied_orientation and anchor.exif_orientation==orientation
        assert [t.operation for t in anchor.transforms]==['decoder_orientation','crop']
        forward=anchor.transforms[0].forward
        # Asymmetric encoded pixel values verify actual decoder pixels. This
        # catches double mirrors/rotations even when bounding dimensions agree.
        for x,y in ((0,0),(79,0),(79,23),(0,23),(11,7)):
            tx,ty=apply_affine(forward,(x+.5,y+.5))
            assert image.getpixel((int(tx),int(ty)))==(x*3,y*9,(x+y+11)%256)
        for rx,ry in ((0.,0.),(float(image.width),0.),(float(image.width),float(image.height)),(0.,float(image.height))):
            original=apply_affine(anchor.transforms[0].inverse,(rx,ry))
            assert original in anchor.original_quad
    finally: image.close()


@pytest.mark.parametrize('mode',['RGBA','LA','P','RGBa'])
def test_declared_white_matte_preserves_alpha_visibility_and_asymmetric_pixels(mode):
    from PIL import Image
    from app.documents.images import visual_rgb
    if mode=='RGBA':
        image=Image.new('RGBA',(3,1));image.putdata([(0,0,0,0),(0,40,80,128),(20,60,100,255)])
        expected=[(255,255,255),(127,147,167),(20,60,100)]
    elif mode=='LA':
        image=Image.new('LA',(3,1));image.putdata([(0,0),(40,128),(80,255)])
        expected=[(255,255,255),(147,147,147),(80,80,80)]
    elif mode=='P':
        image=Image.new('P',(3,1));image.putpalette([0,0,0,0,40,80,20,60,100]+[0]*759)
        image.putdata([0,1,2]);image.info['transparency']=bytes((0,128,255))
        expected=[(255,255,255),(127,147,167),(20,60,100)]
    else:
        image=Image.new('RGBA',(3,1));image.putdata([(0,0,0,0),(0,40,80,128),(20,60,100,255)])
        image=image.convert('RGBa')
        expected=[(255,255,255),(127,147,167),(20,60,100)]
    result=visual_rgb(image)
    try: assert list(result.get_flattened_data())==expected
    finally: result.close();image.close()


def test_configured_ocr_missing_asset_fails_closed(tmp_path):
    from app.documents.toolchain import ocr_identity
    from app.documents.readers import DocumentReadError
    with pytest.raises(DocumentReadError):
        ocr_identity(tesseract=str(tmp_path/'missing'), tessdata=str(tmp_path))


def test_live_memory_counts_all_namespace_processes_and_reserved_tmpfs(tmp_path):
    from app.documents.images import sandbox_memory_bytes
    for pid,resident in [('1',100),('2',200)]:
        (tmp_path/pid).mkdir();(tmp_path/pid/'statm').write_text(f'1000 {resident} 0 0 0 0 0\n')
    assert sandbox_memory_bytes(proc_root=tmp_path,page_size=4096,tmpfs_bytes=16384)==300*4096+16384


def test_package_asset_identity_changes_when_python_adapter_changes(tmp_path):
    from types import SimpleNamespace
    from app.documents.toolchain import distribution_hash
    (tmp_path/'adapter.py').write_text('output_policy=1\n')
    (tmp_path/'codec.so').write_bytes(b'public synthetic library bytes')
    distribution=SimpleNamespace(files=('adapter.py','codec.so'),locate_file=lambda name:tmp_path/name)
    first=distribution_hash(distribution)
    (tmp_path/'adapter.py').write_text('output_policy=2\n')
    assert distribution_hash(distribution)!=first


def test_runtime_tree_hashes_sources_data_extensions_and_ignores_generated_caches(tmp_path):
    from app.documents.toolchain import runtime_tree_assets
    (tmp_path/'adapter.py').write_text('value=1\n')
    (tmp_path/'data.bin').write_bytes(b'public runtime data')
    (tmp_path/'extension.so').write_bytes(b'\x7fELF\x02\x01\x01'+b'\x00'*9+b'\x03\x00\x3e\x00'+b'public extension fixture')
    (tmp_path/'__pycache__').mkdir();(tmp_path/'__pycache__'/'adapter.pyc').write_bytes(b'generated')
    files,roots=runtime_tree_assets(tmp_path)
    assert set(files)=={'adapter.py','data.bin','extension.so'}
    assert [path.name for path in roots]==['extension.so']
    first=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
    (tmp_path/'adapter.py').write_text('value=2\n')
    updated,_=runtime_tree_assets(tmp_path)
    assert hashlib.sha256(json.dumps(updated,sort_keys=True).encode()).hexdigest()!=first


def test_heic_original_exif_orientation_is_retained_without_decode():
    from types import SimpleNamespace
    from PIL import Image
    from app.documents.images import heif_orientation
    from app.documents.schemas import OCRProfile
    exif=Image.Exif();exif[274]=6
    frame=SimpleNamespace(info={'exif':exif.tobytes()})
    assert heif_orientation(frame,OCRProfile())==6


def test_pdf_source_dimensions_remain_exact_fractional_user_space():
    from app.documents.schemas import RasterAnchor
    from app.documents.images import pdf_render_affine,_transform,apply_affine,inverse_affine
    forward=pdf_render_affine((30.25,40.125,850.75,320.875),0,(2279,780))
    quad=tuple(apply_affine(inverse_affine(forward),p) for p in ((0.,0.),(2279.,0.),(2279.,780.),(0.,780.)))
    anchor=RasterAnchor(frame=1,page=1,coordinate_space='pdf_original_user_space',source_sha256='a'*64,
        original_size=(820.5,280.75),raster_size=(2279,780),raster_bbox=(0.,0.,2279.,780.),
        original_quad=quad,transforms=(_transform('pdf_render',forward,'Public fractional PDF user-space fixture'),))
    assert anchor.original_size==(820.5,280.75)


def test_raster_anchor_rejects_fabricated_original_polygon():
    from app.documents.schemas import RasterAnchor
    with pytest.raises(ValidationError,match='polygon'):
        RasterAnchor(frame=1,source_sha256='a'*64,original_size=(100,80),raster_size=(100,80),
            raster_bbox=(1.,2.,21.,12.),original_quad=((10.,20.),(20.,20.),(20.,30.),(10.,30.)))


def test_tsv_parser_uses_actual_boxes_confidence_and_numeric_review():
    from app.documents.images import parse_tsv
    from app.documents.schemas import RasterAnchor, OCRProfile
    anchor=RasterAnchor(frame=1,source_sha256='a'*64,original_size=(800,240),raster_size=(800,240),
        original_quad=((0.,0.),(800.,0.),(800.,240.),(0.,240.)),raster_bbox=(0.,0.,800.,240.))
    tsv='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
    tsv+='5\t1\t1\t1\t1\t1\t40\t60\t120\t45\t96.1\tMemory\n'
    tsv+='5\t1\t1\t1\t1\t2\t175\t60\t60\t45\t70.5\t16\n'
    result=parse_tsv(tsv.encode(),anchor,OCRProfile(languages=('eng',)))
    assert len(result)==3
    word=result[1]
    assert word.confidence==70.5 and word.anchor.raster_bbox==(175.,60.,235.,105.)
    assert word.anchor.original_quad==((175.,60.),(235.,60.),(235.,105.),(175.,105.))
    assert set(word.review_flags)=={'low_ocr_confidence','numeric_ocr_unverified'}
    assert result[2].kind=='ocr_line' and result[2].transcription_text=='Memory 16'
    assert result[2].child_word_ids==(result[0].id,result[1].id)


def test_invalid_tsv_bounds_and_output_flood_are_rejected():
    from app.documents.images import parse_tsv
    from app.documents.schemas import RasterAnchor, OCRProfile, OCRLimits
    from app.documents.readers import DocumentReadError
    anchor=RasterAnchor(frame=1,source_sha256='a'*64,original_size=(100,100),raster_size=(100,100),
        original_quad=((0.,0.),(100.,0.),(100.,100.),(0.,100.)),raster_bbox=(0.,0.,100.,100.))
    with pytest.raises(DocumentReadError):
        parse_tsv(b'x'*2048,anchor,OCRProfile(limits=OCRLimits(max_ocr_tsv_bytes=1024)))


def test_processing_profile_requires_explicit_ocr_mode_and_verified_assets():
    from test_full_settings import settings
    from app.documents.service import processing_profile
    from app.documents.runner import DocumentRunnerError
    probe={'isolated':True,'code_hash':'a'*64,'runtime_versions':{}}
    assert processing_profile(settings(),'en',probe=probe).version=='native-v1'
    config=settings(document_ocr_mode='tesseract')
    with pytest.raises(DocumentRunnerError) as error:
        processing_profile(config,'en',probe=probe)
    assert error.value.code=='ocr_unavailable'
    profile=processing_profile(config,'uz',probe={**probe,'ocr_toolchain':{'tesseract':'5.5.0'}})
    assert profile.version=='image-ocr-v2' and profile.languages==('rus','uzb','uzb_cyrl','eng')


@pytest.mark.parametrize('value',[3,6,11])
def test_public_ocr_segmentation_environment_is_coerced(monkeypatch,value):
    from test_full_settings import settings
    monkeypatch.setenv('APP_DOCUMENT_OCR_PAGE_SEGMENTATION',str(value))
    assert settings().document_ocr_page_segmentation==value
    monkeypatch.setenv('APP_DOCUMENT_OCR_PAGE_SEGMENTATION','4')
    with pytest.raises(ValidationError): settings()


def test_pdf_render_affine_retains_crop_origin_and_rotation():
    from app.documents.images import pdf_render_affine, apply_affine
    crop=(20.,30.,220.,130.)
    for rotation in (0,90,180,270):
        width,height=(400,200) if rotation in (0,180) else (200,400)
        affine=pdf_render_affine(crop,rotation,(width,height))
        points=[apply_affine(affine,p) for p in ((20.,30.),(220.,30.),(220.,130.),(20.,130.))]
        assert min(p[0] for p in points)==0 and min(p[1] for p in points)==0
        assert max(p[0] for p in points)==width and max(p[1] for p in points)==height


def test_preprocessing_records_scale_exif_and_reversible_original_geometry():
    from app.documents.images import prepare_raster, _mapped_anchor
    from app.documents.schemas import OCRProfile, OCRLimits
    source=image_fixture('JPEG',orientation=6)
    profile=OCRProfile(limits=OCRLimits(max_raster_pixels=48000),deskew_max_degrees=0.)
    image,anchor=prepare_raster(source,'image/jpeg',1,profile)
    try:
        assert image.size==(120,400)
        assert [t.operation for t in anchor.transforms]==['exif_orientation','scale','crop']
        full=_mapped_anchor(anchor,(0.,0.,120.,400.))
        assert sorted(full.original_quad)==sorted(((0.,0.),(800.,0.),(800.,240.),(0.,240.)))
    finally:
        image.close()


def test_v2_native_evidence_keeps_original_text_and_profile_version():
    from app.documents.readers import normalize
    from app.documents.schemas import OCRProfile
    result=normalize(b'Original  text\n','a.txt','text/plain',OCRProfile())
    assert result.schema_version=='2' and result.profile.version=='image-ocr-v2'
    assert result.blocks[0].original_text=='Original  text\n'


def test_runner_chunks_each_frame_and_enforces_cumulative_budget(monkeypatch):
    from app.documents import runner
    from app.documents.schemas import OCRProfile, NormalizedDocumentV2
    from test_full_settings import settings
    content=b'public image placeholder'
    profile=OCRProfile()
    document=NormalizedDocumentV2(content_sha256=hashlib.sha256(content).hexdigest(),filename='a.tiff',
        mime_type='image/tiff',format='tiff',profile=profile,status='partial',blocks=(),coverage=(),physical_pages=3)
    requests=[]
    def stream(command,raw,*args,**kwargs):
        request=json.loads(raw); requests.append(request)
        if 'frame' not in request:
            return json.dumps({'document':document.model_dump(mode='json'),'cpu_seconds':.1,'manifest':{'format':'tiff','total_pixels':30,
                'frames':[{'frame':n,'original_size':[5,2],'render_size':[5,2]} for n in range(1,4)]}}).encode()
        return json.dumps({'frame':request['frame'],'blocks':[],'coverage':[],'cpu_seconds':.1}).encode()
    monkeypatch.setattr(runner,'_stream_process',stream)
    result=runner.run_document(content,'a.tiff','image/tiff',profile,settings(document_runner_mode='wsl-bubblewrap',document_ocr_mode='tesseract'))
    assert result.schema_version=='2' and [request.get('frame') for request in requests]==[None,1,2,3]


def test_document_deadline_includes_manifest_and_never_starts_late_frame(monkeypatch):
    from app.documents import runner
    from app.documents.schemas import OCRProfile, OCRLimits, NormalizedDocumentV2
    from test_full_settings import settings
    source=b'public';profile=OCRProfile(limits=OCRLimits(max_document_seconds=1.))
    doc=NormalizedDocumentV2(content_sha256=hashlib.sha256(source).hexdigest(),filename='a.png',mime_type='image/png',
        format='png',profile=profile,status='partial',blocks=(),coverage=(),physical_pages=1)
    clock=[0.]
    monkeypatch.setattr(runner.time,'monotonic',lambda:clock[0])
    calls=[]
    def stream(command,raw,*args,**kwargs):
        calls.append(json.loads(raw));clock[0]=2.
        return json.dumps({'document':doc.model_dump(mode='json'),'cpu_seconds':.1,'manifest':{'format':'png','total_pixels':1,
            'frames':[{'frame':1,'original_size':[1,1],'render_size':[1,1]}]}}).encode()
    monkeypatch.setattr(runner,'_stream_process',stream)
    with pytest.raises(runner.DocumentRunnerError) as error:
        runner.run_document(source,'a.png','image/png',profile,settings(document_runner_mode='wsl-bubblewrap'))
    assert error.value.code=='timeout' and len(calls)==1


def test_cumulative_cpu_budget_includes_initial_native_manifest(monkeypatch):
    from app.documents import runner
    from app.documents.schemas import OCRProfile, OCRLimits, NormalizedDocumentV2
    from test_full_settings import settings
    source=b'public';profile=OCRProfile(limits=OCRLimits(max_document_cpu_seconds=.5))
    doc=NormalizedDocumentV2(content_sha256=hashlib.sha256(source).hexdigest(),filename='a.png',mime_type='image/png',
        format='png',profile=profile,status='partial',blocks=(),coverage=(),physical_pages=1)
    calls=[]
    def stream(command,raw,*args,**kwargs):
        calls.append(json.loads(raw))
        return json.dumps({'document':doc.model_dump(mode='json'),'cpu_seconds':1.,'manifest':{'format':'png','total_pixels':1,
            'frames':[{'frame':1,'original_size':[1,1],'render_size':[1,1]}]}}).encode()
    monkeypatch.setattr(runner,'_stream_process',stream)
    with pytest.raises(runner.DocumentRunnerError) as error:
        runner.run_document(source,'a.png','image/png',profile,settings(document_runner_mode='wsl-bubblewrap'))
    assert error.value.code=='cpu_limit' and len(calls)==1


def test_v2_native_only_response_cannot_bypass_whole_document_deadline(monkeypatch):
    from app.documents import runner
    from app.documents.schemas import OCRProfile, OCRLimits, NormalizedDocumentV2
    from test_full_settings import settings
    source=b'public';profile=OCRProfile(limits=OCRLimits(max_document_seconds=1.))
    doc=NormalizedDocumentV2(content_sha256=hashlib.sha256(source).hexdigest(),filename='a.txt',mime_type='text/plain',
        format='txt',profile=profile,status='partial',blocks=(),coverage=(),physical_pages=None)
    clock=[0.]
    monkeypatch.setattr(runner.time,'monotonic',lambda:clock[0])
    def stream(*args,**kwargs):
        clock[0]=2.
        return json.dumps({'document':doc.model_dump(mode='json'),'cpu_seconds':.1,'manifest':None}).encode()
    monkeypatch.setattr(runner,'_stream_process',stream)
    with pytest.raises(runner.DocumentRunnerError) as error:
        runner.run_document(source,'a.txt','text/plain',profile,settings(document_runner_mode='wsl-bubblewrap'))
    assert error.value.code=='timeout'


real_ocr=pytest.mark.skipif(os.environ.get('DOCUMENT_OCR_INTEGRATION')!='1',reason='explicit actual OCR sandbox qualification required')


@pytest.fixture(scope='module')
def actual_ocr_runtime():
    from test_full_settings import settings
    from app.documents.runner import ensure_runner_available
    from app.documents.service import processing_profile
    changes={'document_runner_mode':'wsl-bubblewrap','document_ocr_mode':'tesseract'}
    for field in ('document_runner_mode','document_runtime_python', 'document_runtime_python_sha256','document_parser_code_path','document_wsl_distribution'):
        if value:=os.environ.get('WORKSPACE_TEST_'+field.upper()): changes[field]=value
    from app.config import Settings
    for field in Settings.model_fields:
        if field.startswith('document_ocr_') and (value:=os.environ.get('WORKSPACE_TEST_'+field.upper())):
            annotation=Settings.model_fields[field].annotation
            if field in {'document_ocr_mode'}: changes[field]=value
            elif field in {'document_ocr_low_confidence_review','document_ocr_document_seconds'}: changes[field]=float(value)
            else: changes[field]=int(value)
    config=settings(**changes)
    identity=ensure_runner_available(config)
    return config,processing_profile(config,'auto',probe=identity)


def heic_fixture(frames=2):
    """Only public fixture synthesis uses the installed Linux encoder."""
    import subprocess,base64
    code=f'''import io,base64
from PIL import Image,ImageDraw,ImageFont
from pillow_heif import from_pillow
im=Image.new('RGB',(800,240),'white')
ImageDraw.Draw(im).text((40,60),'Memory 16 GB',font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',46),fill='black')
h=from_pillow(im)
for i in range({frames}-1): h.add_from_pillow(im)
out=io.BytesIO();h.save(out,quality=90);print(base64.b64encode(out.getvalue()).decode())'''
    result=subprocess.run(['wsl','--distribution','Ubuntu','--exec',
        '/home/acer/.local/share/tender-sverka/document-runtime/bin/python','-I','-c',code],
        capture_output=True,timeout=30,check=True)
    return base64.b64decode(result.stdout.strip(),validate=True)


def mixed_pdf_fixture(rotation=0):
    jpeg=image_fixture('JPEG')
    stream=b'BT /F1 12 Tf 40 280 Td (Native evidence) Tj ET q 720 0 0 216 40 50 cm /Im1 Do Q'
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        f'<< /Type /Page /Parent 2 0 R /MediaBox [20 30 860 330] /CropBox [30 40 850 320] /Rotate {rotation} /Resources << /Font << /F1 4 0 R >> /XObject << /Im1 6 0 R >> >> /Contents 5 0 R >>'.encode(),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream',
        b'<< /Type /XObject /Subtype /Image /Width 800 /Height 240 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length '+str(len(jpeg)).encode()+b' >>\nstream\n'+jpeg+b'\nendstream']
    output=bytearray(b'%PDF-1.4\n');offsets=[0]
    for index,obj in enumerate(objects,1):
        offsets.append(len(output));output.extend(f'{index} 0 obj\n'.encode()+obj+b'\nendobj\n')
    start=len(output);output.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]: output.extend(f'{offset:010d} 00000 n \n'.encode())
    output.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
    return bytes(output)


@real_ocr
@pytest.mark.parametrize('format',['JPEG','PNG','TIFF','HEIC','BMP','WEBP'])
def test_actual_sandbox_all_image_formats_and_multipage_frames(actual_ocr_runtime,format):
    from app.documents.runner import run_document
    config,profile=actual_ocr_runtime
    source=heic_fixture(2) if format=='HEIC' else image_fixture(format,frames=3 if format=='TIFF' else 1)
    document=run_document(source,'public.'+format.lower(),'image/'+format.lower(),profile,config)
    assert document.schema_version=='2' and document.status=='partial' and not document.analysis_allowed
    assert document.physical_pages==(2 if format=='HEIC' else 3 if format=='TIFF' else 1)
    assert all('original_text' not in b.model_dump() for b in document.blocks)
    words=[b for b in document.blocks if b.kind=='ocr_word']
    assert {b.anchor.frame for b in words}==set(range(1,document.physical_pages+1))
    assert any(b.transcription_text=='16' and 'numeric_ocr_unverified' in b.review_flags for b in words)
    assert all(b.anchor.source_sha256==hashlib.sha256(source).hexdigest() for b in words)
    assert any(c.state=='vision_pending' for c in document.coverage)
    assert {'eng.sha256','rus.sha256','uzb.sha256','uzb_cyrl.sha256','osd.sha256'}<=set(document.profile.toolchain)


@real_ocr
@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_actual_mixed_pdf_keeps_native_text_and_maps_ocr_original_coordinates(actual_ocr_runtime,rotation):
    from app.documents.runner import run_document
    config,profile=actual_ocr_runtime
    source=mixed_pdf_fixture(rotation)
    document=run_document(source,'mixed.pdf','application/pdf',profile,config)
    assert document.blocks[0].original_text=='Native evidence'
    assert document.blocks[0].anchor.rotation==rotation
    words=[b for b in document.blocks if b.kind=='ocr_word']
    assert words
    # Sparse upside-down text can be insufficient for measured OSD. The
    # pipeline must preserve uncertain orientation rather than invent digits.
    assert any('16' in b.transcription_text for b in words) or any(c.reason=='orientation_unverified' for c in document.coverage)
    for word in words:
        assert word.anchor.page==1 and word.anchor.coordinate_space=='pdf_original_user_space'
        assert word.anchor.media_box==(20.,30.,860.,330.)
        assert word.anchor.crop_box==(30.,40.,850.,320.)
        assert word.anchor.pdf_rotation==rotation
        assert all(20<=x<=860 and 30<=y<=330 for x,y in word.anchor.original_quad)


@real_ocr
def test_actual_exif_orientation_scale_mapping_and_blank_unread_remain_pending(actual_ocr_runtime):
    from app.documents.runner import run_document
    config,profile=actual_ocr_runtime
    # Stored landscape pixels with EXIF 6 are a portrait visual frame.
    source=image_fixture('JPEG',orientation=6)
    document=run_document(source,'exif.jpg','image/jpeg',profile,config)
    assert document.blocks and all(b.anchor.exif_orientation==6 for b in document.blocks)
    assert all(any(t.operation=='exif_orientation' for t in b.anchor.transforms) for b in document.blocks)
    from PIL import Image
    import io
    blank=io.BytesIO();Image.new('RGB',(120,120),'white').save(blank,format='PNG')
    result=run_document(blank.getvalue(),'blank.png','image/png',profile,config)
    assert not result.blocks and any(c.state=='ocr_pending' for c in result.coverage)


@real_ocr
def test_actual_chunked_hundred_page_pdf_does_not_require_whole_document_raster(actual_ocr_runtime):
    from app.documents.runner import run_document
    from test_package_inspection import pdf
    config,profile=actual_ocr_runtime
    # Actual 100 physical PDF pages are separately sandboxed. This is bounded
    # execution evidence, not representative throughput/quality acceptance.
    source=pdf(100).replace(b'300 300',b'030 030')
    document=run_document(source,'hundred.pdf','application/pdf',profile,config)
    assert document.physical_pages==100 and not document.blocks
    assert len({c.anchor.frame for c in document.coverage if c.anchor.kind=='raster'})==100
    assert all(c.state!='extracted' for c in document.coverage if c.anchor.kind=='raster')


@real_ocr
@pytest.mark.parametrize('fault',['memory','output'])
def test_actual_cli_aggregate_memory_output_faults_remove_private_workspace(actual_ocr_runtime,fault):
    from app.documents import runner
    config,_=actual_ocr_runtime
    command,is_wsl=runner._build_command(config)
    source=f'''import sys,tempfile,pathlib,json,os
sys.path.insert(0,'/parser')
from images import _cli
from readers import DocumentReadError
from schemas import OCRProfile,RunnerPolicy
policy=OCRProfile(runner_policy=RunnerPolicy(memory_bytes=67108864)) if {fault!r}=='memory' else OCRProfile()
program="x=bytearray(128*1024*1024);import time;time.sleep(10)" if {fault!r}=='memory' else "import sys;sys.stdout.buffer.write(b'x'*8000000)"
with tempfile.TemporaryDirectory(prefix='public-ocr-fault-',dir='/tmp') as path:
 try:
  _cli([sys.executable,'-I','-c',program],path,policy)
  raise AssertionError('resource limit bypassed')
 except DocumentReadError as exc:
  code=exc.code
assert not os.path.exists(path)
print(json.dumps({{'code':code,'cleaned':True}}))
'''
    command[-1:]=['-c',source]
    result=json.loads(runner._stream_process(command,b'',config,None,is_wsl,handshake=True))
    assert result=={'code':'memory_limit' if fault=='memory' else 'output_limit','cleaned':True}


@real_ocr
def test_actual_ocr_cancellation_stops_frame_children_and_cleans_namespace(actual_ocr_runtime,monkeypatch):
    import threading,time
    from app.documents import runner
    config,profile=actual_ocr_runtime
    cancelled=threading.Event();timer=threading.Timer(2,cancelled.set)
    owned=[];kill=runner._kill_owned
    def record(process,pid,*args):
        owned.append(pid);return kill(process,pid,*args)
    monkeypatch.setattr(runner,'_kill_owned',record)
    timer.start();started=time.monotonic()
    try:
        with pytest.raises(runner.DocumentRunnerError) as error:
            runner.run_document(image_fixture('TIFF',frames=10),'cancel.tiff','image/tiff',profile,config,cancelled.is_set)
        assert error.value.code=='cancelled' and time.monotonic()-started<8 and owned
    finally:
        timer.cancel();timer.join()


@real_ocr
@pytest.mark.parametrize('language,text',[('eng','Memory 16 GB'),('rus','Оперативная память 16 ГБ'),
    ('uzb','Xotira 16 GB'),('uzb_cyrl','Хотира 16 ГБ')])
def test_actual_selected_ru_uz_latin_cyrillic_and_english_models(actual_ocr_runtime,language,text):
    import io
    from PIL import Image,ImageDraw,ImageFont
    from app.documents.runner import run_document
    config,profile=actual_ocr_runtime
    profile=profile.model_copy(update={'languages':(language,)})
    image=Image.new('RGB',(1100,220),'white')
    ImageDraw.Draw(image).text((40,60),text,font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',46),fill='black')
    output=io.BytesIO();image.save(output,format='PNG');image.close()
    result=run_document(output.getvalue(),'language.png','image/png',profile,config)
    assert result.blocks and all(b.languages==(language,) for b in result.blocks)
    assert any(b.transcription_text=='16' and 'numeric_ocr_unverified' in b.review_flags for b in result.blocks)
    if language in {'rus','uzb_cyrl'}:
        assert any(any('\u0400'<=ch<='\u04ff' for ch in b.transcription_text) for b in result.blocks)


@real_ocr
def test_actual_dense_osd_rotation_and_affine_deskew_are_recorded(actual_ocr_runtime):
    import io
    from PIL import Image,ImageDraw,ImageFont
    from app.documents.runner import run_document
    config,profile=actual_ocr_runtime
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',36)
    image=Image.new('RGB',(1400,700),'white');draw=ImageDraw.Draw(image)
    for line in range(10):
        draw.text((40,35+line*62),'Public fixture for rotation and original coordinate mapping.',font=font,fill='black')
    upside=image.transpose(Image.Transpose.ROTATE_180);output=io.BytesIO();upside.save(output,format='PNG');upside.close();image.close()
    result=run_document(output.getvalue(),'rotated.png','image/png',profile,config)
    assert any(any(t.operation=='osd_rotation' for t in b.anchor.transforms) for b in result.blocks)
    ruled=Image.new('RGB',(1200,650),'white');draw=ImageDraw.Draw(ruled)
    for line in range(6):
        draw.text((40,35+line*95),'Memory 16 GB public printed fixture',font=font,fill='black')
        draw.line((40,90+line*95,1150,90+line*95),fill='black',width=2)
    tilted=ruled.rotate(-3,resample=Image.Resampling.BICUBIC,fillcolor='white');output=io.BytesIO();tilted.save(output,format='PNG');tilted.close();ruled.close()
    result=run_document(output.getvalue(),'deskew.png','image/png',profile,config)
    assert any(any(t.operation=='deskew' for t in b.anchor.transforms) for b in result.blocks)
    assert any(c.reason=='deskew_clipped_corners' for c in result.coverage)


@real_ocr
@pytest.mark.parametrize('orientation',range(2,9))
def test_actual_tiff_decoder_orientation_asymmetric_pixels_and_later_frame(actual_ocr_runtime,orientation):
    import base64
    from app.documents import runner
    config,profile=actual_ocr_runtime
    command,is_wsl=runner._build_command(config)
    script=f'''import sys,json,base64
sys.path.insert(0,'/parser')
from schemas import OCRProfile
from images import raster_manifest,prepare_raster
source=base64.b64decode(sys.stdin.buffer.read())
profile=OCRProfile(transform_policy='affine-exif-osd-deskew-v3')
manifest=raster_manifest(source,'image/tiff',profile)
image,anchor=prepare_raster(source,'image/tiff',2,profile)
try:
 pixels=[list(image.getpixel((x,y))) for y in range(image.height) for x in range(image.width)]
 print(json.dumps({{'manifest':manifest,'size':list(image.size),'anchor':anchor.model_dump(mode='json'),'pixels':pixels}}))
finally:image.close()
'''
    command[-1:]=['-c',script]
    source=asymmetric_tiff_fixture((1,orientation))
    result=json.loads(runner._stream_process(command,base64.b64encode(source),config,None,is_wsl,handshake=True))
    frame=result['manifest']['frames'][1]
    assert frame['original_size']==[80,24] and frame['decoder_applied_orientation']
    assert frame['exif_orientation']==orientation and result['anchor']['original_size']==[80.,24.]
    assert result['anchor']['decoder_applied_orientation'] and [t['operation'] for t in result['anchor']['transforms']]==['decoder_orientation','crop']
    # Build the expected decoder image independently from encoded pixel indices,
    # rather than testing only inverse algebra or the decoder's own transpose.
    width,height=result['size'];expected=[None]*(width*height)
    for y in range(24):
        for x in range(80):
            tx,ty={2:(79-x,y),3:(79-x,23-y),4:(x,23-y),5:(y,x),6:(23-y,x),7:(23-y,79-x),8:(y,79-x)}[orientation]
            expected[ty*width+tx]=[x*3,y*9,(x+y+11)%256]
    assert result['pixels']==expected
    expected_quads={2:((80,0),(0,0),(0,24),(80,24)),3:((80,24),(0,24),(0,0),(80,0)),
        4:((0,24),(80,24),(80,0),(0,0)),5:((0,0),(0,24),(80,24),(80,0)),
        6:((0,24),(0,0),(80,0),(80,24)),7:((80,24),(80,0),(0,0),(0,24)),
        8:((80,0),(80,24),(0,24),(0,0))}
    assert result['anchor']['original_quad']==[list(p) for p in expected_quads[orientation]]


@real_ocr
def test_actual_runtime_identity_includes_transitives_stdlib_and_all_loader_roots(actual_ocr_runtime):
    config,profile=actual_ocr_runtime
    assets=profile.toolchain
    assert {'python-stdlib.assets.sha256','runtime-elf-libraries.sha256','libexpat.so.1.11.2.sha256',
        'libmvec.so.1.sha256','libdl.so.2.sha256','libpthread.so.0.sha256','librt.so.1.sha256'}<=set(assets)
    assert {'cryptography','charset-normalizer','cffi','packaging','typing-extensions','annotated-types','typing-inspection'}<=set(assets)
    assert profile.transform_policy=='affine-exif-osd-deskew-v3'


@real_ocr
def test_actual_stdlib_source_variant_changes_frozen_identity_without_modifying_selected_runtime(actual_ocr_runtime):
    import subprocess,uuid
    from app.documents import runner
    config,profile=actual_ocr_runtime
    path='/tmp/public-stdlib-identity-'+uuid.uuid4().hex
    # A server-generated public copy is overlaid only in this test namespace.
    # The system's installed original and the selected runtime remain untouched.
    create=f'''import pathlib
root=pathlib.Path({path!r});root.mkdir(mode=0o700)
original=pathlib.Path('/usr/lib/python3.14/encodings/utf_8.py').read_bytes()
(root/'utf_8.py').write_bytes(original+b'\\n# public identity variant fixture\\n')
'''
    prefix=runner._wsl_prefix(config)
    subprocess.run(prefix+['/usr/bin/python3','-I','-c',create],check=True,capture_output=True,timeout=5)
    try:
        command,is_wsl=runner._build_command(config,probe=True)
        at=command.index('--')
        command[at:at]=['--ro-bind',path+'/utf_8.py','/usr/lib/python3.14/encodings/utf_8.py']
        changed=json.loads(runner._stream_process(command,b'',config,None,is_wsl,handshake=True))
        assert changed['isolated'] and changed['code_hash']==profile.code_hash
        assert changed['ocr_toolchain']['python-stdlib.assets.sha256']!=profile.toolchain['python-stdlib.assets.sha256']
        assert changed['ocr_toolchain']['python.sha256']==profile.toolchain['python.sha256']
        assert changed['ocr_toolchain']['runtime-elf-libraries.sha256']==profile.toolchain['runtime-elf-libraries.sha256']
    finally:
        cleanup=f"import pathlib; root=pathlib.Path({path!r}); (root/'utf_8.py').unlink(); root.rmdir()"
        subprocess.run(prefix+['/usr/bin/python3','-I','-c',cleanup],check=True,capture_output=True,timeout=5)


@real_ocr
@pytest.mark.parametrize('mode,format',[('RGBA','PNG'),('LA','PNG'),('P','PNG'),('RGBA','TIFF'),('RGBA','WEBP'),('RGBA','HEIC')])
def test_actual_alpha_white_matte_partial_and_invisible_pixels(actual_ocr_runtime,mode,format):
    import base64
    from app.documents import runner
    config,_=actual_ocr_runtime
    command,is_wsl=runner._build_command(config)
    script=f'''import sys,io,json
sys.path.insert(0,'/parser')
from PIL import Image
from images import prepare_raster
from schemas import OCRProfile
mode={mode!r};format={format!r}
if mode=='RGBA':
 image=Image.new(mode,(3,1));image.putdata([(0,0,0,0),(0,40,80,128),(20,60,100,255)])
elif mode=='LA':
 image=Image.new(mode,(3,1));image.putdata([(0,0),(40,128),(80,255)])
else:
 image=Image.new(mode,(3,1));image.putpalette([0,0,0,0,40,80,20,60,100]+[0]*759)
 image.putdata([0,1,2]);image.info['transparency']=bytes((0,128,255))
source=io.BytesIO()
if format=='HEIC':
 from pillow_heif import from_pillow,open_heif
 from_pillow(image).save(source,quality=-1)
 decoded=open_heif(source.getvalue())[0].to_pillow().convert('RGBA')
else:
 image.save(source,format=format,**({{'lossless':True,'exact':True}} if format=='WEBP' else {{}}))
 decoded=Image.open(io.BytesIO(source.getvalue())).convert('RGBA')
raw=list(decoded.get_flattened_data());decoded.close();image.close()
rendered,anchor=prepare_raster(source.getvalue(),'image/'+format.lower(),1,OCRProfile(transform_policy='affine-exif-osd-deskew-v3'))
try:
 print(json.dumps({{'pixels':list(rendered.get_flattened_data()),'decoded':raw,'anchor':anchor.model_dump(mode='json')}}))
finally:rendered.close()
'''
    command[-1:]=['-c',script]
    result=json.loads(runner._stream_process(command,b'',config,None,is_wsl,handshake=True))
    assert result['pixels'][0]==[255,255,255]
    for pixel,rgba in zip(result['pixels'],result['decoded']):
        alpha=rgba[3]
        expected=[(channel*alpha+255*(255-alpha)+127)//255 for channel in rgba[:3]]
        assert pixel==expected
    anchor=result['anchor']
    assert anchor['original_size']==[3.,1.] and anchor['raster_size']==[3,1]
    assert anchor['original_quad']==[[0.,0.],[3.,0.],[3.,1.],[0.,1.]]
    assert any('white RGB paper matte' in transform['note'] for transform in anchor['transforms'])


@real_ocr
def test_actual_all_transparent_hidden_printed_text_is_unread_blank_ocr(actual_ocr_runtime):
    import io
    from PIL import Image,ImageDraw,ImageFont
    from app.documents.runner import run_document
    config,profile=actual_ocr_runtime
    source=Image.new('RGBA',(800,240),(255,255,255,0))
    ImageDraw.Draw(source).text((40,60),'Memory 16 GB',font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',46),fill=(0,0,0,0))
    output=io.BytesIO();source.save(output,format='PNG');source.close()
    result=run_document(output.getvalue(),'invisible.png','image/png',profile,config)
    assert not result.blocks and result.status=='partial' and not result.analysis_allowed
    assert any(c.state=='ocr_pending' for c in result.coverage) and any(c.state=='vision_pending' for c in result.coverage)
