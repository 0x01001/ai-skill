"""Shared fakes: a fake `jg`, a fake secret store, a sandboxed environment and a local HTTP provider.

Nothing here touches the network, the real secret store, the real PATH/registry or the
`jg auth` credentials of the machine.
"""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SENTINEL_KEY = "tsk-SENTINEL-0123456789abcdef-DO-NOT-LEAK"

FAKE_JG = r'''
import hashlib, json, os, subprocess, sys, time

args = sys.argv[1:]
mode = os.environ.get("FAKE_JG_MODE", "ok")
if args == ["--version"]:
    if mode == "noversion":  # a jg whose --version is broken or prints nothing usable
        sys.exit(1)
    print(os.environ.get("FAKE_JG_VERSION", "0.8.5"))
    sys.exit(0)
xdg = os.environ.get("XDG_CONFIG_HOME")
cred_path = os.path.join(xdg, "jevgrep", "credentials.json") if xdg else None
creds = None
if cred_path and os.path.exists(cred_path):
    with open(cred_path, encoding="utf-8") as fh:
        creds = json.load(fh)
record = {
    "argv": args,
    "xdg": xdg,
    "cred_path": cred_path,
    "creds_exists": creds is not None,
    "provider": (creds or {}).get("provider"),
    "baseURL": (creds or {}).get("baseURL"),
    "key_sha256": hashlib.sha256(creds["apiKey"].encode()).hexdigest() if creds else None,
    "typesafe_env": [k for k in os.environ if k.upper() in ("TYPESAFE_API_KEY", "TYPESAFE_AI_API_KEY")],
    "shim_env": os.environ.get("AI_SKILL_JG_SHIM"),
    "stdin_tty": sys.stdin.isatty() if sys.stdin else None,
}
child = None
if mode == "sleep":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    record["child_pid"] = child.pid
path = os.environ.get("FAKE_JG_RECORD")
if path:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
if mode == "sleep":
    time.sleep(120)
if mode == "leak" and creds:
    print("echo " + creds["apiKey"])
    sys.exit(0)
if mode == "leak_escaped" and creds:
    print(json.dumps({"k": creds["apiKey"]}))
    sys.exit(0)
if mode == "auth401":  # jg's whole-run auth failure: nothing on stdout, non-zero exit
    sys.stderr.write("Jev evaluation failed: authentication\n")
    sys.exit(1)
if mode == "provider500":
    sys.stderr.write("Jev evaluation failed: provider\n")
    sys.exit(1)
print("Summary: event recording lives in src/events.ts")
print("1. src/events.ts:1-20")
if mode == "nomarker":
    sys.exit(0)
if mode == "midmarker":  # a quoted excerpt contains the marker, but jg stopped before its footer
    print("```")
    print('    print("End context.")')
    print("End context.")
    print("```")
    print("2. src/more.ts:1-5")
    sys.exit(0)
if mode == "truncated":
    print("")
    print("Output truncated at the byte limit (100 bytes); 900 original bytes omitted.")
    print("")
    print("End context.")
    sys.exit(0)
if mode == "exit1":
    print("End context.")
    sys.exit(1)
print("End context.")
sys.exit(0)
'''


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class FakeStore:
    backend_name = "FakeStore"

    def __init__(self, fail_delete=False, fail_get=False, slow_get=0.0):
        self.data = {}
        self.fail_delete = fail_delete
        self.fail_get = fail_get
        self.slow_get = slow_get

    def max_secret_chars(self):
        return None

    def get(self, account):
        if self.slow_get:
            import time

            time.sleep(self.slow_get)
        if self.fail_get:
            raise RuntimeError("locked")
        return self.data.get(account)

    def set(self, account, value):
        self.data[account] = value

    def delete(self, account):
        if self.fail_delete:
            raise RuntimeError("locked")
        self.data.pop(account, None)


class FakeUserEnv:
    def __init__(self, user_path, machine_path="", value_type=2):
        self.user_path = user_path
        self.value_type = value_type if user_path is not None else None
        self.machine = machine_path
        self.writes = []
        self.broadcasts = 0

    def read_user_path(self):
        return self.user_path, self.value_type

    def write_user_path(self, value, value_type):
        self.writes.append((value, value_type))
        self.user_path, self.value_type = value, value_type

    def machine_path(self):
        return self.machine

    def broadcast(self):
        self.broadcasts += 1


