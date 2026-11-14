"""Common input cleaning (plan 3.5)."""

import unittest

from ai_skill_jev import sanitize as sz


class RedactTests(unittest.TestCase):
    def test_secret_shapes_are_redacted(self):
        samples = [
            "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
            "AKIAABCDEFGHIJKLMNOP",
            "ghp_" + "a" * 36,
            "github_pat_" + "b" * 30,
            "sk-" + "c" * 30,
            "xoxb-1234567890-abc",
            "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2Q",
        ]
        for secret in samples:
            out = sz.redact(f"before {secret} after", None)
            self.assertNotIn(secret, out, secret)
            self.assertIn(sz.REDACTED, out)

    def test_password_assignment_keeps_the_name(self):
        out = sz.redact('password="hunter2" and token: abc123', None)
        self.assertEqual(out, f'password={sz.REDACTED} and token: {sz.REDACTED}')

    def test_named_secrets_urls_and_headers(self):
        fake = "Zq9fake7Value3x"
        cases = {
            f"DB_PASSWORD={fake}": f"DB_PASSWORD={sz.REDACTED}",
            f"export AWS_SECRET_ACCESS_KEY={fake}": f"export AWS_SECRET_ACCESS_KEY={sz.REDACTED}",
            f"GITHUB_TOKEN: {fake}": f"GITHUB_TOKEN: {sz.REDACTED}",
            f"?access_token={fake}&x=1": f"?access_token={sz.REDACTED}&x=1",
            f"client_secret: '{fake}'": f"client_secret: {sz.REDACTED}",
            f'{{"password": "{fake}", "user": "u"}}': f'{{"password": {sz.REDACTED}, "user": "u"}}',
            f"Authorization: Bearer {fake}": f"Authorization: Bearer {sz.REDACTED}",
            f"postgres://app:{fake}@db:5432/x": f"postgres://app:{sz.REDACTED}@db:5432/x",
        }
        for text, want in cases.items():
            self.assertEqual(sz.redact(text, None), want, text)
        for secret in ("sk_live_" + "d" * 24, "AIza" + "e" * 35, "glpat-" + "f" * 20, "npm_" + "g" * 36):
            self.assertNotIn(secret, sz.redact(f"x {secret} y", None), secret)

    def test_test_log_numbers_survive(self):
        log = "42 passing, 3 failing; pass: 5 fail: 0; tokens: 1200; author: bob; bypass the cache"
        self.assertEqual(sz.redact(log, None), log)

    def test_key_in_use_is_redacted(self):
        self.assertEqual(sz.redact("x tsk-live-1 y", "tsk-live-1"), f"x {sz.REDACTED} y")


class PathTests(unittest.TestCase):
    def test_excluded_groups(self):
        for path in (".env", "config/.env.local", "id_rsa", "certs/server.pem", "logs/app.log",
                     "node_modules/x/index.js", "web\\NODE_MODULES\\a.js"):
            self.assertTrue(sz.is_excluded_path(path), path)
        for path in ("src/app.py", "docs/environment.md", ""):
            self.assertFalse(sz.is_excluded_path(path), path)


class ClipChunkTests(unittest.TestCase):
    def test_clip_head_and_tail(self):
        self.assertEqual(sz.clip("abcdef", 3), ("abc", True))
        self.assertEqual(sz.clip("abcdef", 3, tail=True), ("def", True))
        self.assertEqual(sz.clip("ab", 3), ("ab", False))

    def test_chunks_overlap_and_cover(self):
        text = "".join(chr(65 + i % 26) for i in range(1000))
        parts = sz.chunks(text, 400, 200)
        self.assertTrue(all(len(p) <= 400 for p in parts))
        self.assertEqual(parts[0][-200:], parts[1][:200])
        self.assertTrue(text.endswith(parts[-1]))
        self.assertEqual(sz.chunks("short", 400), ["short"])


if __name__ == "__main__":
    unittest.main()
