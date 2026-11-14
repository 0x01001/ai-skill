"""ai-skill's own jg engine: found outside PATH, preferred over a global npm `jg`, and the hints
that fix a global npm `jg` shadowing the shim without admin rights."""

import io
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

from ai_skill_jev import credentials as cred
from ai_skill_jev import engine as eng
from ai_skill_jev.cli import Deps, main
from ai_skill_jev.jg_runner import NPM_ENTRY
from ai_skill_jev.jg_shim import run

from tests.support import SENTINEL_KEY, FakeUserEnv, Sandbox, active_metadata
from tests.test_auth import ok_validation

REAL_NODE = shutil.which("node")
SHIM_NAME = "jg.exe" if sys.platform == "win32" else "jg"


class EngineDirTests(unittest.TestCase):
    def test_override_wins(self):
        target = os.path.abspath("custom-engine")
        self.assertEqual(eng.engine_dir({"AI_SKILL_ENGINE_DIR": target, "LOCALAPPDATA": os.path.abspath("x")}),
                         Path(target))

    def test_relative_override_is_ignored(self):
        env = {"AI_SKILL_ENGINE_DIR": "rel", "LOCALAPPDATA": os.path.abspath("la"),
               "XDG_DATA_HOME": os.path.abspath("xdg")}
        self.assertNotIn("rel", str(eng.engine_dir(env)).split(os.sep))

    @unittest.skipUnless(sys.platform == "win32", "Windows layout")
    def test_windows_dir(self):
        base = os.path.abspath("la")
        self.assertEqual(eng.engine_dir({"LOCALAPPDATA": base}), Path(base) / "ai-skill" / "engine")
        self.assertIsNone(eng.engine_dir({}))
        self.assertIn('"<engine dir>"', eng.install_command({}))

    @unittest.skipIf(sys.platform == "win32", "POSIX layout")
    def test_posix_dir(self):
        base = os.path.abspath("xdg")
        self.assertEqual(eng.engine_dir({"XDG_DATA_HOME": base}), Path(base) / "ai-skill" / "engine")
        self.assertEqual(eng.engine_dir({}), Path.home() / ".local" / "share" / "ai-skill" / "engine")

    def test_install_command_is_a_prefix_install_of_latest(self):
        target = os.path.abspath("e")
        command = eng.install_command({"AI_SKILL_ENGINE_DIR": target})
        self.assertEqual(command, f'npm install --prefix "{target}" @dzhng/jevgrep@latest')
        self.assertNotIn("--global", command)


