"""The `jg` engine the shim runs.

ai-skill keeps its own copy of the engine (`npm install --prefix <engine dir> @dzhng/jevgrep`),
outside PATH, so nothing named `jg` can shadow the shim and removing a global npm `jg` never
breaks Jev. The engine dir is:

- `AI_SKILL_ENGINE_DIR` when set to an absolute path,
- Windows: `%LOCALAPPDATA%\\ai-skill\\engine`,
- otherwise: `${XDG_DATA_HOME:-~/.local/share}/ai-skill/engine`.

The installer (bin/lib.mjs, `engineDir`) computes the same path. A `jg` found on PATH (the global
npm one of older installs) is still used when the private engine is missing.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .jg_runner import NPM_ENTRY, jg_command
from .pathenv import lookup_jg

ENGINE_PACKAGE = "@dzhng/jevgrep"
ENGINE_DIR_ENV = "AI_SKILL_ENGINE_DIR"
UNINSTALL_GLOBAL_COMMAND = f"npm uninstall --global {ENGINE_PACKAGE}"


def _abs(env, name: str) -> Optional[Path]:
    value = env.get(name)
    return Path(value) if value and os.path.isabs(value) else None


def engine_dir(env=None) -> Optional[Path]:
    env = os.environ if env is None else env
    override = _abs(env, ENGINE_DIR_ENV)
    if override is not None:
        return override
    if sys.platform == "win32":
        base = _abs(env, "LOCALAPPDATA")
        return base / "ai-skill" / "engine" if base is not None else None
    base = _abs(env, "XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return base / "ai-skill" / "engine"


def engine_entry(env=None) -> Optional[str]:
    """The engine's `index.js` when the private engine is installed."""
    directory = engine_dir(env)
    if directory is None:
        return None
    entry = directory.joinpath(*NPM_ENTRY)
    return str(entry) if entry.is_file() else None


def install_command(env=None) -> str:
    directory = engine_dir(env)
    where = f'"{directory}"' if directory is not None else '"<engine dir>"'
    return f"npm install --prefix {where} {ENGINE_PACKAGE}@latest"


def is_npm_package_jg(path: Optional[str]) -> bool:
    """True when `path` is a `jg` launcher that npm installed for @dzhng/jevgrep (global or prefix)."""
    if not path:
        return False
    directory = os.path.dirname(os.path.abspath(path))
    package = os.path.join("node_modules", *ENGINE_PACKAGE.split("/"))
    if os.path.isdir(os.path.join(directory, package)):            # Windows npm layout
        return True
    if os.path.isdir(os.path.join(os.path.dirname(directory), "lib", package)):  # POSIX prefix/bin
        return True
    real = os.path.normcase(os.path.realpath(path))
    return os.path.normcase(package) in real


@dataclass
class Engine:
    cmd: List[str]      # argv prefix that runs it
    path: str           # what to show the user
    private: bool       # ai-skill's own copy (True) or a `jg` found on PATH (False)


def find_engine(env=None) -> Optional[Engine]:
    """The engine the shim runs: the private one first, else the first non-shim `jg` on PATH."""
    env = os.environ if env is None else env
    entry = engine_entry(env)
    if entry is not None:
        node = shutil.which("node", path=env.get("PATH"))
        if node:
            return Engine([node, entry], entry, True)
    real = lookup_jg(env).real
    if real is not None:
        return Engine(jg_command(real, env), real, False)
    return None
