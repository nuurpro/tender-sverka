"""Stage only pure parser code into the owned Linux development runtime.

No application configuration, credentials or uploaded documents are copied.
Old code versions are preserved; publishing the selected version is atomic.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CODE_FILES = ("__init__.py", "entrypoint.py", "readers.py", "schemas.py")
MAX_CODE_BYTES = 262_144


def stage_payload(source=None):
    source = source or ROOT / "backend" / "app" / "documents"
    if source.is_symlink() or any(not (source / name).is_file() or (source / name).is_symlink() for name in CODE_FILES):
        raise ValueError("A complete regular pure-parser source set is required")
    files, hashes = {}, {}
    for name in CODE_FILES:
        data = (source / name).read_bytes()
        if not 0 < len(data) <= MAX_CODE_BYTES:
            raise ValueError("Pure parser code must be bounded")
        files[name] = base64.b64encode(data).decode("ascii")
        hashes[name] = hashlib.sha256(data).hexdigest()
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"files": files, "code_hash": digest}


LINUX_STAGE = r'''
import base64,hashlib,json,os,pathlib,stat,sys,tempfile,uuid
names={'__init__.py','entrypoint.py','readers.py','schemas.py'}
if os.getuid()==0: raise RuntimeError('Use the dedicated non-root development user')
request=json.loads(sys.stdin.buffer.read(1500000))
if set(request)!= {'files','code_hash'} or set(request['files'])!=names: raise ValueError('Invalid source set')
files={name:base64.b64decode(value,validate=True) for name,value in request['files'].items()}
if any(not 0<len(value)<=262144 for value in files.values()): raise ValueError('Code limit')
hashes={name:hashlib.sha256(value).hexdigest() for name,value in files.items()}
digest=hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest()
if request['code_hash']!=digest: raise ValueError('Code identity mismatch')
home=pathlib.Path.home()
root=home/'.local/share/tender-sverka/document-parser'
runtime=home/'.local/share/tender-sverka/document-runtime/bin/python'
if not runtime.is_file(): raise RuntimeError('Prepare the dedicated parser virtual environment first')
for directory in (home/'.local',home/'.local/share',home/'.local/share/tender-sverka',root,root/'versions',root/'app'):
 if directory.is_symlink(): raise ValueError('Unsafe staging parent')
 directory.mkdir(mode=0o700,exist_ok=True)
 if not directory.is_dir(): raise ValueError('Unsafe staging parent')
target=root/'versions'/digest
if target.exists() or target.is_symlink():
 if target.is_symlink() or not target.is_dir() or {p.name for p in target.iterdir()}!=names: raise ValueError('Existing version is not owned parser code')
 for name,value in files.items():
  path=target/name
  if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.read_bytes()!=value: raise ValueError('Existing version was modified')
else:
 temporary=pathlib.Path(tempfile.mkdtemp(prefix='.stage-',dir=root/'versions'))
 for name,value in files.items():
  path=temporary/name
  with path.open('xb') as stream: stream.write(value)
  path.chmod(0o400)
 os.rename(temporary,target)
selected=root/'app/documents'
if selected.exists() or selected.is_symlink():
 if not selected.is_symlink(): raise ValueError('Refusing to replace an existing non-staged directory')
 previous=selected.resolve(strict=True)
 if previous.parent!=root/'versions': raise ValueError('Refusing to replace a foreign stage link')
link=root/'app'/('.documents-'+uuid.uuid4().hex)
link.symlink_to(target,target_is_directory=True)
os.replace(link,selected)
print(json.dumps({'code_hash':digest,'parser_code_path':str(selected),'runtime_python':str(runtime)}))
'''


def stage_parser(*, distribution="Ubuntu", source=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,79}", distribution):
        raise ValueError("A named WSL distribution is required")
    payload = stage_payload(source)
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"}}
    command = [str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/wsl.exe"),
               "--distribution", distribution, "--exec", "/usr/bin/python3", "-I", "-c", LINUX_STAGE]
    try:
        result = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True,
                                timeout=30, check=True, env=environment)
        response = json.loads(result.stdout)
        if response["code_hash"] != payload["code_hash"]:
            raise ValueError("Published code identity mismatch")
        return response
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        raise RuntimeError("Parser staging failed; check the owned Linux runtime and source set") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", default="Ubuntu")
    options = parser.parse_args()
    try:
        print(json.dumps(stage_parser(distribution=options.distribution), ensure_ascii=False))
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
