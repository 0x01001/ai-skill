import io
import json
import os
import time
import unittest
from pathlib import Path
from unittest import mock

from ai_skill_jev import EXIT_FALLBACK
from ai_skill_jev.credentials import encode_secret, save_metadata
from ai_skill_jev import jg_shim
from ai_skill_jev.jg_shim import BUDGET_FILE, BUDGET_LOCK, Fallback, run, strip_run_dir

from tests.support import SENTINEL_KEY, Sandbox, active_metadata, sha


class StripTests(unittest.TestCase):
    def test_strip_run_dir(self):
        self.assertEqual(strip_run_dir(["q", "--ai-run-dir", "/r", "."]), (["q", "."], "/r"))
        self.assertEqual(strip_run_dir(["--ai-run-dir=/r", "q"]), (["q"], "/r"))
        self.assertEqual(strip_run_dir(["q", "--", "--ai-run-dir"]), (["q", "--", "--ai-run-dir"], None))


class LockSpin(BaseException):
    """Raised by a fake once the lock loop retries far more often than LOCK_WAIT allows.

    A BaseException so that no `except Exception`/`except OSError` in the shim swallows it:
    a regression to a busy loop fails the test instead of hanging it.
    """


def spin_guard(limit=200):
    calls = {"n": 0}

    def tick():
        calls["n"] += 1
        if calls["n"] > limit:
            raise LockSpin("lock loop spins without the deadline or sleep")

    return tick, calls


class BudgetLockTests(Sandbox):
    """`_acquire_lock` must fall back within LOCK_WAIT whatever keeps the lock busy."""

    WAIT = 0.3
    MAX_TRIES = 200  # ~6 tries fit in WAIT with the 50 ms sleep; a busy loop would pass this fast

    def setUp(self):
        super().setUp()
        self.lock = self.tmp / BUDGET_LOCK
        self.tries = 0
        patcher = mock.patch.object(jg_shim, "LOCK_WAIT", self.WAIT)
        patcher.start()
        self.addCleanup(patcher.stop)

    def count(self):
        self.tries += 1
        if self.tries > self.MAX_TRIES:
            raise LockSpin("lock loop spins without the deadline or sleep")

    def stale_lock(self):
        self.lock.write_bytes(b"")
        old = time.time() - jg_shim.LOCK_STALE - 60
        os.utime(self.lock, (old, old))

    def assert_bounded_fallback(self):
        start = time.monotonic()
        with self.assertRaises(Fallback) as caught:
            jg_shim._acquire_lock(self.lock)
        self.assertEqual(caught.exception.reason, "budget")
        self.assertLess(time.monotonic() - start, self.WAIT + 2.0)

    def test_held_lock_falls_back_after_wait(self):
        self.lock.write_bytes(b"")
        self.assert_bounded_fallback()

    def test_stale_lock_that_cannot_be_deleted_falls_back(self):
        self.stale_lock()
        real_unlink = os.unlink

        def refuse(path, *args, **kwargs):
            if Path(path) == self.lock:
                self.count()
                raise PermissionError(13, "in use")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(jg_shim.os, "unlink", side_effect=refuse):
            self.assert_bounded_fallback()
        self.assertGreater(self.tries, 0)

    def test_lock_mtime_unreadable_falls_back(self):
        self.lock.write_bytes(b"")

        def unreadable(path):
            self.count()
            raise PermissionError(13, "denied")

        with mock.patch.object(jg_shim.os.path, "getmtime", side_effect=unreadable):
            self.assert_bounded_fallback()
        self.assertGreater(self.tries, 0)

    def test_create_denied_falls_back(self):
        # On Windows a lock still pending delete makes the create fail with access denied.
        def denied(path, *args, **kwargs):
            self.count()
            raise PermissionError(13, "pending delete")

        with mock.patch.object(jg_shim.os, "open", side_effect=denied):
            self.assert_bounded_fallback()
        self.assertGreater(self.tries, 0)

    def test_stale_lock_is_taken_over(self):
        self.stale_lock()
        fd = jg_shim._acquire_lock(self.lock)
        os.close(fd)
        self.assertTrue(self.lock.exists())


