"""Credential-free toolchain identity. Probes run outside application SQL."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile

if __package__:
    from .readers import DocumentReadError
    from .bootstrap import SOURCE_ONLY_START, PYTHON_FLAGS, STARTUP_GUARD
else:
    from readers import DocumentReadError
    from bootstrap import SOURCE_ONLY_START, PYTHON_FLAGS, STARTUP_GUARD

CODE_FILES=('__init__.py','entrypoint.py','readers.py','schemas.py','images.py','toolchain.py','bootstrap.py','archives.py','archive_entrypoint.py')
NATIVE_PACKAGES=('pdfplumber','python-docx','openpyxl','pydantic','lxml','Pillow','pdfminer.six','pypdfium2')
OCR_PACKAGES=('Pillow','pillow-heif','numpy','opencv-python-headless','pypdfium2')
TESSDATA='/usr/share/tesseract-ocr/5/tessdata'
_MAX_ASSET_FILES=30_000
_MAX_ASSET_BYTES=2_000_000_000
_MAX_DISTRIBUTIONS=64
_MAX_ELF_ROOTS=512


def file_hash(path):
    value=hashlib.sha256()
    with open(path,'rb') as source:
        while data:=source.read(1024*1024):
            value.update(data)
    return value.hexdigest()


def code_hash(root):
    hashes={name:file_hash(Path(root)/name) for name in CODE_FILES}
    return hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def distribution_hash(distribution):
    # Python adapter sources and package data can affect pixels/text just as
    # compiled extensions do; bytecode caches are generated, not source assets.
    files={str(file):file_hash(distribution.locate_file(file)) for file in distribution.files or ()
           if not str(file).endswith('.pyc') and '__pycache__' not in str(file)}
    return hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _elf(path):
    with path.open('rb') as source:
        header=source.read(20)
    # Relocatable build objects are hashed assets but cannot be loader roots.
    if len(header)<20 or header[:4]!=b'\x7fELF' or header[5] not in {1,2}:return False
    return int.from_bytes(header[16:18],'little' if header[5]==1 else 'big') in {2,3}


def runtime_tree_assets(root):
    """Hash bounded exact stdlib sources/data/extensions, excluding generated caches."""
    root=Path(root)
    files={};elf=[];size=0
    for directory,children,names in os.walk(root,followlinks=False):
        children[:]=sorted(name for name in children if name not in {'__pycache__','site-packages','dist-packages'})
        for name in sorted(names):
            if name.endswith(('.pyc','.pyo')): continue
            path=Path(directory)/name
            if len(files)>=_MAX_ASSET_FILES:raise ValueError('runtime asset file budget')
            if path.is_symlink() and not path.exists():
                # Debian's sitecustomize.py points into /etc, deliberately
                # absent in this namespace. Freeze the unavailable link state;
                # never widen mounts to make private configuration executable.
                files[path.relative_to(root).as_posix()]=hashlib.sha256(b'unavailable-symlink:'+os.fsencode(os.readlink(path))).hexdigest()
                continue
            size+=path.stat().st_size
            if len(files)>=_MAX_ASSET_FILES or size>_MAX_ASSET_BYTES:
                raise ValueError('runtime asset budget')
            files[path.relative_to(root).as_posix()]=file_hash(path)
            if _elf(path):elf.append(path)
    if not files:raise ValueError('missing runtime assets')
    return files,elf


def installed_distributions(names):
    """Resolve already-installed material dependencies with actual PEP508 markers."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    pending=list(names);selected={}
    while pending:
        name=canonicalize_name(pending.pop())
        if name in selected: continue
        if len(selected)>=_MAX_DISTRIBUTIONS:raise ValueError('dependency budget')
        distribution=importlib.metadata.distribution(name)
        selected[name]=distribution
        for expression in distribution.requires or ():
            requirement=Requirement(expression)
            if requirement.marker is None or requirement.marker.evaluate({'extra':''}):
                pending.append(requirement.name)
    return selected


