// Logic of the one-command installer (bin/install.mjs runs `main`). Every external command goes
// through `deps.run`, so tests drive the whole flow with fakes and nothing is installed for real.
//
// Steps:
//   1. `npx skills add 0x01001/ai-skill` (required) and `npx skills add dzhng/jevgrep` (optional).
//   2. Jev setup, best effort: the `ai` CLI + `jg` shim (pipx, from this package's own cli/) and the
//      real `jg` engine (`npm install --global @dzhng/jevgrep@latest`). A failure here only prints a
//      warning with the manual command: the skills work without Jev.
// Jev stays off until the user runs `ai auth` and enters a key.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const SOURCES = [
  { label: "ai-skill", args: ["0x01001/ai-skill", "--skill", "*"] },
  { label: "jevgrep", args: ["dzhng/jevgrep", "--skill", "jevgrep"], optional: true },
];
export const JG_PACKAGE = "@dzhng/jevgrep@latest";
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
  - the jg engine: npm install --global ${JG_PACKAGE} (Node >= ${MIN_NODE_FOR_JG})
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
  return { binDir, problem: pathOk ? null : { what: "PATH", why: "pipx ensurepath failed", manual: "pipx ensurepath" } };
}

function installJg(deps) {
  const manual = `npm install --global ${JG_PACKAGE}`;
  const major = Number(String(deps.nodeVersion).split(".")[0]);
  if (!(major >= MIN_NODE_FOR_JG)) {
    return { what: "jg", why: `Node >= ${MIN_NODE_FOR_JG} needed (this is ${deps.nodeVersion})`, manual: `upgrade Node, then: ${manual}` };
  }
  const npm = npmCli(deps);
  const args = ["install", "--global", JG_PACKAGE];
  deps.log(`\n> ${show(npm, args)}`);
  return ok(exec(deps, npm, args)) ? null : { what: "jg", why: "npm install failed", manual };
}

export function setupJev(deps) {
  deps.log("\n== Jev (optional): ai CLI + jg. Jev stays off until you run `ai auth`. ==");
  const cli = installCli(deps);
  const jgProblem = installJg(deps);
  const problems = [cli.problem, jgProblem].filter(Boolean);
  const cliInstalled = !cli.problem || cli.problem.what === "PATH";
  return { cliInstalled, binDir: cli.binDir || "", problems };
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
    exists: existsSync,
    cliDir: resolve(dirname(fileURLToPath(import.meta.url)), "..", "cli"),
  };
}
