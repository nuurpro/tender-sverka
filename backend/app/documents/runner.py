"""Fail-closed document subprocess isolation; never imports native readers on host."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import TYPE_CHECKING, Callable
from .bootstrap import PYTHON_FLAGS, SOURCE_ONLY_START, SOURCE_ONLY_DISPATCH, STARTUP_GUARD

if TYPE_CHECKING:
    from app.config import Settings
    from .schemas import NormalizedDocument, ParserProfile


class DocumentRunnerError(RuntimeError):
    def __init__(self, code: str, message: str, *, returncode: int | None = None):
        self.code = code
        self.returncode = returncode
        super().__init__(message)


# The trusted launcher lives in argv, outside the parser's mount namespace. The
# one-byte ACK guarantees no parser starts before its process group is known.
_SUPERVISOR = SOURCE_ONLY_START + """import errno,json,os,resource,signal,sys
limits=json.loads(sys.argv[1])
try:
 os.setsid()
except PermissionError as exc:
 if exc.errno != errno.EPERM or not (os.getpid()==os.getpgrp()==os.getsid(0)):
  raise
# WSL may already give this exec'd trusted launcher its own session. Keeping
# it is safe only when this exact PID owns both session and process group.
assert os.getpid()==os.getpgrp()==os.getsid(0)
for name,value in limits.items():
 resource.setrlimit(getattr(resource,name),(value,value))
signal.alarm(5)
os.write(2,('DOCUMENT_RUNNER_PID:'+str(os.getpid())+'\\n').encode())
if os.read(0,1)!=b'A': sys.exit(75)
signal.alarm(0)
os.execv(sys.argv[2],sys.argv[2:])
"""
_PID_PREFIX = b"DOCUMENT_RUNNER_PID:"
_STDERR_LIMIT = 65_536
_CODE_FILES = ("__init__.py", "entrypoint.py", "readers.py", "schemas.py", "images.py", "toolchain.py", "bootstrap.py", "archives.py", "archive_entrypoint.py")

# Apply the parser's process cap after Bubblewrap creates its trusted namespaces.
# Linux charges namespace setup to ancestor user counters; a low pre-namespace
# NPROC cap can prevent isolation from starting even though the parser is small.
_SANDBOX_START = SOURCE_ONLY_START + """import os,resource,sys
limit=int(sys.argv[1])
resource.setrlimit(resource.RLIMIT_NPROC,(limit,limit))
os.execv(sys.argv[2],sys.argv[2:])
"""


def parser_code_hash() -> str:
    """Hash only the pure parser source, in the same canonical form as staging."""
    base = Path(__file__).parent
    hashes = {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in _CODE_FILES}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _wsl_prefix(settings: Settings) -> list[str]:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return [str(Path(root) / "System32" / "wsl.exe"), "--distribution", settings.document_wsl_distribution, "--exec"]


def _build_command(settings: Settings, *, probe: bool = False, archive: bool = False) -> tuple[list[str], bool]:
    mode = settings.document_runner_mode
    if mode == "disabled":
        raise DocumentRunnerError("runner_disabled", "Document isolation is disabled")
    is_wsl = mode == "wsl-bubblewrap"
    if not is_wsl and sys.platform != "linux":
        raise DocumentRunnerError("runner_unavailable", "Bubblewrap requires Linux")
    runtime_root = str(PurePosixPath(settings.document_runtime_python).parent.parent)
    runtime_python = "/runtime/bin/" + PurePosixPath(settings.document_runtime_python).name
    command = ["/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
               "--cap-drop", "ALL", "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib",
               "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/bin", "/bin",
               "--proc", "/proc", "--dev", "/dev", "--size", str(getattr(settings,"document_runner_tmp_bytes",settings.document_runner_file_bytes)), "--tmpfs", "/tmp",
               "--ro-bind", runtime_root, "/runtime", "--ro-bind", settings.document_parser_code_path,
               "/parser", "--chdir", "/tmp", "--setenv", "HOME", "/tmp", "--setenv", "TMPDIR", "/tmp",
               "--setenv", "LANG", "C.UTF-8", "--setenv", "OMP_THREAD_LIMIT", "1",
               "--setenv", "OPENBLAS_NUM_THREADS", "1", "--", "/bin/sh", "-c", STARTUP_GUARD,
               "document-startup-guard", "/runtime", settings.document_runtime_python_sha256,
               runtime_python, *PYTHON_FLAGS, "-c", _SANDBOX_START,
               str(settings.document_runner_process_limit), runtime_python, *PYTHON_FLAGS,
               "-c", SOURCE_ONLY_START + SOURCE_ONLY_DISPATCH]
    if probe:
        command += ["-c", _PROBE, 'ocr' if settings.document_ocr_mode=='tesseract' else 'native']
    else:
        command += ["/parser/archive_entrypoint.py" if archive else "/parser/entrypoint.py"]
    limits = {"RLIMIT_CPU": settings.document_runner_cpu_seconds,
              "RLIMIT_AS": settings.document_runner_memory_bytes,
              "RLIMIT_NOFILE": settings.document_runner_file_limit,
              "RLIMIT_FSIZE": settings.document_runner_file_bytes, "RLIMIT_CORE": 0}
    prefix = _wsl_prefix(settings) if is_wsl else []
    return prefix + ["/bin/sh", "-c", STARTUP_GUARD, "document-startup-guard",
                     runtime_root, settings.document_runtime_python_sha256,
                     "/usr/bin/python3", *PYTHON_FLAGS, "-c", _SUPERVISOR,
                     json.dumps(limits, separators=(",", ":"))] + command, is_wsl


_PROBE = """import os,json,socket,hashlib,pathlib,importlib.metadata,sys
assert os.getuid() != 0
assert not any(os.path.exists(p) for p in ['/home','/mnt','/etc','/root'])
assert not any(k in os.environ for k in ['DATABASE_URL','APP_SESSION_SECRET','S3_SECRET_KEY'])
s=socket.socket(); s.settimeout(.2)
try:
 s.connect(('1.1.1.1',443))
 raise AssertionError('network unexpectedly available')
