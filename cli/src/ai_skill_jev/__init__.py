"""Optional Jev integration for ai-skill: `ai auth`, `ai logout`, `ai doctor` and the `jg` shim."""

__version__ = "0.1.0"

TOOL_ID = "ai-skill"
SCHEMA_VERSION = 1
SECRET_SERVICE = "ai-skill/jev"
# Any installed `jg` is used: keeping @dzhng/jevgrep up to date is upstream's job, and a new
# release never turns Jev off. Only the key enables Jev.
JG_INSTALL_COMMAND = "npm install --global @dzhng/jevgrep@latest"
# Exit code for every case where the shim refuses or falls back instead of answering.
EXIT_FALLBACK = 3
