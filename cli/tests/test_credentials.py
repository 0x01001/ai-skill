import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ai_skill_jev import POLICY_VERSION
from ai_skill_jev import credentials as cred
from ai_skill_jev import pathenv, provider

from tests.support import SENTINEL_KEY, FakeStore, FakeUserEnv, Sandbox, active_metadata


class InputValidationTests(unittest.TestCase):
    def test_key(self):
        self.assertEqual(provider.validate_key("  " + SENTINEL_KEY + "\n"), SENTINEL_KEY)
        for bad in ("", "   ", "a b", "x" * 9000, "k\x00ey"):
            with self.assertRaises(provider.InputError):
                provider.validate_key(bad)

    def test_base_url(self):
        self.assertEqual(provider.validate_base_url(" https://host.example/v1/ "), "https://host.example/v1")
        self.assertEqual(provider.validate_base_url("http://127.0.0.1:8080/v1"), "http://127.0.0.1:8080/v1")
        for bad in ("http://host.example/v1", "ftp://x/v1", "https://user:pw@host/v1",
                    "https://host/v1?q=1", "https://host/v1#f", "not a url", ""):
            with self.assertRaises(provider.InputError, msg=bad):
                provider.validate_base_url(bad)

    def test_profiles_and_payload(self):
        typesafe = provider.resolve_profile("typesafe")
        self.assertEqual(typesafe["endpoint_host"], "api.typesafe.ai")
        self.assertEqual(provider.jg_credentials_payload(typesafe, "k"), {"provider": "typesafe", "apiKey": "k"})
        custom = provider.resolve_profile("custom", "https://proxy.example/v1", "jev-x")
        self.assertEqual(provider.jg_credentials_payload(custom, "k"),
                         {"provider": "custom", "baseURL": "https://proxy.example/v1", "model": "jev-x", "apiKey": "k"})
        with self.assertRaises(provider.InputError):
            provider.resolve_profile("nope")


class SecretBindingTests(unittest.TestCase):
    def test_roundtrip_and_binding(self):
        meta = active_metadata()
        value = cred.encode_secret(meta, SENTINEL_KEY)
        self.assertEqual(cred.decode_secret(value, meta), SENTINEL_KEY)
        for field, other in (("provider", "custom"), ("base_url", "https://evil.example/v1"), ("model", "m2")):
            moved = dict(meta, **{field: other})
            with self.assertRaises(cred.StateError) as ctx:
                cred.decode_secret(value, moved)
            self.assertEqual(ctx.exception.reason, "no_key")
            self.assertNotIn(SENTINEL_KEY, str(ctx.exception))
        for broken in (None, "", "not json", '{"v":2}', '{"v":1,"provider":"typesafe"}'):
            with self.assertRaises(cred.StateError):
                cred.decode_secret(broken, meta)

    def test_read_key_errors(self):
        meta = active_metadata()
        store = FakeStore()
        store.data["ref0"] = cred.encode_secret(meta, SENTINEL_KEY)
        self.assertEqual(cred.read_key(meta, lambda name: store), SENTINEL_KEY)
        for factory, reason in ((lambda name: None, "locked_store"),
                                (lambda name: FakeStore(fail_get=True), "locked_store"),
                                (lambda name: FakeStore(slow_get=2.0), "locked_store"),
                                (lambda name: FakeStore(), "no_key")):
            with self.assertRaises(cred.StateError) as ctx:
                cred.read_key(meta, factory, timeout=0.5)
            self.assertEqual(ctx.exception.reason, reason)

    def test_consent_valid(self):
        meta = active_metadata()
        self.assertTrue(cred.consent_valid(meta))
        self.assertFalse(cred.consent_valid(active_metadata(consent=False)))
        stale = active_metadata()
        stale["consent"]["policy_version"] = POLICY_VERSION + 1
        self.assertFalse(cred.consent_valid(stale))
        moved = active_metadata()
        moved["base_url"] = "https://other.example/v1"
        self.assertFalse(cred.consent_valid(moved))


