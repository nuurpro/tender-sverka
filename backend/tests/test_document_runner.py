"""Isolation command contracts and real subprocess stream supervision."""
import sys
import json
import os
import subprocess
import threading
import time
import uuid

import pytest
from pydantic import ValidationError
from test_full_settings import settings


def runner():
    from app.documents import runner as module
    return module


def test_runner_is_disabled_by_default_and_never_launches(monkeypatch):
    module = runner()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: pytest.fail("disabled runner launched"))
    with pytest.raises(module.DocumentRunnerError) as error:
        module.run_document(b"hello", "a.pdf", "application/pdf", None, settings())
    assert error.value.code == "runner_disabled"


def test_settings_reject_host_paths_and_unbounded_limits():
    assert settings().document_runner_mode == "disabled"
    for change in [dict(document_runtime_python="C:/Python/python.exe"),
                   dict(document_parser_code_path="/"), dict(document_runner_mode="host"),
                   dict(document_runner_output_bytes=100_000_000),
                   dict(document_runner_process_limit=1000)]:
        with pytest.raises(ValidationError):
            settings(**change)


def test_document_runner_environment_aliases(monkeypatch):
    monkeypatch.setenv("DOCUMENT_RUNNER_MODE", "wsl-bubblewrap")
    monkeypatch.setenv("DOCUMENT_WSL_DISTRIBUTION", "Ubuntu-Test")
    assert settings().document_runner_mode == "wsl-bubblewrap"
    assert settings().document_wsl_distribution == "Ubuntu-Test"


def test_runtime_pin_is_public_explicit_and_validated(monkeypatch):
    assert settings().document_runtime_python_sha256==''
    monkeypatch.setenv('DOCUMENT_RUNTIME_PYTHON_SHA256','a'*64)
    assert settings().document_runtime_python_sha256=='a'*64
    monkeypatch.delenv('DOCUMENT_RUNTIME_PYTHON_SHA256')
    monkeypatch.setenv('APP_DOCUMENT_RUNTIME_PYTHON_SHA256','b'*64)
    assert settings().document_runtime_python_sha256=='b'*64
    for value in ['invalid','c'*63,'C'*64]:
        with pytest.raises(ValidationError):settings(document_runtime_python_sha256=value)


def test_bubblewrap_mounts_only_public_runtime_and_parser(monkeypatch):
    module = runner()
    monkeypatch.setattr(module.sys, "platform", "linux")
    command, is_wsl = module._build_command(settings(document_runner_mode="bubblewrap"))
    assert not is_wsl
    encoded = " ".join(command)
    assert "--unshare-all" in encoded and "--clearenv" in encoded
    assert "--die-with-parent" in encoded and "--new-session" in encoded
    assert "--ro-bind /usr /usr" in encoded
    assert "--proc /proc" in encoded and "--dev /dev" in encoded
    assert "--tmpfs /tmp" in encoded
    assert f"--size {settings().document_runner_file_bytes} --tmpfs /tmp" in encoded
    assert " /parser " in encoded and " /runtime " in encoded
    assert "--ro-bind / /" not in encoded and "--share-net" not in encoded
    assert "Broker.uz" not in encoded and "/etc" not in encoded
    assert command[-1] == "/parser/entrypoint.py"
    assert command.count('pycache_prefix=/dev/null') == 3
    assert command.count('-S') == 3 and command.count('-B') == 3
    assert module.SOURCE_ONLY_START in command[-2]
    assert command.count(module.STARTUP_GUARD)==2
    assert command.index(module.STARTUP_GUARD)<command.index('/usr/bin/python3')
    inner=command.index('--')
    assert command[inner+1:inner+3]==['/bin/sh','-c']


def test_probe_records_material_dependencies_and_python_build():
    module = runner()
    for name in ('pdfminer.six', 'Pillow', 'lxml', 'pypdfium2'):
        assert name in module._PROBE
    assert 'sys.version' in module._PROBE


def test_wsl_uses_explicit_distribution_without_shell(monkeypatch):
    module = runner()
    command, is_wsl = module._build_command(settings(document_runner_mode="wsl-bubblewrap"))
    assert is_wsl
    assert command[1:4] == ["--distribution", "Ubuntu", "--exec"]
    assert "/usr/bin/python3" in command and "/usr/bin/bwrap" in command
    assert "bash" not in command and "-lc" not in command


