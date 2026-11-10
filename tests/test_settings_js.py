"""settings.js: a row save that comes back 401/403/500 must say so (run in node against a fake DOM)."""
from __future__ import annotations
import json, shutil, subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run(status: int, body: str) -> dict:
    p = subprocess.run(["node", str(ROOT / "tests" / "js" / "settings_harness.js"), str(ROOT / "app/web/static/settings.js"), str(status), body],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_401_sends_the_browser_to_sign_in():
    r = run(401, json.dumps({"detail": "sign in required"}))
    assert r["href"] == "/login?next=%2Fsettings" and "sign in" in r["rowstatus"] and "bad" in r["cls"]


def test_403_and_500_show_the_servers_message():
    assert run(403, json.dumps({"detail": "editor role required"}))["rowstatus"] == "editor role required"
    assert run(500, "HTML")["rowstatus"] == "server error 500"


def test_field_errors_still_use_the_field_message():
    assert run(200, json.dumps({"ok": False, "errors": {"label": "required"}}))["rowstatus"] == "Row not saved: fix the highlighted field."
