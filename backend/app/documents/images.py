"""Pure bounded raster adapters. Imported only by the isolated parser in production."""
from __future__ import annotations

from contextlib import closing

import hashlib
import io
import math
import mmap
from pathlib import Path
import subprocess
import tempfile
import time
import warnings

if __package__:
    from .readers import DocumentReadError, _normalized
    from .schemas import OCRProfile, RasterTransform, RasterAnchor, OCRBlock, CoverageV2
else:
    from readers import DocumentReadError, _normalized
    from schemas import OCRProfile, RasterTransform, RasterAnchor, OCRBlock, CoverageV2


IDENTITY = (1.,0.,0.,1.,0.,0.)


def apply_affine(matrix, point):
    a,b,c,d,e,f = matrix
    x,y = point
    return a*x+c*y+e, b*x+d*y+f


def inverse_affine(matrix):
    a,b,c,d,e,f = matrix
    determinant=a*d-b*c
    if abs(determinant)<1e-12:
        raise DocumentReadError('geometry', 'Singular raster transform')
    return (d/determinant,-b/determinant,-c/determinant,a/determinant,
            (c*f-d*e)/determinant,(b*e-a*f)/determinant)


def orientation_affine(orientation,width,height):
    matrices={1:IDENTITY,2:(-1.,0.,0.,1.,float(width),0.),3:(-1.,0.,0.,-1.,float(width),float(height)),
        4:(1.,0.,0.,-1.,0.,float(height)),5:(0.,1.,1.,0.,0.,0.),
        6:(0.,1.,-1.,0.,float(height),0.),7:(0.,-1.,-1.,0.,float(height),float(width)),
        8:(0.,-1.,1.,0.,0.,float(width))}
    if orientation not in matrices:
        raise DocumentReadError('geometry','Invalid EXIF orientation')
    return matrices[orientation], (height,width) if orientation>=5 else (width,height)


def _dimensions(width,height,profile):
    limits=profile.limits
    if width<=0 or height<=0 or max(width,height)>limits.max_dimension:
        raise DocumentReadError('dimension_limit','Raster dimensions exceed bounded policy')
    if width*height>limits.max_frame_pixels:
        raise DocumentReadError('pixel_limit','Raster frame exceeds bounded pixel policy')


def _open_image(content):
    from PIL import Image
    # Register only the dedicated plugin; no host decoder or external command.
    if len(content)>=12 and content[4:8]==b'ftyp':
        from pillow_heif import register_heif_opener
        register_heif_opener()
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        try:
            return Image.open(io.BytesIO(content))
        except Exception:
            raise DocumentReadError('invalid_image','Image header could not be decoded') from None


def heif_orientation(frame,profile):
    from PIL import Image
    raw=frame.info.get('exif')
    if not raw: return 1
    if len(raw)>profile.limits.max_xml_bytes:
        raise DocumentReadError('metadata_limit','HEIC EXIF metadata exceeds bounded policy')
    try:
        exif=Image.Exif();exif.load(raw)
        value=int(exif.get(274,1))
        if not 1<=value<=8: raise ValueError('EXIF orientation')
        return value
    except (ValueError,OSError,TypeError):
        raise DocumentReadError('geometry','HEIC EXIF orientation is malformed') from None


def _image_geometry(source,profile):
    """TIFF's .size is decoder-oriented, not the encoded IFD pixel basis."""
    if source.format=='TIFF':
        original=(source.tag_v2.get(256),source.tag_v2.get(257))
        orientation=source.tag_v2.get(274,1)
        if any(type(value)is not int for value in (*original,orientation)) or not 1<=orientation<=8:
            raise DocumentReadError('geometry','TIFF encoded dimensions/orientation are malformed')
        _dimensions(*original,profile)
        _,visual=orientation_affine(orientation,*original)
        if source.size!=visual:
            raise DocumentReadError('geometry','TIFF decoder metadata disagrees with encoded geometry')
        return original,orientation,visual,orientation!=1
    original=source.size
    _dimensions(*original,profile)
    orientation=int(source.getexif().get(274,1))
    _,visual=orientation_affine(orientation,*original)
    return original,orientation,visual,False


