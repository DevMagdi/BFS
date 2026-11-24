from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(
    shutil.which("node") is None, reason="Node.js is required for browser auth tests"
)
def test_browser_api_refresh_and_logout_coordination() -> None:
    script = Path(__file__).with_name("web_auth_refresh.test.js")
    result = subprocess.run(  # noqa: S603
        [shutil.which("node") or "node", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