class MetadataTests(Sandbox):
    def test_roundtrip_and_corruption(self):
        self.assertIsNone(cred.load_metadata(self.env))
        meta = active_metadata()
        cred.save_metadata(meta, self.env)
        self.assertEqual(cred.load_metadata(self.env), meta)
        cred.metadata_path(self.env).write_text('{"schema_version": 99}')
        with self.assertRaises(cred.StateError) as ctx:
            cred.load_metadata(self.env)
        self.assertEqual(ctx.exception.reason, "no_cli")
        cred.delete_metadata(self.env)
        self.assertIsNone(cred.load_metadata(self.env))

    def test_config_dir_is_private(self):
        cred.save_metadata(active_metadata(), self.env)
        directory = cred.config_dir(self.env)
        if sys.platform == "win32":
            from ai_skill_jev import winsec

            self.assertTrue(winsec.is_private(directory))
        else:
            self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)
            self.assertEqual(os.stat(cred.metadata_path(self.env)).st_mode & 0o777, 0o600)

    def test_write_private_file_is_exclusive(self):
        target = self.tmp / "f"
        cred.write_private_file(target, b"a")
        with self.assertRaises(FileExistsError):
            cred.write_private_file(target, b"b")


@unittest.skipUnless(sys.platform == "win32", "Windows DACL parsing")
class SddlTests(unittest.TestCase):
    USER = "S-1-5-21-1-2-3-1001"

    def test_private_forms(self):
        from ai_skill_jev.winsec import private_dacl_sddl, sddl_is_private

        self.assertTrue(sddl_is_private(private_dacl_sddl(self.USER), self.USER))
        self.assertTrue(sddl_is_private("D:P(A;OICI;FA;;;LA)(A;OICI;FA;;;SY)", self.USER, "LA"))
        self.assertTrue(sddl_is_private("D:P(A;OICIIO;FA;;;CO)(A;;FA;;;%s)" % self.USER, self.USER))
        self.assertTrue(sddl_is_private("D:P(D;;FA;;;WD)(A;;FA;;;%s)" % self.USER, self.USER))

    def test_not_private(self):
        from ai_skill_jev.winsec import sddl_is_private

        for sddl in ("D:P(A;OICI;FA;;;%s)(A;OICI;FR;;;BU)" % self.USER,
                     # inherit-only ACEs land on files created inside, e.g. credentials.json
                     "D:P(A;OICI;FA;;;%s)(A;OIIO;FR;;;WD)" % self.USER,
                     "D:P(A;OICI;FA;;;%s)(A;CIIO;FA;;;BU)" % self.USER,
                     "D:P(A;OICI;FA;;;%s)(A;OICIIO;GR;;;AU)" % self.USER,
                     "D:(A;;FA;;;WD)",
                     "D:NO_ACCESS_CONTROL",
                     "D:P(A;;FA;;;S-1-5-21-9-9-9-500)",
                     ""):
            self.assertFalse(sddl_is_private(sddl, self.USER), sddl)


@unittest.skipUnless(sys.platform == "win32", "Windows ACLs")
class InheritedAceTests(Sandbox):
    """A real dir whose DACL is private for itself but passes read access down to new files."""

    def test_inherit_only_ace_is_replaced_before_files_are_written(self):
        from ai_skill_jev import winsec
        from ai_skill_jev.credentials import ensure_private_dir, write_private_file

        sid = winsec.current_user_sid()
        target = self.tmp / "leaky"
        target.mkdir()
        winsec.set_dacl(target, "D:P(A;OICI;FA;;;%s)(A;OIIO;FR;;;WD)" % sid)
        self.assertFalse(winsec.is_private(target))

        ensure_private_dir(target)
        self.assertTrue(winsec.is_private(target))
        self.assertNotIn(";WD)", winsec.dacl_sddl(target))
        secret_file = target / "credentials.json"
        write_private_file(secret_file, b"{}")
        self.assertNotIn(";WD)", winsec.dacl_sddl(secret_file))
        self.assertTrue(winsec.is_private(secret_file))

    def test_unprotected_file_would_inherit_everyone_read(self):
        # Control: without the fix the file below such a dir really gets Everyone read.
        from ai_skill_jev import winsec

        sid = winsec.current_user_sid()
        target = self.tmp / "leaky"
        target.mkdir()
        winsec.set_dacl(target, "D:P(A;OICI;FA;;;%s)(A;OIIO;FR;;;WD)" % sid)
        probe = target / "probe.txt"
        probe.write_bytes(b"x")
        self.assertIn(";WD)", winsec.dacl_sddl(probe))
        self.assertFalse(winsec.is_private(probe))