def raster_manifest(content,mime_type,profile):
    if len(content)>profile.limits.max_input_bytes:
        raise DocumentReadError('input_limit','Raster input exceeds byte policy')
    frames=[]
    total=0
    if mime_type=='application/pdf' or content.startswith(b'%PDF-'):
        import pypdfium2 as pdfium
        try:
            with pdfium.PdfDocument(content) as pdf:
                if len(pdf)>profile.limits.max_pages:
                    raise DocumentReadError('page_limit','PDF physical page count exceeds policy')
                for index in range(len(pdf)):
                    with closing(pdf[index]) as page:
                        width,height=page.get_size()
                        pixels=(math.ceil(width*profile.raster_dpi/72),math.ceil(height*profile.raster_dpi/72))
                        _dimensions(*pixels,profile)
                        total+=pixels[0]*pixels[1]
                        if total>profile.limits.max_total_pixels:
                            raise DocumentReadError('pixel_limit','Document cumulative pixels exceed policy')
                        crop=page.get_bbox()
                        frames.append({'frame':index+1,'original_size':[crop[2]-crop[0],crop[3]-crop[1]],'render_size':list(pixels),
                            'media_box':list(page.get_mediabox()),'crop_box':list(page.get_bbox()),
                            'pdf_rotation':page.get_rotation(),'exif_orientation':1,'decoder_applied_orientation':False})
            return {'format':'pdf','frames':frames,'total_pixels':total}
        except DocumentReadError:
            raise
        except Exception:
            raise DocumentReadError('invalid_pdf','PDF raster metadata failed') from None
    if len(content)>=12 and content[4:8]==b'ftyp':
        from pillow_heif import open_heif, options
        options.DECODE_THREADS=1
        try:
            heif=open_heif(content)
            if not any(format in heif.mimetype for format in ('heic','heif')):
                raise DocumentReadError('invalid_image','Container is not a supported HEIC visual source')
            if not 0<len(heif)<=profile.limits.max_pages:
                raise DocumentReadError('page_limit','HEIC frame count exceeds policy')
            for index,frame in enumerate(heif):
                width,height=frame.size
                _dimensions(width,height,profile)
                total+=width*height
                if total>profile.limits.max_total_pixels:
                    raise DocumentReadError('pixel_limit','Document cumulative pixels exceed policy')
                frames.append({'frame':index+1,'original_size':[width,height],'render_size':[width,height],
                    'exif_orientation':heif_orientation(frame,profile),'decoder_applied_orientation':True})
            return {'format':'heic','frames':frames,'total_pixels':total}
        except DocumentReadError:
            raise
        except Exception:
            raise DocumentReadError('invalid_image','HEIC metadata unavailable') from None
    image=_open_image(content)
    try:
        format=image.format.lower()
        if format=='heif': format='heic'
        if format not in {'jpeg','png','tiff','heic','bmp','webp'}:
            raise DocumentReadError('invalid_image','Unsupported actual raster format')
        index=0
        while True:
            if index>=profile.limits.max_pages:
                raise DocumentReadError('page_limit','Image frame count exceeds policy')
            original,orientation,visual,applied=_image_geometry(image,profile)
            width,height=original
            total+=width*height
            if total>profile.limits.max_total_pixels:
                raise DocumentReadError('pixel_limit','Document cumulative pixels exceed policy')
            frames.append({'frame':index+1,'original_size':[width,height],'render_size':list(visual),
                'exif_orientation':orientation,'decoder_applied_orientation':applied})
            index+=1
            try:
                image.seek(index)
            except EOFError:
                break
        return {'format':format,'frames':frames,'total_pixels':total}
    except DocumentReadError:
        raise
    except Exception:
        raise DocumentReadError('invalid_image','Raster frame metadata failed') from None
    finally:
        image.close()