class ShimTests(Sandbox):
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

    # -- no Jev key: jg is not used at all -------------------------------------------------

    NO_KEY_COMMANDS = (["where is x", "."], ["where is x", ".", "--ai-run-dir", "RUN"], ["doctor"],
                       ["--help"], ["--version"], ["files", "."], ["skill", "--yes"], [])

    def assert_jg_unused_without_key(self):
        for argv in self.NO_KEY_COMMANDS:
            argv = [str(self.tmp) if a == "RUN" else a for a in argv]
            code, out, err = self.shim(*argv)
            self.assertEqual(code, EXIT_FALLBACK, argv)
            self.assertEqual(out, "")
            self.assertIn("reason=no_key", err)
            self.assertIn("ai auth", err)
            self.assertNotIn("jg auth", err)
        code, _, err = self.shim("auth", "--provider", "typesafe")
        self.assertEqual(code, EXIT_FALLBACK)
        self.assertIn("ai auth", err)
        self.assertNotIn("jg auth", err)
        self.assertEqual(self.records(), [])  # the real jg never ran, not even `jg auth`
        self.assertFalse((self.tmp / BUDGET_FILE).exists())

    def test_before_ai_auth_jg_is_not_used(self):
        self.assert_jg_unused_without_key()

    def test_after_logout_jg_is_not_used(self):
        meta = self.activate()
        meta["enabled"] = False  # what `ai logout` writes first
        save_metadata(meta, self.env)
        self.assert_jg_unused_without_key()
        meta["credential_ref"] = None
        save_metadata(meta, self.env)
        self.assert_jg_unused_without_key()

    def test_no_key_without_real_jg_is_still_no_key(self):
        self.env["PATH"] = os.pathsep.join([str(self.empty), self.system_path()])
        code, _, err = self.shim("where is x", ".")
        self.assertEqual(code, EXIT_FALLBACK)
        self.assertIn("reason=no_key", err)

    # -- configured ------------------------------------------------------------------------

    def test_auth_is_blocked_when_configured(self):
        self.activate()
        code, _, err = self.shim("auth")
        self.assertEqual(code, EXIT_FALLBACK)
        self.assertIn("ai auth", err)
        self.assertNotIn("jg auth", err)
        self.assertEqual(self.records(), [])

    def test_info_commands_run_without_credentials(self):
        self.activate()
        for argv in (["--help"], ["files", "."], ["skill", "--yes"], []):
            code, _, _ = self.shim(*argv)
            self.assertEqual(code, 0)
            rec = self.records()[-1]
            self.assertEqual(rec["argv"], argv)
            self.assertFalse(rec["creds_exists"])
            self.assertIsNotNone(rec["xdg"])  # private empty config dir, never jg auth's

    def test_search_uses_temporary_credentials(self):
        self.activate()
        code, out, err = self.shim("where is auth handled", ".")
        self.assertEqual(code, 0)
        self.assertIn("End context.", out)
        self.assertIn("coverage=complete", err)
        rec = self.records()[-1]
        self.assertTrue(rec["creds_exists"])
        self.assertEqual(rec["provider"], "typesafe")
        self.assertEqual(rec["key_sha256"], sha(SENTINEL_KEY))
        self.assertEqual(rec["typesafe_env"], [])
        self.assertIn("--max-requests", rec["argv"])
        self.assertIn(".env*", rec["argv"])
        self.assertEqual(self.scan_for(SENTINEL_KEY), [])
        self.assertNotIn(SENTINEL_KEY, out + err)

    def test_any_jg_version_searches_with_the_key(self):
        # Upstream jevgrep installs jg@latest; a new release must never turn Jev off. The key is
        # the only switch: the shim does not even ask jg for its version.
        self.activate()
        for version in ("0.9.0", "1.0.0", "2.3.4-beta.1", "garbage"):
            self.env["FAKE_JG_VERSION"] = version
            code, out, err = self.shim("where is auth handled", ".")
            self.assertEqual(code, 0, err)
            self.assertIn("End context.", out)
            self.assertNotIn("unavailable", err)
            rec = self.records()[-1]
            self.assertTrue(rec["creds_exists"])
            self.assertEqual(rec["key_sha256"], sha(SENTINEL_KEY))
        self.assertNotIn(["--version"], [r["argv"] for r in self.records()])

    def test_broken_version_output_still_searches(self):
        self.activate()
        self.env["FAKE_JG_MODE"] = "noversion"
        for argv in (["where is auth handled", "."], ["doctor"]):
            code, _, err = self.shim(*argv)
            self.assertEqual(code, 0, err)
            self.assertTrue(self.records()[-1]["creds_exists"])

    def test_custom_provider_credentials(self):
        self.activate(provider="custom", base_url="https://proxy.example/v1", model="jev-x")
        self.shim("q", ".")
        rec = self.records()[-1]
        self.assertEqual((rec["provider"], rec["baseURL"]), ("custom", "https://proxy.example/v1"))

    def test_doctor_runs_with_credentials_without_search_args(self):
        self.activate()
        code, _, err = self.shim("doctor")
        self.assertEqual(code, 0)
        rec = self.records()[-1]
        self.assertEqual(rec["argv"], ["doctor"])
        self.assertTrue(rec["creds_exists"])
        self.assertNotIn("coverage=", err)

    def test_budget_counts_before_call_and_stops_at_three(self):
        self.activate()
        run_dir = self.tmp / "run"
        run_dir.mkdir()
        for _ in range(3):
            self.assertEqual(self.shim("q", ".", "--ai-run-dir", str(run_dir))[0], 0)
        code, _, err = self.shim("q", ".", "--ai-run-dir", str(run_dir))
        self.assertEqual(code, EXIT_FALLBACK)
        self.assertIn("reason=budget", err)
        self.assertEqual(len(self.records()), 3)
        budget = json.loads((run_dir / BUDGET_FILE).read_text())
        self.assertEqual(budget["used"], 3)
        self.assertTrue(all(c["status"] == "exit=0" for c in budget["calls"]))
        self.assertTrue(all("--ai-run-dir" not in r["argv"] for r in self.records()))

    def test_rejected_key_is_an_auth_error_fallback(self):
        self.activate()
        run_dir = self.tmp / "run"
        run_dir.mkdir()
        cases = (("auth401", "auth_error"), ("provider500", "provider_error"))
        for mode, reason in cases:
            self.env["FAKE_JG_MODE"] = mode
            code, out, err = self.shim("q", ".", "--ai-run-dir", str(run_dir))
            self.assertEqual((code, out), (EXIT_FALLBACK, ""), mode)
            self.assertIn(f"reason={reason}", err)
            self.assertNotIn("coverage=", err)
            self.assertNotIn("Jev evaluation failed", err)  # jg's own text is not passed on
            self.assertEqual("ai auth" in err, reason == "auth_error")
            self.assertNotIn(SENTINEL_KEY, err)
        budget = json.loads((run_dir / BUDGET_FILE).read_text())
        self.assertEqual([c["status"] for c in budget["calls"]], [r for _, r in cases])

    def test_failed_search_with_output_still_passes_through(self):
        self.activate()
        self.env["FAKE_JG_MODE"] = "exit1"
        code, out, err = self.shim("q", ".")
        self.assertEqual(code, 1)
        self.assertIn("src/events.ts", out)
        self.assertIn("coverage=partial (jg exit=1)", err)

    def test_stuck_budget_lock_falls_back_without_calling_jg(self):
        self.activate()
        run_dir = self.tmp / "run"
        run_dir.mkdir()
        lock = run_dir / BUDGET_LOCK
        lock.write_bytes(b"")
        old = time.time() - jg_shim.LOCK_STALE - 60
        os.utime(lock, (old, old))
        real_unlink = os.unlink
        tick, calls = spin_guard()

        def refuse(path, *args, **kwargs):
            if Path(path) == lock:
                tick()
                raise PermissionError(13, "in use")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(jg_shim, "LOCK_WAIT", 0.3), \
                mock.patch.object(jg_shim.os, "unlink", side_effect=refuse):
            code, _, err = self.shim("q", ".", "--ai-run-dir", str(run_dir))
        self.assertEqual(code, EXIT_FALLBACK)
        self.assertIn("reason=budget", err)
        self.assertEqual(self.records(), [])
        self.assertGreater(calls["n"], 0)

    # -- search policy ----------------------------------------------------------------------

    def assert_refused(self, *argv, run_dir=None):
        reads = []
        real_get = self.store.get

        def counting_get(ref):
            reads.append(ref)
            return real_get(ref)

        self.store.get = counting_get
        extra = ("--ai-run-dir", str(run_dir)) if run_dir is not None else ()
        code, out, err = self.shim(*argv, *extra)
        self.assertEqual(code, EXIT_FALLBACK, err)
        self.assertIn("reason=policy", err)
        self.assertIn("refused:", err)
        self.assertEqual(self.records(), [])  # neither the search nor `jg --version` ran
        self.assertEqual(reads, [])            # the key was never read
        self.assertEqual(self.scan_for(SENTINEL_KEY), [])
        self.assertNotIn(SENTINEL_KEY, out + err)
        return err

    def test_widening_flags_are_refused_before_jg_runs(self):
        self.activate()
        for flag in ("--include-sensitive", "--hidden", "--no-ignore", "--include-dependencies"):
            with self.subTest(flag=flag):
                self.assertIn(flag, self.assert_refused("where is x", ".", flag))

    def test_reviewed_probe_is_refused(self):
        # The exact argv the review showed was forwarded unchanged to the real jg.
        self.activate()
        self.assert_refused("q", os.path.abspath(os.sep), "--include-sensitive", "--hidden", "--no-ignore",
                            "--include-dependencies", "--max-requests", "100000")

    def test_root_outside_repo_is_refused_and_spends_no_budget(self):
        self.activate()
        run_dir = self.tmp / "run"
        run_dir.mkdir()
        self.assertIn("outside the git repo", self.assert_refused("q", str(self.tmp), run_dir=run_dir))
        self.assertFalse((run_dir / BUDGET_FILE).exists())

    def test_cwd_outside_any_repo_is_refused(self):
        self.activate()
        os.chdir(self.empty)
        self.assertIn("not inside a git repo", self.assert_refused("q"))

    def test_max_requests_is_clamped_for_the_real_jg(self):
        self.activate()
        code, _, err = self.shim("q", ".", "--max-requests", "100000")
        self.assertEqual(code, 0, err)
        argv = self.records()[-1]["argv"]
        self.assertEqual(argv.count("--max-requests"), 1)
        self.assertEqual(argv[argv.index("--max-requests") + 1], "100")
        self.assertNotIn("100000", argv)

    def test_search_in_a_repo_subdir_runs(self):
        self.activate()
        os.chdir(self.repo / "src")
        code, _, err = self.shim("q", "..")
        self.assertEqual(code, 0, err)
        self.assertIn("coverage=complete", err)

    def test_without_a_key_nothing_reaches_jg(self):
        # No key: no search at all, whatever the flags or root (no policy check needed).
        code, _, err = self.shim("q", str(self.tmp), "--hidden")
        self.assertEqual(code, EXIT_FALLBACK)
        self.assertIn("reason=no_key", err)
        self.assertEqual(self.records(), [])

    def test_budget_is_not_used_without_run_dir(self):
        self.activate()
        for _ in range(4):
            self.assertEqual(self.shim("q", ".")[0], 0)


if __name__ == "__main__":
    unittest.main()
