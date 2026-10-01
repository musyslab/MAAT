"""Read and validate the environment settings used during application startup.

Provide required and optional strings, boolean and integer parsing, comma-separated
lists, and construction of the MySQL SQLAlchemy URL. Missing required values and
invalid integer settings raise clear errors when the application is created."""

import os
from urllib.parse import quote


def require_env(name: str) -> str:
    """Handle require env for this component.

    Inputs: name."""
    value = (os.environ.get(name) or "").strip()
    # Reject this case with the exception below.
    if not value:
        raise RuntimeError(f"{name} is not set.")
    return value


def optional_env(name: str, default: str = "") -> str:
    """Handle optional env for this component.

    Inputs: name, default."""
    value = (os.environ.get(name) or "").strip()
    # Treat both a missing variable and a blank value as a request for the default.
    return value if value else default


def env_bool(name: str, default: bool = False) -> bool:
    """Handle env bool for this component.

    Inputs: name, default."""
    raw = os.environ.get(name)
    # Handle the case where raw is None or not raw.strip().
    if raw is None or not raw.strip():
        return default
    # Accept these common spellings as true; other nonempty values become false.
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def env_int(name: str, default: int) -> int:
    """Handle env int for this component.

    Inputs: name, default."""
    raw = (os.environ.get(name) or "").strip()
    # Handle the case where not raw.
    if not raw:
        return default

    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc


def csv_env(name: str, default: str = "") -> list[str]:
    """Handle csv env for this component.

    Inputs: name, default."""
    raw = optional_env(name, default)
    # Split comma-separated settings, trim whitespace, and discard empty entries.
    return [item.strip() for item in raw.split(",") if item.strip()]


def build_database_uri() -> str:
    """Build database uri."""
    # Assemble the connection URL from required database settings.
    return (
        f"mysql+pymysql://{quote(require_env('DB_USER'), safe='')}:{quote(require_env('DB_PASSWORD'), safe='')}"
        f"@{require_env('DB_HOST')}:{require_env('DB_PORT')}/{quote(require_env('DB_NAME'), safe='')}"
    )
