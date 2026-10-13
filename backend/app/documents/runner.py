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

if TYPE_CHECKING:
    from app.config import Settings
    from .schemas import NormalizedDocument, ParserProfile


class DocumentRunnerError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


# The trusted launcher lives in argv, outside the parser's mount namespace. The
# one-byte ACK guarantees no parser starts before its process group is known.
_SUPERVISOR = """import errno,json,os,resource,signal,sys
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
_CODE_FILES = ("__init__.py", "entrypoint.py", "readers.py", "schemas.py")

# Apply the parser's process cap after Bubblewrap creates its trusted namespaces.
# Linux charges namespace setup to ancestor user counters; a low pre-namespace
# NPROC cap can prevent isolation from starting even though the parser is small.
_SANDBOX_START = """import os,resource,sys
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


def _build_command(settings: Settings, *, probe: bool = False) -> tuple[list[str], bool]:
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
               "--proc", "/proc", "--dev", "/dev", "--size", str(settings.document_runner_file_bytes), "--tmpfs", "/tmp",
               "--ro-bind", runtime_root, "/runtime", "--ro-bind", settings.document_parser_code_path,
               "/parser", "--chdir", "/tmp", "--setenv", "HOME", "/tmp", "--setenv", "TMPDIR", "/tmp",
               "--setenv", "LANG", "C.UTF-8", "--", runtime_python, "-I", "-c", _SANDBOX_START,
               str(settings.document_runner_process_limit), runtime_python, "-I"]
    if probe:
        command += ["-c", _PROBE]
    else:
        command += ["/parser/entrypoint.py"]
    limits = {"RLIMIT_CPU": settings.document_runner_cpu_seconds,
              "RLIMIT_AS": settings.document_runner_memory_bytes,
              "RLIMIT_NOFILE": settings.document_runner_file_limit,
              "RLIMIT_FSIZE": settings.document_runner_file_bytes, "RLIMIT_CORE": 0}
    prefix = _wsl_prefix(settings) if is_wsl else []
    return prefix + ["/usr/bin/python3", "-I", "-c", _SUPERVISOR,
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
names=['__init__.py','entrypoint.py','readers.py','schemas.py']
assert {p.name for p in pathlib.Path('/parser').iterdir()} == set(names)
assert all(pathlib.Path('/parser',n).is_file() and not pathlib.Path('/parser',n).is_symlink() for n in names)
hashes={n:hashlib.sha256(pathlib.Path('/parser',n).read_bytes()).hexdigest() for n in names}
versions={n:importlib.metadata.version(n) for n in ['pdfplumber','python-docx','openpyxl','pydantic','lxml','Pillow','pdfminer.six','pypdfium2']}
versions['python']=sys.version
print(json.dumps({'isolated':True,'runtime_versions':versions,'code_hash':hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest()}))
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
        raise DocumentRunnerError("parser_failed", "Isolated document parser failed")
    return bytes(output)


def ensure_runner_available(settings: Settings) -> dict:
    """Run a real sandbox probe and verify the staged pure-code fingerprint."""
    command, is_wsl = _build_command(settings, probe=True)
    try:
        result = json.loads(_stream_process(command, b"", settings, None, is_wsl, handshake=True))
        if result.get("isolated") is not True or result.get("code_hash") != parser_code_hash():
            raise ValueError("probe mismatch")
        command, is_wsl = _build_command(settings)
        parser_probe = json.loads(_stream_process(command, b'{"probe":true}', settings, None, is_wsl, handshake=True))
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
    profile_data = profile.model_dump(mode="json")
    if len(json.dumps(profile_data).encode()) > 16_384:
        raise DocumentRunnerError("input_limit", "Document parser profile exceeded its limit")
    request = json.dumps({"filename": filename, "mime_type": mime_type,
                          "content_base64": base64.b64encode(content).decode("ascii"),
                          "profile": profile_data}, separators=(",", ":"), ensure_ascii=True).encode()
    if len(request) > (settings.max_package_bytes + 2) // 3 * 4 + 32_768:
        raise DocumentRunnerError("input_limit", "Document parser request exceeded its limit")
    command, is_wsl = _build_command(settings)
    output = _stream_process(command, request, settings, cancelled, is_wsl, handshake=True)
    from .schemas import NormalizedDocument
    try:
        document = NormalizedDocument.model_validate_json(output)
        if document.content_sha256 != hashlib.sha256(content).hexdigest() or document.filename != filename or document.mime_type != mime_type or document.profile != profile:
            raise ValueError("document identity mismatch")
        return document
    except ValueError:
        raise DocumentRunnerError("invalid_output", "Isolated document parser returned invalid evidence") from None