def launch_script(source):
    return [sys.executable, "-I", "-c", source]


def test_streams_stdin_without_inherited_secret_environment(monkeypatch):
    module = runner()
    monkeypatch.setenv("DATABASE_URL", "do-not-inherit")
    command = launch_script("import sys,os; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data + str('DATABASE_URL' in os.environ).encode())")
    assert module._stream_process(command, b"request", settings(), None, False) == b"requestFalse"


def test_stdout_limit_stops_infinite_stream_before_communicate(monkeypatch):
    module = runner()
    monkeypatch.setattr(module.subprocess.Popen, "communicate", lambda *a, **k: pytest.fail("unbounded communicate used"))
    command = launch_script("import sys; sys.stdin.buffer.read();\nwhile True: sys.stdout.buffer.write(b'x'*4096); sys.stdout.buffer.flush()")
    before = time.monotonic()
    with pytest.raises(module.DocumentRunnerError) as error:
        module._stream_process(command, b"", settings(document_runner_output_bytes=8192), None, False)
    assert error.value.code == "output_limit"
    assert time.monotonic() - before < 4


def test_stderr_limit_stops_process_without_leaking_diagnostics():
    module = runner()
    command = launch_script("import sys; sys.stdin.buffer.read(); sys.stderr.buffer.write(b'secret'*20000); sys.stderr.flush(); import time; time.sleep(10)")
    with pytest.raises(module.DocumentRunnerError) as error:
        module._stream_process(command, b"", settings(), None, False)
    assert error.value.code == "output_limit"
    assert "secret" not in str(error.value)


def test_wall_timeout_and_cancellation_stop_owned_process():
    module = runner()
    command = launch_script("import sys,time; sys.stdin.buffer.read(); time.sleep(30)")
    with pytest.raises(module.DocumentRunnerError) as error:
        module._stream_process(command, b"", settings(document_runner_timeout_seconds=0.15), None, False)
    assert error.value.code == "timeout"
    cancelled = threading.Event()
    timer = threading.Timer(0.1, cancelled.set)
    timer.start()
    try:
        with pytest.raises(module.DocumentRunnerError) as error:
            module._stream_process(command, b"", settings(), cancelled.is_set, False)
        assert error.value.code == "cancelled"
    finally:
        timer.cancel()


def test_nonzero_exit_returns_safe_error():
    module = runner()
    command = launch_script("import sys; sys.stderr.write('PRIVATE SECRET'); sys.exit(7)")
    with pytest.raises(module.DocumentRunnerError) as error:
        module._stream_process(command, b"", settings(), None, False)
    assert error.value.code == "parser_failed" and "PRIVATE" not in str(error.value)


def test_oversized_input_rejected_before_launch(monkeypatch):
    module = runner()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: pytest.fail("oversized input launched"))
    with pytest.raises(module.DocumentRunnerError) as error:
        module.run_document(b"xx", "a.pdf", "application/pdf", None, settings(document_runner_mode="bubblewrap", max_package_bytes=1))
    assert error.value.code == "input_limit"


def test_readiness_requires_both_sandbox_and_parser_probes(monkeypatch):
    module = runner()
    result = {"isolated": True, "code_hash": "a" * 64, "runtime_versions": {"pydantic": "2"}}
    monkeypatch.setattr(module, "parser_code_hash", lambda: "a" * 64)
    probes = []
    def stream(command, request, *args, **kwargs):
        probes.append(request)
        return json.dumps(result).encode()
    monkeypatch.setattr(module, "_stream_process", stream)
    assert module.ensure_runner_available(settings(document_runner_mode="wsl-bubblewrap")) == result
    assert probes == [b"", b'{"probe":true}']
    result["code_hash"] = "b" * 64
    with pytest.raises(module.DocumentRunnerError) as error:
        module.ensure_runner_available(settings(document_runner_mode="wsl-bubblewrap"))
    assert error.value.code == "runner_unavailable"


real_wsl = pytest.mark.skipif(os.environ.get("DOCUMENT_RUNNER_INTEGRATION") != "1",
                             reason="requires dedicated Ubuntu parser stage and runtime")


