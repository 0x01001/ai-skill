// Logic of the one-command installer (bin/install.mjs runs `main`). Every external command goes
// through `deps.run`, so tests drive the whole flow with fakes and nothing is installed for real.
//
// Steps:
//   1. `npx skills add 0x01001/ai-skill` (required) and `npx skills add dzhng/jevgrep` (optional).
//   2. Jev setup, best effort: the `ai` CLI + `jg` shim (pipx, from this package's own cli/) and
//      ai-skill's own `jg` engine (`npm install --prefix <engine dir> @dzhng/jevgrep@latest`, outside
//      PATH, so nothing can shadow the shim). A global `@dzhng/jevgrep` left by an older install is
//      then removed: it would hide the shim on PATH and is no longer used. A failure here only
//      prints a warning with the manual command: the skills work without Jev.
// Jev stays off until the user runs `ai auth` and enters a key.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, posix, resolve, win32 } from "node:path";
import { fileURLToPath } from "node:url";

export const SOURCES = [
  { label: "ai-skill", args: ["0x01001/ai-skill", "--skill", "*"] },
  { label: "jevgrep", args: ["dzhng/jevgrep", "--skill", "jevgrep"], optional: true },
];
export const ENGINE_PACKAGE = "@dzhng/jevgrep";
export const JG_PACKAGE = `${ENGINE_PACKAGE}@latest`;
export const ENGINE_DIR_ENV = "AI_SKILL_ENGINE_DIR";
export const CLI_GIT_URL = "git+https://github.com/0x01001/ai-skill#subdirectory=cli";
export const CLI_PACKAGE = "ai-skill-jev"; // [project] name in cli/pyproject.toml
export const LAUNCHERS = ["ai", "jg"]; // its console scripts
export const MIN_PYTHON = [3, 9];
export const MIN_NODE_FOR_JG = 22;
const OWN_FLAGS = ["--no-jev"];

export const HELP = `Usage: npx -y github:0x01001/ai-skill [options]

Installs, in one go:
  - the skills ai-main, ai-auto, ai-orchestrate (0x01001/ai-skill) and jevgrep (dzhng/jevgrep)
  - the \`ai\` CLI with its \`jg\` shim (pipx; Python >= 3.9, pipx itself is installed if missing)
  - ai-skill's own jg engine: npm install --prefix <engine dir> ${JG_PACKAGE} (Node >= ${MIN_NODE_FOR_JG}),
    outside PATH (Windows: %LOCALAPPDATA%\\ai-skill\\engine, else ~/.local/share/ai-skill/engine).
    A global ${ENGINE_PACKAGE} from an older install is removed: it would hide the shim.
Then run \`ai auth\` in a new terminal and enter your Jev key. Without a key Jev stays off.

Every option except the ones below is passed to both \`npx skills add\` runs
(e.g. -g, -y, -a claude-code, --copy). The CLI and jg are installed per user either way.

  --no-jev       Skip everything Jev: the jevgrep skill, the ai CLI and jg
  -h, --help     Show this help

Update later with: npx skills update [-g]   (rerun this command to update the ai CLI too)`;

export function parseArgs(argv) {
  if (argv.includes("-h") || argv.includes("--help")) return { help: true };
  if (argv.some((a) => a === "-s" || a === "--skill" || a.startsWith("--skill="))) {
    return { error: "--skill is set per source; use --no-jev to skip the Jev parts." };
  }
  const withJev = !argv.includes("--no-jev");
  const passThrough = argv.filter((a) => !OWN_FLAGS.includes(a));
  return {
    withJev,
    passThrough,
    list: passThrough.includes("--list") || passThrough.includes("-l"),
    global: passThrough.includes("-g") || passThrough.includes("--global"),
  };
}

// ----------------------------------------------------------------------------- commands

const show = (c, extra = []) => [c.file === process.execPath ? "node" : c.file, ...c.args, ...extra]
  .map((a) => (/\s/.test(a) ? `"${a}"` : a)).join(" ");

function exec(deps, c, extra = [], opts = {}) {
  return deps.run(c.file, [...c.args, ...extra], { shell: !!c.shell, capture: !!opts.capture });
}

const ok = (r) => !r.error && r.status === 0;

function npmCli(deps) {
  const p = deps.env.npm_execpath;
  if (p && /npm-cli\.js$/.test(p)) return { file: deps.execPath, args: [p] };
  if (p && /npx-cli\.js$/.test(p)) {
    const sibling = join(dirname(p), "npm-cli.js");
    if (deps.exists(sibling)) return { file: deps.execPath, args: [sibling] };
  }
  return { file: "npm", args: [], shell: deps.platform === "win32" };
}