def _trusted_output(argv,*,limit=1_048_576):
    # Installed runtime assets are trusted probe roots. Private tmpfs plus
    # inherited FSIZE bounds even a defective probe before bounded reads.
    with tempfile.TemporaryFile(dir='/tmp') as output,tempfile.TemporaryFile(dir='/tmp') as error:
        process=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=output,stderr=error,timeout=10,check=False)
        if output.tell()>limit or error.tell()>65536 or process.returncode:
            raise ValueError('trusted probe refused or exceeded output budget')
        output.seek(0)
        return output.read(limit+1)


def elf_dependency_assets(roots):
    """Union exact loader closure for installed interpreter, extensions and tools."""
    import re
    roots=sorted({str(Path(path).resolve()) for path in roots})
    if not roots or len(roots)>_MAX_ELF_ROOTS:raise ValueError('ELF root budget')
    linked={}
    for offset in range(0,len(roots),24):
        raw=_trusted_output(['/usr/bin/ldd',*roots[offset:offset+24]])
        text=raw.decode('utf-8')
        if 'not found' in text:raise ValueError('unresolved ELF dependency')
        # Multiple-root ldd headings are installed assets too, not input paths.
        for name in re.findall(r'(?:=>\s*)?(/[^\s():]+)',text):
            path=Path(name).resolve()
            if not (str(path).startswith('/usr/') or str(path).startswith('/runtime/')):
                raise ValueError('dependency outside public runtime')
            linked[str(path)]=file_hash(path)
            if len(linked)>_MAX_ASSET_FILES:raise ValueError('ELF dependency budget')
    return linked


