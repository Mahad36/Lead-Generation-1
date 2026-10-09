import json
import stat
from types import SimpleNamespace

import pytest

from app.services import ai
from app.services.ai import AIProviderError, ClaudeCodeProvider
from app.services.integrations import AIConfig

FAKE_CLI = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
record = {"args": args, "api_key": "ANTHROPIC_API_KEY" in os.environ,
          "token": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"), "prompt": prompt, "cwd": os.getcwd()}
open(os.environ["FAKE_LOG"], "w").write(json.dumps(record))
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "login":
    print(json.dumps({"type": "result", "is_error": True, "result": "Invalid API key · Please run /login"})); sys.exit(1)
if mode == "limit":
    print(json.dumps({"type": "result", "is_error": True, "result": "Claude AI usage limit reached"})); sys.exit(1)
out = {"answer": 42} if "--json-schema" in args else None
print(json.dumps({"type": "result", "is_error": False, "result": "plain text", "structured_output": out}))
'''


@pytest.fixture
def cli(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text(FAKE_CLI)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(claude_code_bin=str(script), claude_code_oauth_token="sub-token", claude_code_timeout_seconds=60),
    )
    provider = ClaudeCodeProvider(
        AIConfig(api_key="", api_base="", provider="claude_code", model="opus", temperature=0.4, timeout_seconds=30)
    )
    return provider, (lambda: json.loads(log.read_text()))


def test_structured_output_with_screenshots(cli, tmp_path):
    provider, record = cli
    shot = tmp_path / "desktop-1.png"
    shot.write_bytes(b"png")
    text = provider.generate("Audit this", system="sys", json_schema={"type": "object"}, images=[str(shot)])
    assert json.loads(text) == {"answer": 42}
    call = record()
    args = call["args"]
    assert args[args.index("--tools") + 1] == "Read"
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert args[args.index("--model") + 1] == "opus"
    assert "--strict-mcp-config" in args
    assert str(shot) in call["prompt"] and call["cwd"] == str(tmp_path)
    assert call["api_key"] is False  # subscription only, never the API key
    assert call["token"] == "sub-token"


def test_no_tools_without_screenshots(cli):
    provider, record = cli
    assert provider.generate("Say ok", system="sys") == "plain text"
    args = record()["args"]
    assert args[args.index("--tools") + 1] == ""


@pytest.mark.parametrize(("mode", "message"), [("login", "claude setup-token"), ("limit", "usage limit")])
def test_errors_are_explained(cli, monkeypatch, mode, message):
    provider, _ = cli
    monkeypatch.setenv("FAKE_MODE", mode)
    with pytest.raises(AIProviderError, match=message):
        provider.generate("x", system="sys")


def test_missing_cli_is_not_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(claude_code_bin=str(tmp_path / "nope"), claude_code_oauth_token="", claude_code_timeout_seconds=60),
    )
    monkeypatch.setattr(ai.Path, "home", lambda: tmp_path)
    with pytest.raises(ai.AINotConfiguredError, match="not installed"):
        ClaudeCodeProvider(AIConfig("", "", "claude_code", "opus", 0.4, 30))


def test_anthropic_api_gets_screenshots_as_image_blocks(tmp_path):
    shot = tmp_path / "mobile-1.png"
    shot.write_bytes(b"\x89PNG")
    content = ai._content_with_images("Audit", [str(shot)])
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    assert content[-1]["type"] == "text" and "mobile-1.png" in content[-1]["text"]
    assert ai._content_with_images("Audit", None) == "Audit"