def _transform(operation,forward,note):
    return RasterTransform(operation=operation,forward=tuple(float(x) for x in forward),
                           inverse=inverse_affine(forward),note=note)


def pdf_render_affine(crop,rotation,size):
    x0,y0,x1,y1=crop
    width,height=x1-x0,y1-y0
    if min(width,height)<=0:
        raise DocumentReadError('geometry','Invalid PDF crop geometry')
    # Original PDF coordinates use upward Y, raster edges use downward Y.
    unrotated=(1.,0.,0.,-1.,-x0,y1)
    orient={0:1,90:6,180:3,270:8}.get(rotation)
    if orient is None:
        raise DocumentReadError('geometry','Unsupported PDF rotation')
    rotated,dimensions=orientation_affine(orient,width,height)
    scale=(size[0]/dimensions[0],0.,0.,size[1]/dimensions[1],0.,0.)
    return compose_affine(scale,compose_affine(rotated,unrotated))


def compose_affine(after,before):
    a,b,c,d,e,f=after
    g,h,i,j,k,l=before
    return (a*g+c*h,b*g+d*h,a*i+c*j,b*i+d*j,a*k+c*l+e,b*k+d*l+f)


def visual_rgb(source):
    """V3 paper rendering: composite visible alpha onto declared white RGB matte."""
    from PIL import Image
    if any(band.upper()=='A' for band in source.getbands()) or 'transparency' in source.info:
        rgba=source.convert('RGBA')
        white=Image.new('RGBA',rgba.size,(255,255,255,255))
        try:
            white.alpha_composite(rgba)
            return white.convert('RGB')
        finally:
            rgba.close();white.close()
    return source.convert('RGB')


