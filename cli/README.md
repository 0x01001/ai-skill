# ai-skill-jev

Optional CLI for [ai-skill](../README.md): `ai auth`, `ai logout`, `ai doctor`, plus the `jg` shim that lets the `ai-*` skills and the upstream `jevgrep` skill call Jev with the key `ai auth` stored in the OS secret store.

```bash
pipx install "git+https://github.com/0x01001/ai-skill@<tag>#subdirectory=cli"   # users: pin a release tag
pipx install --editable ./cli                                                  # from a checkout
```

Tests (stdlib `unittest`, fake `jg`, fake HTTP, fake secret store; no network, no real key):

```bash
cd cli && PYTHONPATH=src python -m unittest discover -s tests -t .
```

The design and its limits live in [plan/2026-11-14-jev-optional-auth-integration-plan.md](../plan/2026-11-14-jev-optional-auth-integration-plan.md).