class PathPlanTests(unittest.TestCase):
    SHIM = r"C:\pipx\bin"
    NPM = r"C:\npm"

    def classify(self, entry):
        return {self.SHIM.lower(): "shim", self.NPM.lower(): "real"}.get(entry.lower())

    def test_plans(self):
        sep = os.pathsep
        self.assertEqual(pathenv.plan_user_path(None, "", None, self.classify).status, "no_shim")
        ready = pathenv.plan_user_path(self.SHIM + sep + self.NPM, "", self.SHIM, self.classify)
        self.assertEqual(ready.status, "ready")
        change = pathenv.plan_user_path(self.NPM + sep + r"C:\x" + sep + self.SHIM, r"C:\Windows", self.SHIM,
                                        self.classify)
        self.assertEqual(change.status, "change")
        self.assertEqual(change.new_user_path, sep.join([self.SHIM, self.NPM, r"C:\x"]))
        machine = pathenv.plan_user_path(self.SHIM, self.NPM, self.SHIM, self.classify)
        self.assertEqual((machine.status, machine.blocking_dir), ("shadowed_machine", self.NPM))
        empty = pathenv.plan_user_path(None, "", self.SHIM, self.classify)
        self.assertEqual((empty.status, empty.new_user_path), ("change", self.SHIM))


class PathApplyTests(Sandbox):
    def make_shim_dir(self):
        shim_dir = self.tmp / "shim"
        shim_dir.mkdir()
        name = "jg.exe" if sys.platform == "win32" else "jg"
        (shim_dir / name).write_bytes(b"MZ...ai_skill_jev.jg_shim...")
        return str(shim_dir)

    def test_apply_moves_the_shim_dir_first_once(self):
        shim_dir = self.make_shim_dir()
        original = os.pathsep.join([str(self.bin), r"C:\tools"])
        env = FakeUserEnv(original, machine_path=str(self.empty), value_type=1)
        plan = pathenv.apply_user_path_fix(env, shim_dir, self.env)
        self.assertEqual(plan.status, "change")
        self.assertEqual(env.user_path, os.pathsep.join([shim_dir, original]))
        self.assertEqual(env.value_type, 1)
        self.assertEqual(env.broadcasts, 1)
        # Second run: shim already first, nothing written.
        plan2 = pathenv.apply_user_path_fix(env, shim_dir, self.env)
        self.assertEqual((plan2.status, len(env.writes), env.broadcasts), ("ready", 1, 1))

    def test_machine_path_shadow_is_reported_not_written(self):
        shim_dir = self.make_shim_dir()
        env = FakeUserEnv("", machine_path=str(self.bin))
        plan = pathenv.apply_user_path_fix(env, shim_dir, self.env)
        self.assertEqual((plan.status, env.writes), ("shadowed_machine", []))

    def test_shim_detection_by_content(self):
        shim_dir = Path(self.make_shim_dir())
        self.assertTrue(pathenv.is_shim_file(next(shim_dir.iterdir())))
        self.assertFalse(pathenv.is_shim_file(next(p for p in self.bin.iterdir() if p.name.startswith("jg"))))


if __name__ == "__main__":
    unittest.main()