@real_wsl
@pytest.mark.parametrize('format', ['pdf', 'xlsx', 'pdf-af', 'xlsx-row'])
def test_real_wsl_catalog_and_cell_unknown_content_remain_pending(format):
    from app.documents.schemas import ParserProfile
    from test_document_readers import pdf_fixture, unknown_xlsx_fixture, unknown_xlsx_container_fixture
    module = runner()
    changes = {'document_runner_mode': 'wsl-bubblewrap'}
    for field in ('document_runner_mode', 'document_runtime_python', 'document_runtime_python_sha256', 'document_parser_code_path', 'document_wsl_distribution'):
        if value := os.environ.get('WORKSPACE_TEST_'+field.upper()):
            changes[field] = value
    config = settings(**changes)
    identity = module.ensure_runner_available(config)
    profile = ParserProfile(code_hash=identity['code_hash'], runtime_versions=identity['runtime_versions'])
    content = (pdf_fixture(embedded='kids') if format == 'pdf' else pdf_fixture(embedded='af') if format == 'pdf-af'
               else unknown_xlsx_fixture('inline') if format == 'xlsx' else unknown_xlsx_container_fixture('row'))
    result = module.run_document(content, 'evidence.'+format.split('-')[0], '', profile, config)
    assert result.status == 'partial' and not result.analysis_allowed
    if format.startswith('pdf'):
        assert result.blocks[0].original_text == 'Native evidence'
        origin = '/Kids[0]' if format == 'pdf' else '/AF[0]'
        assert any(c.kind == 'embedded' and 'hidden.txt' in c.anchor.part and origin in c.anchor.part
                   and c.anchor.page is None for c in result.coverage)
    elif format == 'xlsx':
        block = result.blocks[0]
        assert block.original_text == 'Known inline text' and 'unknown_xml_content' in block.review_flags
        assert any(c.state == 'review_pending' and c.block_ids == (block.id,) for c in result.coverage)
    else:
        assert result.blocks[0].original_text == 'Known inline text'
        assert any(c.state == 'review_pending' and c.anchor.part == 'xl/worksheets/sheet1.xml'
                   and 'row[0;r=1]' in c.id for c in result.coverage)


@real_wsl
@pytest.mark.parametrize('case', ['csv-u2028', 'csv-u0085', 'adjacent', 'missing_column', 'empty_row',
                                 'changed_span', 'overlapping_restart'])
def test_real_wsl_csv_physical_lines_and_docx_merge_continuity(case):
    from app.documents.schemas import ParserProfile
    from test_document_readers import docx_vertical_fixture
    module = runner()
    changes = {'document_runner_mode': 'wsl-bubblewrap'}
    for field in ('document_runner_mode', 'document_runtime_python', 'document_runtime_python_sha256', 'document_parser_code_path', 'document_wsl_distribution'):
        if value := os.environ.get('WORKSPACE_TEST_'+field.upper()):
            changes[field] = value
    config = settings(**changes)
    identity = module.ensure_runner_available(config)
    profile = ParserProfile(code_hash=identity['code_hash'], runtime_versions=identity['runtime_versions'])
    if case.startswith('csv-'):
        separator = '\u2028' if case == 'csv-u2028' else '\u0085'
        first, second = f'first{separator}second,value\n', f'"quoted{separator}value\r\nnext",42\r\n'
        source = (first+second).encode('utf-8')
        result = module.run_document(source, 'unicode.csv', 'text/csv', profile, config)
        assert len(result.blocks) == 2 and result.blocks[0].anchor.fields == (f'first{separator}second', 'value')
        assert result.blocks[1].anchor.fields == (f'quoted{separator}value\r\nnext', '42')
        assert [(b.anchor.line_start, b.anchor.line_end) for b in result.blocks] == [(1, 1), (2, 3)]
        for block in result.blocks:
            assert source[block.anchor.byte_start:block.anchor.byte_end].decode() == block.original_text
    elif case == 'adjacent':
        result = module.run_document(docx_vertical_fixture(span=2), 'merged.docx', '', profile, config)
        origin = next(b for b in result.blocks if b.original_text == 'Origin')
        assert origin.anchor.row_span == 3 and origin.anchor.col_span == 2
        assert all(b.anchor.merge_origin == origin.anchor.element_path for b in result.blocks
                   if b.original_text.startswith('Continuation'))
    else:
        with pytest.raises(module.DocumentRunnerError) as error:
            module.run_document(docx_vertical_fixture(case), 'broken.docx', '', profile, config)
        assert error.value.code == 'parser_failed'


