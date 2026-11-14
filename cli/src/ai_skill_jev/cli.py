"""`ai auth`, `ai logout`, `ai doctor` (plan 5.2-5.4).

The agent never runs `ai auth` or `ai logout`; it only runs `ai doctor --json` and `jg`.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import warnings
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from . import JG_INSTALL_COMMAND, SCHEMA_VERSION, TOOL_ID, __version__
from . import credentials as cred
from . import menu, pathenv, provider
from .jg_runner import cleanup_orphans, jg_command, jg_version

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


class Abort(Exception):
    """Stop `ai auth` and keep the previous state. The message is safe to print."""


def _hidden_input(prompt: str) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except getpass.GetPassWarning as exc:
            raise Abort("hidden input is not available here; run `ai auth` in a real terminal") from exc


def _isatty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _default_user_env() -> Optional[pathenv.UserEnv]:
    return pathenv.WindowsUserEnv() if sys.platform == "win32" else None


@dataclass
class Deps:
    env: dict = field(default_factory=lambda: os.environ)
    out: object = field(default_factory=lambda: sys.stdout)
    ask: Callable[[str], str] = input
    ask_secret: Callable[[str], str] = _hidden_input
    isatty: Callable[[], bool] = _isatty
    detect_store: Callable[[], Optional[object]] = cred.KeyringStore.detect
    store_by_name: Callable[[str], Optional[object]] = cred.KeyringStore.by_name
    validate: Callable = provider.validate_credentials
    user_env: Callable[[], Optional[pathenv.UserEnv]] = _default_user_env
    shim_dir: Callable[[], Optional[str]] = pathenv.own_shim_dir
    jg_version: Callable = None
    # Arrow-key picker; returns None to fall back to the numbered prompt. Only main() turns it on.
    select: Optional[Callable[[str, List[str], int], Optional[int]]] = None

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def version_of(self, real: str) -> Optional[str]:
        if self.jg_version is not None:
            return self.jg_version(real)
        return jg_version(jg_command(real, self.env), self.env)


# --------------------------------------------------------------------------- jg / PATH state

@dataclass
class JgState:
    jg: str                    # shim | shadowed | missing
    jevgrep: str               # ready | shadowed | jg missing | not configured
    jg_path: Optional[str]
    jg_version: Optional[str]
    hints: List[str]


def jg_state(deps: Deps, active: bool) -> JgState:
    lookup = pathenv.lookup_jg(deps.env)
    version = deps.version_of(lookup.real) if lookup.real else None
    hints = []
    if lookup.real is None:
        jg = "missing"
        hints.append(f"Install the real jg: {JG_INSTALL_COMMAND}")
    elif not lookup.first_is_shim:  # any jg version is fine; `version` is reported, never gated
        jg = "shadowed"
        hints.append("`jg` on PATH is the npm one, not the ai-skill shim; run `ai auth` again "
                     "(it fixes the user PATH) or put the shim dir first in PATH, then open a new shell.")
    else:
        jg = "shim"
    if not active:
        jevgrep = "not configured"
    elif jg == "missing":
        jevgrep = "jg missing"
    elif jg == "shadowed":
        jevgrep = "shadowed"
    else:
        jevgrep = "ready"
    return JgState(jg, jevgrep, lookup.first, version, hints)


def fix_path(deps: Deps) -> tuple:
    """Step 10 of `ai auth`. Returns (jevgrep status, message lines).

    The PATH change is kept by `ai logout` (see pathenv.apply_user_path_fix), so nothing is backed up.
    """
    shim_dir = deps.shim_dir()
    user_env = deps.user_env()
    if user_env is None:
        lookup = pathenv.lookup_jg(deps.env)
        if lookup.first_is_shim:
            return "ready", []
        where = shim_dir or "the pipx bin dir (`pipx environment --value PIPX_BIN_DIR`)"
        return "shadowed", [
            f"`jg` on PATH is not the ai-skill shim. Put {where} before the npm bin dir in PATH "
            "(for example in ~/.profile), then open a new shell."]
    try:
        plan = pathenv.apply_user_path_fix(user_env, shim_dir, deps.env)
    except (OSError, cred.StateError) as exc:
        return "shadowed", [f"Could not update the user PATH ({exc.__class__.__name__}); "
                            "put the shim dir first in PATH yourself."]
    if plan.status == "ready":
        return "ready", []
    if plan.status == "change":
        return "ready", [f"User PATH updated: {shim_dir} now comes first. "
                         "Open a new terminal (and restart Claude Code) to pick it up."]
    if plan.status == "shadowed_machine":
        return "shadowed", [
            f"The npm jg in the machine PATH ({plan.blocking_dir}) comes before every user PATH entry. "
            f"Move {shim_dir} ahead of it in the system PATH (needs admin), then open a new shell."]
    return "shadowed", ["The jg shim was not found next to `ai`; reinstall with pipx."]


# --------------------------------------------------------------------------- ai auth

def _choose(deps: Deps, prompt: str, options: List[str], default: int = 1) -> int:
    if deps.select is not None:
        picked = deps.select(prompt, options, default)
        if picked is not None:
            return picked
    for i, text in enumerate(options, 1):
        deps.say(f"  {i}) {text}")
    while True:
        raw = deps.ask(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw)
        deps.say(f"Enter a number from 1 to {len(options)}.")


def _confirm(deps: Deps, prompt: str) -> bool:
    return deps.ask(prompt).strip().lower() in ("y", "yes")


def _pick_profile(deps: Deps) -> dict:
    labels = []
    for name in provider.PROVIDER_ORDER:
        if name == "custom":
            labels.append("Custom endpoint")
        else:
            labels.append(provider.PRESETS[name]["label"] + (" (recommended)" if name == "typesafe" else ""))
    name = provider.PROVIDER_ORDER[_choose(deps, "Provider", labels) - 1]
    if name == "custom":
        base = deps.ask("HTTPS base URL (for example https://host/v1): ")
        model = deps.ask("Model: ")
        try:
            profile = provider.resolve_profile("custom", base, model)
        except provider.InputError as exc:
            raise Abort(str(exc))
        if provider.is_loopback_http(profile["base_url"]):
            if not _confirm(deps, "This is plain http on localhost. Is it a local proxy you run? [y/N]: "):
                raise Abort("plain http endpoint not confirmed")
    else:
        profile = provider.resolve_profile(name)
    deps.say(f"Endpoint host: {profile['endpoint_host']}   model: {profile['model']}")
    preset = provider.PRESETS.get(name)
    if preset:
        deps.say(f"Cost: {preset['price']}")
    return profile


def cmd_auth(deps: Deps) -> int:
    if not deps.isatty():
        deps.say("ai auth needs an interactive terminal (hidden key input). Run it yourself in a terminal; "
                 "never paste the key into chat.")
        return EXIT_USAGE
    try:
        meta = cred.load_metadata(deps.env)
    except cred.StateError as exc:
        deps.say(f"Cannot read the current state: {exc}. Fix or delete jev.json, then retry.")
        return EXIT_FAIL
    try:
        if cred.is_active(meta):
            deps.say(f"Jev is on: {meta.get('provider')} ({meta.get('endpoint_host')}), model {meta.get('model')}.")
            if _choose(deps, "Choose", ["Change key or provider", "Exit"], default=2) == 2:
                return EXIT_OK
        return _auth_steps(deps, meta)
    except Abort as exc:
        deps.say(f"Stopped: {exc}. Nothing was changed.")
        return EXIT_FAIL
    except (KeyboardInterrupt, EOFError):
        deps.say("\nCancelled.")
        for line in _cancel_report(deps, meta):
            deps.say(line)
        return 130


def _cancel_report(deps: Deps, before: Optional[dict]) -> List[str]:
    """Describe what a cancelled `ai auth` left behind, from the state on disk, not from the step."""
    try:
        after = cred.load_metadata(deps.env)
    except cred.StateError:
        return ["The saved state cannot be read. Run `ai doctor`, then `ai logout` to reset it."]
    if after == before:
        return ["Nothing was changed."]
    if cred.is_active(after):
        return [f"The new setup was already saved: Jev is on with {after.get('provider')} "
                f"({after.get('endpoint_host')}), model {after.get('model')}.",
                "The remaining steps (engine check, PATH) may not have finished. "
                "Run `ai auth` again to finish, or `ai logout` to turn Jev off. `ai doctor` shows the state."]
    return ["The saved state changed before the cancel. Run `ai doctor` to see it, "
            "or `ai logout` to turn Jev off."]


def _auth_steps(deps: Deps, meta: Optional[dict]) -> int:
    profile = _pick_profile(deps)                                      # steps 1-3

    store = deps.detect_store()                                         # step 4
    if store is None:
        raise Abort("no secure secret store is available (Windows Credential Manager, macOS Keychain, "
                    "Secret Service or KWallet). Unlock or set one up, then rerun `ai auth`")
    deps.say(f"Secret store: {store.backend_name}")

    try:                                                                # step 5
        key = provider.validate_key(deps.ask_secret("Jev API key (hidden): "))
    except provider.InputError as exc:
        raise Abort(str(exc))
    secret = cred.encode_secret(profile, key)
    limit = store.max_secret_chars()
    if limit is not None and len(secret) > limit:
        raise Abort("the key is too long for this secret store")

    deps.say("Validating the key with one small synthetic request (no source code)...")   # step 7
    result = deps.validate(profile["base_url"], profile["model"], key)
    del key
    if not result.ok:
        raise Abort(f"validation failed ({result.describe()})")
    deps.say(f"Validation passed (usage: {result.usage}).")

    new_ref = cred.new_credential_ref()                                 # step 8
    old_ref = meta.get("credential_ref") if meta else None
    old_backend = meta.get("secret_backend") if meta else None
    updated = dict(meta) if meta else cred.new_metadata()
    updated.update({
        "enabled": True,
        "provider": profile["provider"],
        "base_url": profile["base_url"],
        "model": profile["model"],
        "endpoint_host": profile["endpoint_host"],
        "secret_backend": store.backend_name,
        "credential_ref": new_ref,
        "profile_generation": int(updated.get("profile_generation") or 0) + 1,
        "validated_at": cred.now_iso(),
        "engine": {"name": "jg"},
    })
    updated.pop("consent", None)  # written by older versions
    replaced = {"ref": old_ref, "backend": old_backend} if old_ref and old_ref != new_ref else None
    if replaced:
        # Write-ahead: the old key is queued for deletion in the same jev.json write that
        # switches to the new key, so a Ctrl+C before it is removed still leaves it to logout.
        updated["pending_secret_delete"] = list(updated.get("pending_secret_delete") or []) + [replaced]
    # The secret and jev.json are committed together: any failure or Ctrl+C before jev.json is
    # written removes the new secret, so a cancelled run leaves the previous state unchanged.
    try:
        store.set(new_ref, secret)
        cred.save_metadata(updated, deps.env)
    except BaseException as exc:
        try:
            committed = (cred.load_metadata(deps.env) or {}).get("credential_ref") == new_ref
        except cred.StateError:
            committed = False
        if not committed:  # jev.json was written just before the interrupt: keep its secret
            try:
                store.delete(new_ref)
            except Exception:  # noqa: BLE001
                pass
        if isinstance(exc, (KeyboardInterrupt, EOFError)):
            raise
        raise Abort("could not save the key or jev.json; the new key was removed from the secret store")
    finally:
        del secret
    if replaced:
        old_store = deps.store_by_name(old_backend) if old_backend else None
        try:
            if old_store is None:
                raise OSError("store unavailable")
            old_store.delete(old_ref)
        except Exception:  # noqa: BLE001
            deps.say("Warning: the previous key could not be removed from the secret store; "
                     "`ai logout` will retry. It is never used again.")
        else:
            updated["pending_secret_delete"] = [
                item for item in updated["pending_secret_delete"] if item != replaced]
            cred.save_metadata(updated, deps.env)
    deps.say(f"Saved: {profile['provider']} ({profile['endpoint_host']}), model {profile['model']}, "
             f"key in {store.backend_name}.")

    lookup = pathenv.lookup_jg(deps.env)                                # step 9
    if lookup.real is None:
        deps.say(f"Engine: jg not found. Install it yourself: {JG_INSTALL_COMMAND}")
        deps.say("State: enabled, engine unavailable. Skills keep the normal workflow until jg is installed.")
    else:
        version = deps.version_of(lookup.real)
        deps.say(f"Engine: jg {version or '(version unknown)'} at {lookup.real}.")

    status, messages = fix_path(deps)                                   # step 10
    for line in messages:
        deps.say(line)
    deps.say(f"jevgrep: {status}")
    return EXIT_OK


# --------------------------------------------------------------------------- ai logout

def cmd_logout(deps: Deps) -> int:
    try:
        meta = cred.load_metadata(deps.env)
    except cred.StateError as exc:
        deps.say(f"Cannot read the current state: {exc}.")
        return EXIT_FAIL
    if meta is None:
        deps.say("Jev is not configured; nothing to do.")
        return EXIT_OK

    meta["enabled"] = False                                             # disable first
    meta.pop("consent", None)
    cred.save_metadata(meta, deps.env)

    pending = list(meta.get("pending_secret_delete") or [])
    if meta.get("credential_ref"):
        pending.insert(0, {"ref": meta["credential_ref"], "backend": meta.get("secret_backend")})
    left = []
    for item in pending:
        store = deps.store_by_name(item.get("backend")) if item.get("backend") else None
        try:
            if store is None:
                raise OSError("store unavailable")
            store.delete(item["ref"])
        except Exception:  # noqa: BLE001
            left.append(item)
    meta["credential_ref"] = None
    meta["pending_secret_delete"] = left
    meta.pop("path_backup", None)  # written by older versions; PATH is no longer restored

    # The user PATH is left as `ai auth` set it: `jg` keeps resolving to the shim, which falls
    # back with reason=no_key, so a `jg auth` login of the npm jg cannot send source after logout.
    if left:
        cred.save_metadata(meta, deps.env)
        deps.say("Jev is disabled, but the key could NOT be removed from the secret store. "
                 "Unlock the store and run `ai logout` again.")
    else:
        cred.delete_metadata(deps.env)
        deps.say("Logged out: Jev is disabled, the key was removed.")
    deps.say("`jg` stays on the ai-skill shim, which does not run without a key; skills use the normal "
             "workflow. `ai auth` turns Jev back on.")
    return EXIT_FAIL if left else EXIT_OK


# --------------------------------------------------------------------------- ai doctor

def doctor_report(deps: Deps) -> dict:
    hints: List[str] = []
    reason = None
    try:
        meta = cred.load_metadata(deps.env)
    except cred.StateError as exc:
        meta = None
        reason = "no_cli"
        hints.append(f"jev.json cannot be read ({exc}); run `ai logout`, then `ai auth`.")
    active = cred.is_active(meta)
    state = jg_state(deps, active)
    if reason is None:
        if not active:
            reason = "no_key"
        elif state.jg == "missing":
            reason = "engine_missing"
        elif state.jg == "shadowed":
            reason = "shadowed"
    if active:
        hints.extend(state.hints)
    if meta and meta.get("pending_secret_delete"):
        hints.append("An old key is still in the secret store; run `ai logout` (or `ai auth` again) to retry.")
    ready = active and reason is None
    return {
        "tool": TOOL_ID,
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "state": "ready" if ready else "off",
        "reason": None if ready else reason,
        "enabled": bool(meta and meta.get("enabled")),
        "provider": meta.get("provider") if active else None,
        "model": meta.get("model") if active else None,
        "endpoint_host": meta.get("endpoint_host") if active else None,
        "secret_backend": meta.get("secret_backend") if active else None,
        "jg": state.jg,
        "jg_path": state.jg_path,
        "jg_version": state.jg_version,
        "jevgrep": state.jevgrep,
        "hints": hints,
    }


def cmd_doctor(deps: Deps, as_json: bool) -> int:
    report = doctor_report(deps)
    if as_json:
        deps.say(json.dumps(report, indent=2))
        return EXIT_OK
    deps.say(f"ai-skill {report['version']}")
    deps.say(f"Jev: {report['state']}" + (f" (reason={report['reason']})" if report["reason"] else ""))
    if report["provider"]:
        deps.say(f"  provider: {report['provider']}  host: {report['endpoint_host']}  model: {report['model']}")
        deps.say(f"  secret store: {report['secret_backend']}")
    deps.say(f"jg: {report['jg']}" + (f" ({report['jg_version']}, {report['jg_path']})" if report["jg_path"] else ""))
    deps.say(f"jevgrep: {report['jevgrep']}")
    for hint in report["hints"]:
        deps.say(f"  hint: {hint}")
    if report["reason"] == "no_key":
        deps.say("  Not configured: the skills run their normal workflow. Run `ai auth` in a terminal to enable Jev.")
    return EXIT_OK


# --------------------------------------------------------------------------- entry point

def _startup_cleanup(env) -> None:
    try:
        root = cred.run_root(env)
        if root.is_dir():
            cleanup_orphans(root)
    except Exception:  # noqa: BLE001 - cleanup is best effort
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai", description="Optional Jev discovery for the ai-skill skills.")
    parser.add_argument("--version", action="version", version=f"ai-skill {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("auth", help="store a Jev key in the OS secret store and set up jevgrep")
    sub.add_parser("logout", help="disable Jev and delete the key (jg stays on the shim)")
    doctor = sub.add_parser("doctor", help="show the Jev state (no network, no secret store access)")
    doctor.add_argument("--json", action="store_true", help="machine-readable output for the skills")
    return parser


def main(argv: Optional[List[str]] = None, deps: Optional[Deps] = None) -> int:
    args = build_parser().parse_args(argv)
    deps = deps or Deps(select=menu.arrow_select)
    _startup_cleanup(deps.env)
    if args.command == "auth":
        return cmd_auth(deps)
    if args.command == "logout":
        return cmd_logout(deps)
    return cmd_doctor(deps, args.json)


if __name__ == "__main__":
    sys.exit(main())
