import io
import json
import os
import sys
import unittest
from unittest import mock

from ai_skill_jev import EXIT_FALLBACK, jg_shim, pathenv, provider
from ai_skill_jev import credentials as cred
from ai_skill_jev.cli import Deps, main
from ai_skill_jev.provider import ValidationResult

from tests.support import (SENTINEL_KEY, FakeStore, FakeUserEnv, ProviderServer, Sandbox, active_metadata,
                           good_answer)


class ValidationTests(unittest.TestCase):
    def test_ok_and_request_shape(self):
        with ProviderServer(body=good_answer()) as server:
            result = provider.validate_credentials(server.url, "jev-1.13.0", SENTINEL_KEY)
        self.assertEqual((result.ok, result.reason), (True, "ok"))
        self.assertIn("input_tokens=42", result.usage)
        req = server.requests[0]
        self.assertEqual(req["path"], "/v1/systemone")
        self.assertEqual(req["headers"]["Authorization"], f"Bearer {SENTINEL_KEY}")
        self.assertEqual(req["body"]["state"], {"source": provider.VALIDATION_SOURCE})
        # The question jg 0.8.5 sends (boolean -> noul, no criteria), like its own `jg doctor` probe.
        self.assertEqual(req["body"]["questions"],
                         {"relevant": {"type": "noul", "instructions": provider.VALIDATION_INSTRUCTIONS}})
        self.assertEqual(req["body"]["model"], "jev-1.13.0")

    def test_gateway_requiring_criteria_fails_validation(self):
        # Such a gateway answers 422 to every jg search; validation must not report it usable.
        with ProviderServer(body=good_answer(), require_criteria=True) as server:
            result = provider.validate_credentials(server.url, "m", SENTINEL_KEY)
        self.assertEqual((result.ok, result.reason, result.http_status), (False, "provider_error", 422))
        self.assertNotIn("criteria", server.requests[0]["body"]["questions"]["relevant"])

    def test_answer_jg_doctor_would_reject_fails_validation(self):
        for value in (0.5, 0.1, 0.0):
            with ProviderServer(body=good_answer(value=value)) as server:
                result = provider.validate_credentials(server.url, "m", "k")
            self.assertEqual((result.ok, result.reason), (False, "provider_error"), value)

    def test_usage_unknown(self):
        with ProviderServer(body=good_answer(usage=False)) as server:
            result = provider.validate_credentials(server.url, "m", "k")
        self.assertEqual((result.ok, result.usage), (True, "unknown"))

    def test_failures(self):
        cases = [
            (dict(status=401, body={}), "auth_error", 401),
            (dict(status=403, body={}), "auth_error", 403),
            (dict(status=500, body={}), "provider_error", 500),
            (dict(body={"answers": {}}), "provider_error", 200),
            (dict(body=good_answer(value=1.5)), "provider_error", 200),
            (dict(body={"answers": {"relevant": {"type": "noul", "noul": True}}}), "provider_error", 200),
            (dict(raw=b"not json"), "provider_error", 200),
            (dict(raw=b" " * (provider.MAX_RESPONSE_BYTES + 10)), "provider_error", 200),
            (dict(status=302, location="http://127.0.0.1:9/v1/systemone"), "provider_error", 302),
        ]
        for kwargs, reason, status in cases:
            with ProviderServer(**kwargs) as server:
                result = provider.validate_credentials(server.url, "m", SENTINEL_KEY)
            self.assertEqual((result.ok, result.reason, result.http_status), (False, reason, status), kwargs)
            self.assertEqual(len(server.requests), 1, "redirects must not be followed")

    def test_timeout_and_network_error(self):
        with ProviderServer(body=good_answer(), delay=2.0) as server:
            result = provider.validate_credentials(server.url, "m", "k", timeout=0.5)
        self.assertEqual(result.reason, "timeout")
        dead = ProviderServer()
        url = dead.url
        dead.httpd.server_close()
        self.assertEqual(provider.validate_credentials(url, "m", "k", timeout=15).reason, "network_error")


def ok_validation(*_args, **_kwargs):
    return ValidationResult(True, "ok", 200, "input_tokens=42, output_tokens=0")


class CliTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.out = io.StringIO()
        self.answers = []
        self.secret = SENTINEL_KEY
        self.validated = []
        self.user_env = FakeUserEnv(str(self.bin), machine_path="")
        self.shim_dir = self.tmp / "shim"
        self.shim_dir.mkdir()
        name = "jg.exe" if sys.platform == "win32" else "jg"
        (self.shim_dir / name).write_bytes(b"launcher for ai_skill_jev.jg_shim")
        self.stores = {"FakeStore": self.store}

    def ask(self, prompt):
        self.out.write(prompt + "\n")
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def deps(self, tty=True, validate=None, jg_version=None):
        def record_validate(base_url, model, key, **kw):
            self.validated.append((base_url, model, key == SENTINEL_KEY))
            return (validate or ok_validation)(base_url, model, key, **kw)

        return Deps(env=self.env, out=self.out, ask=self.ask, ask_secret=lambda prompt: self.secret,
                    isatty=lambda: tty, detect_store=lambda: self.store, store_by_name=self.stores.get,
                    validate=record_validate, user_env=lambda: self.user_env,
                    shim_dir=lambda: str(self.shim_dir), jg_version=jg_version or (lambda real: "0.8.5"))

    def ai(self, *argv, **kwargs):
        code = main(list(argv), deps=self.deps(**kwargs))
        return code, self.out.getvalue()

    def assert_no_leak(self, text):
        self.assertNotIn(SENTINEL_KEY, text)
        self.assertEqual(self.scan_for(SENTINEL_KEY), [])

    # -- ai auth ---------------------------------------------------------------------------

    def test_auth_requires_tty(self):
        code, out = self.ai("auth", tty=False)
        self.assertEqual(code, 2)
        self.assertIn("interactive terminal", out)
        self.assertIsNone(cred.load_metadata(self.env))

    def test_auth_success(self):
        self.answers = ["1", "yes"]
        code, out = self.ai("auth")
        self.assertEqual(code, 0, out)
        meta = cred.load_metadata(self.env)
        self.assertTrue(cred.is_active(meta) and cred.consent_valid(meta))
        self.assertEqual((meta["provider"], meta["endpoint_host"]), ("typesafe", "api.typesafe.ai"))
        self.assertEqual(self.validated, [("https://api.typesafe.ai/v1", "jev-1.13.0", True)])
        self.assertEqual(cred.decode_secret(self.store.data[meta["credential_ref"]], meta), SENTINEL_KEY)
        self.assertEqual(self.user_env.user_path, os.pathsep.join([str(self.shim_dir), str(self.bin)]))
        self.assertNotIn("path_backup", meta)
        self.assertIn("jevgrep: ready", out)
        self.assert_no_leak(out)

    def test_consent_declined_changes_nothing(self):
        self.answers = ["1", "no"]
        code, out = self.ai("auth")
        self.assertEqual(code, 1)
        self.assertIn("consent not given", out)
        self.assertEqual((cred.load_metadata(self.env), self.store.data, self.validated), (None, {}, []))
        self.assertEqual(self.user_env.writes, [])

    def test_validation_failure_changes_nothing(self):
        self.answers = ["1", "yes"]
        code, out = self.ai("auth", validate=lambda *a, **k: ValidationResult(False, "auth_error", 401))
        self.assertEqual(code, 1)
        self.assertIn("auth_error, HTTP 401", out)
        self.assertEqual((cred.load_metadata(self.env), self.store.data), (None, {}))
        self.assert_no_leak(out)

    def test_bad_key_input(self):
        self.answers = ["1"]
        self.secret = "has space"
        code, out = self.ai("auth")
        self.assertEqual(code, 1)
        self.assertNotIn("has space", out)

    def test_custom_loopback_endpoint_end_to_end(self):
        with ProviderServer(body=good_answer()) as server:
            self.answers = ["5", server.url, "jev-x", "y", "yes"]
            code, out = self.ai("auth", validate=provider.validate_credentials)
        self.assertEqual(code, 0, out)
        meta = cred.load_metadata(self.env)
        self.assertEqual((meta["provider"], meta["base_url"]), ("custom", server.url))
        self.assertEqual(len(server.requests), 1)
        self.assert_no_leak(out)

    def test_custom_gateway_requiring_criteria_is_not_enabled(self):
        with ProviderServer(body=good_answer(), require_criteria=True) as server:
            self.answers = ["5", server.url, "jev-x", "y", "yes"]
            code, out = self.ai("auth", validate=provider.validate_credentials)
        self.assertEqual(code, 1, out)
        self.assertIn("validation failed (provider_error, HTTP 422)", out)
        self.assertEqual((cred.load_metadata(self.env), self.store.data), (None, {}))
        self.assertEqual(self.user_env.writes, [])
        self.assert_no_leak(out)

    # -- ai auth cancelled (Ctrl+C) ------------------------------------------------------------

    def test_cancel_before_save_changes_nothing(self):
        def interrupt(*_args, **_kwargs):
            raise KeyboardInterrupt

        self.answers = ["1", "yes"]
        code, out = self.ai("auth", validate=interrupt)
        self.assertEqual(code, 130)
        self.assertIn("Nothing was changed.", out)
        self.assertEqual((cred.load_metadata(self.env), self.store.data), (None, {}))

    def test_cancel_after_save_reports_saved_state(self):
        # Ctrl+C during the jg version check: the key and jev.json are already committed.
        def interrupt(_real):
            raise KeyboardInterrupt

        self.answers = ["1", "yes"]
        code, out = self.ai("auth", jg_version=interrupt)
        self.assertEqual(code, 130)
        self.assertNotIn("Nothing was changed", out)
        self.assertIn("Jev is on with typesafe (api.typesafe.ai)", out)
        self.assertIn("`ai logout`", out)
        meta = cred.load_metadata(self.env)
        self.assertTrue(cred.is_active(meta))
        self.assertIn(meta["credential_ref"], self.store.data)
        self.assert_no_leak(out)

    def test_cancel_while_saving_removes_the_new_secret(self):
        self.answers = ["1", "yes"]
        with mock.patch.object(cred, "save_metadata", side_effect=KeyboardInterrupt):
            code, out = self.ai("auth")
        self.assertEqual(code, 130)
        self.assertIn("Nothing was changed.", out)
        self.assertEqual((cred.load_metadata(self.env), self.store.data), (None, {}))

    def test_cancel_while_the_store_writes_removes_the_new_secret(self):
        # Ctrl+C lands after the keyring stored the key but before store.set returned.
        real_set = self.store.set

        def set_then_interrupt(account, value):
            real_set(account, value)
            raise KeyboardInterrupt

        self.answers = ["1", "yes"]
        with mock.patch.object(self.store, "set", side_effect=set_then_interrupt):
            code, out = self.ai("auth")
        self.assertEqual(code, 130)
        self.assertIn("Nothing was changed.", out)
        self.assertEqual((cred.load_metadata(self.env), self.store.data), (None, {}))

    def test_cancel_right_after_jev_json_is_written_keeps_its_secret(self):
        real_save = cred.save_metadata

        def save_then_interrupt(meta, env=None):
            real_save(meta, env)
            raise KeyboardInterrupt

        self.answers = ["1", "yes"]
        with mock.patch.object(cred, "save_metadata", side_effect=save_then_interrupt):
            code, out = self.ai("auth")
        self.assertEqual(code, 130)
        self.assertIn("Jev is on", out)
        meta = cred.load_metadata(self.env)
        self.assertEqual(cred.decode_secret(self.store.data[meta["credential_ref"]], meta), SENTINEL_KEY)

    def test_rotation_deletes_old_secret_and_keeps_consent(self):
        self.answers = ["1", "yes"]
        self.ai("auth")
        old_ref = cred.load_metadata(self.env)["credential_ref"]
        self.answers = ["1", "1"]  # change key -> same provider; consent still valid, no "yes" needed
        code, out = self.ai("auth")
        self.assertEqual(code, 0, out)
        meta = cred.load_metadata(self.env)
        self.assertNotEqual(meta["credential_ref"], old_ref)
        self.assertEqual(list(self.store.data), [meta["credential_ref"]])
        self.assertEqual(meta["pending_secret_delete"], [])  # the write-ahead entry is cleared
        self.assertEqual(meta["profile_generation"], 2)
        self.assertIn("Consent for this provider and endpoint is still valid", out)

    def test_rotation_with_locked_old_store_records_pending(self):
        self.answers = ["1", "yes"]
        self.ai("auth")
        old_ref = cred.load_metadata(self.env)["credential_ref"]
        self.stores["FakeStore"] = None
        self.answers = ["1", "1"]
        code, out = self.ai("auth")
        self.assertEqual(code, 0)
        self.assertEqual(cred.load_metadata(self.env)["pending_secret_delete"],
                         [{"ref": old_ref, "backend": "FakeStore"}])
        self.assertIn("could not be removed", out)

    def rotate_with_interrupt(self, interrupt_old_delete):
        """Change the key, with Ctrl+C landing between the commit and the old key's removal."""
        self.answers = ["1", "yes"]
        self.ai("auth")
        old_ref = cred.load_metadata(self.env)["credential_ref"]
        real_delete = self.store.delete

        def delete(account):
            if account == old_ref:
                interrupt_old_delete(real_delete, account)
            else:
                real_delete(account)

        self.answers = ["1", "1"]
        with mock.patch.object(self.store, "delete", side_effect=delete):
            code, out = self.ai("auth")
        return old_ref, code, out

    def assert_logout_removes_both_keys(self, old_ref):
        meta = cred.load_metadata(self.env)
        self.assertTrue(cred.is_active(meta))
        self.assertNotEqual(meta["credential_ref"], old_ref)
        self.assertIn({"ref": old_ref, "backend": "FakeStore"}, meta["pending_secret_delete"])
        code, out = self.ai("logout")
        self.assertEqual(code, 0, out)
        self.assertEqual((cred.load_metadata(self.env), self.store.data), (None, {}))

    def test_cancel_before_the_old_key_is_removed_leaves_it_to_logout(self):
        def interrupt(_real_delete, _account):
            raise KeyboardInterrupt

        old_ref, code, out = self.rotate_with_interrupt(interrupt)
        self.assertEqual(code, 130)
        self.assertIn("Jev is on", out)
        self.assertIn(old_ref, self.store.data)  # still in the store, but recorded on disk
        self.assert_logout_removes_both_keys(old_ref)

    def test_cancel_after_the_old_key_is_removed_is_harmless(self):
        # Ctrl+C after the keyring deleted the old key, before jev.json dropped the pending entry.
        def delete_then_interrupt(real_delete, account):
            real_delete(account)
            raise KeyboardInterrupt

        old_ref, code, _ = self.rotate_with_interrupt(delete_then_interrupt)
        self.assertEqual(code, 130)
        self.assertNotIn(old_ref, self.store.data)
        self.assert_logout_removes_both_keys(old_ref)  # deleting a missing entry is not an error

    def test_existing_config_exit(self):
        self.answers = ["1", "yes"]
        self.ai("auth")
        before = cred.load_metadata(self.env)
        self.answers = ["2"]
        self.assertEqual(self.ai("auth")[0], 0)
        self.assertEqual(cred.load_metadata(self.env), before)

    # -- ai logout -------------------------------------------------------------------------

    def assert_jg_is_the_shim_and_falls_back(self):
        """`jg` on the user PATH is still the shim, and the shim no longer runs the real jg."""
        env = dict(self.env, PATH=os.pathsep.join([self.user_env.user_path, self.system_path()]))
        lookup = pathenv.lookup_jg(env)
        self.assertTrue(lookup.first_is_shim)
        out, err = io.BytesIO(), io.BytesIO()
        code = jg_shim.run(["where is x", "."], self.env, store_factory=self.stores.get, out=out, err=err)
        self.assertEqual((code, out.getvalue()), (EXIT_FALLBACK, b""))
        self.assertIn(b"reason=no_key", err.getvalue())
        self.assertEqual(self.records(), [])  # the npm jg never ran

    def test_logout_clean_keeps_jg_on_the_shim(self):
        self.answers = ["1", "yes"]
        self.ai("auth")
        path_after_auth = self.user_env.user_path
        writes = list(self.user_env.writes)
        code, out = self.ai("logout")
        self.assertEqual(code, 0, out)
        self.assertIsNone(cred.load_metadata(self.env))
        self.assertEqual(self.store.data, {})
        self.assertEqual((self.user_env.user_path, self.user_env.writes), (path_after_auth, writes))
        self.assertIn("stays on the ai-skill shim", out)
        self.assertNotIn("PATH restored", out)
        self.assert_jg_is_the_shim_and_falls_back()

    def test_logout_drops_a_path_backup_from_an_older_version(self):
        self.answers = ["1", "yes"]
        self.ai("auth")
        meta = cred.load_metadata(self.env)
        meta["path_backup"] = {"value": str(self.bin), "type": 2, "written": self.user_env.user_path}
        cred.save_metadata(meta, self.env)
        path_after_auth = self.user_env.user_path
        self.store.fail_delete = True
        self.assertEqual(self.ai("logout")[0], 1)  # metadata kept: the key could not be removed
        self.assertNotIn("path_backup", cred.load_metadata(self.env))
        self.store.fail_delete = False
        self.assertEqual(self.ai("logout")[0], 0)
        self.assertEqual(self.user_env.user_path, path_after_auth)
        self.assert_jg_is_the_shim_and_falls_back()

    def test_cancel_after_path_write_then_logout_keeps_the_shim_first(self):
        # Ctrl+C during the WM_SETTINGCHANGE broadcast, i.e. right after the registry write.
        def interrupt():
            raise KeyboardInterrupt

        self.user_env.broadcast = interrupt
        self.answers = ["1", "yes"]
        code, out = self.ai("auth")
        self.assertEqual(code, 130)
        self.assertTrue(self.user_env.user_path.startswith(str(self.shim_dir)))
        self.user_env.broadcast = lambda: None
        code, out = self.ai("logout")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.user_env.user_path.startswith(str(self.shim_dir)))
        self.assertIsNone(cred.load_metadata(self.env))
        self.assert_jg_is_the_shim_and_falls_back()

    def test_failed_registry_write_then_logout_leaves_path_alone(self):
        def refuse(value, value_type):
            raise PermissionError(5, "access denied")

        self.user_env.write_user_path = refuse
        self.answers = ["1", "yes"]
        code, out = self.ai("auth")
        self.assertEqual(code, 0, out)
        self.assertIn("Could not update the user PATH", out)
        self.assertIn("jevgrep: shadowed", out)
        self.assertEqual(self.user_env.user_path, str(self.bin))
        del self.user_env.write_user_path  # back to the recording fake
        code, out = self.ai("logout")
        self.assertEqual(code, 0, out)
        self.assertEqual((self.user_env.writes, self.user_env.user_path), ([], str(self.bin)))
        self.assertIsNone(cred.load_metadata(self.env))

    def test_logout_when_store_fails_keeps_pending_and_disables(self):
        self.answers = ["1", "yes"]
        self.ai("auth")
        self.store.fail_delete = True
        code, out = self.ai("logout")
        self.assertEqual(code, 1)
        meta = cred.load_metadata(self.env)
        self.assertFalse(cred.is_active(meta))
        self.assertIsNone(meta["consent"])
        self.assertEqual(len(meta["pending_secret_delete"]), 1)
        self.store.fail_delete = False
        self.assertEqual(self.ai("logout")[0], 0)
        self.assertEqual((cred.load_metadata(self.env), self.store.data), (None, {}))

    def test_logout_without_config(self):
        code, out = self.ai("logout")
        self.assertEqual(code, 0)
        self.assertIn("not configured", out)

    # -- ai doctor -------------------------------------------------------------------------

    def doctor(self):
        self.out = io.StringIO()
        code, out = self.ai("doctor", "--json")
        self.assertEqual(code, 0)
        return json.loads(out)

    def test_doctor_not_configured(self):
        report = self.doctor()
        self.assertEqual((report["tool"], report["state"], report["reason"]), ("ai-skill", "off", "no_key"))
        self.assertEqual(report["jevgrep"], "not configured")
        self.assertIsNone(report["provider"])

    def test_doctor_states(self):
        meta = active_metadata()
        cred.save_metadata(meta, self.env)
        # The sandbox PATH has the real (fake) jg first, not the shim.
        report = self.doctor()
        self.assertEqual((report["state"], report["reason"], report["jg"]), ("off", "shadowed", "shadowed"))
        self.env["PATH"] = os.pathsep.join([str(self.shim_dir), self.env["PATH"]])
        report = self.doctor()
        self.assertEqual((report["state"], report["reason"], report["jg"], report["jevgrep"]),
                         ("ready", None, "shim", "ready"))
        self.assertEqual(report["endpoint_host"], "api.typesafe.ai")
        meta["consent"] = None
        cred.save_metadata(meta, self.env)
        self.assertEqual(self.doctor()["reason"], "no_consent")

    def test_doctor_ready_with_any_jg_version(self):
        # The version is reported for information only; a key (with its consent) is what counts.
        cred.save_metadata(active_metadata(), self.env)
        self.env["PATH"] = os.pathsep.join([str(self.shim_dir), self.env["PATH"]])
        for version in ("0.9.0", "10.0.0", None):
            self.out = io.StringIO()
            main(["doctor", "--json"], deps=self.deps(jg_version=lambda real, v=version: v))
            report = json.loads(self.out.getvalue())
            self.assertEqual((report["state"], report["reason"], report["jg"], report["jg_version"]),
                             ("ready", None, "shim", version))
            self.assertFalse([h for h in report["hints"] if "support" in h], report["hints"])

    def test_auth_accepts_any_jg_version(self):
        self.answers = ["1", "yes"]
        code, out = self.ai("auth", jg_version=lambda real: "3.0.0")
        self.assertEqual(code, 0, out)
        self.assertIn("Engine: jg 3.0.0", out)
        self.assertNotIn("engine unavailable", out)
        self.assertNotIn("supported", out)
        self.assertTrue(cred.is_active(cred.load_metadata(self.env)))

    def test_doctor_corrupt(self):
        cred.config_dir(self.env).mkdir(parents=True)
        cred.metadata_path(self.env).write_text("{bad")
        report = self.doctor()
        self.assertEqual((report["state"], report["reason"]), ("off", "no_cli"))


if __name__ == "__main__":
    unittest.main()