def ocr_identity(*,tesseract='/usr/bin/tesseract',tessdata=TESSDATA):
    """Fingerprint selected executable, models and wheel binaries, never names alone."""
    try:
        result={'tesseract.sha256':file_hash(tesseract),'python.sha256':file_hash(sys.executable)}
        result['python-execution-policy']='source-only-v2-prestart-zip-guard-qualified-pin'
        result['python-execution-policy.sha256']=hashlib.sha256(
            (json.dumps(PYTHON_FLAGS)+STARTUP_GUARD+SOURCE_ONLY_START).encode()).hexdigest()
        result['python-pyvenv.sha256']=file_hash(Path(sys.prefix)/'pyvenv.cfg')
        # Fixed argv and bounded files. No document participates in a probe.
        version=_trusted_output([tesseract,'--version'],limit=65536)
        result['tesseract']=version.decode().splitlines()[0]
        for name in ('eng','rus','uzb','uzb_cyrl','osd'):
            result[name+'.sha256']=file_hash(Path(tessdata)/(name+'.traineddata'))
        roots=[Path(sys.executable),Path(tesseract)]
        for name,path in (('startup-shell','/bin/sh'),('startup-readlink','/usr/bin/readlink'),('startup-sha256sum','/usr/bin/sha256sum')):
            result[name+'.sha256']=file_hash(path)
            roots.append(Path(path))
        total_files=0;total_bytes=0
        for name,distribution in sorted(installed_distributions((*NATIVE_PACKAGES,*OCR_PACKAGES,'pydantic_core','packaging')).items()):
            result[name]=distribution.version
            result[name+'.assets.sha256']=distribution_hash(distribution)
            for file in distribution.files or ():
                if str(file).endswith(('.pyc','.pyo')) or '__pycache__' in str(file):continue
                path=Path(distribution.locate_file(file))
                total_files+=1;total_bytes+=path.stat().st_size
                if total_files>_MAX_ASSET_FILES or total_bytes>_MAX_ASSET_BYTES:raise ValueError('package asset budget')
                if _elf(path):roots.append(path)
        stdlib,extensions=runtime_tree_assets(sysconfig.get_path('stdlib'))
        result['python-stdlib.assets.sha256']=hashlib.sha256(json.dumps(stdlib,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        # Tesseract's TSV configuration and PDFium's public font fallback data
        # affect output independently of executable/model/package versions.
        for name,path in (('tesseract-data',tessdata),('fonts','/usr/share/fonts'),('fontconfig','/usr/share/fontconfig')):
            assets,_=runtime_tree_assets(path)
            result[name+'.assets.sha256']=hashlib.sha256(json.dumps(assets,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        roots.extend(extensions)
        linked=elf_dependency_assets(roots)
        result['runtime-elf-libraries.sha256']=hashlib.sha256(json.dumps(linked,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        for path,value in linked.items():
            name=Path(path).name
            if name.startswith('libexpat.so.') or name in {'libmvec.so.1','libdl.so.2','libpthread.so.0','librt.so.1'}:
                result[name+'.sha256']=value
        return result
    except (OSError,ValueError,subprocess.SubprocessError,importlib.metadata.PackageNotFoundError):
        raise DocumentReadError('ocr_unavailable','Configured OCR assets are unavailable') from None


def archive_identity():
    """Freeze standard adapters and fixed tools, independent of native/OCR assets."""
    if __package__:
        from .archives import UNRAR_TOOL
    else:
        from archives import UNRAR_TOOL
    try:
        result={'python.sha256':file_hash(sys.executable),
                'python-pyvenv.sha256':file_hash(Path(sys.prefix)/'pyvenv.cfg'),
                'python-execution-policy':'source-only-v2-prestart-zip-guard-qualified-pin',
                'decoder-policy':'standard-zipfile-rarfile-py7zr-skip-failed-container-v1'}
        result['python-execution-policy.sha256']=hashlib.sha256((json.dumps(PYTHON_FLAGS)+STARTUP_GUARD+SOURCE_ONLY_START).encode()).hexdigest()
        roots=[Path(sys.executable)]
        for name,path in (('startup-shell','/bin/sh'),('startup-readlink','/usr/bin/readlink'),('startup-sha256sum','/usr/bin/sha256sum')):
            result[name+'.sha256']=file_hash(path);roots.append(Path(path))
        selected=['packaging']
        for name in ('rarfile','py7zr'):
            try:importlib.metadata.distribution(name)
            except importlib.metadata.PackageNotFoundError:result[name]='unavailable'
            else:selected.append(name)
        total_files=0;total_bytes=0
        for name,distribution in sorted(installed_distributions(selected).items()):
            result[name]=distribution.version;result[name+'.assets.sha256']=distribution_hash(distribution)
            for file in distribution.files or ():
                if str(file).endswith(('.pyc','.pyo')) or '__pycache__' in str(file):continue
                path=Path(distribution.locate_file(file));total_files+=1;total_bytes+=path.stat().st_size
                if total_files>_MAX_ASSET_FILES or total_bytes>_MAX_ASSET_BYTES:raise ValueError('package asset budget')
                if _elf(path):roots.append(path)
        if Path(UNRAR_TOOL).is_file():
            result['unrar.sha256']=file_hash(UNRAR_TOOL);roots.append(Path(UNRAR_TOOL))
            # Official unrar's version/help command has fixed argv and bounded output.
            result['unrar']=_trusted_output([UNRAR_TOOL],limit=65536).decode('utf-8').strip().splitlines()[0]
        else:result['unrar']='unavailable'
        assets,extensions=runtime_tree_assets(sysconfig.get_path('stdlib'))
        result['python-stdlib.assets.sha256']=hashlib.sha256(json.dumps(assets,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        roots.extend(extensions)
        linked=elf_dependency_assets(roots)
        result['runtime-elf-libraries.sha256']=hashlib.sha256(json.dumps(linked,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return result
    except (OSError,ValueError,AttributeError,subprocess.SubprocessError,importlib.metadata.PackageNotFoundError):
        raise DocumentReadError('archive_unavailable','Configured archive adapter assets are unavailable') from None