def prepare_raster(content,mime_type,frame_number,profile):
    """Admit one frame before decoding. Never keep previous frames alive."""
    from PIL import Image
    if not 1<=frame_number<=profile.limits.max_pages:
        raise DocumentReadError('page_limit','Invalid raster frame')
    transforms=[]
    metadata={}
    orientation=1
    applied=False
    if mime_type=='application/pdf' or content.startswith(b'%PDF-'):
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(content) as pdf:
            if frame_number>len(pdf):
                raise DocumentReadError('page_limit','PDF frame does not exist')
            with closing(pdf[frame_number-1]) as page:
                width,height=page.get_size()
                scale=profile.raster_dpi/72
                proposed=(math.ceil(width*scale),math.ceil(height*scale))
                _dimensions(*proposed,profile)
                if proposed[0]*proposed[1]>profile.limits.max_raster_pixels:
                    scale*=math.sqrt(profile.limits.max_raster_pixels/(proposed[0]*proposed[1]))*.999
                with closing(page.render(scale=scale)) as bitmap:
                    with closing(bitmap.to_pil()) as raster:
                        image=visual_rgb(raster)
                crop=tuple(float(x) for x in page.get_bbox())
                original=(crop[2]-crop[0],crop[3]-crop[1])
                metadata={'page':frame_number,'coordinate_space':'pdf_original_user_space',
                    'media_box':tuple(float(x) for x in page.get_mediabox()),'crop_box':crop,'pdf_rotation':page.get_rotation()}
                transforms.append(_transform('pdf_render',pdf_render_affine(crop,page.get_rotation(),image.size),
                    'PDFium crop/media intersection and page rotation; original PDF user space to raster edges'))
    elif len(content)>=12 and content[4:8]==b'ftyp':
        from pillow_heif import open_heif, options
        options.DECODE_THREADS=1
        heif=open_heif(content)
        if frame_number>len(heif):
            raise DocumentReadError('page_limit','HEIC frame does not exist')
        frame=heif[frame_number-1]
        _dimensions(*frame.size,profile)
        original=frame.size
        orientation=heif_orientation(frame,profile)
        with closing(frame.to_pillow()) as raster:
            image=visual_rgb(raster)
        if image.size!=original:
            image.close()
            raise DocumentReadError('geometry','HEIC decoder changed admitted visual-frame geometry')
        applied=True
        transforms.append(_transform('decoder_orientation',IDENTITY,
            'Original visual-frame pixel space is libheif container-oriented output; encoded tile/grid coordinates are not asserted'))
    else:
        source=_open_image(content)
        try:
            source.seek(frame_number-1)
            original,orientation,visual,applied=_image_geometry(source,profile)
            image=visual_rgb(source)
            if source.format=='TIFF':
                if image.size!=visual or source.tag_v2.get(274) is not None:
                    image.close()
                    raise DocumentReadError('geometry','TIFF decoder failed its verified orientation contract')
                if applied:
                    forward,_=orientation_affine(orientation,*original)
                    transforms.append(_transform('decoder_orientation',forward,
                        'Pillow TIFF decoder applied encoded IFD orientation once; original coordinates retain stored width/height'))
            elif orientation!=1:
                forward,_=orientation_affine(orientation,*original)
                operation={2:Image.Transpose.FLIP_LEFT_RIGHT,3:Image.Transpose.ROTATE_180,
                    4:Image.Transpose.FLIP_TOP_BOTTOM,5:Image.Transpose.TRANSPOSE,
                    6:Image.Transpose.ROTATE_270,7:Image.Transpose.TRANSVERSE,8:Image.Transpose.ROTATE_90}.get(orientation)
                if operation is None:
                    raise DocumentReadError('geometry','Unsupported EXIF orientation')
                changed=image.transpose(operation);image.close();image=changed
                transforms.append(_transform('exif_orientation',forward,'EXIF edge-coordinate transform applied once'))
        finally:
            source.close()
    # Validate decoder output dimensions again: a decoder may change dimensions
    # during decompression, and cannot enlarge the admitted frame silently.
    _dimensions(*image.size,profile)
    width,height=image.size
    if width*height>profile.limits.max_raster_pixels:
        ratio=math.sqrt(profile.limits.max_raster_pixels/(width*height))
        size=(max(1,int(width*ratio)),max(1,int(height*ratio)))
        changed=image.resize(size,Image.Resampling.LANCZOS);image.close();image=changed
        transforms.append(_transform('scale',(size[0]/width,0.,0.,size[1]/height,0.,0.),'Bounded raster downsampling; recognition quality remains uncalibrated'))
    transforms.append(_transform('crop',IDENTITY,'Full admitted visual frame; white RGB paper matte composites alpha without moving pixels; no content crop applied'))
    width,height=image.size
    bbox=(0.,0.,float(width),float(height))
    polygon=((0.,0.),(float(width),0.),(float(width),float(height)),(0.,float(height)))
    for transform in reversed(transforms):
        polygon=tuple(apply_affine(transform.inverse,p) for p in polygon)
    base=RasterAnchor(frame=frame_number,source_sha256=hashlib.sha256(content).hexdigest(),
        original_size=original,raster_size=image.size,original_quad=polygon,
        raster_bbox=bbox,transforms=tuple(transforms),
        exif_orientation=orientation,decoder_applied_orientation=applied,**metadata)
    return image,_mapped_anchor(base,base.raster_bbox)


def sandbox_memory_bytes(*,proc_root='/proc',page_size=mmap.PAGESIZE,tmpfs_bytes=0):
    """Count RSS conservatively across the private PID namespace (shared pages twice)."""
    total=tmpfs_bytes
    count=0
    for process in Path(proc_root).iterdir():
        if not process.name.isdigit(): continue
        count+=1
        if count>128:
            raise DocumentReadError('process_limit','Sandbox process inventory exceeded bounded policy')
        try:
            with (process/'statm').open('rb') as source:
                fields=source.read(512).split()
            total+=int(fields[1])*page_size
        except (FileNotFoundError,ProcessLookupError):
            continue
        except (OSError,ValueError,IndexError):
            raise DocumentReadError('memory_unavailable','Sandbox memory accounting unavailable') from None
    if not count:
        raise DocumentReadError('memory_unavailable','Sandbox process accounting unavailable')
    return total