except OSError: pass
finally: s.close()
names=['__init__.py','entrypoint.py','readers.py','schemas.py','images.py','toolchain.py','bootstrap.py','archives.py','archive_entrypoint.py']
assert {p.name for p in pathlib.Path('/parser').iterdir()} == set(names)
assert all(pathlib.Path('/parser',n).is_file() and not pathlib.Path('/parser',n).is_symlink() for n in names)
hashes={n:hashlib.sha256(pathlib.Path('/parser',n).read_bytes()).hexdigest() for n in names}
versions={n:importlib.metadata.version(n) for n in ['pdfplumber','python-docx','openpyxl','pydantic','lxml','Pillow','pdfminer.six','pypdfium2']}
versions['python']=sys.version
result={'isolated':True,'runtime_versions':versions,'code_hash':hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
if sys.argv[1]=='ocr':
 sys.path.insert(0,'/parser')
 from toolchain import ocr_identity
 result['ocr_toolchain']=ocr_identity()
print(json.dumps(result))
"""


def _launcher_env() -> dict[str, str]:
    # WSL needs Windows system lookup, never application configuration.
    return {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows")} if os.name == "nt" else {}


def _kill_owned(process: subprocess.Popen, linux_pid: int | None, is_wsl: bool, settings: Settings):
    if is_wsl and linux_pid is not None:
        try:
            subprocess.run(_wsl_prefix(settings) + ["/usr/bin/kill", "-KILL", "--", f"-{linux_pid}"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=_launcher_env(), cwd=tempfile.gettempdir(), timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass  # The startup alarm / bwrap parent-death signal remain active.
    if os.name != "nt":
        try:
            os.killpg(linux_pid or process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        raise DocumentRunnerError("cleanup_failed", "Could not stop isolated document process") from None


def _stream_process(command: list[str], request: bytes, settings: Settings,
                    cancelled: Callable[[], bool] | None, is_wsl: bool, *, handshake: bool = False) -> bytes:
    if cancelled and cancelled():
        raise DocumentRunnerError("cancelled", "Document parsing cancelled")
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=_launcher_env(), cwd=tempfile.gettempdir(), close_fds=True,
                                   start_new_session=os.name != "nt" and not handshake, bufsize=0)
    except OSError:
        raise DocumentRunnerError("runner_unavailable", "Document isolation executable is unavailable") from None
    output = bytearray()
    exceeded = threading.Event()
    ready = threading.Event()
    linux_pid: list[int] = []
    read_errors: list[bool] = []

    def read_pipe(pipe, limit, collect=False):
        total = 0
        try:
            if handshake and not collect:
                line = pipe.readline(80)
                if line.startswith(_PID_PREFIX) and line.endswith(b"\n"):
                    try:
                        pid = int(line[len(_PID_PREFIX):])
                        if pid > 1:
                            linux_pid.append(pid)
                            ready.set()
                    except ValueError:
                        pass
                total += len(line)
            while chunk := pipe.read(4096):
                total += len(chunk)
                if total > limit:
                    exceeded.set()
                if collect and len(output) < limit:
                    output.extend(chunk[:limit - len(output)])
        except (OSError, ValueError):
            read_errors.append(True)
        finally:
            pipe.close()

    def write_pipe():
        try:
            if handshake:
                while not ready.wait(.02):
                    if process.poll() is not None or stop.is_set():
                        return
                if stop.is_set():
                    return
                process.stdin.write(b"A")
            view = memoryview(request)
            for offset in range(0, len(view), 65536):
                if stop.is_set():
                    return
                block = view[offset:offset + 65536]
                while block:
                    written = process.stdin.write(block)
                    block = block[written:]
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            process.stdin.close()

    stop = threading.Event()
    threads = [threading.Thread(target=read_pipe, args=(process.stdout, settings.document_runner_output_bytes, True), daemon=True),
               threading.Thread(target=read_pipe, args=(process.stderr, _STDERR_LIMIT), daemon=True),
               threading.Thread(target=write_pipe, daemon=True)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + settings.document_runner_timeout_seconds
    failure = None
    try:
        while True:
            if exceeded.is_set():
                failure = DocumentRunnerError("output_limit", "Document parser output exceeded its limit")
            elif cancelled and cancelled():
                failure = DocumentRunnerError("cancelled", "Document parsing cancelled")
            elif time.monotonic() >= deadline:
                failure = DocumentRunnerError("timeout", "Document parser exceeded its wall-time limit")
            if failure or (process.poll() is not None and not any(t.is_alive() for t in threads)):
                break
            time.sleep(.01)
    finally:
        stop.set()
        # Always terminate the owned namespace, including inherited pipes from a
        # surviving descendant. Never wait on untrusted streams indefinitely.
        if failure or process.poll() is None:
            _kill_owned(process, linux_pid[0] if linux_pid else None, is_wsl, settings)
        for thread in threads:
            thread.join(timeout=2)
        if any(thread.is_alive() for thread in threads):
            raise DocumentRunnerError("cleanup_failed", "Could not drain isolated document process")
    if failure:
        raise failure
    if handshake and not ready.is_set():
        raise DocumentRunnerError("runner_unavailable", "Document isolation failed to start")
    if process.returncode or read_errors:
        raise DocumentRunnerError("parser_failed", "Isolated document parser failed", returncode=process.returncode)
    return bytes(output)


def ensure_runner_available(settings: Settings) -> dict:
    """Run a real sandbox probe and verify the staged pure-code fingerprint."""
    command, is_wsl = _build_command(settings, probe=True)
    try:
        result = json.loads(_stream_process(command, b"", settings, None, is_wsl, handshake=True))
        if result.get("isolated") is not True or result.get("code_hash") != parser_code_hash():
            raise ValueError("probe mismatch")
        command, is_wsl = _build_command(settings)
        probe_request=b'{"probe":true,"ocr":true}' if settings.document_ocr_mode=='tesseract' else b'{"probe":true}'
        parser_probe = json.loads(_stream_process(command, probe_request, settings, None, is_wsl, handshake=True))
        if parser_probe != result:
            raise ValueError("parser probe mismatch")
        return result
    except DocumentRunnerError:
        raise
    except (ValueError, OSError):
        raise DocumentRunnerError("runner_unavailable", "Document isolation probe failed") from None


def run_document(content: bytes, filename: str, mime_type: str, profile: ParserProfile,
                 settings: Settings, cancelled: Callable[[], bool] | None = None) -> NormalizedDocument:
    if settings.document_runner_mode == "disabled":
        raise DocumentRunnerError("runner_disabled", "Document isolation is disabled")
    if len(content) > settings.max_package_bytes or len(filename.encode()) > 2048 or len(mime_type.encode()) > 256:
        raise DocumentRunnerError("input_limit", "Document parser input exceeded its limit")
    from .schemas import OCRProfile, OCRBlock, CoverageV2, parse_document
    deadline=time.monotonic()+profile.limits.max_document_seconds if isinstance(profile,OCRProfile) else None
    execution_settings=settings
    if deadline is not None:
        execution_settings=settings.model_copy(update={'document_runner_timeout_seconds':min(settings.document_runner_timeout_seconds,profile.limits.max_document_seconds)})
    profile_data = profile.model_dump(mode="json")
    if len(json.dumps(profile_data).encode()) > 16_384:
        raise DocumentRunnerError("input_limit", "Document parser profile exceeded its limit")
    request = json.dumps({"filename": filename, "mime_type": mime_type,
                          "content_base64": base64.b64encode(content).decode("ascii"),
                          "profile": profile_data}, separators=(",", ":"), ensure_ascii=True).encode()
    if len(request) > (settings.max_package_bytes + 2) // 3 * 4 + 32_768:
        raise DocumentRunnerError("input_limit", "Document parser request exceeded its limit")
    command, is_wsl = _build_command(execution_settings)
    output = _stream_process(command, request, execution_settings, cancelled, is_wsl, handshake=True)
    if deadline is not None and time.monotonic()>=deadline:
        raise DocumentRunnerError('timeout','Document exceeded cumulative time policy')
    try:
        if isinstance(profile,OCRProfile):
            result=json.loads(output)
            if set(result)!={'document','manifest','cpu_seconds'}:
                raise ValueError('OCR manifest protocol')
            cpu_total=_cpu_usage(result['cpu_seconds'])
            if cpu_total>profile.limits.max_document_cpu_seconds:
                raise DocumentRunnerError('cpu_limit','Document CPU consumption exceeded frozen policy')
            document=parse_document(result['document'])
            manifest=result['manifest']
            if manifest:
                _validate_manifest(manifest,profile,document)
                blocks=list(document.blocks); coverage=list(document.coverage)
                estimate=len(document.model_dump_json().encode())
                characters=sum(len(b.original_text) for b in blocks)
                base_request=json.loads(request)
                for frame in manifest['frames']:
                    remaining=deadline-time.monotonic()
                    if remaining<.1:
                        raise DocumentRunnerError('timeout','OCR document exceeded cumulative time policy')
                    chunk_request=json.dumps({**base_request,'frame':frame['frame']},separators=(',',':'),ensure_ascii=True).encode()
                    chunk_settings=settings.model_copy(update={'document_runner_timeout_seconds':min(settings.document_runner_timeout_seconds,remaining)})
                    cpu_remaining=profile.limits.max_document_cpu_seconds-cpu_total
                    if cpu_remaining<.1:
                        raise DocumentRunnerError('cpu_limit','Document CPU consumption exceeded frozen policy')
                    import math
                    chunk_settings=chunk_settings.model_copy(update={'document_runner_cpu_seconds':min(settings.document_runner_cpu_seconds,max(1,math.ceil(cpu_remaining)))})
                    command,is_wsl=_build_command(chunk_settings)
                    chunk_output=_stream_process(command,chunk_request,chunk_settings,cancelled,is_wsl,handshake=True)
                    if time.monotonic()>=deadline:
                        raise DocumentRunnerError('timeout','OCR document exceeded cumulative time policy')
                    chunk=json.loads(chunk_output)
                    if set(chunk)!={'frame','blocks','coverage','cpu_seconds'} or chunk['frame']!=frame['frame']:
                        raise ValueError('OCR chunk protocol')
                    cpu_total+=_cpu_usage(chunk['cpu_seconds'])
                    if cpu_total>profile.limits.max_document_cpu_seconds:
                        raise DocumentRunnerError('cpu_limit','Document CPU consumption exceeded frozen policy')
                    estimate+=len(chunk_output)
                    if estimate>profile.limits.max_output_bytes:
                        raise DocumentRunnerError('output_limit','Cumulative OCR output exceeded policy')
                    words=tuple(OCRBlock.model_validate_json(json.dumps(b)) for b in chunk['blocks'])
                    entries=tuple(CoverageV2.model_validate_json(json.dumps(c)) for c in chunk['coverage'])
                    if any(b.anchor.frame!=frame['frame'] or b.anchor.source_sha256!=document.content_sha256 for b in words):
                        raise ValueError('OCR frame/source mismatch')
                    characters+=sum(len(b.transcription_text) for b in words)
                    if len(blocks)+len(words)>profile.limits.max_blocks or len(coverage)+len(entries)>profile.limits.max_blocks*4 or characters>profile.limits.max_text_chars:
                        raise DocumentRunnerError('output_limit','Cumulative OCR evidence exceeded policy')
                    blocks.extend(words);coverage.extend(entries)
                document=parse_document({**document.model_dump(mode='json'),'blocks':[b.model_dump(mode='json') for b in blocks],
                    'coverage':[c.model_dump(mode='json') for c in coverage],'status':'partial'})
                if len(document.model_dump_json().encode())>profile.limits.max_output_bytes:
                    raise DocumentRunnerError('output_limit','Cumulative OCR output exceeded policy')
        else:
            document = parse_document(output)
        if document.content_sha256 != hashlib.sha256(content).hexdigest() or document.filename != filename or document.mime_type != mime_type or document.profile != profile:
            raise ValueError("document identity mismatch")
        return document
    except ValueError:
        raise DocumentRunnerError("invalid_output", "Isolated document parser returned invalid evidence") from None


def _validate_manifest(manifest,profile,document):
    if not isinstance(manifest,dict) or set(manifest)!={'format','frames','total_pixels'} or manifest['format']!=document.format:
        raise ValueError('Raster manifest format')
    frames=manifest['frames']
    if not isinstance(frames,list) or not 0<len(frames)<=profile.limits.max_pages or document.physical_pages!=len(frames):
        raise ValueError('Raster manifest page count')
    total=0
    for index,frame in enumerate(frames,1):
        if not isinstance(frame,dict) or frame.get('frame')!=index:
            raise ValueError('Raster manifest frame order')
        dimensions=frame.get('render_size')
        if not isinstance(dimensions,list) or len(dimensions)!=2 or any(type(n)is not int or n<=0 or n>profile.limits.max_dimension for n in dimensions):
            raise ValueError('Raster manifest dimensions')
        pixels=dimensions[0]*dimensions[1]
        if pixels>profile.limits.max_frame_pixels:
            raise DocumentRunnerError('pixel_limit','Raster frame exceeded policy')
        total+=pixels
        if total>profile.limits.max_total_pixels:
            raise DocumentRunnerError('pixel_limit','Cumulative raster pixels exceeded policy')
    if type(manifest['total_pixels'])is not int or manifest['total_pixels']!=total:
        raise ValueError('Raster manifest cumulative count')


def _cpu_usage(value):
    import math
    if type(value)not in {int,float} or not math.isfinite(value) or value<0:
        raise ValueError('Invalid measured CPU consumption')
    return float(value)