class EngineTests(Sandbox):
    def setUp(self):
        super().setUp()
        # A `node` launcher in its own dir, so the sandbox PATH never picks up the machine's npm bin.
        self.nodebin = self.tmp / "nodebin"
        self.nodebin.mkdir()
        target = REAL_NODE or os.path.join(str(self.tmp), "no-node")
        if sys.platform == "win32":
            (self.nodebin / "node.cmd").write_text(f'@"{target}" %*\r\n', encoding="utf-8")
        else:
            launcher = self.nodebin / "node"
            launcher.write_text(f'#!/bin/sh\nexec "{target}" "$@"\n', encoding="utf-8")
            launcher.chmod(0o755)
        self.engine_root = self.local / "ai-skill" / "engine"
        self.shim_dir = self.tmp / "shim"
        self.shim_dir.mkdir()
        (self.shim_dir / SHIM_NAME).write_bytes(b"launcher for ai_skill_jev.jg_shim")
        self.out = io.StringIO()
        self.npm_prefix = None  # what the fake `npm prefix --global` answers

    # -- helpers ---------------------------------------------------------------------------

    def set_path(self, *dirs):
        self.env["PATH"] = os.pathsep.join([str(d) for d in dirs] + [str(self.nodebin), self.system_path()])

    def install_private_engine(self):
        """The npm `--prefix` layout, with an index.js that runs the fake jg."""
        entry = self.engine_root.joinpath(*NPM_ENTRY)
        entry.parent.mkdir(parents=True)
        entry.write_text(
            "const { spawnSync } = require('child_process');\n"
            f"const r = spawnSync({json.dumps(sys.executable)}, [{json.dumps(str(self.bin / 'fake_jg.py'))}, "
            "...process.argv.slice(2)], { stdio: 'inherit' });\n"
            "process.exit(r.status === null ? 1 : r.status);\n", encoding="utf-8")
        return entry

    def make_npm_prefix(self, name="npm-global"):
        """An npm prefix holding @dzhng/jevgrep, laid out for this platform. Returns (bin dir, prefix):
        Windows (`%APPDATA%\\npm`) puts the launcher in the prefix, POSIX in `<prefix>/bin`."""
        prefix = self.tmp / name
        if sys.platform == "win32":
            bin_dir, modules = prefix, prefix / "node_modules"
        else:
            bin_dir, modules = prefix / "bin", prefix / "lib" / "node_modules"
        bin_dir.mkdir(parents=True)
        self.install_fake_jg(bin_dir)
        (modules / "@dzhng" / "jevgrep").mkdir(parents=True)
        return bin_dir, prefix

    def make_global_npm_jg(self):
        """The jevgrep copy in npm's current global prefix (what `npm prefix --global` reports)."""
        bin_dir, self.npm_prefix = self.make_npm_prefix()
        return bin_dir

    def activate(self):
        meta = active_metadata()
        cred.save_metadata(meta, self.env)
        self.store.data[meta["credential_ref"]] = cred.encode_secret(meta, SENTINEL_KEY)

    def deps(self, user_env=None):
        return Deps(env=self.env, out=self.out, ask=lambda prompt: "1", ask_secret=lambda prompt: SENTINEL_KEY,
                    isatty=lambda: True, detect_store=lambda: self.store, store_by_name={"FakeStore": self.store}.get,
                    validate=ok_validation, user_env=lambda: user_env or FakeUserEnv(str(self.bin)),
                    shim_dir=lambda: str(self.shim_dir), jg_version=lambda path: "0.8.5",
                    npm_prefix=lambda env: str(self.npm_prefix) if self.npm_prefix else None)

    def doctor(self):
        self.out = io.StringIO()
        self.assertEqual(main(["doctor", "--json"], deps=self.deps()), 0)
        return json.loads(self.out.getvalue())

    # -- engine lookup ---------------------------------------------------------------------

    def test_private_engine_preferred_over_path_jg(self):
        entry = self.install_private_engine()
        self.set_path(self.bin)
        found = eng.find_engine(self.env)
        self.assertTrue(found.private)
        self.assertEqual((found.path, found.cmd[-1]), (str(entry), str(entry)))

    def test_path_jg_used_when_no_private_engine(self):
        self.set_path(self.bin)
        found = eng.find_engine(self.env)
        self.assertFalse(found.private)
        self.assertEqual(os.path.dirname(found.path), str(self.bin))

    def test_private_engine_needs_node(self):
        self.install_private_engine()
        self.env["PATH"] = os.pathsep.join([str(self.empty), self.system_path()])
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(eng.find_engine(self.env))

    def test_is_npm_package_jg(self):
        npm = self.make_global_npm_jg()
        launcher = eng.find_engine(dict(self.env, PATH=str(npm))).path
        self.assertTrue(eng.is_npm_package_jg(launcher))
        self.assertFalse(eng.is_npm_package_jg(str(self.shim_dir / SHIM_NAME)))
        self.assertFalse(eng.is_npm_package_jg(None))

    @unittest.skipUnless(REAL_NODE, "needs node")
    def test_shim_runs_private_engine_without_any_jg_on_path(self):
        self.install_private_engine()
        self.set_path(self.empty)  # no global npm jg at all: the setup that broke before 1.0.0
        self.activate()
        out, err = io.BytesIO(), io.BytesIO()
        code = run(["where is x", "."], self.env, store_factory=lambda name: self.store, out=out, err=err)
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn(b"End context.", out.getvalue())
        (record,) = self.records()
        self.assertTrue(record["creds_exists"])
        self.assertEqual(record["typesafe_env"], [])

    # -- doctor ----------------------------------------------------------------------------

    def test_doctor_ready_with_private_engine(self):
        self.activate()
        entry = self.install_private_engine()
        self.set_path(self.shim_dir)
        report = self.doctor()
        self.assertEqual((report["state"], report["jg"], report["jevgrep"]), ("ready", "shim", "ready"))
        self.assertEqual((report["engine_path"], report["engine_private"]), (str(entry), True))
        self.assertEqual(report["hints"], [])

    def test_doctor_shadowed_by_global_npm_hints_uninstall_not_admin(self):
        self.activate()
        self.install_private_engine()
        npm = self.make_global_npm_jg()
        self.set_path(npm, self.shim_dir)
        report = self.doctor()
        self.assertEqual((report["reason"], report["jg"]), ("shadowed", "shadowed"))
        hint = " ".join(report["hints"])
        self.assertIn("npm uninstall --global @dzhng/jevgrep", hint)
        self.assertNotIn("admin", hint)
        self.assertNotIn("--prefix", hint)  # the private engine is already there

    def test_doctor_shadowed_without_private_engine_installs_it_first(self):
        self.activate()
        npm = self.make_global_npm_jg()
        self.set_path(npm, self.shim_dir)
        hint = " ".join(self.doctor()["hints"])
        self.assertLess(hint.index("--prefix"), hint.index("npm uninstall --global"))

    def test_doctor_missing_engine_hints_prefix_install(self):
        self.activate()
        self.set_path(self.shim_dir)
        report = self.doctor()
        self.assertEqual((report["reason"], report["jg"]), ("engine_missing", "missing"))
        self.assertIn(eng.install_command(self.env), report["hints"][0])

    def test_doctor_shim_not_on_path(self):
        self.activate()
        self.install_private_engine()
        self.set_path(self.empty)
        report = self.doctor()
        self.assertEqual(report["jg"], "shadowed")
        self.assertIn("pipx ensurepath", report["hints"][0])

    def test_doctor_text_names_the_engine(self):
        self.activate()
        self.install_private_engine()
        self.set_path(self.shim_dir)
        main(["doctor"], deps=self.deps())
        self.assertIn("engine: jg 0.8.5 (ai-skill's own copy", self.out.getvalue())

    # -- ai auth ---------------------------------------------------------------------------

    def test_auth_with_global_npm_in_machine_path_says_uninstall(self):
        self.install_private_engine()
        npm = self.make_global_npm_jg()
        self.set_path(npm, self.shim_dir)
        user_env = FakeUserEnv(str(self.shim_dir), machine_path=str(npm))
        self.assertEqual(main(["auth"], deps=self.deps(user_env)), 0, self.out.getvalue())
        text = self.out.getvalue()
        self.assertIn("ai-skill's own copy", text)
        self.assertIn("npm uninstall --global @dzhng/jevgrep", text)
        self.assertNotIn("needs admin", text)
        self.assertIn("jevgrep: shadowed", text)
        self.assertEqual(user_env.writes, [])

    # -- npm prefix: only a copy `npm uninstall --global` removes gets that advice ----------

    def test_is_global_npm_jg_needs_the_current_global_prefix(self):
        bin_dir, prefix = self.make_npm_prefix()
        _, other = self.make_npm_prefix("other-prefix")
        launcher = eng.find_engine(dict(self.env, PATH=str(bin_dir))).path
        self.assertTrue(eng.is_global_npm_jg(launcher, str(prefix)))
        self.assertFalse(eng.is_global_npm_jg(launcher, str(other)))
        self.assertFalse(eng.is_global_npm_jg(launcher, None))
        self.assertFalse(eng.is_global_npm_jg(str(self.shim_dir / SHIM_NAME), str(self.shim_dir)))

    def test_npm_global_bin_layout(self):
        prefix = os.path.abspath("p")
        self.assertEqual(eng.npm_global_bin(prefix, "win32"), prefix)
        self.assertEqual(eng.npm_global_bin(prefix, "linux"), os.path.join(prefix, "bin"))

    def assert_path_hint_only(self, hint):
        self.assertNotIn("npm uninstall", hint)
        self.assertIn("put the shim dir first in PATH", hint)

    def test_doctor_npm_copy_in_another_prefix_keeps_the_path_hint(self):
        self.activate()
        self.install_private_engine()
        shadow, _ = self.make_npm_prefix("project-prefix")    # not npm's global prefix
        _, self.npm_prefix = self.make_npm_prefix()
        self.set_path(shadow, self.shim_dir)
        report = self.doctor()
        self.assertEqual(report["jg"], "shadowed")
        self.assert_path_hint_only(" ".join(report["hints"]))

    def test_doctor_local_node_modules_bin_jg_keeps_the_path_hint(self):
        self.activate()
        self.install_private_engine()
        project = self.tmp / "project" / "node_modules"
        local_bin = project / ".bin"
        local_bin.mkdir(parents=True)
        self.install_fake_jg(local_bin)
        (project / "@dzhng" / "jevgrep").mkdir(parents=True)
        _, self.npm_prefix = self.make_npm_prefix()
        self.set_path(local_bin, self.shim_dir)
        self.assert_path_hint_only(" ".join(self.doctor()["hints"]))

    def test_doctor_npm_prefix_unknown_keeps_the_path_hint(self):
        self.activate()
        self.install_private_engine()
        npm = self.make_global_npm_jg()
        self.npm_prefix = None                                # npm missing or `npm prefix -g` failed
        self.set_path(npm, self.shim_dir)
        self.assert_path_hint_only(" ".join(self.doctor()["hints"]))

    def test_auth_machine_path_npm_copy_in_another_prefix_needs_admin(self):
        self.install_private_engine()
        shadow, _ = self.make_npm_prefix("machine-prefix")
        _, self.npm_prefix = self.make_npm_prefix()
        self.set_path(shadow, self.shim_dir)
        user_env = FakeUserEnv(str(self.shim_dir), machine_path=str(shadow))
        self.assertEqual(main(["auth"], deps=self.deps(user_env)), 0, self.out.getvalue())
        text = self.out.getvalue()
        self.assertNotIn("npm uninstall", text)
        self.assertIn("needs admin", text)

    def test_npm_global_prefix_reads_npm(self):
        fake = mock.Mock(returncode=0, stdout=(os.path.abspath("gp") + "\n").encode())
        with mock.patch("shutil.which", return_value="npm"), mock.patch("subprocess.run", return_value=fake):
            self.assertEqual(eng.npm_global_prefix({"PATH": ""}), os.path.abspath("gp"))
        for result in (mock.Mock(returncode=1, stdout=b"x"), mock.Mock(returncode=0, stdout=b"relative")):
            with mock.patch("shutil.which", return_value="npm"), mock.patch("subprocess.run", return_value=result):
                self.assertIsNone(eng.npm_global_prefix({"PATH": ""}))
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(eng.npm_global_prefix({"PATH": ""}))

    def test_auth_with_path_engine_suggests_private_install(self):
        self.set_path(self.shim_dir, self.bin)
        self.assertEqual(main(["auth"], deps=self.deps(FakeUserEnv(str(self.shim_dir)))), 0)
        text = self.out.getvalue()
        self.assertIn("(from PATH)", text)
        self.assertIn(eng.install_command(self.env), text)


if __name__ == "__main__":
    unittest.main()