def _cli(command,workspace,profile,*,stdout_name='stdout'):
    """All files are in the sandbox's bounded private tmpfs; argv contains no source names."""
    output=Path(workspace)/stdout_name
    error=Path(workspace)/'stderr'
    with output.open('wb') as out,error.open('wb') as err:
        try:
            process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=out,stderr=err,cwd=workspace,
                env={'HOME':workspace,'TMPDIR':workspace,'LANG':'C.UTF-8','OMP_THREAD_LIMIT':'1','OPENBLAS_NUM_THREADS':'1'},
                close_fds=True)
            deadline=time.monotonic()+max(.1,profile.runner_policy.timeout_seconds-1)
            try:
                while process.poll() is None:
                    if time.monotonic()>=deadline:
                        raise DocumentReadError('timeout','OCR executable exceeded page time policy')
                    if sandbox_memory_bytes(tmpfs_bytes=profile.runner_policy.file_bytes)>profile.runner_policy.memory_bytes:
                        raise DocumentReadError('memory_limit','Aggregate sandbox resident memory exceeded policy')
                    if output.stat().st_size>profile.limits.max_ocr_tsv_bytes or error.stat().st_size>65536:
                        raise DocumentReadError('output_limit','OCR diagnostic output exceeded policy')
                    time.sleep(.01)
            finally:
                if process.poll() is None: process.kill()
                process.wait(timeout=2)
        except (OSError,subprocess.SubprocessError):
            raise DocumentReadError('ocr_failed','OCR process failed inside bounded workspace') from None
    if output.stat().st_size>profile.limits.max_ocr_tsv_bytes or error.stat().st_size>65536:
        raise DocumentReadError('output_limit','OCR diagnostic output exceeded policy')
    return process.returncode,output.read_bytes()


def _deskew(image,profile):
    if profile.deskew_max_degrees==0:
        return image,None
    import cv2
    import numpy as np
    cv2.setNumThreads(1)
    # Estimate on a bounded thumbnail; the full raster is allocated only once
    # for an actual transform. Uncertain/no-line images stay unchanged.
    sample=image.copy();sample.thumbnail((1600,1600))
    gray=cv2.cvtColor(np.asarray(sample),cv2.COLOR_RGB2GRAY);sample.close()
    edges=cv2.Canny(gray,50,150)
    lines=cv2.HoughLinesP(edges,1,np.pi/1800,threshold=80,minLineLength=100,maxLineGap=20)
    if lines is None:
        return image,None
    angles=[]
    for x0,y0,x1,y1 in lines.reshape(-1,4)[:2000]:
        angle=math.degrees(math.atan2(int(y1)-int(y0),int(x1)-int(x0)))
        if abs(angle)<=profile.deskew_max_degrees:
            angles.append(angle)
    if len(angles)<3:
        return image,None
    angle=float(np.median(angles))
    if abs(angle)<.2 or float(np.std(angles))>2.:
        return image,None
    width,height=image.size
    matrix=cv2.getRotationMatrix2D((width/2,height/2),angle,1.)
    raster=cv2.warpAffine(np.asarray(image),matrix,(width,height),borderValue=(255,255,255))
    from PIL import Image
    changed=Image.fromarray(raster);image.close()
    # OpenCV addresses pixel centers; translate by half a pixel to the edge
    # convention used by Tesseract rectangles before storing the exact inverse.
    a,c,e=matrix[0];b,d,f=matrix[1]
    edge=(float(a),float(b),float(c),float(d),float(e)+.5-.5*float(a+c),float(f)+.5-.5*float(b+d))
    return changed,_transform('deskew',edge,'Bounded affine line-angle deskew; canvas clips rotated corners and those regions remain pending')


