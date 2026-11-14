import os
import sys
import time
import unittest

from ai_skill_jev import EXIT_FALLBACK
from ai_skill_jev.credentials import StateError
from ai_skill_jev.jg_runner import (BLOCKED_SEARCH_FLAGS, EXCLUDE_PATTERNS, MAX_REQUESTS_CAP, cleanup_orphans,
                                    failure_reason, is_complete, jg_command, prepare_root, prepare_search_args, run_jg,
                                    split_command)
from ai_skill_jev.pathenv import lookup_jg

from tests.support import SENTINEL_KEY, Sandbox, sha


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import subprocess

        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class ArgvTests(unittest.TestCase):
    def test_split_command(self):
        self.assertEqual(split_command([]), (None, False))
        self.assertEqual(split_command(["auth", "--provider", "custom"]), ("auth", False))
        self.assertEqual(split_command(["--max-requests", "5", "where is x", "."]), ("where is x", False))
        self.assertEqual(split_command(["--exclude=doctor", "doctor"]), ("doctor", False))
        self.assertEqual(split_command(["--help"]), (None, True))
        self.assertEqual(split_command(["-h"]), (None, True))
        self.assertEqual(split_command(["--", "auth"]), ("auth", False))


class SearchPolicyTests(Sandbox):
    """`prepare_search_args`: what the shim lets through to a paid, data-sending jg search."""

    def prepare(self, *args, cwd=None):
        return prepare_search_args(list(args), str(cwd or self.repo))

    def refused(self, *args, cwd=None):
        with self.assertRaises(StateError) as caught:
            self.prepare(*args, cwd=cwd)
        self.assertEqual(caught.exception.reason, "policy")
        return str(caught.exception)

    def cap(self, out):
        self.assertEqual(out.count("--max-requests"), 1)
        return out[out.index("--max-requests") + 1]

    # -- defaults ---------------------------------------------------------------------------

    def test_adds_cap_and_excludes(self):
        out = self.prepare("where is auth", ".")
        self.assertEqual(out[:2], ["where is auth", "."])
        self.assertEqual(self.cap(out), "100")
        for pattern in EXCLUDE_PATTERNS:
            self.assertIn(pattern, out)

    def test_keeps_user_cap_and_terminator(self):
        out = self.prepare("--max-requests=7", "--", "-weird query")
        self.assertEqual(self.cap(out), "7")
        self.assertEqual(out[-2:], ["--", "-weird query"])
        self.assertLess(out.index("--exclude"), out.index("--"))

    # -- flags that widen what is sent --------------------------------------------------------

    def test_widening_flags_are_refused_in_every_form(self):
        for flag in BLOCKED_SEARCH_FLAGS:
            for argv in (["q", ".", f"--{flag}"], [f"--{flag}", "q"], ["q", f"--{flag}=true"],
                         ["--exclude", "x", f"--{flag}", "q"], ["--exclude", f"--{flag}", "q"]):
                with self.subTest(argv=argv):
                    self.assertIn(f"--{flag}", self.refused(*argv))

    def test_blocked_names_after_terminator_are_a_query_not_flags(self):
        out = self.prepare("--", "--hidden")
        self.assertEqual(out[-2:], ["--", "--hidden"])

    def test_blocked_name_as_option_value_is_not_a_flag(self):
        out = self.prepare("--exclude=--hidden", "q")
        self.assertIn("--exclude=--hidden", out)

    # -- request cap --------------------------------------------------------------------------

    def test_max_requests_above_cap_is_clamped(self):
        self.assertEqual(MAX_REQUESTS_CAP, 100)
        for argv in (["--max-requests", "100000", "q"], ["q", "--max-requests=101"]):
            with self.subTest(argv=argv):
                self.assertEqual(self.cap(self.prepare(*argv)), "100")

    def test_max_requests_at_or_below_cap_is_kept(self):
        self.assertEqual(self.cap(self.prepare("q", "--max-requests", "100")), "100")
        self.assertEqual(self.cap(self.prepare("q", "--max-requests", "3")), "3")

    def test_every_repeated_max_requests_is_clamped(self):
        # jg keeps the last value of a repeated option; none may exceed the cap.
        out = self.prepare("q", "--max-requests", "5", "--max-requests=99999")
        values = [out[i + 1] for i, tok in enumerate(out) if tok == "--max-requests"]
        self.assertEqual(values, ["5", "100"])

    def test_invalid_max_requests_is_refused(self):
        for argv in (["q", "--max-requests", "0"], ["q", "--max-requests", "-5"],
                     ["q", "--max-requests=abc"], ["q", "--max-requests="], ["q", "--max-requests"],
                     ["q", "--max-requests", "1e9"], ["q", "--max-requests", "١٢"]):
            with self.subTest(argv=argv):
                self.assertIn("--max-requests", self.refused(*argv))

    # -- search root --------------------------------------------------------------------------

    def test_root_inside_the_repo_is_allowed(self):
        nested = self.repo / "src" / "app.py"
        nested.write_text("x = 1\n", encoding="utf-8")
        for root in (".", "src", str(self.repo / "src"), "src/app.py"):
            with self.subTest(root=root):
                self.assertEqual(self.prepare("q", root)[1], root)

    def test_default_root_is_the_cwd_and_needs_a_repo(self):
        self.prepare("q")  # cwd = repo
        self.prepare("q", cwd=self.repo / "src")
        self.assertIn("not inside a git repo", self.refused("q", cwd=self.empty))

    def test_root_outside_the_repo_is_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        for root in (str(outside), "..", "../outside", str(self.tmp), os.path.abspath(os.sep)):
            with self.subTest(root=root):
                self.assertIn("outside the git repo", self.refused("q", root))

    def test_root_in_another_repo_is_refused(self):
        other = self.tmp / "other"
        (other / ".git").mkdir(parents=True)
        self.assertIn("outside the git repo", self.refused("q", str(other)))

    def test_root_given_after_terminator_is_checked(self):
        self.assertIn("outside the git repo", self.refused("--", "q", str(self.tmp)))

    def test_missing_root_is_refused(self):
        self.assertIn("does not exist", self.refused("q", "no-such-dir"))

    def test_git_file_marks_a_worktree(self):
        worktree = self.tmp / "wt"
        worktree.mkdir()
        (worktree / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
        self.prepare("q", ".", cwd=worktree)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks")
    def test_symlink_inside_repo_pointing_outside_is_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        link = self.repo / "escape"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError:
            self.skipTest("cannot create symlinks here")
        self.assertIn("outside the git repo", self.refused("q", "escape"))


class RunnerTests(Sandbox):
    def cmd(self):
        return jg_command(lookup_jg(self.env).real, self.env)

    def test_credentials_exist_only_during_run_and_env_is_clean(self):
        creds = {"provider": "typesafe", "apiKey": SENTINEL_KEY}
        result = run_jg(self.cmd(), ["q", "."], env=self.env, credentials=creds, key=SENTINEL_KEY, search=True)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.coverage, "complete")
        rec = self.records()[-1]
        self.assertTrue(rec["creds_exists"])
        self.assertEqual(rec["key_sha256"], sha(SENTINEL_KEY))
        self.assertEqual(rec["typesafe_env"], [])
        self.assertEqual(rec["shim_env"], "1")
        if sys.platform != "win32":  # NUL is a character device, so isatty() is True on Windows
            self.assertFalse(rec["stdin_tty"])
        self.assertFalse(os.path.exists(rec["cred_path"]))
        self.assertEqual(self.run_dirs(), [])
        self.assertEqual(self.scan_for(SENTINEL_KEY), [])

    def test_private_dir_acl(self):
        root = prepare_root(self.env)
        if sys.platform == "win32":
            from ai_skill_jev import winsec

            self.assertTrue(winsec.is_private(root))
            self.assertTrue(winsec.is_private(root.parent))
        else:
            self.assertEqual(os.stat(root).st_mode & 0o777, 0o700)

    def test_coverage_partial_without_marker_or_on_error(self):
        self.env["FAKE_JG_MODE"] = "nomarker"
        self.assertEqual(run_jg(self.cmd(), ["q"], env=self.env, search=True).coverage, "partial")
        self.env["FAKE_JG_MODE"] = "exit1"
        result = run_jg(self.cmd(), ["q"], env=self.env, search=True)
        self.assertEqual((result.exit_code, result.coverage), (1, "partial"))

    def test_failed_search_without_output_is_a_fallback(self):
        for mode, reason in (("auth401", "auth_error"), ("provider500", "provider_error")):
            self.env["FAKE_JG_MODE"] = mode
            result = run_jg(self.cmd(), ["q"], env=self.env, search=True)
            self.assertEqual((result.exit_code, result.reason, result.coverage, result.stdout),
                             (EXIT_FALLBACK, reason, "partial", b""), mode)

    def test_failed_non_search_keeps_jg_exit(self):
        self.env["FAKE_JG_MODE"] = "auth401"
        result = run_jg(self.cmd(), ["doctor"], env=self.env)
        self.assertEqual((result.exit_code, result.reason), (1, None))

    def test_marker_inside_an_excerpt_is_not_completion(self):
        self.env["FAKE_JG_MODE"] = "midmarker"
        result = run_jg(self.cmd(), ["q"], env=self.env, search=True)
        self.assertIn(b"End context.", result.stdout)
        self.assertEqual((result.exit_code, result.coverage), (0, "partial"))

    def test_output_cut_at_the_byte_limit_is_partial(self):
        self.env["FAKE_JG_MODE"] = "truncated"
        result = run_jg(self.cmd(), ["q"], env=self.env, search=True)
        self.assertTrue(result.stdout.rstrip().endswith(b"End context."))
        self.assertEqual((result.exit_code, result.coverage), (0, "partial"))

    def test_output_with_key_is_dropped(self):
        for mode in ("leak", "leak_escaped"):
            self.env["FAKE_JG_MODE"] = mode
            creds = {"provider": "typesafe", "apiKey": SENTINEL_KEY + '"\\'}
            result = run_jg(self.cmd(), ["q"], env=self.env, credentials=creds, key=creds["apiKey"], search=True)
            self.assertEqual((result.exit_code, result.reason, result.stdout), (EXIT_FALLBACK, "provider_error", b""))

    def test_timeout_kills_tree_and_cleans_up(self):
        self.env["FAKE_JG_MODE"] = "sleep"
        creds = {"provider": "typesafe", "apiKey": SENTINEL_KEY}
        started = time.monotonic()
        result = run_jg(self.cmd(), ["q"], env=self.env, credentials=creds, key=SENTINEL_KEY,
                        search=True, timeout=3)
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual((result.exit_code, result.reason), (EXIT_FALLBACK, "timeout"))
        rec = self.records()[-1]
        deadline = time.monotonic() + 10
        while _pid_alive(rec["child_pid"]) and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertFalse(_pid_alive(rec["child_pid"]))
        self.assertFalse(os.path.exists(rec["cred_path"]))
        self.assertEqual(self.run_dirs(), [])

    def test_orphan_cleanup(self):
        root = prepare_root(self.env)
        old = root / "run-old"
        (old / "jevgrep").mkdir(parents=True)
        (old / "jevgrep" / "credentials.json").write_text("{}")
        past = time.time() - 3600
        os.utime(old, (past, past))
        fresh = root / "run-fresh"
        fresh.mkdir()
        self.assertEqual(cleanup_orphans(root), 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())


