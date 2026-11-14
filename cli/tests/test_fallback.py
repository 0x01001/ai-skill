"""Every failure path falls back with a reason code, makes no provider call, and leaks nothing (JEV-07)."""

import io
import os

from ai_skill_jev import EXIT_FALLBACK, POLICY_VERSION
from ai_skill_jev.credentials import encode_secret, metadata_path, save_metadata
from ai_skill_jev.jg_shim import run

from tests.support import SENTINEL_KEY, FakeStore, Sandbox, active_metadata


class FallbackTests(Sandbox):
    def shim(self, *argv, store=None):
        out, err = io.BytesIO(), io.BytesIO()
        store = store or self.store
        code = run(list(argv), self.env, store_factory=lambda name: store, out=out, err=err)
        return code, out.getvalue().decode(), err.getvalue().decode()

    def activate(self, **kwargs):
        meta = active_metadata(**kwargs)
        save_metadata(meta, self.env)
        self.store.data[meta["credential_ref"]] = encode_secret(meta, SENTINEL_KEY)
        return meta

    def assert_fallback(self, reason, *argv, store=None, calls=0):
        code, out, err = self.shim(*(argv or ("where is x", ".")), store=store)
        self.assertEqual(code, EXIT_FALLBACK, err)
        self.assertIn(f"reason={reason}", err)
        self.assertIn("grep/read files", err)
        self.assertNotIn("jg auth", err)
        self.assertNotIn(SENTINEL_KEY, out + err)
        searches = [r for r in self.records() if r["argv"] != ["--version"]]
        self.assertEqual(len(searches), calls)
        self.assertEqual(self.scan_for(SENTINEL_KEY), [])
        return err

    def test_no_consent(self):
        self.activate(consent=False)
        self.assert_fallback("no_consent")

    def test_outdated_policy(self):
        meta = self.activate()
        meta["consent"]["policy_version"] = POLICY_VERSION - 1
        save_metadata(meta, self.env)
        self.assert_fallback("no_consent")

    def test_consent_for_other_endpoint(self):
        meta = self.activate()
        meta["base_url"] = "https://evil.example/v1"
        save_metadata(meta, self.env)
        self.assert_fallback("no_consent")

    def test_secret_bound_to_other_endpoint(self):
        meta = self.activate()
        meta["base_url"] = "https://evil.example/v1"
        meta["consent"]["endpoint"] = "https://evil.example/v1"
        save_metadata(meta, self.env)
        self.assert_fallback("no_key")

    def test_missing_secret(self):
        self.activate()
        self.store.data.clear()
        self.assert_fallback("no_key")

    def test_locked_store(self):
        self.activate()
        self.assert_fallback("locked_store", store=FakeStore(fail_get=True))

    def test_missing_jg(self):
        self.activate()
        self.env["PATH"] = os.pathsep.join([str(self.empty), self.system_path()])
        err = self.assert_fallback("engine_missing")
        self.assertIn("@dzhng/jevgrep@latest", err)  # same as upstream jevgrep, no pinned version
        self.assertNotIn("supported", err)

    def test_timeout(self):
        self.activate()
        self.env["FAKE_JG_MODE"] = "sleep"
        import ai_skill_jev.jg_runner as runner

        original = runner.DEADLINE_SECONDS
        runner.run_jg.__kwdefaults__["timeout"] = 3
        try:
            self.assert_fallback("timeout", calls=1)
        finally:
            runner.run_jg.__kwdefaults__["timeout"] = original

    def test_leak(self):
        self.activate()
        self.env["FAKE_JG_MODE"] = "leak"
        self.assert_fallback("provider_error", calls=1)

    def test_corrupt_metadata(self):
        self.activate()
        metadata_path(self.env).write_text("{not json")
        self.assert_fallback("no_cli")

    def test_recursion_guard(self):
        self.env["AI_SKILL_JG_SHIM"] = "1"
        self.assert_fallback("engine_missing")

    def test_no_key_in_metadata(self):
        self.activate()
        self.assertNotIn(SENTINEL_KEY, metadata_path(self.env).read_text())
