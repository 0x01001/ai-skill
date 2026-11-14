// Installer tests: every external command goes to a fake `run`, so nothing is installed for real.
// Run: npm test   (node --test test/install.test.mjs)
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { CLI_PACKAGE, JG_PACKAGE, main, parseArgs } from "../bin/lib.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const CLI_DIR = "/pkg/cli";
const EXEC = "/node/bin/node";
const NPM_CLI = "/node/lib/npm/bin/npm-cli.js";
const LINUX_BIN = "/home/u/.local/bin";

// `pipx list --json` output: venv name -> the apps its packages expose.
function pipxList(venvs) {
  const entries = Object.entries(venvs).map(([name, apps]) => [name, {
    metadata: { main_package: { package: name, apps }, injected_packages: {} },
  }]);
  return JSON.stringify({ pipx_spec_version: "0.1", venvs: Object.fromEntries(entries) });
}
const EMPTY_PIPX = pipxList({});

// `responses` maps a command line (or a prefix of it) to a result; the longest match wins.
// Unknown commands succeed, except a bare `pipx` (missing by default) and pythons (3.13 by default).
function fakeDeps({ responses = {}, platform = "linux", nodeVersion = "23.11.1", env, exists } = {}) {
  const calls = [];
  const out = [];
  const err = [];
  const defaults = {
    "pipx --version": { error: new Error("ENOENT") },
    "python3 -c": { status: 0, stdout: "3.13\n" },
    "python -c": { status: 0, stdout: "3.13\n" },
    "py -3 -c": { status: 0, stdout: "3.13\n" },
    "python3 -m pipx --version": { status: 0, stdout: "1.7.1\n" },
    "py -3 -m pipx --version": { status: 0, stdout: "1.7.1\n" },
    "python3 -m pipx environment": { status: 0, stdout: "/home/u/.local/bin\n" },
    "py -3 -m pipx environment": { status: 0, stdout: "C:\\Users\\u\\.local\\bin\n" },
    "pipx list --json": { status: 0, stdout: EMPTY_PIPX },
    "python3 -m pipx list --json": { status: 0, stdout: EMPTY_PIPX },
    "py -3 -m pipx list --json": { status: 0, stdout: EMPTY_PIPX },
  };
  const table = { ...defaults, ...responses };
  return {
    calls,
    out,
    err,
    deps: {
      run(file, args, opts) {
        const line = [file === EXEC ? "node" : file, ...args].join(" ");
        calls.push({ line, opts });
        const key = Object.keys(table).filter((k) => line === k || line.startsWith(k + " "))
          .sort((a, b) => b.length - a.length)[0];
        return key ? { status: 0, ...table[key] } : { status: 0, stdout: "" };
      },
      log: (s) => out.push(s),
      warn: (s) => err.push(s),
      env: env ?? { npm_execpath: NPM_CLI },
      platform,
      execPath: EXEC,
      nodeVersion,
      exists: exists ?? ((p) => p.endsWith("pyproject.toml")),
      cliDir: CLI_DIR,
    },
  };
}

const lines = (calls) => calls.map((c) => c.line);
const installs = (calls) => lines(calls).filter((l) => !/--version$|^\S+( -3)? -c |environment --value|list --json$/.test(l));
const text = (arr) => arr.join("\n");