def ocr_frame(content,mime_type,frame_number,profile):
    from PIL import Image
    image,anchor=prepare_raster(content,mime_type,frame_number,profile)
    uncertain=[]
    try:
        with tempfile.TemporaryDirectory(prefix='ocr-',dir='/tmp') as workspace:
            raster=Path(workspace)/'input.png'
            image.save(raster,format='PNG')
            if raster.stat().st_size>profile.runner_policy.file_bytes:
                raise DocumentReadError('file_limit','Raster temporary file exceeds policy')
            code,osd=_cli(['/usr/bin/tesseract',str(raster),'stdout','--tessdata-dir','/usr/share/tesseract-ocr/5/tessdata',
                          '-l','osd','--psm','0'],workspace,profile,stdout_name='orientation')
            import re
            rotate=re.search(rb'Rotate: (0|90|180|270)\b',osd)
            confidence=re.search(rb'Orientation confidence: ([0-9.]+)',osd)
            if code==0 and rotate and confidence and float(confidence.group(1))>=profile.orientation_min_confidence:
                degrees=int(rotate.group(1))
                if degrees:
                    orientation={90:6,180:3,270:8}[degrees]
                    affine,_=orientation_affine(orientation,*image.size)
                    operation={90:Image.Transpose.ROTATE_270,180:Image.Transpose.ROTATE_180,270:Image.Transpose.ROTATE_90}[degrees]
                    changed=image.transpose(operation);image.close();image=changed
                    anchor=anchor.model_copy(update={'transforms':anchor.transforms+(_transform('osd_rotation',affine,'Tesseract OSD rotation and measured confidence'),)})
            else:
                uncertain.append('orientation_unverified')
            image,deskew=_deskew(image,profile)
            if deskew:
                anchor=anchor.model_copy(update={'transforms':anchor.transforms+(deskew,)})
                uncertain.append('deskew_clipped_corners')
            image.save(raster,format='PNG')
            width,height=image.size
            anchor=anchor.model_copy(update={'raster_size':image.size,'raster_bbox':(0.,0.,float(width),float(height))})
            anchor=_mapped_anchor(anchor,anchor.raster_bbox)
            code,_=_cli(['/usr/bin/tesseract',str(raster),str(Path(workspace)/'result'),
                '--tessdata-dir','/usr/share/tesseract-ocr/5/tessdata','-l','+'.join(profile.languages),
                '--psm',str(profile.page_segmentation),'tsv'],workspace,profile)
            if code:
                raise DocumentReadError('ocr_failed','Tesseract could not recognize admitted raster')
            tsv=Path(workspace)/'result.tsv'
            if tsv.stat().st_size>profile.limits.max_ocr_tsv_bytes:
                raise DocumentReadError('output_limit','OCR TSV exceeds policy')
            blocks=parse_tsv(tsv.read_bytes(),anchor,profile)
            coverage=[CoverageV2(id=f'coverage:ocr:{frame_number}',kind='image',state='review_pending',anchor=anchor,
                reason='OCR transcription requires review; numeric acceptance policy is uncalibrated',block_ids=tuple(b.id for b in blocks)),
                CoverageV2(id=f'coverage:visual:{frame_number}',kind='unknown',state='vision_pending',anchor=anchor,
                    reason='Handwriting, seals, signatures, tables, drawings and unread visual regions are not certified by printed OCR')]
            if not blocks:
                coverage.append(CoverageV2(id=f'coverage:unread:{frame_number}',kind='image',state='ocr_pending',anchor=anchor,
                    reason='No readable words recognized; absence of text is not established'))
            for flag in uncertain:
                coverage.append(CoverageV2(id=f'coverage:{flag}:{frame_number}',kind='unknown',state='review_pending',anchor=anchor,reason=flag))
            if anchor.decoder_applied_orientation and anchor.exif_orientation!=1 and content[4:8]==b'ftyp':
                coverage.append(CoverageV2(id=f'coverage:heic_orientation:{frame_number}',kind='unknown',state='review_pending',anchor=anchor,
                    reason='Container-oriented HEIC visual pixels retained; original EXIF orientation is recorded and is not reapplied'))
            return blocks,tuple(coverage)
    finally:
        image.close()