def isolated_script(source, **overrides):
    module = runner()
    changes={'document_runner_mode':'wsl-bubblewrap'}
    for field in ('document_runtime_python','document_runtime_python_sha256','document_parser_code_path','document_wsl_distribution'):
        if value:=os.environ.get('WORKSPACE_TEST_'+field.upper()):changes[field]=value
    changes.update(overrides)
    config = settings(**changes)
    command, is_wsl = module._build_command(config)
    command[-1:] = ["-c", source]
    return module, config, command, is_wsl


@real_wsl
def test_real_wsl_private_paths_network_environment_and_resource_limits():
    source = """import json,os,resource,socket
assert os.getuid()!=0
assert not any(os.path.exists(p) for p in ['/home','/mnt/c','/mnt/e','/etc','/root'])
assert 'DATABASE_URL' not in os.environ
s=socket.socket(); s.settimeout(.2)
try:
 s.connect(('1.1.1.1',443)); raise AssertionError('network available')
except OSError: pass
print(json.dumps({n:resource.getrlimit(getattr(resource,n)) for n in ['RLIMIT_CPU','RLIMIT_AS','RLIMIT_NPROC','RLIMIT_NOFILE','RLIMIT_FSIZE']}))
"""
    module, config, command, is_wsl = isolated_script(source)
    output = json.loads(module._stream_process(command, b"", config, None, is_wsl, handshake=True))
    assert output["RLIMIT_CPU"] == [config.document_runner_cpu_seconds] * 2
    assert output["RLIMIT_AS"] == [config.document_runner_memory_bytes] * 2
    assert output["RLIMIT_NPROC"] == [config.document_runner_process_limit] * 2
    assert output["RLIMIT_NOFILE"] == [config.document_runner_file_limit] * 2
    assert output["RLIMIT_FSIZE"] == [config.document_runner_file_bytes] * 2


@real_wsl
@pytest.mark.parametrize("cancel", [False, True])
def test_real_wsl_timeout_and_cancel_kill_linux_descendants(cancel):
    token = "document-owned-" + uuid.uuid4().hex
    source = f"import os,time; os.fork(); time.sleep(90) # {token}"
    module, config, command, is_wsl = isolated_script(source, document_runner_timeout_seconds=2)
    cancel_event = threading.Event()
    timer = threading.Timer(1, cancel_event.set)
    threads_before = set(threading.enumerate())
    if cancel:
        timer.start()
    try:
        with pytest.raises(module.DocumentRunnerError) as error:
            module._stream_process(command, b"", config, cancel_event.is_set if cancel else None, is_wsl, handshake=True)
        assert error.value.code == ("cancelled" if cancel else "timeout")
    finally:
        timer.cancel()
        if cancel:
            timer.join()
    remaining = subprocess.run(module._wsl_prefix(config) + ["/usr/bin/pgrep", "-f", token],
                               capture_output=True, timeout=5)
    assert remaining.returncode == 1, f"surviving Linux descendants: {remaining.stdout!r}"
    assert set(threading.enumerate()) <= threads_before


@real_wsl
def test_real_wsl_cpu_address_space_and_output_limits():
    module, config, command, is_wsl = isolated_script("while True: pass", document_runner_cpu_seconds=1,
                                                    document_runner_timeout_seconds=8)
    with pytest.raises(module.DocumentRunnerError) as error:
        module._stream_process(command, b"", config, None, is_wsl, handshake=True)
    assert error.value.code == "parser_failed"
    module, config, command, is_wsl = isolated_script("x=bytearray(300_000_000)", document_runner_memory_bytes=67_108_864)
    with pytest.raises(module.DocumentRunnerError) as error:
        module._stream_process(command, b"", config, None, is_wsl, handshake=True)
    assert error.value.code == "parser_failed"
    module, config, command, is_wsl = isolated_script("import sys;\nwhile True: sys.stdout.buffer.write(b'x'*4096); sys.stdout.flush()",
                                                    document_runner_output_bytes=8192)
    with pytest.raises(module.DocumentRunnerError) as error:
        module._stream_process(command, b"", config, None, is_wsl, handshake=True)
    assert error.value.code == "output_limit"