test("default: skills, jevgrep, ai CLI from this package, jg@latest, then only `ai auth` is left", () => {
  const { deps, calls, out, err } = fakeDeps();
  assert.equal(main(["-a", "claude-code", "-y", "-g"], deps), 0);
  assert.deepEqual(installs(calls), [
    `node ${NPM_CLI} exec --yes -- skills add 0x01001/ai-skill --skill * -a claude-code -y -g`,
    `node ${NPM_CLI} exec --yes -- skills add dzhng/jevgrep --skill jevgrep -a claude-code -y -g`,
    `python3 -m pipx install ${CLI_DIR}`,
    "python3 -m pipx ensurepath",
    `node ${NPM_CLI} install --global @dzhng/jevgrep@latest`, // never pinned: upstream owns updates
  ]);
  assert.match(text(out), /Next: open a new terminal and run `ai auth`/);
  assert.match(text(out), /[\\/]home[\\/]u[\\/]\.local[\\/]bin[\\/]ai" auth/);
  assert.match(text(out), /npx skills update -g/);
  assert.equal(err.length, 0);
});

test("pipx on PATH is used directly", () => {
  const { deps, calls } = fakeDeps({ responses: { "pipx --version": { status: 0, stdout: "1.7.1" } } });
  assert.equal(main(["-g"], deps), 0);
  assert.ok(lines(calls).includes(`pipx install ${CLI_DIR}`));
  assert.ok(!lines(calls).some((l) => l.startsWith("python3")), "python is not probed when pipx exists");
});

test("no pipx anywhere: pipx is installed with pip --user, then used", () => {
  let installed = false;
  const { deps, calls } = fakeDeps();
  const run = deps.run;
  deps.run = (file, args, opts) => {
    const line = [file, ...args].join(" ");
    if (line === "python3 -m pip install --user pipx") installed = true;
    if (line === "python3 -m pipx --version" && !installed) {
      calls.push({ line, opts });
      return { status: 1, stdout: "" };
    }
    return run(file, args, opts);
  };
  assert.equal(main(["-g"], deps), 0);
  const l = lines(calls);
  assert.ok(l.indexOf("python3 -m pip install --user pipx") < l.indexOf(`python3 -m pipx install ${CLI_DIR}`));
});

test("pipx cannot be installed: warning with the manual command, jg still installed, exit 0", () => {
  const { deps, calls, err, out } = fakeDeps({
    responses: { "python3 -m pipx --version": { status: 1 }, "python3 -m pip": { status: 1 } },
  });
  assert.equal(main(["-g"], deps), 0);
  assert.ok(!lines(calls).some((l) => l.includes("pipx install")));
  assert.ok(lines(calls).includes(`node ${NPM_CLI} install --global ${JG_PACKAGE}`));
  assert.match(text(err), /Jev setup incomplete/);
  assert.match(text(err), /ai CLI: pipx could not be installed.*pipx install "git\+https:\/\/github.com\/0x01001\/ai-skill#subdirectory=cli"/);
  assert.doesNotMatch(text(out), /Next: open a new terminal/);
});

test("python too old or missing: the CLI is skipped with a clear reason", () => {
  for (const responses of [
    { "python3 -c": { status: 0, stdout: "3.8\n" }, "python -c": { status: 0, stdout: "2.7\n" } },
    { "python3 -c": { error: new Error("ENOENT") }, "python -c": { status: 9009 } },
  ]) {
    const { deps, calls, err } = fakeDeps({ responses });
    assert.equal(main(["-g"], deps), 0);
    assert.ok(!lines(calls).some((l) => l.includes("pipx install") || l.includes("pip install")));
    assert.match(text(err), /ai CLI: Python >= 3\.9 not found/);
  }
});

test("ai-skill-jev already installed by pipx: refreshed with --force", () => {
  const { deps, calls, out } = fakeDeps({
    responses: { "python3 -m pipx list --json": { stdout: pipxList({ [CLI_PACKAGE]: ["ai", "jg"], other: ["x"] }) } },
    exists: () => true, // its launchers are on disk, and they are ours
  });
  assert.equal(main(["-g"], deps), 0);
  assert.ok(lines(calls).includes(`python3 -m pipx install --force ${CLI_DIR}`));
  assert.match(text(out), /run `ai auth`/);
});

test("`ai` or `jg` from another pipx package: never overwritten, warning, jg engine still installed", () => {
  for (const [venvs, owner] of [[{ "other-ai": ["ai", "aictl"] }, "other-ai"], [{ jgtool: ["jg"] }, "jgtool"],
    [{ [CLI_PACKAGE]: ["ai", "jg"], jgtool: ["jg"] }, "jgtool"]]) {
    const { deps, calls, err, out } = fakeDeps({ responses: { "python3 -m pipx list --json": { stdout: pipxList(venvs) } } });
    assert.equal(main(["-g"], deps), 0);
    assert.ok(!lines(calls).some((l) => l.includes("pipx install") || l.includes("ensurepath")), owner);
    assert.match(text(err), new RegExp(`ai CLI: not overwriting other commands: .*pipx package ${owner}`));
    assert.match(text(err), /remove or rename those, then: pipx install "git\+/);
    assert.ok(lines(calls).includes(`node ${NPM_CLI} install --global ${JG_PACKAGE}`));
    assert.doesNotMatch(text(out), /Next: open a new terminal/);
  }
});

test("a launcher file pipx does not manage (an npm jg in the same bin dir): not overwritten", () => {
  const foreign = join(LINUX_BIN, "jg");
  const { deps, calls, err } = fakeDeps({ exists: (p) => p.endsWith("pyproject.toml") || p === foreign });
  assert.equal(main(["-g"], deps), 0);
  assert.ok(!lines(calls).some((l) => l.includes("pipx install")));
  assert.ok(text(err).includes(`${foreign} exists and is not managed by pipx`));
});

test("`pipx list --json` unreadable: plain install when no launcher exists, skip when one does", () => {
  for (const listed of [{ status: 1 }, { stdout: "not json" }, { stdout: "{}" }]) {
    const fresh = fakeDeps({ responses: { "python3 -m pipx list --json": listed } });
    assert.equal(main(["-g"], fresh.deps), 0);
    assert.ok(lines(fresh.calls).includes(`python3 -m pipx install ${CLI_DIR}`), "no --force without a known owner");
    const taken = fakeDeps({ responses: { "python3 -m pipx list --json": listed }, exists: () => true });
    assert.equal(main(["-g"], taken.deps), 0);
    assert.ok(!lines(taken.calls).some((l) => l.includes("pipx install")));
    assert.match(text(taken.err), /ai exists and its owner is unknown/);
  }
});

test("Windows: pipx apps listed as ai.exe/JG.EXE are matched to their owner", () => {
  const list = (venvs) => ({ "py -3 -m pipx list --json": { stdout: pipxList(venvs) } });
  const ours = fakeDeps({ platform: "win32", env: {}, responses: list({ [CLI_PACKAGE]: ["ai.exe", "JG.EXE"] }), exists: () => true });
  assert.equal(main(["-g"], ours.deps), 0);
  assert.ok(lines(ours.calls).includes(`py -3 -m pipx install --force ${CLI_DIR}`));
  const theirs = fakeDeps({ platform: "win32", env: {}, responses: list({ other: ["AI.exe"] }) });
  assert.equal(main(["-g"], theirs.deps), 0);
  assert.ok(!lines(theirs.calls).some((l) => l.includes("pipx install")));
  assert.match(text(theirs.err), /`ai` belongs to pipx package other/);
  const exe = join("C:\\Users\\u\\.local\\bin", "jg.exe");
  const file = fakeDeps({ platform: "win32", env: {}, exists: (p) => p.endsWith("pyproject.toml") || p === exe });
  assert.equal(main(["-g"], file.deps), 0);
  assert.ok(text(file.err).includes(`${exe} exists and is not managed by pipx`));
});

test("CLI_PACKAGE and LAUNCHERS match cli/pyproject.toml", async () => {
  const { readFileSync } = await import("node:fs");
  const { LAUNCHERS } = await import("../bin/lib.mjs");
  const toml = readFileSync(join(ROOT, "cli", "pyproject.toml"), "utf8");
  assert.match(toml, new RegExp(`^name = "${CLI_PACKAGE}"$`, "m"));
  const scripts = toml.split("[project.scripts]")[1].split(/^\[/m)[0];
  assert.deepEqual([...scripts.matchAll(/^(\w+) = /gm)].map((m) => m[1]).sort(), [...LAUNCHERS].sort());
});

test("pipx install fails: no ensurepath, warning, exit 0", () => {
  const { deps, calls, err } = fakeDeps({ responses: { "python3 -m pipx install": { status: 1 } } });
  assert.equal(main(["-g"], deps), 0);
  assert.ok(!lines(calls).includes("python3 -m pipx ensurepath"));
  assert.match(text(err), /ai CLI: pipx install failed/);
});

test("ensurepath fails: CLI still counts as installed, the PATH step is reported", () => {
  const { deps, err, out } = fakeDeps({ responses: { "python3 -m pipx ensurepath": { status: 1 } } });
  assert.equal(main(["-g"], deps), 0);
  assert.match(text(err), /PATH: pipx ensurepath failed\. Run: pipx ensurepath/);
  assert.match(text(out), /run `ai auth`/);
});

test("Node older than 22: jg is skipped with the reason, the CLI is still installed", () => {
  const { deps, calls, err, out } = fakeDeps({ nodeVersion: "20.18.0" });
  assert.equal(main(["-g"], deps), 0);
  assert.ok(!lines(calls).some((l) => l.includes(JG_PACKAGE)));
  assert.match(text(err), /jg: Node >= 22 needed \(this is 20\.18\.0\)/);
  assert.match(text(out), /run `ai auth`/);
});

test("npm install of jg fails: warning with the manual command, exit 0", () => {
  const { deps, err } = fakeDeps({ responses: { [`node ${NPM_CLI} install`]: { status: 1 } } });
  assert.equal(main(["-g"], deps), 0);
  assert.match(text(err), new RegExp(`jg: npm install failed\\. Run: npm install --global ${JG_PACKAGE}`));
});

test("--no-jev skips the jevgrep skill, the CLI and jg, and is not passed to skills add", () => {
  const { deps, calls, out } = fakeDeps();
  assert.equal(main(["-g", "--no-jev"], deps), 0);
  assert.deepEqual(lines(calls), [`node ${NPM_CLI} exec --yes -- skills add 0x01001/ai-skill --skill * -g`]);
  assert.doesNotMatch(text(out), /ai auth/);
});

test("--no-jev is the only installer flag: --no-jevgrep is gone, help names only --no-jev", () => {
  assert.deepEqual(parseArgs(["-g", "--no-jevgrep"]).passThrough, ["-g", "--no-jevgrep"]);
  assert.equal(parseArgs(["--no-jevgrep"]).withJev, true);
  const { deps, out } = fakeDeps();
  main(["--help"], deps);
  assert.doesNotMatch(text(out), /--no-jevgrep|--list/);
  assert.match(text(out), /--no-jev /);
});

// `--list` is a `skills add` flag, not an installer option; listing must still install nothing.
test("--list only lists: nothing is installed for Jev", () => {
  const { deps, calls } = fakeDeps();
  assert.equal(main(["--list"], deps), 0);
  assert.equal(calls.length, 2);
  assert.ok(lines(calls).every((l) => l.includes("skills add") && l.endsWith("--list")));
});

test("the ai-skill skills fail: exit code kept, jevgrep and Jev skipped", () => {
  const { deps, calls } = fakeDeps({ responses: { [`node ${NPM_CLI} exec --yes -- skills add 0x01001/ai-skill`]: { status: 5 } } });
  assert.equal(main(["-g"], deps), 5);
  assert.equal(calls.length, 1);
});

test("the jevgrep skill fails: exit code kept, Jev setup still runs", () => {
  const { deps, calls } = fakeDeps({ responses: { [`node ${NPM_CLI} exec --yes -- skills add dzhng/jevgrep`]: { status: 1 } } });
  assert.equal(main(["-g"], deps), 1);
  assert.ok(lines(calls).includes(`python3 -m pipx install ${CLI_DIR}`));
});

test("cli/ missing from the package: warning with the git install command", () => {
  const { deps, calls, err } = fakeDeps({ exists: (p) => !p.endsWith("pyproject.toml") });
  assert.equal(main(["-g"], deps), 0);
  assert.ok(!lines(calls).some((l) => l.includes("pipx")));
  assert.match(text(err), /cli\/ is missing/);
});

test("Windows without npm_execpath: py -3 first, npm and npx through the shell", () => {
  const { deps, calls } = fakeDeps({ platform: "win32", env: {} });
  assert.equal(main(["-g"], deps), 0);
  const byLine = Object.fromEntries(calls.map((c) => [c.line, c.opts]));
  assert.equal(byLine["npx --yes skills add 0x01001/ai-skill --skill * -g"].shell, true);
  assert.equal(byLine[`npm install --global ${JG_PACKAGE}`].shell, true);
  assert.equal(byLine[`py -3 -m pipx install ${CLI_DIR}`].shell, false);
  assert.ok(!lines(calls).some((l) => l.startsWith("python")), "py -3 answered first");
});

test("run through npx (npx-cli.js): jg is installed with the sibling npm-cli.js", () => {
  const npx = "/node/lib/npm/bin/npx-cli.js";
  const { deps, calls } = fakeDeps({ env: { npm_execpath: npx }, exists: (p) => /(pyproject\.toml|npm-cli\.js)$/.test(p) });
  assert.equal(main(["-g"], deps), 0);
  assert.ok(lines(calls).includes(`node ${npx} --yes skills add 0x01001/ai-skill --skill * -g`));
  assert.ok(lines(calls).includes(`node ${join(dirname(npx), "npm-cli.js")} install --global ${JG_PACKAGE}`));
});

test("--skill is rejected; help names the Jev flags", () => {
  assert.ok(parseArgs(["--skill", "x"]).error);
  assert.ok(parseArgs(["--skill=x"]).error);
  const r = spawnSync(process.execPath, [join(ROOT, "bin", "install.mjs"), "--help"], { encoding: "utf8" });
  assert.equal(r.status, 0);
  assert.match(r.stdout, /--no-jev /);
  assert.match(r.stdout, /ai auth/);
  const bad = spawnSync(process.execPath, [join(ROOT, "bin", "install.mjs"), "-s", "x"], { encoding: "utf8" });
  assert.equal(bad.status, 2);
});

test("the npm package ships cli/ for pipx, without tests or caches", () => {
  const r = spawnSync(process.platform === "win32" ? "npm.cmd" : "npm", ["pack", "--dry-run", "--json"],
    { cwd: ROOT, encoding: "utf8", shell: process.platform === "win32" });
  assert.equal(r.status, 0, r.stderr);
  const files = JSON.parse(r.stdout)[0].files.map((f) => f.path.replace(/\\/g, "/"));
  for (const need of ["bin/install.mjs", "bin/lib.mjs", "cli/pyproject.toml", "cli/README.md",
    "cli/src/ai_skill_jev/cli.py", "cli/src/ai_skill_jev/jg_shim.py"]) {
    assert.ok(files.includes(need), `missing ${need}`);
  }
  assert.ok(!files.some((f) => f.startsWith("cli/tests/") || f.includes("__pycache__") || f.includes(".egg-info")),
    files.filter((f) => f.startsWith("cli/")).join(", "));
  assert.ok(!files.some((f) => f.startsWith("test/") || f.startsWith("plan/")));
});
