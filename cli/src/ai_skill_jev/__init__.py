"""Optional Jev integration for ai-skill: `ai auth`, `ai logout`, `ai doctor` and the `jg` shim."""

__version__ = "1.0.0"

TOOL_ID = "ai-skill"
SCHEMA_VERSION = 1
SECRET_SERVICE = "ai-skill/jev"
# Exit code for every case where the shim refuses or falls back instead of answering.
EXIT_FALLBACK = 3