class Sandbox(unittest.TestCase):
    """A temp home with APPDATA/LOCALAPPDATA/XDG dirs, a fake jg on PATH and a record file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ai-skill-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.appdata = self.tmp / "appdata"
        self.local = self.tmp / "local"
        self.bin = self.tmp / "bin"
        self.empty = self.tmp / "empty"
        for d in (self.appdata, self.local, self.bin, self.empty):
            d.mkdir()
        self.record = self.tmp / "record.jsonl"
        self.env = {k: v for k, v in os.environ.items()
                    if k.upper() not in ("TYPESAFE_API_KEY", "TYPESAFE_AI_API_KEY", "AI_SKILL_JG_SHIM",
                                         "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "XDG_CACHE_HOME")}
        self.env.update({
            "APPDATA": str(self.appdata),
            "LOCALAPPDATA": str(self.local),
            "XDG_CONFIG_HOME": str(self.appdata),
            "XDG_RUNTIME_DIR": str(self.local),
            "FAKE_JG_RECORD": str(self.record),
            "TYPESAFE_API_KEY": "env-key-must-not-reach-jg",
        })
        self.install_fake_jg(self.bin)
        self.env["PATH"] = os.pathsep.join([str(self.bin), self.system_path()])
        self.store = FakeStore()
        # Searches must stay inside the git repo of the working directory: run every test from a
        # throwaway repo so the result never depends on where the suite was started.
        self.repo = self.tmp / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / "src").mkdir()
        previous_cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, previous_cwd)  # runs before the rmtree registered above

    @staticmethod
    def system_path() -> str:
        if sys.platform == "win32":
            root = os.environ.get("SystemRoot", r"C:\Windows")
            return os.pathsep.join([os.path.join(root, "System32"), root])
        return "/usr/bin:/bin"

    @staticmethod
    def install_fake_jg(directory: Path) -> Path:
        script = directory / "fake_jg.py"
        script.write_text(FAKE_JG, encoding="utf-8")
        if sys.platform == "win32":
            launcher = directory / "jg.cmd"
            launcher.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            launcher = directory / "jg"
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            launcher.chmod(0o755)
        return launcher

    def records(self):
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text(encoding="utf-8").splitlines() if line]

    def scan_for(self, needle: str):
        """Files under the sandbox (except the fake jg record) whose bytes contain `needle`."""
        hits = []
        for path in self.tmp.rglob("*"):
            if path.is_file() and path != self.record:
                if needle.encode() in path.read_bytes():
                    hits.append(str(path))
        return hits

    def run_dirs(self):
        root = self.local / "ai-skill" / "jg-run"
        return [p for p in root.iterdir()] if root.exists() else []


def active_metadata(provider="typesafe", base_url="https://api.typesafe.ai/v1", model="jev-1.13.0",
                    ref="ref0", consent=True):
    from ai_skill_jev import POLICY_VERSION
    from ai_skill_jev.credentials import new_metadata

    meta = new_metadata()
    meta.update({
        "enabled": True, "provider": provider, "base_url": base_url, "model": model,
        "endpoint_host": base_url.split("/")[2], "secret_backend": "FakeStore", "credential_ref": ref,
        "profile_generation": 1,
        "consent": {"provider": provider, "endpoint": base_url, "policy_version": POLICY_VERSION,
                    "accepted_at": "2026-11-14T00:00:00Z"} if consent else None,
    })
    return meta


class ProviderServer:
    """Local HTTP server on 127.0.0.1 that plays the provider for validation tests."""

    def __init__(self, status=200, body=None, delay=0.0, location=None, raw=None, require_criteria=False):
        """`require_criteria` plays a gateway that answers 422 to a question without `criteria.true`."""
        self.status, self.body, self.delay, self.location, self.raw = status, body, delay, location, raw
        self.require_criteria = require_criteria
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                questions = body.get("questions") or {}
                if outer.require_criteria and any(
                        not isinstance(q.get("criteria"), dict) or "true" not in q["criteria"]
                        for q in questions.values()):
                    payload = b'{"error": "questions.relevant.criteria.true is required"}'
                    self.send_response(422)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if outer.delay:
                    import time

                    time.sleep(outer.delay)
                payload = outer.raw if outer.raw is not None else json.dumps(outer.body or {}).encode()
                self.send_response(outer.status)
                if outer.location:
                    self.send_header("Location", outer.location)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def good_answer(value=0.9, usage=True):
    body = {"answers": {"relevant": {"type": "noul", "noul": value}}}
    if usage:
        body["usage"] = {"input_tokens": 42, "output_tokens": 0}
    return body


class Capture(io.StringIO):
    pass