def _mapped_anchor(anchor,bbox):
    x0,y0,x1,y1=bbox
    points=((x0,y0),(x1,y0),(x1,y1),(x0,y1))
    for transform in reversed(anchor.transforms):
        points=tuple(apply_affine(transform.inverse,p) for p in points)
    return RasterAnchor(**{**anchor.model_dump(),'raster_bbox':bbox,'original_quad':points})


def parse_tsv(raw,anchor,profile):
    import csv
    if len(raw)>profile.limits.max_ocr_tsv_bytes:
        raise DocumentReadError('output_limit','OCR TSV exceeds bounded policy')
    blocks=[]
    lines={}
    characters=0
    try:
        rows=csv.DictReader(io.StringIO(raw.decode('utf-8')),delimiter='\t')
        expected={'level','page_num','block_num','par_num','line_num','word_num','left','top','width','height','conf','text'}
        if set(rows.fieldnames or ())!=expected:
            raise ValueError('TSV fields')
        for index,row in enumerate(rows):
            if index>profile.limits.max_blocks*5:
                raise DocumentReadError('block_limit','OCR structure exceeds policy')
            if row['level']!='5' or not row['text'].strip():
                continue
            text=row['text']
            characters+=len(text)
            if len(text)>profile.limits.max_block_chars or characters>profile.limits.max_text_chars:
                raise DocumentReadError('text_limit','OCR text exceeds policy')
            left,top,width,height=(float(row[key]) for key in ('left','top','width','height'))
            confidence=float(row['conf'])
            flags=[]
            if confidence<profile.low_confidence_review: flags.append('low_ocr_confidence')
            if any(ch.isnumeric() for ch in text): flags.append('numeric_ocr_unverified')
            if not 0<=confidence<=100 or width<=0 or height<=0:
                raise ValueError('TSV confidence/size')
            normalized,mapping=_normalized(text,profile.normalize_whitespace)
            word=OCRBlock(id=f'ocr:{anchor.frame}:w:{index}',kind='ocr_word',transcription_text=text,
                normalized_text=normalized,normalization_map=mapping,anchor=_mapped_anchor(anchor,(left,top,left+width,top+height)),
                confidence=confidence,languages=profile.languages,review_flags=tuple(flags))
            blocks.append(word)
            key=tuple(row[key] for key in ('block_num','par_num','line_num'))
            lines.setdefault(key,[]).append(word)
            if len(blocks)+len(lines)>profile.limits.max_blocks:
                raise DocumentReadError('block_limit','OCR blocks exceed policy')
        for index,words in enumerate(lines.values()):
            text=' '.join(word.transcription_text for word in words)
            characters+=len(text)
            if len(text)>profile.limits.max_block_chars or characters>profile.limits.max_text_chars:
                raise DocumentReadError('text_limit','OCR line text exceeds policy')
            normalized,mapping=_normalized(text,profile.normalize_whitespace)
            boxes=[word.anchor.raster_bbox for word in words]
            bbox=(min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes))
            # Minimum constituent confidence is conservative; this is explicitly
            # a line aggregation of actual Tesseract word scores, not calibration.
            blocks.append(OCRBlock(id=f'ocr:{anchor.frame}:l:{index}',kind='ocr_line',transcription_text=text,
                normalized_text=normalized,normalization_map=mapping,anchor=_mapped_anchor(anchor,bbox),
                confidence=min(w.confidence for w in words),languages=profile.languages,
                child_word_ids=tuple(w.id for w in words),review_flags=tuple(sorted({f for w in words for f in w.review_flags}))))
        return tuple(blocks)
    except DocumentReadError:
        raise
    except (ValueError,KeyError,TypeError,csv.Error):
        raise DocumentReadError('invalid_ocr','OCR output has invalid evidence') from None
