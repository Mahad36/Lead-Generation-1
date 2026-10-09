import sys
import time

import pytest

from app.services.ai import AICancelledError, _run_stoppable, cancel_check

SLEEPER = [sys.executable, "-c", "import sys, time; sys.stdin.read(); time.sleep(30)"]


def test_running_claude_process_is_ended_when_the_audit_is_stopped(tmp_path):
    started = time.monotonic()
    token = cancel_check.set(lambda: time.monotonic() - started > 1)
    try:
        with pytest.raises(AICancelledError):
            _run_stoppable(SLEEPER, "prompt", limit=60, env=None, cwd=str(tmp_path))
    finally:
        cancel_check.reset(token)
    assert time.monotonic() - started < 10  # stopped within seconds, not after the 30 s sleep


def test_process_output_is_returned_when_not_stopped(tmp_path):
    echo = [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"]
    result = _run_stoppable(echo, "hello", limit=30, env=None, cwd=str(tmp_path))
    assert result.returncode == 0 and result.stdout.strip() == "HELLO"