@unittest.skipUnless(sys.platform == "win32", "Windows npm layout")
class NpmLayoutTests(unittest.TestCase):
    def test_npm_cmd_is_bypassed(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "jg.cmd").write_text("@echo off\r\n")
            entry = base / "node_modules" / "@dzhng" / "jevgrep" / "dist" / "bin"
            entry.mkdir(parents=True)
            (entry / "index.js").write_text("")
            (base / "node.exe").write_bytes(b"")
            cmd = jg_command(str(base / "jg.cmd"), {"PATH": ""})
            self.assertEqual(cmd, [str(base / "node.exe"), str(entry / "index.js")])


class CompletionTests(unittest.TestCase):
    def test_footer_shapes(self):
        cases = {
            b"Summary\n1. a.ts:1-2\n\nEnd context.\n": True,
            b"Summary\r\n\r\nEnd context.\r\n": True,  # CRLF from a Windows console
            b"End context.": True,
            b"": False,
            b"Summary\n": False,
            b"x\nEnd context.\n2. b.ts:3-4\n": False,  # marker inside an excerpt
            b"x\nEnd context. more\n": False,
            b"x\n\nOutput truncated at the byte limit (9 bytes); 1 original bytes omitted.\n\nEnd context.\n": False,
        }
        for stdout, expected in cases.items():
            with self.subTest(stdout=stdout):
                self.assertIs(is_complete(stdout), expected)

    def test_failure_reasons(self):
        cases = {
            b"Jev evaluation failed: authentication\n": "auth_error",
            b"AI Gateway authentication failed: Invalid API key or token.\n": "auth_error",
            b"Invalid credentials. Run jg auth again.\n": "auth_error",
            b"HTTP 401: bad key\n": "auth_error",
            b"HTTP 403: Forbidden\n": "auth_error",
            b"Unauthorized\n": "auth_error",
            b"Jev evaluation failed: provider\n": "provider_error",
            b"HTTP 500: upstream error\n": "provider_error",
            b"HTTP 4013: odd\n": "provider_error",
            b"": "provider_error",
        }
        for stderr, expected in cases.items():
            with self.subTest(stderr=stderr):
                self.assertEqual(failure_reason(stderr), expected)


if __name__ == "__main__":
    unittest.main()
