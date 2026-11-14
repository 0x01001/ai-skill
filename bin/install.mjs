#!/usr/bin/env node
// One-command installer: `npx -y github:0x01001/ai-skill [skills add flags] [--no-jev]`.
// Installs the skills (`npx skills add` runs once per source: it takes a single source and has no
// dependency mechanism), then the optional Jev parts: the `ai` CLI + `jg` shim and the `jg` engine.
// After it, only `ai auth` is left. The logic lives in lib.mjs.
import { main, realDeps } from "./lib.mjs";

process.exit(main(process.argv.slice(2), realDeps()));
