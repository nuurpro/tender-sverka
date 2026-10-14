import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


def staging_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "stage_document_parser.py"
    assert path.is_file(), "The allowlisted document staging helper is missing"
    spec = importlib.util.spec_from_file_location("stage_documents_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_staging_carries_only_allowlisted_code_and_matches_binary_source_hash(tmp_path):
    module = staging_module()
    names = ("__init__.py", "entrypoint.py", "readers.py", "schemas.py", "images.py", "toolchain.py", "bootstrap.py", "archives.py", "archive_entrypoint.py")
    contents = {name: ("# " + name + "\r\n").encode() for name in names}
    for name, data in contents.items():
        (tmp_path / name).write_bytes(data)
    (tmp_path / ".env").write_text("do-not-stage-private-configuration", encoding="utf-8")
    (tmp_path / "service.py").write_text("do-not-stage-server-code", encoding="utf-8")
    result = module.stage_payload(tmp_path)
    assert set(result["files"]) == set(names)
    assert {name: base64.b64decode(data) for name, data in result["files"].items()} == contents
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    expected = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert result["code_hash"] == expected


def test_missing_or_oversized_code_refuses_before_any_stage_process(tmp_path, monkeypatch):
    module = staging_module()
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="complete"):
        module.stage_payload(tmp_path)
    for name in module.CODE_FILES:
        (tmp_path / name).write_bytes(b"# valid\n")
    (tmp_path / "readers.py").write_bytes(b"x" * 262_145)
    with pytest.raises(ValueError, match="bounded"):
        module.stage_payload(tmp_path)
    assert calls == []


@pytest.mark.skipif(os.environ.get("DOCUMENT_RUNNER_INTEGRATION") != "1", reason="Actual sandbox staging opt-in required")
def test_linux_stage_accepts_largest_nine_file_payload_in_private_tmpfs():
    from app.config import Settings
    from app.documents.runner import _build_command, _stream_process

    module = staging_module()
    names = ("__init__.py", "entrypoint.py", "readers.py", "schemas.py", "images.py", "toolchain.py", "bootstrap.py", "archives.py", "archive_entrypoint.py")
    contents = {name: b"#" * 262_144 for name in names}
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    request = json.dumps({"files": {name: base64.b64encode(value).decode() for name, value in contents.items()}, "code_hash": digest}).encode()
    assert len(request) > 3_000_000
    assert len(request) <= 4_000_000
    runner_fields = ("DOCUMENT_RUNNER_MODE", "DOCUMENT_RUNTIME_PYTHON", "DOCUMENT_RUNTIME_PYTHON_SHA256",
                     "DOCUMENT_PARSER_CODE_PATH", "DOCUMENT_WSL_DISTRIBUTION")
    configured = {key.lower(): os.environ["WORKSPACE_TEST_" + key] for key in runner_fields}
    config = Settings(environment="test", database_url="sqlite://", session_secret="s" * 48, **configured)
    command, is_wsl = _build_command(config)
    preparation = "from pathlib import Path\np=Path('/tmp/.local/share/tender-sverka/document-runtime/bin')\np.mkdir(parents=True)\n(p/'python').write_bytes(b'# temporary marker')\n"
    command[-1:] = ["-c", preparation + module.LINUX_STAGE]
    response = json.loads(_stream_process(command, request, config, None, is_wsl, handshake=True))
    assert response["code_hash"] == digest
    assert response["parser_code_path"].startswith("/tmp/")