// Prefer npm's own CLI (set when run via npx/npm) so no shell is involved and "*" is not globbed.
function npxCommand(deps) {
  const p = deps.env.npm_execpath;
  if (p && /npm-cli\.js$/.test(p)) return { file: deps.execPath, args: [p, "exec", "--yes", "--"] };
  if (p && /npx-cli\.js$/.test(p)) return { file: deps.execPath, args: [p, "--yes"] };
  return { file: "npx", args: ["--yes"], shell: deps.platform === "win32" };
}

function pythonCandidates(platform) {
  return platform === "win32"
    ? [{ file: "py", args: ["-3"] }, { file: "python", args: [] }, { file: "python3", args: [] }]
    : [{ file: "python3", args: [] }, { file: "python", args: [] }];
}

export function findPython(deps) {
  for (const c of pythonCandidates(deps.platform)) {
    const r = exec(deps, c, ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"], { capture: true });
    if (!ok(r)) continue;
    const [major, minor] = String(r.stdout || "").trim().split(".").map(Number);
    if (major === MIN_PYTHON[0] && minor >= MIN_PYTHON[1]) return c;
  }
  return null;
}

// pipx on PATH, else `python -m pipx`, else install it with `python -m pip install --user pipx`.
export function findPipx(deps) {
  const direct = { file: "pipx", args: [] };
  if (ok(exec(deps, direct, ["--version"], { capture: true }))) return { pipx: direct };
  const python = findPython(deps);
  if (!python) return { problem: `Python >= ${MIN_PYTHON.join(".")} not found` };
  const viaPython = { file: python.file, args: [...python.args, "-m", "pipx"] };
  if (ok(exec(deps, viaPython, ["--version"], { capture: true }))) return { pipx: viaPython };
  const pipInstall = ["-m", "pip", "install", "--user", "pipx"];
  deps.log(`\n> ${show(python, pipInstall)}`);
  if (ok(exec(deps, python, pipInstall)) && ok(exec(deps, viaPython, ["--version"], { capture: true }))) {
    return { pipx: viaPython };
  }
  return { problem: "pipx could not be installed" };
}

// ----------------------------------------------------------------------------- jev setup

const appName = (name, platform) => {
  const base = String(name).split(/[\\/]/).pop();
  return platform === "win32" ? base.toLowerCase().replace(/\.exe$/, "") : base;
};

// Which pipx venv exposes each app, from `pipx list --json`; null when that cannot be read.
function pipxApps(deps, pipx) {
  const r = exec(deps, pipx, ["list", "--json"], { capture: true });
  if (!ok(r)) return null;
  let venvs;
  try {
    venvs = JSON.parse(String(r.stdout || "")).venvs;
  } catch {
    return null;
  }
  if (!venvs || typeof venvs !== "object") return null;
  const owners = new Map();
  for (const [venv, info] of Object.entries(venvs)) {
    const meta = info?.metadata ?? {};
    for (const pkg of [meta.main_package, ...Object.values(meta.injected_packages ?? {})]) {
      for (const app of pkg?.apps ?? []) owners.set(appName(app, deps.platform), venv);
    }
  }
  return { installed: Object.hasOwn(venvs, CLI_PACKAGE), owners };
}

// `ai`/`jg` launchers that belong to someone else: another pipx package, or a file in the pipx
// bin dir that pipx does not manage (an npm `jg` under the same prefix, say). Never overwritten.
function launcherConflicts(deps, binDir, apps) {
  const conflicts = [];
  for (const name of LAUNCHERS) {
    const owner = apps?.owners.get(name);
    if (owner === CLI_PACKAGE) continue;
    if (owner) {
      conflicts.push(`\`${name}\` belongs to pipx package ${owner}`);
      continue;
    }
    const files = deps.platform === "win32" ? [`${name}.exe`, name] : [name];
    const file = binDir && files.map((f) => join(binDir, f)).find((p) => deps.exists(p));
    if (file) conflicts.push(`${file} exists and ${apps ? "is not managed by pipx" : "its owner is unknown"}`);
  }
  return conflicts;
}

function installCli(deps) {
  const manual = `pipx install "${CLI_GIT_URL}"`;
  if (!deps.exists(join(deps.cliDir, "pyproject.toml"))) {
    return { problem: { what: "ai CLI", why: "cli/ is missing from this package", manual } };
  }
  const found = findPipx(deps);
  if (!found.pipx) {
    return { problem: { what: "ai CLI", why: found.problem, manual: `install Python >= 3.9 and pipx, then: ${manual}` } };
  }
  const pipx = found.pipx;
  const bin = exec(deps, pipx, ["environment", "--value", "PIPX_BIN_DIR"], { capture: true });
  const binDir = ok(bin) ? String(bin.stdout || "").trim() : "";
  const apps = pipxApps(deps, pipx);
  const conflicts = launcherConflicts(deps, binDir, apps);
  if (conflicts.length) {
    return { problem: { what: "ai CLI", why: `not overwriting other commands: ${conflicts.join("; ")}`,
      manual: `remove or rename those, then: ${manual}` } };
  }
  // --force only refreshes our own venv (its `ai`/`jg` were checked above); a fresh install never forces.
  const install = apps?.installed ? ["install", "--force", deps.cliDir] : ["install", deps.cliDir];
  deps.log(`\n> ${show(pipx, install)}`);
  if (!ok(exec(deps, pipx, install))) {
    return { problem: { what: "ai CLI", why: "pipx install failed", manual } };
  }
  deps.log(`\n> ${show(pipx, ["ensurepath"])}`);
  const pathOk = ok(exec(deps, pipx, ["ensurepath"]));
  return { pipx, binDir, problem: pathOk ? null : { what: "PATH", why: "pipx ensurepath failed", manual: "pipx ensurepath" } };
}

// Same rule as cli/src/ai_skill_jev/engine.py `engine_dir`; null when it cannot be computed.
export function engineDir(deps) {
  const env = deps.env ?? {};
  const p = deps.platform === "win32" ? win32 : posix;
  const abs = (v) => (v && p.isAbsolute(v) ? v : null);
  const override = abs(env[ENGINE_DIR_ENV]);
  if (override) return override;
  if (deps.platform === "win32") {
    const local = abs(env.LOCALAPPDATA);
    return local ? p.join(local, "ai-skill", "engine") : null;
  }
  const data = abs(env.XDG_DATA_HOME) ?? (deps.home ? p.join(deps.home, ".local", "share") : null);
  return data ? p.join(data, "ai-skill", "engine") : null;
}

function installJg(deps) {
  const dir = engineDir(deps);
  const manual = `npm install --prefix "${dir ?? "<engine dir>"}" ${JG_PACKAGE}`;
  const major = Number(String(deps.nodeVersion).split(".")[0]);
  if (!(major >= MIN_NODE_FOR_JG)) {
    return { what: "jg", why: `Node >= ${MIN_NODE_FOR_JG} needed (this is ${deps.nodeVersion})`, manual: `upgrade Node, then: ${manual}` };
  }
  if (!dir) {
    return { what: "jg", why: "the engine dir cannot be found (LOCALAPPDATA / HOME not set)", manual };
  }
  const npm = npmCli(deps);
  const args = ["install", "--prefix", dir, "--no-audit", "--no-fund", JG_PACKAGE];
  deps.log(`\n> ${show(npm, args)}`);
  return ok(exec(deps, npm, args)) ? null : { what: "jg", why: "npm install failed", manual };
}

// A global @dzhng/jevgrep (installed by ai-skill < 1.0.0, or by the jevgrep skill) puts an npm `jg`
// on PATH that can hide the shim; when it sits in the machine PATH only an admin could reorder it.
// ai-skill no longer uses it, so it is removed once the shim and the private engine are in place.
function removeGlobalJg(deps) {
  const npm = npmCli(deps);
  const list = exec(deps, npm, ["ls", "--global", "--depth=0", "--json", ENGINE_PACKAGE], { capture: true });
  let installed = false;
  try {
    installed = Boolean(JSON.parse(String(list.stdout || "{}")).dependencies?.[ENGINE_PACKAGE]);
  } catch {
    return null; // cannot tell: leave it; `ai doctor` reports a shadowed shim with the fix
  }
  if (!installed) return { removed: false, problem: null };
  const args = ["uninstall", "--global", ENGINE_PACKAGE];
  deps.log(`\nRemoving the global ${ENGINE_PACKAGE}: ai-skill runs its own engine, and the global \`jg\` would hide the shim.`);
  deps.log(`> ${show(npm, args)}`);
  // Even a failed uninstall may have removed some links, so the shim is checked either way.
  return ok(exec(deps, npm, args)) ? { removed: true, problem: null }
    : { removed: true, problem: { what: "global jg", why: "npm uninstall failed", manual: `npm uninstall --global ${ENGINE_PACKAGE}` } };
}

// When npm's global bin dir is the pipx bin dir (npm prefix ~/.local, PIPX_BIN_DIR ~/.local/bin),
// `npm uninstall --global` deletes the `jg` path the package declared, even after pipx replaced
// it with the shim. Put the shim back with the same pipx install, or say how to.
function restoreShim(deps, cli) {
  if (!cli.binDir || !cli.pipx) return null;
  const p = deps.platform === "win32" ? win32 : posix;
  const launcher = p.join(cli.binDir, deps.platform === "win32" ? "jg.exe" : "jg");
  if (deps.exists(launcher)) return null;
  const args = ["install", "--force", deps.cliDir];
  deps.log(`\nThe npm uninstall removed the \`jg\` shim (npm and pipx share ${cli.binDir}); reinstalling it.`);
  deps.log(`> ${show(cli.pipx, args)}`);
  if (ok(exec(deps, cli.pipx, args)) && deps.exists(launcher)) return null;
  return { what: "jg shim", why: `${launcher} was removed by npm uninstall and could not be restored`,
    manual: `pipx install --force "${CLI_GIT_URL}"` };
}

export function setupJev(deps) {
  deps.log("\n== Jev (optional): ai CLI + jg. Jev stays off until you run `ai auth`. ==");
  const cli = installCli(deps);
  const jgProblem = installJg(deps);
  const cliOk = !cli.problem || cli.problem.what === "PATH";
  // Only once both are in place: without the shim, the global jg is what the jevgrep skill uses.
  const cleanup = cliOk && !jgProblem ? removeGlobalJg(deps) : null;
  const shimProblem = cleanup?.removed ? restoreShim(deps, cli) : null;
  const problems = [cli.problem, jgProblem, cleanup?.problem, shimProblem].filter(Boolean);
  return { cliInstalled: cliOk, binDir: cli.binDir || "", problems };
}

// ----------------------------------------------------------------------------- main

export function main(argv, deps) {
  const opts = parseArgs(argv);
  if (opts.help) {
    deps.log(HELP);
    return 0;
  }
  if (opts.error) {
    deps.warn(`ai-skill installer: ${opts.error}`);
    return 2;
  }

  const npx = npxCommand(deps);
  let exitCode = 0;
  let requiredFailed = false;
  for (const source of SOURCES) {
    if (source.optional && !opts.withJev) continue;
    const full = ["skills", "add", ...source.args, ...opts.passThrough];
    deps.log(`\n> npx ${full.join(" ")}`);
    const result = exec(deps, npx, full);
    const code = result.error ? 1 : result.status ?? 1;
    if (result.error) deps.warn(`ai-skill installer: ${result.error.message}`);
    if (code !== 0) {
      deps.warn(`ai-skill installer: installing ${source.label} failed (exit ${code}).`);
      exitCode = code;
      if (!source.optional) {
        requiredFailed = true;
        break;
      }
    }
  }
  // `--list` (a skills add flag) only lists: nothing is installed for Jev.
  if (requiredFailed || opts.list) return exitCode;

  const jev = opts.withJev ? setupJev(deps) : null;

  const scope = opts.global ? " -g" : "";
  deps.log(`\nDone. Update the skills later with: npx skills update${scope}`);
  if (!jev) return exitCode;
  if (jev.problems.length) {
    deps.warn("\nJev setup incomplete (the skills work without it). To finish it by hand:");
    for (const p of jev.problems) deps.warn(`  - ${p.what}: ${p.why}. Run: ${p.manual}`);
  }
  if (jev.cliInstalled) {
    const now = jev.binDir ? ` (or right now: "${join(jev.binDir, "ai")}" auth)` : "";
    deps.log(`\nNext: open a new terminal and run \`ai auth\`${now}, then enter your Jev key.`);
    deps.log("Without a key Jev stays off and the skills keep the normal workflow.");
  }
  return exitCode;
}

export function realRun(file, args, { shell = false, capture = false } = {}) {
  const options = { stdio: capture ? ["ignore", "pipe", "pipe"] : "inherit", encoding: "utf8" };
  if (shell) {
    // cmd.exe does not glob "*"; quote every argument for the shell.
    const quoted = args.map((a) => `"${String(a).replace(/"/g, '\\"')}"`);
    return spawnSync(file, quoted, { ...options, shell: true });
  }
  return spawnSync(file, args, options);
}

export function realDeps() {
  return {
    run: realRun,
    log: (s) => console.log(s),
    warn: (s) => console.error(s),
    env: process.env,
    platform: process.platform,
    execPath: process.execPath,
    nodeVersion: process.versions.node,
    home: homedir(),
    exists: existsSync,
    cliDir: resolve(dirname(fileURLToPath(import.meta.url)), "..", "cli"),
  };
}
