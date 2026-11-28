// Static checks: skills call host operations from hosts.md, never Claude Code tools directly.
// Run: npm test   (node --test test/install.test.mjs test/skills-host.test.mjs)
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");
const SKILLS = ["ai-main", "ai-auto", "ai-orchestrate"];
const skillPath = (name) => `skills/${name}/SKILL.md`;
const HOSTS = "skills/ai-orchestrate/references/hosts.md";

function frontmatter(text) {
  const lines = text.split(/\r?\n/);
  assert.equal(lines[0], "---");
  const end = lines.indexOf("---", 1);
  assert.ok(end > 0, "frontmatter is closed");
  return lines.slice(1, end).map((line) => {
    const i = line.indexOf(":");
    return [line.slice(0, i), line.slice(i + 1).trim()];
  });
}

for (const name of SKILLS) {
  test(`${name}: frontmatter`, () => {
    const entries = frontmatter(read(skillPath(name)));
    for (const key of ["name", "description", "compatibility"]) {
      assert.equal(entries.filter(([k]) => k === key).length, 1, `${key} appears once`);
    }
    const get = (key) => entries.find(([k]) => k === key)?.[1];
    assert.equal(get("name"), name);
    for (const [key, value] of entries) {
      assert.ok(value.startsWith('"') || !value.includes(": "), `${key}: unquoted value contains ": "`);
    }
    const compat = get("compatibility");
    assert.ok(compat.length <= 500, "compatibility <= 500 chars");
    assert.ok(compat.length >= 2 && compat.startsWith('"') && compat.endsWith('"'), "compatibility is quoted");
    assert.ok(!compat.slice(1, -1).includes('"'), "no inner quote in compatibility");
    assert.ok(get("argument-hint")?.includes("--host"), "argument-hint has --host");
  });

  test(`${name}: no Claude Code tool names`, () => {
    const text = read(skillPath(name));
    for (const banned of ["Skill(skill:", "AskUserQuestion", "SendMessage", "ListAgents", "claude --bg", ".claude/skills/ai-orchestrate"]) {
      assert.ok(!text.includes(banned), `contains ${banned}`);
    }
  });
}

test("hosts.md has every operation and host id", () => {
  const text = read(HOSTS);
  for (const op of ["ask", "handoff", "spawn-subagent", "spawn-sub-session", "wait", "talk", "stop", "usage"]) {
    assert.match(text, new RegExp(`^### ${op}$`, "m"), `### ${op}`);
  }
  for (const id of ["claude-code", "opencode", "omp", "cursor", "codex", "generic"]) {
    assert.ok(text.includes(`\`${id}\``), id);
  }
});

test("relative markdown links resolve", () => {
  for (const rel of [...SKILLS.map(skillPath), HOSTS]) {
    for (const [, target] of read(rel).matchAll(/\]\(([^)\s]+)\)/g)) {
      if (/^https?:\/\//.test(target)) continue;
      const path = target.split("#")[0];
      if (!path) continue;
      assert.ok(existsSync(join(ROOT, dirname(rel), path)), `${rel} -> ${target}`);
    }
  }
});

test("ai-main agents/openai.yaml disables implicit invocation", () => {
  const lines = read("skills/ai-main/agents/openai.yaml").split(/\r?\n/).filter((l) => l !== "");
  assert.deepEqual(lines, ["policy:", "  allow_implicit_invocation: false"]);
});

test("sub-sessions.md uses the skill-root sessions.py path", () => {
  assert.ok(!read("skills/ai-orchestrate/references/sub-sessions.md").includes(".claude/skills/ai-orchestrate"));
});

test("ai-orchestrate job table has a runtime row", () => {
  assert.match(read(skillPath("ai-orchestrate")), /^\| runtime \|/m);
});

test("worker brief carries Host and Worker runtime", () => {
  const text = read("skills/ai-orchestrate/references/worker-brief.md");
  assert.ok(text.includes("Host:") && text.includes("Worker runtime:"));
});

test("npm test runs both suites", () => {
  assert.equal(JSON.parse(read("package.json")).scripts.test, "node --test test/install.test.mjs test/skills-host.test.mjs");
});
